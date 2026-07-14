# Contributing to JAMES

JAMES is built to be legendary *because* the community extends it. All
contributions — tools, providers, voice engines, docs, bug fixes — are welcome.

## Getting started

```bash
git clone https://github.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S.git
cd james
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in at least one provider
python -m james --check
```

## Project conventions

- **Python 3.10+**, type hints everywhere, no comments unless non-obvious.
- New capability ⇒ a new `Tool` in `james/tools/`, registered in
  `james/tools/registry.py`. Keep heavy imports *inside* the function body so the
  package imports without optional dependencies.
- New LLM provider ⇒ implement `LLMProvider` in `james/llm/providers.py` and
  wire it in `james/llm/factory.py`.
- Keep `config.py` the single source of truth for environment configuration;
  add a matching entry to `.env.example`.

## Pull requests

1. Fork and branch from `main` (`feat/...`, `fix/...`).
2. Run `python -m py_compile` on changed files (and `python -m james --check`).
3. Add a short description of *what* and *why*.
4. Open the PR — GitHub Actions will lint/build.

Let's build the best open-source JARVIS together. 🚀

