"""Ed25519 signing and verification for JAMES plugin bundles."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .manifest import MANIFEST_PREFIX, parse_manifest

_SIGNATURE_FIELDS = {"content-sha256", "signature"}


def canonical_plugin_bytes(source: str) -> bytes:
    """Return stable signed bytes, excluding self-referential signature fields."""
    lines = []
    for line in source.replace("\r\n", "\n").splitlines():
        if line.startswith(MANIFEST_PREFIX):
            key = line[len(MANIFEST_PREFIX) :].partition(":")[0].strip()
            if key in _SIGNATURE_FIELDS:
                continue
        lines.append(line.rstrip())
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def plugin_digest(source: str) -> str:
    return hashlib.sha256(canonical_plugin_bytes(source)).hexdigest()


def verify_plugin_signature(
    source: str,
    trusted_keys: dict[str, str | bytes | Path],
) -> tuple[bool, str]:
    """Verify the manifest digest and Ed25519 signature against trusted keys."""
    manifest = parse_manifest(source)
    if manifest is None:
        return False, "Plugin manifest is missing."
    if not manifest.signing_key_id or not manifest.signature or not manifest.content_sha256:
        return False, "Plugin is unsigned."
    key_data = trusted_keys.get(manifest.signing_key_id)
    if key_data is None:
        return False, f"Signing key '{manifest.signing_key_id}' is not trusted."
    digest = plugin_digest(source)
    if digest != manifest.content_sha256:
        return False, "Plugin content digest does not match its manifest."
    try:
        if isinstance(key_data, Path):
            raw = key_data.read_bytes()
        elif isinstance(key_data, str):
            raw = key_data.encode("utf-8")
        else:
            raw = key_data
        public_key = serialization.load_pem_public_key(raw)
        if not isinstance(public_key, Ed25519PublicKey):
            return False, "Trusted key is not Ed25519."
        public_key.verify(base64.b64decode(manifest.signature), canonical_plugin_bytes(source))
    except (InvalidSignature, ValueError, TypeError) as exc:
        return False, f"Plugin signature is invalid: {exc}"
    return True, "Plugin signature verified."


def sign_plugin_source(source: str, private_key_pem: bytes, key_id: str) -> str:
    """Return source with refreshed digest/signature manifest fields."""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("Plugin signing key must be Ed25519.")
    cleaned = []
    for line in source.replace("\r\n", "\n").splitlines():
        if line.startswith(MANIFEST_PREFIX):
            key = line[len(MANIFEST_PREFIX) :].partition(":")[0].strip()
            if key in _SIGNATURE_FIELDS | {"signing-key-id"}:
                continue
        cleaned.append(line)
    insert_at = next(
        (index for index, line in enumerate(cleaned) if not line.startswith("#")),
        len(cleaned),
    )
    unsigned = list(cleaned)
    unsigned[insert_at:insert_at] = [f"{MANIFEST_PREFIX}signing-key-id: {key_id}"]
    unsigned_source = "\n".join(unsigned).rstrip() + "\n"
    digest = plugin_digest(unsigned_source)
    signature = base64.b64encode(private_key.sign(canonical_plugin_bytes(unsigned_source))).decode(
        "ascii"
    )
    unsigned[insert_at + 1 : insert_at + 1] = [
        f"{MANIFEST_PREFIX}content-sha256: {digest}",
        f"{MANIFEST_PREFIX}signature: {signature}",
    ]
    return "\n".join(unsigned).rstrip() + "\n"
