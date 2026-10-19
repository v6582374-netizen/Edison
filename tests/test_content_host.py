"""Real Web/host loops/temp library, controlled external browser and captions."""

from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app
from youtube_strataread.workbench.content_session import ContentSessionError
from youtube_strataread.workbench.session_browser import ServerBrowser


def test_web_content_handoff_reports_login_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_WORKBENCH_WORKSPACE", str(tmp_path / "library"))
    monkeypatch.setenv("EDISON_CONTENT_SESSION_DIR", str(tmp_path / "content"))
    monkeypatch.setenv("EDISON_CREDENTIAL_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("COWORKER_API_TOKEN", "content-test")
    monkeypatch.setattr(ServerBrowser, "start", lambda self, profile: None)
    monkeypatch.setattr(ServerBrowser, "close", lambda self: None)

    def incomplete(self):
        raise ContentSessionError("user", "请在 YouTube 页面完成登录。")

    monkeypatch.setattr(ServerBrowser, "snapshot", incomplete)
    app = create_app(SessionManager(data_dir=tmp_path / "host"))
    with TestClient(app, headers={"X-OpenWorker-Token": "content-test"}) as client:

        def call(name, **arguments):
            return client.post(
                "/v1/youtube/capability", json={"capability": name, "arguments": arguments}
            ).json()

        assert call("content_session.open")["ok"]
        failed = call("content_session.confirm", video_id="abcdefghijk", same_account=True)
        assert not failed["ok"]
        assert "YouTube" in failed["error"]
        assert call("content_session.status")["result"]["state"] == "needs_user"
        assert call("activity.snapshot")["result"]["drain_paused"]
        assert call("content_session.close")["result"]["browser_running"] is False
    app.state.youtube.close()
