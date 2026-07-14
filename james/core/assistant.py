"""JAMES — top-level orchestrator that wires voice, LLM and tools together."""
from __future__ import annotations

import logging
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
        self._forged_tasks: set = set()
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

        # Surface relevant long-term memory so JAMES "remembers everything".
        mem = get_relevant_memories(user_text)
        prompt = f"[Relevant memory]\n{mem}\n\n{user_text}" if mem else user_text

        prev_len = len(self.history)
        reply, self.history = self.agent.run(prompt, history=self.history[-20:])
        self._maybe_auto_forge(user_text, self.history, prev_len)
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
            reply = f"Something went wrong: {exc}"
            self.log.exception("Agent error")
        self._emit({"type": "reply", "text": reply})
        self.speak(reply)

    def greet(self) -> None:
        import datetime

        hour = datetime.datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        self.speak(f"Good {part}, {settings.assistant.user_name}. {settings.assistant.name} online. How can I help?")

    def voice_loop(self) -> None:
        wake = settings.assistant.wake_word
        self.speak(f"Say '{wake}' to wake me up.")
        while True:
            try:
                heard = self.stt.listen()
            except Exception as exc:
                self.log.warning("Listening error: %s", exc)
                continue
            if not heard:
                continue
            console.print(f"[dim]heard:[/dim] {heard}")
            if wake in heard.lower():
                command = heard.lower().replace(wake, "").strip() or None
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
