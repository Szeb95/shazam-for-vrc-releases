"""Secure updater for the public Shazam for VRC release repository."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

REPOSITORY_OWNER = "Szeb95"
REPOSITORY_NAME = "shazam-for-vrc-releases"
REPOSITORY_URL = f"https://github.com/{REPOSITORY_OWNER}/{REPOSITORY_NAME}"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{REPOSITORY_OWNER}/{REPOSITORY_NAME}/releases/latest"
)
MANIFEST_ASSET_NAME = "Shazam-for-VRC-update.json"
SIGNATURE_ASSET_NAME = f"{MANIFEST_ASSET_NAME}.sig"
UPDATE_SIGNING_PUBLIC_KEY = base64.b64decode(
    "E1dZh3G2nSzTYcisXW76P3HjLc96UPtsQowGyJMbUtE="
)
MAX_API_BYTES = 1_000_000
MAX_MANIFEST_BYTES = 64_000
MAX_SIGNATURE_BYTES = 8_000
MAX_INSTALLER_BYTES = 500_000_000
SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)
INSTALLER_PATTERN = re.compile(r"^Shazam-for-VRC-Setup-(\d+\.\d+\.\d+)\.exe$")

ProgressCallback = Callable[[int, int], None]
ReadUrl = Callable[[str, int], bytes]


class UpdateError(RuntimeError):
    """Raised when an update check or download cannot complete safely."""


class UpdateSecurityError(UpdateError):
    """Raised when release data fails signature or integrity validation."""


class _NoPublishedRelease(UpdateError):
    """Internal signal for an empty GitHub Releases page."""


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    """One newer, signed release that is safe to offer to the user."""

    version: str
    release_page: str
    notes: str
    installer_name: str
    installer_url: str
    sha256: str
    size: int
    immutable: bool


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    """Result of comparing the installed version with the latest release."""

    current_version: str
    latest_version: str | None
    update: UpdateInfo | None

    @property
    def update_available(self) -> bool:
        return self.update is not None


@dataclass(frozen=True, slots=True)
class DownloadedUpdate:
    """A fully verified installer ready to be launched."""

    version: str
    installer_path: Path


class UpdateClient:
    """Check and download signed releases without storing GitHub credentials."""

    def __init__(
        self,
        *,
        public_key: bytes = UPDATE_SIGNING_PUBLIC_KEY,
        read_url: ReadUrl | None = None,
    ) -> None:
        if len(public_key) != 32:
            raise ValueError("Ed25519 public key must contain 32 bytes")
        self._public_key = Ed25519PublicKey.from_public_bytes(public_key)
        self._read_url = read_url or _read_url

    def check(self, current_version: str) -> UpdateCheckResult:
        """Return the latest signed release when it is newer than the app."""

        current = _parse_version(current_version)
        try:
            release_bytes = self._read_url(LATEST_RELEASE_API, MAX_API_BYTES)
        except _NoPublishedRelease:
            return UpdateCheckResult(current_version, None, None)
        release = _json_object(release_bytes, "GitHub release response")
        update = self._validate_release(release)
        if _parse_version(update.version) <= current:
            return UpdateCheckResult(current_version, update.version, None)
        return UpdateCheckResult(current_version, update.version, update)

    def download(
        self,
        update: UpdateInfo,
        progress: ProgressCallback | None = None,
    ) -> DownloadedUpdate:
        """Download an installer to a temporary folder and verify its signed hash."""

        _validate_trusted_asset_url(update.installer_url)
        if update.size < 1 or update.size > MAX_INSTALLER_BYTES:
            raise UpdateSecurityError("The signed installer size is outside the safe limit.")
        target_directory = Path(tempfile.gettempdir()) / "Shazam for VRC Updates"
        target_directory.mkdir(parents=True, exist_ok=True)
        target = target_directory / update.installer_name
        partial = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()
        downloaded = 0
        request = _request(update.installer_url)
        try:
            with (
                urllib.request.urlopen(request, timeout=60) as response,
                partial.open("wb") as file,
            ):
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > update.size or downloaded > MAX_INSTALLER_BYTES:
                        raise UpdateSecurityError("The installer exceeded its signed size.")
                    digest.update(chunk)
                    file.write(chunk)
                    if progress:
                        progress(downloaded, update.size)
            if downloaded != update.size:
                raise UpdateSecurityError("The installer size did not match its signature.")
            if digest.hexdigest() != update.sha256:
                raise UpdateSecurityError("The installer checksum did not match its signature.")
            partial.replace(target)
        except (OSError, urllib.error.URLError) as error:
            partial.unlink(missing_ok=True)
            raise UpdateError("The update installer could not be downloaded.") from error
        except UpdateError:
            partial.unlink(missing_ok=True)
            raise
        return DownloadedUpdate(version=update.version, installer_path=target)

    @staticmethod
    def launch_installer(downloaded: DownloadedUpdate) -> None:
        """Launch a verified Windows installer without using a command shell."""

        installer = downloaded.installer_path
        if installer.suffix.casefold() != ".exe" or not installer.is_file():
            raise UpdateError("The verified update installer is no longer available.")
        try:
            subprocess.Popen(  # noqa: S603 - exact verified executable, never a shell
                [str(installer)],
                cwd=str(installer.parent),
                close_fds=True,
            )
        except OSError as error:
            raise UpdateError("Windows could not start the update installer.") from error

    def _validate_release(self, release: dict[str, Any]) -> UpdateInfo:
        tag_version = _release_version(release.get("tag_name"))
        assets = _release_assets(release.get("assets"))
        manifest_asset = _required_asset(assets, MANIFEST_ASSET_NAME)
        signature_asset = _required_asset(assets, SIGNATURE_ASSET_NAME)
        manifest_url = _asset_url(manifest_asset)
        signature_url = _asset_url(signature_asset)
        manifest_bytes = self._read_url(manifest_url, MAX_MANIFEST_BYTES)
        signature_bytes = self._read_url(signature_url, MAX_SIGNATURE_BYTES)
        _verify_signature(self._public_key, manifest_bytes, signature_bytes)

        manifest = _json_object(manifest_bytes, "signed update manifest")
        if manifest.get("schema") != 1:
            raise UpdateSecurityError("The signed update manifest schema is unsupported.")
        version = manifest.get("version")
        if not isinstance(version, str) or _parse_version(version) != _parse_version(tag_version):
            raise UpdateSecurityError("The signed version does not match the GitHub release tag.")
        installer = manifest.get("installer")
        if not isinstance(installer, dict):
            raise UpdateSecurityError("The signed installer information is missing.")
        installer_name = installer.get("name")
        sha256 = installer.get("sha256")
        size = installer.get("size")
        if (
            not isinstance(installer_name, str)
            or not INSTALLER_PATTERN.fullmatch(installer_name)
            or INSTALLER_PATTERN.fullmatch(installer_name).group(1) != version  # type: ignore[union-attr]
        ):
            raise UpdateSecurityError("The signed installer filename is invalid.")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise UpdateSecurityError("The signed installer checksum is invalid.")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not 1 <= size <= MAX_INSTALLER_BYTES
        ):
            raise UpdateSecurityError("The signed installer size is invalid.")
        installer_asset = _required_asset(assets, installer_name)
        if installer_asset.get("size") != size:
            raise UpdateSecurityError("GitHub's installer size does not match the signature.")
        github_digest = installer_asset.get("digest")
        if github_digest is not None and github_digest != f"sha256:{sha256}":
            raise UpdateSecurityError("GitHub's installer digest does not match the signature.")
        release_page = release.get("html_url")
        if not isinstance(release_page, str) or not _trusted_release_page(release_page):
            raise UpdateSecurityError("The GitHub release page URL is invalid.")
        notes = release.get("body")
        if not isinstance(notes, str):
            notes = ""
        return UpdateInfo(
            version=version,
            release_page=release_page,
            notes=notes[:4_000],
            installer_name=installer_name,
            installer_url=_asset_url(installer_asset),
            sha256=sha256,
            size=size,
            immutable=release.get("immutable") is True,
        )


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Shazam-for-VRC/1.1 (+{REPOSITORY_URL})",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _read_url(url: str, limit: int) -> bytes:
    if url != LATEST_RELEASE_API:
        _validate_trusted_asset_url(url)
    try:
        with urllib.request.urlopen(_request(url), timeout=15) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > limit:
                raise UpdateSecurityError("The update response exceeded its safe size limit.")
            data = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if url == LATEST_RELEASE_API and error.code == 404:
            raise _NoPublishedRelease("No GitHub release has been published yet.") from error
        raise UpdateError("GitHub could not provide update information.") from error
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise UpdateError("The update service could not be reached.") from error
    if len(data) > limit:
        raise UpdateSecurityError("The update response exceeded its safe size limit.")
    return data


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateSecurityError(f"The {label} is not valid JSON.") from error
    if not isinstance(value, dict):
        raise UpdateSecurityError(f"The {label} must be a JSON object.")
    return value


def _verify_signature(
    public_key: Ed25519PublicKey,
    manifest: bytes,
    signature_text: bytes,
) -> None:
    try:
        signature = base64.b64decode(signature_text.strip(), validate=True)
        public_key.verify(signature, manifest)
    except (binascii.Error, InvalidSignature, ValueError) as error:
        raise UpdateSecurityError("The update release signature is invalid.") from error


def _parse_version(version: str) -> tuple[int, int, int]:
    match = SEMVER_PATTERN.fullmatch(version)
    if not match:
        raise UpdateSecurityError("The update version is not valid.")
    return tuple(int(match.group(name)) for name in ("major", "minor", "patch"))


def _release_version(value: Any) -> str:
    if not isinstance(value, str):
        raise UpdateSecurityError("The GitHub release tag is missing.")
    version = value[1:] if value.startswith("v") else value
    _parse_version(version)
    return version


def _release_assets(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise UpdateSecurityError("The GitHub release assets are missing.")
    assets: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise UpdateSecurityError("The GitHub release contains an invalid asset.")
        name = item["name"]
        if name in assets:
            raise UpdateSecurityError("The GitHub release contains duplicate asset names.")
        assets[name] = item
    return assets


def _required_asset(
    assets: dict[str, dict[str, Any]],
    name: str,
) -> dict[str, Any]:
    try:
        return assets[name]
    except KeyError as error:
        raise UpdateSecurityError(f"The signed release is missing {name}.") from error


def _asset_url(asset: dict[str, Any]) -> str:
    url = asset.get("browser_download_url")
    if not isinstance(url, str):
        raise UpdateSecurityError("A GitHub release asset has no download URL.")
    _validate_trusted_asset_url(url)
    return url


def _validate_trusted_asset_url(url: str) -> None:
    parsed = urlparse(url)
    expected_prefix = f"/{REPOSITORY_OWNER}/{REPOSITORY_NAME}/releases/download/"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or not parsed.path.startswith(expected_prefix)
        or parsed.query
        or parsed.fragment
    ):
        raise UpdateSecurityError("An update asset URL is outside the trusted repository.")


def _trusted_release_page(url: str) -> bool:
    parsed = urlparse(url)
    expected_prefix = f"/{REPOSITORY_OWNER}/{REPOSITORY_NAME}/releases/tag/"
    return (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and parsed.path.startswith(expected_prefix)
        and not parsed.query
        and not parsed.fragment
    )
