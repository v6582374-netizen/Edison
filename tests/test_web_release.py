import importlib.util
from pathlib import Path


def test_open_reader_can_still_lazy_load_previous_assets_after_promotion(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/promote_web_release.py"
    spec = importlib.util.spec_from_file_location("promote_web_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old, new = tmp_path / "old", tmp_path / "new"
    for folder in (old, new):
        (folder / "assets").mkdir(parents=True)
        (folder / "index.html").write_text(folder.name)
    (old / "assets/reader-old.js").write_text("old lazy reader")
    (new / "assets/reader-new.js").write_text("new lazy reader")
    live = tmp_path / "web"
    live.symlink_to(old)
    assert module.promote(new, live) == 1
    assert (live / "index.html").read_text() == "new"
    assert (live / "assets/reader-old.js").read_text() == "old lazy reader"
    assert (live / "assets/reader-new.js").read_text() == "new lazy reader"
    assert module.promote(new, live) == 0
