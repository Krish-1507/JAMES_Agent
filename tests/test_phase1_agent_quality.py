"""Tests for Phase 1 (agent quality): plan-then-act, self-correction,
parallel tool calls, context compaction, and upgraded web tools.

These tests never touch the network or a real LLM: the LLM is faked and the
tool registry is built from hand-made tools.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import pytest

from james.core.agent import (
    PERMANENT_ERROR,
    TRANSIENT_ERROR,
    UNKNOWN_ERROR,
    Agent,
    classify_tool_error,
)
from james.tools.base import ToolResult, tool
from james.tools.registry import ToolRegistry

# --- fakes -----------------------------------------------------------------

@dataclass
class FakeLLM:
    """Scripted LLM: a queue of responses; chat() pops them one at a time."""

    responses: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    sent_tools: list = field(default_factory=list)
    sent_images: list = field(default_factory=list)

    def chat(self, messages, tools=None, tool_choice="auto", images=None, model=None):
        self.calls.append(messages)
        self.sent_tools.append(tools)
        self.sent_images.append(images)
        if tools is None:
            # Compaction summary call: never consume scripted responses.
            return _Simple("digest of earlier context", None)
        if not self.responses:
            return _Simple("done", None)
        return self.responses.pop(0)


@dataclass
class _Simple:
    content: str
    tool_calls: list | None


@dataclass
class _TC:
    id: str
    name: str
    arguments: dict


def _respond(content: str = "", *calls) -> _Simple:
    return _Simple(content, [c for c in calls if c is not None])


@tool("fake_ok", "returns ok", {"x": {"type": "string"}}, required=["x"])
def fake_ok(x: str) -> ToolResult:
    return ToolResult(ok=True, output=f"ok:{x}")


@tool("fake_fail", "returns an error", {"x": {"type": "string"}}, required=["x"])
def fake_fail(x: str) -> ToolResult:
    return ToolResult(ok=False, output=f"Error: {x}")


@tool("fake_transient", "returns a transient error", {})
def fake_transient() -> ToolResult:
    return ToolResult(ok=False, output="Error: rate limit exceeded, try again later")


@tool("fake_slow", "sleeps then returns", {})
def fake_slow() -> ToolResult:
    time.sleep(0.3)
    return ToolResult(ok=True, output="slow done")


def _registry() -> ToolRegistry:
    reg = ToolRegistry(tools=[], discover_plugins=False)
    for t in (fake_ok, fake_fail, fake_transient, fake_slow):
        reg.register(t)
    return reg


def _agent(llm: FakeLLM, **kwargs) -> Agent:
    return Agent(llm, _registry(), max_iterations=10, nudge=False, **kwargs)


# --- error classification ---------------------------------------------------

def test_classify_transient() -> None:
    for text in ("rate limit exceeded", "timed out", "connection reset", "HTTP 503"):
        assert classify_tool_error(text) == TRANSIENT_ERROR, text


def test_classify_permanent() -> None:
    for text in ("invalid argument", "file not found", "unknown tool", "HTTP 404", "denied"):
        assert classify_tool_error(text) == PERMANENT_ERROR, text


def test_classify_unknown() -> None:
    assert classify_tool_error("something weird happened") == UNKNOWN_ERROR
    assert classify_tool_error("") == UNKNOWN_ERROR


# --- plan-then-act -----------------------------------------------------------

def test_require_plan_nudges_once_then_executes() -> None:
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "fake_ok", {"x": "1"})),  # no plan -> nudge
            _respond("PLAN: 1) done", _TC("t2", "fake_ok", {"x": "2"})),  # plan + call
            _respond("final answer", None),
        ]
    )
    agent = _agent(llm, require_plan=True)
    reply, history = agent.run("do the thing")
    assert reply == "final answer"
    kinds = [m["role"] for m in history]
    assert kinds.count("system") == 1  # exactly one corrective nudge
    nudge = next(m for m in history if m["role"] == "system")
    assert "plan" in nudge["content"].lower()


def test_plan_not_required_when_disabled() -> None:
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "fake_ok", {"x": "1"})),
            _respond("done", None),
        ]
    )
    agent = _agent(llm, require_plan=False)
    reply, history = agent.run("do the thing")
    assert reply == "done"
    assert sum(m["role"] == "system" for m in history) == 0


# --- self-correction ----------------------------------------------------------

def test_transient_error_auto_retries_once() -> None:
    state = {"calls": 0}

    @tool("flaky", "fails once then works", {})
    def flaky() -> ToolResult:
        state["calls"] += 1
        if state["calls"] == 1:
            return ToolResult(ok=False, output="Error: timed out waiting for connection")
        return ToolResult(ok=True, output="second attempt ok")

    reg = _registry()
    reg.register(flaky)
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "flaky", {})),
            _respond("all good now", None),
        ]
    )
    agent = Agent(llm, reg, max_iterations=5, nudge=False, retry_backoff=0.0)
    reply, history = agent.run("use flaky")
    assert reply == "all good now"
    assert state["calls"] == 2
    assert agent.auto_retries == 1
    tool_msg = next(m for m in history if m["role"] == "tool")
    assert tool_msg["content"] == "second attempt ok"


def test_transient_retry_failure_adds_hint() -> None:
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "fake_transient", {})),
            _respond("changed approach", None),
        ]
    )
    agent = _agent(llm, retry_backoff=0.0)
    reply, history = agent.run("use transient")
    assert agent.auto_retries == 1
    tool_msg = next(m for m in history if m["role"] == "tool")
    assert "[TRANSIENT error" in tool_msg["content"]


def test_permanent_error_not_retried() -> None:
    state = {"calls": 0}

    @tool("bad", "always errors", {})
    def bad() -> ToolResult:
        state["calls"] += 1
        return ToolResult(ok=False, output="Error: invalid configuration")

    reg = _registry()
    reg.register(bad)
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "bad", {})),
            _respond("ok", None),
        ]
    )
    agent = Agent(llm, reg, max_iterations=5, nudge=False, retry_backoff=0.0)
    reply, history = agent.run("call bad")
    assert state["calls"] == 1
    assert agent.auto_retries == 0
    tool_msg = next(m for m in history if m["role"] == "tool")
    assert tool_msg["content"] == "Error: invalid configuration"


# --- parallel tool calls -------------------------------------------------------

def test_parallel_calls_run_concurrently() -> None:
    llm = FakeLLM(
        responses=[
            _respond(
                "",
                _TC("t1", "fake_slow", {}),
                _TC("t2", "fake_ok", {"x": "a"}),
                _TC("t3", "fake_slow", {}),
            ),
            _respond("done", None),
        ]
    )
    agent = _agent(llm)
    start = time.monotonic()
    reply, history = agent.run("parallel")
    elapsed = time.monotonic() - start
    assert reply == "done"
    # Two 0.3s sleeps in parallel should take ~0.3s, not ~0.6s.
    assert elapsed < 0.55
    tool_msgs = [m for m in history if m["role"] == "tool"]
    assert [m["content"] for m in tool_msgs] == ["slow done", "ok:a", "slow done"]


def test_stateful_tools_run_serially_in_order() -> None:
    """browser_navigate is stateful -> must run after/never with parallel ones."""
    order: list[str] = []

    @tool("browser_navigate", "stateful", {})
    def browser_navigate():
        order.append("nav")
        return ToolResult(ok=True, output="navigated")

    @tool("fake_ok2", "stateless", {})
    def fake_ok2():
        order.append("ok")
        time.sleep(0.1)
        return ToolResult(ok=True, output="ok2")

    reg = _registry()
    reg.register(browser_navigate)
    reg.register(fake_ok2)
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "fake_ok2", {}), _TC("t2", "browser_navigate", {})),
            _respond("done", None),
        ]
    )
    agent = Agent(llm, reg, max_iterations=5, nudge=False)
    reply, history = agent.run("mixed")
    assert reply == "done"
    assert order == ["ok", "nav"]
    tool_msgs = [m for m in history if m["role"] == "tool"]
    assert [m["content"] for m in tool_msgs] == ["ok2", "navigated"]


def test_parallel_results_preserve_call_order() -> None:
    llm = FakeLLM(
        responses=[
            _respond(
                "",
                _TC("t1", "fake_slow", {}),
                _TC("t2", "fake_ok", {"x": "first"}),
                _TC("t3", "fake_slow", {}),
            ),
            _respond("done", None),
        ]
    )
    agent = _agent(llm)
    _, history = agent.run("order")
    tool_msgs = [m for m in history if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["t1", "t2", "t3"]


def test_parallel_disabled_runs_sequentially() -> None:
    llm = FakeLLM(
        responses=[
            _respond(
                "",
                _TC("t1", "fake_slow", {}),
                _TC("t2", "fake_slow", {}),
            ),
            _respond("done", None),
        ]
    )
    agent = _agent(llm, parallel_tool_calls=False)
    start = time.monotonic()
    _, history = agent.run("seq")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.55
    assert len([m for m in history if m["role"] == "tool"]) == 2


def test_thread_safe_registry_audit() -> None:
    """Parallel calls hitting _audit must not corrupt the log."""
    import os
    import tempfile

    from james.tools.registry import ToolRegistry as TR

    @tool("tiny", "fast", {})
    def tiny() -> ToolResult:
        return ToolResult(ok=True, output="tiny")

    reg = TR(tools=[tiny], discover_plugins=False)
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["JAMES_AUDIT_LOG"] = os.path.join(tmp, "audit.log")
        try:
            errors = []

            def hammer():
                try:
                    for _ in range(30):
                        reg.execute("tiny", {})
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [threading.Thread(target=hammer) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors
            assert TR.verify_audit_integrity(os.environ["JAMES_AUDIT_LOG"])
        finally:
            os.environ.pop("JAMES_AUDIT_LOG", None)


# --- context compaction -------------------------------------------------------

def test_compaction_triggers_and_preserves_tail() -> None:
    big = "x" * 5000

    @tool("big_ok", "returns big output", {})
    def big_ok() -> ToolResult:
        return ToolResult(ok=True, output=big)

    reg = _registry()
    reg.register(big_ok)
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "big_ok", {})),
            _respond("", _TC("t2", "big_ok", {})),
            _respond("", _TC("t3", "big_ok", {})),
            _respond("", _TC("t4", "big_ok", {})),
            _respond("compact me", None),
        ]
    )
    agent = Agent(llm, reg, max_iterations=10, nudge=False, compact_threshold_chars=10_000)
    reply, history = agent.run("make context big")
    assert reply == "compact me"
    assert agent.compactions >= 1
    # Last tool call must still be present verbatim.
    assert any(
        m["role"] == "tool" and m["content"] == big and m["tool_call_id"] == "t4"
        for m in history
    )
    # The summary message replaced the middle, and the tail is preserved.
    assert any(
        m["role"] == "user" and "Earlier context summary" in str(m.get("content", ""))
        for m in history
    )


def test_compaction_disabled_by_threshold_zero() -> None:
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "fake_ok", {"x": "1"})),
            _respond("no compact", None),
        ]
    )
    agent = _agent(llm, compact_threshold_chars=0)
    reply, _ = agent.run("hi")
    assert reply == "no compact"
    assert agent.compactions == 0


def test_max_tools_clips_schema_list() -> None:
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "fake_ok", {"x": "1"})),
            _respond("done", None),
        ]
    )
    agent = _agent(llm, max_tools=1)
    reply, _ = agent.run("clipped")
    assert reply == "done"
    assert len(_registry().schemas()) > 1  # sanity: registry really has more tools
    assert len(llm.sent_tools[0]) == 1


def test_max_tools_no_clip_when_none() -> None:
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "fake_ok", {"x": "1"})),
            _respond("done", None),
        ]
    )
    agent = _agent(llm, max_tools=None)
    _, _ = agent.run("all")
    assert len(llm.sent_tools[0]) == len(_registry().schemas())


def test_extract_links_function_still_exposed() -> None:
    """extract_links stays available as the engine behind fetch_url(include_links)."""
    from james.tools.web_tools import extract_links

    html = '<a href="/x">X</a>'
    assert extract_links(html, "https://e.com/", limit=5) == ["https://e.com/x"]


# --- web tools (offline, unit level) ------------------------------------------

def test_extract_main_text_strips_nav() -> None:
    from james.tools.web_tools import extract_main_text

    html = """
    <html><body>
      <nav>Home About Contact</nav>
      <aside class="sidebar">ads everywhere</aside>
      <article>
        <h1>Title</h1>
        <p>The real content paragraph one.</p>
        <p>And a second real paragraph.</p>
      </article>
      <footer>Copyright</footer>
    </body></html>
    """
    text = extract_main_text(html)
    assert "The real content paragraph one." in text
    assert "ads everywhere" not in text
    assert "Home About Contact" not in text
    assert "Copyright" not in text


def test_extract_links_dedupes_and_absolutizes() -> None:
    from james.tools.web_tools import extract_links

    html = """
    <a href="/a">A</a>
    <a href="https://example.com/a">dup A</a>
    <a href="#frag">frag</a>
    <a href="mailto:x@y.z">mail</a>
    <a href="/b">B</a>
    """
    links = extract_links(html, "https://example.com/start", limit=10)
    assert links == ["https://example.com/a", "https://example.com/b"]


def test_search_falls_back_to_ddg_without_keys(monkeypatch) -> None:
    """No API keys configured -> engine=auto must use the DDG path."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    import requests

    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        assert "duckduckgo" in url

        class _R:
            text = '<div class="result"><a class="result__a" href="https://ddg.example/r">DDG Result</a></div>'
            status_code = 200

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr(requests, "post", fake_post)
    from james.tools.web_tools import web_search

    result = web_search.run(query="hello world", max_results=5)
    assert result.ok
    assert calls["n"] == 1
    assert "DDG Result" in result.output


