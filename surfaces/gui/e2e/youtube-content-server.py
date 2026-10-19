"""Real Web, host queue, durable library; only external services and time controlled."""
# ruff: noqa: E402
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
root = Path(sys.argv[1])
os.environ.update(COWORKER_STATE_DIR=str(root / 'host'), COWORKER_API_TOKEN='content-e2e',
    YOUTUBE_WORKBENCH_WORKSPACE=str(root / 'library'), EDISON_CONTENT_SESSION_DIR=str(root / 'content'),
    EDISON_CREDENTIAL_FILE=str(root / 'credentials.json'))
sys.path.insert(0, str(Path.cwd() / 'tests'))
import uvicorn
from shorts_fixture import player_page
from translation_fixture import source_from_prompt
from coworker.server import SessionManager, create_app
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from youtube_strataread.workbench import content_session, session_browser, connection, youtube_api, shorts, workspace, manual, discovery
from youtube_strataread.workbench.caption_process import CaptionProcess
from youtube_strataread.workbench.content_session import ContentSessionError


def now():
    return float((root / 'clock').read_text())
workspace.time = manual.time = discovery.time = SimpleNamespace(time=now)
content_session.time = SimpleNamespace(time=now)
original_session_init = content_session.ContentSession.__init__
def session_init(self, *args, **kwargs):
    original_session_init(self, *args, now=now, **kwargs)
content_session.ContentSession.__init__ = session_init

class Browser:
    def __init__(self, **kwargs):
        pass
    def start(self, profile):
        profile.mkdir(parents=True, exist_ok=True)
        (root / 'browser-running').touch()
    def close(self):
        (root / 'browser-running').unlink(missing_ok=True)
    def snapshot(self):
        if not (root / 'youtube-login').exists():
            raise ContentSessionError('user', '请在 YouTube 页面完成登录及站点交接。')
        return '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tfixture-session\n'
    def command(self, command, args):
        if command == 'click':
            (root / 'youtube-login').touch()
        if command == 'screen':
            return {'image':'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7xkAAAAASUVORK5CYII=',
                    'width':1100,'height':760,'origin':'www.youtube.com'}
        return {}
session_browser.ServerBrowser = Browser

def worker_init(self):
    self.command = [sys.executable, str(Path(__file__).with_name('youtube-content-worker.py')), str(root)]
CaptionProcess.__init__ = worker_init

def response(payload):
    return BytesIO(json.dumps(payload).encode())

def google(request, timeout=30):
    url = request.full_url
    parsed = urlsplit(url)
    if parsed.hostname == 'oauth2.googleapis.com':
        return response({'access_token':'fixture-access','expires_in':86400})
    endpoint = parsed.path.rsplit('/',1)[-1]
    if endpoint == 'subscriptions':
        return response({'items':[]})
    if endpoint == 'videos':
        video = parse_qs(parsed.query)['id'][0]
        return response({'items':[{'id':video,'snippet':{'title':'Content session sample','channelId':'sample-channel',
            'channelTitle':'Sample','publishedAt':datetime.fromtimestamp(now()-100,timezone.utc).isoformat(),'liveBroadcastContent':'none'},
            'status':{'privacyStatus':'public','uploadStatus':'processed'},'contentDetails':{'duration':'PT2M'}}]})
    raise AssertionError(endpoint)
connection.urlopen = youtube_api.urlopen = google

def platform(request, cookies):
    assert cookies.parent.parent.name == 'consumers'
    assert 'fixture-session' in cookies.read_text()
    video = parse_qs(urlsplit(request.full_url).query)['v'][0]
    content = player_page(video, False)
    result = BytesIO(content.encode()); result.status=200; result.geturl=lambda:request.full_url
    return result
shorts.open_page = platform

class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text=source_from_prompt(messages[-1]['content']),finish_reason='stop')
    def capabilities(self, model):
        return ModelCapabilities()
manager=SessionManager(data_dir=root/'host',provider=Provider(),model='fixture-model')
manager.get_settings=lambda:{'model_ready':True}
app=create_app(manager)
sock=socket.socket(); sock.bind(('127.0.0.1',0))
print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
