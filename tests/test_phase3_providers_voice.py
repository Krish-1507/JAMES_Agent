"""Tests for Phase 3 additions: provider presets and voice/TTS defaults."""

from __future__ import annotations

import pytest

from james.config import LLMSettings, settings
from james.llm.factory import _BASE_URLS, _OPENAI_COMPATIBLE, build_provider
from james.llm.providers import AnthropicProvider, GeminiProvider, OpenAICompatibleProvider


class TestProviderPresets:
    def test_all_expected_providers_are_openai_compatible(self) -> None:
        for provider in [
            "openai",
            "openrouter",
            "groq",
            "mistral",
            "xai",
            "deepseek",
            "together",
            "cerebras",
            "cohere",
            "custom",
        ]:
            assert provider in _OPENAI_COMPATIBLE

    def test_known_base_urls_present(self) -> None:
        assert _BASE_URLS["openai"] == "https://api.openai.com/v1"
        assert _BASE_URLS["groq"] == "https://api.groq.com/openai/v1"
        assert _BASE_URLS["mistral"] == "https://api.mistral.ai/v1"
        assert _BASE_URLS["xai"] == "https://api.x.ai/v1"
        assert _BASE_URLS["deepseek"] == "https://api.deepseek.com/v1"
        assert _BASE_URLS["together"] == "https://api.together.xyz/v1"
        assert _BASE_URLS["cerebras"] == "https://api.cerebras.ai/v1"
        assert _BASE_URLS["cohere"] == "https://api.cohere.com/v1"

    @pytest.mark.parametrize(
        "provider",
        [
            "openai",
            "openrouter",
            "groq",
            "mistral",
            "xai",
            "deepseek",
            "together",
            "cerebras",
            "cohere",
        ],
    )
    def test_preset_builds_openai_compatible_provider(self, provider: str) -> None:
        provider_instance = build_provider(
            LLMSettings(provider=provider, model="test-model", failover=[])
        )
        assert isinstance(provider_instance, OpenAICompatibleProvider)
        assert provider_instance.model == "test-model"

    def test_custom_uses_configured_base_url(self) -> None:
        provider_instance = build_provider(
            LLMSettings(
                provider="custom", model="llama3", custom_base_url="http://127.0.0.1:11434/v1",
                failover=[],
            )
        )
        assert isinstance(provider_instance, OpenAICompatibleProvider)

    def test_native_providers_keep_their_classes(self) -> None:
        assert isinstance(
            build_provider(LLMSettings(provider="anthropic", model="claude-x", failover=[])),
            AnthropicProvider,
        )
        assert isinstance(
            build_provider(LLMSettings(provider="gemini", model="gemini-x", failover=[])),
            GeminiProvider,
        )

    def test_api_key_mapping_covers_new_providers(self) -> None:
        mapping_cfg = LLMSettings(
            provider="mistral",
            mistral_api_key="mistral-key",
            xai_api_key="xai-key",
            deepseek_api_key="deepseek-key",
        )
        assert mapping_cfg.api_key == "mistral-key"
        mapping_cfg.provider = "xai"
        assert mapping_cfg.api_key == "xai-key"
        mapping_cfg.provider = "deepseek"
        assert mapping_cfg.api_key == "deepseek-key"


class TestVoiceDefaults:
    def test_tts_default_is_edge(self) -> None:
        assert settings.voice.tts_provider == "edge"

    def test_build_tts_edge_preferred_when_dep_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import james.voice.tts as tts_module

        def _no_edge(*_args, **_kwargs):
            raise ImportError("edge_tts not installed")

        def _fake_pyttsx3(*_args, **_kwargs):
            return tts_module.NoneTTS()

        monkeypatch.setattr(tts_module, "EdgeTTS", _no_edge)
        monkeypatch.setattr(tts_module, "Pyttsx3TTS", _fake_pyttsx3)
        settings.voice.tts_provider = "edge"
        result = tts_module.build_tts(settings.voice)
        assert isinstance(result, tts_module.NoneTTS)

    def test_build_tts_none_when_disabled(self) -> None:
        from james.voice.tts import NoneTTS, build_tts

        settings.voice.tts_provider = "none"
        assert isinstance(build_tts(settings.voice), NoneTTS)
        settings.voice.enabled = False
        settings.voice.tts_provider = "edge"
        assert isinstance(build_tts(settings.voice), NoneTTS)
