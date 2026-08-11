# JAMES — Just A Modular Executive System

<p align="center">
  <img src="James.png" alt="JAMES" width="480">
</p>

> Status: **alpha**. JAMES is a local, desktop-first LLM assistant for people who want to experiment with agentic workflows. Do not give it unrestricted access to important data or production systems yet.

JAMES can work with files, documents, browser sessions, desktop controls, reminders, memory, and selected system information. It supports cloud LLM providers and OpenAI-compatible local endpoints such as Ollama.

The project’s goal is simple: make a desktop agent that is useful, inspectable, and safe by default—not an assistant that silently takes control of a machine.

## What works today

- Text and voice interaction, with configurable STT/TTS providers.
- A professional, OpenCode-style terminal chat UI (`james --text`): ASCII
  logo, status header, colour-coded message panels, live thinking spinner, and
  a styled input field.
- Model selection everywhere — pick provider and model interactively in the
  terminal (`/provider`, `/model`) and from the web UI dropdown. Choices
  persist to your `.env`.
- OpenAI, Anthropic, Gemini, OpenRouter, Groq, Mistral, xAI (Grok), DeepSeek,
  Together, Cerebras, Cohere, and any OpenAI-compatible custom endpoint (Ollama,
  LM Studio, vLLM...).
- File, document, browser, memory, scheduling, and desktop-control tools.
- MCP discovery for configured servers.
- Local encrypted conversation-history storage and JSON/Markdown export.
- Named conversation sessions — start, switch, resume, and clear per-session
  chat history (in-loop commands: `/new`, `/sessions`, `/resume <name>`,
  `/clear`, `/export`, or `james --session <name>`).
- Wake-word support: `WAKE_ENGINE=always` (continuous listen),
  `porcupine` (low-power Picovoice), or `none`.
- **Full-duplex voice** (`DUPLEX_MODE`): simultaneous speak-and-listen with
  interruption (barge-in), wake-gated always-on sessions, and native
  speech-to-speech with Gemini Live or OpenAI Realtime — or a fully local
  pipeline (VAD + faster-whisper + edge-tts streaming). Typed text stays
  first-class: you can interrupt or steer the conversation with the keyboard
  from the web UI.
- A closed learning loop: skills forged in one session are re-surfaced when
  the same kind of request comes up again, conversation summaries persist to
  long-term memory for cross-session recall, and the marketplace can publish
  and re-install skills (`publish_skill`, `install_plugin`).
