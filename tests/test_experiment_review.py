"""Conditional review observed through real preparation and versioned artifacts."""

import json
from pathlib import Path

import pytest

from coworker.providers.base import AssistantTurn, TokenUsage
from youtube_strataread.workbench.experiment import CaptionSnapshot, ModelIdentity, run_baseline
from youtube_strataread.workbench.experiment_translation import CompletionCapacity
from youtube_strataread.workbench.workspace import LocalWorkspace

IDENTITY = ModelIdentity("mock-model", "synthetic-v1", "0" * 64, simulated=True)
CAPACITY = CompletionCapacity(100000, 8192, simulated=True)


def source(tmp_path, lines=("Age is 25 years.", "The sequence stays the same.")):
    path = tmp_path / "source.srt"
    path.write_text(
        "\n\n".join(
            f"{i}\n00:00:{i * 3:02d},000 --> 00:00:{i * 3 + 2:02d},000\n{text}"
            for i, text in enumerate(lines, 1)
        )
    )
    return CaptionSnapshot(path, "sample", "Private", "en")


def row(ids, text):
    return {"source_ids": ids, "text": text}


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, model, messages, **options):
        self.calls.append((messages, options))
        value = next(self.responses)
        if isinstance(value, AssistantTurn):
            return value
        return AssistantTurn(
            text=json.dumps(value, ensure_ascii=False),
            finish_reason="stop",
            usage=TokenUsage(input=100, output=100),
        )


def run(tmp_path, model, snapshot=None, **options):
    return run_baseline(
        tmp_path / "run",
        snapshot or source(tmp_path),
        IDENTITY,
        model,
        variant="C-conditional",
        capacity=CAPACITY,
        **options,
    )


def test_equivalent_numeric_spelling_completes_without_automatic_rewrite(tmp_path):
    model = ScriptedModel(
        [
            {
                "units": [
                    row(["cue-000001"], "年龄是二十五岁。"),
                    row(["cue-000002"], "顺序保持不变。"),
                ]
            }
        ]
    )
    result = run(tmp_path, model)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 1
    assert (result.parent / "translation.md").read_text() == "年龄是二十五岁。\n\n顺序保持不变。\n"
    assert (
        result.parent / "manuscript.md"
    ).read_text() == "## 第 1 节\n\n年龄是二十五岁。\n\n顺序保持不变。\n"
    provenance = json.loads(
        (result.parent / record["artifacts"]["candidate_provenance"]).read_text()
    )
    assert provenance["variant"] == "C-conditional"
    assert provenance["review"]["repairs"] == 0
    assert provenance["coverage"]["semantic_fidelity_verified"] is False


def test_missing_source_repairs_only_that_range_and_preserves_accepted_text(tmp_path):
    model = ScriptedModel(
        [
            {"units": [row(["cue-000001"], "年龄为二十五岁。")]},
            {"decision": "replace", "units": [row(["cue-000002"], "顺序保持不变。")]},
        ]
    )
    result = run(tmp_path, model)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    assert (result.parent / "translation.md").read_text() == "年龄为二十五岁。\n\n顺序保持不变。\n"
    repaired = json.loads(model.calls[1][0][-1]["content"])
    assert repaired["affected_source_ids"] == ["cue-000002"]
    events = json.loads((result.parent / record["artifacts"]["conditional_review"]).read_text())
    assert events[0]["triggers"] == ["source_coverage"]
    assert events[0]["affected_source_ids"] == ["cue-000002"]
    assert events[-1]["result"] == "repaired"


def test_matching_numbers_do_not_hide_unit_and_negation_errors(tmp_path):
    snapshot = source(tmp_path, ("Do not spend 25 dollars.", "The sequence stays the same."))
    model = ScriptedModel(
        [
            {"units": [row(["cue-000001"], "花费25元。"), row(["cue-000002"], "顺序保持不变。")]},
            {"decision": "replace", "units": [row(["cue-000001"], "不要花费二十五美元。")]},
        ]
    )
    result = run(tmp_path, model, snapshot)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    assert (
        result.parent / "translation.md"
    ).read_text() == "不要花费二十五美元。\n\n顺序保持不变。\n"
    payload = json.loads(model.calls[1][0][-1]["content"])
    assert payload["affected_source_ids"] == ["cue-000001"]
    assert set(payload["triggers"]) >= {"unit", "negation"}


