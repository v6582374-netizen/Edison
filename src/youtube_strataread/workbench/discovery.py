"""Bounded subscription discovery using only the official YouTube Data API."""
from __future__ import annotations

import html
import json
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from youtube_strataread.workbench.connection import ConnectionService, SubscriptionSource
from youtube_strataread.workbench.freshness import WINDOW_SECONDS, event_completion
from youtube_strataread.workbench.workspace import LocalWorkspace
from youtube_strataread.workbench.youtube_api import (
    MAX_PAGE_ITEMS,
    MAX_SCAN_CHANNELS,
    POLL_SECONDS,
    AuthorizationRequired,
    ConnectionError,
    YouTubeAPIError,
)

_MAX_REFRESH_CANDIDATES = 100


@dataclass(frozen=True)
class Candidate:
    video_id: str
    channel_id: str
    channel_title: str
    title: str
    url: str
    published_at: str
    published_ts: float | None
    timing_status: str = 'publication'
    playlist_added_at: str | None = None
    source_observed_at: float | None = None
    source_observed_from: float | None = None
    source_observed_until: float | None = None
    completion_status: str = 'unverified'
    actual_start_at: str | None = None
    actual_end_at: str | None = None
    scheduled_start_at: str | None = None
    timing_checked_at: float | None = None


@dataclass(frozen=True)
class SourcePage:
    candidates: list[Candidate]
    complete: bool = True
    reason: str = ''


@dataclass(frozen=True)
class DiscoveryResult:
    discovered: int
    scanned_sources: int
    truncated: bool
    status: str = 'healthy'
    error: str | None = None

    def as_result(self) -> dict[str, object]:
        return dict(discovered=self.discovered, scanned_sources=self.scanned_sources,
                    truncated=self.truncated, status=self.status, error=self.error)


class UploadsSource(Protocol):
    def fetch(self, source: SubscriptionSource) -> SourcePage: ...


