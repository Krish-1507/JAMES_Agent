# Changelog

All notable changes to JAMES are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **GAIA benchmark harness** (`james/evaluation/gaia.py`): loads the public GAIA
  validation split (166 tasks with answers), scores replies with a faithful
  port of the official answer matcher (quasi-exact match, number/percent/float
  closeness), stratifies results by difficulty level, and writes JSON reports.
  Run with `james --eval gaia` (add `--download-gaia` to fetch the dataset,
  `--eval-dir <folder>` for a local copy, `--eval-limit N` for cheap runs).
- **Isolated eval worker** (`james/evaluation/worker.py`): each benchmark task
  runs in a fresh interpreter subprocess with a hard timeout, so a hung LLM
  call can never stall a suite. Tool calls and inference iterations are
  counted per task and included in reports.
- **Level-1 reading/compute tools** required by GAIA-style tasks:
  `read_pdf` (pypdf), `read_document` (docx/pptx/xlsx/csv/tsv/text),
  `extract_audio_text` (Whisper), `describe_image` (vision-capable models),
  `unzip_archive` (zip/tar with zip-slip protection), and `calculate`
  (AST-allowlisted arithmetic sandbox — no variables, imports, or attributes).
- `james --eval smoke` now exercises the real evaluator pipeline
  (metrics, reports) instead of a stub that always reported "completed".
- `Evaluator.run_task` records `tool_calls`/`iterations` and the model reply;
  `TaskResult` gained an `output` field.
- Nightly benchmark workflow (`.github/workflows/eval.yml`): runs the offline
  smoke suite always, and a GAIA validation subset when an `OPENAI_API_KEY`
  secret is configured; reports are uploaded as artifacts.
- `docs/BENCHMARKS.md` documenting methodology and how to reproduce a run.
- **Automatic benchmark publishing**: `scripts/publish_benchmarks.py` appends a
  GAIA run to the `docs/BENCHMARKS.md` results table, and the `Eval` workflow
  runs it (and commits the update) after every successful GAIA run.
- `read_document` now reads **ODS spreadsheets** (odfpy; added to the
  `docs`/`all` extras), completing the docx/pptx/xlsx/csv/tsv/ods family.
- **Agent quality (Phase 1)** — `james/core/agent.py` reasoning-loop upgrade:
  - **Plan-then-act**: the model is asked to state a short numbered plan in
    the same message as its first tool call; `Agent(require_plan=True)` injects
    a single corrective nudge if the first tool-using reply skips it.
  - **Self-correction**: tool errors are classified as transient/permanent
    (`classify_tool_error`). Transient failures (rate limits, timeouts,
    connection errors, 5xx) are retried once automatically with backoff; if
    the retry also fails the result carries a "try a different approach"
    marker. Permanent errors (invalid args, not found, denied) are never
    blindly re-attempted.
  - **Parallel tool calls**: independent calls from one model reply run in a
    small thread pool (`max_parallel`, default 4); stateful browser/desktop
    tools run serially; results always come back in call order. The registry
    audit writer is thread-safe (`_audit_lock`).
  - **Context compaction**: once the conversation exceeds
    `compact_threshold_chars`, older turns are summarized into a single digest
    by the LLM (counting `Agent.compactions`); the tail of the conversation is
    preserved verbatim.
  - **Provider tool caps**: `Agent(max_tools=N)` clips the schema list before
    sending (Anthropic and OpenRouter reject >64 tools per request).
- **Web tool upgrade** (`james/tools/web_tools.py`):
  - Main-content extraction: nav/ads/sidebars are stripped before reading
    (`extract_main_text`).
  - Multi-engine search: `web_search(engine="auto")` uses Tavily or Brave
    when `TAVILY_API_KEY`/`BRAVE_API_KEY` are set, else DuckDuckGo (no key).
  - Link discovery: `fetch_url(include_links=True)` returns the page's links
    for site exploration.
  - JS-only pages: `fetch_url` re-renders SPA shells with headless Playwright
    when the plain fetch yields almost no text.
- **Multimodal input across providers**: `Agent.run(images=...)` and
  `Assistant.think(..., images=...)` accept file paths / data URIs / URLs for
  vision models. Anthropic (base64/url image blocks) and Gemini (inline_data)
  previously ignored the `images` argument and now attach them; OpenAI-
  compatible providers already supported them.
- **Eval harness resilience**: `run_gaia_suite` persists results after every
  task, so a crashed or killed suite never loses completed work; the eval
  worker sets `max_tools=64` to stay under provider request limits.

### Fixed (real-run hardening, validated on live GAIA data)
- `GeminiProvider.chat` crashed with `AttributeError: 'NoneType' object has no
  attribute 'parts'` when a candidate carried no content (safety-filtered or
  function-call-only responses); empty candidates now yield an empty response.
- Anthropic and Gemini providers accepted but silently dropped the `images`
  argument on vision-capable models; both now attach images to the last user
  turn.
