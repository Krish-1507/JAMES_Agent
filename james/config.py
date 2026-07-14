"""Central configuration for JAMES.

Loads everything from environment variables (a `.env` file is supported via
python-dotenv). Every value is overridable so the project works out-of-the-box
in text-only mode without any API keys, and scales up to any provider.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at import time
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _bool(key: str, default: bool = False) -> bool:
    val = _env(key, "").lower()
    if val in ("", "0", "false", "no", "off"):
        return False if val != "" else default
    return val in ("1", "true", "yes", "on")


def _int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


@dataclass
class LLMSettings:
    provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai").lower())
    model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o-mini"))
    temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.4))
    max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 2048))
    timeout: int = field(default_factory=lambda: _int("LLM_TIMEOUT", 120))

    # provider keys / endpoints
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    custom_base_url: str = field(default_factory=lambda: _env("CUSTOM_BASE_URL", "http://localhost:11434/v1"))
    custom_api_key: str = field(default_factory=lambda: _env("CUSTOM_API_KEY"))
    openrouter_referer: str = field(default_factory=lambda: _env("OPENROUTER_HTTP_REFERER"))
    openrouter_site: str = field(default_factory=lambda: _env("OPENROUTER_SITE_NAME", "JAMES"))

    @property
    def api_key(self) -> str:
        """Resolve the API key for the currently selected provider."""
        mapping = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "openrouter": self.openrouter_api_key,
            "groq": self.groq_api_key,
            "custom": self.custom_api_key,
        }
        return mapping.get(self.provider, "")


@dataclass
class VoiceSettings:
    enabled: bool = field(default_factory=lambda: _bool("VOICE_ENABLED", True))
    stt_provider: str = field(default_factory=lambda: _env("STT_PROVIDER", "whisper_local").lower())
    tts_provider: str = field(default_factory=lambda: _env("TTS_PROVIDER", "pyttsx3").lower())
    whisper_api_key: str = field(default_factory=lambda: _env("WHISPER_API_KEY"))
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(default_factory=lambda: _env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"))
    mic_device_index: Optional[int] = field(default_factory=lambda: (int(_env("MIC_DEVICE_INDEX")) if _env("MIC_DEVICE_INDEX") else None))


@dataclass
class AssistantSettings:
    name: str = field(default_factory=lambda: _env("ASSISTANT_NAME", "JAMES"))
    user_name: str = field(default_factory=lambda: _env("USER_NAME", "User"))
    wake_word: str = field(default_factory=lambda: _env("WAKE_WORD", "jarvis").lower())
    system_prompt: str = field(default_factory=lambda: _env("SYSTEM_PROMPT"))
    workspace_dir: Path = field(default_factory=lambda: Path(_env("WORKSPACE_DIR", "./workspace")).resolve())
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())
    confirm_dangerous_actions: bool = field(default_factory=lambda: _bool("CONFIRM_DANGEROUS_ACTIONS", True))

    # Safety / capability tiers
    # mode: "standard" (read-only + safe tools) or "full" (shell/delete/apps)
    mode: str = field(default_factory=lambda: _env("JAMES_MODE", "full").lower())
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", False))
    audit_log: Path = field(default_factory=lambda: Path(_env("AUDIT_LOG", "./workspace/james_audit.log")).resolve())

    # Memory / RAG
    memory_enabled: bool = field(default_factory=lambda: _bool("MEMORY_ENABLED", True))
    memory_file: Path = field(default_factory=lambda: Path(_env("MEMORY_FILE", "./workspace/memory.jsonl")).resolve())

    # Browser automation
    browser_headless: bool = field(default_factory=lambda: _bool("BROWSER_HEADLESS", True))

    # Wake word engine: "always" (continuous listen) | "porcupine" | "none"
    wake_engine: str = field(default_factory=lambda: _env("WAKE_ENGINE", "always").lower())
    porcupine_key: str = field(default_factory=lambda: _env("PORCUPINE_KEY"))


@dataclass
class Settings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    assistant: AssistantSettings = field(default_factory=AssistantSettings)

    def __post_init__(self):
        self.assistant.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.assistant.audit_log.parent.mkdir(parents=True, exist_ok=True)
        self.assistant.memory_file.parent.mkdir(parents=True, exist_ok=True)


# A single shared instance used across the application.
settings = Settings()
