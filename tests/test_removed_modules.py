"""The shipped server must not mount or initialize the paused object module."""

from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app


def test_object_module_is_absent_from_server(tmp_path, monkeypatch):
    library = tmp_path / "objects"
    monkeypatch.setenv("EDISON_MINIMALISM_WORKSPACE", str(library))
    app = create_app(SessionManager(workspace=tmp_path / "workspace"))
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 200
        assert client.post(
            "/v1/minimalism/capability", json={"capability": "library.snapshot"}
        ).status_code == 404
        assert client.get("/v1/minimalism/backup").status_code == 404
        assert client.post("/v1/minimalism/restore", content=b"archive").status_code == 404
    assert not library.exists()
    assert not hasattr(app.state, "minimalism")
