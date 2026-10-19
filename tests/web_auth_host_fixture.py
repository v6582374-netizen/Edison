"""Isolated real host; replace platform/model transports and time only.

No queue, discovery, connection, persistence or auth method is replaced. Files
control external responses and the clock; they never edit persisted work state.
"""

# ruff: noqa: E402 -- isolate environment before importing the real host.
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

root = Path(sys.argv[1])
os.environ.update(
    COWORKER_STATE_DIR=str(root / "host"),
    COWORKER_API_TOKEN="isolated-proxy-token",
    YOUTUBE_WORKBENCH_WORKSPACE=str(root / "youtube"),
    EDISON_CREDENTIAL_FILE=str(root / "credentials.json"),
    EDISON_WEB_LOGIN="1",
    YOUTUBE_COOKIES_FILE="",
    EDISON_GOOGLE_OAUTH_CLIENT_ID="",
    EDISON_GOOGLE_OAUTH_CLIENT_SECRET="",
    EDISON_WEB_USERNAME="owner",
    EDISON_WEB_PASSWORD_HASH_FILE=str(root / "password.hash"),
)
import uvicorn
from shorts_fixture import player_page
from translation_fixture import source_from_prompt
from yt_dlp import YoutubeDL

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app, web_auth
from youtube_strataread.workbench import connection, discovery, shorts, workspace, youtube_api
from youtube_strataread.workbench.connection import OAuthCredentials


def auth_now():
    return float((root / "clock").read_text())


def now():
    # The clock file selects an epoch; real elapsed time still advances pacing
    # between requests. Auth alone uses the exact epoch for boundary assertions.
    clock = root / "clock"
    return float(clock.read_text()) + max(0, time.time() - clock.stat().st_mtime)


def record(kind, value):
    with (root / "external.jsonl").open("a") as output:
        output.write(json.dumps({"kind": kind, "value": value}) + "\n")


def wait(name):
    deadline = time.monotonic() + 60
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise TimeoutError("External fixture gate expired")
        time.sleep(0.03)


controlled = SimpleNamespace(time=now, monotonic=now)
workspace.time = discovery.time = youtube_api.time = controlled
web_auth.time = SimpleNamespace(time=auth_now, monotonic=auth_now)
# Connection uses monotonic to serialize real refresh requests; only wall time advances.
connection.time = SimpleNamespace(time=now, monotonic=time.monotonic)


class Response(BytesIO):
    status = 200

    def __init__(self, value, url=""):
        super().__init__(value.encode())
        self.url = url

    def geturl(self):
        return self.url


def external(request, timeout=30):
    parsed = urlsplit(request.full_url)
    endpoint = parsed.path.rsplit("/", 1)[-1]
    query = parse_qs(parsed.query)
    record("official", endpoint)
    if endpoint == "token":
        assert b"refresh_token=fixture-refresh" in request.data
        payload = {"access_token": "fixture-access", "expires_in": 3600}
    elif endpoint == "subscriptions":
        payload = {
            "items": [
                {"snippet": {"title": "Fixture channel", "resourceId": {"channelId": "channel"}}}
            ]
        }
    elif endpoint == "channels":
        payload = {
            "items": [
                {"id": "channel", "contentDetails": {"relatedPlaylists": {"uploads": "uploads"}}}
            ]
        }
    elif endpoint == "playlistItems":
        stamp = datetime.fromtimestamp(
            float((root / "published").read_text()), timezone.utc
        ).isoformat()
        payload = {
            "items": [
                {
                    "snippet": {"publishedAt": stamp},
                    "contentDetails": {"videoId": key, "videoPublishedAt": stamp},
                }
                for key in ["one00000001", "two00000002", "cancel00003"]
            ]
        }
    elif endpoint == "videos":
        stamp = datetime.fromtimestamp(
            float((root / "published").read_text()), timezone.utc
        ).isoformat()
        payload = {
            "items": [
                {
                    "id": key,
                    "snippet": {
                        "channelId": "channel",
                        "title": key,
                        "publishedAt": stamp,
                        "liveBroadcastContent": "none",
                    },
                    "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                }
                for key in query["id"][0].split(",")
            ]
        }
    else:
        raise AssertionError(endpoint)
    return Response(json.dumps(payload))


connection.urlopen = youtube_api.urlopen = external
# This is the external human/Google consent boundary, not ConnectionService.
connection.GoogleOAuthGateway.authorize = lambda self, config: OAuthCredentials(
    "fixture-access", "fixture-refresh", now() + 3600
)
shorts.urlopen = lambda request, timeout: Response(
    player_page(parse_qs(urlsplit(request.full_url).query)["v"][0], False), request.full_url
)


class CaptionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        key = self.path.removesuffix(".srt").removeprefix("/")
        record("caption", key)
        wait("captions-release")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            f"1\n00:00:00,000 --> 00:00:02,000\nThe source for {key} remains available.\n".encode()
        )

    def log_message(self, *args):
        pass


captions = ThreadingHTTPServer(("127.0.0.1", 0), CaptionHandler)
threading.Thread(target=captions.serve_forever, daemon=True).start()


def extract(self, url, download=False):
    key = parse_qs(urlsplit(url).query)["v"][0]
    source = f"http://127.0.0.1:{captions.server_port}/{key}.srt"
    return self.process_ie_result(
        {
            "id": key,
            "title": key,
            "extractor": "youtube",
            "extractor_key": "Youtube",
            "webpage_url": url,
            "live_status": "not_live",
            "subtitles": {"en": [{"ext": "srt", "url": source}]},
            "automatic_captions": {},
            "formats": [{"url": source, "ext": "mp4", "format_id": "fixture"}],
        },
        download=download,
    )


YoutubeDL.extract_info = extract


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        text = source_from_prompt(messages[-1]["content"])
        key = "two00000002" if "two00000002" in text else "one00000001"
        record("model", key)
        composing = "完整译文：\n" in messages[-1]["content"]
        record("composition" if composing else "translation", key)
        if key == "one00000001" or composing:
            wait(f"model-release-{key}")
        return AssistantTurn(text=text, finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


manager = SessionManager(data_dir=root / "host", provider=Provider(), model="fixture-model")
manager.get_settings = lambda: {"model_ready": (root / "model-ready").exists()}
app = create_app(manager)
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
(root / "port").write_text(str(sock.getsockname()[1]))
uvicorn.Server(
    uvicorn.Config(
        app,
        log_level="error",
        ssl_keyfile=str(root / "key.pem"),
        ssl_certfile=str(root / "cert.pem"),
    )
).run(sockets=[sock])
