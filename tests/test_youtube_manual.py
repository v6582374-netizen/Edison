"""Manual submission through Web, real host loops and durable library."""
import json
import time
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app
from youtube_strataread.workbench.connection import GoogleOAuthGateway, OAuthCredentials


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv('COWORKER_API_TOKEN', 'manual-web')
    monkeypatch.setenv('YOUTUBE_WORKBENCH_WORKSPACE', str(tmp_path / 'library'))
    monkeypatch.setenv('EDISON_CREDENTIAL_FILE', str(tmp_path / 'credentials.json'))
    calls = []
    def google(request, **kwargs):
        calls.append(request.full_url)
        video_id = parse_qs(urlsplit(request.full_url).query)['id'][0]
        return BytesIO(json.dumps({'items':[{'id':video_id,'snippet':{'title':'Old video','channelId':'outsider','channelTitle':'Outside subscriptions','publishedAt':'2020-01-01T00:00:00Z','liveBroadcastContent':'none'},'status':{'privacyStatus':'public','uploadStatus':'processed'}}]}).encode())
    monkeypatch.setattr('youtube_strataread.workbench.youtube_api.urlopen', google)
    monkeypatch.setattr(GoogleOAuthGateway, 'refresh', lambda *a, **kw: OAuthCredentials('access', None, time.time()+3600))
    monkeypatch.setattr(GoogleOAuthGateway, 'list_subscriptions', lambda *a, **kw: [])
    app = create_app(SessionManager(data_dir=tmp_path / 'host'))
    with TestClient(app, headers={'X-OpenWorker-Token':'manual-web'}) as client:
        def call(capability, **arguments):
            return client.post('/v1/youtube/capability', json={'capability':capability,'arguments':arguments}).json()
        assert call('connection.import_authorization', client_id='client', client_secret='secret', refresh_token='refresh')['ok']
        yield call, calls
    app.state.youtube.close()


def test_manual_old_unsubscribed_video_is_accepted_once_without_enabling_auto_update(host):
    call, external = host
    first = call('manual.submit', url='https://youtu.be/abcdefghijk?t=90', request_id='request-one')
    assert first['ok'], first
    assert first['result']['asset']['request_kind'] == 'manual'
    assert first['result']['asset']['preparation_state'] == 'queued'
    assert call('activity.snapshot')['result']['drain_paused']
    duplicate = call('manual.submit', url='https://www.youtube.com/watch?v=abcdefghijk&list=PLtest', request_id='request-two')
    assert duplicate['result']['asset']['video_id'] == 'abcdefghijk'
    assert len(external) == 1
    assert call('manual.confirm', request_id='request-one')['result']['accepted']


@pytest.mark.parametrize('url', ['https://youtube.com/@channel', 'https://youtube.com/playlist?list=PLx', 'https://youtube.com/watch?v=bad', 'https://youtube.com.evil.invalid/watch?v=abcdefghijk', 'https://youtu.be/abcdefghijk https://youtu.be/lmnopqrstuv'])
def test_invalid_links_never_create_a_receipt_or_call_google(host, url):
    call, external = host
    result = call('manual.submit', url=url, request_id='invalid-one')
    assert not result['ok']
    assert not external
    assert not call('manual.confirm', request_id='invalid-one')['result']['accepted']


def test_pasting_cancelled_video_does_not_restore_it_and_explicit_restore_keeps_manual_permission(host):
    call, external = host
    assert call('manual.submit', url='https://youtu.be/abcdefghijk', request_id='request-first')['ok']
    assert call('activity.cancel', video_ids=['abcdefghijk'])['result']['changed'] == ['abcdefghijk']
    again = call('manual.submit', url='https://youtube.com/watch?v=abcdefghijk', request_id='request-again')['result']
    assert again['disposition'] == 'action'
    assert again['asset']['preparation_state'] == 'cancelled'
    restored = call('manual.restore', video_id='abcdefghijk', request_id='restore-one')['result']
    assert restored['asset']['request_kind'] == 'manual'
    assert restored['asset']['preparation_state'] == 'queued'
    assert call('activity.snapshot')['result']['drain_paused']
    assert len(external) == 1
