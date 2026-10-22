"""Public resource admission boundary; only kernel observations are controlled."""
import pytest

from youtube_strataread.workbench.content_session import ContentSessionError
from youtube_strataread.workbench.resource_governor import ResourceGovernor, ResourceObservation


def test_memory_pressure_waits_then_recovers_without_granting_permission():
    observed = [ResourceObservation(available=1024**3, browser_unreclaimable=0, browser_swap=0, constrained=True)]
    governor = ResourceGovernor(observe=lambda: observed[0])
    with pytest.raises(ContentSessionError, match="内存") as error:
        governor.require_browser_start()
    assert error.value.kind == "resource"
    assert governor.status()["browser_waiting"]
    observed[0] = ResourceObservation(available=2*1024**3, browser_unreclaimable=0, browser_swap=0, constrained=True)
    governor.require_browser_start()
    assert not governor.status()["browser_waiting"]


def test_live_pressure_releases_browser_without_confusing_startup_reserve():
    observed = [ResourceObservation(available=800*1024**2, browser_unreclaimable=400*1024**2, browser_swap=0, constrained=True)]
    governor = ResourceGovernor(observe=lambda: observed[0])
    assert not governor.status()["release_browser"]
    governor.require_captions()
    observed[0] = ResourceObservation(available=800*1024**2, browser_unreclaimable=1100*1024**2, browser_swap=0, constrained=True)
    assert governor.status()["release_browser"]
    with pytest.raises(ContentSessionError, match="释放"):
        governor.require_captions()


def test_running_host_floor_is_384_mib_while_launch_still_needs_1_25_gib():
    observed = [ResourceObservation(384*1024**2, 0, 0, True)]
    governor = ResourceGovernor(observe=lambda: observed[0])
    state = governor.status()
    assert not state["release_browser"] and not state["captions_waiting"] and state["browser_waiting"]
    governor.require_captions()
    observed[0] = ResourceObservation(383*1024**2, 0, 0, True)
    assert governor.status()["release_browser"]
    with pytest.raises(ContentSessionError, match="可用内存不足"):
        governor.require_captions()
    observed[0] = ResourceObservation(1279*1024**2, 0, 0, True)
    with pytest.raises(ContentSessionError, match="1.25 GiB"):
        governor.require_browser_start()
    observed[0] = ResourceObservation(1280*1024**2, 0, 0, True)
    governor.require_browser_start()


def test_kernel_contract_rejects_unbounded_display_group_and_host_group(tmp_path):
    from youtube_strataread.workbench.resource_governor import LinuxResources
    proc = tmp_path / 'proc'; proc.mkdir()
    (proc / 'meminfo').write_text('MemAvailable: 1572864 kB\n')
    (proc / 'self').mkdir()
    (proc / 'self/cgroup').write_text('0::/edison/backend\n')
    cgroups = tmp_path / 'cgroup'
    group = cgroups / 'edison/browser'; group.mkdir(parents=True)
    for name, value in {'memory.max':'1342177280', 'memory.high':'1207959552', 'memory.swap.max':'0',
                        'cpu.max':'50000 100000',
                        'memory.stat':'anon 0\nfile 0\nkernel 0\nshmem 0\n', 'memory.swap.current':'0',
                        'cgroup.procs':'999999'}.items():
        (group / name).write_text(value)
    kernel = LinuxResources(group, proc=proc, cgroup_root=cgroups)
    assert kernel.observe().constrained
    (group / 'memory.swap.max').write_text('max')
    assert not kernel.observe().constrained
    (group / 'memory.swap.max').write_text('0')
    (proc / 'self/cgroup').write_text('0::/edison/browser\n')
    assert not kernel.observe().constrained


def test_kernel_contract_rejects_a_cpu_cap_above_half_a_core(tmp_path):
    kernel = kernel_fixture(tmp_path)
    assert kernel.observe().constrained
    (kernel.group / 'cpu.max').write_text('max 100000')
    assert not kernel.observe().constrained
    (kernel.group / 'cpu.max').write_text('100000 100000')
    assert not kernel.observe().constrained
    (kernel.group / 'cpu.max').unlink()
    assert not kernel.observe().constrained


