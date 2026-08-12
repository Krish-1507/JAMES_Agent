"""The reasoning core: an LLM-driven agent that calls tools to complete tasks.

Phase-1 agent quality features:
- Plan-then-act: the model is asked to state a short plan before acting; when
  ``require_plan`` is set, a single corrective nudge is injected if the first
  tool-using response skips the plan.
- Self-correction: tool errors are classified (transient vs permanent); a
  transient failure (rate limit, timeout, network) is retried once with a
  backoff before the error is surfaced to the model with a recovery hint.
- Parallel tool calls: independent (non-stateful) tool calls from one model
  response run concurrently; stateful browser/desktop tools stay serial.
- Mid-task context compaction: once the conversation exceeds a size threshold,
  older messages are summarized into one compact digest so long tasks keep
  working within the model's context window.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress

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


_confirm_queue: queue.Queue[_ConfirmRequest] = queue.Queue()
_confirm_thread: threading.Thread | None = None


def request_confirmation(name: str, arguments: dict) -> bool:
    """Non-blocking confirmation request. The desktop/web UI handles this.

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

# ---------------------------------------------------------------------------
# Tool error classification (self-correction)
# ---------------------------------------------------------------------------

TRANSIENT_ERROR = "TRANSIENT"
PERMANENT_ERROR = "PERMANENT"
UNKNOWN_ERROR = "UNKNOWN"

_TRANSIENT_PATTERNS = (
    r"rate\s*limit",
    r"quota",
    r"too many requests",
    r"tim(e|ed)\s*out",
    r"connection",
    r"econnreset",
    r"econnrefused",
    r"network",
    r"temporar",
    r"try again later",
    r"overloaded",
    r"is busy",
    r"remote end closed",
    r"can'?t reach",
    r"unavailable",
    r"\b5\d\d\b",
    r"\b(502|503|504)\b",
    r"\b429\b",
)

_PERMANENT_PATTERNS = (
    r"invalid",
    r"unknown tool",
    r"not found",
    r"no such ",
    r"denied",
    r"disabled",
    r"not allowed",
    r"missing required",
    r"must be ",
    r"malformed",
    r"failed to parse",
    r"\b404\b",
    r"\b400\b",
)


def classify_tool_error(text: str) -> str:
    """Classify a tool error as TRANSIENT, PERMANENT, or UNKNOWN.

    Transient errors (rate limits, timeouts, network blips) are worth an
    automatic retry; permanent ones (bad arguments, missing files, denials)
    should not be re-attempted blindly — the model should change approach.
    """
    if not text:
        return UNKNOWN_ERROR
    lowered = text.lower()
    if any(re.search(p, lowered) for p in _TRANSIENT_PATTERNS):
        return TRANSIENT_ERROR
    if any(re.search(p, lowered) for p in _PERMANENT_PATTERNS):
        return PERMANENT_ERROR
    return UNKNOWN_ERROR


# ---------------------------------------------------------------------------
# Parallel tool-call safety
# ---------------------------------------------------------------------------

# Stateful tools share a session/screen/UI and must never run concurrently.
_SERIAL_TOOLS = {
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_extract",
    "browser_screenshot",
    "browser_close",
    "browser_health",
    "computer_use",
    "click_at",
    "type_text",
    "press_key",
    "screenshot_save",
    "take_screenshot",
    "open_application",
    "control_media",
    "clipboard",
    "manage_files",
    "schedule_task",
    "outlook_read_inbox",
    "outlook_send_email",
    "outlook_create_event",
    "excel_read_cells",
    "excel_write_cells",
    "word_read_document",
    "powerpoint_create",
    "run_recipe_now",
    "send_message",
}

_PLAN_RE = re.compile(r"\bplan\b\s*[:：]", re.IGNORECASE)  # noqa: RUF001 - fullwidth colon is intentional

_PLAN_NUDGE = (
    "You called tools without first stating your plan. Before acting on a "
    "multi-step task, write a short numbered plan as plain text (e.g. "
    '"PLAN: 1) ... 2) ... 3) ...") in the SAME message as your first tool '
    "call. Then continue executing. Do not re-state the plan afterwards."
)

_COMPACT_PROMPT = (
    "You are compressing the middle of an ongoing agent conversation so it "
    "fits in the context window. Produce a concise factual digest (plain "
    "text, no formatting) that preserves EVERY concrete fact that could "
    "still matter later: numbers, names, dates, URLs, file paths, tool "
    "results, verified conclusions, and the user's original question and "
    "requirements. Keep the original tool-call messages OUT of the digest; "
    "summarize their findings instead. Do not add new information."
)


