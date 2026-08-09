"""Entry point: `james` (desktop app), `james --text` (CLI), or `python -m james`."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import settings
from .core.assistant import Assistant

PROVIDER_HELP = (
    "openai|anthropic|gemini|openrouter|groq|mistral|xai|deepseek|together|cerebras|cohere|custom"
)


def _banner() -> str:
    return r"""
     _   _    __  __ _____ ____
    | | / \  |  \/  | ____/ ___|
 _  | |/ _ \ | |\/| |  _| \___ \
| |_| / ___ \| |  | | |___ ___) |
 \___/_/   \_\_|  |_|_____|____/
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


def _run_gaia_eval(args) -> int:
    """Run the GAIA validation harness. Returns the process exit code."""
    from pathlib import Path

    from james.evaluation.gaia import (
        download_gaia,
        load_gaia_metadata,
        run_gaia_suite,
    )

    eval_dir: Path | None = Path(args.eval_dir) if args.eval_dir else None
    if eval_dir is not None and not (eval_dir / "metadata.jsonl").exists():
        alt = eval_dir / "2023" / "validation" / "metadata.jsonl"
        if not alt.exists():
            print(f"[!] No metadata.jsonl found under {eval_dir}")
            return 2

    if eval_dir is None:
        if not args.download_gaia:
            print(
                "[!] No GAIA dataset found. Either pass --eval-dir <folder> pointing at a\n"
                "    GAIA validation folder (containing metadata.jsonl), or add\n"
                "    --download-gaia to fetch the public validation set first."
            )
            return 2
        eval_dir = download_gaia(
            settings.assistant.workspace_dir / "eval_data",
            limit=args.eval_limit or 0,
        )
        print(f"[+] GAIA validation set downloaded to {eval_dir}")

    tasks = load_gaia_metadata(eval_dir)
    if args.eval_level:
        tasks = [t for t in tasks if t.level == args.eval_level]
    if args.eval_limit:
        tasks = tasks[: args.eval_limit]
    if not tasks:
        print("[!] No GAIA tasks loaded from the dataset folder.")
        return 2

    print(f"[+] Running {len(tasks)} GAIA validation tasks…")
    report = run_gaia_suite(tasks, max_iterations=args.eval_iterations)
    print(
        f"[+] PASS {report['passed']}/{report['total']} "
        f"({report['pass_rate']:.1%}) — avg {report['avg_iterations']} "
        f"iterations, {report['avg_tool_calls']} tool calls per task"
    )
    for level, stats in sorted(report["by_level"].items()):
        print(f"    Level {level}: {stats['passed']}/{stats['total']} ({stats['pass_rate']:.1%})")
    print(json.dumps(report, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="james",
        description="JAMES — your open-source, voice-first assistant.\n\n"
        "No arguments launches the desktop app. Use --text or --voice for the terminal.",
    )
    parser.add_argument("--version", action="version", version=f"james {__version__}")
    parser.add_argument(
        "--text",
        action="store_true",
        help="Run the terminal CLI in text-only mode (no microphone).",
    )
    parser.add_argument("--voice", action="store_true", help="Run the terminal CLI in voice mode.")
    parser.add_argument(
        "--ui", action="store_true", help="Force the desktop app (default when no mode is given)."
    )
    parser.add_argument("--provider", help=f"Override LLM_PROVIDER ({PROVIDER_HELP}).")
    parser.add_argument("--model", help="Override the model id.")
    parser.add_argument("--check", action="store_true", help="Validate configuration and exit.")
    parser.add_argument(
        "--new-tool", metavar="NAME", help="Scaffold a new plugin tool file in ./plugins/."
    )
    parser.add_argument(
        "--web-dashboard", action="store_true", help="Launch the web-based dashboard."
    )
    parser.add_argument("--eval", metavar="SUITE", help="Run a benchmark suite: gaia or smoke.")
    parser.add_argument(
        "--eval-dir",
        metavar="DIR",
        help="Path to a local GAIA validation folder containing metadata.jsonl.",
    )
    parser.add_argument(
        "--eval-limit",
        metavar="N",
        type=int,
        help="Run only the first N tasks (cheap smoke runs / CI).",
    )
    parser.add_argument(
        "--eval-iterations",
        metavar="N",
        type=int,
        default=20,
        help="Max agent iterations per task (default 20; lower saves tokens).",
    )
    parser.add_argument(
        "--eval-level",
        metavar="N",
        type=int,
        choices=[1, 2, 3],
        help="Only run tasks of this difficulty level (1-3).",
    )
    parser.add_argument(
        "--download-gaia",
        action="store_true",
        help="Download the public GAIA validation set before evaluating.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Privacy mode: block ALL non-local network egress (audited).",
    )
    parser.add_argument(
        "--doctor", action="store_true", help="Run self-diagnostic checks and exit."
    )
    parser.add_argument(
        "--setup", action="store_true", help="Run the interactive first-run setup wizard."
    )
    parser.add_argument(
        "--session", metavar="NAME", help="Start or resume a named conversation session."
    )
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
        from james.evaluation import Evaluator

        suite_name = args.eval.lower()
        if suite_name == "gaia":
            return _run_gaia_eval(args)
        if suite_name == "smoke":
            # Offline pipeline smoke test: no API key, deterministic fake agent.
            from dataclasses import asdict

            evaluator = Evaluator()
            tasks = [
                {"description": "What is 2 + 2?", "metadata": {"answer": "4"}},
                {"description": "Capital of France?", "metadata": {"answer": "Paris"}},
            ]
            results = []

            def _fake_agent(description: str, **kw):
                answer = next(
                    (t["metadata"]["answer"] for t in tasks if t["description"] == description),
                    "",
                )
                return (answer, {"tool_calls": 2, "iterations": 1})

            for task in tasks:
                results.append(
                    evaluator.run_task(task["description"], _fake_agent, metadata=task["metadata"])
                )
            summary = evaluator.summary()
            summary["results"] = [asdict(r) for r in results]
            print(json.dumps(summary, indent=2))
            return 0
        print(f"[!] Unknown suite: {args.eval}. Available suites: gaia, smoke")
        return 2

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
            print(
                "\n[!] No API key set for the selected provider. Copy .env.example to .env and fill it in."
            )
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
