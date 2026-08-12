"""Phase-5 tests for the Playwright browser tools (mocked â€” no real browser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from james.tools import browser_tools


class FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[tuple] = []
        self.clicks: list[str] = []
        self.fills: list[tuple] = []
        self.extract = "Page text content"
        self.screenshot_path = None
        self.title_value = "Fake Title"

    def goto(self, url: str, **kw) -> None:
        self.goto_calls.append((url, kw))

    def title(self) -> str:
        return self.title_value

    def click(self, target: str, **kw) -> None:
        self.clicks.append(target)

    def get_by_text(self, text, exact=False):
        return _FakeLocator(self, ("text", text))

    def get_by_placeholder(self, text, exact=False):
        return _FakeLocator(self, ("placeholder", text))

    def fill(self, selector: str, text: str) -> None:
        self.fills.append((selector, text))

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, ("css", selector))

    def inner_text(self) -> str:
        return self.extract

    def screenshot(self, path: str) -> None:
        self.screenshot_path = path


class _FakeLocator:
    def __init__(self, page: FakePage, kind: tuple) -> None:
        self.page = page
        self.kind = kind

    @property
    def first(self) -> _FakeLocator:
        return self

    def click(self, **kw) -> None:
        self.page.clicks.append(str(self.kind[1]))

    def fill(self, text: str) -> None:
        self.page.fills.append((str(self.kind[1]), text))

    def inner_text(self) -> str:
        return self.page.extract


@pytest.fixture
def fake_page(monkeypatch: pytest.MonkeyPatch) -> FakePage:
    page = FakePage()
    monkeypatch.setattr(browser_tools, "_page", page)
    monkeypatch.setattr(browser_tools, "_browser", object())
    return page


def test_browser_navigate(fake_page: FakePage) -> None:
    result = browser_tools.browser_navigate.run(url="https://example.com")
    assert result.ok is True
    assert fake_page.goto_calls == [
        ("https://example.com", {"wait_until": "domcontentloaded", "timeout": 30000})
    ]
    assert "Fake Title" in result.output


def test_browser_click_by_selector_then_text(fake_page: FakePage) -> None:
    def fail_then_ok(target, **kw):
        if not fail_then_ok.failed:
            fail_then_ok.failed = True
            raise RuntimeError("not found")
        fake_page.clicks.append(target)

    fail_then_ok.failed = False
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(fake_page, "click", fail_then_ok)
    try:
        result = browser_tools.browser_click.run(target="Submit")
        assert result.ok is True
        assert fake_page.clicks[-1] == "Submit"
    finally:
        monkeypatch.undo()


def test_browser_type_with_placeholder_fallback(fake_page: FakePage) -> None:
    def fail_then_ok(selector, text, **kw):
        if not fail_then_ok.failed:
            fail_then_ok.failed = True
            raise RuntimeError("no selector")
        fake_page.fills.append((selector, text))

    fail_then_ok.failed = False
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(fake_page, "fill", fail_then_ok)
    try:
        result = browser_tools.browser_type.run(selector="Enter email", text="a@b.c")
        assert result.ok is True
        assert ("Enter email", "a@b.c") in fake_page.fills
    finally:
        monkeypatch.undo()


def test_browser_extract_full_page_and_selector(fake_page: FakePage) -> None:
    result = browser_tools.browser_extract.run()
    assert result.ok is True
    assert result.output == "Page text content"
    result = browser_tools.browser_extract.run(selector="#main")
    assert result.ok is True


def test_browser_screenshot_saves_to_workspace(
    fake_page: FakePage, isolated_workspace: Path
) -> None:
    from james.config import settings

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings.assistant, "workspace_dir", isolated_workspace)
    try:
        result = browser_tools.browser_screenshot.run(filename="shot.png")
        assert result.ok is True
        assert fake_page.screenshot_path is not None
        assert str(isolated_workspace) in fake_page.screenshot_path
    finally:
        monkeypatch.undo()


def test_browser_health_when_running(fake_page: FakePage) -> None:
    result = browser_tools.browser_health.run()
    assert result.ok is True
    assert "healthy" in result.output


def test_browser_health_when_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_tools, "_page", None)
    monkeypatch.setattr(browser_tools, "_browser", None)
    result = browser_tools.browser_health.run()
    assert result.ok is False


def test_browser_close_resets_state(fake_page: FakePage) -> None:
    result = browser_tools.browser_close.run()
    assert result.ok is True
    assert browser_tools._page is None
    assert browser_tools._browser is None


def test_browser_launch_failure_counts_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_tools, "_page", None)
    monkeypatch.setattr(browser_tools, "_browser", None)
    monkeypatch.setattr(browser_tools, "_browser_errors", 0)

    def boom(*a, **k):
        raise RuntimeError("no playwright installed")

    monkeypatch.setattr(browser_tools.sync_playwright, "start", boom) if hasattr(
        browser_tools, "sync_playwright"
    ) else None

    # Simulate the import+launch failure by patching _get_page entirely.
    def failing_get_page():
        browser_tools._browser_errors += 1
        raise RuntimeError(f"Browser launch failed ({browser_tools._browser_errors}/3): nope")

    monkeypatch.setattr(browser_tools, "_get_page", failing_get_page)
    result = browser_tools.browser_navigate.run(url="https://example.com")
    assert result.ok is False
    assert "Navigation failed" in result.output


def test_browser_error_exhaustion_blocks_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_tools, "_page", None)
    monkeypatch.setattr(browser_tools, "_browser", None)
    monkeypatch.setattr(browser_tools, "_browser_errors", 3)
    result = browser_tools.browser_navigate.run(url="https://example.com")
    assert result.ok is False
    assert "restart the browser" in result.output


def test_browser_tools_registered_in_registry() -> None:
    from james.tools.registry import ALL_TOOLS

    names = {t.name for t in ALL_TOOLS}
    assert {
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_extract",
        "browser_screenshot",
        "browser_health",
        "browser_close",
    } <= names