class Agent:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        max_iterations: int = 20,
        confirm_dangerous: bool | None = None,
        confirm: Callable[[str, dict], bool] | None = None,
        nudge: bool = True,
        system_prompt: str | None = None,
        require_plan: bool = False,
        parallel_tool_calls: bool = True,
        max_parallel: int = 4,
        auto_retry_transient: bool = True,
        retry_backoff: float = 1.5,
        compact_threshold_chars: int = 120_000,
        keep_turns_on_compact: int = 3,
        max_tools: int | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.max_iterations = max_iterations
        self.confirm_dangerous = (
            settings.assistant.confirm_dangerous_actions
            if confirm_dangerous is None
            else confirm_dangerous
        )
        self.confirm = confirm or request_confirmation
        self._nudge = nudge
        # Phase-1 quality knobs.
        self.require_plan = require_plan
        self.parallel_tool_calls = parallel_tool_calls
        self.max_parallel = max(1, max_parallel)
        self.auto_retry_transient = auto_retry_transient
        self.retry_backoff = max(0.0, retry_backoff)
        self.compact_threshold_chars = compact_threshold_chars
        self.keep_turns_on_compact = max(1, keep_turns_on_compact)
        # Some providers cap the number of tools per request (OpenAI 128,
        # Anthropic 64, OpenRouter 64). ``max_tools`` clips the schema list
        # before it is sent so the request is never rejected outright.
        self.max_tools = max_tools
        # Optional hooks for live UI / logging. Both receive a unique per-call
        # ``call_id`` so a "started" event can be matched to its "finished" event.
        self.on_tool_start = None  # on_tool_start(call_id, name, args)
        self.on_tool_pending = None  # on_tool_pending(call_id, name, args)
        self.on_tool = None  # on_tool(call_id, name, args, result)
        self._tool_seq = 0
        self.system_prompt = system_prompt or build_system_prompt()
        if confirm is None:
            _ensure_confirm_thread()
        # Diagnostics for eval reports / tests.
        self.compactions = 0
        self.auto_retries = 0

    # -- message helpers -----------------------------------------------------

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

    # -- plan-then-act --------------------------------------------------------

    def _plan_present(self, content: str | None) -> bool:
        return bool(content) and bool(_PLAN_RE.search(content))

    # -- tool execution -------------------------------------------------------

    def _run_one_tool(self, tc) -> str:
        """Execute a single tool call, with one automatic retry on transient
        errors. Returns the text that goes back to the model as the result."""
        call_id = self._next_call_id(tc.name)
        result_text = self._execute_tool(call_id, tc)

        if (
            not _result_ok(result_text)
            and self.auto_retry_transient
            and classify_tool_error(result_text) == TRANSIENT_ERROR
        ):
            logger.info("tool %s failed transiently; retrying once", tc.name)
            time.sleep(self.retry_backoff)
            self.auto_retries += 1
            result_text = self._execute_tool(call_id, tc)
            if not _result_ok(result_text):
                result_text = _annotate_error(result_text)
        return result_text

    def _next_call_id(self, name: str) -> str:
        self._tool_seq += 1
        return f"{id(self)}-{self._tool_seq}"

    def _execute_tool(self, call_id: str, tc) -> str:
        if self.confirm_dangerous and is_dangerous_tool_call(tc.name, tc.arguments):
            if self.on_tool_pending:
                with suppress(Exception):
                    self.on_tool_pending(call_id, tc.name, tc.arguments)
            allowed = self.confirm(tc.name, tc.arguments)
            if not allowed:
                return f"Action '{tc.name}' was denied by the user."
        if self.on_tool_start:
            with suppress(Exception):
                self.on_tool_start(call_id, tc.name, tc.arguments)
        try:
            result = self.registry.execute(tc.name, tc.arguments)
        except Exception as exc:  # defensive: a crashing tool must not kill the loop
            result_text = f"Error: {exc}"
        else:
            result_text = result.output
        if self.on_tool:
            with suppress(Exception):
                self.on_tool(call_id, tc.name, tc.arguments, result_text)
        return result_text

    def _execute_calls(self, tool_calls) -> list[str]:
        """Execute a batch of tool calls from one model response.

        Stateful tools run serially in order; everything else runs in a small
        thread pool. Results are returned in the original call order.
        """
        serial, parallel = [], []
        for tc in tool_calls:
            (serial if tc.name in _SERIAL_TOOLS else parallel).append(tc)

        results: dict[int, str] = {}
        if parallel and self.parallel_tool_calls:
            with ThreadPoolExecutor(max_workers=min(self.max_parallel, len(parallel))) as pool:
                future_map = {
                    pool.submit(self._run_one_tool, tc): i for i, tc in enumerate(parallel)
                }
                for future in as_completed(future_map):
                    idx = future_map[future]
                    try:
                        results[idx] = future.result()
                    except Exception as exc:  # thread-level guard
                        results[idx] = f"Error: {exc}"
        else:
            for i, tc in enumerate(parallel):
                results[i] = self._run_one_tool(tc)

        for i, tc in enumerate(serial):
            results[len(parallel) + i] = self._run_one_tool(tc)

        return [results[i] for i in range(len(tool_calls))]

    # -- context compaction ---------------------------------------------------

    def _maybe_compact(self, messages: list[dict], step: int) -> None:
        if self.compact_threshold_chars <= 0 or step < 2:
            return
        total = sum(len(json.dumps(m, default=str)) for m in messages)
        if total < self.compact_threshold_chars:
            return
        keep_count = 1 + self.keep_turns_on_compact * 2  # system + last turns
        if len(messages) <= keep_count + 2:
            return
        tail = messages[-keep_count:]
        middle = messages[1:-keep_count]
        summary = self._summarize(middle)
        if not summary:
            return
        summary_msg = {"role": "user", "content": f"[Earlier context summary]\n{summary}"}
        messages[:] = [messages[0], summary_msg, *tail]
        self.compactions += 1
        logger.info("context compacted (%d messages -> %d)", len(middle) + 1, 2)

    def _summarize(self, middle: list[dict]) -> str | None:
        try:
            resp = self.llm.chat(
                [{"role": "system", "content": _COMPACT_PROMPT}, *middle],
                tools=None,
            )
            return (resp.content or "").strip() or None
        except Exception as exc:
            logger.warning("context compaction failed: %s", exc)
            return None

    # -- main loop ------------------------------------------------------------

    def run(
        self,
        user_message: str,
        history: list[dict] | None = None,
        images: list[str] | None = None,
    ) -> tuple[str, list[dict]]:
        """Run one user turn. Returns (final_reply, full_message_history).

        ``images`` is a list of image references (file paths, base64 data
        URIs, or http(s) URLs) attached to this turn for vision models.

        The returned history excludes the system prompt this method injected,
        so callers can feed it straight back in on the next turn without the
        system prompt accumulating duplicates.
        """
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        def _tool_schemas() -> list[dict]:
            schemas = self.registry.schemas()
            if self.max_tools and len(schemas) > self.max_tools:
                schemas = schemas[: self.max_tools]
            return schemas

        def _history_out() -> list[dict]:
            return messages[1:]

        tool_calls_this_turn = 0
        saved_skill = False
        plan_nudged = False

        for step in range(self.max_iterations):
            self._maybe_compact(messages, step)
            try:
                resp = self.llm.chat(messages, tools=_tool_schemas(), images=images)
            except Exception as exc:
                logger.warning("LLM API error: %s", exc)
                if not self.confirm_dangerous:
                    # Headless/unattended mode (evaluations, background tasks):
                    # never block on an interactive retry prompt — surface the
                    # error so the caller can record it.
                    raise
                retry = self.confirm(
                    "retry_llm",
                    {"error": str(exc)[:200], "attempt": "retry"},
                )
                if not retry:
                    return (
                        "The LLM API failed and you chose not to retry. Please try again later.",
                        _history_out(),
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
                        '"save this as a skill called <name>" and I\'ll persist it.'
                    )
                return reply, _history_out()

            # Plan-then-act enforcement (single corrective nudge).
            if self.require_plan and not plan_nudged and not self._plan_present(resp.content):
                messages.append({"role": "system", "content": _PLAN_NUDGE})
                plan_nudged = True
                continue

            results = self._execute_calls(resp.tool_calls)
            for tc, result_text in zip(resp.tool_calls, results, strict=True):
                tool_calls_this_turn += 1
                if tc.name == "save_skill":
                    saved_skill = True
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

        return (
            "I reached the step limit while working on that. Here is what I have so far.",
            _history_out(),
        )


def _result_ok(result_text: str) -> bool:
    """Heuristic: a tool result is 'ok' unless it starts with an error marker."""
    return not result_text.startswith("Error:") and not result_text.startswith("Search failed:")


def _annotate_error(result_text: str) -> str:
    marker = "[TRANSIENT error, automatic retry failed — consider a different approach]"
    if marker in result_text:
        return result_text
    return f"{marker}\n{result_text}"
