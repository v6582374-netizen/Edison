"""Fixed private evidence and honest evaluation of real preparation outputs."""

import json

import pytest

from youtube_strataread.workbench.experiment_evaluation import (
    ExpectedCheck,
    Sample,
    build_plan,
    evaluate_run,
    evaluate_with_judge,
    freeze_manifest,
    load_manifest,
    load_plan,
    sample_gaps,
    summarize_runs,
)


def test_fixed_manifest_keeps_private_source_identity_and_unknown_duration_honest(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:02,000\nAlice spoke for 25 minutes.\n")
    destination = tmp_path / "manifest.json"
    freeze_manifest(
        destination,
        [
            Sample(
                id="short",
                snapshot=source,
                video_id="synthetic",
                language="en",
                source_kind="synthetic",
                split="development",
                duration_seconds=None,
                duration_evidence=None,
                categories=("short-english", "number-dense"),
            )
        ],
    )
    manifest = load_manifest(destination)
    assert manifest["samples"][0]["bytes"] == 60
    assert manifest["samples"][0]["duration_seconds"] is None
    assert "approximately-2h-speech" in sample_gaps(manifest)
    assert "approximately-4h-speech" in sample_gaps(manifest)
    assert "held-out-speech" in sample_gaps(manifest)
    with pytest.raises(FileExistsError):
        freeze_manifest(destination, [])
    source.write_text("changed source")
    with pytest.raises(ValueError, match="hash"):
        load_manifest(destination)


def test_real_duration_evidence_and_frozen_plan_bound_the_comparison(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nA sample.\n")
    source4 = tmp_path / "source4.srt"
    source4.write_text("1\n00:00:00,000 --> 00:00:01,000\nAnother sample.\n")
    short = tmp_path / "short.srt"
    short.write_text("1\n00:00:00,000 --> 00:00:01,000\nA short English sample.\n")
    samples = [
        Sample(
            "two-hour",
            source,
            "v2",
            "en",
            "speech-captions",
            "development",
            7200,
            "copied official video duration",
            ("number-dense",),
        ),
        Sample(
            "four-hour",
            source4,
            "v4",
            "zh-en",
            "speech-captions",
            "held-out",
            14400,
            "copied official video duration",
            ("chinese-or-mixed", "multi-speaker"),
        ),
        Sample(
            "short",
            short,
            "vs",
            "en",
            "speech-captions",
            "development",
            60,
            "copied official video duration",
            ("short-english",),
        ),
    ]
    manifest = freeze_manifest(tmp_path / "manifest.json", samples)
    assert sample_gaps(manifest) == []
    plan = build_plan(
        tmp_path / "plan.json",
        manifest,
        model_identity={"id": "same-model"},
        settings_sha256="a" * 64,
        representative_ids=("two-hour",),
        repeats=2,
    )
    assert len(plan["runs"]) == 12
    assert [row["variant"] for row in plan["runs"][:3]] == ["A", "B", "C"]
    assert [row["variant"] for row in plan["runs"][3:6]] == ["B", "C", "A"]
    assert [row["variant"] for row in plan["runs"][6:9]] == ["C", "A", "B"]
    assert plan["changed_factor"] == "translation mechanism"
    assert plan["segmentation"] == "fixed-common-source-units"
    assert plan["manifest_sha256"] == manifest["identity_sha256"]
    assert load_plan(tmp_path / "plan.json", manifest)["identity_sha256"] == plan["identity_sha256"]
    with pytest.raises(ValueError, match="repeats"):
        build_plan(
            tmp_path / "too-many.json",
            manifest,
            model_identity={"id": "same-model"},
            settings_sha256="a" * 64,
            repeats=999,
        )
    tuned = Sample(
        "tuned", source, "v", "en", "speech-captions", "held-out", None, None, used_for_tuning=True
    )
    with pytest.raises(ValueError, match="held-out"):
        freeze_manifest(tmp_path / "tuned.json", [tuned])
    reused = Sample(
        "reused", source, "different-video", "en", "speech-captions", "held-out", None, None
    )
    with pytest.raises(ValueError, match="both"):
        freeze_manifest(tmp_path / "contaminated.json", [samples[0], reused])
    plan["runs"][0]["variant"] = "C"
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="hash"):
        load_plan(tmp_path / "plan.json", manifest)


def test_evaluation_detects_annotated_errors_in_actual_preparation_artifacts(tmp_path):
    from coworker.providers.base import AssistantTurn
    from youtube_strataread.workbench.experiment import CaptionSnapshot, ModelIdentity, run_baseline

    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nFirst check temperature.\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nAlice spoke for twenty five minutes. She did not reduce the dose.\n\n"
        "3\n00:00:04,000 --> 00:00:06,000\nThen discussion ended.\n"
    )
    checks = [
        ExpectedCheck("temperature", "omission", (1, 1), ("首先检查温度",)),
        ExpectedCheck("number", "number", (2, 2), ("二十五", "25")),
        ExpectedCheck("unit", "unit", (2, 2), ("分钟",)),
        ExpectedCheck("negation", "negation", (2, 2), ("没有减少剂量",)),
        ExpectedCheck("entity", "entity", (2, 2), ("爱丽丝",)),
        ExpectedCheck("addition", "addition", (2, 2), forbidden=("一百名观众",)),
        ExpectedCheck("ending", "order", (3, 3), ("随后讨论结束",)),
        ExpectedCheck("opening", "order", (1, 1), ("首先",)),
    ]
    for label, output in [
        ("good", "首先检查温度。爱丽丝讲了二十五分钟。她没有减少剂量。随后讨论结束。"),
        ("bad", "随后讨论结束。鲍勃讲了二十六小时。她已经减少剂量。首先计划变化。另有一百名观众。"),
    ]:
        responses = iter([output, "检查忠实性。", output, output])
        record = run_baseline(
            tmp_path / label,
            CaptionSnapshot(source, "synthetic", label, "en"),
            ModelIdentity("fake-model", "test", "a" * 64, True),
            lambda *_args, responses=responses, **_kwargs: AssistantTurn(
                text=next(responses), finish_reason="stop"
            ),
        )
        result = evaluate_run(record, checks, source_kind="synthetic")
        assert result["dimensions"]["readability"] == "unknown"
        if label == "good":
            assert result["dimensions"]["number"] == "pass"
            assert result["failed_checks"] == []
        else:
            for dimension in (
                "omission",
                "addition",
                "number",
                "unit",
                "negation",
                "entity",
                "order",
            ):
                assert result["dimensions"][dimension] == "fail"
        assert result["semantic_fidelity_certified"] is False
        assert result["method"]["id"] == "annotated-literal-checks-v1"
        if label == "bad":
            judged = evaluate_with_judge(
                record,
                lambda evidence: {
                    "number": {
                        "status": "fail",
                        "source_cues": [2, 2],
                        "source_quote": "twenty five",
                        "artifact": "translation",
                        "output_quote": "二十六",
                    }
                },
                model_identity={"id": "synthetic-judge", "simulated": True},
                method_id="source-aligned-example-v1",
                limitations="Synthetic judge; no evidence about real translation accuracy.",
            )
            assert judged["dimensions"]["number"] == "fail"
            assert judged["dimensions"]["readability"] == "unknown"
            assert judged["method"]["judge_model"]["simulated"] is True
            assert judged["semantic_fidelity_certified"] is False