def test_web_search_uses_tavily_when_key_present(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    import requests

    calls = {"post": 0, "get": 0}

    def fake_post(url, **kwargs):
        calls["post"] += 1
        assert "tavily" in url

        class _R:
            def json(self):
                return {"results": [{"title": "T", "url": "https://t.example", "content": "snippet"}]}

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr(requests, "post", fake_post)
    from james.tools.web_tools import web_search

    result = web_search.run(query="q", max_results=5)
    assert result.ok
    assert calls["post"] == 1
    assert "https://t.example" in result.output


def test_fetch_url_js_shell_falls_back_to_playwright(monkeypatch) -> None:
    """A near-empty SPA shell (id=app + scripts) triggers a headless render."""
    import james.tools.web_tools as wt

    rendered_html = (
        '<html><body><div id="app"><article><h1>Loaded</h1>'
        "<p>Real content rendered by JS at runtime.</p></article></div></body></html>"
    )
    shell_html = '<html><body><div id="app"></div><script src="bundle.js"></script></body></html>'
    calls = {"render": 0}

    class _FakeResp:
        text = shell_html
        url = "https://spa.example/"

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=25):
        return _FakeResp()

    def fake_render(url, timeout_ms=25000):
        calls["render"] += 1
        return rendered_html

    monkeypatch.setattr(wt, "_get", fake_get)
    monkeypatch.setattr(wt, "_render_with_playwright", fake_render)
    from james.tools.web_tools import fetch_url

    result = fetch_url.run(url="https://spa.example/")
    assert result.ok
    assert calls["render"] == 1
    assert "Real content rendered by JS at runtime." in result.output


