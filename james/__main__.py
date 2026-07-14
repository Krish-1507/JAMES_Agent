"""Entry point: `python -m james` or `james` after install."""
from __future__ import annotations

import argparse
import sys

from .config import settings
from .core.assistant import Assistant


def print_banner() -> None:
    print(
        r"""
       ___  ___  ___  ___  ___
      | | | | | | | | | | | |
      | |_| | |_| | |_| | | |
      |  _  |  _  |  _  | | |
      | | | | | | | | | | | |
      |_| |_|_| |_|_| |_| |_|   Just A Modular Executive System
""",
        flush=True,
    )


def _scaffold_tool(name: str) -> int:
    import re
    from pathlib import Path

    safe = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_") or "my_tool"
    folder = Path("plugins")
    folder.mkdir(exist_ok=True)
    path = folder / f"{safe}.py"
    if path.exists():
        print(f"[!] {path} already exists.")
        return 1
    template = f'''"""Community plugin: {safe}."""
from james.tools.base import tool


@tool(
    "{safe}",
    "Describe what this tool does.",
    {{
        "input": {{"type": "string", "description": "Describe the input."}},
    }},
    required=["input"],
)
def {safe}(input: str):
    # TODO: implement the capability here.
    return f"Processed: {{input}}"
'''
    path.write_text(template, encoding="utf-8")
    print(f"[+] Created plugin scaffold at {path}")
    print(f"    Edit it, then run: python -m james --check")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="james", description="JAMES — your open-source, voice-first JARVIS."
    )
    parser.add_argument("--text", action="store_true", help="Force text-only mode (no microphone).")
    parser.add_argument("--voice", action="store_true", help="Force voice mode.")
    parser.add_argument("--provider", help="Override LLM_PROVIDER (openai|anthropic|gemini|openrouter|groq|custom).")
    parser.add_argument("--model", help="Override the model id.")
    parser.add_argument("--check", action="store_true", help="Validate configuration and exit.")
    parser.add_argument("--new-tool", metavar="NAME", help="Scaffold a new plugin tool file in ./plugins/.")
    parser.add_argument("--ui", action="store_true", help="Launch the optional PyQt5 orb GUI.")
    parser.add_argument("--doctor", action="store_true", help="Run self-diagnostic checks and exit.")
    parser.add_argument("command", nargs="?", help="Optional command: 'doctor'.")
    args = parser.parse_args(argv)

    if args.new_tool:
        return _scaffold_tool(args.new_tool)

    if args.doctor or args.command == "doctor":
        from james.core.doctor import run_diagnostics

        text = run_diagnostics()
        try:
            print(text)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(text.encode("utf-8", "replace"))
        return 0

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

    if args.ui:
        from james.ui import run_ui

        return run_ui()

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
        print(
            "\n[!] No API key configured for provider "
            f"'{settings.llm.provider}'.\n"
            "    Copy .env.example to .env and set the relevant key, or use a local model (LLM_PROVIDER=custom)."
        )
        return 1

    try:
        assistant = Assistant()
        assistant.run()
    except KeyboardInterrupt:
        print("\nGoodbye.")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
