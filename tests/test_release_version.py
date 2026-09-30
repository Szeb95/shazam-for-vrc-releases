import re
import tomllib
from pathlib import Path

from shazam_for_vrc import __version__
from shazam_for_vrc.release_notes import RELEASE_NOTES


def test_all_release_version_sources_match() -> None:
    root = Path(__file__).parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    installer = (root / "packaging/windows/ShazamForVRC.iss").read_text(
        encoding="utf-8"
    )
    installer_match = re.search(r'#define MyAppVersion "([0-9]+\.[0-9]+\.[0-9]+)"', installer)

    assert installer_match is not None
    assert pyproject["project"]["version"] == __version__
    assert installer_match.group(1) == __version__
    assert RELEASE_NOTES[0].version == __version__
    assert RELEASE_NOTES[0].changes


def test_installer_uses_the_shared_application_icon() -> None:
    root = Path(__file__).parents[1]
    installer = (root / "packaging/windows/ShazamForVRC.iss").read_text(
        encoding="utf-8"
    )

    assert "SetupIconFile={#MyAppIcon}" in installer


def test_installer_accepts_its_existing_dedicated_install_directory() -> None:
    root = Path(__file__).parents[1]
    installer = (root / "packaging/windows/ShazamForVRC.iss").read_text(
        encoding="utf-8"
    )

    assert "DirExistsWarning=no" in installer


def test_installer_refreshes_windows_icons_after_installation() -> None:
    root = Path(__file__).parents[1]
    installer = (root / "packaging/windows/ShazamForVRC.iss").read_text(
        encoding="utf-8"
    )

    assert "SHCNE_ASSOCCHANGED" in installer
    assert "if CurStep = ssPostInstall" in installer
    assert "SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0)" in installer
