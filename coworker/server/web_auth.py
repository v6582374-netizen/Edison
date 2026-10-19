"""Single-owner browser login for the self-hosted Edison instance.

Caddy asks ``/v1/web-auth/check`` before serving a page or proxying an API call.
The desktop sidecar does not enable this module.
"""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

_COOKIE = "edison_session"
_SESSION_SECONDS = 24 * 60 * 60
_ATTEMPT_SECONDS = 60
_MAX_ATTEMPTS = 5


class WebAuth:
    def __init__(self, username: str, password_hash: str) -> None:
        self.username = username
        self.password_hash = password_hash.encode()
        self.sessions: dict[str, float] = {}
        self.failed: dict[str, deque[float]] = defaultdict(deque)

    @classmethod
    def from_environment(cls) -> WebAuth | None:
        if os.environ.get("EDISON_WEB_LOGIN") != "1":
            return None
        username = os.environ["EDISON_WEB_USERNAME"]
        password_hash = Path(os.environ["EDISON_WEB_PASSWORD_HASH_FILE"]).read_text().strip()
        if not username or not password_hash:
            raise ValueError("Web login requires a username and password hash")
        return cls(username, password_hash)

    def valid(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        expires = self.sessions.get(session_id, 0)
        if expires <= time.monotonic():
            self.sessions.pop(session_id, None)
            return False
        return True

    def password_matches(self, username: str, password: str) -> bool:
        import bcrypt

        # Always check the hash, including for an unknown username.
        password_ok = bcrypt.checkpw(password.encode(), self.password_hash)
        return password_ok and secrets.compare_digest(username, self.username)

    def attempt_allowed(self, address: str) -> bool:
        now = time.monotonic()
        attempts = self.failed[address]
        while attempts and now - attempts[0] >= _ATTEMPT_SECONDS:
            attempts.popleft()
        return len(attempts) < _MAX_ATTEMPTS

    def record_failure(self, address: str) -> None:
        self.failed[address].append(time.monotonic())

    def issue(self) -> str:
        session_id = secrets.token_urlsafe(32)
        self.sessions[session_id] = time.monotonic() + _SESSION_SECONDS
        return session_id


def _page(error: str = "") -> HTMLResponse:
    message = f'<p class="error" role="alert">{error}</p>' if error else ""
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 Edison</title><style>
:root {{ color-scheme: light dark; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
body {{ min-height:100vh; margin:0; display:grid; place-items:center; background:#f5f4f1; color:#242424; }}
main {{ box-sizing:border-box; width:min(400px,calc(100vw - 32px)); padding:36px; background:white;
  border:1px solid #e8e6e0; border-radius:18px; box-shadow:0 18px 60px #0000000c; }}
h1 {{ font-size:25px; margin:0 0 8px; }} p {{ color:#656565; margin:0 0 28px; line-height:1.5; }}
label {{ display:block; font-size:14px; font-weight:600; margin:20px 0 8px; }}
input {{ box-sizing:border-box; width:100%; height:44px; padding:0 12px; border:1px solid #b8b8b8;
  border-radius:9px; font:inherit; background:transparent; color:inherit; }}
input:focus {{ outline:2px solid #6262a9; outline-offset:2px; }}
button {{ width:100%; height:44px; margin-top:28px; border:0; border-radius:9px; background:#25252a;
  color:white; font:inherit; font-weight:600; cursor:pointer; }}
.error {{ margin:18px 0 0; color:#ad2525; font-size:14px; }}
@media (prefers-color-scheme:dark) {{ body {{ background:#202025; color:#eee; }}
 main {{ background:#29292f; border-color:#414148; }} p {{ color:#aaa; }}
 input {{ border-color:#777; }} button {{ background:#e7e7ea; color:#222; }} .error {{ color:#ff9999; }} }}
</style></head><body><main><h1>Edison</h1><p>登录后固定 24 小时可访问工作空间。退出或到期不影响已许可的 YouTube 后台工作。</p>
<form method="post" action="/v1/web-auth/login">
<label for="username">用户名</label><input id="username" name="username" autocomplete="username" required autofocus>
<label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required>
{message}<button type="submit">登录</button></form></main></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"})


def attach_web_auth(app: FastAPI, auth: WebAuth) -> None:
    @app.get("/login")
    def login_page(request: Request) -> Response:
        if auth.valid(request.cookies.get(_COOKIE)):
            return RedirectResponse("/", status_code=303)
        return _page()

    @app.post("/v1/web-auth/login")
    async def login(request: Request) -> Response:
        # A foreign site cannot submit credentials or set a session through this endpoint.
        origin = request.headers.get("origin")
        # Caddy rewrites the same-origin browser Origin to localhost for the
        # existing sidecar API origin gate. A foreign Origin is left untouched.
        if origin and origin not in {f"https://{request.headers.get('host')}", "http://localhost"}:
            return JSONResponse({"error": "invalid origin"}, status_code=403)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            return JSONResponse({"error": "invalid form"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                return _page("登录请求过大，请重试。")
        try:
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            return JSONResponse({"error": "invalid form"}, status_code=400)
        username = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        address = request.client.host if request.client else "unknown"
        if not auth.attempt_allowed(address):
            return _page("尝试次数过多，请一分钟后重试。")
        if not auth.password_matches(username, password):
            auth.record_failure(address)
            return _page("用户名或密码不正确，请重试。")
        auth.failed.pop(address, None)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(_COOKIE, auth.issue(), max_age=_SESSION_SECONDS, secure=True, httponly=True, samesite="strict", path="/")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/v1/web-auth/check")
    def check(request: Request) -> Response:
        if auth.valid(request.cookies.get(_COOKIE)):
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        original_path = request.headers.get("x-forwarded-uri", "/")
        if original_path.startswith(("/v1/", "/ws/")):
            return JSONResponse({"error": "login required"}, status_code=401, headers={"Cache-Control": "no-store"})
        return RedirectResponse("/login", status_code=303, headers={"Cache-Control": "no-store"})

    @app.post("/v1/web-auth/logout")
    def logout(request: Request) -> Response:
        session_id = request.cookies.get(_COOKIE)
        if session_id:
            auth.sessions.pop(session_id, None)
        response = RedirectResponse("/login", status_code=303, headers={"Cache-Control": "no-store"})
        response.delete_cookie(_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response
