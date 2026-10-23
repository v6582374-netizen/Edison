"""Real host/workspace with a controlled clock and external service boundaries."""
import hashlib
import json
import os
import socket
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

root = Path(sys.argv[1])
os.environ.update(COWORKER_STATE_DIR=str(root / 'host'),
                  COWORKER_API_TOKEN='freshness-e2e',
                  YOUTUBE_WORKBENCH_WORKSPACE=str(root / 'youtube'))
sys.path.insert(0, str(Path.cwd() / 'tests'))

import uvicorn
from shorts_fixture import RegularVideos
from translation_fixture import source_from_prompt
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app, youtube
from youtube_strataread.downloader.youtube import SubtitleResult
from youtube_strataread.workbench import workspace, discovery, shorts
from youtube_strataread.workbench.connection import SubscriptionSource


def now():
    return float((root / 'clock').read_text())


def wait(name):
    deadline = time.monotonic() + 90
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise TimeoutError(name)
        time.sleep(.03)


workspace.time = discovery.time = SimpleNamespace(time=now)


class Feeds:
    def __init__(self, *, workspace, connection):
        pass

    def fetch(self, source):
        entries = json.loads((root / 'videos.json').read_text())
        return discovery.SourcePage([discovery.Candidate(
            video_id=e['id'], channel_id='channel', channel_title='测试频道', title=e['id'],
            url=f"https://www.youtube.com/watch?v={e['id']}",
            published_at=e.get('published_at', datetime.fromtimestamp(e['ts'] or 0, timezone.utc).isoformat()),
            published_ts=e.get('ts'),
        ) for e in entries])


class CaptionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        video_id = self.path.removesuffix('.srt').removeprefix('/')
        (root / f'caption-{video_id}').touch()
        wait('captions-release')
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'1\n00:00:00,000 --> 00:00:02,000\nThe complete source remains available.\n')

    def log_message(self, *args):
        pass


captions_server = ThreadingHTTPServer(('127.0.0.1', 0), CaptionHandler)
threading.Thread(target=captions_server.serve_forever, daemon=True).start()

# Replace only the external YouTube metadata response; real yt-dlp fetches SRT
# over HTTP and the real workbench adapter controls the commencement boundary.
from yt_dlp import YoutubeDL


def extract(self, url, download=False):
    video_id = url.split('=')[-1]
    (root / f'metadata-{video_id}').touch()
    if (root / 'hold-metadata').exists():
        wait('metadata-release')
    caption_url = f'http://127.0.0.1:{captions_server.server_port}/{video_id}.srt'
    info = {
        'id': video_id, 'title': video_id, 'extractor': 'youtube', 'extractor_key': 'Youtube',
        'webpage_url': url, 'live_status': 'is_live' if video_id == 'event-video' else 'not_live',
        'subtitles': {'en': [{'ext': 'srt', 'url': caption_url}]}, 'automatic_captions': {},
        'formats': [{'url': caption_url, 'ext': 'mp4', 'format_id': 'fixture'}],
    }
    return self.process_ie_result(info, download=download)


YoutubeDL.extract_info = extract


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        (root / 'model-started').touch()
        fingerprint = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        calls = root / 'model-calls'
        with calls.open('a') as output:
            output.write(fingerprint + '\n')
        if not ((root / 'first-model-response').exists() and len(calls.read_text().splitlines()) == 1):
            wait('model-release')
        return AssistantTurn(text=source_from_prompt(messages[-1]['content']), finish_reason='stop')

    def capabilities(self, model):
        return ModelCapabilities()


def classify(self, video_id, **_kwargs):
    (root / f'classification-{video_id}').touch()
    wait('classification-release')
    return False


youtube.YouTubeWorkbench._sync_subscriptions = lambda self: None
youtube.YouTubeUploadsAPI = Feeds
shorts.YouTubeShortsClassifier.classify = classify
ws = workspace.LocalWorkspace.open(root / 'youtube')
ws.set_meta('youtube_oauth_authorized', '1')
ws.replace_subscription_sources([SubscriptionSource(channel_id='channel', title='测试频道')])
# Existing caption-only assets are snapshot inputs for the translation-entry tests.
# This is not used by discovery/admission tests, which always enter through Feeds.
if (root / 'retained').exists() and not (root / 'retained-seeded').exists():
    timestamp = now() - 71 * 3600
    ws.add_candidate(discovery.Candidate('retained-video', 'channel', '测试频道', 'retained-video',
        'https://www.youtube.com/watch?v=retained-video',
        datetime.fromtimestamp(timestamp, timezone.utc).isoformat(), timestamp))
    ws.store_transcript('retained-video', language='en',
        srt_text='1\n00:00:00,000 --> 00:00:02,000\nThe complete source remains available.\n')
    (root / 'retained-seeded').touch()

# An old on-disk workspace is fixture input, not a shortcut around new discovery.
if (root / 'legacy').exists() and not (root / 'legacy-seeded').exists():
    import sqlite3
    for key, age, evidence in [
        ('evidence-old', 100, True), ('evidence-fresh', 71, True),
        ('no-evidence-old', 100, False), ('no-evidence-fresh', 71, False),
        ('mismatched-old', 100, True),
    ]:
        timestamp = now() - age * 3600
        ws.add_candidate(discovery.Candidate(key, 'channel', '测试频道', key,
            f'https://www.youtube.com/watch?v={key}',
            datetime.fromtimestamp(timestamp, timezone.utc).isoformat(), timestamp))
        if evidence:
            path = ws.store_transcript(key, language='en',
                srt_text='1\n00:00:00,000 --> 00:00:02,000\nThe complete source remains available.\n')
            if key == 'mismatched-old':
                Path(path).write_text('unrelated source')
    with sqlite3.connect(ws.database_path) as db:
        db.execute("UPDATE candidates SET preparation_state = 'queued', preparation_started_at = ?, preparation_stage = 'checking'", (now() - 1000,))
        db.execute("DELETE FROM workspace_meta WHERE key = 'youtube_commencement_v1'")
        db.execute('ALTER TABLE candidates DROP COLUMN commenced_at')
        db.execute('ALTER TABLE candidates DROP COLUMN request_kind')
    (root / 'legacy-seeded').touch()
manager = SessionManager(data_dir=root / 'host', provider=Provider(), model='fixture-model')
manager.get_settings = lambda: {'model_ready': (root / 'model-ready').exists()}
app = create_app(manager)
# Publication/qualification assertions use the exact selected epoch. Actual
# downloader slots still need advancing time, including across a host restart.
host = youtube.start_youtube(app, manager)
host.workspace.youtube_requests.now = lambda: now() + max(0, time.time() - (root / 'clock').stat().st_mtime)
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
print(json.dumps({'port': sock.getsockname()[1]}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])
