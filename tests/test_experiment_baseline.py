"""Comparable baseline through preparation, host generation and real persistence."""

import json
import re

import pytest

from coworker.providers.base import AssistantTurn
from youtube_strataread.workbench.experiment import CaptionSnapshot, ModelIdentity, run_baseline
from youtube_strataread.workbench.experiment_translation import (
    CompletionCapacity,
    OutputSegmentation,
)
from youtube_strataread.workbench.workspace import LocalWorkspace

MODEL = ModelIdentity("mock-model", "synthetic-v1", "0" * 64, simulated=True)
CAPACITY = CompletionCapacity(100000, 256, simulated=True)
CHAPTERS = OutputSegmentation("semantic-chapters", ("cue-000001", "cue-000002"))
VARIANT = "A-comparable-three-stage"


def snapshot(tmp_path):
    path = tmp_path / "captions.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nAda: We need 25 units (not 30).\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nBen: We must not reorder the steps.\n"
    )
    return CaptionSnapshot(path, "sample", "Private sample", "en")


class BaselineModel:
    def __init__(self):
        self.requests = []

    def __call__(self, model, messages, **options):
        self.requests.append((messages, options))
        system, prompt = [message["content"] for message in messages]
        if "阅读稿编辑" in system:
            text = "## 阅读标题\n\n" + prompt.split("完整译文：\n", 1)[1]
        elif "审校" in system:
            text = "保留数字、否定和顺序。"
        else:
            chapter = re.findall(r"<TRANSLATE_THIS>(.*?)</TRANSLATE_THIS>", prompt, re.S)[-1]
            revised = "修订：" if "修订" in system else "初译："
            text = revised + (
                "Ada：需要25个单位（不是30个）。"
                if "Ada:" in chapter
                else "Ben：不得改变步骤顺序。"
            )
        return AssistantTurn(text=text, finish_reason="stop")


def run(tmp_path, model, **options):
    return run_baseline(
        tmp_path / "experiment",
        snapshot(tmp_path),
        MODEL,
        model,
        variant=VARIANT,
        capacity=CAPACITY,
        segmentation=CHAPTERS,
        **options,
    )


def test_comparable_baseline_retains_revised_chapters_and_separate_manuscript(tmp_path):
    from translation_agent import utils

    original_chunk_limit = utils.MAX_TOKENS_PER_CHUNK
    model = BaselineModel()
    path = run(tmp_path, model)
    record = json.loads(path.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 8
    assert {options["max_tokens"] for _, options in model.requests} == {256}
    assert all(
        "Ada: We need 25 units (not 30)." in messages[-1]["content"]
        and "Ben: We must not reorder the steps." in messages[-1]["content"]
        for messages, _ in model.requests[:6]
    )
    translation = (path.parent / "translation.md").read_text()
    manuscript = (path.parent / "manuscript.md").read_text()
    assert translation == "修订：Ada：需要25个单位（不是30个）。\n\n修订：Ben：不得改变步骤顺序。\n"
    assert "初译：" not in translation
    assert manuscript.count("## 阅读标题") == 2
    assert translation.replace("\n\n", "") in manuscript.replace("## 阅读标题\n\n", "").replace(
        "\n\n", ""
    )
    provenance = json.loads((path.parent / record["artifacts"]["candidate_provenance"]).read_text())
    assert provenance["coverage"]["source_ids"] == ["cue-000001", "cue-000002"]
    assert provenance["coverage"]["output_mapping"] == "host-assigned-chapter-range"
    assert provenance["coverage"]["model_returned_source_ids_verified"] is False
    assert provenance["coverage"]["semantic_fidelity_verified"] is False
    assert set(record["stage_elapsed_seconds"]) == {
        "initial",
        "review",
        "revision",
        "translation",
        "composition",
    }
    ws = LocalWorkspace.open(tmp_path / "experiment/workspace")
    assert ws.document("sample")["version"] == 1
    assert ws.retained_transcript("sample")["content"] == snapshot(tmp_path).path.read_text()
    assert original_chunk_limit == utils.MAX_TOKENS_PER_CHUNK


def test_effective_chapter_plan_pairs_a_b_c_and_separates_current_path(tmp_path):
    from translation_fixture import EchoModel

    from youtube_strataread.workbench.experiment_evaluation import summarize_runs

    source = snapshot(tmp_path)
    source.path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nAda: First fact.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nBen: Second fact.\n"
    )

    def direct(_model, messages, **_options):
        payload = json.loads(messages[-1]["content"])
        return AssistantTurn(
            text=json.dumps(
                {"units": [{"source_ids": payload["output_source_ids"], "text": "完整保留来源。"}]}
            ),
            finish_reason="stop",
        )

    paths = []
    for variant, model, segmentation in [
        (VARIANT, BaselineModel(), CHAPTERS),
        ("B-direct", direct, CHAPTERS),
        ("C-conditional", direct, CHAPTERS),
        ("A-current-baseline", EchoModel(), CHAPTERS),
        ("B-direct", direct, OutputSegmentation("whole")),
    ]:
        paths.append(
            run_baseline(
                tmp_path / f"run-{len(paths)}",
                source,
                MODEL,
                model,
                variant=variant,
                capacity=CAPACITY,
                segmentation=segmentation,
            )
        )
    records = [json.loads(path.read_text()) for path in paths]
    assert all(record["state"] == "ready" for record in records)
    assert records[0]["effective_chapter_plan"]["chapters"] == [
        {"id": "chapter-000001", "source_ids": ["cue-000001"]},
        {"id": "chapter-000002", "source_ids": ["cue-000002"]},
    ]
    hashes = [record["effective_chapter_plan_sha256"] for record in records]
    assert hashes[0] == hashes[1] == hashes[2]
    assert hashes[3] is None
    assert hashes[4] != hashes[0]
    summary = summarize_runs(tmp_path / "summary.json", paths)
    identities = [row["comparison_identity"] for row in summary["runs"]]
    assert identities[0] == identities[1] == identities[2]
    assert identities[3] != identities[0]
    assert identities[4] != identities[0]


