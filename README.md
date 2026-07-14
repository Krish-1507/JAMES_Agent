# 🤖 JAMES — *Just A Modular Executive System*

> Your open-source, voice-first **JARVIS**. JAMES lives on your computer and can
> read files, write documents, build PowerPoint decks and PDFs, browse the web,
> run commands, open apps, take screenshots, control media — and do basically
> anything a human can do at a laptop, driven by an LLM agent that calls real tools.

JAMES is built to be **provider-agnostic**, **modular**, and **yours**. Pick any
model from OpenAI, Anthropic, Google Gemini, OpenRouter, Groq, or a local/custom
OpenAI-compatible endpoint (Ollama, LM Studio, vLLM). Change the model id in one
line. Add new superpowers by dropping a `Tool` into the registry.

---

## ✨ Why JAMES is different

- 🎙️ **Voice-first** — wake-word activation, speech-to-text + natural TTS. Runs
  silently in text mode too.
- 🧠 **Real agentic reasoning** — the LLM decides which tools to call and chains
  them to actually *finish* tasks, not just chat about them.
- 🔌 **Every provider, one interface** — swap models without touching a line of
  agent code. Editable model id, editable temperature, editable endpoints.
- 🛠️ **Ships with 16 built-in tools** — files, documents, web, system control.
- 🧩 **Trivially extensible** — add a capability in ~10 lines with the `@tool`
  decorator.
- 🔒 **Safety-aware** — destructive actions ask for confirmation.
- 🪶 **Zero hard dependencies to start** — works in text mode with no API key
  (using a local model) and scales up as you add providers.

## 🗺️ What JAMES can do today

| Area | Tools |
|------|-------|
| **Files** | `read_file`, `write_file`, `list_directory`, `search_files`, `delete_file` |
| **Documents** | `create_word_document` (.docx), `create_powerpoint` (.pptx), `create_pdf` |
| **Web** | `web_search`, `fetch_url` |
| **🌐 Browser** | `browser_navigate`, `browser_click`, `browser_type`, `browser_extract`, `browser_screenshot`, `browser_close` |
| **🧠 Memory** | `remember`, `recall` (long-term, cross-session RAG over a local store) |
| **⏰ Scheduling** | `schedule_task`, `list_scheduled`, `cancel_task` (reminders + delayed/recurring commands) |
| **System** | `run_shell_command`, `open_application`, `take_screenshot`, `get_system_info`, `control_media`, `clipboard` |
| **🔌 Plugins** | `roll_dice` (example) + any tool you drop into `james/plugins/` or `./plugins/` |
| **🧩 Delegate** | `delegate` — fan out subtasks to isolated sub-agents (parallel via threads) |
| **🌐 MCP** | Connect **any** Model Context Protocol server; its tools appear automatically (`pip install mcp`) |
| **🧠 Skill Forge** | `save_skill` / `list_skills` / `forget_skill` — JAMES persists working plugins and hot-loads them |

> Want a new skill? Run `python -m james --new-tool my_tool`, edit the scaffold,
> and JAMES discovers it automatically — no core changes needed.

## 🔐 Safety & control

- **Permission tiers** — `JAMES_MODE=standard` blocks shell/delete/app tools;
  `full` (default) enables them.
- **Confirmation** — destructive tools prompt before running (`CONFIRM_DANGEROUS_ACTIONS`).
- **Dry-run** — `DRY_RUN=true` simulates dangerous actions and logs them instead of executing.
- **Audit log** — every tool call is appended to `AUDIT_LOG` (who/what/when/result).
- No telemetry: every request goes only to the provider/endpoint *you* configure.

## 🌐 Extensibility: MCP + Skill Forge

**MCP — inherit the whole ecosystem for free.** `pip install mcp`, then point JAMES
at any Model Context Protocol server (stdio or HTTP/SSE) via `MCP_SERVERS` or a
`mcp.json` file. Its tools show up in the registry like native ones — no glue code:

```json
[{"name":"fs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","./workspace"]}]
```

**Skill Forge — JAMES teaches itself.** After a complex task, say
*“save this as a skill called <name>”* and JAMES writes a real, typed `@tool`
plugin to `./plugins/`, validates it, and hot-loads it into the live session.
Unlike generic "skills", the result is a directly executable native tool — no
re-prompting next time. Manage them with `list_skills` / `forget_skill`.

**Delegation.** `delegate` spins up isolated sub-agents for parallel subtasks and
combines their results — split a big job across threads in one request.

## 🖥️ Optional GUI

Prefer a visual shell? `pip install pyqt5` then `python -m james --ui` launches the
orb interface (assistant runs in a worker thread, log streams to the window).

---

## 🚀 Quick start

