import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

from shazam_for_vrc import __version__


def _load_builder():
    script_path = Path(__file__).parents[1] / "scripts" / "build_avatar_package.py"
    spec = importlib.util.spec_from_file_location("build_avatar_package", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_avatar_package_manifest_and_prefab_are_consistent() -> None:
    module = _load_builder()
    package = module.PACKAGE_DIRECTORY
    manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
    prefab = (
        package / "Runtime/Prefabs/Shazam for VRC Avatar Button.prefab"
    ).read_text(encoding="utf-8")
    menu = (package / "Runtime/Menus/Shazam for VRC Menu.asset").read_text(
        encoding="utf-8"
    )

    assert module.validate_package() == __version__
    assert manifest["version"] == __version__
    assert "nadena.dev.modular-avatar" in manifest["vpmDependencies"]
    assert "nameOrPrefix: ShazamListen" in prefab
    assert "localOnly: 1" in prefab
    assert "saved: 0" in prefab
    assert "name: Listen for song" in menu
    assert "type: 101" in menu


def test_builds_reproducible_vpm_and_unity_packages(tmp_path: Path) -> None:
    module = _load_builder()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_artifacts = module.build_avatar_package(first)
    second_artifacts = module.build_avatar_package(second)

    assert [path.name for path in first_artifacts] == [path.name for path in second_artifacts]
    for left, right in zip(first_artifacts, second_artifacts, strict=True):
        assert hashlib.sha256(left.read_bytes()).digest() == hashlib.sha256(
            right.read_bytes()
        ).digest()

    vpm_zip, unity_package, release_manifest, checksums = first_artifacts
    with zipfile.ZipFile(vpm_zip) as archive:
        names = set(archive.namelist())
        assert "package.json" in names
        assert "Runtime/Prefabs/Shazam for VRC Avatar Button.prefab" in names
        assert "Runtime/Menus/Shazam for VRC Menu.asset" in names

    with (
        gzip.GzipFile(fileobj=io.BytesIO(unity_package.read_bytes())) as compressed,
        tarfile.open(fileobj=compressed, mode="r:") as archive,
    ):
        pathnames = {}
        for member in archive.getmembers():
            if not member.name.endswith("/pathname"):
                continue
            extracted = archive.extractfile(member)
            assert extracted is not None
            pathnames[member.name] = extracted.read().decode("utf-8")
    assert "Assets/Shazam for VRC Avatar" in pathnames.values()
    assert (
        "Assets/Shazam for VRC Avatar/Runtime/Prefabs/"
        "Shazam for VRC Avatar Button.prefab"
    ) in pathnames.values()
    assert json.loads(release_manifest.read_text(encoding="utf-8"))["version"] == __version__
    assert vpm_zip.name in checksums.read_text(encoding="utf-8")
    assert unity_package.name in checksums.read_text(encoding="utf-8")