def test_multi_number_association_is_checked_even_when_digits_and_units_are_present(tmp_path):
    snapshot = source(
        tmp_path, ("Alice has 5 dollars; Ben has 10 dollars.", "The sequence stays the same.")
    )
    model = ScriptedModel(
        [
            {
                "units": [
                    row(["cue-000001"], "Alice有10美元，Ben有5美元。"),
                    row(["cue-000002"], "顺序保持不变。"),
                ]
            },
            {"decision": "replace", "units": [row(["cue-000001"], "Alice有5美元，Ben有10美元。")]},
        ]
    )
    result = run(tmp_path, model, snapshot)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    assert (result.parent / "translation.md").read_text().startswith("Alice有5美元，Ben有10美元。")
    assert "number_association" in json.loads(model.calls[1][0][-1]["content"])["triggers"]


def test_truncation_and_repeated_no_progress_stop_without_overwriting_history(tmp_path):
    snapshot = source(tmp_path)
    first = run(
        tmp_path,
        ScriptedModel(
            [
                {
                    "units": [
                        row(["cue-000001"], "年龄是25岁。"),
                        row(["cue-000002"], "顺序保持不变。"),
                    ]
                }
            ]
        ),
        snapshot,
    )
    workspace = LocalWorkspace.open(tmp_path / "run" / "workspace")
    historical = workspace.document("sample")
    incomplete = AssistantTurn(text='{"units": [', finish_reason="length")
    model = ScriptedModel([incomplete, incomplete])
    failed = run(tmp_path, model, snapshot)
    record = json.loads(failed.read_text())
    assert record["state"] == "failed"
    assert record["attempts"] == 2
    assert "定向复核" in record["reason"]
    assert "manuscript" not in record["artifacts"]
    assert workspace.document("sample") == historical
    assert Path(historical["translation_path"]).read_text().startswith("年龄是25岁。")
    assert json.loads(first.read_text())["state"] == "ready"
    events = json.loads((failed.parent / record["artifacts"]["conditional_review"]).read_text())
    assert events[0]["triggers"] == ["incomplete_response"]
    assert events[-1]["rejected_response"] == '{"units": ['


def test_semantic_check_can_accept_without_rewriting_verified_range(tmp_path):
    snapshot = source(
        tmp_path, ("Alice has 5 dollars; Ben has 10 dollars.", "The sequence stays the same.")
    )
    valid = row(["cue-000001"], "Alice有5美元，Ben有10美元。")
    model = ScriptedModel(
        [
            {"units": [valid, row(["cue-000002"], "顺序保持不变。")]},
            {"decision": "accept", "units": [valid]},
        ]
    )
    result = run(tmp_path, model, snapshot)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    assert (
        result.parent / "translation.md"
    ).read_text() == "Alice有5美元，Ben有10美元。\n\n顺序保持不变。\n"


def test_repair_cannot_edit_an_unaffected_source_range(tmp_path):
    model = ScriptedModel(
        [
            {"units": [row(["cue-000001"], "年龄是二十五岁。")]},
            {
                "decision": "replace",
                "units": [row(["cue-000001", "cue-000002"], "将已接受段落也改写。")],
            },
            {
                "decision": "replace",
                "units": [row(["cue-000001", "cue-000002"], "将已接受段落也改写。")],
            },
        ]
    )
    result = run(tmp_path, model)
    record = json.loads(result.read_text())
    assert record["state"] == "failed"
    assert record["attempts"] == 3
    assert "manuscript" not in record["artifacts"]