**One line (Linux/macOS/WSL):**
```bash
curl -fsSL https://raw.githubusercontent.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S/main/install.sh | bash
```
**One line (Windows, PowerShell):**
```powershell
irm https://raw.githubusercontent.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S/main/install.ps1 | iex
```

Or manually:
```bash
git clone https://github.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S.git
cd "Voice-Automated-Desktop-Agent-J.A.M.E.S"
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env      # edit: set LLM_PROVIDER + API key (or custom for Ollama)
python -m james --check
python -m james --text    # or: python -m james --ui
```

Minimal `.env` for an OpenAI user:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
VOICE_ENABLED=false
```

Minimal `.env` for a **fully local** setup (no API keys at all):

```dotenv
LLM_PROVIDER=custom
LLM_MODEL=llama3.1
CUSTOM_BASE_URL=http://localhost:11434/v1   # Ollama
CUSTOM_API_KEY=ollama
VOICE_ENABLED=false
```

---

## 🧠 Providers & model selection

Every provider is selected with `LLM_PROVIDER` and the exact model with
`LLM_MODEL`. Because the model id is fully editable, you choose what's best for
you (cost, speed, intelligence).

| `LLM_PROVIDER` | Env key | Example `LLM_MODEL` |
|----------------|---------|---------------------|
| `openai` | `OPENAI_API_KEY` | `gpt-4o`, `gpt-4o-mini`, `o3-mini` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-3-5-sonnet-latest`, `claude-3-opus-latest` |
| `gemini` | `GEMINI_API_KEY` | `gemini-1.5-pro`, `gemini-1.5-flash` |
| `openrouter` | `OPENROUTER_API_KEY` | `anthropic/claude-3.5-sonnet`, `openai/gpt-4o` |
| `groq` | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| `custom` | `CUSTOM_BASE_URL` + `CUSTOM_API_KEY` | any OpenAI-compatible model |

Voice providers are just as flexible:

- **STT**: `whisper_local` (offline), `whisper_api`, `google`, `none` (typed).
- **TTS**: `pyttsx3` (offline), `edge` (free, high quality), `openai`, `elevenlabs`, `none`.

---

## 🏗️ Architecture

```
james/
├── __main__.py        # CLI entry point (--text, --voice, --provider, --model, --check)
├── config.py          # loads .env into typed settings (LLM / Voice / Assistant)
├── llm/
│   ├── base.py        # LLMProvider + normalized LLMResponse / ToolCall
│   ├── providers.py   # OpenAI-compatible, Anthropic, Gemini implementations
│   └── factory.py     # builds a provider from settings
├── tools/
│   ├── base.py        # Tool ABC, @tool decorator, JSON-schema generation
│   ├── registry.py    # discovers + executes tools, flags dangerous ones
│   ├── file_tools.py  # read/write/search the filesystem
│   ├── document_tools.py  # Word / PowerPoint / PDF generation
│   ├── web_tools.py   # search + fetch
│   └── system_tools.py# shell, apps, screenshots, media, clipboard
├── voice/
│   ├── stt.py         # speech-to-text providers
│   └── tts.py         # text-to-speech providers
└── core/
    ├── personality.py # system prompt
    ├── agent.py       # the reasoning loop (LLM ⇄ tools)
    └── assistant.py   # orchestrator: voice + llm + tools + logging
```

The agent loop is simple and robust: it sends the conversation + tool schemas to
the model; if the model returns tool calls, JAMES executes them, feeds the
results back, and repeats until the task is done (or a step limit is hit).

---

## ➕ Adding a tool

```python
from james.tools.base import tool

@tool(
    "translate_text",
    "Translate text into another language.",
    {
        "text": {"type": "string", "description": "Text to translate."},
        "target_lang": {"type": "string", "description": "Language code, e.g. 'fr'."},
    },
    required=["text", "target_lang"],
)
def translate_text(text: str, target_lang: str):
    # ... call your translation API ...
    return f"(translated) {text}"
```

Register it in `james/tools/registry.py` (add `translate_text` to `ALL_TOOLS`)
and the LLM can use it immediately — no other changes needed.

---

## 🔒 Safety

- Tools that mutate the system (`run_shell_command`, `delete_file`,
  `open_application`) are flagged dangerous and, when
  `CONFIRM_DANGEROUS_ACTIONS=true`, ask for confirmation before running.
- JAMES never phones home. All calls go to the provider/endpoint *you* configure.

---

## 🧭 Roadmap

- [ ] GUI / system-tray presence (revive the original PyQt orb UI)
- [ ] Long-term memory + RAG over your files
- [ ] Multi-step scheduled tasks & reminders
- [ ] Browser agent (Playwright) for full click-through automation
- [ ] Plugin marketplace for community tools
- [ ] Mobile companion app

Contributions are very welcome — see `CONTRIBUTING.md`.

---

## 📜 License

MIT — do what you want, just keep the attribution. Built to be legendary, together.

