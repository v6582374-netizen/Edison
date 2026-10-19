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

        right = client.post(
            "/v1/web-auth/login", data={"username": "shiwen", "password": "correct horse"}
        )
        assert right.status_code == 303
        assert right.headers["location"] == "/"
        assert "HttpOnly" in right.headers["set-cookie"]
        assert "Secure" in right.headers["set-cookie"]
        assert client.get("/v1/web-auth/check").status_code == 204

        logout = client.post("/v1/web-auth/logout")
        assert logout.status_code == 303
        assert (
            client.get(
                "/v1/web-auth/check", headers={"x-forwarded-uri": "/v1/sessions"}
            ).status_code
            == 401
        )


def test_login_expires_exactly_24_hours_after_issue_without_sliding(monkeypatch):
    from types import SimpleNamespace

    from coworker.server import web_auth

    clock = [1000.0]
    monkeypatch.setattr(web_auth, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    app = FastAPI()
    attach_web_auth(
        app, WebAuth("owner", bcrypt.hashpw(b"isolated-secret", bcrypt.gensalt(rounds=4)).decode())
    )
    with TestClient(app, base_url="https://edison.example", follow_redirects=False) as client:
        response = client.post(
            "/v1/web-auth/login", data={"username": "owner", "password": "isolated-secret"}
        )
        assert response.status_code == 303
        assert "Max-Age=86400" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        for elapsed in (43200, 86399.999):
            clock[0] = 1000 + elapsed
            check = client.get("/v1/web-auth/check")
            assert check.status_code == 204
            assert "set-cookie" not in check.headers
        clock[0] = 87400
        assert (
            client.get(
                "/v1/web-auth/check", headers={"x-forwarded-uri": "/v1/youtube/capability"}
            ).status_code
            == 401
        )
        assert client.get("/v1/web-auth/check").headers["location"] == "/login"
        page = client.get("/login")
        assert "24 小时" in page.text
        assert "后台" in page.text
        assert "isolated-secret" not in page.text


def test_logout_clears_secure_cookie_without_cacheable_auth_responses():
    app = FastAPI()
    attach_web_auth(
        app, WebAuth("owner", bcrypt.hashpw(b"isolated-secret", bcrypt.gensalt(rounds=4)).decode())
    )
    with TestClient(app, base_url="https://edison.example", follow_redirects=False) as client:
        client.post("/v1/web-auth/login", data={"username": "owner", "password": "isolated-secret"})
        assert client.get("/v1/web-auth/check").headers["cache-control"] == "no-store"
        logout = client.post("/v1/web-auth/logout")
        cookie = logout.headers["set-cookie"]
        assert all(
            flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=strict", "Max-Age=0")
        )
        assert logout.headers["cache-control"] == "no-store"
        assert client.get("/v1/web-auth/check").headers["cache-control"] == "no-store"


def test_fixed_session_keeps_origin_form_and_login_attempt_restrictions(monkeypatch):
    from types import SimpleNamespace

    from coworker.server import web_auth

    clock = [1000.0]
    monkeypatch.setattr(web_auth, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    app = FastAPI()
    attach_web_auth(
        app, WebAuth("owner", bcrypt.hashpw(b"isolated-secret", bcrypt.gensalt(rounds=4)).decode())
    )
    with TestClient(app, base_url="https://edison.example", follow_redirects=False) as client:
        credentials = {"username": "owner", "password": "isolated-secret"}
        assert (
            client.post(
                "/v1/web-auth/login",
                data=credentials,
                headers={"origin": "https://foreign.example"},
            ).status_code
            == 403
        )
        assert client.post("/v1/web-auth/login", json=credentials).status_code == 415
        for _ in range(5):
            response = client.post(
                "/v1/web-auth/login", data={**credentials, "password": "incorrect"}
            )
            assert "用户名或密码不正确" in response.text
        blocked = client.post("/v1/web-auth/login", data=credentials)
        assert "尝试次数过多" in blocked.text
        assert "set-cookie" not in blocked.headers
        clock[0] += 60
        assert client.post("/v1/web-auth/login", data=credentials).status_code == 303
