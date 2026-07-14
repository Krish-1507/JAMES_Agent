# JAMES — Voice Automated Desktop Agent

> Your open-source, voice-first **JARVIS**. Reads files, writes Word/PPT/PDF,
> browses the web, runs commands, and automates almost anything a human can do
> at a computer — driven by an LLM agent that calls real tools.

See the [README on GitHub](https://github.com/Krish-1507/Voice-Automated-Desktop-Agent-J.A.M.E.S)
for full setup, provider configuration and the feature matrix.

## Quick start

```bash
pip install -e .
cp .env.example .env   # set LLM_PROVIDER + API key + LLM_MODEL
python -m james --text
```

## Why JAMES

- 🎙️ Voice-first with wake-word, STT + TTS — or silent text mode.
- 🧠 Real agentic reasoning: the LLM chains tools to *finish* tasks.
- 🔌 Every provider, one interface: OpenAI, Anthropic, Gemini, OpenRouter, Groq, or any OpenAI-compatible local model.
- 🛠️ 20+ built-in tools + a plugin system to add your own in ~10 lines.
- 🔒 Safety-aware: confirmation prompts, permission tiers and dry-run.
