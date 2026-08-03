"""Entry point: `james` (desktop app), `james --text` (CLI), or `python -m james`."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import settings
from .core.assistant import Assistant

PROVIDER_HELP = (
    "openai|anthropic|gemini|openrouter|groq|mistral|xai|deepseek|"
    "together|cerebras|cohere|custom"
)


def _banner() -> str:
    return r"""
       ___  ___  ___  ___  ___
      | | | | | | | | | | | |
      | |_| | |_| | |_| | | |
      |  _  |  _  |  _  | | |
      | | | | | | | | | | | |
      |_| |_|_| |_|_| |_| |_|   Just A Modular Executive System
"""


def print_banner() -> None:
    print(_banner(), flush=True)


def _ui_available() -> bool:
    try:
        import PyQt5  # noqa: F401

        return True
    except Exception:
        return False


def _launch_desktop() -> int:
    from james.ui import run_ui

    return run_ui()


def _scaffold_tool(name: str) -> int:
    from james.sdk import create_plugin

    try:
        path = create_plugin(name)
    except FileExistsError as exc:
        print(f"[!] {exc}")
        return 1
    print(f"[+] Created plugin scaffold at {path}")
    print("    Edit it, then run: james --check")
    return 0


def _run_diagnostics() -> int:
    from james.core.doctor import run_diagnostics

    text = run_diagnostics()
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="james",
        description="JAMES — your open-source, voice-first assistant.\n\n"
        "No arguments launches the desktop app. Use --text or --voice for the terminal.",
    )
    parser.add_argument(
        "--version", action="version", version=f"james {__version__}"
    )
    parser.add_argument("--text", action="store_true", help="Run the terminal CLI in text-only mode (no microphone).")
    parser.add_argument("--voice", action="store_true", help="Run the terminal CLI in voice mode.")
    parser.add_argument("--ui", action="store_true", help="Force the desktop app (default when no mode is given).")
    parser.add_argument("--provider", help=f"Override LLM_PROVIDER ({PROVIDER_HELP}).")
    parser.add_argument("--model", help="Override the model id.")
    parser.add_argument("--check", action="store_true", help="Validate configuration and exit.")
    parser.add_argument("--new-tool", metavar="NAME", help="Scaffold a new plugin tool file in ./plugins/.")
    parser.add_argument("--web-dashboard", action="store_true", help="Launch the web-based dashboard.")
    parser.add_argument("--eval", metavar="SUITE", help="Run a benchmark suite and print results.")
    parser.add_argument("--offline", action="store_true", help="Privacy mode: block ALL non-local network egress (audited).")
    parser.add_argument("--doctor", action="store_true", help="Run self-diagnostic checks and exit.")
    parser.add_argument("--setup", action="store_true", help="Run the interactive first-run setup wizard.")
    parser.add_argument("--session", metavar="NAME", help="Start or resume a named conversation session.")
    parser.add_argument("command", nargs="?", help="Optional command: 'doctor'.")
    args = parser.parse_args(argv)

    if args.new_tool:
        return _scaffold_tool(args.new_tool)

    if args.setup:
        from .onboarding import setup_cmd

        return setup_cmd()

    if args.doctor or args.command == "doctor":
        return _run_diagnostics()

    if args.provider:
        settings.llm.provider = args.provider.lower()
    if args.model:
        settings.llm.model = args.model
    if args.text:
        settings.voice.enabled = False
        settings.voice.stt_provider = "none"
    if args.voice:
        settings.voice.enabled = True
        settings.voice.stt_provider = settings.voice.stt_provider or "whisper_local"

    if args.web_dashboard:
        from james.ui.dashboard import start_dashboard

        start_dashboard()
        print("Dashboard running on http://127.0.0.1:8123")
        print("Press Ctrl+C to stop.")
        try:
            while True:
                import time

                time.sleep(1)
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
            return 0

    if args.eval:
        from james.evaluation import run_benchmark

        suite_name = args.eval
        tasks = [{"description": suite_name}]
        result = run_benchmark(suite_name, tasks, lambda desc, **kw: ("completed", []))
        print(json.dumps(result, indent=2))
        return 0

    # Desktop app is the default experience. --ui forces it; if PyQt5 is not
    # installed we fall back to the text CLI with a helpful hint. Other explicit
    # actions (--check, --eval, --web-dashboard, ...) always take priority.
    want_desktop = args.ui or not (
        args.text
        or args.voice
        or args.check
        or args.eval
        or args.web_dashboard
        or args.session
        or args.offline
    )
    if want_desktop and _ui_available():
        return _launch_desktop()

    if want_desktop:
        print(
            "\n[!] The desktop app needs PyQt5, which is not installed.\n"
            "    Install it with:  pip install 'james-assistant[ui]'\n"
            "    Then run `james` again. Starting text mode instead.\n"
        )

    print_banner()

    if args.check:
        keys = {
            "provider": settings.llm.provider,
            "model": settings.llm.model,
            "voice_enabled": settings.voice.enabled,
            "stt": settings.voice.stt_provider,
            "tts": settings.voice.tts_provider,
            "workspace": str(settings.assistant.workspace_dir),
            "api_key_set": bool(settings.llm.api_key),
        }
        for k, v in keys.items():
            print(f"  {k:>14}: {v}")
        if not keys["api_key_set"]:
            print("\n[!] No API key set for the selected provider. Copy .env.example to .env and fill it in.")
        return 0

    if not settings.llm.api_key:
        from .onboarding import env_exists, run_onboarding

        if not env_exists():
            print(
                "\n[!] No .env found and no API key configured.\n"
                "    This looks like a first run — let's set you up."
            )
            try:
                run_onboarding()
            except (EOFError, KeyboardInterrupt):
                print("\nSetup cancelled. Run `james --setup` anytime.")
                return 0
        else:
            print(
                "\n[!] No API key configured for provider "
                f"'{settings.llm.provider}'.\n"
                "    Edit your .env and set the relevant key, or use a local model "
                "(LLM_PROVIDER=custom). Run `james --setup` to re-run the wizard."
            )
        return 1

    if args.offline:
        settings.assistant.offline_mode = True
    if settings.assistant.offline_mode:
        from .core.guard import install_offline_guard

        install_offline_guard()

    try:
        assistant = Assistant(session=args.session)
        assistant.run()
    except KeyboardInterrupt:
        print("\nGoodbye.")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