def test_equivalent_percent_and_decimal_spellings_do_not_trigger_rejection(tmp_path):
    snapshot = source(tmp_path, ("The rate is 25 percent.", "The value is 25.0."))
    model = ScriptedModel(
        [
            {
                "units": [
                    row(["cue-000001"], "比例是百分之二十五。"),
                    row(["cue-000002"], "数值为25。"),
                ]
            }
        ]
    )
    result = run(tmp_path, model, snapshot)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 1


def test_reordered_draft_ranges_are_explicitly_repaired_before_publication(tmp_path):
    model = ScriptedModel(
        [
            {"units": [row(["cue-000002"], "顺序保持不变。"), row(["cue-000001"], "年龄是25岁。")]},
            {
                "decision": "replace",
                "units": [
                    row(["cue-000001"], "年龄是25岁。"),
                    row(["cue-000002"], "顺序保持不变。"),
                ],
            },
        ]
    )
    result = run(tmp_path, model)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    events = json.loads((result.parent / record["artifacts"]["conditional_review"]).read_text())
    assert events[0]["triggers"] == ["source_order"]
    assert (result.parent / "translation.md").read_text().startswith("年龄是25岁。")


@pytest.mark.parametrize(
    "extra", [{"source_ids": None, "text": "无法对齐。"}, {"text": "无法对齐。"}]
)
def test_malformed_extra_range_requires_explicit_repair(tmp_path, extra):
    valid = [row(["cue-000001"], "年龄是25岁。"), row(["cue-000002"], "顺序保持不变。")]
    model = ScriptedModel(
        [
            {"units": [*valid, extra]},
            {"decision": "replace", "units": valid},
        ]
    )
    result = run(tmp_path, model)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    payload = json.loads(model.calls[1][0][-1]["content"])
    assert payload["affected_source_ids"] == ["cue-000001", "cue-000002"]
    assert payload["triggers"] == ["source_structure"]


def test_cli_selects_conditional_variant_and_saves_private_review_record(tmp_path, capsys):
    from youtube_strataread.workbench.experiment import main

    snapshot = source(tmp_path)
    responses = tmp_path / "responses.json"
    responses.write_text(
        json.dumps(
            [
                {
                    "text": json.dumps(
                        {
                            "units": [
                                row(["cue-000001"], "年龄是25岁。"),
                                row(["cue-000002"], "顺序保持不变。"),
                            ]
                        }
                    )
                }
            ]
        )
    )
    assert (
        main(
            [
                "--root",
                str(tmp_path / "run"),
                "--snapshot",
                str(snapshot.path),
                "--video-id",
                "sample",
                "--language",
                "en",
                "--mock-responses",
                str(responses),
                "--variant",
                "C-conditional",
                "--capacity-input-characters",
                "100000",
            ]
        )
        == 0
    )
    result = Path(json.loads(capsys.readouterr().out)["record"])
    record = json.loads(result.read_text())
    assert record["variant"] == "C-conditional"
    assert json.loads((result.parent / record["artifacts"]["conditional_review"]).read_text()) == []


def test_successful_conditional_repair_is_not_stopped_by_legacy_local_budget(tmp_path):
    import copy

    from youtube_strataread.workbench.translation import DEFAULTS

    config = copy.deepcopy(DEFAULTS)
    config["max_calls"] = 1
    config["max_tokens"] = 1000
    model = ScriptedModel(
        [
            {"units": [row(["cue-000001"], "年龄为二十五岁。")]},
            {"decision": "replace", "units": [row(["cue-000002"], "顺序保持不变。")]},
        ]
    )
    result = run(tmp_path, model, config=config)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    assert record["consumption_policy"] == "no-local-cumulative-budget"


