"""The reasoning core: an LLM-driven agent that calls tools to complete tasks."""
from __future__ import annotations

import json
from typing import Callable, List, Optional, Tuple

from ..config import settings
from ..llm.base import LLMProvider, LLMResponse
from ..tools.registry import DANGEROUS_TOOLS, ToolRegistry
from .personality import build_system_prompt


class Agent:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        max_iterations: int = 10,
        confirm_dangerous: Optional[bool] = None,
        confirm: Optional[Callable[[str, dict], bool]] = None,
    ):
        self.llm = llm
        self.registry = registry
        self.max_iterations = max_iterations
        self.confirm_dangerous = (
            settings.assistant.confirm_dangerous_actions if confirm_dangerous is None else confirm_dangerous
        )
        self.confirm = confirm or self._default_confirm
        self.system_prompt = build_system_prompt()

    @staticmethod
    def _default_confirm(name: str, args: dict) -> bool:
        print(f"\n[confirm] Tool '{name}' wants to run with: {args}")
        return input("Allow? [y/N] ").strip().lower() in {"y", "yes"}

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

        for _ in range(self.max_iterations):
            resp = self.llm.chat(messages, tools=self.registry.schemas())
            messages.append(self._annotate(resp))

            if not resp.tool_calls:
                return resp.content or "(no response)", messages

            for tc in resp.tool_calls:
                if self.confirm_dangerous and tc.name in DANGEROUS_TOOLS:
                    allowed = self.confirm(tc.name, tc.arguments)
                    if not allowed:
                        result_text = f"Action '{tc.name}' was denied by the user."
                    else:
                        result_text = self.registry.execute(tc.name, tc.arguments).output
                else:
                    result_text = self.registry.execute(tc.name, tc.arguments).output

                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result_text}
                )

        return (
            "I reached the step limit while working on that. Here is what I have so far.",
            messages,
        )
