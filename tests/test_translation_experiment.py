"""Isolated baseline behavior through PreparationService and real persistence."""

import hashlib
import json
from pathlib import Path

from translation_fixture import EchoModel

from youtube_strataread.workbench.experiment import CaptionSnapshot, ModelIdentity, run_baseline
from youtube_strataread.workbench.workspace import LocalWorkspace

SOURCE = b"1\n00:00:00,000 --> 00:00:02,000\nSource with 42 people.\n"


def snapshot(tmp_path):
    path = tmp_path / "private.srt"
    path.write_bytes(SOURCE)
    return CaptionSnapshot(path=path, video_id="sample", title="Private sample", language="en")


def identity():
    return ModelIdentity("mock-model", "test-double-v1", "0" * 64, simulated=True)


def test_explicit_baseline_retains_source_translation_manuscript_and_reproducible_record(tmp_path):
    source = snapshot(tmp_path)
    result = run_baseline(tmp_path / "experiment", source, identity(), EchoModel())
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    ws = LocalWorkspace.open(tmp_path / "experiment" / "workspace")
    doc = ws.document("sample")
    assert Path(doc["translation_path"]).read_text() == "Source with 42 people.\n"
    assert Path(doc["path"]).read_text() == "Source with 42 people.\n"
    assert ws.retained_transcript("sample")["content"].encode() == SOURCE
    assert source.path.read_bytes() == SOURCE
    assert (result.parent / record["artifacts"]["raw_captions"]).read_bytes() == SOURCE
    assert record["source"]["sha256"] == hashlib.sha256(SOURCE).hexdigest()
    assert record["model"]["id"] == "mock-model"
    assert record["variant"] == "A-current-baseline"
    assert record["attempts"] == 4
    assert record["usage"]["reported"] == {
        "input": 400,
        "output": 400,
        "cache_read": 0,
        "cache_write": 0,
    }
    assert record["usage"]["cost"] is None
    assert record["settings"]["max_calls"] == 240
    assert record["settings"]["prompts"]["initial"]["system"]
    assert record["repository"]["base_commit"].startswith("dccf993")
    assert record["upstream"]["commit"] == "e0fc605acbb5d78cb7a58a98bc8bd8f0056df49c"
    assert record["fair_long_comparison"] is False