- Agentic quality features: plan-then-act (the model states a short plan
  before acting, with a corrective nudge when it forgets), self-correction
  (tool errors are classified as transient/permanent — rate limits and
  timeouts are retried once automatically, failures surface with recovery
  hints), parallel tool calls (independent calls run concurrently while
  stateful browser/desktop tools stay serial), and mid-task context
  compaction (long conversations are summarized to fit the model's window).
- Richer web tools: main-content extraction (navigation, ads and sidebars
  stripped), multi-engine search (Tavily/Brave when keys are set, DuckDuckGo
  fallback), link discovery, and automatic headless-browser rendering for
  JavaScript-only pages.
- Multimodal input across providers: attach images to any turn for OpenAI,
  Anthropic, and Gemini vision models.
- Optional offline mode that blocks non-loopback network egress.
- A browser-based desktop UI (`james` or `james --serve`): a dependency-free
  single-page app with streaming chat threads, live tool activity, a model
  switcher, sessions sidebar, voice controls, settings and tools pages,
  deny-by-default approval prompts, and an onboarding wizard — served by a
  local FastAPI sidecar and wrapped in a Qt shell (system tray,
  minimize-to-tray) that falls back to the default browser when Qt is missing.

## Quick start

Requirements: Python 3.10 or newer and Git.

### One-command install (recommended)

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Krish-1507/JAMES_Agent/main/install.sh | bash

# Windows — PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Krish-1507/JAMES_Agent/main/install.ps1 | iex"
```

> The PowerShell one-liner works from **both** PowerShell and Command Prompt
> (cmd). `irm` / `iex` are PowerShell-only commands, so the `powershell -Command`
> wrapper is required when you launch it from cmd — running the bare
> `irm ... | iex` in cmd silently does nothing.

The installer clones JAMES to a stable location, creates a virtualenv, installs
everything (auto-downloading dependencies), adds the `james` command to your
PATH, and creates desktop/start-menu shortcuts. It then launches the one-time
setup wizard — **paste your API key, press Enter** — JAMES detects the provider
from the key format and writes a working `.env` for you.

**Open a new terminal**, then:

```bash
james              # desktop shell (Qt window, or browser fallback)
james --serve      # web UI in your default browser (http://127.0.0.1:8124)
james --text       # terminal CLI
james --voice      # terminal voice mode
james --setup      # re-run the wizard anytime
```

### Terminal chat (CLI)

`james --text` opens the OpenCode-style chat: it prints the JAMES logo, a status
bar with the active provider, model, session, and version, then renders each
turn as a colour-coded panel with a live spinner while JAMES thinks. Tool calls
stream inline as they execute.

### Choosing a model

- **Terminal:** type `/provider` to switch providers or `/model` to switch the
  model for the current provider. Both open an interactive picker built from
  the curated model catalog. `custom` lets you type any id (Ollama, LM Studio,
  vLLM...).
- **Desktop app:** pick any `provider:model` from the dropdown; JAMES applies it
  immediately without a restart.
- Your choice is written back to `.env` (`LLM_PROVIDER`, `LLM_MODEL`) so it
  sticks across sessions. The `--provider` / `--model` flags override the
  current selection for a single run.

### Manual install

```bash
git clone https://github.com/Krish-1507/JAMES_Agent.git
cd JAMES_Agent
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[ui,mcp]"
james --setup      # interactive wizard: paste key, pick voice, writes .env
james --text
```

`--setup` runs a quick wizard. In the express path you paste an API key and
JAMES detects the provider (OpenAI, Anthropic, Gemini, Groq, OpenRouter, ...)
and applies its default model — just press Enter to accept. It writes a `.env`
file (gitignored, chmod 600 on POSIX). It auto-runs on first launch when no
`.env` exists. If you already have a key, just `cp .env.example .env` and edit
it instead.

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
james doctor
python -m pytest -q
```

## Voice (speech-to-speech)

The default pipeline is a full speech loop: microphone → STT → LLM → TTS →
speaker, with wake-word control. Out of the box it uses natural, neural
Microsoft Edge voices (no API key):

```dotenv
VOICE_ENABLED=true
STT_PROVIDER=whisper_local
TTS_PROVIDER=edge
```

- **TTS**: `edge` (recommended — free, human-like, needs the `[voice]` extra),
  `pyttsx3` (offline system voices), or premium `openai` / `elevenlabs`
  (most human, need their API key). Missing `edge-tts` falls back to
  `pyttsx3`, then text output — JAMES never fails to start.
- **STT**: `whisper_local` (offline, recommended), `whisper_api`, or `google`.
- Run `james --voice` to force voice mode, `--text` for text only.

```bash
pip install -e ".[voice]"   # installs whisper + edge-tts + pyttsx3 + pyaudio
```

## Full-duplex voice (speak and listen at once)

Instead of a strict "talk → wait → listen" loop, JAMES can keep a live session
open: you speak when you want, interrupt mid-answer, and JAMES responds while
you are still talking. Set `DUPLEX_MODE` to one of:

| Mode | Engine | Requires |
|---|---|---|
| `off` | Turn-based loop (default) | — |
| `auto` | Best available engine | Any LLM key |
| `gemini_live` | Gemini Live API — true speech-to-speech, server VAD, native function calling | `GEMINI_API_KEY` |
| `openai_realtime` | OpenAI Realtime API — speech-to-speech over WebSocket, server barge-in | `OPENAI_API_KEY` |
| `local` | Fully local: VAD segmentation + faster-whisper (falls back to openai-whisper) + edge-tts streaming over ffmpeg, with mic-level barge-in | `ffmpeg` on PATH |

`auto` picks the first available of `gemini_live` → `openai_realtime` → `local`.

```dotenv
DUPLEX_MODE=auto
WAKE_ENGINE=always            # always | none | porcupine
DUPLEX_IDLE_TIMEOUT=30        # seconds of quiet before the session closes
VAD_THRESHOLD=0.02            # speech sensitivity (0..1 RMS)
BARGE_IN_THRESHOLD=0.03       # how loudly you must speak to interrupt
STREAMING_STT_MODEL=small     # faster-whisper model for the local engine
DUPLEX_EDGE_VOICE=en-US-AriaNeural
GEMINI_LIVE_VOICE=Puck
GEMINI_LIVE_MODEL=gemini-2.0-flash-live-001
OPENAI_REALTIME_VOICE=alloy
OPENAI_REALTIME_MODEL=gpt-4o-realtime-preview
```

Duplex sessions are **wake-gated**: the controller sits idle until the wake
word, opens a session on wake, and returns to idle after the idle timeout or a
spoken exit — so the mic is not continuously transcribed. The web UI shows
the live state (idle / listening / transcribing / thinking / speaking),
a mic level meter, and controls for mute, interrupt, and voice-only mode;
typed text routes into the live session at any moment. In the terminal,
`james --voice` runs the same controller with CLI printing.

Local streaming STT uses faster-whisper when installed (`[voice]` extra now
ships it) and falls back to openai-whisper. The local engine's function
calling goes through the normal gated, audited `ToolRegistry`; cloud sessions
use their native tool-calling protocol with the same registry as the executor.

> Duplex mode needs `VOICE_ENABLED=true` and a wake engine other than `none`
> to be always-available; with `WAKE_ENGINE=none` the session activates
> immediately. Voice interactions run the full agent loop, so dangerous
> actions still require confirmation exactly as they do in text mode.

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
- The desktop/web UI shows a deny-by-default approval dialog with redacted arguments for every dangerous call.
- `run_shell_command` and scheduled commands use a small read-only command policy. Arbitrary interpreter flags and mutating utilities are rejected.
- Agent-controlled paths are bounded to `WORKSPACE_DIR`; deletion uses recoverable workspace trash.
- Shell, scheduled command, deletion, and plugin execution runs in spawned workers with timeouts.
- Audit entries are HMAC-signed with a random per-workspace key, created on first use. Set `JAMES_AUDIT_HMAC_KEY` to manage the key yourself.
- Conversation history uses authenticated Fernet encryption with a random per-workspace key. Set `JAMES_HISTORY_KEY` to manage that key yourself.
- TTS playback never invokes a shell, and audio files use secure temporary-file creation.
- `OFFLINE_MODE=true` blocks non-loopback network access. Pair it with a local model for a local-only workflow.

Read the complete security guidance in [docs/security.md](docs/security.md).

### Important limitations

JAMES is **not** a hardened OS sandbox. Spawned workers reduce blast radius but are not containers or virtual machines. Treat LLM output, MCP servers, and third-party software as untrusted; enterprise policy management remains future work.

## Tools and capabilities

| Area | Examples |
|---|---|
| Files | Read, write, search, copy, move, rename, and inspect directories |
| Reading | Extract text from PDFs, Word/PPT/Excel/CSV/ODS documents, transcribe audio (Whisper), describe images with a vision model, unpack zip/tar archives |
| Documents | Create Word, PowerPoint, and PDF files |
| Compute | Safe, AST-allowlisted arithmetic (`calculate` — no variables, imports, or attributes) |
| Browser | Navigate, click, type, extract text, screenshots, health checks |
| Desktop | Screenshots, keyboard and pointer automation, vision-assisted computer use |
| Memory | Local recall and persistent memory facts |
| Scheduling | Reminders and tightly restricted scheduled read-only commands |
| Integrations | Configured MCP servers and web tools: multi-engine search (Tavily/Brave/DuckDuckGo), main-content extraction, link discovery, JS-page rendering |
| Voice | Offline and hosted STT/TTS providers |

Use `help` in JAMES to inspect tools enabled in the current configuration.

## Plugins and Skill Forge

The [Plugin SDK](docs/plugins.md) (`james.sdk`) is the documented way to write
extensions. There are two intentionally different extension models:

1. **Generated skills** are constrained, pure `@tool` functions. They are parsed, reject imports/reflection/attribute access/loops, and are revalidated before loading. They carry a machine-readable manifest and are suitable for small deterministic transformations—not arbitrary automation.
2. **Trusted external plugins** are ordinary Python and can execute arbitrary code. They are disabled by default. Enable them only after reviewing their source:

   ```dotenv
   ENABLE_TRUSTED_EXTERNAL_PLUGINS=true
   ```

Scaffold a valid, manifest-carrying plugin with:

```bash
james --new-tool hello
```

Skills can be published to the local marketplace and reinstalled later.
Automatic skill generation is off by default. Enable `AUTO_SKILL=true` only if you understand the constraint model and have reviewed the generated skill workflow.

## Evaluation and benchmarks

JAMES ships a GAIA benchmark harness in `james/evaluation/` that scores the
agent with a faithful port of the official answer matcher (quasi-exact match,
number/percent closeness), isolates every task in a fresh subprocess worker
with a hard timeout, and records per-task `tool_calls`/`iterations` plus
level-stratified pass rates. Eval runs use the upgraded agent loop (plan-then-
act, transient-error retries, parallel tool calls, context compaction) pinned
to a single model per run, and persist results after every task so a crashed
or killed suite never loses completed work.

```bash
python -m james --eval smoke                                        # offline pipeline check (no API key)
python -m james --eval gaia --download-gaia --eval-limit 10         # cheap real run
python -m james --eval gaia                                         # full 166-task validation split
```

Requires `pip install -e ".[docs]"` (pypdf, openpyxl, python-docx, python-pptx,
odfpy, pyarrow) and a gated GAIA download: accept the terms at
[huggingface.co/datasets/gaia-benchmark/GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA)
and set `HF_TOKEN`. Results land in `workspace/evaluations/gaia_report_*.json`
and are tracked in [docs/BENCHMARKS.md](docs/BENCHMARKS.md), which the nightly
`Eval` workflow (`.github/workflows/eval.yml`) updates automatically.

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

Optional web-search keys improve `web_search` results (engine=`auto` picks
Tavily, then Brave, then DuckDuckGo):

```dotenv
TAVILY_API_KEY=...
BRAVE_API_KEY=...
```

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

- `james/core/agent.py` — LLM tool-call loop: plan-then-act, transient-error
  retries, parallel tool calls, context compaction, and confirmation handling.
- `james/tools/web_tools.py` — multi-engine search, main-content extraction,
  link discovery, and JS-page rendering.
- `james/tools/registry.py` — tool registration, permissions, rate limit, audit trail, plugin boundary.
- `james/core/command_policy.py` — shell-free read-only command policy.
- `james/tools/forge_tools.py` — constrained generated-skill runtime.
- `james/core/assistant.py` — orchestration, encrypted history, and `switch_model` (live provider/agent rebuild).
- `james/core/guard.py` — offline egress guard.
- `james/ui/cli.py` — the OpenCode-style terminal renderer (`JamesCLI`).
- `james/ui/server.py` — the FastAPI sidecar behind the web UI: JSON API
  (turns, sessions, model, settings, tools, voice, approvals, onboarding),
  SSE event broadcast, and static asset serving.
- `james/ui/shell.py` — the Qt shell hosting the web UI (system tray,
  minimize-to-tray) with a browser fallback when Qt is missing.
- `james/ui/web/` — the dependency-free single-page web app.
- `james/voice/duplex.py` — full-duplex voice: VAD, wake gate, and the
  `DuplexController` state machine behind the Gemini Live / OpenAI Realtime /
  local streaming engines (see the full-duplex voice section above).
- `james/llm/catalog.py` — the shared provider/model catalog used by the CLI pickers, the web UI dropdown, and setup.
- `james/sdk/` — the plugin authoring SDK (manifest, validation, scaffolding).

## Roadmap to v1

JAMES is in **alpha** and should not be treated as production software yet.
The promotion-readiness checklist below is **complete**; the remaining gaps
before JAMES can be recommended to general users are listed in the next section.

### Promotion-readiness checklist (done)

### Safety (highest priority)
- [x] **Explicit GUI approval prompts for dangerous calls.** One-time approvals are deny-by-default and redact sensitive arguments.
- [x] **Process isolation for high-risk tool execution.** Shell, scheduled command, recoverable deletion, and plugin calls use spawned workers with hard timeouts. Container/VM isolation remains optional future defense in depth.
- [x] **Workspace-scoped filesystem permissions.** Canonical path checks reject traversal, absolute escapes, and symlink escapes.

### Release engineering
- [x] **Signed release artifacts.** Tag builds are signed through Sigstore using GitHub OIDC and attached to releases. CI currently covers Windows and Linux; macOS coverage remains planned.
- [x] **Dependency/security scanning.** `pip-audit`, Bandit, and CodeQL run in the security workflow. Bandit now runs clean with no suppressions or baseline.

### Plugin & marketplace trust
- [x] **Plugin signing + dependency metadata.** Manifests support Ed25519 signatures, content digests, key IDs, dependencies, and cycle/missing-dependency rejection.

### Usability & recovery
- [x] **Guided onboarding and clearer recovery/undo behavior.** Desktop empty states guide setup, and recoverable deletion includes a dedicated Recovery view.

### Remaining before general release
- [ ] **Published benchmark results.** The GAIA harness, isolated worker, and
      Level-1 reading/compute tools are in; CI runs the offline smoke suite
      nightly, executes a GAIA validation subset when an `OPENAI_API_KEY` is
      configured, and auto-publishes runs to `docs/BENCHMARKS.md`. Real model
      results will fill the table as runs land.
- [ ] **Broader automated test coverage.** The suite exercises every security boundary
      (egress guard, worker isolation, agent confirmation, skill runtime, plugin
      signing), the core agent/assistant paths, and the full-duplex voice stack
      (VAD, controller, all three session engines, and streaming-TTS barge-in).
      UI, browser, and document tools still have thin coverage. Target ≥80% on
      security-critical modules before recommending JAMES to non-technical users.
- [ ] **macOS signed artifacts.** The test matrix now covers macOS (Ubuntu +
      Windows + macOS across Python 3.10-3.12); OIDC-signed release artifacts
      still build on Linux only, which is fine because the wheel is a pure-Python
      `py3-none-any` build shared by all platforms.
- [ ] **Enterprise policy management.** Centralized allow/deny and audit policy is
      explicitly future work; single-user `.env` configuration remains the model.
- [ ] **Container/VM isolation.** Spawned workers are process isolation, not a
      sandbox boundary; container or VM execution remains optional defense in depth.

The near-term product focus is a trustworthy desktop workflow: organize a bounded workspace, summarize local documents, and produce a reviewable result—with the user in control throughout.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), run the full test suite, and include tests for behavior changes—especially anything involving permissions, files, processes, plugins, or network access.

## License

MIT. See [LICENSE](LICENSE).
