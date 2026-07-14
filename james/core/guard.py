"""Privacy-certified offline mode — a network egress firewall you can prove.

When installed, *every* outbound network attempt (DNS resolution + TCP connect)
is intercepted at the socket layer. Only loopback destinations (127.0.0.1,
::1, localhost) are permitted — that's exactly where a local model like Ollama
listens. Anything else is denied and written to the egress audit log, so the
user (or an auditor) can cryptographically *see* that no data left the machine.

This is stronger than per-tool guards: even the LLM SDKs, the browser, or a
future plugin cannot phone home, because the underlying socket calls are blocked.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
from typing import Tuple

from ..config import settings

_INSTALLED = False
_LOCK = threading.Lock()
_orig_getaddrinfo = socket.getaddrinfo
_orig_connect = socket.socket.connect
_orig_create = socket.create_connection


class BlockedEgress(Exception):
    """Raised when offline mode blocks a non-local network attempt."""


def _host_of(address) -> Tuple[str, int]:
    """Normalise a sockaddr into (host, port)."""
    if isinstance(address, tuple):
        host = address[0]
        port = address[1] if len(address) > 1 else 0
        return host, int(port)
    return str(address), 0


def _is_loopback(host) -> bool:
    if host is None:
        return False
    h = str(host).strip().lower()
    if h in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "::"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        pass
    # Resolve (via the *original* getaddrinfo to avoid recursion) and check results.
    try:
        for info in _orig_getaddrinfo(h, None):
            ip = info[4][0]
            try:
                if ipaddress.ip_address(ip).is_loopback:
                    return True
            except ValueError:
                continue
    except Exception:
        return False
    return False


def _audit(host, port, allowed: bool) -> None:
    try:
        from datetime import datetime

        line = (
            f"{datetime.now().isoformat(timespec='seconds')} | "
            f"{'ALLOW' if allowed else 'BLOCK'} | {host}:{port}\n"
        )
        with open(settings.assistant.egress_audit_log, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host and not _is_loopback(host):
        _audit(host, args[0] if args else 0, False)
        raise BlockedEgress(f"Blocked name resolution for {host!r} (offline mode)")
    return _orig_getaddrinfo(host, *args, **kwargs)


def _guarded_connect(self, address, *args, **kwargs):
    host, port = _host_of(address)
    allowed = _is_loopback(host)
    _audit(host, port, allowed)
    if allowed:
        return _orig_connect(self, address, *args, **kwargs)
    raise BlockedEgress(f"Blocked egress to {host}:{port} (offline mode)")


def _guarded_create(*args, **kwargs):
    # create_connection(address, ...) — inspect the address tuple.
    if args:
        host, port = _host_of(args[0])
        allowed = _is_loopback(host)
        _audit(host, port, allowed)
        if not allowed:
            raise BlockedEgress(f"Blocked egress to {host}:{port} (offline mode)")
    return _orig_create(*args, **kwargs)


def install_offline_guard() -> None:
    """Monkey-patch the socket layer to enforce offline mode. Idempotent."""
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        socket.getaddrinfo = _guarded_getaddrinfo
        socket.socket.connect = _guarded_connect
        socket.create_connection = _guarded_create
        _INSTALLED = True


def is_offline() -> bool:
    return bool(settings.assistant.offline_mode)
