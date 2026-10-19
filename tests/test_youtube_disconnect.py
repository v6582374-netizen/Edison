"""Managed Web withdrawal through the real host and temporary persistent library."""
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app
from youtube_strataread.workbench.connection import GoogleOAuthGateway, OAuthCredentials


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv('YOUTUBE_WORKBENCH_WORKSPACE', str(tmp_path / 'library'))
    monkeypatch.setenv('EDISON_CREDENTIAL_FILE', str(tmp_path / 'credentials.json'))
    monkeypatch.setenv('EDISON_CONTENT_SESSION_DIR', str(tmp_path / 'content'))
    monkeypatch.setenv('COWORKER_API_TOKEN', 'disconnect-test')
    app = create_app(SessionManager(data_dir=tmp_path / 'host'))
    with TestClient(app, headers={'X-OpenWorker-Token': 'disconnect-test'}) as client:
        def call(name, **arguments):
            return client.post('/v1/youtube/capability', json={'capability': name, 'arguments': arguments}).json()
        yield call
    app.state.youtube.close()


@pytest.mark.parametrize('capability', ['session_cookies.replace', 'session_cookies.test', 'connection.migrate_credentials', 'connection.configure', 'connection.authorize'])
def test_managed_web_rejects_retired_credential_ingress(web, capability):
    response = web(capability, text='not-a-cookie', client_id='old-client', client_secret='old-secret')
    assert response == {'ok': False, 'error': '此连接入口已退役，请刷新页面并使用新的 YouTube 连接入口。', 'kind': 'retired_entry'}


def test_disconnect_clears_both_connections_even_when_google_revocation_fails(web, monkeypatch, tmp_path):
    from youtube_strataread.workbench import session_browser
    from youtube_strataread.workbench.connection import ConnectionError
    class Browser:
        def __init__(self, **kwargs): pass
        def start(self, profile):
            profile.mkdir()
            (profile / 'secret').write_text('browser-secret')
        def close(self): pass
    monkeypatch.setattr(session_browser, 'ServerBrowser', Browser)
    monkeypatch.setattr(GoogleOAuthGateway, 'refresh', lambda *args: OAuthCredentials('access', 'refresh', time.time()+3600))
    monkeypatch.setattr(GoogleOAuthGateway, 'list_subscriptions', lambda *args, **kwargs: [])
    def revoke(self, token):
        assert token == 'refresh'
        raise ConnectionError('remote secret error')
    monkeypatch.setattr(GoogleOAuthGateway, 'revoke', revoke, raising=False)
    assert web('connection.import_authorization', client_id='client', client_secret='secret', refresh_token='refresh')['ok']
    assert web('content_session.open')['ok']
    result = web('connection.disconnect')
    assert result['ok'], result
    assert result['result']['disconnection'] == {'local': 'cleared', 'google': 'failed'}
    assert not web('connection.status')['result']['authorized']
    content = web('content_session.status')['result']
    assert content['state'] == 'disconnected'
    assert not content['browser_running'] and not content['snapshot_present']
    assert not (tmp_path / 'content' / 'profile').exists()
    assert 'refresh' not in (tmp_path / 'credentials.json').read_text()
    assert web('activity.snapshot')['result']['drain_paused']


def test_disconnect_invalidates_inflight_refresh_before_google_returns(web, monkeypatch):
    entered, release = Event(), Event()
    calls = []
    def refresh(*args):
        calls.append('refresh')
        if len(calls) > 1:
            entered.set()
            assert release.wait(5)
        return OAuthCredentials('late-access', 'late-refresh', time.time()-100)
    monkeypatch.setattr(GoogleOAuthGateway, 'refresh', refresh)
    monkeypatch.setattr(GoogleOAuthGateway, 'list_subscriptions', lambda *args, **kwargs: [])
    monkeypatch.setattr(GoogleOAuthGateway, 'revoke', lambda *args: None)
    assert web('connection.import_authorization', client_id='client', client_secret='secret', refresh_token='refresh')['ok']
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(web, 'connection.refresh_subscriptions')
        assert entered.wait(2)
        disconnected = pool.submit(web, 'connection.disconnect')
        try:
            assert not disconnected.result(timeout=2)['result']['authorized']
        finally:
            release.set()
        assert not old.result()['ok']
    assert not web('connection.status')['result']['authorized']
    assert web('connection.status')['result']['disconnection']['local'] == 'cleared'