def test_production_path_and_its_ancestors_are_rejected_before_any_write(tmp_path, monkeypatch):
    import pytest

    source = snapshot(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    sentinel = production / "settings.json"
    sentinel.write_text("do not change")
    monkeypatch.setenv("YOUTUBE_WORKBENCH_WORKSPACE", str(production))
    for target in (production, production / "experiments", tmp_path):
        with pytest.raises(ValueError, match="production"):
            run_baseline(target, source, identity(), EchoModel())
    assert sorted(p.name for p in production.iterdir()) == ["settings.json"]
    assert sentinel.read_text() == "do not change"
    assert not (tmp_path / "runs").exists()


def test_changed_snapshot_cannot_be_mislabeled_or_overwrite_a_successful_version(tmp_path):
    import pytest

    source = snapshot(tmp_path)
    root = tmp_path / "experiment"
    run_baseline(root, source, identity(), EchoModel())
    first = LocalWorkspace.open(root / "workspace").document("sample")
    source.path.write_text("1\n00:00:00,000 --> 00:00:02,000\nDifferent source.\n")
    with pytest.raises(ValueError, match="snapshot"):
        run_baseline(root, source, identity(), EchoModel())
    assert LocalWorkspace.open(root / "workspace").document("sample") == first
    assert len(list((root / "runs").iterdir())) == 1


def test_existing_storage_cannot_redirect_writes_through_symlinks_or_hardlinks(tmp_path):
    import pytest

    source = snapshot(tmp_path)
    root = tmp_path / "experiment"
    run_baseline(root, source, identity(), EchoModel())
    outside = tmp_path / "untouched.txt"
    outside.write_text("production sentinel")
    redirect = root / "workspace" / "redirect"
    redirect.symlink_to(outside)
    with pytest.raises(ValueError, match="link"):
        run_baseline(root, source, identity(), EchoModel())
    redirect.unlink()
    redirect.hardlink_to(outside)
    with pytest.raises(ValueError, match="link"):
        run_baseline(root, source, identity(), EchoModel())
    assert outside.read_text() == "production sentinel"
    assert len(list((root / "runs").iterdir())) == 1


def test_failed_regeneration_preserves_history_and_records_actual_attempts(tmp_path, monkeypatch):
    source = snapshot(tmp_path)
    root = tmp_path / "experiment"
    monkeypatch.setenv("BY_PROMPTS_DIR", str(tmp_path / "must-not-create-prompts"))
    production = tmp_path / "production"
    production.mkdir()
    sentinel = production / "settings.json"
    sentinel.write_text("unchanged")
    monkeypatch.setenv("COWORKER_STATE_DIR", str(production))
    first_record = run_baseline(root, source, identity(), EchoModel())
    first = LocalWorkspace.open(root / "workspace").document("sample")
    truncated = EchoModel()
    truncated.truncate_at = 3
    failed_record = run_baseline(root, source, identity(), truncated)
    failed = json.loads(failed_record.read_text())
    assert failed["state"] == "failed"
    assert "截断" in failed["reason"]
    assert failed["attempts"] == 3
    assert "manuscript" not in failed["artifacts"]
    assert LocalWorkspace.open(root / "workspace").document("sample") == first
    assert json.loads(first_record.read_text())["state"] == "ready"
    third_record = run_baseline(root, source, identity(), EchoModel())
    assert json.loads(third_record.read_text())["artifacts"]["manuscript_version"] == 2
    assert Path(first["path"]).read_text() == "Source with 42 people.\n"
    assert source.path.read_bytes() == SOURCE
    assert not (tmp_path / "must-not-create-prompts").exists()
    assert sentinel.read_text() == "unchanged"
    assert sorted(p.name for p in production.iterdir()) == ["settings.json"]


def test_unknown_usage_is_not_reported_as_zero_cost_or_measured_tokens(tmp_path):
    model = EchoModel()
    model.usage = None
    record = json.loads(
        run_baseline(tmp_path / "experiment", snapshot(tmp_path), identity(), model).read_text()
    )
    assert record["state"] == "ready"
    assert record["usage"]["reported"] is None
    assert record["usage"]["requests_with_reported_usage"] == 0
    assert record["usage"]["requests_without_reported_usage"] == 4
    assert record["usage"]["cost"] is None


def test_mock_cli_runs_once_from_private_snapshot_without_any_network(
    tmp_path, monkeypatch, capsys
):
    import socket

    from youtube_strataread.workbench.experiment import main

    def network_forbidden(*_args, **_kwargs):
        raise AssertionError("offline experiment must not use the network")

    monkeypatch.setattr(socket.socket, "connect", network_forbidden)
    source = snapshot(tmp_path)
    responses = tmp_path / "responses.json"
    responses.write_text(
        json.dumps(
            [
                {"text": "有 42 人。"},
                {"text": "保留数字。"},
                {"text": "有 42 人。"},
                {"text": "# 参与者\n\n有 42 人。"},
            ]
        )
    )
    assert (
        main(
            [
                "--root",
                str(tmp_path / "experiment"),
                "--snapshot",
                str(source.path),
                "--video-id",
                "sample",
                "--language",
                "en",
                "--mock-responses",
                str(responses),
            ]
        )
        == 0
    )
    record_path = Path(json.loads(capsys.readouterr().out)["record"])
    record = json.loads(record_path.read_text())
    assert record["model"]["simulated"] is True
    assert (record_path.parent / "translation.md").read_text() == "有 42 人。\n"
    assert (record_path.parent / "manuscript.md").read_text() == "# 参与者\n\n有 42 人。\n"


def test_default_and_configured_production_roots_and_unowned_workspace_are_refused(
    tmp_path, monkeypatch
):
    import pytest
    from platformdirs import user_data_dir

    from coworker.secrets import state_dir

    source = snapshot(tmp_path)
    configured = tmp_path / "configured-state"
    monkeypatch.setenv("COWORKER_STATE_DIR", str(configured))
    forbidden = [
        configured / "youtube",
        Path.home() / ".config" / "coworker" / "youtube",
        Path(user_data_dir("youtube-reading-workbench")) / "workspace",
        state_dir(),
    ]
    for path in forbidden:
        with pytest.raises(ValueError, match="production"):
            run_baseline(path, source, identity(), EchoModel())
    assert not configured.exists()
    unowned = tmp_path / "unowned"
    LocalWorkspace.open(unowned)
    before = (unowned / "workspace.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="not owned"):
        run_baseline(unowned, source, identity(), EchoModel())
    assert (unowned / "workspace.sqlite3").read_bytes() == before
    assert not (unowned / "experiment.json").exists()


def test_local_budget_is_retained_and_request_failures_remain_private(tmp_path):
    import copy

    from youtube_strataread.workbench.translation import DEFAULTS

    source = snapshot(tmp_path)
    config = copy.deepcopy(DEFAULTS)
    config["max_calls"] = 1
    record_path = run_baseline(
        tmp_path / "experiment", source, identity(), EchoModel(), config=config, historical_diagnostic=True
    )
    record = json.loads(record_path.read_text())
    assert record["state"] == "failed"
    assert "超出处理预算" in record["reason"]
    assert record["attempts"] == 1
    assert record["fair_long_comparison"] is False

    def fail(*_args, **_kwargs):
        raise ValueError("private credential must not be copied")

    failure_path = run_baseline(tmp_path / "failed-experiment", source, identity(), fail)
    assert json.loads(failure_path.read_text())["attempts"] == 1
    for path in failure_path.parent.glob("*.json"):
        assert "private credential" not in path.read_text()
    assert (
        json.loads((failure_path.parent / "request-0001.json").read_text())["error_type"]
        == "ValueError"
    )
