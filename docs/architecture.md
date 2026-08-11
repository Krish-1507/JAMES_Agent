# Architecture

JAMES is a modular, desktop-first LLM assistant. Everything the model can do is
expressed as a **tool**; the agent loop turns natural language into tool calls.

## Layers

```text
┌────────────────────────────────────────────┐
│ CLI / Shell / Web UI / Voice loop          │
│   • JamesCLI (OpenCode-style rich renderer)│
│   • Qt shell + FastAPI sidecar + SPA       │
│     (chat, catalog, approvals, voice)      │
├────────────────────────────────────────────┤
│ Assistant (sessions, history, memory,      │
│           skills, events, switch_model)     │
├────────────────────────────────────────────┤
│ Agent (plan-then-act, error recovery,      │
│       parallel calls, compaction)          │
├────────────────────────────────────────────┤
│ ToolRegistry (execute gate: audit, limits, │
│              modes)                        │
├───────────────┬────────────────────────────┤
│ Built-in      │ Plugins (james.plugins,    │
│ tools         │ plugins/, MCP, SDK skills) │
└───────────────┴────────────────────────────┘
```

## Full-duplex voice

`james.voice.duplex` runs a second, parallel runtime when `DUPLEX_MODE` is
enabled: the `DuplexController` owns a wake-gated state machine
(`IDLE` → `ACTIVE` → `IDLE`), and the active `DuplexSession` is one of three
interchangeable engines — Gemini Live (`GeminiLiveSession`), OpenAI Realtime
(`OpenAIRealtimeSession`), or the fully local pipeline (`LocalDuplexEngine`:
`VAD` + `LocalStreamingSTT` + `StreamTTS` with mic-level barge-in). Tool calls
from either cloud engine are executed through the same gated, audited
`ToolRegistry`; local turns use the agent loop's tool executor. The assistant
exposes thread-safe controls (`mute_voice`, `interrupt_voice`,
`set_voice_only`, `send_voice_text`) that the web UI and Qt shell wire to
buttons and a text input, so typed text and voice share one conversation.

## Core pieces

- **`james.core.assistant.Assistant`** — the runtime object. Holds settings,
  the registry, the LLM provider, voice providers, encrypted history,
  per-session state, and hooks for the GUI. It is the integration point the
  front ends use.
- **`james.core.agent.Agent`** — the reasoning loop. Before acting it asks the
  model to state a short plan (and nudges once when `require_plan` is set and
  the first tool-using reply skips it). Tool errors are classified: transient
  failures (rate limits, timeouts, 5xx) are retried once with backoff, and
  permanent ones surface to the model with a recovery hint. Independent tool
  calls from one reply run concurrently (stateful browser/desktop tools stay
  serial). When the conversation outgrows a size threshold, older turns are
  summarized by the LLM into one compact digest.
- **`james.tools.registry.ToolRegistry`** — the single gated entry point for
  capability execution. `execute()` applies rate limiting, allowed/denied
  lists, mode tiers, dry-run, and HMAC-signed audit logging (thread-safe for
  parallel calls).
- **`james.tools.web_tools`** — multi-engine search (Tavily/Brave when keys
  are configured, DuckDuckGo fallback), main-content extraction, link
  discovery, and Playwright fallback rendering for JavaScript-only pages.
- **`james.tools.base`** — the tool framework: `Tool`, `FunctionTool`,
  `ToolResult`, and the `tool` decorator.
- **`james.llm`** — pluggable LLM providers (OpenAI, Anthropic, Gemini,
  OpenRouter, Groq, Mistral, xAI, DeepSeek, Together, Cerebras, Cohere, and
  any OpenAI-compatible custom/local endpoint) with automatic failover.
- **`james.llm.catalog`** — the shared provider/model catalog plus
  `save_llm_config`, which persists `LLM_PROVIDER`/`LLM_MODEL` back to `.env`.
  Used by the CLI pickers, the web UI dropdown, and the setup wizard.
- **`james.ui.cli`** — `JamesCLI`, the rich/OpenCode-style terminal renderer.
- **`james.ui.server`** — FastAPI sidecar: JSON API (turns, sessions, model,
  settings, tools, voice, approvals, onboarding), SSE event broadcast, and
  static web serving.
- **`james.ui.shell`** — Qt shell hosting the web UI with system-tray
  support, falling back to the default browser when Qt is missing.
- **`james.ui.web`** — the dependency-free single-page web app (streaming
  chat, tool activity, approvals, onboarding).
- **`james.voice.duplex`** — the full-duplex voice runtime: `VAD`, `WakeGate`,
  `DuplexController`, the Gemini Live / OpenAI Realtime / local streaming
  session engines, `LocalStreamingSTT`, and barge-in-capable `StreamTTS` (see
  the full-duplex voice section above).
- **`james.integrations`** — the one-click MCP catalog (`catalog.py`) and the
  `mcp.json` manager (`manager.py`). Enabling an integration appends its
  server spec to `mcp.json`; `ToolRegistry.reload_mcp_tools()` re-discovers
  MCP tools into the running registry without a restart.
- **`james.gateway`** — the messaging gateway: `GatewayManager` owns the
  channel set and routes messages. `TelegramChannel` long-polls the Bot API
  (no extra dependencies); `WhatsAppChannel` sends via the Twilio REST API
  and receives inbound webhooks at `POST /api/gateway/whatsapp`; Discord and
  Slack (Socket Mode) channels are optional (`[gateway]` extra). Inbound
  messages run as assistant turns in dedicated threads and `reply` events are
  delivered back to the originating channel; the `send_message` tool lets the
  agent push messages proactively. `james --gateway` runs the bridge headless.
- **`james.core.recipes`** — the recipe engine: persists named multi-step
  automations (triggers `daily HH:MM` / `hourly` / `every N minutes` / ISO) to
  `workspace/recipes.json` and fires due recipes on a background thread.
  Every step executes through `ToolRegistry.execute`, so permissions, mode
  tiers, dry-run and HMAC audit apply to automations exactly as they do to
  interactive calls.
- **`james.tools.office_tools`** — Windows app automation via pywin32 COM
  (Outlook email/calendar, live Excel, Word, PowerPoint) with
  openpyxl/python-docx fallbacks so the file-based paths work on every
  platform.
- **`james.config`** — validated settings loaded from `.env`.
- **`james.sdk`** — the stable plugin authoring surface (see
  [plugins.md](plugins.md)).

## Configuration and startup

Settings load from `.env` via `james.config.settings`. `james --check`
prints the effective configuration; `james doctor` runs diagnostics.
On first run with no `.env`, an interactive onboarding wizard (`--setup`)
configures the provider and safety defaults.

## Sessions and privacy

Each named session (`--session NAME` or `/new`, `/resume` in the REPL) keeps an
independently encrypted history file under `workspace/sessions/`. The default
conversation history is also encrypted with a per-workspace Fernet key. Audit
records are HMAC-signed with a per-workspace key.

## Skill Forge and the learning loop

The agent can forge new tools from completed tasks. Generated code is validated
against a constrained AST runtime before it is persisted or executed. Saved
skills are re-surfaced when a later request matches them, and session summaries
persist to long-term memory, closing a learning loop across sessions.

## Security boundaries

- The only way the model reaches a capability is `ToolRegistry.execute`, which
  audits and gates every call.
- Generated skills run under the constrained runtime; trusted external plugins
  are explicitly opt-in.
- `OFFLINE_MODE=true` blocks non-loopback egress with a guard.

See [security.md](security.md) for the full threat model.
