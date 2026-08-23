import base64
import hashlib
import importlib.util
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _load_manifest_script():
    script_path = Path(__file__).parents[1] / "scripts" / "create_update_manifest.py"
    spec = importlib.util.spec_from_file_location("create_update_manifest", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_creates_verifiable_manifest_for_exact_installer(tmp_path: Path) -> None:
    module = _load_manifest_script()
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "private.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    installer = tmp_path / "Shazam-for-VRC-Setup-1.1.0.exe"
    installer.write_bytes(b"installer")

    manifest_path, signature_path = module.create_signed_manifest(
        installer,
        "1.1.0",
        key_path,
        tmp_path,
    )

    manifest_bytes = manifest_path.read_bytes()
    key.public_key().verify(base64.b64decode(signature_path.read_bytes()), manifest_bytes)
    manifest = json.loads(manifest_bytes)
    assert manifest["version"] == "1.1.0"
    assert manifest["installer"]["sha256"] == hashlib.sha256(b"installer").hexdigest()
