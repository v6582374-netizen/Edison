"""Direct translation through the confirmed preparation and persistence boundary."""

import json
from pathlib import Path

from coworker.providers.base import AssistantTurn, TokenUsage
from youtube_strataread.workbench.experiment import CaptionSnapshot, ModelIdentity, run_baseline
from youtube_strataread.workbench.experiment_translation import (
    CompletionCapacity,
    OutputSegmentation,
)
from youtube_strataread.workbench.workspace import LocalWorkspace

IDENTITY = ModelIdentity("mock-model", "synthetic-v1", "0" * 64, simulated=True)
CAPACITY = CompletionCapacity(input_characters=100000, output_tokens=8192, simulated=True)


def source(tmp_path, text=None):
    path = tmp_path / "source.srt"
    path.write_text(
        text
        or (
            "1\n00:00:00,000 --> 00:00:02,000\nAda: We need 25 units (not 30).\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\nBen: We must not reorder the steps.\n"
        )
    )
    return CaptionSnapshot(path, "sample", "Private", "en")


class DirectModel:
    def __init__(self, units):
        self.units = units
        self.calls = []

    def __call__(self, model, messages, **options):
        self.calls.append((model, messages, options))
        return AssistantTurn(
            text=json.dumps({"units": self.units}, ensure_ascii=False),
            finish_reason="stop",
            usage=TokenUsage(input=200, output=100),
        )


def test_direct_variant_retains_complete_translation_and_formatted_reading_manuscript(tmp_path):
    model = DirectModel(
        [
            {
                "source_ids": ["cue-000001", "cue-000002"],
                "text": "Ada：需要二十五个单位（不是三十个）。Ben：不得改变步骤顺序。",
            },
        ]
    )
    result = run_baseline(
        tmp_path / "run", source(tmp_path), IDENTITY, model, variant="B-direct", capacity=CAPACITY
    )
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["variant"] == "B-direct"
    assert (
        result.parent / "translation.md"
    ).read_text() == "Ada：需要二十五个单位（不是三十个）。Ben：不得改变步骤顺序。\n"
    assert (
        result.parent / "manuscript.md"
    ).read_text() == "## 第 1 节\n\nAda：需要二十五个单位（不是三十个）。Ben：不得改变步骤顺序。\n"
    assert record["attempts"] == 1
    ws = LocalWorkspace.open(tmp_path / "run" / "workspace")
    assert ws.document("sample")["version"] == 1
    assert ws.retained_transcript("sample")["content"].startswith("1\n00:00:00")
    mapping = json.loads((result.parent / record["artifacts"]["candidate_provenance"]).read_text())
    assert mapping["coverage"]["source_ids"] == ["cue-000001", "cue-000002"]
    assert mapping["coverage"]["semantic_fidelity_verified"] is False
    assert record["segmentation"]["strategy"] == "whole"
    assert model.calls[0][2]["max_tokens"] == 8192


def test_selected_output_allocation_is_forwarded_for_both_comparison_mechanisms(tmp_path):
    from translation_fixture import EchoModel

    capacity = CompletionCapacity(input_characters=100000, output_tokens=256, simulated=True)
    direct = DirectModel(
        [{"source_ids": ["cue-000001", "cue-000002"], "text": "保留二十五个单位，不能改变步骤。"}]
    )
    run_baseline(
        tmp_path / "direct",
        source(tmp_path),
        IDENTITY,
        direct,
        variant="B-direct",
        capacity=capacity,
    )
    baseline = EchoModel()
    run_baseline(tmp_path / "baseline", source(tmp_path), IDENTITY, baseline, capacity=capacity)
    assert direct.calls[0][2]["max_tokens"] == 256
    assert {call[2]["max_tokens"] for call in baseline.calls} == {256}