def test_equivalent_year_and_chinese_decimal_can_be_accepted_without_rewrite(tmp_path):
    snapshot = source(tmp_path, ("In 2024 it cost 2.5 dollars.",))
    faithful = row(["cue-000001"], "二零二四年，它花费二点五美元。")
    model = ScriptedModel(
        [
            {"units": [faithful]},
            {"decision": "accept", "units": [faithful]},
            {"decision": "accept", "units": [faithful]},
        ]
    )
    result = run(tmp_path, model, snapshot)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    assert (result.parent / "translation.md").read_text() == "二零二四年，它花费二点五美元。\n"
    payload = json.loads(model.calls[1][0][-1]["content"])
    assert payload["triggers"] == ["number_association"]
    events = json.loads((result.parent / record["artifacts"]["conditional_review"]).read_text())
    assert events[-1]["decision"] == "accept"
    assert events[-1]["remaining_signals"] == ["number_association"]


@pytest.mark.parametrize(
    "original,translated,trigger",
    [
        ("The fraction is 0.5.", "比例为一半。", "number"),
        ("It costs 25 dollars.", "价格是25美金。", "unit"),
        ("There is no evidence.", "证据尚缺。", "negation"),
    ],
)
def test_explicit_review_can_resolve_fallible_signals_for_valid_paraphrase(
    tmp_path, original, translated, trigger
):
    snapshot = source(tmp_path, (original,))
    faithful = row(["cue-000001"], translated)
    model = ScriptedModel(
        [
            {"units": [faithful]},
            {"decision": "accept", "units": [faithful]},
            {"decision": "accept", "units": [faithful]},
        ]
    )
    result = run(tmp_path, model, snapshot)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    assert (result.parent / "translation.md").read_text() == translated + "\n"
    events = json.loads((result.parent / record["artifacts"]["conditional_review"]).read_text())
    assert trigger in events[0]["triggers"]
    assert trigger in events[-1]["remaining_signals"]
    assert events[-1]["semantic_resolution"] == "automatic_review_accept"
    provenance = json.loads(
        (result.parent / record["artifacts"]["candidate_provenance"]).read_text()
    )
    assert provenance["coverage"]["semantic_fidelity_verified"] is False


def test_replacement_with_remaining_signal_requires_review_of_the_new_candidate(tmp_path):
    snapshot = source(tmp_path, ("The fraction is 0.5.",))
    wrong = row(["cue-000001"], "比例为0.6。")
    faithful = row(["cue-000001"], "比例为一半。")
    model = ScriptedModel(
        [
            {"units": [wrong]},
            {"decision": "replace", "units": [faithful]},
            {"decision": "accept", "units": [faithful]},
        ]
    )
    result = run(tmp_path, model, snapshot)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 3
    assert json.loads(model.calls[2][0][-1]["content"])["current_translation"] == [faithful]
    events = json.loads((result.parent / record["artifacts"]["conditional_review"]).read_text())
    assert events[1]["result"] == "check_required"
    assert events[1]["remaining_signals"] == ["number"]
    assert events[-1]["decision"] == "accept"
    assert (result.parent / "translation.md").read_text() == "比例为一半。\n"
    provenance = json.loads(
        (result.parent / record["artifacts"]["candidate_provenance"]).read_text()
    )
    assert provenance["review"]["repairs"] == 1


