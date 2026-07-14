"""Self-diagnostic checks — `james doctor`.

Surfaces the most common setup problems (missing deps, missing keys, no mic,
unwritable workspace, missing browser) in one PASS/WARN/FAIL report.
"""
from __future__ import annotations

import sys

from ..config import settings


def _line(mark: str, name: str, detail: str = "") -> str:
    return f"[{mark}] {name}" + (f" — {detail}" if detail else "")


def run_diagnostics() -> str:
    out: list[str] = []
    out.append("🤖 JAMES doctor\n")

    # Python
    py = sys.version.split()[0]
    out.append(_line("PASS", f"Python {py}", "3.10+ recommended"))

    # Core deps
    for mod in ["dotenv", "rich", "requests", "bs4"]:
        try:
            __import__(mod)
            out.append(_line("PASS", f"import {mod}"))
        except Exception:
            out.append(_line("FAIL", f"import {mod}", "pip install -r requirements.txt"))

    # Provider SDKs
    sdk_map = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "google.generativeai": "Gemini",
        "mcp": "MCP client",
    }
    for mod, label in sdk_map.items():
        try:
            __import__(mod)
            out.append(_line("PASS", f"{label} SDK"))
        except Exception:
            out.append(_line("WARN", f"{label} SDK", "optional — install to enable that provider"))

    # Document + browser + voice + ui
    extra = {
        "docx": "Word docs",
        "pptx": "PowerPoint",
        "reportlab": "PDF",
        "playwright": "Browser automation",
        "pyautogui": "Desktop control",
        "plyer": "Notifications",
        "PyQt5": "Orb GUI",
        "sentence_transformers": "Semantic memory",
    }
    for mod, label in extra.items():
        try:
            __import__(mod)
            out.append(_line("PASS", f"{label} ({mod})"))
        except Exception:
            out.append(_line("WARN", f"{label} ({mod})", "optional — see README"))

    # API key for selected provider
    if settings.llm.api_key:
        out.append(_line("PASS", f"API key set for '{settings.llm.provider}'"))
    else:
        out.append(_line("WARN", f"No API key for '{settings.llm.provider}'",
                         "set it in .env, or use LLM_PROVIDER=custom + Ollama"))

    # Failover
    if settings.llm.failover:
        out.append(_line("PASS", f"Failover configured: {', '.join(settings.llm.failover)}"))
    else:
        out.append(_line("WARN", "No failover configured", "set LLM_FAILOVER for resilience"))

    # Mic
    try:
        import speech_recognition as sr
        mics = sr.Microphone.list_microphone_names()
        out.append(_line("PASS", f"Microphone available ({len(mics)} found)") if mics
                   else _line("WARN", "No microphone detected"))
    except Exception as exc:
        out.append(_line("WARN", "Mic check skipped", str(exc)[:80]))

    # .env
    from pathlib import Path
    if Path(".env").exists():
        out.append(_line("PASS", ".env present"))
    else:
        out.append(_line("WARN", ".env missing", "cp .env.example .env"))

    # Workspace writable
    try:
        settings.assistant.workspace_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.assistant.workspace_dir / ".probe"
        probe.write_text("ok")
        probe.unlink()
        out.append(_line("PASS", f"Workspace writable ({settings.assistant.workspace_dir})"))
    except Exception as exc:
        out.append(_line("FAIL", "Workspace not writable", str(exc)[:80]))

    # Browser (Playwright chromium)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).stop()
        out.append(_line("PASS", "Playwright Chromium ready"))
    except Exception as exc:
        out.append(_line("WARN", "Browser not ready", "pip install playwright && playwright install chromium"))

    return "\n".join(out)
