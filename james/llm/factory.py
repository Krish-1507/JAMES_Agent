"""Provider factory — turns :class:`LLMSettings` into a live provider.

Supports an ordered **failover**: if the primary provider errors, JAMES
automatically retries the next one, so a flaky API or rate limit never kills a
task (this is what OpenClaw calls "model failover").
"""
from __future__ import annotations

from typing import Any, List

from ..config import LLMSettings
from .base import LLMProvider, LLMResponse, Message, Tool, ToolCall
from .providers import AnthropicProvider, GeminiProvider, OpenAICompatibleProvider

_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def _build_one(provider: str, model: str, settings: LLMSettings) -> LLMProvider:
    provider = provider.lower()
    if provider in ("openai", "openrouter", "groq", "custom"):
        base_url = _BASE_URLS.get(provider, settings.custom_base_url)
        extra_headers = {}
        if provider == "openrouter":
            if settings.openrouter_referer:
                extra_headers["HTTP-Referer"] = settings.openrouter_referer
            extra_headers["X-Title"] = settings.openrouter_site
        return OpenAICompatibleProvider(
            api_key=settings.api_key if provider != "custom" else settings.custom_api_key,
            model=model,
            base_url=base_url,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout,
            extra_headers=extra_headers or None,
        )
    if provider == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout,
        )
    if provider == "gemini":
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout,
        )
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def _is_permanent_error(exc: Exception) -> bool:
    """Return True for errors that won't heal by retrying another provider.

    Auth/permission problems are permanent for a given key, so retrying a
    different provider (which may use the *same* bad key) just wastes time and
    can rack up unnecessary requests. Transient issues (rate limits, timeouts,
    5xx, network) are retried.
    """
    etype = type(exc).__name__
    if "Authentication" in etype or "Permission" in etype or "Authorization" in etype:
        return True
    msg = str(exc).lower()
    markers = ("401", "403", "authentication", "api key", "incorrect api",
               "permission", "forbidden", "unauthorized", "invalid api key")
    return any(m in msg for m in markers)


class FailoverProvider(LLMProvider):
    name = "failover"

    def __init__(self, providers: List[LLMProvider]):
        self.providers = providers

    def validate(self) -> None:
        self.providers[0].validate()

    def chat(self, messages, tools=None, tool_choice="auto") -> LLMResponse:
        last: Exception | None = None
        for i, p in enumerate(self.providers):
            label = getattr(p, "name", f"provider[{i}]")
            try:
                if len(self.providers) > 1:
                    print(f"[failover] trying {label}...")
                resp = p.chat(messages, tools=tools, tool_choice=tool_choice)
                if len(self.providers) > 1:
                    print(f"[failover] OK served by {label}")
                return resp
            except Exception as exc:  # try the next provider
                if _is_permanent_error(exc):
                    raise
                last = exc
                print(f"[failover] {label} failed ({exc}); retrying next...")
                continue
        raise RuntimeError(f"All {len(self.providers)} providers failed. Last error: {last}")


def build_provider(settings: LLMSettings) -> LLMProvider:
    primary = _build_one(settings.provider, settings.model, settings)
    if not settings.failover:
        return primary

    fallbacks: List[LLMProvider] = []
    for spec in settings.failover:
        prov, _, model = spec.partition(":")
        model = model.strip() or settings.model
        try:
            fallbacks.append(_build_one(prov.strip(), model.strip(), settings))
        except ValueError:
            continue
    if not fallbacks:
        return primary
    return FailoverProvider([primary, *fallbacks])
