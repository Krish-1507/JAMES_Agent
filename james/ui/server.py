"""FastAPI sidecar powering the Phase-3 web UI (``james serve`` / embedded shell).

One :class:`Assistant` runs in a background thread; every event it emits
(``user``/``thinking``/``reply``/``tool_start``/``tool``/``voice``/...) is
broadcast to browser clients over Server-Sent Events, and JSON endpoints cover
turns, approvals, sessions, model switching, settings, voice controls and the
onboarding wizard.

Run standalone with ``python -m james --serve``, embedded by the Qt shell, or
instantiated directly for tests.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import queue
import threading
import time
import webbrowser
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

log = logging.getLogger("james")

_APPROVAL_TIMEOUT = 300.0  # seconds a request can stay open
_NO_CLIENT_GRACE = 5.0  # auto-deny after this long with zero connected clients


# ---------------------------------------------------------------------------
# Event bus: thread-safe broadcast to SSE clients
# ---------------------------------------------------------------------------


class EventBus:
    """Ordered, bounded event history with per-client watermarks."""

    def __init__(self, maxlen: int = 5000) -> None:
        self._events: deque[dict] = deque(maxlen=maxlen)
        self._counter = 0
        self._lock = threading.Lock()
        self._clients = 0

    def publish(self, payload: dict) -> int:
        with self._lock:
            self._counter += 1
            self._events.append({"id": self._counter, "payload": payload})
            return self._counter

    def connect(self) -> int:
        with self._lock:
            self._clients += 1
            return self._counter

    def disconnect(self) -> None:
        with self._lock:
            self._clients = max(0, self._clients - 1)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return self._clients

    def drain(self, after: int) -> tuple[list[dict], int]:
        with self._lock:
            return [e for e in self._events if e["id"] > after], self._counter


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


class _PendingApproval:
    def __init__(self, req_id: str, name: str, redacted: dict) -> None:
        self.req_id = req_id
        self.name = name
        self.redacted = redacted
        self._done = threading.Event()
        self.allowed = False

    @property
    def resolved(self) -> bool:
        return self._done.is_set()

    def respond(self, allowed: bool) -> None:
        self.allowed = bool(allowed)
        self._done.set()

    def wait(self, timeout: float) -> bool:
        self._done.wait(timeout=timeout)
        return self.allowed


class ApprovalRegistry:
    """Pending dangerous-action requests, resolvable by id."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = threading.Lock()
        self._seq = 0

    def request(self, name: str, redacted: dict) -> _PendingApproval:
        with self._lock:
            self._seq += 1
            req = _PendingApproval(f"req-{self._seq}", name, redacted)
            self._pending[req.req_id] = req
        self._bus.publish(
            {"type": "approval_requested", "id": req.req_id, "name": name, "args": redacted}
        )
        return req

    def respond(self, req_id: str, allowed: bool) -> bool:
        with self._lock:
            req = self._pending.pop(req_id, None)
        if req is None:
            return False
        req.respond(allowed)
        self._bus.publish(
            {"type": "approval_resolved", "id": req_id, "allowed": allowed, "name": req.name}
        )
        return True

    def resolve(self, req: _PendingApproval) -> None:
        with self._lock:
            self._pending.pop(req.req_id, None)


# ---------------------------------------------------------------------------
# Runtime: owns the assistant thread + wiring
# ---------------------------------------------------------------------------

_SECRET_KEYS = ("api_key", "token", "secret", "password", "key", "auth")


def _redact_args(name: str, arguments: dict | None) -> dict:
    """Replace secret-looking argument values before sending them to the UI."""
    args = dict(arguments or {})
    for key in list(args):
        value = args[key]
        secret_key = any(part in key.lower() for part in _SECRET_KEYS)
        oversized = isinstance(value, str) and len(value) > 200
        if secret_key or oversized:
            args[key] = "***"
    return args


class _ConsoleSink(io.StringIO):
    """Routes stray CLI prints into the log so the server stays quiet."""

    def __init__(self, target: logging.Logger) -> None:
        super().__init__()
        self._target = target

    def write(self, s: str) -> int:
        s = s.strip()
        if s:
            self._target.debug("%s", s)
        return len(s)