def test_fetch_url_no_playwright_when_text_rich(monkeypatch) -> None:
    import james.tools.web_tools as wt

    calls = {"render": 0}

    class _FakeResp:
        text = "<html><body><article>" + "<p>Long paragraph with real content.</p>" * 10 + "</article></body></html>"
        url = "https://plain.example/"

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=25):
        return _FakeResp()

    def fake_render(url, timeout_ms=25000):
        calls["render"] += 1
        return ""

    monkeypatch.setattr(wt, "_get", fake_get)
    monkeypatch.setattr(wt, "_render_with_playwright", fake_render)
    from james.tools.web_tools import fetch_url

    result = fetch_url.run(url="https://plain.example/")
    assert result.ok
    assert calls["render"] == 0
    assert "Long paragraph with real content." in result.output


# --- multimodal passthrough ---------------------------------------------------

def test_agent_forwards_images_to_llm() -> None:
    llm = FakeLLM(
        responses=[
            _respond("", _TC("t1", "fake_ok", {"x": "1"})),
            _respond("seen it", None),
        ]
    )
    agent = _agent(llm)
    reply, _ = agent.run("what is in this image?", images=["img1.png", "data:image/png;base64,AAAA"])
    assert reply == "seen it"
    assert llm.sent_tools[0] is not None  # first call went out with tools
    assert llm.sent_images[0] == ["img1.png", "data:image/png;base64,AAAA"]


