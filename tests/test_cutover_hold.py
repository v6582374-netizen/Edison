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


@pytest.mark.parametrize('deadline_expired', [False, True])
def test_storage_failure_during_drain_wakes_shutdown_and_never_reports_ready(tmp_path, monkeypatch, deadline_expired):
    import sqlite3

    workspace = LocalWorkspace.open(tmp_path)
    clock = [1000.0]
    workspace.now = lambda: clock[0]
    hold = CutoverHold(workspace)
    entered, release, waiting, finished = (threading.Event() for _ in range(4))

    def close_browser():
        entered.set()
        assert release.wait(3)

    def wait_for_shutdown():
        waiting.set()
        hold.wait_idle()
        finished.set()

    hold.begin(threading.Event())
    hold.observe_drain((), close_browser)
    assert entered.wait(2)
    waiter = threading.Thread(target=wait_for_shutdown, daemon=True)
    waiter.start()
    assert waiting.wait(2)
    assert not finished.wait(.05)
    real_connect = sqlite3.connect

    def read_only_storage(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.execute('PRAGMA query_only = ON')
        return connection

    try:
        with monkeypatch.context() as fault:
            # Fail at the real SQLite storage boundary, after the hold was
            # durably admitted and while shutdown waits for browser completion.
            fault.setattr(sqlite3, 'connect', read_only_storage)
            if deadline_expired:
                clock[0] = 2800.0
            release.set()
            assert finished.wait(2), 'completed drain stranded a shutdown waiter'
            state = hold.status()
            assert state['state'] == 'aborted'
            assert state['held'] and not state['drained']
            assert '保存' in state['reason']
            with pytest.raises(CutoverBlocked), hold.work():
                pass
    finally:
        release.set()
        # Clean up the intentionally stranded waiter on the red version.
        with hold.work(withdrawal=True):
            pass
        waiter.join(2)
    restarted = CutoverHold(LocalWorkspace.open(tmp_path))
    assert restarted.status()['held']
    assert restarted.status()['state'] == 'aborted'


@pytest.mark.parametrize('boundary', ['verify', 'prepare_workers'])
@pytest.mark.parametrize('clock_source', ['wall', 'monotonic'])
def test_completion_crossing_maintenance_deadline_keeps_hold_and_cancels_pending_workers(
        tmp_path, monkeypatch, boundary, clock_source):
    from types import SimpleNamespace
    from youtube_strataread.workbench import cutover

    clock = {'wall': 1000.0, 'monotonic': 3000.0}
    workspace = LocalWorkspace.open(tmp_path)
    workspace.now = lambda: clock['wall']
    monkeypatch.setattr(cutover, 'time', SimpleNamespace(monotonic=lambda: clock['monotonic']))
    context = {'build': 'fixture-build', 'uid': 1001, 'profile': 'fixture-profile'}
    hold = CutoverHold(workspace, context=context)
    hold.begin(threading.Event())
    hold.observe_drain((), lambda: None)
    hold.wait_idle()
    hold.begin_validation(context)
    # Prerequisite server receipts are supplied through the public hold protocol;
    # this focused test controls time at its completion callbacks, not a scheduler.
    with hold.validation_work() as revision:
        for key in ('official', 'initial', 'closed'):
            hold.validation_proof(revision, key, {'at': 1000.0})
    hold.prepare_restart()
    hold.seal()
    hold.closed_normally()
    hold = CutoverHold(workspace, context=context)
    with hold.validation_work() as revision:
        for key in ('official', 'restarted'):
            hold.validation_proof(revision, key, {'at': 1000.0})
    clock.update(wall=2799.99, monotonic=4799.99)
    events = []

    def cross_deadline():
        clock[clock_source] = 2800.01 if clock_source == 'wall' else 4800.01

    def verify_current(proofs):
        assert {'official', 'initial', 'closed', 'restarted'} <= proofs.keys()
        if boundary == 'verify':
            cross_deadline()

    def prepare_workers():
        events.append('prepared')
        if boundary == 'prepare_workers':
            cross_deadline()
        return lambda: events.append('released'), lambda: events.append('cancelled')

    with pytest.raises(CutoverBlocked):
        hold.complete_validation(verify_current, prepare_workers)
    assert events == ([] if boundary == 'verify' else ['prepared', 'cancelled'])
    state = hold.status()
    assert state['held'] and state['state'] == 'aborted'
    assert state['deadline_at'] == 2800.0
    restarted = CutoverHold(LocalWorkspace.open(tmp_path), context=context)
    assert restarted.status()['held'] and restarted.status()['state'] == 'aborted'