class YouTubeUploadsAPI:
    def __init__(self, *, workspace: LocalWorkspace, connection: ConnectionService) -> None:
        self.workspace, self.connection = workspace, connection

    def fetch(self, source: SubscriptionSource) -> SourcePage:
        scan = self.workspace.source_scan(source.channel_id)
        uploads = scan.get('uploads_playlist_id')
        if not uploads or time.time() - (scan.get('uploads_checked_at') or 0) >= 86400:
            payload = self.connection.youtube_get('channels', {'part':'contentDetails', 'id':source.channel_id})
            channels = [item for item in payload['items'] if isinstance(item, dict) and item.get('id') == source.channel_id]
            uploads = _object(_object(channels[0].get('contentDetails')).get('relatedPlaylists')).get('uploads') if channels else None
            if not isinstance(uploads, str) or not uploads:
                raise YouTubeAPIError('channel')
            self.workspace.record_source_scan(source.channel_id, uploads_playlist_id=uploads, uploads_checked_at=time.time())
        observed_from = time.time()
        listing = self.connection.youtube_get('playlistItems', {
            'part':'snippet,contentDetails', 'playlistId':uploads, 'maxResults':str(MAX_PAGE_ITEMS),
        })
        observed_until = time.time()
        entries: dict[str, dict[str, Any]] = {}
        complete = not bool(listing.get('nextPageToken')) and len(listing['items']) <= MAX_PAGE_ITEMS
        for item in listing['items'][:MAX_PAGE_ITEMS]:
            if not isinstance(item, dict) or not isinstance(item.get('contentDetails'), dict):
                complete = False
                continue
            video_id = item['contentDetails'].get('videoId')
            if not isinstance(video_id, str) or not video_id:
                complete = False
                continue
            entries[video_id] = item
        observations = self.workspace.observe_uploads(list(entries), observed_from, observed_until)
        known: dict[str, dict[str, Any]] = {}
        pending = {asset['video_id']: asset for asset in
                   self.workspace.pending_video_timing(source.channel_id, MAX_PAGE_ITEMS)}
        for video_id in entries:
            with suppress(KeyError):
                known[video_id] = self.workspace.asset(video_id)
        needed = [video_id for video_id in entries if video_id not in known
                  or not known[video_id].get('source_observed_at')
                  or known[video_id]['preparation_state'] in ('awaiting_timing', 'awaiting_completion')]
        # One bounded videos.list request also revisits known events that have left
        # the latest uploads page. Oldest checks go first to rotate pending work.
        needed = sorted(set(needed) | pending.keys(),
                        key=lambda key: (pending.get(key, known.get(key, {})).get('timing_checked_at') or 0, key))
        selected = needed[:MAX_PAGE_ITEMS]
        if len(needed) > len(selected):
            complete = False
        details: dict[str, dict[str, Any]] = {}
        if selected:
            payload = self.connection.youtube_get('videos', {
                'part':'snippet,contentDetails,status,liveStreamingDetails', 'id':','.join(selected),
            })
            details = {item['id']:item for item in payload['items'] if isinstance(item,dict) and isinstance(item.get('id'),str)}
        candidates = []
        for video_id in dict.fromkeys([*entries, *selected]):
            previous = known.get(video_id, pending.get(video_id, {}))
            entry = entries.get(video_id, {})
            added = _object(entry.get('snippet')).get('publishedAt') or previous.get('playlist_added_at')
            completion_status = previous.get('completion_status', 'unverified')
            actual_start = previous.get('actual_start_at')
            actual_end = previous.get('actual_end_at')
            scheduled_start = previous.get('scheduled_start_at')
            checked_at = previous.get('timing_checked_at')
            if video_id not in selected:
                if not previous:
                    continue  # Details deferred by the bounded request, not invented.
                published_at = str(previous['published_at'])
                title = str(previous['title'])
                published_ts = previous.get('published_ts')
                timing_status = str(previous.get('timing_status') or 'unverified')
            else:
                video = details.get(video_id, {})
                snippet = _object(video.get('snippet'))
                status = _object(video.get('status'))
                live = _object(video.get('liveStreamingDetails'))
                broadcast = snippet.get('liveBroadcastContent')
                published_at = str(snippet.get('publishedAt') or previous.get('published_at') or '')
                published_ts = _published_timestamp(published_at)
                playlist_publication = _object(entry.get('contentDetails')).get('videoPublishedAt')
                if playlist_publication and _published_timestamp(str(playlist_publication)) != published_ts:
                    published_ts = None
                public = (snippet.get('channelId') == source.channel_id
                          and status.get('privacyStatus') == 'public')
                is_event = (broadcast in ('live', 'upcoming') or 'liveStreamingDetails' in video
                            or previous.get('timing_status') == 'event')
                ordinary = (public and broadcast == 'none' and not is_event
                            and status.get('uploadStatus') == 'processed')
                timing_status = 'event' if is_event else ('publication' if ordinary else 'unverified')
                if is_event:
                    completion_status = event_completion(broadcast, live, time.time()) if public else 'unverified'
                    if completion_status == 'ended' and status.get('uploadStatus') != 'processed':
                        completion_status = 'unverified'
                    # Retain the reported facts even when the latest response is
                    # insufficient. Only completion_status grants their use.
                    actual_start = live.get('actualStartTime', actual_start)
                    if not isinstance(actual_start, str):
                        actual_start = None
                    actual_end = live.get('actualEndTime', actual_end)
                    if not isinstance(actual_end, str):
                        actual_end = None
                    scheduled_start = live.get('scheduledStartTime', scheduled_start)
                    if not isinstance(scheduled_start, str):
                        scheduled_start = None
                checked_at = time.time()
                # A listed waiting event was discovered successfully. Detail
                # availability and list coverage are separate from eligibility.
                if not video or not public or (not is_event and (not ordinary or published_ts is None)):
                    complete = False
                title = str(snippet.get('title') or previous.get('title') or _object(entry.get('snippet')).get('title') or video_id)
            observed = observations.get(video_id, {})
            candidates.append(Candidate(
                video_id, source.channel_id, source.title, html.unescape(title),
                'https://www.youtube.com/watch?v=' + video_id, published_at, published_ts,
                timing_status=timing_status, playlist_added_at=str(added) if added else None,
                source_observed_at=observed.get('observed_until', previous.get('source_observed_at')),
                source_observed_from=observed.get('observed_from', previous.get('source_observed_from')),
                source_observed_until=observed.get('observed_until', previous.get('source_observed_until')),
                completion_status=completion_status, actual_start_at=actual_start,
                actual_end_at=actual_end, scheduled_start_at=scheduled_start, timing_checked_at=checked_at,
            ))
        reason = '' if complete else '补查未完成：本轮仅检查一页上传列表，或仍有视频详情待核实。'
        return SourcePage(candidates, complete, reason)


