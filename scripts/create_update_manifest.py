"""Create and sign the small manifest consumed by the in-app updater."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MANIFEST_NAME = "Shazam-for-VRC-update.json"
SIGNATURE_NAME = f"{MANIFEST_NAME}.sig"
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from shazam_for_vrc.updates.service import UPDATE_SIGNING_PUBLIC_KEY  # noqa: E402


def create_signed_manifest(
    installer_path: Path,
    version: str,
    private_key_path: Path,
    output_directory: Path,
    expected_public_key: bytes | None = None,
) -> tuple[Path, Path]:
    """Write a deterministic manifest and its Ed25519 signature."""

    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("Version must use major.minor.patch format.")
    expected_name = f"Shazam-for-VRC-Setup-{version}.exe"
    if installer_path.name != expected_name or not installer_path.is_file():
        raise ValueError(f"Expected the built installer at {expected_name}.")
    try:
        private_key = serialization.load_pem_private_key(
            private_key_path.read_bytes(),
            password=None,
        )
    except (OSError, ValueError) as error:
        raise ValueError("The update signing key could not be loaded.") from error
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("The update signing key is not an Ed25519 private key.")
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if expected_public_key is not None and public_key != expected_public_key:
        raise ValueError(
            "The private update signing key does not match the public key compiled into the app."
        )

    sha256 = hashlib.sha256()
    size = 0
    with installer_path.open("rb") as installer:
        while chunk := installer.read(1024 * 1024):
            size += len(chunk)
            sha256.update(chunk)
    manifest = {
        "installer": {
            "name": installer_path.name,
            "sha256": sha256.hexdigest(),
            "size": size,
        },
        "schema": 1,
        "version": version,
    }
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")
    signature = base64.b64encode(private_key.sign(manifest_bytes)) + b"\n"
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = output_directory / MANIFEST_NAME
    signature_path = output_directory / SIGNATURE_NAME
    manifest_path.write_bytes(manifest_bytes)
    signature_path.write_bytes(signature)
    return manifest_path, signature_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args()
    manifest_path, signature_path = create_signed_manifest(
        arguments.installer,
        arguments.version,
        arguments.private_key,
        arguments.output_directory,
        UPDATE_SIGNING_PUBLIC_KEY,
    )
    print(f"Signed update manifest: {manifest_path}")
    print(f"Update signature: {signature_path}")


if __name__ == "__main__":
    main()
