"""External HTTP boundary checks for persistent quota and service recovery."""
import json
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

from youtube_strataread.workbench import youtube_api
from youtube_strataread.workbench.workspace import LocalWorkspace
from youtube_strataread.workbench.youtube_api import YouTubeAPIError, YouTubeDataAPI


def test_budget_survives_restart_and_resets_at_pacific_midnight(tmp_path, monkeypatch):
    clock = [datetime(2026, 9, 21, 23, 59, tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()]
    monkeypatch.setattr(youtube_api, 'time', SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setenv('EDISON_YOUTUBE_API_DAILY_BUDGET', '2')
    requests = []

    def get(request, timeout):
        requests.append(request.full_url)
        return BytesIO(b'{"items":[]}')

    monkeypatch.setattr(youtube_api, 'urlopen', get)
    api = YouTubeDataAPI(LocalWorkspace.open(tmp_path))
    api.get('subscriptions', {}, 'secret')
    api.get('channels', {}, 'secret')
    api = YouTubeDataAPI(LocalWorkspace.open(tmp_path))
    with pytest.raises(YouTubeAPIError) as failure:
        api.get('playlistItems', {}, 'secret')
    assert failure.value.kind == 'budget'
    assert len(requests) == 2
    snapshot = api.snapshot(channels=35, subscriptions=35)
    assert snapshot['used_units'] == 2
    assert snapshot['over_capacity']
    assert snapshot['project_remaining_units'] is None
    clock[0] += 61
    api.get('playlistItems', {}, 'secret')
    assert api.snapshot(channels=1, subscriptions=1)['used_units'] == 1
    assert api.gate() == {}


@pytest.mark.parametrize('payload', [None, {'items': None}])
def test_invalid_response_is_visible_global_failure_not_empty_scan(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(youtube_api, 'urlopen', lambda *a, **kw: BytesIO(json.dumps(payload).encode()))
    api = YouTubeDataAPI(LocalWorkspace.open(tmp_path))
    with pytest.raises(YouTubeAPIError) as failure:
        api.get('playlistItems', {}, 'secret')
    assert failure.value.kind == 'service'
    restored = YouTubeDataAPI(LocalWorkspace.open(tmp_path))
    with pytest.raises(YouTubeAPIError):
        restored.get('channels', {}, 'secret')
    assert restored.snapshot(channels=2, subscriptions=2)['used_units'] == 1


def test_quota_failure_persists_and_accounts_for_failed_attempt(tmp_path, monkeypatch):
    def fail(request, timeout):
        raise HTTPError(request.full_url, 403, 'quota', {}, BytesIO(b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}'))
    monkeypatch.setattr(youtube_api, 'urlopen', fail)
    api = YouTubeDataAPI(LocalWorkspace.open(tmp_path))
    with pytest.raises(YouTubeAPIError):
        api.get('videos', {}, 'secret')
    restored = YouTubeDataAPI(LocalWorkspace.open(tmp_path))
    with pytest.raises(YouTubeAPIError):
        restored.get('subscriptions', {}, 'secret')
    assert restored.snapshot(channels=1, subscriptions=1)['requests_by_endpoint'] == {'videos': 1}


def test_service_gate_honors_http_date_retry_after(tmp_path, monkeypatch):
    from email.utils import formatdate
    clock = 1790000000.0
    monkeypatch.setattr(youtube_api, 'time', SimpleNamespace(time=lambda: clock))
    def fail(request, timeout):
        raise HTTPError(request.full_url, 503, 'service', {'Retry-After': formatdate(clock + 3600, usegmt=True)}, BytesIO(b'{}'))
    monkeypatch.setattr(youtube_api, 'urlopen', fail)
    api = YouTubeDataAPI(LocalWorkspace.open(tmp_path))
    with pytest.raises(YouTubeAPIError):
        api.get('videos', {}, 'secret')
    assert api.gate()['retry_at'] == clock + 3600


@pytest.mark.parametrize(('reason', 'kind'), [('insufficientPermissions', 'permission'), ('accessNotConfigured', 'configuration')])
def test_official_permission_and_configuration_are_not_account_challenges(tmp_path, monkeypatch, reason, kind):
    def fail(request, timeout):
        raise HTTPError(request.full_url, 403, 'denied', {}, BytesIO(json.dumps({'error': {'errors': [{'reason': reason}]}}).encode()))
    monkeypatch.setattr(youtube_api, 'urlopen', fail)
    with pytest.raises(YouTubeAPIError) as error:
        YouTubeDataAPI(LocalWorkspace.open(tmp_path)).get('subscriptions', {}, 'secret')
    assert error.value.kind == kind