def test_session_defers_explicit_open_and_recovers_until_user_closes(tmp_path):
    from youtube_strataread.workbench.content_session import ContentSession
    from test_content_session import Browser
    observed = [ResourceObservation(available=1024**3, browser_unreclaimable=0, browser_swap=0, constrained=True)]
    browser = Browser()
    session = ContentSession(tmp_path/'content', browser=browser, resources=ResourceGovernor(observe=lambda: observed[0]))
    with pytest.raises(ContentSessionError, match='内存'):
        session.open()
    assert not browser.running
    assert session.status()['state'] == 'waiting_resources'
    observed[0] = ResourceObservation(available=2*1024**3, browser_unreclaimable=0, browser_swap=0, constrained=True)
    session.tick()
    assert browser.running
    observed[0] = ResourceObservation(available=300*1024**2, browser_unreclaimable=0, browser_swap=0, constrained=True)
    session.tick()
    assert not browser.running
    assert session.status()['state'] == 'waiting_resources'
    session.close_browser()
    observed[0] = ResourceObservation(available=2*1024**3, browser_unreclaimable=0, browser_swap=0, constrained=True)
    session.tick()
    assert not browser.running
    session.shutdown()


def test_native_start_fails_closed_without_kernel_budget(tmp_path, monkeypatch):
    from youtube_strataread.workbench.session_browser import ServerBrowser
    monkeypatch.setenv('EDISON_CONTENT_BROWSER_ENABLED', '1')
    monkeypatch.delenv('EDISON_CONTENT_CGROUP_DIR', raising=False)
    browser = ServerBrowser()
    with pytest.raises(ContentSessionError) as error:
        browser.start(tmp_path/'profile')
    assert error.value.kind == 'resource'
    assert not (tmp_path/'profile').exists()
    assert not browser.alive


def kernel_fixture(tmp_path, *, limit='1342177280', high='1207959552', stat='anon 0\nfile 0\nkernel 0\nshmem 0\n',
                   swap='0', available_kb=2000000):
    from youtube_strataread.workbench.resource_governor import LinuxResources
    proc=tmp_path/'proc'; (proc/'self').mkdir(parents=True)
    (proc/'meminfo').write_text(f'MemAvailable: {available_kb} kB\n')
    (proc/'self/cgroup').write_text('0::/backend\n')
    root=tmp_path/'cgroup'; group=root/'browser'; group.mkdir(parents=True)
    files = {'memory.max':limit,'memory.high':high,'memory.swap.max':'0','cpu.max':'50000 100000','memory.stat':stat,'memory.swap.current':swap}
    for name, value in files.items():
        if value is not None:
            (group/name).write_text(value)
    return LinuxResources(group,proc=proc,cgroup_root=root)


def test_reclaimable_file_cache_does_not_release_but_unreclaimable_usage_does(tmp_path):
    # Logged-in YouTube replay: ~906 MiB anon+shmem+kernel with ~290 MiB file cache.
    kernel = kernel_fixture(tmp_path, stat='anon 734003200\nfile 304087040\nkernel 104857600\nshmem 83886080\n')
    observed = kernel.observe()
    assert observed.constrained and observed.browser_unreclaimable == 922746880
    governor = ResourceGovernor(observe=kernel.observe)
    assert not governor.status()['release_browser']
    governor.require_browser_start()
    (kernel.group/'memory.stat').write_text('anon 851443712\nfile 304087040\nkernel 104857600\nshmem 117440512\n')
    state = governor.status()
    assert state['release_browser'] and state['browser_unreclaimable_bytes'] == 1024*1024**2


def test_any_browser_swap_releases(tmp_path):
    kernel = kernel_fixture(tmp_path, stat='anon 104857600\nfile 0\nkernel 0\nshmem 0\n', swap='4096')
    assert ResourceGovernor(observe=kernel.observe).status()['release_browser']


@pytest.mark.parametrize('stat', [None, 'anon 0\nfile 0\nshmem 0\n', 'anon x\nfile 0\nkernel 0\nshmem 0\n'])
def test_unreadable_memory_stat_is_unconstrained_not_old_current_criterion(tmp_path, stat):
    kernel = kernel_fixture(tmp_path, stat=stat)
    (kernel.group/'memory.current').write_text('0')
    assert not kernel.observe().constrained
    state = ResourceGovernor(observe=kernel.observe).status()
    assert state['release_browser'] and state['browser_waiting'] and '隔离不可用' in state['reason']