class SubscriptionDiscovery:
    """One bounded pass; discovery never waits for captions or model processing."""
    def __init__(self, *, workspace: LocalWorkspace, source: UploadsSource, manual_timing_check: Callable[[], None] | None = None) -> None:
        self.workspace, self.source = workspace, source
        self.manual_timing_check = manual_timing_check
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    @staticmethod
    def now() -> float:
        return time.time()

    def run(self, stop: threading.Event, wake: threading.Event | None = None) -> None:
        """Independent local scheduler; no subtitle/model work runs on this loop."""
        while not stop.wait(.5):
            if self.manual_timing_check and not self._stopping.is_set():
                self.manual_timing_check()
            if self.workspace.meta('drain_paused') == '1':
                continue
            previous = json.loads(self.workspace.meta('youtube_discovery') or '{}')
            due = previous.get('next_scan_at', 0)
            if isinstance(self.source, YouTubeUploadsAPI):
                gate = self.source.connection.api.gate()
                # Retry transient/global failures only when their own deadline permits.
                if gate.get('retry_at'):
                    due = gate['retry_at']
                elif gate.get('kind') == 'authorization':
                    continue
            if time.time() < due and not (wake and wake.is_set()):
                continue
            if wake:
                wake.clear()
            self.refresh(automatic=True)

    def stop(self) -> None:
        self._stopping.set()

    def refresh(self, *, limit: int = _MAX_REFRESH_CANDIDATES, automatic: bool = False) -> DiscoveryResult:
        if not 1 <= limit <= _MAX_REFRESH_CANDIDATES:
            raise ConnectionError('Discovery limit must be between 1 and 100')
        with self._lock:
            return self._discover(limit, automatic=automatic)

    def backfill(self, *, days: int = 3, limit: int = _MAX_REFRESH_CANDIDATES) -> DiscoveryResult:
        if days <= 0:
            raise ConnectionError('Backfill days must be positive')
        # The basic pass remains bounded; requested history cannot bypass the start window.
        return self.refresh(limit=limit)

    def _discover(self, limit: int, *, automatic: bool) -> DiscoveryResult:
        started = time.time()
        discovered = scanned = 0
        truncated = False
        errors: list[str] = []
        status, error = 'healthy', None
        excluded = set(self.workspace.excluded_channels())
        sources = [source for source in self.workspace.subscription_sources() if source['channel_id'] not in excluded]
        self.workspace.set_meta('youtube_discovery_scanning', '1')
        try:
            if isinstance(self.source, YouTubeUploadsAPI) and not self.source.connection.status().authorized:
                raise AuthorizationRequired('YouTube 授权失效，请重新连接。')
            for data in sources[:MAX_SCAN_CHANNELS]:
                if self._stopping.is_set() or (automatic and self.workspace.meta('drain_paused') == '1'):
                    truncated, status = True, 'paused'
                    break
                if discovered >= limit:
                    truncated = True
                    break
                source = SubscriptionSource(channel_id=str(data['channel_id']), title=str(data['title']),
                                            description=data.get('description') or '',
                                            thumbnail_url=data.get('thumbnail_url'), subscribed_at=data.get('subscribed_at'))
                source_started = time.time()
                scanned += 1
                try:
                    page = self.source.fetch(source)
                    page_truncated = False
                    for candidate in page.candidates:
                        if discovered >= limit:
                            page_truncated = True
                            break
                        if self.workspace.add_candidate(candidate):
                            discovered += 1
                    complete = page.complete and not page_truncated
                    truncated = truncated or not complete
                    self.workspace.record_source_scan(source.channel_id,
                        last_started_at=source_started, last_finished_at=time.time(), last_success_at=time.time(),
                        valid_from=started-WINDOW_SECONDS if complete else None,
                        valid_until=started if complete else None, complete=int(complete),
                        error='', coverage_reason=page.reason or ('补查未完成：本轮新增数量达到上限。' if not complete else ''))
                except YouTubeAPIError as failure:
                    self.workspace.record_source_scan(source.channel_id, last_started_at=source_started,
                        last_finished_at=time.time(), complete=0, error=str(failure), coverage_reason='补查未完成')
                    if failure.global_failure:
                        raise
                    self.workspace.record_source_scan(source.channel_id, uploads_checked_at=0)
                    errors.append(source.title)
                    truncated = True
            if len(sources) > MAX_SCAN_CHANNELS:
                truncated = True
            if truncated and status == 'healthy':
                status = 'partial'
            if errors:
                error = '部分频道不可用，其他频道继续检查。'
        except AuthorizationRequired as failure:
            status, error, truncated = 'authorization', str(failure), True
        except YouTubeAPIError as failure:
            status, error, truncated = failure.kind, str(failure), True
        except ConnectionError:
            status, error, truncated = 'service', 'YouTube 官方 API 暂不可用，稍后重试。', True
        finally:
            self.workspace.set_meta('youtube_discovery_scanning', '0')
        self.workspace.set_meta('youtube_discovery_finished_at', str(time.time()))
        result = DiscoveryResult(discovered, scanned, truncated, status, error)
        self.workspace.set_meta('youtube_discovery', json.dumps({
            **result.as_result(), 'started_at':started, 'finished_at':time.time(),
            'next_scan_at':max(started + POLL_SECONDS, time.time()), 'source_count':len(sources),
        }))
        return result

    def snapshot(self) -> dict[str, object]:
        result: dict[str, Any] = json.loads(self.workspace.meta('youtube_discovery') or '{}')
        result['scanning'] = self.workspace.meta('youtube_discovery_scanning') == '1'
        result['sources'] = self.workspace.source_scans()
        if isinstance(self.source, YouTubeUploadsAPI):
            excluded = set(self.workspace.excluded_channels())
            sources = self.workspace.subscription_sources()
            result['budget'] = self.source.connection.api.snapshot(
                channels=sum(s['channel_id'] not in excluded for s in sources), subscriptions=len(sources))
        return result


def _published_timestamp(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
