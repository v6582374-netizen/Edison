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
measurement = len(sys.argv) > 2 and sys.argv[2] == "native-measurement"
if measurement:
    (root / "measurement").touch()
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
from youtube_strataread.workbench.resource_governor import LinuxResources, ResourceObservation
from youtube_strataread.workbench.caption_process import CaptionProcess
from youtube_strataread.workbench.content_session import ContentSessionError


def resource_observation(self):
    path = root / "resources.json"
    return ResourceObservation(**json.loads(path.read_text())) if path.exists() else ResourceObservation(2*1024**3, 0, 0, True)
if not measurement:
    LinuxResources.observe = resource_observation

def now():
    if measurement:
        return time.time()
    return float((root / 'clock').read_text())
workspace.time = manual.time = discovery.time = SimpleNamespace(time=now)
content_session.time = SimpleNamespace(time=now)
original_session_init = content_session.ContentSession.__init__
def session_init(self, *args, **kwargs):
    original_session_init(self, *args, now=now, **kwargs)
content_session.ContentSession.__init__ = session_init

NativeBrowser = session_browser.ServerBrowser

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
        if command == 'text':
            (root / 'browser-input').write_text(args['text'])
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
        if measurement:
            import hashlib
            source = source_from_prompt(messages[-1]['content'])
            digest = hashlib.sha256(source.encode()).hexdigest()
            with (root / "model-calls.jsonl").open("a") as output:
                output.write(json.dumps({"at":time.time(), "input_characters":len(source), "sha256":digest})+"\n")
            # Fixed external service response latency, in addition to the real
            # host's translation, validation, checkpoint and persistence work.
            time.sleep(.15)
        if (root / "model-hold").exists():
            (root / "model-active").write_text("active")
            until = time.monotonic() + 60
            while (root / "model-hold").exists() and time.monotonic() < until:
                time.sleep(0.02)
        return AssistantTurn(text=source_from_prompt(messages[-1]['content']),finish_reason='stop')
    def capabilities(self, model):
        return ModelCapabilities()
manager=SessionManager(data_dir=root/'host',provider=Provider(),model='fixture-model')
manager.get_settings=lambda:{'model_ready':True}
app=create_app(manager)
sock=socket.socket(); sock.bind(('127.0.0.1',int(os.environ.get('EDISON_MEASUREMENT_PORT','0'))))
native = None
if measurement:
    from fastapi import Header, HTTPException
    from fastapi.responses import HTMLResponse
    from youtube_strataread.workbench.resource_governor import ResourceGovernor
    native = NativeBrowser(start_url=f"http://127.0.0.1:{sock.getsockname()[1]}/_measurement/page")
    @app.get("/_measurement/page", response_class=HTMLResponse)
    def fixture_page():
        return "<!doctype html><title>Isolated Edison resource fixture</title><h1>Native maintenance view</h1><input placeholder='Synthetic input'><p>No Google account or credentials.</p>"
    @app.post("/_measurement/native")
    def native_control(payload: dict, x_openworker_token: str = Header(default="")):
        if x_openworker_token != 'content-e2e':
            raise HTTPException(401)
        operation = payload.get("operation")
        if operation == "open":
            native.start(root / "native-profile")
        elif operation == "close":
            native.close()
        elif operation == "screen":
            frame = native.command("screen", {})
            return {"width":frame['width'],"height":frame['height']}
        elif operation == "admission":
            try:
                ResourceGovernor().require_browser_start()
            except ContentSessionError as error:
                return {"admitted":False,"kind":error.kind,"reason":str(error),"resources":ResourceGovernor().status()}
            return {"admitted":True,"resources":ResourceGovernor().status()}
        return {"running":native.alive,"pid":native.process.pid if native.process else None,
                "resources":ResourceGovernor().status(),
                "model_calls":len((root/'model-calls.jsonl').read_text().splitlines()) if (root/'model-calls.jsonl').exists() else 0}
print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
try:
    uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
finally:
    if native:
        native.close()