@pytest.mark.parametrize('limit,high', [('805306368','603979776'),('1073741824','1207959552'),
                                        ('1342177280','max'),('1342177280','1275068416'),('max','1207959552')])
def test_smaller_or_unbounded_kernel_budget_cannot_reuse_fixed_release_headroom(tmp_path, limit, high):
    assert not kernel_fixture(tmp_path, limit=limit, high=high).observe().constrained


def test_native_command_preserves_resource_exit_before_next_status_poll():
    import subprocess
    import sys
    from youtube_strataread.workbench.session_browser import ServerBrowser
    browser = ServerBrowser()
    browser.process = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(75)'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, start_new_session=True)
    browser.process.wait(timeout=5)
    try:
        with pytest.raises(ContentSessionError) as error:
            browser.command('screen', {})
        assert error.value.kind == 'resource'
    finally:
        browser.close()


@pytest.mark.parametrize("failure,expected_state", [
    (ContentSessionError("maintenance", "浏览器安装不可用。"), "needs_maintenance"),
    (ContentSessionError("user", "需要用户操作。"), "needs_user"),
    (OSError("storage unavailable"), "needs_maintenance"),
])
def test_nonresource_start_failure_does_not_replay_human_open_on_heartbeat(tmp_path, failure, expected_state):
    from youtube_strataread.workbench.content_session import ContentSession
    class UnavailableBrowser:
        starts = 0
        def start(self, profile):
            self.starts += 1
            raise failure
        def close(self):
            pass
    browser = UnavailableBrowser()
    session = ContentSession(tmp_path/'content', browser=browser, resources=ResourceGovernor(
        observe=lambda: ResourceObservation(2*1024**3, 0, 0, True)))
    try:
        with pytest.raises(ContentSessionError):
            session.open()
        session.tick(); session.tick()
        assert not session.status()['browser_pending']
        assert browser.starts == 1
        assert session.status()['state'] == expected_state
    finally:
        session.shutdown()


def test_measured_logged_in_youtube_peak_stays_open_below_unreclaimable_release():
    # Logged-in isolated replay peaked at 906 MiB anon+shmem+kernel, zero swap.
    observed = [ResourceObservation(available=2*1024**3, browser_unreclaimable=906*1024**2, browser_swap=0, constrained=True)]
    governor = ResourceGovernor(observe=lambda: observed[0])
    assert not governor.status()['release_browser']
    governor.require_browser_start()
    observed[0] = ResourceObservation(2*1024**3, 1023*1024**2, 0, True)
    assert not governor.status()['release_browser']
    observed[0] = ResourceObservation(2*1024**3, 1024*1024**2, 0, True)
    assert governor.status()['release_browser']


