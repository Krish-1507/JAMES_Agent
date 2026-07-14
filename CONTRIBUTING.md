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

## Pull requests

1. Fork and branch from `main` (`feat/...`, `fix/...`).
2. Run `python -m py_compile` on changed files (and `python -m james --check`).
3. Add a short description of what and why.
4. Open the PR — GitHub Actions will lint and build.

Let's build the best open-source assistant together.
