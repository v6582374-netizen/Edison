"""Atomic admission contracts supplement the real Web/host acceptance test."""
import threading
import json

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


def _verified_restart_hold(workspace, context):
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
    return hold


@pytest.fixture
def completion_ready(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from youtube_strataread.workbench import cutover

    clock = {'wall': 1000.0, 'monotonic': 3000.0}
    workspace = LocalWorkspace.open(tmp_path)
    workspace.now = lambda: clock['wall']
    monkeypatch.setattr(cutover, 'time', SimpleNamespace(monotonic=lambda: clock['monotonic']))
    context = {'build': 'fixture-build', 'uid': 1001, 'profile': 'fixture-profile'}
    hold = _verified_restart_hold(workspace, context)
    clock.update(wall=2799.99, monotonic=4799.99)
    return workspace, hold, clock, context


@pytest.mark.parametrize('boundary', ['verify', 'prepare_workers', 'save'])
@pytest.mark.parametrize('clock_source', ['wall', 'monotonic'])
def test_completion_crossing_maintenance_deadline_keeps_hold_and_cancels_pending_workers(
        tmp_path, monkeypatch, completion_ready, boundary, clock_source):
    workspace, hold, clock, context = completion_ready
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

    original_set_meta = workspace.set_meta

    def delayed_completion_write(key, value):
        if key == 'youtube_cutover' and json.loads(value).get('state') == 'completing' and boundary == 'save':
            cross_deadline()
        return original_set_meta(key, value)

    monkeypatch.setattr(workspace, 'set_meta', delayed_completion_write)
    with pytest.raises(CutoverBlocked):
        hold.complete_validation(verify_current, prepare_workers)
    assert events == ([] if boundary == 'verify' else ['prepared', 'cancelled'])
    state = hold.status()
    assert state['held'] and state['state'] == 'aborted'
    assert state['deadline_at'] == 2800.0
    restarted = CutoverHold(LocalWorkspace.open(tmp_path), context=context)
    assert restarted.status()['held'] and restarted.status()['state'] == 'aborted'


@pytest.mark.parametrize('clock_source', ['wall', 'monotonic'])
def test_late_completion_stage_remains_durably_held_when_abort_write_also_fails(
        tmp_path, monkeypatch, completion_ready, clock_source):
    import sqlite3

    workspace, hold, clock, context = completion_ready
    events = []
    writes_blocked = [False]
    actual_connect = sqlite3.connect

    class DelayedCommit(sqlite3.Connection):
        completion_write = False

        def execute(self, sql, parameters=()):
            if parameters and parameters[0] == 'youtube_cutover' and len(parameters) > 1:
                state = json.loads(parameters[1]).get('state')
                self.completion_write = state in {'completing', 'complete'}
            return super().execute(sql, parameters)

        def __exit__(self, *args):
            result = super().__exit__(*args)
            if self.completion_write:
                # The first completion-stage transaction really committed. Its
                # acknowledgement crosses the deadline; every later write fails.
                clock[clock_source] = 2800.01 if clock_source == 'wall' else 4800.01
                writes_blocked[0] = True
            return result

    def faulty_connect(*args, **kwargs):
        database = actual_connect(*args, factory=DelayedCommit, **kwargs)
        if writes_blocked[0]:
            database.execute('PRAGMA query_only=ON')
        return database

    with monkeypatch.context() as fault:
        fault.setattr(sqlite3, 'connect', faulty_connect)
        with pytest.raises((CutoverBlocked, sqlite3.Error)):
            hold.complete_validation(lambda proofs: None, lambda: (
                lambda: events.append('released'), lambda: events.append('cancelled')))
        assert events == ['cancelled']
        assert hold.status()['held'] and hold.status()['state'] == 'aborted'
        durable = json.loads(workspace.meta('youtube_cutover'))
        assert durable['held'] and durable['state'] == 'completing'
    restarted = CutoverHold(LocalWorkspace.open(tmp_path), context=context)
    assert restarted.status()['held'] and restarted.status()['state'] == 'aborted'


def test_failed_activation_receipt_keeps_completion_stage_held_after_restart(
        tmp_path, monkeypatch, completion_ready):
    import sqlite3

    workspace, hold, clock, context = completion_ready
    events = []
    actual_connect = sqlite3.connect

    class FailedReceipt(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if (parameters and parameters[0] == 'youtube_cutover' and len(parameters) > 1
                    and json.loads(parameters[1]).get('state') == 'complete'):
                super().execute('PRAGMA query_only=ON')
            return super().execute(sql, parameters)

    with monkeypatch.context() as fault:
        fault.setattr(sqlite3, 'connect', lambda *args, **kwargs:
                      actual_connect(*args, factory=FailedReceipt, **kwargs))
        with pytest.raises(sqlite3.Error):
            hold.complete_validation(lambda proofs: None, lambda: (
                lambda: events.append('released'), lambda: events.append('cancelled')))
    assert events == ['cancelled']
    assert hold.status()['held'] and hold.status()['state'] == 'completing'
    durable = json.loads(workspace.meta('youtube_cutover'))
    assert durable['held'] and durable['state'] == 'completing'
    restarted = CutoverHold(LocalWorkspace.open(tmp_path), context=context)
    assert restarted.status()['held'] and restarted.status()['state'] == 'aborted'


@pytest.mark.parametrize('clock_source', ['wall', 'monotonic'])
def test_receipt_acknowledgement_publishes_the_already_timely_activation_decision(
        monkeypatch, completion_ready, clock_source):
    import sqlite3

    workspace, hold, clock, context = completion_ready
    events = []
    actual_connect = sqlite3.connect

    class SlowReceipt(sqlite3.Connection):
        receipt = False

        def execute(self, sql, parameters=()):
            if parameters and parameters[0] == 'youtube_cutover' and len(parameters) > 1:
                self.receipt = json.loads(parameters[1]).get('state') == 'complete'
            return super().execute(sql, parameters)

        def __exit__(self, *args):
            result = super().__exit__(*args)
            if self.receipt:
                clock[clock_source] = 2800.01 if clock_source == 'wall' else 4800.01
            return result

    with monkeypatch.context() as fault:
        fault.setattr(sqlite3, 'connect', lambda *args, **kwargs:
                      actual_connect(*args, factory=SlowReceipt, **kwargs))
        result = hold.complete_validation(lambda proofs: None, lambda: (
            lambda: events.append('released'), lambda: events.append('cancelled')))
    assert events == ['released']
    assert result['state'] == 'complete' and not result['held']
    durable = json.loads(workspace.meta('youtube_cutover'))
    assert durable['activation_decided_at'] < durable['deadline_at']
    assert durable['activation_decided_monotonic_at'] < 4800.0
    assert 'publication_acknowledged_at' not in durable
    if clock_source == 'wall':
        assert result['publication_acknowledged_at'] > durable['deadline_at']
    else:
        assert result['publication_acknowledged_monotonic_at'] > 4800.0


def test_process_crash_between_completion_stage_and_activation_receipt_stays_held(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    program = r"""
import json, os, sqlite3, sys
from types import SimpleNamespace
from test_cutover_hold import _verified_restart_hold
from youtube_strataread.workbench import cutover
from youtube_strataread.workbench.workspace import LocalWorkspace
workspace = LocalWorkspace.open(__import__('pathlib').Path(sys.argv[1]))
workspace.now = lambda: 1000.0
cutover.time = SimpleNamespace(monotonic=lambda: 3000.0)
hold = _verified_restart_hold(workspace, {'build':'fixture-build','uid':1001,'profile':'fixture-profile'})
actual_connect = sqlite3.connect
class CrashAfterStage(sqlite3.Connection):
    stage = False
    def execute(self, sql, parameters=()):
        if parameters and parameters[0] == 'youtube_cutover' and len(parameters) > 1:
            self.stage = json.loads(parameters[1]).get('state') == 'completing'
        return super().execute(sql, parameters)
    def __exit__(self, *args):
        result = super().__exit__(*args)
        if self.stage:
            os._exit(23)
        return result
sqlite3.connect = lambda *args, **kwargs: actual_connect(*args, factory=CrashAfterStage, **kwargs)
hold.complete_validation(lambda proofs: None, lambda: (lambda: os._exit(99), lambda: None))
"""
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path)], timeout=10,
                            capture_output=True, text=True,
                            env={**os.environ, 'PYTHONPATH': os.pathsep.join(
                                str(repo / item) for item in ('tests', 'src', '.'))})
    assert result.returncode == 23, result.stderr
    workspace = LocalWorkspace.open(tmp_path)
    durable = json.loads(workspace.meta('youtube_cutover'))
    assert durable['held'] and durable['state'] == 'completing'
    restarted = CutoverHold(workspace)
    assert restarted.status()['held'] and restarted.status()['state'] == 'aborted'
