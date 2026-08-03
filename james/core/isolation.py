"""Small, cross-platform process broker for high-risk local operations.

The broker executes each operation in a fresh interpreter subprocess so risky
work never runs in the desktop process. Payloads are JSON-serializable values
and the child exposes only named operations; arbitrary callables or source code
cannot cross the boundary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - required to spawn isolated worker commands
import sys
import time
from pathlib import Path
from typing import Any


def _limit_child() -> None:
    """Apply conservative POSIX limits when available (Windows uses job lifetime)."""
    if os.name == "nt":
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        # RLIMIT_NPROC caps runaway forks but must stay above the host's current
        # per-user process count, otherwise the worker's own nested subprocess
        # fails with EAGAIN on busy runners (co-hosted CI jobs share one user).
        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
            target = min(max(soft, 4096), hard) if hard > 0 else max(soft, 4096)
            resource.setrlimit(resource.RLIMIT_NPROC, (target, hard))
        except (ValueError, OSError):
            pass
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except (ImportError, OSError, ValueError):
        pass


def _inside(root: str, raw: str, *, allow_root: bool = False) -> Path:
    base = Path(root).resolve()
    path = Path(raw).resolve(strict=False)
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Path escaped isolated workspace: {raw}") from exc
    if not allow_root and path == base:
        raise ValueError("Refusing to mutate the workspace root.")
    return path


def _execute(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    _limit_child()
    if operation == "command":
        args = payload["args"]
        proc = subprocess.run(  # nosec B603 - argv list, shell=False, policy-checked before dispatch
            args,
            shell=False,
            cwd=payload["workspace"],
            capture_output=True,
            text=True,
            timeout=int(payload.get("timeout", 60)),
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "output": ((proc.stdout or "") + (proc.stderr or ""))[:8000],
        }
    if operation == "trash":
        source = _inside(payload["workspace"], payload["path"])
        if not source.exists():
            return {"ok": False, "output": "Path does not exist."}
        trash = _inside(payload["workspace"], payload["trash"], allow_root=False)
        trash.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.time_ns()}-{source.name}"
        destination = trash / stamp
        shutil.move(str(source), str(destination))
        return {
            "ok": True,
            "output": f"Moved {source} to recoverable trash.",
            "data": {"original": str(source), "trashed": str(destination)},
        }
    if operation == "restore":
        source = _inside(payload["workspace"], payload["trashed"])
        destination = _inside(payload["workspace"], payload["original"])
        if not source.exists():
            return {"ok": False, "output": "Trashed item no longer exists."}
        if destination.exists():
            return {"ok": False, "output": f"Restore target already exists: {destination}"}
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return {"ok": True, "output": f"Restored {destination}."}
    if operation == "plugin":
        plugin_path = Path(payload["path"]).resolve(strict=True)
        if payload.get("trusted"):
            import importlib.util

            spec = importlib.util.spec_from_file_location(plugin_path.stem, str(plugin_path))
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load plugin: {plugin_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            from ..tools.forge_tools import load_generated_skill

            module = load_generated_skill(plugin_path)
        registered = next(
            (
                value
                for value in vars(module).values()
                if getattr(value, "name", None) == payload["name"]
            ),
            None,
        )
        if registered is None:
            raise ValueError(f"Plugin tool '{payload['name']}' was not found.")
        value = registered.run(**payload.get("arguments", {}))
        return {"ok": value.ok, "output": value.output, "data": value.data}
    if operation == "plugin_delete":
        plugin_root = Path(payload["plugin_root"]).resolve(strict=True)
        target = _inside(str(plugin_root), payload["path"])
        if target.suffix != ".py":
            raise ValueError("Only Python plugin files can be removed.")
        target.unlink()
        return {"ok": True, "output": f"Removed plugin {target.name}."}

    raise ValueError(f"Unknown isolated operation: {operation}")


def _worker_bootstrap() -> str:
    """Python source for the isolated child: import the broker ops, run one op.

    Kept as a string so the child has no access to the parent's process state;
    it only executes a named operation against a JSON payload.
    """
    return r"""
import json
import sys

from james.core.isolation import _execute, _limit_child


def main() -> int:
    request = json.loads(sys.argv[1])
    _limit_child()
    try:
        result = _execute(request["operation"], request["payload"])
    except BaseException as exc:
        result = {"ok": False, "output": "Isolated operation failed: {0}".format(exc)}
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def run_isolated(operation: str, payload: dict[str, Any], *, timeout: int = 120) -> dict[str, Any]:
    """Execute a fixed broker operation in a fresh interpreter with a hard timeout."""
    request = json.dumps({"operation": operation, "payload": payload})
    try:
        proc = subprocess.run(  # nosec B603 - argv list, shell=False, only named ops
            [sys.executable, "-c", _worker_bootstrap(), request],
            shell=False,
            capture_output=True,
            text=True,
            timeout=max(1, timeout),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "Isolated operation timed out and was terminated."}
    output = (proc.stdout or "").strip()
    if not output:
        return {
            "ok": False,
            "output": f"Isolated worker exited without a result (code {proc.returncode}).",
        }
    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "output": f"Isolated worker produced an invalid result (code {proc.returncode}).",
        }
    return result