- The registry audit writer could interleave entries when tool calls ran
  concurrently; audit writes are now serialized with a lock.
- `docs/BENCHMARKS.md` now carries the first real result (2026-08-09,
  10/10 on the 10-task dev subset) with methodology notes covering the
  Phase-1 agent loop.

### Fixed (real-run hardening, validated on live GAIA data)
- Eval workers now pin the provider chain (`settings.llm.failover = []`):
  previously an `LLM_FAILOVER` configured in `.env` could silently switch the
  model mid-suite when the primary failed, mixing results within one run and
  breaking the worker-isolation regression test on machines with a `.env`.
- GAIA metadata is now Parquet-backed upstream (Oct 2025 restructure); the
  harness downloads/converts `metadata.parquet` → JSONL automatically
  (`pyarrow` added to the `docs`/`all` extras) and accepts local folders
  containing either format. Gated-dataset access via `HF_TOKEN` with a clear
  error when the terms haven't been accepted.
- Eval worker subprocesses run from the scratch dir — the harness now
  propagates the package root via `PYTHONPATH` (children previously died with
  `ModuleNotFoundError: james`).
- Headless agents (`confirm_dangerous=False`, e.g. the eval worker) no longer
  block on an interactive retry prompt after an LLM API error — the error
  propagates and lands in the report as `error: ...` instead of a 30 s silent
  stall. Report entries now always include the `error` field.
- `OpenAICompatibleProvider.chat` raises a clean error when the endpoint
  returns HTTP 200 with an empty/error body (free-tier model pool exhaustion)
  instead of crashing with `TypeError: 'NoneType' object is not subscriptable`.
- `james --eval gaia` gained `--eval-iterations N` to cap per-task token burn
  on rate-limited free tiers.

### Dependencies
- The `[docs]` extra now includes `pypdf` and `openpyxl` (PDF/spreadsheet
  reading); mirrored in the `[all]` extra.

## [0.3.0] - 2026-08-03

### Added
- macOS runners added to the CI test matrix (Python 3.10-3.12).

### Changed
- Codebase normalized with `ruff format` so the pre-commit `ruff-format` hook
  passes cleanly.
- `[all]` extra now includes `Pillow` (it was missing, silently leaving the
  desktop/screenshot dependency out of `pip install james-assistant[all]`).
- mkdocs build output (`site/`) is now gitignored.
- Gemini provider migrated to the current `google-genai` SDK (the legacy
  `google-generativeai` package is deprecated). `GeminiProvider` now builds
  native `types.Tool`/`types.FunctionDeclaration` tools and drives
  `client.models.generate_content` with function-call round-trips. `.env`/config
  keys are unchanged; only the pip package is different.
- `james doctor` reports the new `google.genai` SDK.

### Security & robustness
- The Bandit baseline is retired: all previously suppressed findings were
  triaged. Each remaining `subprocess`/`eval`/`exec` use now carries a justified
  `# nosec` comment (hardened with `shutil.which` probe-first checks where it
  matters) and `bandit -q` reports zero findings with **no baseline file** and
  **no suppressions**.
- `marketplace` now validates a `None` manifest with an explicit raise instead
  of a bare `assert` (so it isn't stripped under `-O`).

### Tests
- New `tests/test_security_guard_isolation.py` adds end-to-end coverage for the
  egress guard, worker isolation (command + trash/restore + timeout), agent
  confirmation flow, computer-use action parsing, the Porcupine engine fallback,
  and `james doctor`. Security-critical module coverage rose sharply (e.g.
  `agent.py` 57->83%, `computeruse.py` 0->85%, `guard.py` 27->62%,
  `isolation.py` 22->59%, `porcupine_engine.py` 0->80%, total 49%->54%).

### Added
- OpenCode-style terminal chat UI: `james --text` now renders the ASCII JAMES
  logo, a provider/model/session/version status bar, colour-coded message
  panels, a living thinking spinner, inline tool status, and a styled input
  field (`james/ui/cli.py`, wired into `Assistant.text_loop`).
- Interactive model selection in **both** interfaces:
  - Terminal: `/provider` and `/model` slash commands open rich pickers built
    from a shared catalog; `custom` accepts any model id.
  - Desktop: the dropdown is now populated from the shared catalog and applies
    the chosen `provider:model` immediately (thread-safe) without a restart.
  - A new `Assistant.switch_model(...)` rebuilds the live provider + agent and
    persists the choice.
- Shared model catalog `james/llm/catalog.py` — providers, default models,
  curated per-provider model lists, and `save_llm_config` which writes
  `LLM_PROVIDER`/`LLM_MODEL` back to `.env`. `onboarding.py` now reuses it.
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
- Terminal output could crash on legacy Windows consoles (cp1252) when drawing
  box/marker glyphs; `JamesCLI` now reconfigures the output streams to UTF-8.
- Desktop model switcher no longer silently requires a restart; changes apply
  immediately when the assistant is running.
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
