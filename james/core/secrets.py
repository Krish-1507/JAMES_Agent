"""Persistent secret-key helpers.

Secrets are generated with the operating system CSPRNG and stored with
owner-only permissions where the platform supports them. Environment variables
always take precedence so managed deployments can provide their own keys.
"""
from __future__ import annotations

import base64
import os
import secrets
from contextlib import suppress
from pathlib import Path


def load_or_create_secret(env_name: str, path: Path, *, length: int = 32) -> bytes:
    configured = os.environ.get(env_name)
    if configured:
        return configured.encode("utf-8")

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        encoded = path.read_text(encoding="ascii").strip()
        secret = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if len(secret) >= length:
            return secret
    except FileNotFoundError:
        pass
    except Exception as exc:
        raise RuntimeError(f"Could not read secret key at {path}: {exc}") from exc

    secret = secrets.token_bytes(length)
    encoded = base64.urlsafe_b64encode(secret)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        # Another process won the creation race.
        return load_or_create_secret(env_name, path, length=length)
    except Exception as exc:
        raise RuntimeError(
            f"Could not create secret key at {path}. Set {env_name} explicitly: {exc}"
        ) from exc

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.write(b"\n")
        with suppress(OSError):
            os.chmod(path, 0o600)
    except Exception:
        with suppress(OSError):
            path.unlink()
        raise
    return secret
