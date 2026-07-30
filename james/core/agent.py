"""The reasoning core: an LLM-driven agent that calls tools to complete tasks."""
from __future__ import annotations

import json
import queue
import logging
import sys
import threading
from typing import Callable, List, Optional, Tuple

from ..config import settings
from ..llm.base import LLMProvider, LLMResponse
from ..tools.registry import ToolRegistry, is_dangerous_tool_call
from .personality import build_system_prompt


class _ConfirmRequest:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = arguments
        self._event = threading.Event()
        self._result = False

    def wait(self, timeout: float = 30.0) -> bool:
        self._event.wait(timeout)
        return self._result

    def respond(self, allowed: bool) -> None:
        self._result = allowed
        self._event.set()


_confirm_queue: "queue.Queue[_ConfirmRequest]" = queue.Queue()
_confirm_thread: Optional[threading.Thread] = None


def request_confirmation(name: str, arguments: dict) -> bool:
    """Non-blocking confirmation request. In GUI mode, the orb UI handles this.

    Returns True if confirmed, False if denied or timed out.
    """
    req = _ConfirmRequest(name, arguments)
    _confirm_queue.put(req)
    return req.wait(timeout=30.0)


def _process_confirmation_queue() -> None:
    """Background thread that processes confirmation requests via CLI fallback."""
    while True:
        try:
            req = _confirm_queue.get(timeout=1)
        except queue.Empty:
            continue
        if sys.stdin.isatty():
            print(f"\n[confirm] Tool '{req.name}' wants to run with: {req.arguments}")
            allowed = input("Allow? [y/N] ").strip().lower() in {"y", "yes"}
        else:
            allowed = False
        req.respond(allowed)


def _ensure_confirm_thread() -> None:
    global _confirm_thread
    if _confirm_thread is None or not _confirm_thread.is_alive():
        _confirm_thread = threading.Thread(target=_process_confirmation_queue, daemon=True)
        _confirm_thread.start()


logger = logging.getLogger("james")


class Agent:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        max_iterations: int = 20,
        confirm_dangerous: Optional[bool] = None,
        confirm: Optional[Callable[[str, dict], bool]] = None,
        nudge: bool = True,
        system_prompt: Optional[str] = None,
    ):
        self.llm = llm
        self.registry = registry
        self.max_iterations = max_iterations
        self.confirm_dangerous = (
            settings.assistant.confirm_dangerous_actions if confirm_dangerous is None else confirm_dangerous
        )
        self.confirm = confirm or request_confirmation
        self._nudge = nudge
        # Optional hooks for live UI / logging. Both receive a unique per-call
        # ``call_id`` so a "started" event can be matched to its "finished" event.
        self.on_tool_start = None  # on_tool_start(call_id, name, args)
        self.on_tool = None        # on_tool(call_id, name, args, result)
        self._tool_seq = 0
        self.system_prompt = system_prompt or build_system_prompt()
        _ensure_confirm_thread()

    def _annotate(self, resp: LLMResponse) -> dict:
        msg: dict = {"role": "assistant", "content": resp.content or ""}
        if resp.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in resp.tool_calls
            ]
        return msg

    def run(self, user_message: str, history: Optional[List[dict]] = None) -> Tuple[str, List[dict]]:
        """Run one user turn. Returns (final_reply, full_message_history)."""
        messages: List[dict] = [{"role": "system", "content": self.system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        tool_calls_this_turn = 0
        saved_skill = False

        for _ in range(self.max_iterations):
            try:
                resp = self.llm.chat(messages, tools=self.registry.schemas())
            except Exception as exc:
                logger.warning("LLM API error: %s", exc)
                retry = self.confirm(
                    "retry_llm",
                    {"error": str(exc)[:200], "attempt": "retry"},
                )
                if not retry:
                    return (
                        "The LLM API failed and you chose not to retry. Please try again later.",
                        messages,
                    )
                continue

            messages.append(self._annotate(resp))

            if not resp.tool_calls:
                reply = resp.content or "(no response)"
                # Skill Forge nudge: a complex, successful task is worth saving.
                if (
                    self._nudge
                    and tool_calls_this_turn >= 3
                    and not saved_skill
                    and "save_skill" not in reply
                ):
                    reply += (
                        "\n\n[JAMES] That was a multi-step task — I can save it as a "
                        "reusable skill so I never re-figure it out. Just say "
                        "\"save this as a skill called <name>\" and I'll persist it."
                    )
                return reply, messages

            for tc in resp.tool_calls:
                tool_calls_this_turn += 1
                if tc.name == "save_skill":
                    saved_skill = True
                self._tool_seq += 1
                call_id = f"{id(self)}-{self._tool_seq}"
                if self.on_tool_start:
                    try:
                        self.on_tool_start(call_id, tc.name, tc.arguments)
                    except Exception:
                        pass
                if self.confirm_dangerous and is_dangerous_tool_call(tc.name, tc.arguments):
                    allowed = self.confirm(tc.name, tc.arguments)
                    if not allowed:
                        result_text = f"Action '{tc.name}' was denied by the user."
                    else:
                        result_text = self.registry.execute(tc.name, tc.arguments).output
                else:
                    result_text = self.registry.execute(tc.name, tc.arguments).output

                if self.on_tool:
                    try:
                        self.on_tool(call_id, tc.name, tc.arguments, result_text)
                    except Exception:
                        pass

                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result_text}
                )

        return (
            "I reached the step limit while working on that. Here is what I have so far.",
            messages,
        )