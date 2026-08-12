"""Coverage expansion for forge_tools, web_tools and mcp_tools.

These tests exercise the branches that the phase-1/phase-2 suites leave
uncovered (offline-mode paths, error branches, and every tool lifecycle
path of Skill Forge), closing the security-module coverage gate
(scripts/coverage_gate.py -> >= 80% per module).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Skill Forge: forge_tools.py
# ---------------------------------------------------------------------------


def test_forge_safe_name_normalization() -> None:
    from james.tools.forge_tools import _safe_name

    assert _safe_name("My Skill!") == "my_skill"
    assert _safe_name("!!!") == "skill"


def test_forge_scan_dangerous_imports_variants() -> None:
    from james.tools.forge_tools import _scan_for_dangerous_imports

    issues = _scan_for_dangerous_imports("import os\nimport sys")
    assert any("os" in i for i in issues)
    assert any("sys" in i for i in issues)
    assert _scan_for_dangerous_imports("import not_a_module") != []
    assert _scan_for_dangerous_imports("from .x import tool") != []
    assert _scan_for_dangerous_imports("from james.tools.base import tool") == []
    assert _scan_for_dangerous_imports("def broken(:\n") != []  # syntax error path


def test_forge_literal_only_rejects_expressions() -> None:
    import ast

    from james.tools.forge_tools import _literal_only

    assert _literal_only(ast.parse("3").body[0].value) is True
    assert _literal_only(ast.parse("len([1])").body[0].value) is False


def test_forge_validate_skill_ast_rejections() -> None:
    from james.tools.forge_tools import _validate_skill_ast

    assert _validate_skill_ast("x" * (64 * 1024 + 1)) != []
    assert _validate_skill_ast("def broken(:\n") != []
    assert _validate_skill_ast("print('hi')") != []  # non-function at module scope
    assert _validate_skill_ast("def f():\n    pass\n") != []  # no @tool decorator
    assert (
        _validate_skill_ast("@tool('t', {})\ndef f(secret=__import__('os')):\n    return 1\n") != []
    )  # non-literal default
    assert _validate_skill_ast("def f():\n    return 1\n") != []  # no tool fn found
    assert (
        _validate_skill_ast("@tool('t', {})\ndef f():\n    while True:\n        pass\n") != []
    )  # banned While node
    assert (
        _validate_skill_ast("@tool('t', {})\ndef f():\n    __import__('os')\n") != []
    )  # dunder + restricted call
    assert (
        _validate_skill_ast('@tool("t", {})\ndef f():\n    x = "very_long" * 2000\n    return x\n')
        == []
    )


def test_forge_validate_skill_ast_large_literals() -> None:
    from james.tools.forge_tools import _validate_skill_ast

    long_str = "@tool('t', {})\ndef f():\n    return " + repr("a" * 10001) + "\n"
    assert _validate_skill_ast(long_str) != []
    big_int = "@tool('t', {})\ndef f():\n    return " + str(10**12) + "\n"
    assert _validate_skill_ast(big_int) != []


def test_forge_load_generated_skill_source_valid_and_invalid() -> None:
    from james.tools.forge_tools import (
        _validate_skill_ast,
        load_generated_skill_source,
    )

    valid = (
        "from james.tools.base import tool, ToolResult\n"
        '@tool("add", "adds two numbers", {"a": {"type": "integer"}, "b": {"type": "integer"}})\n'
        "def add(a, b):\n    return a + b\n"
    )
    module = load_generated_skill_source(valid, "james_skill_test")
    assert module.add.name == "add"
    assert module.add.run(a=2, b=3).ok is True
    assert module.add.run(a=2, b=3).output == "5"
    assert _validate_skill_ast(valid) == []

    with pytest.raises(ValueError):
        load_generated_skill_source("import os\n", "james_skill_test")


def test_forge_load_generated_skill_requires_header(tmp_path: Path) -> None:
    from james.tools.forge_tools import load_generated_skill

    path = tmp_path / "plain.py"
    path.write_text("def f():\n    return 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a generated JAMES skill"):
        load_generated_skill(path)


def test_forge_atomic_write_creates_file(tmp_path: Path) -> None:
    from james.tools.forge_tools import _atomic_write

    target = tmp_path / "skill.py"
    _atomic_write(target, "# hello\n")
    assert target.read_text(encoding="utf-8") == "# hello\n"
    _atomic_write(target, "# replaced\n")
    assert target.read_text(encoding="utf-8") == "# replaced\n"


def test_forge_persist_skill_rejections(plugin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import james.tools.forge_tools as forge

    monkeypatch.setattr("james.tools.plugin_proxy.discover_plugin_tools", lambda path, trusted: [])
    monkeypatch.setattr(forge.settings.assistant, "external_plugins_enabled", True)

    bad = forge._persist_skill("bad", "import os\n")
    assert bad.ok is False and "rejected" in bad.output

    no_tool = forge._persist_skill("nothing", "def f():\n    return 1\n")
    assert no_tool.ok is False and "Skill rejected" in no_tool.output


def test_forge_save_and_conflict_and_list(
    plugin_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import james.tools.forge_tools as forge

    fake_tool = SimpleNamespace(name="add", description="adds", _set=None)

    class FakeDiscovery:
        def __call__(self, path, trusted: bool):
            return [fake_tool]

    monkeypatch.setattr("james.tools.plugin_proxy.discover_plugin_tools", FakeDiscovery())

    code = (
        '@tool("add", "adds", {"a": {"type": "integer"}, "b": {"type": "integer"}})\n'
        "def add(a, b):\n    return a + b\n"
    )
    result = forge.save_skill.run(name="add", code=code, description="sum tool")
    assert result.ok is True
    assert (plugin_dir / "add.py").exists()

    conflict = forge.save_skill.run(name="add", code=code.replace("a + b", "a - b"))
    assert conflict.ok is False and "already exists" in conflict.output

    listing = forge.list_skills.run()
    assert listing.ok is True and "add: sum tool" in listing.output

    # loading a generated skill from disk returns the real module
    loaded = forge.load_generated_skill(plugin_dir / "add.py")
    assert callable(loaded.add.run)
    assert loaded.add.name == "add"


def test_forge_forget_skill_paths(plugin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import james.tools.forge_tools as forge

    missing = forge.forget_skill.run(name="ghost")
    assert missing.ok is False and "No such skill" in missing.output

    (plugin_dir / "trusted.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    refused = forge.forget_skill.run(name="trusted")
    assert refused.ok is False and "Refusing to delete" in refused.output

    fake_tool = SimpleNamespace(name="add", description="adds")
    monkeypatch.setattr(
        "james.tools.plugin_proxy.discover_plugin_tools", lambda p, trusted: [fake_tool]
    )
    monkeypatch.setattr(
        "james.core.isolation.run_isolated",
        lambda *a, **k: {"ok": True, "output": "removed"},
    )
    code = (
        '@tool("add", "adds", {"a": {"type": "integer"}, "b": {"type": "integer"}})\n'
        "def add(a, b):\n    return a + b\n"
    )
    assert forge.save_skill.run(name="add", code=code).ok is True

    def delete_skill(*a, **k):
        (plugin_dir / "add.py").unlink(missing_ok=True)
        return {"ok": True, "output": "removed"}

    monkeypatch.setattr("james.core.isolation.run_isolated", delete_skill)
    forgot = forge.forget_skill.run(name="add")
    assert forgot.ok is True
    assert not (plugin_dir / "add.py").exists()

    monkeypatch.setattr(
        "james.core.isolation.run_isolated",
        lambda *a, **k: {"ok": False, "output": "nope"},
    )
    (plugin_dir / "add.py").write_text("# JAMES-GENERATED-SKILL v1\n", encoding="utf-8")
    failed = forge.forget_skill.run(name="add")
    assert failed.ok is False and "nope" in failed.output


def test_forge_extract_code_variants() -> None:
    from james.tools.forge_tools import _extract_code

    assert _extract_code("text ```python\ncode_here\n``` tail") == "code_here"
    assert _extract_code("def f():\n    pass") == "def f():\n    pass"
    assert _extract_code("plain text") == ""


def test_forge_skill_infos_and_relevance(plugin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import james.tools.forge_tools as forge

    monkeypatch.setattr(forge.settings.assistant, "external_plugins_enabled", True)
    (plugin_dir / "broken.py").write_text("not python (", encoding="utf-8")
    assert forge._skill_infos() == []
    assert forge.get_relevant_skills("anything") == ""

    (plugin_dir / "read_inbox.py").write_text(
        "# JAMES-GENERATED-SKILL v1\n"
        "from james.tools.base import tool, ToolResult\n"
        '@tool("read_inbox", "reads your email", {})\n'
        "def read_inbox():\n    return []\n",
        encoding="utf-8",
    )
    infos = forge._skill_infos()
    assert infos == [
        {
            "name": "read_inbox",
            "description": "reads your email",
            "path": str(plugin_dir / "read_inbox.py"),
        }
    ]

    info = infos[0]
    score = forge._skill_score("read inbox", info)
    assert score > 0.5
    assert forge._skill_score("pizza", info) == 0.0

    hint = forge.get_relevant_skills("read inbox please", top_k=3)
    assert "read_inbox" in hint


def test_forge_derive_name_is_deterministic() -> None:
    from james.tools.forge_tools import _derive_name

    assert _derive_name("Make me a weekly report.") == _derive_name("Make me a weekly report.")
    assert len(_derive_name("...")) > 5


def test_forge_auto_forge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import james.tools.forge_tools as forge

    class FakeLLM:
        def __init__(self, content: str):
            self.content = content

        def chat(self, messages):
            return SimpleNamespace(content=self.content)

    # too little activity -> no forge
    result = forge.auto_forge_from_history(FakeLLM("x"), [])
    assert result.ok is False and "Not enough tool activity" in result.output

    history = [
        {"role": "user", "content": "sum two numbers"},
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "add", "arguments": '{"a": 1, "b": 2}'}},
            ],
        },
        {"role": "tool", "content": "3"},
    ]
    captured: dict = {}

    def fake_persist(name, code, description=""):
        captured["name"] = name
        captured["code"] = code
        captured["description"] = description
        return SimpleNamespace(ok=True, output="saved")

    monkeypatch.setattr(forge, "_persist_skill", fake_persist)
    ok = forge.auto_forge_from_history(FakeLLM("```python\ncode\n```"), history)
    assert ok.ok is True
    assert "sum_two_numbers" in captured["name"]
    assert captured["code"] == "code"

    # LLM failure path
    class BoomLLM:
        def chat(self, messages):
            raise RuntimeError("provider down")

    failed = forge.auto_forge_from_history(BoomLLM(), history)
    assert failed.ok is False and "Auto-forge generation failed" in failed.output

    # empty code path
    empty = forge.auto_forge_from_history(FakeLLM("no code here"), history)
    assert empty.ok is False and "no usable code" in empty.output


# ---------------------------------------------------------------------------
# Web tools: web_tools.py
# ---------------------------------------------------------------------------


def test_web_offline_blocked(monkeypatch: pytest.MonkeyPatch, isolated_workspace: Path) -> None:
    from james.config import settings
    from james.tools.web_tools import _offline_blocked, extract_links_tool, fetch_url, web_search

    monkeypatch.setattr(settings.assistant, "offline_mode", True)
    blocked = _offline_blocked()
    assert blocked is not None and blocked.ok is False
    assert web_search.run(query="x").ok is False
    assert fetch_url.run(url="http://localhost/").ok is False
    assert extract_links_tool.run(url="http://localhost/").ok is False


def test_web_render_with_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    import james.tools.web_tools as wt

    class FakePage:
        def goto(self, url, **kwargs):
            pass

        def content(self):
            return "<html><body>rendered</body></html>"

    class FakeBrowser:
        def __init__(self):
            self.closed = False

        def new_page(self):
            return FakePage()

        def close(self):
            self.closed = True

    class FakeChromium:
        def __init__(self):
            self.browser = FakeBrowser()

        def launch(self, **kwargs):
            return self.browser

    class FakePw:
        def __init__(self):
            self.chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake_sync = types.ModuleType("playwright.sync_api")
    fake_sync.sync_playwright = lambda: FakePw()
    fake_playwright = types.ModuleType("playwright")
    fake_playwright.sync_api = fake_sync
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)
    html = wt._render_with_playwright("https://example.com")
    assert "rendered" in html


def test_web_js_shell_heuristic() -> None:
    from james.tools.web_tools import _looks_like_js_shell

    assert _looks_like_js_shell("<html>hi</html>", "x" * 300) is False
    assert _looks_like_js_shell('<script src="a"></script><div id="app"></div>', "x") is True
    assert _looks_like_js_shell("<script></script>", "x") is False


def test_web_get_and_strip_and_nav(monkeypatch: pytest.MonkeyPatch) -> None:
    from bs4 import BeautifulSoup

    import james.tools.web_tools as wt

    class FakeResp:
        text = "<html><body><p>ok</p></body></html>"

        def raise_for_status(self):
            pass

    class BoomResp:
        def raise_for_status(self):
            raise RuntimeError("400")

    calls: list[tuple] = []
    monkeypatch.setattr(
        wt.requests,
        "get",
        lambda url, headers=None, timeout=25: (calls.append((url, headers, timeout)), FakeResp())[
            1
        ],
    )
    assert wt._get("http://localhost/").text.startswith("<html")
    monkeypatch.setattr(wt.requests, "get", lambda *a, **k: BoomResp())
    with pytest.raises(RuntimeError):
        wt._get("http://localhost/")

    soup = BeautifulSoup("<html><body><script>x</script>keep<p>hi</p></body></html>", "html.parser")
    wt._strip_junk(soup)
    assert "script" not in str(soup)

    assert wt._is_nav_el(BeautifulSoup("<nav></nav>", "html.parser").nav) is True
    assert wt._is_nav_el(BeautifulSoup('<div class="sidebar"></div>', "html.parser").div) is True
    assert wt._is_nav_el(BeautifulSoup('<div id="content"></div>', "html.parser").div) is False


def test_web_select_main_variants() -> None:
    from bs4 import BeautifulSoup

    import james.tools.web_tools as wt

    soup = BeautifulSoup(
        "<html><body><article><p>a</p><p>b</p></article><main><p>c</p></main></body></html>",
        "html.parser",
    )
    main = wt._select_main(soup)
    assert main.name == "article"  # longest semantic candidate wins

    soup = BeautifulSoup(
        "<html><body><div><p>1</p></div><div><p>2</p><p>3</p></div></body></html>",
        "html.parser",
    )
    best = wt._select_main(soup)
    assert best is not None and len(best.find_all("p")) == 2

    assert wt._select_main(BeautifulSoup("<html></html>", "html.parser")) is None


def test_web_extract_main_text_full() -> None:
    from james.tools.web_tools import extract_main_text

    html = (
        "<html><body>"
        "<nav><a href='/x'>nav link</a></nav>"
        '<div class="ad">buy now</div>'
        "<article><h1>Head</h1><p>Body text here.</p></article>"
        "<footer>foot</footer>"
        "</body></html>"
    )
    text = extract_main_text(html)
    assert "Head" in text and "Body text here" in text
    assert "buy now" not in text and "nav link" not in text


def test_web_search_engine_paths(monkeypatch: pytest.MonkeyPatch, isolated_workspace: Path) -> None:
    import james.tools.web_tools as wt

    def fake_tavily(query, max_results):
        return [{"title": "T", "url": "https://t.example", "snippet": "s"}]

    def fake_none(query, max_results):
        return None

    monkeypatch.setattr(wt, "_search_tavily", fake_tavily)
    monkeypatch.setattr(wt, "_search_brave", fake_none)
    monkeypatch.setattr(
        wt, "_search_ddg", lambda q, m: [{"title": "D", "url": "https://d.example", "snippet": ""}]
    )

    result = wt.web_search.run(query="q", engine="tavily")
    assert result.ok is True and "t.example" in result.output

    result = wt.web_search.run(query="q", engine="brave")
    assert result.ok is True and "d.example" in result.output

    result = wt.web_search.run(query="q", engine="auto")
    assert result.ok is True and "t.example" in result.output


def test_web_search_error_and_ddg_result(
    monkeypatch: pytest.MonkeyPatch, isolated_workspace: Path
) -> None:
    import james.tools.web_tools as wt

    monkeypatch.setattr(wt, "_search_tavily", lambda q, m: None)
    monkeypatch.setattr(wt, "_search_brave", lambda q, m: None)

    class FakeResp:
        text = (
            '<html><body><a class="result__a" href="https://ddg.example">Title &amp; More</a>'
            '<a class="result__a" href="https://ddg.example/2">Second</a></body></html>'
        )

        def raise_for_status(self):
            pass

    monkeypatch.setattr(wt.requests, "post", lambda *a, **k: FakeResp())
    result = wt.web_search.run(query="q", engine="duckduckgo", max_results=1)
    assert result.ok is True and "Title & More" in result.output

    monkeypatch.setattr(
        wt.requests, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    failed = wt.web_search.run(query="q", engine="duckduckgo")
    assert failed.ok is False and "Search failed" in failed.output


def test_web_tavily_and_brave_searches(
    monkeypatch: pytest.MonkeyPatch, isolated_workspace: Path
) -> None:
    import james.tools.web_tools as wt

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert wt._search_tavily("q", 5) is None
    assert wt._search_brave("q", 5) is None

    class FakeJsonResp:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    monkeypatch.setenv("TAVILY_API_KEY", "tk")
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return FakeJsonResp({"results": [{"title": "T", "url": "https://t", "content": "c"}]})

    monkeypatch.setattr(wt.requests, "post", fake_post)
    out = wt._search_tavily("q", 3)
    assert out == [{"title": "T", "url": "https://t", "snippet": "c"}]

    with pytest.raises(RuntimeError, match="Tavily error"):
        monkeypatch.setattr(
            wt.requests, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        wt._search_tavily("q", 3)

    monkeypatch.setenv("BRAVE_API_KEY", "bk")
    monkeypatch.setattr(
        wt.requests,
        "get",
        lambda url, params=None, headers=None, timeout=20: FakeJsonResp(
            {"web": {"results": [{"title": "B", "url": "https://b", "description": "d"}]}}
        ),
    )
    brave = wt._search_brave("q", 2)
    assert brave == [{"title": "B", "url": "https://b", "snippet": "d"}]

    with pytest.raises(RuntimeError, match="Brave error"):
        monkeypatch.setattr(
            wt.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
        )
        wt._search_brave("q", 2)


def test_web_format_results_empty_and_snippets() -> None:
    from james.tools.web_tools import _format_results

    assert _format_results([]) == "No results."
    assert "(untitled)" in _format_results([{"url": "https://x"}])
    assert "snip" in _format_results([{"title": "t", "url": "u", "snippet": "snip"}])


def test_web_fetch_url_include_links_and_error(
    monkeypatch: pytest.MonkeyPatch, isolated_workspace: Path
) -> None:
    import james.tools.web_tools as wt

    html = (
        "<html><body><article><p>Main content here.</p>"
        "<a href='https://site.example/a'>A</a><a href='#frag'>skip</a>"
        "</article></body></html>"
    )

    class FakeResp:
        url = "https://site.example/page"
        text = html

        def raise_for_status(self):
            pass

    monkeypatch.setattr(wt, "_get", lambda url, timeout=25: FakeResp())
    result = wt.fetch_url.run(url="https://site.example/page", include_links=True)
    assert result.ok is True
    assert "Main content here" in result.output
    assert "https://site.example/a" in result.output
    assert "#frag" not in result.output  # fragment links are skipped
    assert "[Links on this page]" in result.output


def test_web_fetch_url_extract_links_tool(
    monkeypatch: pytest.MonkeyPatch, isolated_workspace: Path
) -> None:
    import james.tools.web_tools as wt

    class FakeResp:
        url = "https://site.example/path"
        text = (
            "<html><body>"
            "<a href='/same'>Same</a>"
            "<a href='https://other.example/x'>Other</a>"
            "<a href='mailto:a@b.c'>Mail</a>"
            "extra text <p>body</p>"
            "</body></html>"
        )

        def raise_for_status(self):
            pass

    monkeypatch.setattr(wt, "_get", lambda url, timeout=25: FakeResp())
    result = wt.extract_links_tool.run(url="https://site.example/path")
    assert result.ok is True
    assert "https://site.example/same" in result.output
    assert "other.example" not in result.output

    result_all = wt.extract_links_tool.run(url="https://site.example/path", same_domain_only=False)
    assert "https://other.example/x" in result_all.output

    monkeypatch.setattr(
        wt, "_get", lambda url, timeout=25: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    failed = wt.extract_links_tool.run(url="https://site.example/path")
    assert failed.ok is False


def test_web_fetch_url_limits_text(
    monkeypatch: pytest.MonkeyPatch, isolated_workspace: Path
) -> None:
    import james.tools.web_tools as wt

    class FakeResp:
        url = "https://site.example/page"
        text = "<html><body><article><p>" + "word " * 100 + "</p></article></body></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(wt, "_get", lambda url, timeout=25: FakeResp())
    result = wt.fetch_url.run(url="https://site.example/page", max_chars=20)
    assert result.ok is True and len(result.output) <= 20


def test_web_extract_links_skips_and_limit() -> None:
    from james.tools.web_tools import extract_links

    html = (
        "<html><body>"
        "<a href='https://a.example/1'>1</a>"
        "<a href='https://a.example/1'>dup</a>"
        "<a href='//a.example/2'>2</a>"
        "<a href='javascript:void(0)'>js</a>"
        "<a href='tel:123'>tel</a>"
        "<a>no href</a>"
        "</body></html>"
    )
    links = extract_links(html, "https://a.example/", limit=10)
    assert links[0] == "https://a.example/1"
    assert "https://a.example/2" in links  # protocol-relative resolved
    assert len(links) == 2


# ---------------------------------------------------------------------------
# MCP client: mcp_tools.py
# ---------------------------------------------------------------------------


def test_mcp_run_async_with_running_loop() -> None:
    from james.tools import mcp_tools

    async def fake_coro():
        await asyncio.sleep(0)
        return 42

    async def main():
        return mcp_tools._run_async(fake_coro())

    assert asyncio.run(main()) == 42


def test_mcp_extract_text_variants() -> None:
    from james.tools.mcp_tools import _extract_text

    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="hello"),
            SimpleNamespace(type="image", data=b"\x89PNG"),
            SimpleNamespace(type="text", text="world"),
        ]
    )
    assert _extract_text(result) == "hello\nb'\\x89PNG'\nworld"
    assert _extract_text(SimpleNamespace(content=[])) == ""
    assert _extract_text(SimpleNamespace(content=None)) == ""


def test_mcp_validate_arguments_extra_branches() -> None:
    from james.tools.mcp_tools import _validate_mcp_arguments

    with pytest.raises(ValueError):
        _validate_mcp_arguments(1, "t")  # non-dict
    with pytest.raises(ValueError):
        _validate_mcp_arguments({1: "x"}, "t")  # non-string key
    with pytest.raises(ValueError):
        _validate_mcp_arguments({"a": "x" * 70000}, "t")  # too large
    sanitized = _validate_mcp_arguments({"api_key": "secret", "big": "z" * 12000}, "t")
    assert sanitized["api_key"] == "***REDACTED***"
    assert len(sanitized["big"]) == 10000
    assert _validate_mcp_arguments({"token": 1234}, "t") == {
        "token": 1234
    }  # non-str value untouched


def test_mcp_call_with_fake_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.tools.mcp_tools import MCPServerSpec, call_mcp

    class FakeSession:
        async def call_tool(self, name, args):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"{name}:{args}")])

    async def fake_with_session(spec, async_fn):
        return await async_fn(FakeSession())

    monkeypatch.setattr("james.tools.mcp_tools._with_session", fake_with_session)
    spec = MCPServerSpec(name="srv", transport="http", url="http://localhost:9000")
    assert call_mcp(spec, "math_add", {"a": 1}) == "math_add:{'a': 1}"

    async def boom(spec, async_fn):
        raise RuntimeError("transport down")

    monkeypatch.setattr("james.tools.mcp_tools._with_session", boom)
    with pytest.raises(RuntimeError):
        call_mcp(spec, "x", {})


def test_mcp_tool_init_and_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.tools.mcp_tools import MCPServerSpec, MCPTool

    mcp_tool = SimpleNamespace(
        name="hello-world!",
        description="Says hi",
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )
    spec = MCPServerSpec(name="demo")
    tool = MCPTool(spec, mcp_tool)
    assert tool.name == "mcp_demo_hello_world_"
    assert "Says hi" in tool.description
    assert tool.parameters == {"name": {"type": "string"}}
    assert tool.required == ["name"]

    monkeypatch.setattr("james.tools.mcp_tools.call_mcp", lambda spec, name, kwargs: "result here")
    ok = tool.run(name="james")
    assert ok.ok is True and ok.output == "result here"

    monkeypatch.setattr(
        "james.tools.mcp_tools.call_mcp",
        lambda spec, name, kwargs: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    bad = tool.run(name="james")
    assert bad.ok is False and "failed" in bad.output


def test_mcp_load_configs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import james.tools.mcp_tools as mcp_tools

    mcp_file = tmp_path / "mcp.json"
    monkeypatch.setattr(mcp_tools, "mcp_config_path", lambda: mcp_file)
    monkeypatch.delenv("MCP_SERVERS", raising=False)
    assert mcp_tools.load_mcp_configs() == []

    mcp_file.write_text(
        '{"a": {"name": "svc", "transport": "http", "url": "http://x"}}', encoding="utf-8"
    )
    configs = mcp_tools.load_mcp_configs()
    assert len(configs) == 1 and configs[0].name == "svc"

    mcp_file.write_text('[{"name": "list-svc"}]', encoding="utf-8")
    assert mcp_tools.load_mcp_configs()[0].name == "list-svc"

    mcp_file.write_text("{not json", encoding="utf-8")
    assert mcp_tools.load_mcp_configs() == []  # bad file -> silent skip

    mcp_file.unlink()
    monkeypatch.setenv("MCP_SERVERS", '{"e": {"name": "env-svc"}}')
    assert mcp_tools.load_mcp_configs()[0].name == "env-svc"

    monkeypatch.setenv("MCP_SERVERS", "[nope")
    assert mcp_tools.load_mcp_configs() == []  # bad env -> silent skip


def test_mcp_discover_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    import james.tools.mcp_tools as mcp_tools

    spec = mcp_tools.MCPServerSpec(name="demo")
    fake_mcp_tool = SimpleNamespace(
        name="greet",
        description="hello",
        inputSchema={"type": "object", "properties": {}},
    )

    async def fake_with_session(spec, async_fn):
        class FakeSession:
            async def list_tools(self):
                return SimpleNamespace(tools=[fake_mcp_tool])

        return await async_fn(FakeSession())

    async def fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(mcp_tools, "_with_session", fake_with_session)
    monkeypatch.setattr(mcp_tools, "load_mcp_configs", lambda: [spec])
    monkeypatch.setattr(mcp_tools.asyncio, "wait_for", fake_wait_for)

    tools = mcp_tools.discover_mcp_tools()
    assert len(tools) == 1 and tools[0].name == "mcp_demo_greet"

    async def boom(spec, async_fn):
        raise RuntimeError("cannot connect")

    monkeypatch.setattr(mcp_tools, "_with_session", boom)
    assert mcp_tools.discover_mcp_tools() == []


def test_mcp_run_async_direct() -> None:
    from james.tools.mcp_tools import _run_async

    async def coro():
        return "done"

    assert _run_async(coro()) == "done"
