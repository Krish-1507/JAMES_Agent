# Contributing to JAMES

JAMES is built to be useful because the community extends it. All contributions — tools, providers, voice engines, docs, and bug fixes — are welcome.

## Getting started

```bash
git clone https://github.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S.git
cd "Voice-Automated-Desktop-Agent-J.A.M.E.S"
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env   # fill in at least one provider
python -m james --check
```

## Project conventions

- Python 3.10+, type hints everywhere, no comments unless non-obvious.
- New capability means a new Tool in `james/tools/`, registered in `james/tools/registry.py`. Keep heavy imports inside the function body so the package imports without optional dependencies.
- New LLM provider means implementing `LLMProvider` in `james/llm/providers.py` and wiring it in `james/llm/factory.py`.
- Keep `config.py` the single source of truth for environment configuration; add a matching entry to `.env.example`.
- Security-first: never use `shell=True` in subprocess calls; validate all tool arguments; sandbox untrusted code execution (restricted builtins + dangerous import scanning in Skill Forge); sign audit log entries with HMAC; clean up temp files after use; use modern importlib APIs for plugin discovery.
- Tests: add pytest tests in `tests/` for new functionality. Run with `python -m pytest tests/`.

## Pull requests

1. Fork and branch from `main` (`feat/...`, `fix/...`).
2. Run `python -m py_compile` on changed files (and `python -m james --check`).
3. Run `python -m pytest tests/` to verify no regressions.
4. Add a short description of what and why.
5. Open the PR — GitHub Actions will lint and build.

Let's build the best open-source assistant together.
