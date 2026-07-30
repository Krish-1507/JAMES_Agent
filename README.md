# JAMES — Just A Modular Executive System

> Status: **alpha**. JAMES is a local, desktop-first LLM assistant for people who want to experiment with agentic workflows. Do not give it unrestricted access to important data or production systems yet.

JAMES can work with files, documents, browser sessions, desktop controls, reminders, memory, and selected system information. It supports cloud LLM providers and OpenAI-compatible local endpoints such as Ollama.

The project’s goal is simple: make a desktop agent that is useful, inspectable, and safe by default—not an assistant that silently takes control of a machine.

## What works today

- Text and voice interaction, with configurable STT/TTS providers.
- OpenAI, Anthropic, Gemini, OpenRouter, Groq, and OpenAI-compatible providers.
- File, document, browser, memory, scheduling, and desktop-control tools.
- MCP discovery for configured servers.
- Local encrypted conversation-history storage and JSON/Markdown export.
- Optional offline mode that blocks non-loopback network egress.
- A lightweight PyQt orb UI and local web dashboard.

## Quick start

Requirements: Python 3.10 or newer and Git.

```bash
git clone https://github.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S.git
cd Voice-Automated-Desktop-Agent-J.A.M.E.S
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[ui,mcp]"
cp .env.example .env
python -m james --check
python -m james --text
```

For a local Ollama-compatible endpoint:

```dotenv
LLM_PROVIDER=custom
LLM_MODEL=llama3.1
CUSTOM_BASE_URL=http://localhost:11434/v1
CUSTOM_API_KEY=ollama
VOICE_ENABLED=false
```

Before enabling extra capabilities, run:

```bash
python -m james doctor
python -m pytest -q
```

## Safety model

JAMES starts in `standard` mode. In that mode, system-mutating tools are blocked. `full` mode enables them, but they remain subject to confirmation and configured permissions.

```dotenv
JAMES_MODE=standard
CONFIRM_DANGEROUS_ACTIONS=true
DRY_RUN=false
AUTO_SKILL=false
ENABLE_TRUSTED_EXTERNAL_PLUGINS=false
```

- Dangerous calls require confirmation; non-interactive contexts deny them by default.
- `run_shell_command` and scheduled commands use a small read-only command policy. Arbitrary interpreter flags and mutating utilities are rejected.
- Audit entries are HMAC-signed with a random per-workspace key, created on first use. Set `JAMES_AUDIT_HMAC_KEY` to manage the key yourself.
- Conversation history uses authenticated Fernet encryption with a random per-workspace key. Set `JAMES_HISTORY_KEY` to manage that key yourself.
- TTS playback never invokes a shell, and audio files use secure temporary-file creation.
- `OFFLINE_MODE=true` blocks non-loopback network access. Pair it with a local model for a local-only workflow.

Read the complete security guidance in [docs/security.md](docs/security.md).

### Important limitations

JAMES is **not** a hardened OS sandbox. Treat LLM output, MCP servers, and third-party software as untrusted. The project does not yet provide container/VM isolation, enterprise policy management, or a signed-plugin marketplace.

The GUI currently fails closed for dangerous actions unless it is given an explicit confirmation handler. This is intentional while the dedicated GUI approval flow is being built.

## Tools and capabilities

| Area | Examples |
|---|---|
| Files | Read, write, search, copy, move, rename, and inspect directories |
| Documents | Create Word, PowerPoint, and PDF files |
| Browser | Navigate, click, type, extract text, screenshots, health checks |
| Desktop | Screenshots, keyboard and pointer automation, vision-assisted computer use |
| Memory | Local recall and persistent memory facts |
| Scheduling | Reminders and tightly restricted scheduled read-only commands |
| Integrations | Configured MCP servers and selected web tools |
| Voice | Offline and hosted STT/TTS providers |

Use `help` in JAMES to inspect tools enabled in the current configuration.

## Plugins and Skill Forge

There are two intentionally different extension models:

1. **Generated skills** are constrained, pure `@tool` functions. They are parsed, reject imports/reflection/attribute access/loops, and are revalidated before loading. They are suitable for small deterministic transformations—not arbitrary automation.
2. **Trusted external plugins** are ordinary Python and can execute arbitrary code. They are disabled by default. Enable them only after reviewing their source:

   ```dotenv
   ENABLE_TRUSTED_EXTERNAL_PLUGINS=true
   ```

Automatic skill generation is off by default. Enable `AUTO_SKILL=true` only if you understand the constraint model and have reviewed the generated skill workflow.

## Configuration

Copy `.env.example` to `.env`; it documents every supported setting. The most useful controls are:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=...

JAMES_MODE=standard
CONFIRM_DANGEROUS_ACTIONS=true
DRY_RUN=true
ALLOWED_TOOLS=read_file,list_directory,web_search
DENIED_TOOLS=run_shell_command,delete_file

OFFLINE_MODE=false
VOICE_ENABLED=false
```

`ALLOWED_TOOLS` is an allowlist. If it is populated, tools outside it are denied.

## Architecture

```text
input (text / voice / UI)
  → Assistant
  → Agent loop
  → ToolRegistry
  → configured tools and providers
  → audited result / reply
```

Core modules:

- `james/core/agent.py` — LLM tool-call loop and confirmation handling.
- `james/tools/registry.py` — tool registration, permissions, rate limit, audit trail, plugin boundary.
- `james/core/command_policy.py` — shell-free read-only command policy.
- `james/tools/forge_tools.py` — constrained generated-skill runtime.
- `james/core/assistant.py` — orchestration and encrypted history.
- `james/core/guard.py` — offline egress guard.

## Roadmap to v1

Before recommending JAMES to general users, the project needs:

- Explicit GUI approval prompts for dangerous calls.
- Process/container isolation for high-risk tool execution.
- Workspace-scoped filesystem permissions.
- Signed releases, dependency/security scanning, and CI across Windows, macOS, and Linux.
- A stable plugin SDK with signing and dependency metadata.
- A guided onboarding flow and clearer recovery/undo behavior.

The near-term product focus is a trustworthy desktop workflow: organize a bounded workspace, summarize local documents, and produce a reviewable result—with the user in control throughout.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), run the full test suite, and include tests for behavior changes—especially anything involving permissions, files, processes, plugins, or network access.

## License

MIT. See [LICENSE](LICENSE).
