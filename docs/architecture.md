# Architecture

JAMES is a modular, desktop-first LLM assistant. Everything the model can do is
expressed as a **tool**; the agent loop turns natural language into tool calls.

## Layers

```text
┌────────────────────────────────────────────┐
│ CLI / GUI / Dashboard / Voice loop          │
│   • JamesCLI (OpenCode-style rich renderer) │
│   • PyQt orb (desktop, catalog dropdown)    │
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
  Used by the CLI pickers, the desktop dropdown, and the setup wizard.
- **`james.ui.cli`** — `JamesCLI`, the rich/OpenCode-style terminal renderer.
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
