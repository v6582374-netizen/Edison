"""Official read-only Data API transport, shared failure gate and local request budget.

The local ledger is an accounting limit, never Google's remaining project quota.
Caption/network throttling has a separate policy and cannot block this client.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from youtube_strataread.workbench.workspace import LocalWorkspace

POLL_SECONDS = 5 * 60
SUBSCRIPTION_SYNC_SECONDS = 6 * 60 * 60
MAX_SCAN_CHANNELS = 100
MAX_PAGE_ITEMS = 50
_GATE = 'youtube_data_api_gate'
_PACIFIC = ZoneInfo('America/Los_Angeles')
_MESSAGES = {
    'permission': 'Google 授权缺少所需权限，或此账号尚未启用 YouTube；请检查权限。',
    'configuration': 'Google OAuth 项目或 YouTube API 配置不可用，请检查服务配置。',
    'network': '无法连接 YouTube 官方 API，稍后重试。',
    'authorization': 'YouTube 授权失效，请重新连接。',
    'quota': 'YouTube 项目额度已用尽，等待配额重置。实际余额由 Google 管理。',
    'budget': '本机 YouTube API 日预算已用尽，等待下一预算日。',
    'service': 'YouTube 官方 API 暂不可用，稍后重试。',
    'channel': '此频道的上传列表暂不可访问，其他频道继续检查。',
}


class ConnectionError(RuntimeError):
    """Safe public message, without remote response bodies or credential values."""


class AuthorizationRequired(ConnectionError):
    """An access token or account grant was rejected."""


class YouTubeAPIError(ConnectionError):
    def __init__(self, kind: str, retry_at: float | None = None):
        super().__init__(_MESSAGES[kind])
        self.kind, self.retry_at = kind, retry_at

    @property
    def global_failure(self) -> bool:
        return self.kind != 'channel'


class YouTubeDataAPI:
    def __init__(self, workspace: LocalWorkspace | None = None) -> None:
        self.workspace = workspace
        self._lock = threading.RLock()
        self.daily_budget = max(1, int(os.environ.get('EDISON_YOUTUBE_API_DAILY_BUDGET', '10000')))
        self._gate: dict[str, Any] = {}
        if workspace:
            with sqlite3.connect(workspace.database_path) as db:
                db.execute('''CREATE TABLE IF NOT EXISTS youtube_api_usage (
                    day TEXT NOT NULL, endpoint TEXT NOT NULL, requests INTEGER NOT NULL,
                    PRIMARY KEY(day, endpoint))''')

    @staticmethod
    def _day() -> tuple[str, float]:
        now = datetime.fromtimestamp(time.time(), _PACIFIC)
        tomorrow = (now + timedelta(days=1)).date()
        return now.date().isoformat(), datetime.combine(tomorrow, datetime.min.time(), _PACIFIC).timestamp()

    def gate(self) -> dict[str, Any]:
        value = self.workspace.meta(_GATE) if self.workspace else None
        return json.loads(value) if value else self._gate.copy()

    def block(self, kind: str, retry_at: float | None = None) -> None:
        with self._lock:
            self._gate = {'kind': kind, 'retry_at': retry_at, 'message': _MESSAGES[kind]}
            if self.workspace:
                self.workspace.set_meta(_GATE, json.dumps(self._gate))

    def authorized(self) -> None:
        with self._lock:
            if self.gate().get('kind') == 'authorization':
                self._gate = {}
                if self.workspace:
                    self.workspace.set_meta(_GATE, '{}')

    def ensure_available(self, *, grant_check: bool = False) -> None:
        gate = self.gate()
        if grant_check and gate.get("kind") == "authorization":
            return
        if gate and (gate.get('retry_at') is None or gate['retry_at'] > time.time()):
            if gate['kind'] == 'authorization':
                raise AuthorizationRequired(_MESSAGES['authorization'])
            raise YouTubeAPIError(gate['kind'], gate.get('retry_at'))

    def _reserve(self, endpoint: str, *, grant_check: bool = False) -> None:
        self.ensure_available(grant_check=grant_check)
        if not self.workspace:
            return
        day, reset_at = self._day()
        with sqlite3.connect(self.workspace.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            used = db.execute('SELECT COALESCE(SUM(requests),0) FROM youtube_api_usage WHERE day=?', (day,)).fetchone()[0]
            if used >= self.daily_budget:
                # Do not acquire a second SQLite writer while this transaction is open.
                db.execute('INSERT INTO workspace_meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (_GATE, json.dumps({'kind':'budget','retry_at':reset_at,'message':_MESSAGES['budget']})))
            else:
                db.execute('INSERT INTO youtube_api_usage VALUES (?,?,1) ON CONFLICT(day,endpoint) DO UPDATE SET requests=requests+1', (day,endpoint))
        if used >= self.daily_budget:
            raise YouTubeAPIError('budget', reset_at)

    def get(self, endpoint: str, parameters: dict[str, str], access_token: str, *, grant_check: bool = False) -> dict[str, Any]:
        if endpoint not in {'subscriptions', 'channels', 'playlistItems', 'videos'}:
            raise ValueError('Unsupported YouTube Data API resource')
        with self._lock:
            self._reserve(endpoint, grant_check=grant_check)
            request = Request('https://www.googleapis.com/youtube/v3/' + endpoint + '?' + urlencode(parameters),
                              headers={'Authorization': 'Bearer ' + access_token})
            try:
                with urlopen(request, timeout=30) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
                    raise ValueError('Invalid Data API response')
            except HTTPError as error:
                try:
                    body = json.load(error)
                    reasons = {str(item.get('reason')) for item in body.get('error', {}).get('errors', []) if isinstance(item, dict)}
                except (ValueError, TypeError, AttributeError, OSError):
                    reasons = set()
                if error.code == 401 or reasons & {'authError'}:
                    raise AuthorizationRequired(_MESSAGES['authorization']) from None
                if reasons & {'insufficientPermissions', 'youtubeSignupRequired'}:
                    raise YouTubeAPIError('permission') from None
                if reasons & {'accessNotConfigured'}:
                    raise YouTubeAPIError('configuration') from None
                if reasons & {'quotaExceeded', 'dailyLimitExceeded'}:
                    kind, retry_at = 'quota', self._day()[1]
                elif error.code == 429 or error.code >= 500 or reasons & {'rateLimitExceeded', 'userRateLimitExceeded'}:
                    kind, retry_at = 'service', time.time() + 60
                    value = error.headers.get('Retry-After', '')
                    try:
                        deadline = time.time() + float(value)
                    except (ValueError, TypeError):
                        try:
                            deadline = parsedate_to_datetime(value).timestamp()
                        except (ValueError, TypeError, OverflowError):
                            deadline = retry_at
                    if math.isfinite(deadline):
                        retry_at = max(retry_at, deadline)
                else:
                    raise YouTubeAPIError('channel') from None
                self.block(kind, retry_at)
                raise YouTubeAPIError(kind, retry_at) from None
            except (OSError, ValueError) as error:
                kind = 'network' if isinstance(error, OSError) else 'service'
                retry_at = time.time() + 60
                self.block(kind, retry_at)
                raise YouTubeAPIError(kind, retry_at) from None
            # A successful post-deadline request ends a temporary global failure.
            self._gate = {}
            if self.workspace:
                self.workspace.set_meta(_GATE, '{}')
            return payload

    def snapshot(self, *, channels: int, subscriptions: int) -> dict[str, Any]:
        day, reset_at = self._day()
        counts: dict[str, int] = {}
        if self.workspace:
            with sqlite3.connect(self.workspace.database_path) as db:
                counts = dict(db.execute('SELECT endpoint,requests FROM youtube_api_usage WHERE day=?', (day,)))
        polls = 86400 // POLL_SECONDS
        membership = max(1, math.ceil(subscriptions / MAX_PAGE_ITEMS)) * (86400 // SUBSCRIPTION_SYNC_SECONDS)
        channel_details = channels
        minimum = polls * channels + membership + channel_details
        return {
            'day': day, 'reset_at': reset_at, 'used_units': sum(counts.values()),
            'requests_by_endpoint': counts, 'local_daily_budget': self.daily_budget,
            'project_remaining_units': None, 'default_project_quota': 10000,
            'minimum_daily_units': minimum,
            'full_page_new_videos_daily_units': minimum + polls * channels,
            'over_capacity': minimum > self.daily_budget or channels > MAX_SCAN_CHANNELS,
            'assumptions': {'poll_seconds':POLL_SECONDS, 'subscription_sync_seconds':SUBSCRIPTION_SYNC_SECONDS,
                            'max_channels_per_scan':MAX_SCAN_CHANNELS, 'pages_per_channel':1, 'items_per_page':MAX_PAGE_ITEMS,
                            'units_per_request':1, 'upload_cache_seconds':86400, 'other_project_usage':'unknown'},
            'gate': self.gate(),
        }
