"""Source fidelity at the confirmed preparation boundary, with real persistence."""

import json

from translation_fixture import EchoModel

from youtube_strataread.workbench.experiment import CaptionSnapshot, ModelIdentity, run_baseline
from youtube_strataread.workbench.workspace import LocalWorkspace

IDENTITY = ModelIdentity("mock-model", "test-v1", "0" * 64, simulated=True)


def test_preparation_preserves_qualifications_speakers_repetition_and_timed_source(tmp_path):
    raw = (
        "1\n00:00:00,000 --> 00:00:02,000\n>> Ada: We need 25 [not 30] units (unless delayed).\n\n"
        "2\n00:00:03,000 --> 00:00:05,000\n- Ben: Never. Never.\n\n"
        "3\n00:00:10,000 --> 00:00:12,000\n>> Ada: We need 25 [not 30] units (unless delayed).\n"
    )
    source = tmp_path / "private.srt"
    source.write_text(raw)
    result = run_baseline(
        tmp_path / "run", CaptionSnapshot(source, "sample", "Private", "en"), IDENTITY, EchoModel()
    )
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    expected = (
        ">> Ada: We need 25 [not 30] units (unless delayed).\n"
        "- Ben: Never. Never.\n"
        ">> Ada: We need 25 [not 30] units (unless delayed).\n"
    )
    assert (result.parent / "translation.md").read_text() == expected
    assert LocalWorkspace.open(tmp_path / "run" / "workspace").retained_transcript("sample")["content"] == raw
    normalized = json.loads((result.parent / record["artifacts"]["normalized_source"]).read_text())
    assert normalized["policy"] == "conservative-v1"
    assert normalized["text"] == expected.rstrip()
    assert [(u["ordinal"], u["start_ms"], u["end_ms"]) for u in normalized["units"]] == [
        (1, 0, 2000), (2, 3000, 5000), (3, 10000, 12000)
    ]
    assert normalized["units"][0]["source_char_range"] == [0, 51]
    assert source.read_text() == raw


def test_annotated_rolling_overlap_only_removes_verified_prefix_and_keeps_later_repetition(tmp_path):
    raw = (
        "1\n00:00:00,000 --> 00:00:03,000\nWe need 25 units\n\n"
        "2\n00:00:02,000 --> 00:00:05,000\n25 units unless delayed.\n\n"
        "3\n00:00:06,000 --> 00:00:08,000\nWe need 25 units\n"
    )
    source = tmp_path / "private.srt"
    source.write_text(raw)
    snapshot = CaptionSnapshot(source, "sample", "Private", "en", verified_rolling_pairs=((1, 2),))
    result = run_baseline(tmp_path / "run", snapshot, IDENTITY, EchoModel())
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert (result.parent / "translation.md").read_text() == "We need 25 units\nunless delayed.\nWe need 25 units\n"
    normalized = json.loads((result.parent / record["artifacts"]["normalized_source"]).read_text())
    assert normalized["units"][1]["overlap_of"] == "cue-000001"
    assert normalized["units"][1]["source_char_range"] == [9, 24]
    assert normalized["units"][2]["overlap_of"] is None


def test_chat_only_snapshot_is_unavailable_without_model_calls_and_retains_raw_source(tmp_path):
    raw = '{"live_chat": "Viewer: hello"}'
    source = tmp_path / "chat.json"
    source.write_text(raw)
    model = EchoModel()
    result = run_baseline(
        tmp_path / "run", CaptionSnapshot(source, "chat", "Private", "live_chat", source_type="live_chat"),
        IDENTITY, model,
    )
    record = json.loads(result.read_text())
    assert record["state"] == "unavailable"
    assert record["reason"] == "暂无可用字幕，未处理"
    assert record["attempts"] == 0
    assert not model.calls
    assert (result.parent / "source.srt").read_text() == raw
    assert "translation" not in record["artifacts"]


def test_missing_snapshot_captions_is_unavailable_without_generation(tmp_path):
    source = tmp_path / "empty.srt"
    source.write_text("")
    result = run_baseline(
        tmp_path / "run", CaptionSnapshot(source, "empty", "Private", "en"), IDENTITY, EchoModel()
    )
    record = json.loads(result.read_text())
    assert record["state"] == "unavailable"
    assert record["reason"] == "暂无可用字幕，未处理"
    assert record["attempts"] == 0


