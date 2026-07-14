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
from ..tools.forge_tools import configure_forge
from ..core.scheduler import scheduler
from ..voice import build_stt, build_tts
from .agent import Agent

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
        self.registry = ToolRegistry()
        self.llm = build_provider(settings.llm)
        self.agent = Agent(self.llm, self.registry)
        self.stt = build_stt(settings.voice)
        self.tts = build_tts(settings.voice)
        self.history: List[dict] = []
        configure_delegate(self.llm)
        configure_forge(self.registry)
        scheduler.start()
        self.on_event = None  # GUI hook: receives dict events (type: user|thinking|reply|speak)

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
        reply, self.history = self.agent.run(user_text, history=self.history[-20:])
        return reply

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
            scheduler.stop()
