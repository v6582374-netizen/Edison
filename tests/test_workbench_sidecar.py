from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from youtube_strataread.downloader.youtube import SubtitleResult
from youtube_strataread.workbench.discovery import Candidate
from youtube_strataread.workbench.library import LibraryService, PreparationService
from youtube_strataread.workbench.sidecar import handle_request
from youtube_strataread.workbench.workspace import LocalWorkspace


@dataclass
class _Captions:
    def acquire(self, url: str) -> SubtitleResult:
        return SubtitleResult(
            video_id="fixture",
            title="Fixture",
            language="zh-Hans",
            is_auto=False,
            srt_text="1\n00:00:00,000 --> 00:00:02,000\n一段可追溯的字幕\n",
        )


@dataclass
class _Manuscripts:
    def generate(self, transcript: str) -> str:
        return "# 可交接的 Markdown\n\n正文。\n"


def test_sidecar_serves_repeated_empty_library_snapshots(tmp_path: Path) -> None:
    workspace = tmp_path / "library"
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        "YOUTUBE_WORKBENCH_WORKSPACE": str(workspace),
        "YOUTUBE_WORKBENCH_TEST_VAULT": "1",
    }
    requests = "\n".join(
        [
            json.dumps({"id": "first", "capability": "library.snapshot", "arguments": {}}),
            json.dumps({"id": "second", "capability": "library.snapshot", "arguments": {}}),
            json.dumps({"id": "connection", "capability": "connection.status", "arguments": {}}),
            json.dumps(
                {
                    "id": "configure",
                    "capability": "connection.configure",
                    "arguments": {
                        "client_id": "desktop-client",
                        "client_secret": "not-a-real-secret",
                    },
                }
            ),
            json.dumps({"id": "configured", "capability": "connection.status", "arguments": {}}),
            json.dumps(
                {"id": "sources", "capability": "collection.subscription_sources", "arguments": {}}
            ),
            json.dumps(
                {"id": "refresh", "capability": "collection.refresh_updates", "arguments": {}}
            ),
        ]
    )

    result = subprocess.run(
        [sys.executable, "-m", "youtube_strataread.workbench.sidecar"],
        input=f"{requests}\n",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert responses == [
        {
            "id": "first",
            "ok": True,
            "result": {
                "workspace": {"label": "Local Library", "status": "ready"},
                "counts": {"inbox": 0, "to_read": 0, "reading": 0, "read": 0},
                "inbox": [],
            },
        },
        {
            "id": "second",
            "ok": True,
            "result": {
                "workspace": {"label": "Local Library", "status": "ready"},
                "counts": {"inbox": 0, "to_read": 0, "reading": 0, "read": 0},
                "inbox": [],
            },
        },
        {
            "id": "connection",
            "ok": True,
            "result": {
                "configured": False,
                "authorized": False,
                "subscription_count": 0,
                "subscription_capability": {"state": "unverified", "last_success_at": None, "last_failure_at": None, "failure_kind": None, "reason": None, "last_failure_kind": None, "last_failure_reason": None},
            },
        },
        {
            "id": "configure",
            "ok": True,
            "result": {
                "configured": True,
                "authorized": False,
                "subscription_count": 0,
                "subscription_capability": {"state": "unverified", "last_success_at": None, "last_failure_at": None, "failure_kind": None, "reason": None, "last_failure_kind": None, "last_failure_reason": None},
            },
        },
        {
            "id": "configured",
            "ok": True,
            "result": {
                "configured": True,
                "authorized": False,
                "subscription_count": 0,
                "subscription_capability": {"state": "unverified", "last_success_at": None, "last_failure_at": None, "failure_kind": None, "reason": None, "last_failure_kind": None, "last_failure_reason": None},
            },
        },
        {
            "id": "sources",
            "ok": True,
            "result": {"sources": []},
        },
        {
            "id": "refresh",
            "ok": True,
            "result": {"discovered": 0, "scanned_sources": 0, "truncated": True, "status": "authorization", "error": "YouTube 授权失效，请重新连接。"},
        },
    ]
    assert "not-a-real-secret" not in result.stdout
    assert workspace.is_dir()


def test_frozen_sidecar_rejects_the_test_vault(monkeypatch) -> None:
    import youtube_strataread.workbench.sidecar as sidecar

    sentinel = object()
    monkeypatch.setenv("YOUTUBE_WORKBENCH_TEST_VAULT", "1")
    monkeypatch.setattr(sidecar.sys, "frozen", True, raising=False)
    monkeypatch.setattr(sidecar, "NativeKeychainVault", lambda **kwargs: sentinel)

    assert sidecar._vault() is sentinel


def test_sidecar_library_capabilities_expose_the_prepared_asset_contract(tmp_path: Path) -> None:
    workspace = LocalWorkspace.open(tmp_path / "workspace")
    from test_workbench_library import candidate
    workspace.set_meta("drain_paused", "0")
    workspace.add_candidate(candidate("fixture"))
    preparation = PreparationService(
        workspace=workspace, captions=_Captions(), manuscripts=_Manuscripts()
    )
    from shorts_fixture import RegularVideos
    preparation.shorts = RegularVideos()
    library = LibraryService(workspace=workspace, preparation=preparation)
    assert preparation.run_next() is True

    listed = handle_request(
        {"id": "list", "capability": "library.list", "arguments": {"reading_state": "inbox"}},
        workspace,
        None,
        None,
        library,  # type: ignore[arg-type]
    )
    document = handle_request(
        {"id": "document", "capability": "documents.get", "arguments": {"video_id": "fixture"}},
        workspace,
        None,
        None,
        library,  # type: ignore[arg-type]
    )
    inspected = handle_request(
        {"id": "inspect", "capability": "library.inspect", "arguments": {"video_id": "fixture"}},
        workspace,
        None,
        None,
        library,  # type: ignore[arg-type]
    )
    moved = handle_request(
        {
            "id": "move",
            "capability": "library.set_reading_state",
            "arguments": {"video_id": "fixture", "reading_state": "to-read"},
        },
        workspace,
        None,
        None,
        library,  # type: ignore[arg-type]
    )

    assert listed["ok"] is True
    assert listed["result"]["total"] == 1  # type: ignore[index]
    assert listed["result"]["assets"][0]["preparation_state"] == "ready"  # type: ignore[index]
    assert document["result"]["markdown"].startswith("# 可交接")  # type: ignore[index]
    assert inspected["result"]["generation_records"][0]["manuscript_version"] == 1  # type: ignore[index]
    assert moved["result"]["reading_state"] == "to-read"  # type: ignore[index]


def test_connection_save_through_sidecar_and_vault_stdin(tmp_path: Path) -> None:
    vault = tmp_path / "av"
    vault.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        'if sys.argv[1:3] != ["save", "--stdin"]: sys.exit(2)\n'
        'if sys.stdin.read() not in ("fixture-client", "fixture-secret"): sys.exit(3)\n'
    )
    vault.chmod(0o700)
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        "YOUTUBE_WORKBENCH_WORKSPACE": str(tmp_path / "workspace"),
        "YOUTUBE_WORKBENCH_AUTOMIC_VAULT": str(vault),
    }
    environment.pop("YOUTUBE_WORKBENCH_TEST_VAULT", None)
    binary = os.environ.get("WORKBENCH_TEST_BINARY")
    command = [binary] if binary else [sys.executable, "-m", "youtube_strataread.workbench.sidecar"]
    result = subprocess.run(
        command,
        input=json.dumps(
            {
                "id": "save",
                "capability": "connection.configure",
                "arguments": {"client_id": "fixture-client", "client_secret": "fixture-secret"},
            }
        )
        + "\n",
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    response = json.loads(result.stdout)
    assert response["ok"], response
    assert response["result"]["configured"]
    assert "fixture-secret" not in result.stdout + result.stderr
