# JAMES documentation

JAMES is an alpha-stage, desktop-first LLM assistant. It is designed for experimentation with local workflows—not unattended access to important systems.

## Start safely

1. Install the project and copy `.env.example` to `.env`.
2. Keep `JAMES_MODE=standard` and `CONFIRM_DANGEROUS_ACTIONS=true`.
3. Run `python -m james --check`, then `python -m james doctor`.
4. Start with `python -m james --text` and only enable tools you need.

The repository [README](https://github.com/Krish-1507/JAMES_Agent#readme) contains installation, provider, configuration, and roadmap guidance.

## Documentation

- [Architecture](architecture.md)
- [Writing plugins with the SDK](plugins.md)
- [Security model and threat boundaries](security.md)
- [Contributing](https://github.com/Krish-1507/JAMES_Agent/blob/main/CONTRIBUTING.md)

## Project status

JAMES currently supports LLM tool calling, local files and documents, configured MCP servers, browser and desktop automation, voice providers, memory, encrypted history, optional offline operation, a constrained Skill Forge, and a documented plugin SDK (`james.sdk`).
