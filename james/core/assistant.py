"""JAMES — top-level orchestrator that wires voice, LLM and tools together."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import List, Optional

from rich.console import Console
from rich.logging import RichHandler

from ..config import settings
from ..llm import build_provider
from ..tools.registry import ToolRegistry
from ..tools.delegate_tool import configure_delegate
from ..tools.desktop_tools import configure_computer_use
from ..tools.research_tools import configure_research
from ..tools.background_tools import configure_background
from ..tools.file_manager_tools import configure_file_manager, start_file_manager_daemon, stop_file_manager_daemon
from ..tools.forge_tools import configure_forge
from ..core.scheduler import scheduler
from ..core.guard import install_offline_guard
from ..voice import build_stt, build_tts
from .agent import Agent


def _derive_history_key() -> bytes:
    secret = os.environ.get("JAMES_HISTORY_KEY", "james-history-secret-change-me")
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), b"james-history-salt", 100_000)


def _make_wake_re(wake_word: str) -> re.Pattern:
    return re.compile(r'\b' + re.escape(wake_word) + r'\b', re.IGNORECASE)


def _xor_encrypt(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def encrypt_history(history: list) -> bytes:
    raw = json.dumps(history).encode("utf-8")
    return _xor_encrypt(raw, _derive_history_key())


def decrypt_history(encrypted: bytes) -> list:
    raw = _xor_encrypt(encrypted, _derive_history_key())
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return []


def _fmt_args(args: dict) -> str:
    try:
        s = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
    except Exception:
        s = str(args)
    return s[:80]

console = Console()


def get_logger() -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, settings.assistant.log_level, logging.INFO),
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    return logging.getLogger(settings.assistant.name)


class Assistant:
    def __init__(self):
        self.log = get_logger()
        self.settings = settings
        if settings.assistant.offline_mode:
            install_offline_guard()  # enforce privacy-certified local mode
        self.registry = ToolRegistry()
        self.llm = build_provider(settings.llm)
        self.agent = Agent(self.llm, self.registry)
        self.stt = build_stt(settings.voice)
        self.tts = build_tts(settings.voice)
        self.history: List[dict] = []
        self._history_encrypted: bytes = b""
        self._forged_tasks: set = set()
        self._wake_re = _make_wake_re(settings.assistant.wake_word)
        self._load_history()
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
        console.print(f"[dim]🔧 {name}({_fmt_args(args)})…[/dim]")
        self._emit({"type": "tool_start", "call_id": call_id, "name": name, "args": args})

    def _on_tool(self, call_id: str, name: str, args: dict, result: str) -> None:
        ok = not (result.startswith("Error") or "failed" in result.lower())
        tag = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"{tag} {name}: {result[:120]}")
        self._emit(
            {"type": "tool", "call_id": call_id, "name": name, "args": args, "result": result, "ok": ok}
        )

    def set_tool_hooks(self, on_tool=None, on_tool_start=None) -> None:
        """Let the GUI replace the default console hooks (and propagate to delegates)."""
        self.agent.on_tool = on_tool
        self.agent.on_tool_start = on_tool_start
        configure_delegate(self.llm, on_tool=on_tool, on_tool_start=on_tool_start)

    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    def _load_history(self) -> None:
        try:
            path = settings.assistant.workspace_dir / "conversation_history.jsonl"
            if not path.exists():
                return
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    self.history.append(msg)
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

    def _save_history(self) -> None:
        try:
            path = settings.assistant.workspace_dir / "conversation_history.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                for msg in self.history[-50:]:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def export_conversation(self, format: str = "json") -> str:
        """Export conversation history to a file. Returns the file path."""
        try:
            path = settings.assistant.workspace_dir / "conversation_history.jsonl"
            if not path.exists():
                return ""
            messages = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

            if format == "json":
                export_path = settings.assistant.workspace_dir / "conversation_export.json"
                export_path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
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
            summary_msg = {
                "role": "system",
                "content": f"[Conversation summary]: {summary.content or ''}",
            }
            self.history = [summary_msg] + recent
            self._save_history()
        except Exception:
            pass

    def get_memory_facts(self) -> list[dict]:
        """Return structured memory facts for UI visualization."""
        facts = []
        for msg in self.history:
            content = msg.get("content", "")
            if msg.get("role") == "user" and content:
                facts.append({"source": "user", "text": content[:200]})
            elif msg.get("role") == "assistant" and content:
                facts.append({"source": "assistant", "text": content[:200]})
        return facts[-20:]

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        console.print(f"[bold cyan]{settings.assistant.name}:[/bold cyan] {text}")
        self._emit({"type": "speak", "text": text})
        try:
            self.tts.speak(text)
        except Exception as exc:
            self.log.warning("TTS error: %s", exc)

    def think(self, user_text: str) -> str:
        from ..tools.memory_tools import get_relevant_memories

        # Decrypt history for processing.
        if self._history_encrypted:
            self.history = decrypt_history(self._history_encrypted)

        # Surface relevant long-term memory so JAMES "remembers everything".
        mem = get_relevant_memories(user_text)
        prompt = f"[Relevant memory]\n{mem}\n\n{user_text}" if mem else user_text

        prev_len = len(self.history)
        reply, self.history = self.agent.run(prompt, history=self.history[-20:])
        self._maybe_auto_forge(user_text, self.history, prev_len)

        # Re-encrypt history after processing.
        self._history_encrypted = encrypt_history(self.history)
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
                tc.get("function", {}).get("name") == "save_skill"
                for tc in m.get("tool_calls", [])
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
        self._emit({"type": "user", "text": user_text})
        console.print(f"[green]{settings.assistant.user_name}:[/green] {user_text}")
        try:
            self._emit({"type": "thinking"})
            reply = self.think(user_text)
        except Exception as exc:
            self.log.exception("Agent error")
            reply = "Something went wrong. Please try again."
        self._emit({"type": "reply", "text": reply})
        self.speak(reply)
        self._save_history()
        self._summarize_history()

    def greet(self) -> None:
        import datetime

        hour = datetime.datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        self.speak(f"Good {part}, {settings.assistant.user_name}. {settings.assistant.name} online. How can I help?")

    def voice_loop(self) -> None:
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

    def text_loop(self) -> None:
        self.greet()
        console.print("[dim]Type 'exit' or 'quit' to leave.[/dim]")
        while True:
            try:
                user_text = input(f"{settings.assistant.user_name}: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_text.lower() in {"exit", "quit", "stop"}:
                self.speak("Goodbye!")
                break
            self.handle_turn(user_text)

    def run(self) -> None:
        try:
            if settings.voice.enabled and settings.voice.stt_provider != "none":
                self.voice_loop()
            else:
                self.text_loop()
        finally:
            stop_file_manager_daemon()
            scheduler.stop()