def test_semantic_chapters_keep_full_context_and_complete_sentences_in_independent_outputs(
    tmp_path,
):
    class ChapterModel:
        def __init__(self):
            self.contexts = []

        def __call__(self, model, messages, **options):
            payload = json.loads(messages[-1]["content"])
            self.contexts.append(payload)
            ids = payload["output_source_ids"]
            text = (
                "Ada：需要25个单位（不是30个）。"
                if ids == ["cue-000001"]
                else "Ben：不得改变步骤顺序。"
            )
            return AssistantTurn(
                text=json.dumps({"units": [{"source_ids": ids, "text": text}]}),
                finish_reason="stop",
            )

    model = ChapterModel()
    result = run_baseline(
        tmp_path / "run",
        source(tmp_path),
        IDENTITY,
        model,
        variant="B-direct",
        capacity=CAPACITY,
        segmentation=OutputSegmentation("semantic-chapters", ("cue-000001", "cue-000002")),
    )
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert (
        result.parent / "translation.md"
    ).read_text() == "Ada：需要25个单位（不是30个）。\n\nBen：不得改变步骤顺序。\n"
    assert (
        (result.parent / "manuscript.md").read_text()
        == "## 第 1 节\n\nAda：需要25个单位（不是30个）。\n\n## 第 2 节\n\nBen：不得改变步骤顺序。\n"
    )
    assert len(model.contexts) == 2
    assert all(
        [u["id"] for u in p["full_source_context"]] == ["cue-000001", "cue-000002"]
        for p in model.contexts
    )
    assert record["stage_elapsed_seconds"]["translation"] >= 0
    assert record["stage_elapsed_seconds"]["composition"] >= 0


def test_missing_duplicated_and_reordered_source_units_do_not_publish(tmp_path):
    cases = [
        [{"source_ids": ["cue-000001"], "text": "缺少第二句。"}],
        [{"source_ids": ["cue-000001", "cue-000001", "cue-000002"], "text": "重复。"}],
        [{"source_ids": ["cue-000002", "cue-000001"], "text": "乱序。"}],
    ]
    for index, units in enumerate(cases):
        result = run_baseline(
            tmp_path / f"run-{index}",
            source(tmp_path),
            IDENTITY,
            DirectModel(units),
            variant="B-direct",
            capacity=CAPACITY,
        )
        record = json.loads(result.read_text())
        assert record["state"] == "failed"
        assert "来源单元" in record["reason"]
        assert "translation" not in record["artifacts"]
        assert (
            LocalWorkspace.open(tmp_path / f"run-{index}" / "workspace").asset("sample")[
                "manuscript_version"
            ]
            is None
        )


def test_sentence_and_numeric_fragments_cannot_be_selected_as_chapter_boundaries(tmp_path):
    raw = (
        "1\n00:00:00,000 --> 00:00:02,000\nThe amount is 1,\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\n250 units unless delayed.\n"
    )
    model = DirectModel([])
    result = run_baseline(
        tmp_path / "run",
        source(tmp_path, raw),
        IDENTITY,
        model,
        variant="B-direct",
        capacity=CAPACITY,
        segmentation=OutputSegmentation("semantic-chapters", ("cue-000001", "cue-000002")),
    )
    record = json.loads(result.read_text())
    assert record["state"] == "failed"
    assert record["attempts"] == 0
    assert "manuscript" not in record["artifacts"]


def test_real_candidate_requires_observed_proxy_capacity_and_full_context_is_not_silently_cut(
    tmp_path,
):
    import pytest

    real = ModelIdentity("real-model", "real-proxy-v1", "a" * 64)
    with pytest.raises(ValueError, match="verified"):
        run_baseline(
            tmp_path / "unverified",
            source(tmp_path),
            real,
            DirectModel([]),
            variant="B-direct",
            capacity=CAPACITY,
        )
    assert not (tmp_path / "unverified").exists()
    model = DirectModel([])
    result = run_baseline(
        tmp_path / "too-small",
        source(tmp_path),
        IDENTITY,
        model,
        variant="B-direct",
        capacity=CompletionCapacity(30, 256, simulated=True),
    )
    record = json.loads(result.read_text())
    assert record["state"] == "failed"
    assert "输入能力" in record["reason"]
    assert record["attempts"] == 0


def test_missing_finish_reason_does_not_publish_a_structurally_complete_candidate(tmp_path):
    class MissingFinish(DirectModel):
        def __call__(self, *args, **kwargs):
            turn = super().__call__(*args, **kwargs)
            turn.finish_reason = None
            return turn

    model = MissingFinish(
        [{"source_ids": ["cue-000001", "cue-000002"], "text": "需要25个单位，不得改变步骤顺序。"}]
    )
    result = run_baseline(
        tmp_path / "run", source(tmp_path), IDENTITY, model, variant="B-direct", capacity=CAPACITY
    )
    record = json.loads(result.read_text())
    assert record["state"] == "failed"
    assert "结束原因" in record["reason"]
    assert "manuscript" not in record["artifacts"]