def test_summary_preserves_unknown_usage_and_missing_stage_measurements(tmp_path):
    paths = []
    for i, elapsed in enumerate((1.0, 9.0)):
        path = tmp_path / f"run-{i}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": str(i),
                    "variant": "A",
                    "source": {"sha256": "a" * 64},
                    "model": {"id": "fixed-model", "simulated": True},
                    "settings": {"country": ""},
                    "state": "ready" if i == 0 else "failed",
                    "reason": None if i == 0 else "truncated",
                    "attempts": 4 if i == 0 else 3,
                    "elapsed_seconds": elapsed,
                    "usage": {
                        "reported": {"input": 10, "output": 5, "cache_read": 2, "cache_write": 0}
                        if i == 0
                        else None,
                        "cost": None,
                    },
                }
            )
        )
        paths.append(path)
    summary = summarize_runs(tmp_path / "summary.json", paths)
    assert summary["runs"][0]["stage_elapsed_seconds"] is None
    assert summary["runs"][0]["estimated_tokens"] is None
    assert summary["runs"][1]["reported_usage"] is None
    assert summary["runs"][1]["quality"] is None
    assert summary["variants"]["A"]["ready"] == 1
    assert summary["variants"]["A"]["total"] == 2
    assert summary["variants"]["A"]["attempts"] == 7
    assert summary["variants"]["A"]["elapsed_seconds"]["p50"] == 5.0
    assert summary["variants"]["A"]["elapsed_seconds"]["p95"] == pytest.approx(8.6)
    assert summary["variants"]["A"]["reported_usage"] == {
        "input": 10,
        "output": 5,
        "cache_read": 2,
        "cache_write": 0,
    }
    assert summary["variants"]["A"]["runs_without_usage"] == 1
    assert summary["variants"]["A"]["cost"] is None
    assert "descriptive" in summary["quantile_method"]
    with pytest.raises(ValueError, match="Duplicate"):
        summarize_runs(tmp_path / "duplicated.json", [paths[0], paths[0]])
