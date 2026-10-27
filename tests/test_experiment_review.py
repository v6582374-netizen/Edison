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
