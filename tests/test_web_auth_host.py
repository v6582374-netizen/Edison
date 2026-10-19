"""Real HTTPS login + host queue + durable library across process lifecycles.

The client follows Caddy's forward-auth contract before forwarding a capability.
Only external Google/YouTube/model responses and time are controlled; the host
loops, connection service, downloader, translation pipeline and storage are real.
This is isolated functional evidence, not a production service/host restart claim.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import bcrypt
import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]


def eventually(check, timeout=25):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (httpx.TransportError, FileNotFoundError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"Expected observable progress, last result: {last!r}")


class Host:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.client = None
        self.log = (root / "host.log").open("a")
        (root / "password.hash").write_bytes(
            bcrypt.hashpw(b"isolated-login-secret", bcrypt.gensalt(rounds=4))
        )
        self.set_clock(1792368000)
        (root / "published").write_text("1792367900")
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-keyout",
                str(root / "key.pem"),
                "-out",
                str(root / "cert.pem"),
                "-subj",
                "/CN=localhost",
            ],
            check=True,
            capture_output=True,
        )
        self.start()

    def start(self):
        (self.root / "port").unlink(missing_ok=True)
        env = {
            **os.environ,
            "PYTHONPATH": f"{REPO}:{REPO / 'src'}",
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_DATA_HOME": str(self.root / "data"),
        }
        self.process = subprocess.Popen(
            [sys.executable, str(REPO / "tests/web_auth_host_fixture.py"), str(self.root)],
            cwd=REPO,
            env=env,
            stdout=self.log,
            stderr=self.log,
        )
        eventually(lambda: (self.root / "port").exists())
        cookies = self.client.cookies if self.client else None
        if self.client:
            self.client.close()
        self.client = httpx.Client(
            base_url=f"https://127.0.0.1:{(self.root / 'port').read_text()}",
            verify=False,
            timeout=10,
            cookies=cookies,
            follow_redirects=False,
        )
        eventually(lambda: self.client.get("/login").status_code in (200, 303))

    def stop(self, crash=False):
        if self.process and self.process.poll() is None:
            self.client.close()
            self.process.send_signal(signal.SIGKILL if crash else signal.SIGTERM)
            if not crash:
                # A normal shutdown allows the external in-flight model response
                # to finish; the crash variant intentionally provides no response.
                self.release("model-release-two00000002")
            self.process.wait(timeout=15)

    def close(self):
        self.stop(crash=True)
        self.client.close()
        self.log.close()

    def set_clock(self, value):
        staged = self.root / "clock.next"
        staged.write_text(str(value))
        staged.replace(self.root / "clock")

    def release(self, name):
        (self.root / name).touch()

    def check(self):
        return self.client.get(
            "/v1/web-auth/check", headers={"x-forwarded-uri": "/v1/youtube/capability"}
        )

    def login(self):
        response = self.client.post(
            "/v1/web-auth/login", data={"username": "owner", "password": "isolated-login-secret"}
        )
        assert response.status_code == 303
        assert self.check().status_code == 204

    def api(self, capability, **arguments):
        assert self.check().status_code == 204, "Caddy must reject unauthenticated workspace access"
        response = self.client.post(
            "/v1/youtube/capability",
            headers={"x-openworker-token": "isolated-proxy-token"},
            json={"capability": capability, "arguments": arguments},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"], payload
        return payload["result"]

    def calls(self, kind):
        path = self.root / "external.jsonl"
        return (
            [
                event["value"]
                for line in path.read_text().splitlines()
                if (event := json.loads(line))["kind"] == kind
            ]
            if path.exists()
            else []
        )

    def prepare(self):
        self.login()
        self.api(
            "connection.configure",
            client_id="fixture-client",
            client_secret="fixture-client-secret",
        )
        assert self.api("connection.authorize")["authorized"]
        self.api("activity.resume")
        eventually(lambda: self.api("activity.list", state="queued")["total"] == 3)
        self.api("activity.cancel", video_ids=["cancel00003"])


@pytest.fixture
def host(tmp_path):
    server = Host(tmp_path)
    try:
        yield server
    finally:
        server.close()


@pytest.mark.parametrize("loss", ["closed_page", "logout", "expired"])
def test_background_work_continues_after_access_ends(host, loss):
    host.prepare()
    host.release("model-ready")
    eventually(lambda: host.calls("caption") == ["one00000001"])
    if loss == "logout":
        response = host.client.post("/v1/web-auth/logout")
        assert response.headers["location"] == "/login"
        assert host.check().status_code == 401
    elif loss == "expired":
        host.set_clock(1792368000 + 86399.999)
        assert host.check().status_code == 204
        host.set_clock(1792368000 + 86400)
        assert host.check().status_code == 401
    if loss != "expired":
        host.set_clock(1792368000 + 6 * 3600 + 30)
    # Subscription maintenance is independent of the browser too.
    eventually(lambda: host.calls("official").count("subscriptions") >= 2)
    # No workspace request is made while external evidence proves the real worker
    # reaches the model after captions are released with the page absent.
    host.release("captions-release")
    host.release("model-release-one00000001")
    host.release("model-release-two00000002")
    eventually(lambda: "one00000001" in host.calls("composition"))
    time.sleep(0.3)
    host.login()
    eventually(lambda: host.api("activity.list", state="ready")["total"] >= 1)
    document = host.api("documents.get", video_id="one00000001")
    assert "one00000001" in document["markdown"]
    assert host.api("connection.status")["authorized"]
    assert not host.api("activity.snapshot")["drain_paused"]
    assert host.api("activity.list", state="cancelled")["total"] == 1
    assert "cancel00003" not in host.calls("caption")
    # Turning off Auto Update is a separate explicit permission change.
    assert host.api("activity.drain_pause")["drain_paused"]
    host.client.post("/v1/web-auth/logout")
    host.login()
    assert host.api("activity.snapshot")["drain_paused"]
    for secret in ("fixture-refresh", "fixture-client-secret", "isolated-login-secret"):
        assert secret not in json.dumps(document)
        assert secret not in (host.root / "host.log").read_text()


@pytest.mark.parametrize("crash", [False, True], ids=["graceful", "killed"])
def test_restart_requires_login_preserving_authorization_work_and_stage_results(host, crash):
    host.prepare()
    host.release("captions-release")
    host.release("model-release-one00000001")
    host.release("model-ready")
    eventually(lambda: host.api("activity.list", state="ready")["total"] == 1)
    original = host.api("documents.get", video_id="one00000001")
    host.api("library.set_reading_state", video_id="one00000001", reading_state="read")
    # Advance the external clock past the real platform pacing period; no queue
    # state/cooldown mutation and no direct prepare_one/run_next call.
    host.set_clock(1792368000 + 30)
    eventually(lambda: "two00000002" in host.calls("composition"))
    caption_calls = host.calls("caption")
    translation_calls = host.calls("translation")
    host.stop(crash=crash)
    host.start()
    assert host.check().status_code == 401
    assert host.client.get("/login").status_code == 200
    host.release("model-release-two00000002")
    # The restarted server refreshes the durable official grant on the next
    # scheduled subscription check, without a logged-in page requesting it.
    host.set_clock(1792368000 + 6 * 3600 + 30)
    eventually(lambda: "token" in host.calls("official"))
    host.login()
    eventually(lambda: host.api("activity.list", state="ready")["total"] == 2)
    assert host.api("connection.status")["authorized"]
    assert not host.api("activity.snapshot")["drain_paused"]
    assert host.api("activity.list", state="cancelled")["total"] == 1
    assert host.calls("caption") == caption_calls  # retained captions reused
    assert host.calls("translation") == translation_calls  # complete translation reused
    assert host.api("documents.get", video_id="one00000001") == original
    asset = host.api("library.inspect", video_id="one00000001")
    assert asset["reading_state"] == "read"
    assert "two00000002" in host.api("documents.get", video_id="two00000002")["markdown"]

    # A later explicit Auto Update choice is also durable across either restart.
    host.api("activity.drain_pause")
    host.stop(crash=crash)
    host.start()
    assert host.check().status_code == 401
    host.login()
    assert host.api("activity.snapshot")["drain_paused"]
    assert host.api("activity.list", state="cancelled")["total"] == 1