def test_rejected_repair_replays_after_process_loss_and_reaches_next_repair(tmp_path):
    class ProcessLoss(BaseException):
        pass

    snapshot = source(tmp_path)
    correct = [row(["cue-000001"], "年龄是25岁。"), row(["cue-000002"], "顺序保持不变。")]
    first = run(tmp_path, ScriptedModel([{"units": correct}]), snapshot)
    workspace = LocalWorkspace.open(tmp_path / "run/workspace")
    historical = workspace.document("sample")
    bad_repair = {"decision": "replace", "units": []}
    scripted = ScriptedModel(
        [
            {"units": [row(["cue-000001"], "年龄是26岁。"), correct[1]]},
            bad_repair,
        ]
    )
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ProcessLoss()
        return scripted(*args, **kwargs)

    with pytest.raises(ProcessLoss):
        run(tmp_path, interrupted, snapshot, attempt_id="rejected-repair")
    assert workspace.document("sample") == historical
    resumed_model = ScriptedModel([{"decision": "replace", "units": [correct[0]]}])
    resumed = run(tmp_path, resumed_model, snapshot, attempt_id="rejected-repair", mode="resume")
    record = json.loads(resumed.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 1
    assert len(resumed_model.calls) == 1
    assert record["artifacts"]["manuscript_version"] == 2
    events = json.loads((resumed.parent / record["artifacts"]["conditional_review"]).read_text())
    assert events[1]["attempt"] == 1
    assert events[1]["result"] == "rejected"
    assert json.loads(events[1]["rejected_response"]) == bad_repair
    assert events[-1]["attempt"] == 2
    assert events[-1]["result"] == "repaired"
    assert Path(historical["translation_path"]).read_text().startswith("年龄是25岁。")
    assert json.loads(first.read_text())["state"] == "ready"
    assert len(workspace.generation_records("sample")) == 2


@pytest.mark.parametrize(
    "draft,reviews",
    [
        (
            {"units": []},
            [
                {"decision": "accept", "units": [row(["cue-000001"], "年龄是25岁。")]},
                {"decision": "accept", "units": [row(["cue-000001"], "年龄是25岁。")]},
            ],
        ),
        (
            {"units": [row(["cue-000001"], "年龄是26岁。")]},
            [
                {"decision": "replace", "units": [row(["cue-000001"], "年龄是27岁。")]},
                {"decision": "replace", "units": [row(["cue-000001"], "年龄是28岁。")]},
            ],
        ),
    ],
)
def test_structural_accept_and_unresolved_replacements_never_publish(tmp_path, draft, reviews):
    snapshot = source(tmp_path, ("Age is 25 years.",))
    model = ScriptedModel([draft, *reviews])
    result = run(tmp_path, model, snapshot)
    record = json.loads(result.read_text())
    assert record["state"] == "failed"
    assert record["attempts"] == 3
    assert "manuscript" not in record["artifacts"]
    workspace = LocalWorkspace.open(tmp_path / "run/workspace")
    assert workspace.generation_records("sample") == []
    assert workspace.retained_transcript("sample")["content"] == snapshot.path.read_text()


@pytest.mark.parametrize(
    "decision,original,draft_text,final_text",
    [
        ("replace", "Age is 25 years.", "年龄是26岁。", "年龄是25岁。"),
        ("accept", "It costs 25 dollars.", "价格是25美金。", "价格是25美金。"),
    ],
)
def test_fenced_conditional_decision_resumes_after_interruption(
    tmp_path, decision, original, draft_text, final_text
):
    class ProcessLoss(BaseException):
        pass

    snapshot = source(tmp_path, (original,))
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ProcessLoss()
        payload = {"units": [row(["cue-000001"], draft_text)]}
        return AssistantTurn(text="```json\n" + json.dumps(payload) + "\n```", finish_reason="stop")

    with pytest.raises(ProcessLoss):
        run(tmp_path, interrupted, snapshot, attempt_id="fenced-review")
    payload = {"decision": decision, "units": [row(["cue-000001"], final_text)]}
    resumed_model = ScriptedModel(
        [AssistantTurn(text="```json\n" + json.dumps(payload) + "\n```", finish_reason="stop")]
    )
    result = run(tmp_path, resumed_model, snapshot, attempt_id="fenced-review", mode="resume")
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 1
    assert (result.parent / "translation.md").read_text() == final_text + "\n"
    assert (result.parent / "manuscript.md").read_text() == "## 第 1 节\n\n" + final_text + "\n"
    events = json.loads((result.parent / record["artifacts"]["conditional_review"]).read_text())
    assert events[-1]["decision"] == decision
    assert "source_coverage" not in events[0]["triggers"]
    repeated = run(tmp_path, ScriptedModel([]), snapshot, attempt_id="fenced-review", mode="resume")
    assert json.loads(repeated.read_text())["attempts"] == 0
    assert len(LocalWorkspace.open(tmp_path / "run/workspace").generation_records("sample")) == 1
