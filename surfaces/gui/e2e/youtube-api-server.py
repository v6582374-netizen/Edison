"""Real UI host and OAuth with external Google, keyring, captions and model fixtures."""
# ruff: noqa: E402
import json
import os
import socket
import sys
import time
import webbrowser
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen as real_urlopen

root = Path(sys.argv[1])
os.environ.update(COWORKER_STATE_DIR=str(root / 'host'),
    COWORKER_API_TOKEN='youtube-api-e2e', YOUTUBE_WORKBENCH_WORKSPACE=str(root / 'youtube'),
    EDISON_GOOGLE_OAUTH_CLIENT_ID='fixture-client', EDISON_GOOGLE_OAUTH_CLIENT_SECRET='fixture-client-secret')
sys.path.insert(0, str(Path.cwd() / 'tests'))

import uvicorn
from keyring.backends.macOS import Keyring
from translation_fixture import source_from_prompt
from shorts_fixture import player_page

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from youtube_strataread.downloader.youtube import SubtitleResult
from youtube_strataread.workbench import connection, discovery, manual, shorts, workspace
from youtube_strataread.workbench.library import YtDlpCaptions


def now():
    return float((root / 'clock').read_text())


def config():
    return json.loads((root / 'scenario.json').read_text())


def stamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def wait(name):
    deadline = time.monotonic() + 90
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise TimeoutError(name)
        time.sleep(.02)


def keyring_read(self, service, account):
    file = root / 'keyring-fixture.json'
    return file.read_text() if file.exists() else None


def keyring_write(self, service, account, password):
    (root / 'keyring-fixture.json').write_text(password)


Keyring.get_password, Keyring.set_password = keyring_read, keyring_write
workspace.time = discovery.time = manual.time = SimpleNamespace(time=now)


def response(payload):
    return BytesIO(json.dumps(payload).encode())


def google(request, timeout=30):
    url = request if isinstance(request, str) else request.full_url
    parsed = urlsplit(url)
    if parsed.hostname == '127.0.0.1':
        return real_urlopen(request, timeout=timeout)
    spec = config()
    if parsed.hostname == 'oauth2.googleapis.com':
        if spec.get('auth_failure'):
            raise HTTPError(url, 400, 'Bad Request', {}, response({'error':'invalid_grant'}))
        return response({'access_token':'fixture-bearer-secret', 'refresh_token':'fixture-refresh-secret', 'expires_in':86400})
    if parsed.hostname != 'www.googleapis.com':
        raise OSError('Only the official Data API is available')
    endpoint = parsed.path.rsplit('/', 1)[-1]
    query = parse_qs(parsed.query)
    with (root / 'api-requests.jsonl').open('a') as log:
        log.write(json.dumps({'endpoint':endpoint, 'query':query, 'at':now()}) + '\n')
    mode = spec.get('global_failure')
    if mode and (not spec.get('failure_endpoint') or spec['failure_endpoint'] == endpoint):
        status, reason = {'quota':(403,'quotaExceeded'), 'authorization':(401,'authError'),
                          'service':(503,'backendError')}[mode]
        raise HTTPError(url, status, 'External failure', {}, response({'error':{'errors':[{'reason':reason}]}}))
    channels = spec.get('channels', ['alpha'])
    videos = spec.get('videos', [])
    if endpoint == 'subscriptions':
        return response({'items':[{'snippet':{'resourceId':{'channelId':c}, 'title':c}} for c in channels]})
    if endpoint == 'channels':
        return response({'items':[{'id':c, 'contentDetails':{'relatedPlaylists':{'uploads':'uploads-' + c}}}
                                  for c in query['id'][0].split(',') if c in channels]})
    if endpoint == 'playlistItems':
        channel = query['playlistId'][0].removeprefix('uploads-')
        if channel == spec.get('channel_failure'):
            raise HTTPError(url, 404, 'Missing playlist', {}, response({'error':{'errors':[{'reason':'playlistNotFound'}]}}))
        items = [{'id':'entry-' + v['id'], 'snippet':{'publishedAt':stamp(v.get('added',v['published'])),
            'title':v['id'], 'channelId':channel}, 'contentDetails':{'videoId':v['id'],
            'videoPublishedAt':stamp(v.get('claimed_publication',v['published']))}}
                 for v in videos if v.get('channel','alpha') == channel and v.get('listed', True)]
        result = {'items':items}
        if spec.get('more_pages'):
            result['nextPageToken'] = 'more'
        return response(result)
    if endpoint == 'videos':
        return response({'items':[{'id':v['id'], 'snippet':{'title':v['id'], 'channelId':v.get('channel','alpha'),
            'channelTitle':v.get('channel','alpha'), 'publishedAt':stamp(v['published']),
            'liveBroadcastContent':v.get('kind','none')}, 'status':{'privacyStatus':'public','uploadStatus':'processed'},
            'contentDetails':{'duration':'PT2M'}, **({'liveStreamingDetails':v['live']} if 'live' in v else {})} for v in videos if v['id'] in query['id'][0].split(',')]})
    raise AssertionError(endpoint)


