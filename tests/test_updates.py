import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shazam_for_vrc.updates.service import (
    LATEST_RELEASE_API,
    MANIFEST_ASSET_NAME,
    SIGNATURE_ASSET_NAME,
    UpdateClient,
    UpdateSecurityError,
    _validate_trusted_asset_url,
)


def _signed_release(*, version: str = "1.1.0", tamper: bool = False):
    key = Ed25519PrivateKey.generate()
    public_key = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    installer_name = f"Shazam-for-VRC-Setup-{version}.exe"
    installer = b"signed installer bytes"
    manifest = json.dumps(
        {
            "schema": 1,
            "version": version,
            "installer": {
                "name": installer_name,
                "sha256": hashlib.sha256(installer).hexdigest(),
                "size": len(installer),
            },
        },
        indent=2,
        sort_keys=True,
    ).encode() + b"\n"
    signature = base64.b64encode(key.sign(manifest)) + b"\n"
    if tamper:
        manifest = manifest.replace(version.encode(), b"9.9.9", 1)
    base = f"https://github.com/Szeb95/shazam-for-vrc-releases/releases/download/v{version}"
    manifest_url = f"{base}/{MANIFEST_ASSET_NAME}"
    signature_url = f"{base}/{SIGNATURE_ASSET_NAME}"
    installer_url = f"{base}/{installer_name}"
    release = {
        "tag_name": f"v{version}",
        "html_url": (
            "https://github.com/Szeb95/shazam-for-vrc-releases/releases/tag/"
            f"v{version}"
        ),
        "body": "Release notes",
        "immutable": True,
        "assets": [
            {"name": MANIFEST_ASSET_NAME, "browser_download_url": manifest_url},
            {"name": SIGNATURE_ASSET_NAME, "browser_download_url": signature_url},
            {
                "name": installer_name,
                "browser_download_url": installer_url,
                "size": len(installer),
                "digest": f"sha256:{hashlib.sha256(installer).hexdigest()}",
            },
        ],
    }
    responses = {
        LATEST_RELEASE_API: json.dumps(release).encode(),
        manifest_url: manifest,
        signature_url: signature,
    }
    return public_key, responses


def test_accepts_newer_signed_immutable_release() -> None:
    public_key, responses = _signed_release()
    client = UpdateClient(
        public_key=public_key,
        read_url=lambda url, _limit: responses[url],
    )

    result = client.check("1.0.3")

    assert result.update_available
    assert result.update is not None
    assert result.update.version == "1.1.0"
    assert result.update.immutable


def test_current_signed_release_is_not_offered_again() -> None:
    public_key, responses = _signed_release()
    client = UpdateClient(
        public_key=public_key,
        read_url=lambda url, _limit: responses[url],
    )

    result = client.check("1.1.0")

    assert not result.update_available
    assert result.latest_version == "1.1.0"


def test_rejects_tampered_manifest() -> None:
    public_key, responses = _signed_release(tamper=True)
    client = UpdateClient(
        public_key=public_key,
        read_url=lambda url, _limit: responses[url],
    )

    with pytest.raises(UpdateSecurityError, match="signature"):
        client.check("1.0.3")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/Szeb95/shazam-for-vrc-releases/releases/download/v1/a.exe",
        "https://example.com/Szeb95/shazam-for-vrc-releases/releases/download/v1/a.exe",
        "https://github.com/another/repo/releases/download/v1/a.exe",
        "https://user@github.com/Szeb95/shazam-for-vrc-releases/releases/download/v1/a.exe",
        "https://github.com/OldOwner/shazam-for-vrc-releases/releases/download/v1/a.exe",
    ],
)
def test_rejects_untrusted_asset_urls(url: str) -> None:
    with pytest.raises(UpdateSecurityError):
        _validate_trusted_asset_url(url)
