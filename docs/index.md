# JAMES — Voice Automated Desktop Agent

JAMES is an open-source, voice-first assistant that runs on your own computer. It reads and writes files, creates Word/PowerPoint/PDF documents, browses the web, controls applications, takes screenshots, and automates almost anything a person can do at a laptop — driven by an LLM agent that decides which tools to call and chains them together to finish real tasks.

See the [README on GitHub](https://github.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S) for full setup, provider configuration, and the feature matrix.

## Quick start

```bash
pip install -e .
cp .env.example .env   # set LLM_PROVIDER + API key + LLM_MODEL
python -m james --text
```

## What JAMES is

- Voice-first with wake-word, STT and TTS, or silent text mode.
- Real agentic reasoning: the LLM chains tools to finish tasks, not just talk about them.
- Every provider, one interface: OpenAI, Anthropic, Gemini, OpenRouter, Groq, or any OpenAI-compatible local model.
- A broad set of built-in tools plus a plugin system to add your own in a few lines.
- Self-learning: it researches the web with `research` and teaches itself new capabilities with `learn_skill`.
- Autonomous file control: it can take over the file explorer, organize the filesystem, and run that work in the background.
- Safety-aware: confirmation prompts, permission tiers, and dry-run.
