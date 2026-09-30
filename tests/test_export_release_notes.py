import importlib.util
from pathlib import Path


def test_exports_current_in_app_notes(tmp_path: Path) -> None:
    script_path = Path(__file__).parents[1] / "scripts" / "export_release_notes.py"
    spec = importlib.util.spec_from_file_location("export_release_notes", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output = module.export_release_notes("1.3.0", tmp_path)

    text = output.read_text(encoding="utf-8")
    assert "# Shazam for VRC 1.3.0" in text
    assert "Hide in system tray" in text
