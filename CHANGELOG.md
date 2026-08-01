# Changelog

All notable changes to JAMES are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
