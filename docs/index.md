# JAMES documentation

<p align="center">
  <img src="https://raw.githubusercontent.com/Krish-1507/JAMES_Agent/main/James.png" alt="JAMES" width="480">
</p>

JAMES is an alpha-stage, desktop-first LLM assistant. It is designed for experimentation with local workflows—not unattended access to important systems.

## Start safely

1. Install the project and copy `.env.example` to `.env`.
2. Keep `JAMES_MODE=standard` and `CONFIRM_DANGEROUS_ACTIONS=true`.
3. Run `james --check`, then `james doctor`.
4. Start with `james --text` and only enable tools you need. `james` with no
   arguments opens the desktop shell (Qt window, or browser fallback).

The repository [README](https://github.com/Krish-1507/JAMES_Agent#readme) contains installation, provider, configuration, and roadmap guidance.

## Documentation

- [Architecture](architecture.md)
- [Writing plugins with the SDK](plugins.md)
- [Security model and threat boundaries](security.md)
- [Benchmarks and evaluation harness](BENCHMARKS.md)
- [Contributing](https://github.com/Krish-1507/JAMES_Agent/blob/main/CONTRIBUTING.md)

## Project status

JAMES currently supports LLM tool calling, local files and documents, configured MCP servers, browser and desktop automation, turn-based and full-duplex voice (Gemini Live / OpenAI Realtime / fully local streaming), memory, encrypted history, optional offline operation, a constrained Skill Forge, and a documented plugin SDK (`james.sdk`). The agent loop plans before acting, retries transient tool errors, runs independent tool calls in parallel, and compacts long contexts; web tools offer multi-engine search, main-content extraction, and JS-page rendering.

Phase-4 automation and scale work adds:

- **One-click MCP integrations** — a curated catalog of default MCP servers
  (filesystem, fetch, browser automation, GitHub, Slack, Notion, Gmail,
  sequential thinking) toggled from the web UI, writing `mcp.json` and
  hot-reloading tools into the running registry.
- **Windows app automation** — drive the real Microsoft Office applications
  via COM (Outlook email/calendar, Excel cells, Word text, PowerPoint decks)
  with openpyxl/python-docx fallbacks on other platforms, plus desktop
  notifications.
- **Recipes** — persisted multi-step automations (`daily 09:00`, `hourly`,
  `every N minutes`) run through the gated tool registry, composed by the LLM
  from plain language ("every morning summarize my emails and notify me").
- **Messaging gateway** — Telegram, WhatsApp (Twilio), Discord and Slack
  bridged to the same agent core (`james --gateway`); inbound messages become
  turns and replies return to the originating chat, with a `send_message`
  tool for proactive pushes.
- **Cloud plugin registry** — the marketplace syncs a GitHub-hosted catalog
  (`MARKETPLACE_URL`); remote plugins still require Ed25519 signatures.

### Promotion readiness

JAMES is **alpha**. The current hardening baseline includes explicit desktop approvals, workspace-scoped paths, spawned-process workers for high-risk tools and plugins, signed plugin metadata, recoverable deletion, SCA/SAST, and OIDC-signed release artifacts. Remaining promotion work is tracked in the [README roadmap](https://github.com/Krish-1507/JAMES_Agent#roadmap-to-v1).

## Interfaces and model selection

- **Terminal CLI** (`james --text`) uses an OpenCode-style chat UI: ASCII logo,
  status header, colour-coded message panels, spinner, and styled input.
- **Desktop shell / web UI** (`james` or `james --serve`): a Qt shell with
  system-tray support hosts — or falls back to the default browser for — a
  dependency-free single-page app: streaming chat, live tool activity, model
  switcher, sessions sidebar, voice controls, settings/tools pages,
  deny-by-default approval prompts, and an onboarding wizard. Phase-4 adds
  **Integrations** (one-click MCP servers + cloud registry sync + gateway
  status), **Recipes** (compose, run now, pause, delete), and gateway send.
- **Messaging gateway** (`james --gateway`): run the agent headless and
  bridge Telegram, WhatsApp (Twilio webhook on `/api/gateway/whatsapp`),
  Discord and Slack. Messages become turns; replies go back to the same chat.
- **Voice** (`james --voice`): turn-based speech-to-speech by default, or
  full-duplex (`DUPLEX_MODE=gemini_live|openai_realtime|local|auto`) with
  wake-gated sessions, interruption, and barge-in. See the README
  [full-duplex voice section](https://github.com/Krish-1507/JAMES_Agent#full-duplex-voice-speak-and-listen-at-once).
- **Model selection** is available in both the terminal and the web UI:
  `/provider` and `/model` open interactive pickers in the CLI, and the web UI
  dropdown applies a `provider:model` immediately. Choices persist to `.env`
  via the shared catalog in `james/llm/catalog.py`.
