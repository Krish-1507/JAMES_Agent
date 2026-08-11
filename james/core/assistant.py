"""JAMES — top-level orchestrator that wires voice, LLM and tools together."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import queue
import re
import tempfile
import threading
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from rich.console import Console
from rich.logging import RichHandler

from .. import __version__
from ..config import settings
from ..core.guard import install_offline_guard
from ..core.scheduler import scheduler
from ..llm import build_provider
from ..llm.catalog import (
    DEFAULT_MODELS,
    PROVIDER_KEY,
    PROVIDERS,
    model_choices,
    save_llm_config,
)
from ..tools.background_tools import configure_background
from ..tools.delegate_tool import configure_delegate
from ..tools.desktop_tools import configure_computer_use
from ..tools.file_manager_tools import (
    configure_file_manager,
    start_file_manager_daemon,
    stop_file_manager_daemon,
)
from ..tools.forge_tools import configure_forge
from ..tools.registry import ToolRegistry
from ..tools.research_tools import configure_research
from ..ui.cli import create_cli
from ..voice import build_stt, build_tts
from .agent import Agent
from .secrets import load_or_create_secret


def _history_fernet() -> Fernet:
    key_path = settings.assistant.workspace_dir / ".james_history.key"
    secret = load_or_create_secret("JAMES_HISTORY_KEY", key_path)
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def _make_wake_re(wake_word: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(wake_word) + r"\b", re.IGNORECASE)


def encrypt_history(history: list) -> bytes:
    raw = json.dumps(history, ensure_ascii=False).encode("utf-8")
    return _history_fernet().encrypt(raw)


def decrypt_history(encrypted: bytes) -> list:
    try:
        raw = _history_fernet().decrypt(encrypted)
        return json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return []


def _fmt_args(args: dict) -> str:
    try:
        s = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
    except Exception:
        s = str(args)
    return s[:80]


def _session_slug(name: str) -> str:
    """Sanitize a session name into a safe filename slug."""
    slug = re.sub(r"[^a-z0-9_-]+", "_", (name or "").strip().lower()).strip("_")
    return slug or "default"


def _session_path(name: str) -> Path:
    slug = _session_slug(name)
    if slug == "default":
        return settings.assistant.history_file
    return settings.assistant.workspace_dir / "sessions" / f"{slug}.enc"


console = Console()


def get_logger() -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, settings.assistant.log_level, logging.INFO),
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    return logging.getLogger(settings.assistant.name)


class Assistant:
    def __init__(self, session: str | None = None, confirm=None):
        self.log = get_logger()
        self.settings = settings
        if settings.assistant.offline_mode:
            install_offline_guard()  # enforce privacy-certified local mode
        self.registry = ToolRegistry()
        self.llm = build_provider(settings.llm)
        self._confirmation_handler = confirm
        self._tool_hook = None
        self._tool_start_hook = None
        self._tool_pending_hook = None
        self.agent = Agent(self.llm, self.registry, confirm=confirm)
        self.cli = create_cli()
        self.stt = build_stt(settings.voice)
        self.tts = build_tts(settings.voice)
        self.history: list[dict] = []
        self._history_encrypted: bytes = b""
        self._forged_tasks: set = set()
        self._wake_re = _make_wake_re(settings.assistant.wake_word)
        self.session: str | None = session
        self._history_file = _session_path(session) if session else settings.assistant.history_file
        self._load_history()
        # Optional GUI text input queue (web UI / shell): when set, text_loop
        # reads from it instead of stdin so typed text stays first-class.
        self._text_queue: queue.Queue | None = None
        # Serializes handle_turn so typed and spoken turns never interleave.
        self._turn_lock = threading.Lock()
        self.duplex = None  # live DuplexController while full-duplex voice runs
        configure_delegate(self.llm, on_tool=self._on_tool, on_tool_start=self._on_tool_start)
        configure_computer_use(self.llm)
        configure_research(self.llm)
        configure_background(self.llm)
        configure_forge(self.registry)
        configure_file_manager(self.llm)
        if settings.assistant.auto_file_manager:
            start_file_manager_daemon()
        scheduler.start()
        self.on_event = None  # GUI hook: receives dict events (type: user|thinking|reply|speak)

    # ---- live tool hooks (console by default, GUI overrides via set_tool_hooks) ----
    def _on_tool_start(self, call_id: str, name: str, args: dict) -> None:
        if getattr(self, "cli", None):
            self.cli.print_tool_start(name, _fmt_args(args))
        else:
            console.print(f"[dim]🔧 {name}({_fmt_args(args)})…[/dim]")
        self._emit({"type": "tool_start", "call_id": call_id, "name": name, "args": args})

    def _on_tool(self, call_id: str, name: str, args: dict, result: str) -> None:
        ok = not (result.startswith("Error") or "failed" in result.lower())
        if getattr(self, "cli", None):
            self.cli.print_tool_done(name, ok, result)
        else:
            tag = "[green]✓[/green]" if ok else "[red]✗[/red]"
            console.print(f"{tag} {name}: {result[:120]}")
        self._emit(
            {
                "type": "tool",
                "call_id": call_id,
                "name": name,
                "args": args,
                "result": result,
                "ok": ok,
            }
        )

    def set_tool_hooks(self, on_tool=None, on_tool_start=None, on_tool_pending=None) -> None:
        """Let the GUI replace the default console hooks (and propagate to delegates)."""
        self._tool_hook = on_tool
        self._tool_start_hook = on_tool_start
        self._tool_pending_hook = on_tool_pending
        self.agent.on_tool = on_tool
        self.agent.on_tool_start = on_tool_start
        self.agent.on_tool_pending = on_tool_pending
        configure_delegate(self.llm, on_tool=on_tool, on_tool_start=on_tool_start)

    def set_confirmation_handler(self, confirm=None) -> None:
        """Persist a confirmation handler across live model switches."""
        self._confirmation_handler = confirm
        if confirm is not None:
            self.agent.confirm = confirm

    def switch_model(self, provider: str, model: str) -> bool:
        """Rebuild the live LLM provider + agent for a new provider/model.

        Returns True on success. The choice is persisted to ``.env`` so it
        survives restarts. Callers should check the API key for the provider
        before switching to avoid a provider that can never succeed.
        """
        provider = (provider or "").strip().lower()
        model = (model or "").strip()
        if not provider or not model:
            return False

        prev_provider, prev_model = settings.llm.provider, settings.llm.model
        settings.llm.provider = provider
        settings.llm.model = model
        try:
            self.llm = build_provider(settings.llm)
        except Exception:
            settings.llm.provider, settings.llm.model = prev_provider, prev_model
            self.log.exception("Failed to build provider for %s/%s", provider, model)
            return False
        self.agent = Agent(self.llm, self.registry, confirm=self._confirmation_handler)
        self.agent.on_tool = self._tool_hook
        self.agent.on_tool_start = self._tool_start_hook
        self.agent.on_tool_pending = self._tool_pending_hook
        configure_delegate(
            self.llm,
            on_tool=self._tool_hook or self._on_tool,
            on_tool_start=self._tool_start_hook or self._on_tool_start,
        )
        configure_computer_use(self.llm)
        configure_research(self.llm)
        configure_background(self.llm)
        configure_file_manager(self.llm)
        with suppress(Exception):
            save_llm_config(provider, model)
        self.log.info("Switched model -> provider=%s model=%s", provider, model)
        return True

    def _provider_has_key(self, provider: str) -> bool:
        """True when a non-empty API key is configured for ``provider``.

        Local/custom endpoints may present with an empty key, so ``custom`` is
        always allowed through (Ollama uses a dummy key).
        """
        if provider == "custom":
            return True
        key_var = PROVIDER_KEY.get(provider)
        if not key_var:
            return False
        return bool(os.environ.get(key_var))

    def _select_provider(self) -> bool:
        """Interactive provider picker for the ``/provider`` slash command."""
        from rich.prompt import Select

        if not getattr(self, "cli", None):
            return False
        provider = Select.ask(
            "provider",
            choices=[f"{p}  ·  {DEFAULT_MODELS.get(p, '')}" for p in PROVIDERS],
            default=f"{settings.llm.provider}  ·  {DEFAULT_MODELS.get(settings.llm.provider, '')}",
            show_default=False,
        )
        provider = provider.split("  ·  ")[0].strip()
        if provider != "custom" and not self._provider_has_key(provider):
            self.cli.print_hint(f"No API key configured for '{provider}'. Configure it in .env.")
            return False
        choices = model_choices(provider)
        if not choices:
            choices = [DEFAULT_MODELS.get(provider, "gpt-4o-mini")]
        default = (
            settings.llm.model
            if provider == settings.llm.provider
            else DEFAULT_MODELS.get(provider, choices[0])
        )
        model = Select.ask(
            f"model for {provider}:",
            choices=choices,
            default=default,
            show_default=False,
        )
        ok = self.switch_model(provider, model)
        if ok and getattr(self, "cli", None):
            self.cli.print_session_message(f"Now using {provider}:{model}")
        return ok

    def _select_model(self) -> bool:
        """Interactive model picker for the ``/model`` slash command."""
        if not getattr(self, "cli", None):
            return False
        provider = settings.llm.provider
        choices = model_choices(provider)
        if not choices:
            from rich.prompt import Prompt as RichPrompt

            model = RichPrompt.ask(
                f"Enter an id for the {provider} provider", default=settings.llm.model
            )
            return self.switch_model(provider, model)
        try:
            from rich.prompt import Select
        except ImportError:
            return False
        model = Select.ask(
            f"choose a model for {provider}",
            choices=choices,
            default=settings.llm.model,
            show_default=False,
        )
        if self.switch_model(provider, model) and getattr(self, "cli", None):
            self.cli.print_session_message(f"Model set to {model}")
        return True

    def _emit(self, event: dict) -> None:
        if self.on_event:
            with suppress(Exception):
                self.on_event(event)

    def _load_history(self) -> None:
        try:
            path = self._history_file
            if path.exists():
                self._history_encrypted = path.read_bytes()
                self.history = decrypt_history(self._history_encrypted)
                return
            if self.session:
                return
            legacy_path = settings.assistant.workspace_dir / "conversation_history.jsonl"
            if not legacy_path.exists():
                return
            for line in legacy_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self.history.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            if self.history:
                self._save_history()
                if legacy_path != path:
                    legacy_path.unlink()
        except Exception:  # nosec B110 - best-effort legacy history migration
            pass

    def _save_history(self) -> None:
        try:
            messages = self.history or decrypt_history(self._history_encrypted)
            self._history_encrypted = encrypt_history(messages)
            path = self._history_file
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                handle.write(self._history_encrypted)
                handle.flush()
                os.fsync(handle.fileno())
                temp_name = handle.name
            os.replace(temp_name, path)
        except Exception:  # nosec B110 - best-effort history persistence
            pass

    def export_conversation(self, format: str = "json") -> str:
        """Export conversation history to a file. Returns the file path."""
        try:
            messages = self.history or decrypt_history(self._history_encrypted)
            if not messages and self._history_file.exists():
                messages = decrypt_history(self._history_file.read_bytes())

            if format == "json":
                export_path = settings.assistant.workspace_dir / "conversation_export.json"
                export_path.write_text(
                    json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            elif format == "markdown":
                export_path = settings.assistant.workspace_dir / "conversation_export.md"
                md = ["# JAMES Conversation Export\n"]
                md.append(f"**Exported:** {datetime.now().isoformat()}\n")
                for msg in messages:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    md.append(f"## {role.capitalize()}\n\n{content}\n")
                export_path.write_text("\n".join(md), encoding="utf-8")
            else:
                return ""
            return str(export_path)
        except Exception:
            return ""

    def list_sessions(self) -> list[str]:
        """Return available named sessions (excluding the default history)."""
        sessions_dir = settings.assistant.workspace_dir / "sessions"
        try:
            if not sessions_dir.exists():
                return []
            return sorted(
                path.stem
                for path in sessions_dir.glob("*.enc")
                if _session_slug(path.stem) == path.stem
            )
        except Exception:
            return []

    def switch_session(self, name: str) -> None:
        """Persist the current conversation and switch to another named session."""
        self._save_history()
        self.history = []
        self._history_encrypted = b""
        self.session = _session_slug(name)
        self._history_file = _session_path(self.session)
        self._load_history()

    def current_session(self) -> str:
        return self.session or "default"

    def new_session(self) -> str:
        """Start a fresh named session (mirrors the /new command)."""
        name = f"conversation-{int(datetime.now().timestamp())}"
        self.switch_session(name)
        return name

    def clear_history(self) -> None:
        """Wipe the current session's in-memory + persisted history (/clear)."""
        self.history = []
        self._history_encrypted = b""
        self._save_history()

    def _summarize_history(self) -> None:
        if len(self.history) < 20:
            return
        try:
            recent = self.history[-10:]
            older = self.history[:-10]
            summary_prompt = (
                "Summarize the following conversation in 3-5 sentences. "
                "Include key facts, decisions made, files created or modified, "
                "and any pending tasks. Be concise and factual.\n\n"
                + "\n".join(
                    f"{m.get('role', '')}: {m.get('content', '')[:200]}"
                    for m in older
                    if m.get("role") in ("user", "assistant")
                )
            )
            summary = self.llm.chat([{"role": "user", "content": summary_prompt}])
            summary_text = summary.content or ""
            summary_msg = {
                "role": "system",
                "content": f"[Conversation summary]: {summary_text}",
            }
            self.history = [summary_msg, *recent]
            # Persist the summary to long-term memory so future *sessions*
            # can recall it — this is the cross-session learning loop.
            self._remember_summary(summary_text)
        except Exception:  # nosec B110 - summarization failure must not break the loop
            pass

    def _remember_summary(self, summary: str) -> None:
        if not summary or not settings.assistant.memory_enabled:
            return
        try:
            from ..tools.memory_tools import remember

            remember.run(text=f"[session summary] {summary}")
        except Exception:  # nosec B110 - memory persistence is best-effort
            pass

    def get_memory_facts(self) -> list[dict]:
        """Return structured memory facts for UI visualization."""
        facts = []
        for msg in self.history or decrypt_history(self._history_encrypted):
            content = msg.get("content", "")
            if msg.get("role") == "user" and content:
                facts.append({"source": "user", "text": content[:200]})
            elif msg.get("role") == "assistant" and content:
                facts.append({"source": "assistant", "text": content[:200]})
        return facts[-20:]

    def speak(self, text: str, *, cli=None) -> None:
        text = (text or "").strip()
        if not text:
            return
        if cli is not None:
            cli.print_assistant(settings.assistant.name, text)
        else:
            console.print(f"[bold cyan]{settings.assistant.name}:[/bold cyan] {text}")
        self._emit({"type": "speak", "text": text})
        try:
            self.tts.speak(text)
        except Exception as exc:
            self.log.warning("TTS error: %s", exc)

    def think(self, user_text: str, images: list[str] | None = None) -> str:
        from ..tools.forge_tools import get_relevant_skills
        from ..tools.memory_tools import get_relevant_memories

        # Decrypt history for processing.
        if self._history_encrypted:
            self.history = decrypt_history(self._history_encrypted)

        # Surface relevant long-term memory so JAMES "remembers everything".
        mem = get_relevant_memories(user_text)
        # Surface saved skills that match, so the loop is read+write: a skill
        # forged in an earlier session is re-suggested when relevant.
        skills = get_relevant_skills(user_text)

        hints: list[str] = []
        if mem:
            hints.append(f"[Relevant memory]\n{mem}")
        if skills:
            hints.append(
                "[Relevant saved skills — consider invoking one of these if it fits]\n" + skills
            )
        prompt = "\n\n".join([*hints, user_text]) if hints else user_text

        prev_len = len(self.history[-20:])
        reply, self.history = self.agent.run(prompt, history=self.history[-20:], images=images)
        self._maybe_auto_forge(user_text, self.history, prev_len)
        self._summarize_history()

        # Re-encrypt history after processing.
        self._history_encrypted = encrypt_history(self.history)
        self._save_history()
        self.history = []

        return reply

    def _maybe_auto_forge(self, user_text: str, messages: list, prev_len: int) -> None:
        """After a successful multi-tool task, persist it as a native @tool skill."""
        if not settings.assistant.auto_skill:
            return
        new_msgs = messages[prev_len:]
        tool_calls = sum(1 for m in new_msgs if m.get("role") == "tool")
        saved = any(
            m.get("role") == "assistant"
            and any(
                tc.get("function", {}).get("name") == "save_skill" for tc in m.get("tool_calls", [])
            )
            for m in new_msgs
        )
        if tool_calls < 3 or saved:
            return
        key = user_text.strip().lower()
        if key in self._forged_tasks:
            return
        self._forged_tasks.add(key)
        try:
            from ..tools.forge_tools import auto_forge_from_history

            res = auto_forge_from_history(self.llm, messages)
            self.log.info("Skill Forge auto-generated: %s", res.output)
            self._emit({"type": "skill", "text": res.output})
        except Exception as exc:
            self.log.warning("Auto-forge error: %s", exc)

    def handle_turn(self, user_text: str) -> None:
        if not user_text:
            return
        with self._turn_lock:
            self._emit({"type": "user", "text": user_text})
            if getattr(self, "cli", None):
                self.cli.print_user(user_text)
            else:
                console.print(f"[green]{settings.assistant.user_name}:[/green] {user_text}")
            try:
                self._emit({"type": "thinking"})
                if getattr(self, "cli", None):
                    with self.cli.thinking():
                        reply = self.think(user_text)
                else:
                    reply = self.think(user_text)
            except Exception as exc:
                self.log.exception("Agent error")
                self._emit({"type": "error", "text": exc.__class__.__name__ + ": " + str(exc)})
                reply = "Something went wrong. Please try again."
            self._emit({"type": "reply", "text": reply})
            self.speak(reply, cli=getattr(self, "cli", None))

    def greet(self) -> None:
        import datetime

        hour = datetime.datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        self.speak(
            f"Good {part}, {settings.assistant.user_name}. {settings.assistant.name} online. How can I help?"
        )

    def voice_loop(self) -> None:
        mode = (settings.voice.duplex_mode or "off").lower()
        if mode != "off":
            self._voice_loop_duplex()
            return
        if self._text_queue is not None:
            # GUI mode: typed text must stay first-class in every voice loop.
            threading.Thread(target=self._text_drain_loop, name="voice-text-drain", daemon=True).start()
        engine = (settings.assistant.wake_engine or "always").lower()
        if engine == "none":
            self.speak("Listening continuously. Say 'exit' or 'stop' to quit.")
            while True:
                try:
                    heard = self.stt.listen()
                except Exception as exc:
                    self.log.warning("Listening error: %s", exc)
                    continue
                if not heard:
                    continue
                console.print(f"[dim]heard:[/dim] {heard}")
                if heard.lower().strip() in {"exit", "stop", "quit"}:
                    self.speak("Goodbye!")
                    break
                self.handle_turn(heard)
            return

        if engine == "porcupine":
            import importlib.util

            if not importlib.util.find_spec("pvporcupine"):
                self.speak("Porcupine wake word requested but not installed.")
                self.log.warning(
                    "WAKE_ENGINE=porcupine but pvporcupine is not installed. "
                    "Fall back to WAKE_ENGINE=always, or `pip install pvporcupine`."
                )
                self._voice_loop_wake_word()
                return
            self._voice_loop_porcupine()
            return

        # default: "always" — continuous mic, respond only after the wake word
        self._voice_loop_wake_word()

    def _voice_loop_duplex(self) -> None:
        """Full-duplex voice: wake-gated always-on session with interruption."""
        from ..voice.duplex import build_duplex

        try:
            controller = build_duplex(self)
        except Exception as exc:
            self.log.warning("Duplex voice unavailable (%s); using turn-based voice.", exc)
            self.speak("Full-duplex voice is not available. Falling back to turn-based mode.")
            self._voice_loop_wake_word()
            return
        if controller is None:
            self._voice_loop_wake_word()
            return
        self.duplex = controller
        self.speak(f"Full-duplex voice online. Say '{settings.assistant.wake_word}' to wake me.")
        try:
            controller.run()
        finally:
            self.duplex = None

    # ---- live duplex controls (thread-safe; called from the GUI thread) ----
    def mute_voice(self, muted: bool) -> None:
        controller = getattr(self, "duplex", None)
        if controller is not None:
            controller.mute(muted)

    def interrupt_voice(self) -> None:
        controller = getattr(self, "duplex", None)
        if controller is not None:
            controller.interrupt()

    def set_voice_only(self, enabled: bool) -> None:
        controller = getattr(self, "duplex", None)
        if controller is not None:
            controller.voice_only = bool(enabled)

    def send_voice_text(self, text: str) -> None:
        """Typed input: routed to the duplex session when live, else queued."""
        text = (text or "").strip()
        if not text:
            return
        controller = getattr(self, "duplex", None)
        if controller is not None:
            controller.send_text(text)
            return
        queue = self._text_queue
        if queue is not None:
            queue.put(text)

    def _text_drain_loop(self) -> None:
        """Consume the GUI text queue while a non-duplex voice loop runs."""
        while True:
            try:
                text = self._text_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            text = (text or "").strip()
            if text:
                self.handle_turn(text)

    def _voice_loop_wake_word(self) -> None:
        self.speak(f"Say '{settings.assistant.wake_word}' to wake me up.")
        while True:
            try:
                heard = self.stt.listen()
            except Exception as exc:
                self.log.warning("Listening error: %s", exc)
                continue
            if not heard:
                continue
            console.print(f"[dim]heard:[/dim] {heard}")
            if self._wake_re.search(heard):
                command = heard.lower().replace(settings.assistant.wake_word, "").strip() or None
                if not command:
                    self.speak("Yes?")
                    command = self.stt.listen()
                if command:
                    self.handle_turn(command)

    def _voice_loop_porcupine(self) -> None:
        from .porcupine_engine import PorcupineWakeEngine

        engine = PorcupineWakeEngine(settings.assistant.porcupine_key)
        self.speak("Wake word armed. Say it to start a command.")
        try:
            while True:
                keyword = engine.listen()
                if not keyword:
                    continue
                self.speak("Yes?")
                command = self.stt.listen()
                if command and command.lower().strip() in {"exit", "stop", "quit"}:
                    self.speak("Goodbye!")
                    break
                if command:
                    self.handle_turn(command)
        finally:
            engine.close()

    def text_loop(self) -> None:
        self.cli.print_logo(__version__)
        self.cli.print_header(
            provider=settings.llm.provider,
            model=settings.llm.model,
            session=self.current_session() or "default",
            version=__version__,
        )
        self.cli.print_welcome(settings.assistant.name, settings.assistant.user_name)
        self.cli.print_hint(
            "Type 'exit' or 'quit' to leave. Commands: /new, /sessions, "
            "/resume <name>, /clear, /export, /provider, /model"
        )
        while True:
            if self._text_queue is not None:
                # GUI mode: typed text arrives through the queue.
                try:
                    user_text = self._text_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                user_text = (user_text or "").strip()
            else:
                try:
                    user_text = self.cli.read_prompt(settings.assistant.user_name)
                except (EOFError, KeyboardInterrupt):
                    break
                user_text = (user_text or "").strip()
            if user_text.lower() in {"exit", "quit", "stop"}:
                self.speak("Goodbye!", cli=self.cli)
                break
            if self._handle_session_command(user_text):
                continue
            self.handle_turn(user_text)

    def _handle_session_command(self, cmd: str) -> bool:
        """Handle in-loop session commands. Returns True if the command was consumed."""
        cmd = cmd.strip()
        if not cmd.startswith("/"):
            return False
        parts = cmd.split(maxsplit=1)
        verb = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if verb == "/new":
            self.new_session()
            console.print(f"[dim]New session started ({self.current_session()}).[/dim]")
            return True
        if verb == "/sessions":
            sessions = self.list_sessions()
            if not sessions:
                console.print("[dim]No named sessions yet. Use /new to create one.[/dim]")
            else:
                console.print("[dim]Saved sessions:[/dim] " + ", ".join(sessions))
            return True
        if verb == "/resume":
            if not arg:
                console.print("[dim]Usage: /resume <session-name>[/dim]")
                return True
            self.switch_session(arg)
            console.print(f"[dim]Resumed session '{self.current_session()}'.[/dim]")
            return True
        if verb == "/clear":
            self.clear_history()
            console.print("[dim]Current session cleared.[/dim]")
            return True
        if verb == "/export":
            path = self.export_conversation("markdown")
            console.print(f"[dim]Exported to {path}.[/dim]")
            return True
        if verb == "/model":
            if arg:
                self.cli.print_hint("Usage: /model opens the interactive model picker.")
            else:
                self._select_model()
            return True
        if verb == "/provider":
            self._select_provider()
            return True
        return False

    def run(self) -> None:
        try:
            if settings.voice.enabled and settings.voice.stt_provider != "none":
                self.voice_loop()
            else:
                self.text_loop()
        finally:
            stop_file_manager_daemon()
            scheduler.stop()