def test_actual_acquisition_adapter_rejects_chat_only_before_download_and_preparation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from test_youtube_downloader import _install_fake_ytdlp

    from coworker.server.youtube import HostManuscripts
    from youtube_strataread.workbench.discovery import Candidate
    from youtube_strataread.workbench.experiment_source import normalize_captions
    from youtube_strataread.workbench.library import PreparationService, YtDlpCaptions

    for case, subtitles in [("chat-only", {"live_chat": [{}]}), ("missing", {})]:
        seen_opts = []
        _install_fake_ytdlp(
            monkeypatch,
            info={"id": case, "title": "Private", "subtitles": subtitles,
                  "automatic_captions": {}, "live_status": "not_live"},
            files_by_lang={}, seen_opts=seen_opts,
        )
        ws = LocalWorkspace.open(tmp_path / case)
        ws.add_candidate(Candidate(case, "source", "Private", "Private", f"https://youtu.be/{case}", "1970-01-01T00:00:00+00:00", 0))
        ws.queue_regeneration(case)
        model = EchoModel()
        service = PreparationService(
            ws, YtDlpCaptions(speech_only=True),
            HostManuscripts(SimpleNamespace(model="mock", provider_complete=model), ws),
            shorts=SimpleNamespace(classify=lambda _video: False),
            transcript_normalizer=lambda text, _language: normalize_captions(text).text,
        )
        assert service.run_next()
        asset = ws.asset(case)
        assert asset["preparation_state"] == "unavailable"
        assert asset["failure_reason"] == "暂无可用字幕，未处理"
        assert not model.calls
        assert len(seen_opts) == 1  # metadata probe only, no chat download/conversion
        assert ws.retained_transcript(case) is None


def test_unverified_temporal_overlap_is_retained_and_shared_snapshot_is_stable(tmp_path):
    source = tmp_path / "private.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\nWe need 25 units\n\n"
        "2\n00:00:02,000 --> 00:00:05,000\n25 units unless delayed.\n"
    )
    snapshot = CaptionSnapshot(source, "sample", "Private", "en")
    first = run_baseline(tmp_path / "first", snapshot, IDENTITY, EchoModel())
    second = run_baseline(tmp_path / "second", snapshot, IDENTITY, EchoModel())
    normalized = json.loads((first.parent / "normalized-source.json").read_text())
    assert normalized == json.loads((second.parent / "normalized-source.json").read_text())
    assert normalized["text"] == "We need 25 units\n25 units unless delayed."
    assert all(u["overlap_of"] is None for u in normalized["units"])
    assert (first.parent / "normalized-source.json").stat().st_mode & 0o222 == 0


def test_invalid_verified_pair_cannot_erase_intentional_repetition(tmp_path):
    source = tmp_path / "private.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nWe need 25 units\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\nWe need 25 units\n"
    )
    result = run_baseline(
        tmp_path / "run", CaptionSnapshot(source, "sample", "Private", "en", verified_rolling_pairs=((1, 2),)),
        IDENTITY, EchoModel(),
    )
    record = json.loads(result.read_text())
    assert record["state"] == "failed"
    assert record["attempts"] == 0
    assert "translation" not in record["artifacts"]
    assert "overlap in time" in record["reason"]


def test_chat_language_cannot_be_reclassified_as_speech_by_omitting_source_type(tmp_path):
    source = tmp_path / "chat.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:02,000\nViewer: hello.\n")
    result = run_baseline(tmp_path / "run", CaptionSnapshot(source, "sample", "Private", "live_chat"), IDENTITY, EchoModel())
    record = json.loads(result.read_text())
    assert record["state"] == "unavailable"
    assert record["reason"] == "暂无可用字幕，未处理"
    assert record["attempts"] == 0


def test_historical_cleanup_is_separate_from_fixed_main_comparison_source(tmp_path):
    source = tmp_path / "private.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:02,000\nAda: 25 units (unless delayed).\n")
    result = run_baseline(
        tmp_path / "historical", CaptionSnapshot(source, "sample", "Private", "en"), IDENTITY,
        EchoModel(), source_policy="historical",
    )
    record = json.loads(result.read_text())
    assert record["source_processing"]["policy"] == "historical"
    assert record["source_processing"]["normalized_sha256"] is None
    assert (result.parent / "translation.md").read_text() == "Ada: 25 units .\n"
    assert "normalized_source" not in record["artifacts"]
