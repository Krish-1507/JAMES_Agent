"""Small, cross-platform process broker for high-risk local operations.

The broker uses Python's ``spawn`` context so risky work never executes in the
desktop process. Payloads are JSON-like values and the child exposes only named
operations; arbitrary callables or source code cannot cross the boundary.
"""
from __future__ import annotations

import multiprocessing
import os
import shutil
import subprocess
import time
from pathlib import Path
from queue import Empty
from typing import Any


def _limit_child() -> None:
    """Apply conservative POSIX limits when available (Windows uses job lifetime)."""
    if os.name == "nt":
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        # RLIMIT_NPROC is per user and counts threads already owned by the host.
        # Keep a finite ceiling without dropping below normal CI/desktop usage.
        resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
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
        proc = subprocess.run(
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


def _worker(operation: str, payload: dict[str, Any], output) -> None:
    try:
        output.put(_execute(operation, payload))
    except BaseException as exc:
        output.put({"ok": False, "output": f"Isolated operation failed: {exc}"})


def run_isolated(operation: str, payload: dict[str, Any], *, timeout: int = 120) -> dict[str, Any]:
    """Execute a fixed broker operation in a spawned process with a hard timeout."""
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(target=_worker, args=(operation, payload, output), daemon=True)
    process.start()
    process.join(max(1, timeout))
    if process.is_alive():
        process.terminate()
        process.join(5)
        return {"ok": False, "output": "Isolated operation timed out and was terminated."}
    try:
        return output.get_nowait()
    except Empty:
        return {
            "ok": False,
            "output": f"Isolated worker exited without a result (code {process.exitcode}).",
        }
