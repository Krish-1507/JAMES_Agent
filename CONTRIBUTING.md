# Contributing to JAMES

JAMES is an alpha-stage desktop agent. Contributions are welcome, especially improvements to safety, reliability, onboarding, testing, and bounded high-value workflows.

## Getting started

```bash
git clone https://github.com/Krish-1507/JAMES_Agent.git
cd JAMES_Agent
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[ui,mcp]"
cp .env.example .env
python -m james --check
python -m pytest -q
```

Keep local development conservative:

```dotenv
JAMES_MODE=standard
CONFIRM_DANGEROUS_ACTIONS=true
AUTO_SKILL=false
ENABLE_TRUSTED_EXTERNAL_PLUGINS=false
```

## Project conventions

- Support Python 3.10+ and add type hints to new public code.
- Keep optional imports inside the capability that uses them.
- Add configuration in `james/config.py` and document it in `.env.example`.
- Add tests for every behavior change. Security-sensitive changes need both an allow case and a deny/bypass case.
- Do not use `shell=True`, `os.system`, or `tempfile.mktemp`.
- Do not introduce hardcoded secrets. Use the workspace key helpers or documented environment variables.
- Treat plugin, MCP, web, and model output as untrusted input.
- Generated skills must remain inside the constrained runtime in `james/tools/forge_tools.py`. Do not weaken its AST restrictions to make arbitrary Python loadable.
- Ordinary external Python plugins are trusted code and must remain opt-in.

## Before opening a pull request

```bash
python -m compileall -q james
python -m pytest -q
```

Also update the README, `.env.example`, and `docs/` whenever a user-facing behavior, security boundary, or configuration setting changes.

## Pull requests

1. Create a focused branch from `main`.
2. Explain the user impact and the reason for the change.
3. Include tests and documentation updates.
4. Call out any new permissions, network access, filesystem access, or executable-code path.

## Security reports

Do not post exploit details, credentials, or private user data in a public issue. Use the repository’s private security-reporting channel when available, or contact the maintainers directly.
