# Changelog

All notable changes to JAMES are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `james` is now a first-class global command after install: `james` launches
  the desktop app (default), `james --text` / `james --voice` run the terminal
  CLI, and `james --setup` re-runs the wizard. Installers install to a stable
  per-user location (`~/james`), expose the command on PATH, and create
  desktop/start-menu shortcuts, so it works from any terminal.
- Windows fix: the false-positive "`.env` is world-readable" warning (which
  PowerShell rendered as a scary error on every run) no longer fires on Windows.
- Relative config paths (`./workspace`, audit/log/history files) now resolve
  against the project root instead of the current working directory, so the
  global `james` command never scatters state into whatever folder it's run from.
- One-command setup: `install.sh` / `install.ps1` now end with the interactive
  setup wizard (skipped when an API key is already configured), so the full
  path from clone to a working assistant is a single command:
  `curl ... | bash` or `irm ... | iex`.
- Express onboarding: paste an API key and JAMES detects the provider from its
  format (`sk-ant-` → Anthropic, `AIza` → Gemini, `gsk_` → Groq,
  `sk-or-` → OpenRouter, `sk-` → OpenAI) and applies the default model, so a
  typical setup is "paste key -> Enter -> Enter". Unrecognized keys fall back
  to the provider menu; local/custom endpoints still supported.
- Named LLM provider presets: `mistral`, `xai`, `deepseek`, `together`,
  `cerebras`, and `cohere` (all OpenAI-compatible, with base URLs, API-key
  env vars, onboarding entries, `.env.example` keys, and CLI/orb entries).
  Combined with the existing `openai`, `anthropic`, `gemini`, `openrouter`,
  `groq`, `custom`, and `LLM_FAILOVER`, JAMES now covers every major provider
  out of the box.
- TTS now defaults to `edge` (free, neural, human-like Microsoft voices, no
  API key). Missing `edge-tts` falls back to `pyttsx3`, then text output, so
  voice never prevents startup. `pyttsx3`, `openai`, and `elevenlabs` remain
  selectable.
- README voice/speech-to-speech section and updated provider lists in docs.
- Plugin SDK (`james.sdk`) with a stable authoring surface, plugin manifest
  schema (`# manifest-*` block, `PluginManifest`, `validate_manifest`),
  `create_plugin` scaffolding, `validate_plugin`, and `load_plugin`.
- `python -m james --new-tool <name>` now scaffolds a manifest-carrying,
  runtime-valid plugin via the SDK instead of a raw template.
- The marketplace bundles skill manifest metadata (`author`, `version`, `tags`)
  when publishing a skill.
- Docs: `docs/architecture.md` and `docs/plugins.md` (plugin SDK guide); mkdocs
  nav and docs index updated; README plugin section and roadmap updated.
- Closed learning loop (Phase 2):
  - Skills are now read *and* written: `get_relevant_skills()` re-surfaces
    saved generated skills that match the current request, so a skill forged
    in an earlier session is automatically suggested again (skill
    auto-application).
  - Cross-session recall: conversation summaries are persisted to long-term
    memory, so key facts/decisions survive across sessions and are recalled
    later.
  - Marketplace is now functional: `publish_skill` bundles a saved generated
    skill into the catalog, and `install_plugin` installs it back through the
    constrained Skill Forge runtime. Both are exposed as registered tools.
- First-run onboarding wizard (`python -m james --setup`) — picks a provider,
  model, and API key interactively and writes a gitignored `.env`. Auto-triggers
  on first launch when no `.env` exists.
- Named conversation sessions: `--session <name>` flag plus in-loop `/new`,
  `/sessions`, `/resume <name>`, `/clear`, `/export` commands. Each session is
  encrypted and stored under `workspace/sessions/<name>.enc`.
- Wake-word engine dispatch honors `WAKE_ENGINE`: `always` (continuous listen),
  `none` (no wake word), and `porcupine` (Picovoice, optional dep) with a safe
  fallback to `always` when pvporcupine is missing.

### Fixed
- `test_http_url_accepted` called the real `open_application`, which opened
  `https://example.com` in the user's browser on every test run. The test now
  mocks `webbrowser.open`.

## [0.2.0] - 2026-08-01

### Fixed
- `python -m james --eval` crashed with a missing `json` import (#eval).
- Tool permissions (`TOOL_<name>=...`) were read once at import time and never
  picked up the environment. They are now loaded from `os.environ` and tool
  names are normalized to lowercase (Windows uppercases env-var keys).
- The system prompt was duplicated into the visible agent history after the
  first turn. `Agent.run()` now returns only user/assistant messages.
- Auto-forge candidate window used an off-by-N slice that could re-review the
  same work repeatedly.
- The web dashboard showed a placeholder for past conversations instead of the
  real, decrypted history. It now reads the encrypted history file.
- `ToolRegistry` rate limiting was not thread-safe.
- Several tools were registered but unreachable (dead imports/duplicates):
  `browser_health`, `upload_image`, `search_plugins`, `list_plugins`. The
  unreachable stubs `install_plugin`/`remove_plugin` were removed.
- Offline guard monkey-patches could silently fail with `UnboundLocalError`
  because patch targets were never declared `global`.
- `.env.example` shipped with dangerous tools (`run_shell_command`,
  `delete_file`, ...) enabled by default; they are now off by default.

### Changed
- Orb model switcher now updates the active LLM settings immediately instead of
  requiring a restart; the MCP toggle no longer tears down the worker thread.
- Lint suite is now enforced: `ruff check james tests` passes clean.
- `requirements.txt` trimmed to match the `pyproject.toml` core dependencies
  (dropped dead `pydantic` / `selenium` pins).
- New `[dev]` optional dependency group for contributors.
- CI now runs on Windows and Linux across Python 3.10–3.12 with real lint and
  pytest checks (previously lint failures were masked with `|| true`).
- PyInstaller spec rebuilt to cover the full core module set.

### Added
- 11 regression tests covering the bug fixes above (`tests/test_phase0_regressions.py`).
- Pre-commit config for ruff + repo hygiene.
