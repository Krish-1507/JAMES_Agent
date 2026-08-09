"""Isolated GAIA task worker.

Runs exactly one evaluation task in a fresh interpreter subprocess so a hung
or crashing agent run never stalls the suite. The parent harness
(:mod:`james.evaluation.gaia`) enforces a hard timeout on this process.

Payload (JSON on argv[1]):
    question, file_path, scratch_dir, max_iterations, system_prompt

Result (JSON on stdout):
    reply, tool_calls, iterations, ok, error
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _run_task(payload: dict) -> dict:
    import sys

    def _trace(msg: str) -> None:
        # Flushed immediately so the parent harness can see where a timed-out
        # worker was stuck (it surfaces the last stderr line as the error).
        print(f"[worker] {msg}", file=sys.stderr, flush=True)

    from ..config import settings
    from ..core.agent import Agent
    from ..llm.base import LLMProvider
    from ..llm.factory import build_provider
    from ..tools.registry import ToolRegistry

    _trace("imports done")
    question = str(payload.get("question") or "")
    max_iterations = int(payload.get("max_iterations", 20) or 20)
    scratch = Path(payload.get("scratch_dir") or ".")
    scratch.mkdir(parents=True, exist_ok=True)

    file_path = payload.get("file_path") or ""
    if file_path and Path(file_path).exists():
        question += (
            f"\n\n[You are given the file {Path(file_path).name}, located at "
            f"{file_path}. Read it if you need to answer the question.]"
        )

    class _CountingProvider:
        """Wraps the LLM to count inference rounds (iterations)."""

        def __init__(self, llm: LLMProvider):
            self._llm = llm
            self.iterations = 0

        def chat(self, messages, tools=None, tool_choice="auto", images=None, model=None):
            self.iterations += 1
            return self._llm.chat(
                messages, tools=tools, tool_choice=tool_choice, images=images, model=model
            )

    # Evaluations must never block on a human approval prompt.
    settings.assistant.confirm_dangerous_actions = False
    # Evaluations must never silently switch models mid-suite: pin the provider
    # chain to the configured primary and disable failover, so every task in a
    # run is scored with the same model (the report names one model per run).
    settings.llm.failover = []

    llm = _CountingProvider(build_provider(settings.llm))
    _trace("provider built")
    registry = ToolRegistry(discover_plugins=False)

    counters = {"tool_calls": 0}

    def _on_tool(call_id: str, name: str, arguments: dict, result: str) -> None:
        counters["tool_calls"] += 1

    agent = Agent(
        llm,
        registry,
        max_iterations=max_iterations,
        confirm_dangerous=False,
        nudge=False,
        system_prompt=payload.get("system_prompt"),
    )
    agent.on_tool = _on_tool

    _trace("agent.run starting")
    reply, _history = agent.run(question)
    _trace("agent.run done")
    # The GAIA prompt instructs the model to end with the bare answer on the
    # final line; when it complies (possibly after narration above), grading
    # against the last line is far more robust than the whole message.
    lines = [ln.strip() for ln in (reply or "").splitlines() if ln.strip()]
    if lines:
        reply = lines[-1]
    return {
        "reply": reply,
        "tool_calls": counters["tool_calls"],
        "iterations": llm.iterations,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        payload = json.loads(argv[0])
        result = _run_task(payload)
        result["ok"] = True
    except BaseException as exc:
        result = {"ok": False, "error": str(exc), "reply": "", "tool_calls": 0, "iterations": 0}
    print(json.dumps(result, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