def test_image_payload_helpers() -> None:
    from james.llm.providers import _image_payload

    mime, b64 = _image_payload("data:image/jpeg;base64,AAAA")
    assert (mime, b64) == ("image/jpeg", "AAAA")
    mime, b64 = _image_payload("data:image/png;base64,BBBB")
    assert (mime, b64) == ("image/png", "BBBB")


def test_anthropic_provider_attaches_images() -> None:
    pytest.importorskip("anthropic")
    from james.llm.providers import AnthropicProvider

    p = AnthropicProvider(api_key="test-key", model="claude-x")
    msgs = [
        {"role": "user", "content": "what is this?"},
        {"role": "assistant", "content": "checking"},
        {"role": "user", "content": "and now this"},
    ]
    out = p._attach_images(msgs, ["img.png"])
    last = out[-1]
    assert isinstance(last["content"], list)
    assert last["content"][0] == {"type": "text", "text": "and now this"}
    assert last["content"][1]["type"] == "image"
    assert last["content"][1]["source"]["type"] == "base64"


def test_gemini_provider_attaches_images() -> None:
    pytest.importorskip("google.genai")
    from james.llm.providers import GeminiProvider

    p = GeminiProvider(api_key="test-key", model="gemini-x")
    msgs = [
        {"role": "user", "content": "what is this?"},
        {"role": "user", "content": "look at this"},
    ]
    contents = p._to_gemini_contents(msgs, ["data:image/png;base64,AAAA"])
    last = contents[-1]
    parts = last.parts
    assert any(getattr(part, "inline_data", None) is not None for part in parts)
    inline = next(part.inline_data for part in parts if getattr(part, "inline_data", None))
    assert inline.mime_type == "image/png"
