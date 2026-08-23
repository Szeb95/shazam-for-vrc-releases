"""Build the VPM zip and legacy Unity package for the avatar button."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).parents[1]
PACKAGE_NAME = "com.szeb95.shazam-for-vrc-avatar"
PACKAGE_DIRECTORY = PROJECT_ROOT / "Packages" / PACKAGE_NAME
LEGACY_ASSET_ROOT = PurePosixPath("Assets/Shazam for VRC Avatar")
LEGACY_ROOT_GUID = "4ff6232a66e24f0e8f51bf5b7855b5f6"
GUID_PATTERN = re.compile(r"^guid:\s*([0-9a-f]{32})\s*$", re.MULTILINE)
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def _project_version() -> str:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject["project"]["version"])


def _package_manifest() -> dict[str, object]:
    value = json.loads((PACKAGE_DIRECTORY / "package.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Avatar package manifest must be a JSON object.")
    return value


def _asset_meta_path(asset_path: Path) -> Path:
    return asset_path.with_name(asset_path.name + ".meta")


def _guid_from_meta(meta_path: Path) -> str:
    match = GUID_PATTERN.search(meta_path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"Unity metadata has no valid GUID: {meta_path}")
    return match.group(1)


def validate_package() -> str:
    """Validate the portable package and return its release version."""

    manifest = _package_manifest()
    if manifest.get("name") != PACKAGE_NAME:
        raise ValueError(f"Avatar package name must be {PACKAGE_NAME}.")
    version = manifest.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Avatar package version must use major.minor.patch format.")
    if version != _project_version():
        raise ValueError("Avatar package and desktop app versions must match.")

    dependencies = manifest.get("vpmDependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("Avatar package must declare VPM dependencies.")
    for dependency in ("com.vrchat.avatars", "nadena.dev.modular-avatar"):
        if not isinstance(dependencies.get(dependency), str):
            raise ValueError(f"Avatar package is missing dependency {dependency}.")

    guids: dict[str, Path] = {}
    for asset_path in sorted(PACKAGE_DIRECTORY.rglob("*")):
        if asset_path.name.endswith(".meta"):
            continue
        meta_path = _asset_meta_path(asset_path)
        if not meta_path.is_file():
            raise ValueError(f"Unity asset is missing metadata: {asset_path}")
        guid = _guid_from_meta(meta_path)
        if guid in guids:
            raise ValueError(f"Duplicate Unity GUID in {meta_path} and {guids[guid]}.")
        guids[guid] = meta_path

    prefab = (
        PACKAGE_DIRECTORY / "Runtime" / "Prefabs" / "Shazam for VRC Avatar Button.prefab"
    ).read_text(encoding="utf-8")
    menu = (
        PACKAGE_DIRECTORY / "Runtime" / "Menus" / "Shazam for VRC Menu.asset"
    ).read_text(encoding="utf-8")
    required_prefab_values = (
        "nameOrPrefix: ShazamListen",
        "syncType: 3",
        "localOnly: 1",
        "saved: 0",
        "guid: 71a96d4ea0c344f39e277d82035bf9bd",
        "guid: 7ef83cb0c23d4d7c9d41021e544a1978",
    )
    if not all(value in prefab for value in required_prefab_values):
        raise ValueError("Avatar prefab no longer contains the expected Modular Avatar setup.")
    required_menu_values = (
        "name: Listen for song",
        "type: 101",
        "name: ShazamListen",
        "value: 1",
    )
    if not all(value in menu for value in required_menu_values):
        raise ValueError("Avatar menu no longer contains the expected Button control.")
    return version


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def _build_vpm_zip(output_path: Path) -> None:
    with zipfile.ZipFile(output_path, "w", compresslevel=9) as archive:
        for source in sorted(path for path in PACKAGE_DIRECTORY.rglob("*") if path.is_file()):
            relative = source.relative_to(PACKAGE_DIRECTORY).as_posix()
            archive.writestr(_zip_info(relative), source.read_bytes())


def _tar_bytes_entry(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info, data


def _add_unity_asset(
    archive: tarfile.TarFile,
    *,
    guid: str,
    pathname: PurePosixPath,
    meta_bytes: bytes,
    asset_bytes: bytes | None,
) -> None:
    entries: list[tuple[tarfile.TarInfo, bytes]] = []
    if asset_bytes is not None:
        entries.append(_tar_bytes_entry(f"{guid}/asset", asset_bytes))
    entries.append(_tar_bytes_entry(f"{guid}/asset.meta", meta_bytes))
    entries.append(_tar_bytes_entry(f"{guid}/pathname", str(pathname).encode("utf-8")))
    for info, data in entries:
        archive.addfile(info, io.BytesIO(data))


def _legacy_assets() -> list[Path]:
    assets = [
        PACKAGE_DIRECTORY / "README.md",
        PACKAGE_DIRECTORY / "CHANGELOG.md",
        PACKAGE_DIRECTORY / "LICENSE.md",
        PACKAGE_DIRECTORY / "Runtime",
    ]
    assets.extend(sorted((PACKAGE_DIRECTORY / "Runtime").rglob("*")))
    return [path for path in assets if not path.name.endswith(".meta")]


def _build_unity_package(output_path: Path) -> None:
    root_meta = (
        "fileFormatVersion: 2\n"
        f"guid: {LEGACY_ROOT_GUID}\n"
        "folderAsset: yes\n"
        "DefaultImporter:\n"
        "  externalObjects: {}\n"
        "  userData:\n"
        "  assetBundleName:\n"
        "  assetBundleVariant:\n"
    ).encode()
    with (
        output_path.open("wb") as raw_output,
        gzip.GzipFile(fileobj=raw_output, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        _add_unity_asset(
            archive,
            guid=LEGACY_ROOT_GUID,
            pathname=LEGACY_ASSET_ROOT,
            meta_bytes=root_meta,
            asset_bytes=None,
        )
        for source in _legacy_assets():
            relative = PurePosixPath(source.relative_to(PACKAGE_DIRECTORY).as_posix())
            meta_path = _asset_meta_path(source)
            _add_unity_asset(
                archive,
                guid=_guid_from_meta(meta_path),
                pathname=LEGACY_ASSET_ROOT / relative,
                meta_bytes=meta_path.read_bytes(),
                asset_bytes=None if source.is_dir() else source.read_bytes(),
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_avatar_package(output_directory: Path) -> tuple[Path, Path, Path, Path]:
    """Build release artifacts and return zip, unitypackage, manifest, and checksums."""

    version = validate_package()
    output_directory.mkdir(parents=True, exist_ok=True)
    vpm_zip = output_directory / f"{PACKAGE_NAME}-{version}.zip"
    unity_package = output_directory / f"Shazam-for-VRC-Avatar-{version}.unitypackage"
    release_manifest = output_directory / "package.json"
    checksums = output_directory / f"Shazam-for-VRC-Avatar-{version}-SHA256.txt"

    _build_vpm_zip(vpm_zip)
    _build_unity_package(unity_package)
    shutil.copyfile(PACKAGE_DIRECTORY / "package.json", release_manifest)
    checksums.write_text(
        f"{_sha256(vpm_zip)}  {vpm_zip.name}\n"
        f"{_sha256(unity_package)}  {unity_package.name}\n",
        encoding="utf-8",
    )
    return vpm_zip, unity_package, release_manifest, checksums


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=PROJECT_ROOT / "dist" / "avatar",
    )
    arguments = parser.parse_args()
    for artifact in build_avatar_package(arguments.output_directory):
        print(artifact)


if __name__ == "__main__":
    main()
