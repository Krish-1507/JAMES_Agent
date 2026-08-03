"""Model catalog shared by the CLI, desktop app, setup wizard, and docs.

Central source of truth for which providers JAMES supports and which model ids
they ship with. Keeping this in one place means the ``/model`` picker, the
desktop dropdown, the onboarding wizard, and the README all agree.

``write_provider_config`` persists ``LLM_PROVIDER`` / ``LLM_MODEL`` back into
the user's ``.env`` so a model choice survives across sessions.
"""

from __future__ import annotations

import os
from contextlib import suppress

from ..config import PROJECT_ROOT

# The providers JAMES can talk to out of the box.
PROVIDERS = [
    "openai",
    "anthropic",
    "gemini",
    "openrouter",
    "groq",
    "mistral",
    "xai",
    "deepseek",
    "together",
    "cerebras",
    "cohere",
    "custom",
]

# env key used by each provider to hold the API token
PROVIDER_KEY = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together": "TOGETHER_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "cohere": "COHERE_API_KEY",
    "custom": "CUSTOM_API_KEY",
}

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
    "gemini": "gemini-2.0-flash",
    "openrouter": "deepseek/deepseek-chat",
    "groq": "llama-3.3-70b-versatile",
    "mistral": "mistral-large-latest",
    "xai": "grok-3",
    "deepseek": "deepseek-chat",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "cerebras": "llama-3.3-70b",
    "cohere": "command-r-plus",
    "custom": "local-model",
}

# Curated model list offered by the interactive pickers (CLI + desktop).
# ``None`` means "let the user type a free-form id" (used for `custom`).
PROVIDER_MODELS: dict[str, list[str] | None] = {
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4-turbo",
        "o1-mini",
        "o1-preview",
        "o3-mini",
    ],
    "anthropic": [
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "claude-3-opus-latest",
        "claude-sonnet-4-20250514",
        "claude-opus-4-1",
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
    "openrouter": [
        "deepseek/deepseek-chat",
        "anthropic/claude-3.5-sonnet",
        "google/gemini-2.0-flash-001",
        "mistralai/mistral-large-latest",
        "meta-llama/llama-3.3-70b-instruct",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
        "deepseek-r1-distill-llama-70b",
    ],
    "mistral": [
        "mistral-large-latest",
        "mistral-medium-latest",
        "codestral-latest",
        "ministral-8b-latest",
    ],
    "xai": ["grok-3", "grok-3-mini", "grok-2-latest", "grok-2-vision-latest"],
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-coder",
    ],
    "together": [
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        "deepseek-ai/DeepSeek-V3",
        "Qwen/Qwen2.5-72B-Instruct-Turbo",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
    ],
    "cerebras": ["llama-3.3-70b", "llama-3.1-8b", "llama-3.1-70b"],
    "cohere": [
        "command-r-plus",
        "command-r-plus-08-2024",
        "command-r-08-2024",
    ],
    "custom": None,
}


def default_model(provider: str) -> str:
    """The model JAMES uses for a provider when none is chosen."""
    return DEFAULT_MODELS.get(provider.lower(), "gpt-4o-mini")


def model_choices(provider: str) -> list[str]:
    """The selectable model ids for a provider (empty for free-form custom)."""
    return PROVIDER_MODELS.get(provider.lower()) or []


def save_llm_config(provider: str, model: str) -> None:
    """Persist ``LLM_PROVIDER`` / ``LLM_MODEL`` to the user's ``.env``.

    Updates existing lines in place when present; appends otherwise. Never
    writes secrets — only the two provider/model keys.
    """
    env_path = PROJECT_ROOT / ".env"
    replacements = {"LLM_PROVIDER": provider, "LLM_MODEL": model}

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        rendered: list[str] = []
        for key, value in replacements.items():
            rendered.append(f"{key}={value}")
        for line in lines:
            stripped = line.strip()
            key = stripped.partition("=")[0].strip()
            if key in replacements:
                continue
            rendered.append(line)
        body = "\n".join(rendered) + "\n"
    else:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f"{k}={v}" for k, v in replacements.items()) + "\n"

    env_path.write_text(body, encoding="utf-8")
    with suppress(Exception):
        os.chmod(env_path, 0o600)
