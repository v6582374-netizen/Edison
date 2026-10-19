"""One explicit video request, sharing subscription assets and preparation work."""
from __future__ import annotations

import html
import re
import time
from urllib.parse import parse_qs, urlsplit

from youtube_strataread.workbench.connection import ConnectionError, ConnectionService
from youtube_strataread.workbench.discovery import Candidate
from youtube_strataread.workbench.freshness import event_completion, timestamp
from youtube_strataread.workbench.workspace import LocalWorkspace


def video_identity(url: object) -> str:
    if not isinstance(url, str) or len(url) > 4096 or any(char.isspace() for char in url.strip()):
        raise ValueError('请提供一个完整的 YouTube 视频链接，不支持批量提交。')
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise ValueError()
        host = (parsed.hostname or '').lower()
        parts = parsed.path.strip('/').split('/')
        if host == 'youtu.be' and len(parts) == 1:
            video_id = parts[0]
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            if parsed.path == '/watch':
                values = parse_qs(parsed.query).get('v', [])
                if len(values) != 1:
                    raise ValueError()
                video_id = values[0]
            elif len(parts) == 2 and parts[0] in ('shorts', 'live', 'embed'):
                video_id = parts[1]
            else:
                raise ValueError()
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError()
        return video_id
    except ValueError:
        raise ValueError('请提供包含明确视频的 YouTube 链接；不支持频道或纯播放列表。') from None


class ManualVideoRequests:
    def __init__(self, workspace: LocalWorkspace, connection: ConnectionService):
        self.workspace, self.connection = workspace, connection

    def recheck_timing(self) -> None:
        """Bounded official rechecks for explicit requests, independent of subscription scope."""
        now = time.time()
        if now < float(self.workspace.meta('youtube_manual_timing_due') or 0):
            return
        waiting = self.workspace.pending_manual_timing(now)
        if not waiting:
            return
        self.workspace.set_meta('youtube_manual_timing_due', str(now + 300))
        for video_id in waiting:
            try:
                self.workspace.refresh_manual_timing(self.verify(video_id))
            except (ConnectionError, ValueError):
                # An inaccessible result never fabricates completion or deletes retained facts.
                continue

    def submit(self, url: object, request_id: object) -> dict:
        video_id = video_identity(url)
        request_id = self.request_id(request_id)
        receipt = self.workspace.manual_receipt(request_id)
        if receipt:
            if receipt != video_id:
                raise ValueError('提交标识已被其他视频使用，请重新提交。')
            return self.result(video_id)
        try:
            self.workspace.asset(video_id)
            candidate = None  # Local assets remain usable without Google/network access.
        except KeyError:
            candidate = self.verify(video_id)
        self.workspace.accept_manual_request(video_id, request_id, candidate=candidate)
        return self.result(video_id)

    def confirm(self, request_id: object) -> dict:
        video_id = self.workspace.manual_receipt(self.request_id(request_id))
        return self.result(video_id) if video_id else {'accepted': False, 'disposition': 'unconfirmed'}

    def act(self, video_id: str, request_id: object, action: str) -> dict:
        self.workspace.accept_manual_request(video_id, self.request_id(request_id), action=action)
        return self.result(video_id)

    @staticmethod
    def request_id(value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', value):
            raise ValueError('提交标识无效，请重新提交。')
        return value

    def result(self, video_id: str) -> dict:
        try:
            asset = self.workspace.asset(video_id)
        except KeyError:
            return {'accepted': True, 'disposition': 'deleted', 'video_id': video_id}
        disposition = ('document' if asset['manuscript_version'] else 'action'
                       if asset['preparation_state'] in ('failed', 'cancelled', 'unavailable', 'filtered') else 'progress')
        return {'accepted': True, 'disposition': disposition, 'asset': asset}

    def verify(self, video_id: str) -> Candidate:
        payload = self.connection.youtube_get('videos', {'part':'snippet,status,contentDetails,liveStreamingDetails', 'id':video_id})
        matches = [item for item in payload.get('items', []) if isinstance(item, dict) and item.get('id') == video_id]
        if len(matches) != 1:
            raise ValueError('无法验证此视频：可能已删除、设为私有或没有访问权限；尚未添加。')
        video = matches[0]
        snippet, status = video.get('snippet', {}), video.get('status', {})
        if not isinstance(snippet, dict) or not isinstance(status, dict) or not snippet.get('channelId') or not snippet.get('title') or status.get('privacyStatus') not in ('public', 'unlisted'):
            raise ValueError('此视频的访问权限或基础信息尚未验证；尚未添加。')
        live = video.get('liveStreamingDetails', {})
        if not isinstance(live, dict):
            live = {}
        broadcast = snippet.get('liveBroadcastContent')
        event = 'liveStreamingDetails' in video or broadcast in ('live', 'upcoming')
        timing = 'event' if event else 'publication' if broadcast == 'none' and status.get('uploadStatus') == 'processed' else 'unverified'
        completion = event_completion(broadcast, live, time.time()) if event and status.get('uploadStatus') == 'processed' else 'unverified'
        published = str(snippet.get('publishedAt') or '')
        return Candidate(video_id, str(snippet['channelId']), str(snippet.get('channelTitle') or snippet['channelId']),
            html.unescape(str(snippet['title'])), 'https://www.youtube.com/watch?v='+video_id,
            published, timestamp(published), timing_status=timing, completion_status=completion,
            actual_start_at=live.get('actualStartTime'), actual_end_at=live.get('actualEndTime'),
            scheduled_start_at=live.get('scheduledStartTime'), timing_checked_at=time.time())
