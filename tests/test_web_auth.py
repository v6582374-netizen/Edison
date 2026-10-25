"""Browser login through the same routes Caddy uses in production."""

import bcrypt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from coworker.server.web_auth import WebAuth, attach_web_auth


def test_browser_login_shows_error_then_opens_workspace():
    app = FastAPI()
    auth = WebAuth("shiwen", bcrypt.hashpw(b"correct horse", bcrypt.gensalt()).decode())
    attach_web_auth(app, auth)
    with TestClient(app, base_url="https://edison.example", follow_redirects=False) as client:
        login = client.get("/login")
        assert login.status_code == 200
        assert "用户名" in login.text
        assert "密码" in login.text

        wrong = client.post("/v1/web-auth/login", data={"username": "shiwen", "password": "wrong"})
        assert wrong.status_code == 200
        assert "用户名或密码不正确" in wrong.text
        assert "set-cookie" not in wrong.headers
        assert client.get("/v1/web-auth/check", headers={"x-forwarded-uri": "/"}).status_code == 303

        right = client.post("/v1/web-auth/login", data={"username": "shiwen", "password": "correct horse"})
        assert right.status_code == 303
        assert right.headers["location"] == "/"
        assert "HttpOnly" in right.headers["set-cookie"]
        assert "Secure" in right.headers["set-cookie"]
        assert client.get("/v1/web-auth/check").status_code == 204

        logout = client.post("/v1/web-auth/logout")
        assert logout.status_code == 303
        assert client.get("/v1/web-auth/check", headers={"x-forwarded-uri": "/v1/sessions"}).status_code == 401