def test_partial_local_clear_is_completed_on_restart_without_reconnecting(tmp_path, monkeypatch):
    from youtube_strataread.workbench import session_browser
    monkeypatch.setenv('YOUTUBE_WORKBENCH_WORKSPACE', str(tmp_path / 'library'))
    monkeypatch.setenv('EDISON_CREDENTIAL_FILE', str(tmp_path / 'credentials.json'))
    monkeypatch.setenv('EDISON_CONTENT_SESSION_DIR', str(tmp_path / 'content'))
    monkeypatch.setenv('COWORKER_API_TOKEN', 'disconnect-test')
    close_fails = Event()
    class Browser:
        def __init__(self, **kwargs): pass
        def start(self, profile):
            profile.mkdir()
            (profile / 'secret').write_text('browser-secret')
        def close(self):
            if close_fails.is_set():
                raise OSError('controlled browser close failure')
    monkeypatch.setattr(session_browser, 'ServerBrowser', Browser)
    monkeypatch.setattr(GoogleOAuthGateway, 'refresh', lambda *args: OAuthCredentials('access', 'refresh', time.time()+3600))
    monkeypatch.setattr(GoogleOAuthGateway, 'list_subscriptions', lambda *args, **kwargs: [])
    monkeypatch.setattr(GoogleOAuthGateway, 'revoke', lambda *args: None)
    for restart in range(2):
        app = create_app(SessionManager(data_dir=tmp_path / 'host'))
        with TestClient(app, headers={'X-OpenWorker-Token':'disconnect-test'}) as client:
            def call(name, **arguments):
                value=client.post('/v1/youtube/capability',json={'capability':name,'arguments':arguments}).json()
                assert value['ok'], value
                return value['result']
            if not restart:
                call('connection.import_authorization',client_id='client',client_secret='secret',refresh_token='refresh')
                call('content_session.open')
                close_fails.set()
                status=call('connection.disconnect')
                assert status['disconnection']=={'local':'failed','google':'revoked'}
                assert not status['authorized']
                refused = client.post('/v1/youtube/capability', json={'capability':'content_session.open'}).json()
                assert not refused['ok'] and '清理' in refused['error']
                close_fails.clear()
            else:
                status=call('connection.status')
                assert status['disconnection']=={'local':'cleared','google':'revoked'}
                assert not status['authorized']
                assert call('content_session.status')['state']=='disconnected'
                assert not (tmp_path / 'content' / 'profile').exists()
        app.state.youtube.close()


def test_repeated_disconnect_keeps_the_inflight_platform_revocation_result(web, monkeypatch):
    entered, release = Event(), Event()
    monkeypatch.setattr(GoogleOAuthGateway, 'refresh', lambda *args: OAuthCredentials('access', 'refresh', time.time()+3600))
    monkeypatch.setattr(GoogleOAuthGateway, 'list_subscriptions', lambda *args, **kwargs: [])
    def revoke(*args):
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(GoogleOAuthGateway, 'revoke', revoke)
    assert web('connection.import_authorization',client_id='client',client_secret='secret',refresh_token='refresh')['ok']
    with ThreadPoolExecutor() as pool:
        first = pool.submit(web, 'connection.disconnect')
        assert entered.wait(2)
        try:
            second = web('connection.disconnect')
            assert second['result']['disconnection']['local']=='cleared'
            refused = web('connection.import_authorization',client_id='client',client_secret='secret',refresh_token='refresh')
            assert not refused['ok']
        finally:
            release.set()
        assert first.result()['result']['disconnection']['google']=='revoked'
    assert web('connection.status')['result']['disconnection']['google']=='revoked'
