"""Official authorization through the authenticated Web host and durable store."""
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app
from youtube_strataread.workbench.connection import (
    GoogleOAuthGateway,
    OAuthCredentials,
    SubscriptionSource,
)


def test_web_handoff_refresh_and_restart_preserve_permission(tmp_path, monkeypatch):
    monkeypatch.setenv('YOUTUBE_WORKBENCH_WORKSPACE', str(tmp_path / 'library'))
    monkeypatch.setenv('EDISON_CREDENTIAL_FILE', str(tmp_path / 'credentials.json'))
    monkeypatch.setenv('COWORKER_API_TOKEN', 'test-web')
    clock = [time.time()]
    monkeypatch.setattr("youtube_strataread.workbench.connection.time", SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic))
    refreshed = []
    def refresh(self, configuration, refresh_token):
        assert configuration.client_id == 'fixture-client'
        assert refresh_token == 'fixture-refresh'
        refreshed.append(refresh_token)
        return OAuthCredentials('fixture-access', None, clock[0] + 3600)
    monkeypatch.setattr(GoogleOAuthGateway, 'refresh', refresh)
    monkeypatch.setattr(GoogleOAuthGateway, 'list_subscriptions', lambda self, credentials, **kwargs: [SubscriptionSource('channel', 'Example')])
    for restart in range(2):
        manager = SessionManager(data_dir=tmp_path / 'host')
        app = create_app(manager)
        with TestClient(app, headers={'X-OpenWorker-Token': 'test-web'}) as client:
            def call(name, **arguments):
                result = client.post('/v1/youtube/capability', json={'capability': name, 'arguments': arguments}).json()
                assert result['ok'], result
                return result['result']
            if not restart:
                assert call('connection.status')['subscription_capability']['state'] == 'unverified'
                status = call('connection.import_authorization', client_id='fixture-client', client_secret='fixture-secret', refresh_token='fixture-refresh')
                assert status['subscription_capability']['state'] == 'available'
                clock[0] += 3601  # ordinary expiry; concurrent Web consumers share one refresh
            with ThreadPoolExecutor(max_workers=4) as pool:
                statuses = list(pool.map(lambda _: call('connection.refresh_subscriptions'), range(4)))
            assert all(s['subscription_capability']['state'] == 'available' for s in statuses)
            assert call('activity.snapshot')['drain_paused']
            assert len(refreshed) == restart + 2
            assert 'fixture-refresh' not in str(statuses)
        app.state.youtube.close()
    assert os.geteuid() != 0
    assert (tmp_path / 'credentials.json').stat().st_mode & 0o777 == 0o600



@pytest.mark.parametrize(('remote_error', 'kind'), [
    (HTTPError('https://oauth2.googleapis.com/token', 400, 'bad', {}, io.BytesIO(b'{"error":"invalid_grant"}')), 'reauthorization'),
    (HTTPError('https://oauth2.googleapis.com/token', 401, 'bad', {}, io.BytesIO(b'{"error":"invalid_client"}')), 'configuration'),
    (URLError('secret-bearing remote text'), 'network'),
])
def test_web_reports_safe_official_failure_without_claiming_security_challenge(tmp_path, monkeypatch, remote_error, kind):
    monkeypatch.setenv('YOUTUBE_WORKBENCH_WORKSPACE', str(tmp_path / 'library'))
    monkeypatch.setenv('EDISON_CREDENTIAL_FILE', str(tmp_path / 'credentials.json'))
    monkeypatch.setenv('COWORKER_API_TOKEN', 'test-web')
    def fail(*args, **kwargs):
        raise remote_error
    monkeypatch.setattr('youtube_strataread.workbench.connection.urlopen', fail)
    app = create_app(SessionManager(data_dir=tmp_path / 'host'))
    with TestClient(app, headers={'X-OpenWorker-Token': 'test-web'}) as client:
        response = client.post('/v1/youtube/capability', json={'capability': 'connection.import_authorization', 'arguments': {'client_id': 'fixture-client', 'client_secret': 'fixture-secret', 'refresh_token': 'fixture-refresh'}}).json()
        assert not response['ok']
        status = client.post('/v1/youtube/capability', json={'capability': 'connection.status'}).json()['result']
        assert status['subscription_capability']['failure_kind'] == kind
        assert status['subscription_capability']['state'] == 'interrupted'
        assert status['subscription_capability']['last_failure_at']
        assert 'fixture-secret' not in str(status) and 'secret-bearing' not in str(status)
    app.state.youtube.close()


