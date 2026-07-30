# JAMES — Voice Automated Desktop Agent

JAMES is an open-source, voice-first assistant that runs on your own computer. It reads and writes files, creates Word/PowerPoint/PDF documents, browses the web, controls applications, takes screenshots, and automates almost anything a person can do at a laptop — driven by an LLM agent that decides which tools to call and chains them together to finish real tasks.

See the [README on GitHub](https://github.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S) for full setup, provider configuration, and the feature matrix.

## Quick start

```bash
pip install -e .
cp .env.example .env   # set LLM_PROVIDER + API key + LLM_MODEL
python -m james --text
python -m james --web-dashboard   # launch web dashboard
```

## What JAMES is

- Voice-first with wake-word, STT and TTS, or silent text mode.
- Real agentic reasoning: the LLM chains tools to finish tasks, not just talk about them.
- Every provider, one interface: OpenAI, Anthropic, Gemini, OpenRouter, Groq, or any OpenAI-compatible local model.
- A broad set of built-in tools plus a plugin system to add your own in a few lines.
- Self-learning: it researches the web with `research` and teaches itself new capabilities with `learn_skill`.
- Autonomous file control: it can take over the file explorer, organize the filesystem, and run that work in the background.
- Safety-aware: confirmation prompts, permission tiers, dry-run, HMAC-signed audit logs, command injection protection (shell=False, input sanitization), and error handling that does not leak internal details.
- Voice cleanup: STT temp files are cleaned up after transcription; TTS providers use independent playback with no cross-provider coupling.
- Conversation history persists across sessions with LLM-powered summarization.
- Web dashboard for remote management and monitoring.
- Plugin marketplace for community tools.

## Key features

| Feature | Description |
|---------|-------------|
| **Web Dashboard** | Browser-based UI at `http://127.0.0.1:8123` for status, tools, history, MCP management, and permissions |
| **Conversation Export** | Export history as JSON or Markdown for sharing or review |
| **Per-Tool Permissions** | Fine-grained allow/deny per tool via `.env` or `ALLOWED_TOOLS`/`DENIED_TOOLS` |
| **Plugin Marketplace** | Curated registry of community plugins: `search_plugins`, `install_plugin`, `list_plugins` |
| **Task Dependency Graph** | Visualize tool call chains as DOT graphs with `task_dependency_graph` |
| **Multi-Modal Input** | Upload images for vision-capable LLM analysis with `upload_image` |
| **Agent Memory Visualization** | View what the agent remembered across sessions in the dashboard |
| **Tool Output Streaming** | Incremental tool results in the orb UI for perceived responsiveness |
| **Command Allowlist** | `run_shell_command` only permits safe commands (echo, cat, grep, python, curl, etc.) |
| **URL Scheme Validation** | `open_application` only allows `http://` and `https://` URLs and uses `shell=False` to prevent command injection |
| **Scheduled Task Safety** | Scheduled commands use `shell=False` and sanitized input to prevent injection |
| **Browser Resilience** | Browser sessions auto-recover from crashes via health checks |
| **Non-Blocking Confirmation** | Orb GUI handles dangerous-action confirmation via signals, never blocking the worker thread |
| **Robust MCP** | MCP calls handle both running and non-running async contexts without event-loop errors |
| **Skill Forge Sandboxing** | Generated skills are scanned for dangerous imports and executed with restricted builtins |
| **Modern Plugin Discovery** | Plugin registry uses current importlib APIs (`spec_from_file_location` + `module_from_spec`) |
| **Temp File Cleanup** | STT audio temp files are deleted after transcription |