@pytest.mark.parametrize("stubborn_descendant", [False, True])
def test_stalled_partial_screen_releases_on_pressure_and_keeps_observation(tmp_path, monkeypatch, stubborn_descendant):
    import subprocess
    import sys
    import time
    from youtube_strataread.workbench.resource_governor import LinuxResources
    from youtube_strataread.workbench.session_browser import ServerBrowser
    marker = tmp_path / 'awaiting-screen'
    ticks = tmp_path / 'child-ticks'
    child = "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); p=pathlib.Path(" + repr(str(ticks)) + ");\nwhile True:\n p.write_text(str(time.monotonic())); time.sleep(.02)"
    worker = tmp_path / 'partial-screen.py'
    worker.write_text("""import pathlib,subprocess,sys,time
sys.stdin.readline()
if sys.argv[2]:
    subprocess.Popen([sys.executable,'-c',sys.argv[2]])
    while not pathlib.Path(sys.argv[3]).exists(): time.sleep(.01)
pathlib.Path(sys.argv[1]).touch()
sys.stdout.write('{"ok":');sys.stdout.flush()
time.sleep(2)
print('true,"result":{}}',flush=True)
""")
    monkeypatch.setattr(LinuxResources, 'observe', lambda self, **_: ResourceObservation(
        2*1024**3, 1153433600 if marker.exists() else 0, 0, True))
    browser = ServerBrowser(command_timeout=.5)
    browser.process = subprocess.Popen([sys.executable, str(worker), str(marker), child if stubborn_descendant else '', str(ticks)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, start_new_session=True)
    process = browser.process
    started = time.monotonic()
    try:
        with pytest.raises(ContentSessionError) as error:
            browser.command('screen', {})
        assert error.value.kind == 'resource', str(error.value)
        assert browser.last_resource_release['browser_unreclaimable_bytes'] == 1153433600
        assert not browser.alive
        assert process.poll() is not None
        assert time.monotonic() - started < (4.5 if stubborn_descendant else 1.5)
        if stubborn_descendant:
            stopped = ticks.read_text()
            time.sleep(.1)
            assert ticks.read_text() == stopped
    finally:
        browser.close()


def test_heartbeat_pressure_releases_idle_native_without_screenshot(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time
    from youtube_strataread.workbench import session_browser
    from youtube_strataread.workbench.content_session import ContentSession
    from youtube_strataread.workbench.resource_governor import LinuxResources
    observed = [ResourceObservation(2*1024**3, 0, 0, True)]
    monkeypatch.setattr(LinuxResources, 'observe', lambda self, **_: observed[0])
    monkeypatch.setenv('EDISON_CONTENT_BROWSER_ENABLED', '1')
    worker = tmp_path / 'idle-browser.py'
    worker.write_text("""import json,time
print(json.dumps({'ok':True,'result':{}}),flush=True)
time.sleep(2)
""")
    original = subprocess.Popen
    def external_driver(argv, **kwargs):
        return original([sys.executable, str(worker)], **kwargs)
    monkeypatch.setattr(session_browser.subprocess, 'Popen', external_driver)
    browser = session_browser.ServerBrowser()
    session = ContentSession(tmp_path/'content', browser=browser, resources=ResourceGovernor(observe=lambda:observed[0]))
    try:
        session.open()
        observed[0] = ResourceObservation(2*1024**3, 1153433600, 0, True)
        started = time.monotonic()
        session.tick()
        assert time.monotonic()-started < 1
        assert not session.status()['browser_running']
        assert session.status()['state'] == 'waiting_resources'
        assert browser.last_resource_release['browser_unreclaimable_bytes'] == 1153433600
    finally:
        session.shutdown()


def test_pressure_close_cleans_only_its_parent_owned_display_runtime(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    from pathlib import Path
    from youtube_strataread.workbench import session_browser
    from youtube_strataread.workbench.resource_governor import LinuxResources
    observed = [ResourceObservation(2*1024**3, 0, 0, True)]
    monkeypatch.setattr(LinuxResources, 'observe', lambda self, **_: observed[0])
    monkeypatch.setenv('EDISON_CONTENT_BROWSER_ENABLED', '1')
    owned = tmp_path/'owned-path.json'
    unrelated = tmp_path/'unrelated-display'; unrelated.mkdir()
    worker = tmp_path/'runtime-driver.py'
    worker.write_text("""import json,pathlib,sys,time
runtime=pathlib.Path(sys.argv[-1])
assert runtime.is_absolute() and runtime.is_dir()
(runtime/'Xauthority').write_text('fixture-private-display')
pathlib.Path(sys.argv[1]).write_text(json.dumps(str(runtime)))
print(json.dumps({'ok':True,'result':{}}),flush=True)
time.sleep(2)
""")
    original = subprocess.Popen
    monkeypatch.setattr(session_browser.subprocess, 'Popen', lambda argv, **kwargs:
        original([sys.executable,str(worker),str(owned),*argv[3:]], **kwargs))
    browser = session_browser.ServerBrowser()
    try:
        browser.start(tmp_path/'profile')
        runtime = Path(json.loads(owned.read_text()))
        assert (runtime/'Xauthority').exists()
        observed[0] = ResourceObservation(2*1024**3, 1153433600, 0, True)
        with pytest.raises(ContentSessionError) as error:
            browser.command('screen', {})
        assert error.value.kind == 'resource'
        assert not runtime.exists()
        assert unrelated.exists()
    finally:
        browser.close()