@pytest.mark.parametrize(
    "large_stage, expected_calls", [("initial", 2), ("review", 4), ("revision", 6)]
)
def test_expanded_stages_cannot_bypass_rendered_input_capacity(
    tmp_path, large_stage, expected_calls
):
    model = BaselineModel()

    def large_initial(*args, **kwargs):
        turn = model(*args, **kwargs)
        system = args[1][0]["content"]
        stage = "review" if "审校" in system else "revision" if "修订" in system else "initial"
        if stage == large_stage:
            turn.text = "保留完整来源内容。" * 500
        return turn

    path = run_baseline(
        tmp_path / "experiment",
        snapshot(tmp_path),
        MODEL,
        large_initial,
        variant=VARIANT,
        capacity=CompletionCapacity(2500, 256, simulated=True),
        segmentation=CHAPTERS,
    )
    record = json.loads(path.read_text())
    assert record["state"] == "failed"
    assert "输入能力" in record["reason"]
    assert record["attempts"] == expected_calls
    assert "manuscript" not in record["artifacts"]
    assert LocalWorkspace.open(tmp_path / "experiment/workspace").generation_records("sample") == []


@pytest.mark.parametrize("bad_at", [1, 3, 5, 7])
@pytest.mark.parametrize("finish", [None, "length"])
def test_incomplete_stage_retains_completed_requests_for_explicit_resume(tmp_path, bad_at, finish):
    model = BaselineModel()

    def interrupted(*args, **kwargs):
        turn = model(*args, **kwargs)
        if len(model.requests) == bad_at:
            turn.finish_reason = finish
        return turn

    path = run(tmp_path, interrupted, attempt_id="stable")
    failed = json.loads(path.read_text())
    assert failed["state"] == "failed"
    assert failed["attempts"] == bad_at
    assert "translation" not in failed["artifacts"]
    resumed_path = run(tmp_path, BaselineModel(), attempt_id="stable", mode="resume")
    resumed = json.loads(resumed_path.read_text())
    assert resumed["state"] == "ready"
    assert resumed["recovery"]["cached_responses_before"] == bad_at - 1
    assert resumed["attempts"] == 9 - bad_at

    def forbidden(*args, **kwargs):
        raise AssertionError("completed attempt must not regenerate")

    repeated = json.loads(run(tmp_path, forbidden, attempt_id="stable", mode="resume").read_text())
    assert repeated["state"] == "ready"
    assert repeated["attempts"] == 0
    assert repeated["effective_chapter_plan_sha256"] == resumed["effective_chapter_plan_sha256"]
    assert (
        len(LocalWorkspace.open(tmp_path / "experiment/workspace").generation_records("sample"))
        == 1
    )


@pytest.mark.parametrize("boundary", ["after_bundle", "after_commit"])
def test_comparable_baseline_publication_recovers_without_model_regeneration(tmp_path, boundary):
    class PowerLoss(BaseException):
        pass

    def interrupt(stage):
        if stage == boundary:
            raise PowerLoss()

    with pytest.raises(PowerLoss):
        run(tmp_path, BaselineModel(), attempt_id="publish", publication_fault=interrupt)

    def forbidden(*args, **kwargs):
        raise AssertionError("completed stage outputs must survive publication interruption")

    path = run(tmp_path, forbidden, attempt_id="publish", mode="resume")
    record = json.loads(path.read_text())
    assert record["state"] == "ready"
    assert record["attempts"] == 0
    assert (path.parent / "translation.md").read_text().startswith("修订：Ada")
    assert (path.parent / "manuscript.md").read_text().startswith("## 阅读标题")
    assert (
        len(LocalWorkspace.open(tmp_path / "experiment/workspace").generation_records("sample"))
        == 1
    )


def test_changed_baseline_plan_cannot_reuse_published_attempt(tmp_path):
    run(tmp_path, BaselineModel(), attempt_id="fixed")
    root = tmp_path / "experiment"
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="identity"):
        run_baseline(
            root,
            snapshot(tmp_path),
            MODEL,
            BaselineModel(),
            variant=VARIANT,
            capacity=CAPACITY,
            segmentation=OutputSegmentation("whole"),
            attempt_id="fixed",
            mode="resume",
        )
    assert {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
