"""Provider factory — turns :class:`LLMSettings` into a live provider."""
from __future__ import annotations

from typing import Any

from ..config import LLMSettings
from .base import LLMProvider
from .providers import AnthropicProvider, GeminiProvider, OpenAICompatibleProvider

_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def build_provider(settings: LLMSettings) -> LLMProvider:
    provider = settings.provider
    model = settings.model

    if provider in ("openai", "openrouter", "groq", "custom"):
        base_url = _BASE_URLS.get(provider, settings.custom_base_url)
        extra_headers = {}
        if provider == "openrouter":
            if settings.openrouter_referer:
                extra_headers["HTTP-Referer"] = settings.openrouter_referer
            extra_headers["X-Title"] = settings.openrouter_site
        return OpenAICompatibleProvider(
            api_key=settings.api_key,
            model=model,
            base_url=base_url,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout,
            extra_headers=extra_headers or None,
        )

    if provider == "anthropic":
        return AnthropicProvider(
            api_key=settings.api_key,
            model=model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout,
        )

    if provider == "gemini":
        return GeminiProvider(
            api_key=settings.api_key,
            model=model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout,
        )

    raise ValueError(f"Unknown LLM provider: {provider!r}")