def test_semantic_strategy_can_plan_automatic_sentence_aligned_chapters(tmp_path):
    raw = (
        "1\n00:00:00,000 --> 00:00:02,000\nThe first step requires twenty five units.\n\n"
        "2\n00:00:05,000 --> 00:00:07,000\nThe second step must stay second.\n\n"
        "3\n00:00:10,000 --> 00:00:12,000\nThe third step must remain last.\n"
    )

    class AutoChapters:
        def __init__(self):
            self.selected = []

        def __call__(self, model, messages, **options):
            ids = json.loads(messages[-1]["content"])["output_source_ids"]
            self.selected.append(ids)
            return AssistantTurn(
                text=json.dumps({"units": [{"source_ids": ids, "text": "保留原始步骤。"}]}),
                finish_reason="stop",
            )

    model = AutoChapters()
    result = run_baseline(
        tmp_path / "run",
        source(tmp_path, raw),
        IDENTITY,
        model,
        variant="B-direct",
        capacity=CAPACITY,
        segmentation=OutputSegmentation("semantic-chapters", target_characters=30),
    )
    assert json.loads(result.read_text())["state"] == "ready"
    assert model.selected == [["cue-000001"], ["cue-000002"], ["cue-000003"]]


def test_official_source_chapter_evidence_allows_unpunctuated_caption_boundary(tmp_path):
    raw = (
        "1\n00:00:00,000 --> 00:00:02,000\nour first topic is complete\n\n"
        "2\n00:00:05,000 --> 00:00:07,000\nthe next official topic begins\n"
    )

    def complete(_model, messages, **_options):
        ids = json.loads(messages[-1]["content"])["output_source_ids"]
        return AssistantTurn(
            text=json.dumps({"units": [{"source_ids": ids, "text": "保留主题内容。"}]}),
            finish_reason="stop",
        )

    result = run_baseline(
        tmp_path / "run",
        source(tmp_path, raw),
        IDENTITY,
        complete,
        variant="B-direct",
        capacity=CAPACITY,
        segmentation=OutputSegmentation(
            "semantic-chapters",
            ("cue-000001", "cue-000002"),
            boundary_evidence=(("cue-000001", "synthetic official chapter begins at00:00:05"),),
        ),
    )
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 2
    assert record["segmentation"]["boundary_evidence"] == [
        ["cue-000001", "synthetic official chapter begins at00:00:05"]
    ]


def test_mock_cli_selects_direct_variant_and_records_shared_comparison_controls(tmp_path, capsys):
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
                                {
                                    "source_ids": ["cue-000001", "cue-000002"],
                                    "text": "需要二十五个单位，不得改变步骤。",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }
            ]
        )
    )
    assert (
        main(
            [
                "--snapshot",
                str(snapshot.path),
                "--root",
                str(tmp_path / "run"),
                "--video-id",
                "sample",
                "--language",
                "en",
                "--mock-responses",
                str(responses),
                "--variant",
                "B-direct",
                "--capacity-input-characters",
                "100000",
                "--output-tokens",
                "256",
            ]
        )
        == 0
    )
    record = json.loads(Path(json.loads(capsys.readouterr().out)["record"]).read_text())
    assert record["variant"] == "B-direct"
    assert record["comparison_settings"]["requested_output_tokens"] == 256
    assert record["capacity"]["simulated"] is True


def test_failed_direct_regeneration_preserves_successful_translation_and_version(tmp_path):
    snapshot = source(tmp_path)
    root = tmp_path / "run"
    first_record = run_baseline(
        root,
        snapshot,
        IDENTITY,
        DirectModel(
            [{"source_ids": ["cue-000001", "cue-000002"], "text": "需要25个单位，不得改变顺序。"}]
        ),
        variant="B-direct",
        capacity=CAPACITY,
    )
    ws = LocalWorkspace.open(root / "workspace")
    first = ws.document("sample")
    result = run_baseline(
        root,
        snapshot,
        IDENTITY,
        DirectModel([{"source_ids": ["cue-000001"], "text": "缺失另一条来源。"}]),
        variant="B-direct",
        capacity=CAPACITY,
    )
    assert json.loads(result.read_text())["state"] == "failed"
    assert ws.document("sample") == first
    assert Path(first["translation_path"]).read_text() == "需要25个单位，不得改变顺序。\n"
    assert json.loads(first_record.read_text())["state"] == "ready"
