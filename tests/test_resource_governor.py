"""Public resource admission boundary; only kernel observations are controlled."""
import pytest

from youtube_strataread.workbench.content_session import ContentSessionError
from youtube_strataread.workbench.resource_governor import ResourceGovernor, ResourceObservation


def test_memory_pressure_waits_then_recovers_without_granting_permission():
    observed = [ResourceObservation(available=1024**3, browser_memory=0, browser_swap=0, constrained=True)]
    governor = ResourceGovernor(observe=lambda: observed[0])
    with pytest.raises(ContentSessionError, match="内存") as error:
        governor.require_browser_start()
    assert error.value.kind == "resource"
    assert governor.status()["browser_waiting"]
    observed[0] = ResourceObservation(available=2*1024**3, browser_memory=0, browser_swap=0, constrained=True)
    governor.require_browser_start()
    assert not governor.status()["browser_waiting"]


def test_live_pressure_releases_browser_without_confusing_startup_reserve():
    observed = [ResourceObservation(available=800*1024**2, browser_memory=400*1024**2, browser_swap=0, constrained=True)]
    governor = ResourceGovernor(observe=lambda: observed[0])
    assert not governor.status()["release_browser"]
    governor.require_captions()
    observed[0] = ResourceObservation(available=800*1024**2, browser_memory=620*1024**2, browser_swap=0, constrained=True)
    assert governor.status()["release_browser"]
    with pytest.raises(ContentSessionError, match="释放"):
        governor.require_captions()


def test_kernel_contract_rejects_unbounded_display_group_and_host_group(tmp_path):
    from youtube_strataread.workbench.resource_governor import LinuxResources
    proc = tmp_path / 'proc'; proc.mkdir()
    (proc / 'meminfo').write_text('MemAvailable: 1572864 kB\n')
    (proc / 'self').mkdir()
    (proc / 'self/cgroup').write_text('0::/edison/backend\n')
    cgroups = tmp_path / 'cgroup'
    group = cgroups / 'edison/browser'; group.mkdir(parents=True)
    for name, value in {'memory.max':'805306368', 'memory.high':'603979776', 'memory.swap.max':'0',
                        'memory.current':'100000000', 'memory.swap.current':'0', 'cgroup.procs':'999999'}.items():
        (group / name).write_text(value)
    kernel = LinuxResources(group, proc=proc, cgroup_root=cgroups)
    assert kernel.observe().constrained
    (group / 'memory.swap.max').write_text('max')
    assert not kernel.observe().constrained
    (group / 'memory.swap.max').write_text('0')
    (proc / 'self/cgroup').write_text('0::/edison/browser\n')
    assert not kernel.observe().constrained


def test_session_defers_explicit_open_and_recovers_until_user_closes(tmp_path):
    from youtube_strataread.workbench.content_session import ContentSession
    from test_content_session import Browser
    observed = [ResourceObservation(available=1024**3, browser_memory=0, browser_swap=0, constrained=True)]
    browser = Browser()
    session = ContentSession(tmp_path/'content', browser=browser, resources=ResourceGovernor(observe=lambda: observed[0]))
    with pytest.raises(ContentSessionError, match='内存'):
        session.open()
    assert not browser.running
    assert session.status()['state'] == 'waiting_resources'
    observed[0] = ResourceObservation(available=2*1024**3, browser_memory=0, browser_swap=0, constrained=True)
    session.tick()
    assert browser.running
    observed[0] = ResourceObservation(available=400*1024**2, browser_memory=620*1024**2, browser_swap=0, constrained=True)
    session.tick()
    assert not browser.running
    assert session.status()['state'] == 'waiting_resources'
    session.close_browser()
    observed[0] = ResourceObservation(available=2*1024**3, browser_memory=0, browser_swap=0, constrained=True)
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


@pytest.mark.parametrize('limit,high', [('536870912','402653184'),('805306368','max'),('1073741824','603979776')])
def test_smaller_or_unbounded_kernel_budget_cannot_reuse_fixed_release_headroom(tmp_path, limit, high):
    from youtube_strataread.workbench.resource_governor import LinuxResources
    proc=tmp_path/'proc'; (proc/'self').mkdir(parents=True)
    (proc/'meminfo').write_text('MemAvailable: 2000000 kB\n')
    (proc/'self/cgroup').write_text('0::/backend\n')
    root=tmp_path/'cgroup'; group=root/'browser'; group.mkdir(parents=True)
    for name, value in {'memory.max':limit,'memory.high':high,'memory.swap.max':'0','memory.current':'0','memory.swap.current':'0'}.items():
        (group/name).write_text(value)
    assert not LinuxResources(group,proc=proc,cgroup_root=root).observe().constrained
