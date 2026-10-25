"""Real app + built GUI, with disposable state and no real credentials/connectors."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="edison-reader-e2e-") as directory:
        root = Path(directory)
        os.environ["COWORKER_STATE_DIR"] = str(root / "state")
        os.environ["COWORKER_SCRATCH_BASE"] = str(root / "scratch")
        os.environ["COWORKER_API_TOKEN"] = "isolated-reader-e2e-token"
        # This process must not inherit production browser login configuration.
        os.environ.pop("EDISON_WEB_LOGIN", None)
        from coworker.server import SessionManager, create_app
        from fastapi.staticfiles import StaticFiles
        import uvicorn

        app = create_app(SessionManager(workspace=root, data_dir=root / "data"))
        # Static files are normally served by Caddy. Leave the app's API middleware
        # untouched and serve the production GUI from a separate unprotected mount.
        from fastapi import FastAPI
        outer = FastAPI()
        outer.mount("/api", app)
        outer.mount("/", StaticFiles(directory=ROOT / "surfaces/gui/dist", html=True))
        uvicorn.run(outer, host="127.0.0.1", port=8791, log_level="warning")