class ServerRuntime:
    """The running UI server: assistant thread + control surface."""

    def __init__(
        self,
        *,
        assistant_factory: Callable[[], Any] | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.bus = bus or EventBus()
        self.approvals = ApprovalRegistry(self.bus)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._assistant = None
        self._assistant_factory = assistant_factory
        self._voice_state = "idle"
        self._voice_level = 0.0
        self._muted = False
        self._stdout = None
        self._stderr = None
        self._lock = threading.Lock()

    # ---- lifecycle --------------------------------------------------------

    @property
    def assistant(self) -> Any:
        return self._assistant

    def start(self) -> None:
        """Build the assistant, wire it, and start the turn loop thread."""
        if self._thread is not None:
            return
        if self._assistant_factory is not None:
            assistant = self._assistant_factory()
        else:
            from ..core.assistant import Assistant

            assistant = Assistant(session=None, confirm=self._confirm)
        self._assistant = assistant
        assistant.on_event = self._on_event
        if getattr(assistant, "_text_queue", None) is None:
            assistant._text_queue = queue.Queue()
        with suppress(Exception):
            assistant.set_confirmation_handler(self._confirm)
        self._thread = threading.Thread(target=self._run_loop, name="james-server-assistant", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        assistant = self._assistant
        old_out, old_err = sys_stdout_stderr()
        self._stdout, self._stderr = _ConsoleSink(log), _ConsoleSink(log)
        sys_set_stdout_stderr(self._stdout, self._stderr)
        try:
            from ..config import settings

            voice_on = settings.voice.enabled and settings.voice.stt_provider != "none"
            if voice_on:
                # Duplex routes typed text itself; turn-based voice loops drain
                # the same queue via Assistant._text_drain_loop.
                assistant.voice_loop()
            else:
                self._text_loop()
        except Exception:
            log.exception("Assistant loop ended unexpectedly")
        finally:
            sys_set_stdout_stderr(old_out, old_err)

    def _text_loop(self) -> None:
        assistant = self._assistant
        while not self._stop.is_set():
            try:
                text = assistant._text_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            text = (text or "").strip()
            if not text:
                continue
            if not assistant._handle_session_command(text):
                assistant.handle_turn(text)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    # ---- assistant event plumbing ------------------------------------------

    def _on_event(self, event: dict) -> None:
        event = dict(event or {})
        if event.get("type") == "voice":
            self._voice_state = str(event.get("state", self._voice_state))
        elif event.get("type") == "voice_level":
            with suppress(TypeError, ValueError):
                self._voice_level = float(event.get("level", 0.0))
        self.bus.publish(event)

    def _confirm(self, name: str, arguments: dict | None) -> bool:
        """Confirmation handler installed on the agent (dangerous actions)."""
        req = self.approvals.request(name, _redact_args(name, arguments))
        deadline = time.monotonic() + _APPROVAL_TIMEOUT
        no_client_since = None
        while not req.resolved:
            if self.bus.subscribers == 0:
                if no_client_since is None:
                    no_client_since = time.monotonic()
                elif time.monotonic() - no_client_since > _NO_CLIENT_GRACE:
                    log.info("No UI client connected — denying '%s' by default.", name)
                    req.respond(False)
                    break
            else:
                no_client_since = None
            if time.monotonic() > deadline:
                req.respond(False)
                break
            req._done.wait(timeout=0.25)
        self.approvals.resolve(req)
        return req.allowed

    # ---- controls (thread-safe) --------------------------------------------

    def submit_turn(self, text: str) -> bool:
        if self._assistant is None or not (text or "").strip():
            return False
        self._assistant.send_voice_text(text)
        return True

    def voice_status(self) -> dict:
        with self._lock:
            return {"state": self._voice_state, "level": self._voice_level, "muted": self._muted}

    def mute_voice(self, muted: bool) -> None:
        self._muted = bool(muted)
        with suppress(Exception):
            self._assistant.mute_voice(self._muted)

    def interrupt_voice(self) -> None:
        with suppress(Exception):
            self._assistant.interrupt_voice()

    def set_voice_only(self, enabled: bool) -> None:
        with suppress(Exception):
            self._assistant.set_voice_only(bool(enabled))

    def switch_model(self, provider: str, model: str) -> tuple[bool, str]:
        if self._assistant is None:
            return False, "assistant not started"
        ok = self._assistant.switch_model(provider, model)
        if ok:
            self.bus.publish({"type": "model_changed", "provider": provider, "model": model})
        return ok, "" if ok else "failed to build the provider"

    def switch_session(self, name: str) -> bool:
        if self._assistant is None:
            return False
        self._assistant.switch_session(name)
        self.bus.publish({"type": "session_changed", "name": self._assistant.current_session()})
        return True


def sys_stdout_stderr() -> tuple[Any, Any]:
    import sys

    return sys.stdout, sys.stderr


def sys_set_stdout_stderr(out: Any, err: Any) -> None:
    import sys

    sys.stdout, sys.stderr = out, err


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def _load_web_asset(name: str) -> bytes:
    from importlib.resources import files

    try:
        return (files("james.ui.web") / name).read_bytes()
    except FileNotFoundError as exc:  # pragma: no cover - packaged app
        raise HTTPException(status_code=404, detail=f"asset not found: {name}") from exc


def _status(runtime: ServerRuntime, assistant: Any) -> dict:
    from ..config import settings
    from ..llm.catalog import PROVIDERS, default_model, model_choices

    history = []
    for msg in getattr(assistant, "history", []) or []:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role not in ("user", "assistant") or not content:
            continue
        if not isinstance(content, str):
            content = str(content)
        history.append({"role": role, "text": content})
    return {
        "provider": settings.llm.provider,
        "model": settings.llm.model,
        "mode": settings.assistant.mode,
        "session": assistant.current_session(),
        "sessions": assistant.list_sessions(),
        "history": history[-200:],
        "providers": [
            {"name": p, "models": model_choices(p), "default_model": default_model(p)}
            for p in PROVIDERS
        ],
        "name": settings.assistant.name,
        "version": "0.5.0",
    }


def create_app(runtime: ServerRuntime) -> FastAPI:
    app = FastAPI(title="JAMES", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse(_load_web_asset("index.html").decode("utf-8"))

    @app.get("/static/{name}")
    async def static_asset(name: str):
        safe = name.replace("..", "").replace("/", "").replace("\\", "")
        if not safe or safe != name.replace("/", "").replace("\\", ""):
            raise HTTPException(status_code=404, detail="bad path")
        return _serve_asset(safe)

    @app.get("/api/events")
    async def events():
        async def stream():
            yield ": connected\n\n"
            last = runtime.bus.connect()
            try:
                while True:
                    items, last = runtime.bus.drain(last)
                    for item in items:
                        yield f"id: {item['id']}\ndata: {json.dumps(item['payload'])}\n\n"
                    await asyncio.sleep(0.2)
            finally:
                runtime.bus.disconnect()

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/status")
    async def status():
        a = runtime.assistant
        if a is None:
            return {"ready": False}
        return {"ready": True, **_status(runtime, a)}

    @app.post("/api/turn")
    async def turn(request: Request):
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="empty text")
        if not runtime.submit_turn(text):
            raise HTTPException(status_code=503, detail="assistant not ready")
        return {"ok": True}

    @app.get("/api/sessions")
    async def sessions():
        a = runtime.assistant
        if a is None:
            return {"sessions": [], "current": ""}
        return {"sessions": a.list_sessions(), "current": a.current_session()}

    @app.post("/api/sessions/switch")
    async def sessions_switch(request: Request):
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="missing name")
        if not runtime.switch_session(name):
            raise HTTPException(status_code=503, detail="assistant not ready")
        return {"ok": True}

    @app.post("/api/sessions/new")
    async def sessions_new():
        a = runtime.assistant
        if a is None:
            raise HTTPException(status_code=503, detail="assistant not ready")
        name = a.new_session()
        runtime.bus.publish({"type": "session_changed", "name": name})
        return {"ok": True, "name": name}

    @app.post("/api/sessions/clear")
    async def sessions_clear():
        a = runtime.assistant
        if a is None:
            raise HTTPException(status_code=503, detail="assistant not ready")
        a.clear_history()
        runtime.bus.publish({"type": "session_cleared", "name": a.current_session()})
        return {"ok": True}

    @app.get("/api/model")
    async def model():
        from ..config import settings

        return {"provider": settings.llm.provider, "model": settings.llm.model}

    @app.post("/api/model")
    async def model_set(request: Request):
        body = await request.json()
        provider = (body.get("provider") or "").strip().lower()
        model = (body.get("model") or "").strip()
        if not provider or not model:
            raise HTTPException(status_code=400, detail="provider and model required")
        ok, err = runtime.switch_model(provider, model)
        if not ok:
            raise HTTPException(status_code=400, detail=err or "invalid provider/model")
        return {"ok": True}

    @app.get("/api/tools")
    async def tools():
        a = runtime.assistant
        if a is None:
            return {"tools": []}
        from ..config import settings
        from ..tools.registry import is_dangerous_tool_call

        allowed = set(settings.assistant.allowed_tools or [])
        denied = set(settings.assistant.denied_tools or [])
        out = []
        for schema in a.registry.schemas():
            name = schema.get("name", "")
            out.append(
                {
                    "name": name,
                    "description": schema.get("description", ""),
                    "dangerous": is_dangerous_tool_call(name, {}),
                    "allowed": not allowed or name in allowed,
                    "denied": name in denied,
                }
            )
        return {"tools": out}

    @app.get("/api/settings")
    async def settings_get():
        from ..config import settings

        return _settings_snapshot(settings)

    @app.post("/api/settings")
    async def settings_post(request: Request):
        from ..config import settings

        body = await request.json()
        updates = body.get("updates") or body
        if not isinstance(updates, dict):
            raise HTTPException(status_code=400, detail="updates must be an object")
        errors = _apply_settings(updates, settings)
        if errors:
            raise HTTPException(status_code=400, detail="; ".join(errors))
        return _settings_snapshot(settings)

    @app.get("/api/voice")
    async def voice():
        from ..config import settings

        return {
            "enabled": settings.voice.enabled,
            "duplex_mode": settings.voice.duplex_mode,
            "state": runtime.voice_status()["state"],
            "level": runtime.voice_status()["level"],
            "muted": runtime.voice_status()["muted"],
        }

    @app.post("/api/voice/mute")
    async def voice_mute(request: Request):
        body = await request.json()
        runtime.mute_voice(bool(body.get("muted")))
        return {"ok": True}

    @app.post("/api/voice/interrupt")
    async def voice_interrupt():
        runtime.interrupt_voice()
        return {"ok": True}

    @app.post("/api/voice/voice_only")
    async def voice_voice_only(request: Request):
        body = await request.json()
        runtime.set_voice_only(bool(body.get("enabled")))
        return {"ok": True}

    @app.post("/api/approvals/{req_id}")
    async def approvals(req_id: str, request: Request):
        body = await request.json()
        if not runtime.approvals.respond(req_id, bool(body.get("allowed"))):
            raise HTTPException(status_code=404, detail="unknown request id")
        return {"ok": True}

    @app.get("/api/onboarding")
    async def onboarding_get():
        from ..config import settings
        from ..llm.catalog import PROVIDERS, default_model, model_choices
        from ..onboarding import env_exists

        return {
            "needed": not env_exists(),
            "api_key_set": bool(settings.llm.api_key),
            "providers": [
                {"name": p, "models": model_choices(p), "default_model": default_model(p)}
                for p in PROVIDERS
            ],
        }

    @app.post("/api/onboarding")
    async def onboarding_post(request: Request):
        from ..config import settings
        from ..onboarding import configure, detect_provider

        body = await request.json()
        provider = (body.get("provider") or "").strip().lower()
        model = (body.get("model") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        if not provider or not model:
            raise HTTPException(status_code=400, detail="provider and model required")
        configure(
            provider,
            model,
            api_key,
            voice_enabled=bool(body.get("voice_enabled")),
            base_url=(body.get("base_url") or "").strip(),
        )
        # Apply live so no restart is needed: the running assistant rebuilds.
        if api_key:
            from ..llm.catalog import PROVIDER_KEY

            key_attr = PROVIDER_KEY[provider]
            setattr(settings.llm, key_attr, api_key)
        settings.voice.enabled = bool(body.get("voice_enabled"))
        runtime.switch_model(provider, model)
        return {"ok": True, "detected": detect_provider(api_key)}

    return app


def _serve_asset(name: str):
    from fastapi.responses import Response

    data = _load_web_asset(name)
    ctype = "text/html" if name.endswith(".html") else "text/css" if name.endswith(".css") else "text/javascript"
    return Response(content=data, media_type=ctype)


def _settings_snapshot(settings: Any) -> dict:
    return {
        "mode": settings.assistant.mode,
        "dry_run": settings.assistant.dry_run,
        "confirm_dangerous_actions": settings.assistant.confirm_dangerous_actions,
        "wake_engine": settings.assistant.wake_engine,
        "wake_word": settings.assistant.wake_word,
        "assistant_name": settings.assistant.name,
        "user_name": settings.assistant.user_name,
        "offline_mode": settings.assistant.offline_mode,
        "voice_enabled": settings.voice.enabled,
        "stt_provider": settings.voice.stt_provider,
        "tts_provider": settings.voice.tts_provider,
        "duplex_mode": settings.voice.duplex_mode,
        "allowed_tools": list(settings.assistant.allowed_tools or []),
        "denied_tools": list(settings.assistant.denied_tools or []),
        "workspace_dir": str(settings.assistant.workspace_dir),
    }


def _apply_settings(updates: dict, settings: Any) -> list[str]:
    """Apply whitelisted setting updates in memory. Returns error strings."""
    errors: list[str] = []
    a = settings.assistant
    v = settings.voice
    for key, value in updates.items():
        if key == "mode":
            if value not in ("standard", "full"):
                errors.append("mode must be 'standard' or 'full'")
            else:
                a.mode = value
        elif key in ("dry_run", "confirm_dangerous_actions", "offline_mode", "voice_enabled"):
            a.dry_run = bool(value) if key == "dry_run" else a.dry_run
            a.confirm_dangerous_actions = bool(value) if key == "confirm_dangerous_actions" else a.confirm_dangerous_actions
            a.offline_mode = bool(value) if key == "offline_mode" else a.offline_mode
            v.enabled = bool(value) if key == "voice_enabled" else v.enabled
        elif key == "wake_engine":
            if value not in ("always", "none", "porcupine"):
                errors.append("wake_engine must be always|none|porcupine")
            else:
                a.wake_engine = value
        elif key in ("wake_word", "assistant_name", "user_name"):
            a.wake_word = str(value) if key == "wake_word" else a.wake_word
            a.name = str(value) if key == "assistant_name" else a.name
            a.user_name = str(value) if key == "user_name" else a.user_name
        elif key in ("stt_provider", "tts_provider", "duplex_mode"):
            v.stt_provider = str(value) if key == "stt_provider" else v.stt_provider
            v.tts_provider = str(value) if key == "tts_provider" else v.tts_provider
            v.duplex_mode = str(value) if key == "duplex_mode" else v.duplex_mode
        elif key in ("allowed_tools", "denied_tools"):
            if isinstance(value, list):
                a.allowed_tools = [str(t) for t in value]
                a.denied_tools = [str(t) for t in value] if key == "denied_tools" else a.denied_tools
            else:
                errors.append(f"{key} must be a list")
        else:
            errors.append(f"unknown setting: {key}")
    return errors


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8124,
    open_browser: bool = False,
    runtime: ServerRuntime | None = None,
) -> ServerRuntime:
    """Start uvicorn in a background thread; returns the runtime."""
    import uvicorn

    runtime = runtime or ServerRuntime()
    runtime.start()
    app = create_app(runtime)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("server failed to start")
    runtime._server = server  # type: ignore[attr-defined]
    if open_browser:
        with suppress(Exception):
            webbrowser.open(f"http://{host}:{port}/")
    return runtime


def serve_cli(port: int = 8124) -> int:
    """Blocking entry for `python -m james --serve`."""
    print(f"JAMES web UI: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    run_server(port=port, open_browser=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
