# Changelog

All notable changes to JAMES are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