def test_two_web_hosts_cannot_refresh_the_same_official_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('YOUTUBE_WORKBENCH_WORKSPACE', str(tmp_path / 'library'))
    monkeypatch.setenv('EDISON_CREDENTIAL_FILE', str(tmp_path / 'credentials.json'))
    monkeypatch.setenv('COWORKER_API_TOKEN', 'test-web')
    apps = [create_app(SessionManager(data_dir=tmp_path / f'host-{i}')) for i in range(2)]
    with TestClient(apps[0], headers={'X-OpenWorker-Token': 'test-web'}) as first, TestClient(apps[1], headers={'X-OpenWorker-Token': 'test-web'}) as second:
        assert first.post('/v1/youtube/capability', json={'capability': 'connection.status'}).json()['ok']
        rejected = second.post('/v1/youtube/capability', json={'capability': 'connection.status'}).json()
        assert not rejected['ok']
        assert 'owner' in rejected['error']
        apps[0].state.youtube.close()
        retired = first.post('/v1/youtube/capability', json={'capability': 'connection.refresh_subscriptions'}).json()
        assert not retired['ok']
        assert 'retired' in retired['error']
        assert second.post('/v1/youtube/capability', json={'capability': 'connection.status'}).json()['ok']
    apps[1].state.youtube.close()


def test_rejected_handoff_preserves_the_existing_verified_connection(tmp_path, monkeypatch):
    monkeypatch.setenv('YOUTUBE_WORKBENCH_WORKSPACE', str(tmp_path / 'library'))
    monkeypatch.setenv('EDISON_CREDENTIAL_FILE', str(tmp_path / 'credentials.json'))
    monkeypatch.setenv('COWORKER_API_TOKEN', 'test-web')
    from youtube_strataread.workbench.connection import OfficialConnectionError
    def refresh(self, configuration, refresh_token):
        if configuration.client_id == 'wrong-client':
            raise OfficialConnectionError('configuration', 'Google OAuth client configuration was rejected')
        return OAuthCredentials('valid-access', None, time.time() + 3600)
    monkeypatch.setattr(GoogleOAuthGateway, 'refresh', refresh)
    monkeypatch.setattr(GoogleOAuthGateway, 'list_subscriptions', lambda self, credentials, **kwargs: [])
    app = create_app(SessionManager(data_dir=tmp_path / 'host'))
    with TestClient(app, headers={'X-OpenWorker-Token': 'test-web'}) as client:
        def call(name, **arguments):
            return client.post('/v1/youtube/capability', json={'capability': name, 'arguments': arguments}).json()
        assert call('connection.import_authorization', client_id='client', client_secret='secret', refresh_token='refresh')['ok']
        assert not call('connection.import_authorization', client_id='wrong-client', client_secret='secret', refresh_token='refresh')['ok']
        status = call('connection.status')['result']
        assert status['authorized']
        assert status['subscription_capability']['state'] == 'available'
    app.state.youtube.close()


def test_repeated_handoff_respects_official_quota_gate(tmp_path, monkeypatch):
    monkeypatch.setenv('YOUTUBE_WORKBENCH_WORKSPACE', str(tmp_path / 'library'))
    monkeypatch.setenv('EDISON_CREDENTIAL_FILE', str(tmp_path / 'credentials.json'))
    monkeypatch.setenv('COWORKER_API_TOKEN', 'test-web')
    external = []
    def token(*args, **kwargs):
        external.append('token')
        return io.BytesIO(b'{"access_token":"fixture-access","expires_in":3600}')
    def quota(request, **kwargs):
        external.append('subscriptions')
        raise HTTPError(request.full_url, 403, 'quota', {}, io.BytesIO(b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}'))
    monkeypatch.setattr('youtube_strataread.workbench.connection.urlopen', token)
    monkeypatch.setattr('youtube_strataread.workbench.youtube_api.urlopen', quota)
    app = create_app(SessionManager(data_dir=tmp_path / 'host'))
    with TestClient(app, headers={'X-OpenWorker-Token': 'test-web'}) as client:
        for _ in range(2):
            response = client.post('/v1/youtube/capability', json={'capability': 'connection.import_authorization', 'arguments': {'client_id': 'client', 'client_secret': 'secret', 'refresh_token': 'refresh'}}).json()
            assert not response['ok']
        assert external == ['token', 'subscriptions']
    app.state.youtube.close()
