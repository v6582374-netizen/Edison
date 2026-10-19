"""Atomic admission contracts supplement the real Web/host acceptance test."""
import threading

import pytest

from youtube_strataread.workbench.connection import ConnectionService
from youtube_strataread.workbench.cutover import CutoverBlocked, CutoverHold
from youtube_strataread.workbench.sidecar import _TestVault
from youtube_strataread.workbench.workspace import LocalWorkspace


def test_a_new_legacy_owner_cannot_write_credentials_during_durable_cutover(tmp_path):
    workspace = LocalWorkspace.open(tmp_path)
    hold = CutoverHold(workspace)
    hold.begin(threading.Event())
    reopened = LocalWorkspace.open(tmp_path)
    connection = ConnectionService(workspace=reopened, vault=_TestVault(), oauth=object())
    with pytest.raises(CutoverBlocked, match="切换暂停"):
        connection.configure(client_id="fixture", client_secret="fixture")


def test_slow_browser_close_does_not_extend_deadline_or_hide_new_withdrawal(tmp_path):
    workspace = LocalWorkspace.open(tmp_path)
    clock = [1000.0]
    workspace.now = lambda: clock[0]
    hold = CutoverHold(workspace)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def close_browser():
        entered.set()
        assert release.wait(3)
        finished.set()

    hold.begin(threading.Event())
    hold.observe_drain((), close_browser)
    assert entered.wait(2)
    try:
        clock[0] = 2800.0
        assert hold.status()["state"] == "aborted"
        with hold.work(withdrawal=True):
            release.set()
            assert finished.wait(2)
            assert not hold.status()["drained"]
    finally:
        release.set()
    hold.wait_idle()
    assert not hold.status()["drained"]
    hold.observe_drain((), lambda: None)
    hold.wait_idle()
    assert hold.status()["drained"]
    assert hold.status()["state"] == "aborted"


def test_failed_browser_close_aborts_without_a_busy_retry_loop(tmp_path):
    hold = CutoverHold(LocalWorkspace.open(tmp_path))
    calls = []

    def failed_close():
        calls.append(True)
        raise OSError("controlled external browser close failure")

    hold.begin(threading.Event())
    hold.observe_drain((), failed_close)
    hold.wait_idle()
    assert hold.status()["state"] == "aborted"
    assert not hold.status()["drained"]
    hold.observe_drain((), failed_close)
    hold.wait_idle()
    assert len(calls) == 1


def test_legacy_dispatch_rejects_new_work_even_without_live_server(tmp_path):
    from youtube_strataread.workbench.discovery import SubscriptionDiscovery
    from youtube_strataread.workbench.library import LibraryService, PreparationService
    from youtube_strataread.workbench.sidecar import handle_request

    workspace = LocalWorkspace.open(tmp_path)
    CutoverHold(workspace).begin(threading.Event())
    reopened = LocalWorkspace.open(tmp_path)
    connection = ConnectionService(workspace=reopened, vault=_TestVault(), oauth=object())
    discovery = SubscriptionDiscovery(workspace=reopened, source=object())
    preparation = PreparationService(workspace=reopened, captions=object(), manuscripts=object())
    library = LibraryService(workspace=reopened, preparation=preparation)
    result = handle_request({'capability': 'library.regenerate', 'arguments': {'video_id': 'abcdefghijk'}},
                            reopened, connection, discovery, library)
    assert not result['ok']
    assert '切换暂停' in result['error']