connection.urlopen = google
if hasattr(discovery, 'urlopen'):
    discovery.urlopen = google
try:
    from youtube_strataread.workbench import youtube_api
    youtube_api.urlopen = google
    youtube_api.time = SimpleNamespace(time=now)
except ImportError:
    pass


def browser(url):
    query = parse_qs(urlsplit(url).query)
    real_urlopen(query['redirect_uri'][0] + '?' + urlencode({'code':'fixture-code','state':query['state'][0]})).close()
    return True


webbrowser.open = browser
original_classify = shorts.YouTubeShortsClassifier.classify


def classify(self, video_id, **kwargs):
    if config().get('platform_pages'):
        return original_classify(self, video_id, **kwargs)
    return False


def platform_page(request, timeout=15):
    video_id = parse_qs(urlsplit(request.full_url).query)['v'][0]
    video = next(v for v in config()['videos'] if v['id'] == video_id)
    with (root / 'classification-requests.jsonl').open('a') as log:
        log.write(json.dumps({'video_id':video_id, 'at':now()}) + '\n')
    if config().get('hold_classification'):
        (root / 'classification-started').touch()
        wait('classification-release')
    content = player_page(video_id, video.get('shorts', False))
    if 'live' in video:
        content = content.replace('"lengthSeconds": "120"', '"lengthSeconds": "120", "isLiveContent": true')
    result = BytesIO(content.encode())
    result.status = 200
    result.geturl = lambda: request.full_url
    return result


shorts.YouTubeShortsClassifier.classify = classify
shorts.urlopen = platform_page


def captions(self, url, *, before_subtitles=None):
    video_id = url.split('=')[-1]
    if before_subtitles:
        video = next(v for v in config().get('videos', []) if v['id'] == video_id)
        before_subtitles({'id':video_id,'live_status':video.get('caption_status','not_live')})
    (root / ('caption-' + video_id)).touch()
    if config().get('caption_failure') is True:
        from youtube_strataread.downloader.youtube import YouTubeError
        raise YouTubeError('This video is unavailable')
    with (root / 'caption-requests.jsonl').open('a') as log:
        log.write(json.dumps({'video_id':video_id, 'at':now()}) + '\n')
    if config().get('caption_failure') == 'absent-all' or (config().get('caption_failure') == 'absent' and video_id == 'absent-one'):
        from youtube_strataread.downloader.request_policy import ConfirmedCaptionAbsence
        raise ConfirmedCaptionAbsence('可访问视频尚无字幕')
    if config().get('caption_failure') == 'terminal':
        from youtube_strataread.downloader.youtube import YouTubeError
        raise YouTubeError({'private':'Private video', 'deleted':'This video has been removed',
                            'denied':'You do not have permission to access this video',
                            'unknown':'no subtitles: unknown empty response'}[video_id])
    if config().get('caption_failure') == 'rate':
        raise HTTPError(url,429,'Too Many Requests',{'Retry-After':config().get('retry_after','0')},None)
    if config().get('caption_failure') == 'network':
        raise ConnectionError('Connection reset by peer')
    if config().get('caption_cooldown'):
        from youtube_strataread.downloader.youtube import YouTubeError
        raise YouTubeError('HTTP Error 429: Too Many Requests')
    return SubtitleResult(video_id, video_id, 'en', False,
        '1\n00:00:00,000 --> 00:00:02,000\nThe full original source is retained.\n')


YtDlpCaptions.acquire = captions


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        (root / 'model-started').touch()
        with (root / 'model-requests.jsonl').open('a') as log:
            log.write(json.dumps({'at':now()}) + '\n')
        if config().get('model_failure'):
            raise HTTPError('https://model.example',429,'Account limit',{'Retry-After':'1200'},None)
        wait('model-release')
        return AssistantTurn(text=source_from_prompt(messages[-1]['content']), finish_reason='stop')

    def capabilities(self, model):
        return ModelCapabilities()


manager = SessionManager(data_dir=root / 'host', provider=Provider(), model='fixture-model')
manager.get_settings = lambda: {'model_ready': config().get('model_ready', True)}
app = create_app(manager)
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
print(json.dumps({'port':sock.getsockname()[1]}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])
