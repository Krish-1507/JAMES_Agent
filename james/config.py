"""Central configuration for JAMES.

Loads everything from environment variables (a `.env` file is supported via
python-dotenv). Supports encrypted `.env.gpg` files for protecting API keys
at rest. Every value is overridable so the project works out-of-the-box
in text-only mode without any API keys, and scales up to any provider.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - only used for optional .env.gpg decryption
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is optional at import time
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _decrypt_env_gpg(env_gpg_path: Path) -> dict[str, str]:
    gpg_bin = shutil.which("gpg")
    if not gpg_bin:
        return {}
    try:
        result = subprocess.run(
            [gpg_bin, "--batch", "--quiet", "--decrypt", str(env_gpg_path)],  # nosec B603 - argv list, no shell
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {}
        env_vars: dict[str, str] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env_vars[key.strip()] = value.strip()
        return env_vars
    except Exception:
        return {}


def _load_env_file() -> None:
    env_path = PROJECT_ROOT / ".env"
    env_gpg_path = PROJECT_ROOT / ".env.gpg"
    if env_gpg_path.exists():
        decrypted = _decrypt_env_gpg(env_gpg_path)
        for key, value in decrypted.items():
            if key not in os.environ:
                os.environ[key] = value
    elif env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(str(env_path))
        except Exception:  # nosec B110 - best-effort dotenv load; plain .env still works
            pass
        _warn_env_permissions(env_path)


def _load_tool_permissions() -> None:
    """Apply TOOL_<name>=true/false from the loaded environment.

    Must run after ``settings`` exists (it mutates ``settings.assistant``).
    ``TOOL_<name>=false`` denies a tool; ``TOOL_<name>=true`` removes it from
    the deny list.
    """
    try:
        for key, value in os.environ.items():
            key = key.strip()
            value = value.strip().lower()
            if key.startswith("TOOL_") and value in ("true", "false"):
                tool_name = key[5:].strip().lower()
                if value == "false" and tool_name not in settings.assistant.denied_tools:
                    settings.assistant.denied_tools.append(tool_name)
                elif value == "true" and tool_name in settings.assistant.denied_tools:
                    settings.assistant.denied_tools.remove(tool_name)
    except Exception:  # nosec B110 - best-effort env permission reconciliation
        pass


def _warn_env_permissions(env_path: Path) -> None:
    # Windows has no POSIX file modes (chmod is a no-op and st_mode always
    # reports 0666), so the permission check only makes sense on Unix.
    if os.name == "nt":
        return
    try:
        if hasattr(os, "getuid") and os.getuid() == 0:
            return
        mode = env_path.stat().st_mode
        world_readable = bool(mode & 0o004)
        group_readable = bool(mode & 0o040)
        if world_readable or group_readable:
            import warnings

            warnings.warn(
                f".env file at {env_path} is {'world' if world_readable else 'group'}-readable. "
                "API keys may be exposed. Run: chmod 600 .env",
                stacklevel=3,
            )
    except Exception:  # nosec B110 - best-effort warning; safety default is unchanged
        pass


_load_env_file()


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


def _path(key: str, default: str) -> Path:
    """Resolve a path from env, anchoring relative values to PROJECT_ROOT.

    This makes ``james`` work from any working directory: ``./workspace`` in
    the config always means ``<project>/workspace``, never the current folder.
    """
    raw = _env(key, default)
    p = Path(raw)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


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
    # Ordered failover list: "provider[:model]" entries tried after the primary on error.
    failover: list[str] = field(
        default_factory=lambda: [
            s.strip() for s in _env("LLM_FAILOVER", "").split(",") if s.strip()
        ]
    )

    # provider keys / endpoints
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    mistral_api_key: str = field(default_factory=lambda: _env("MISTRAL_API_KEY"))
    xai_api_key: str = field(default_factory=lambda: _env("XAI_API_KEY"))
    deepseek_api_key: str = field(default_factory=lambda: _env("DEEPSEEK_API_KEY"))
    together_api_key: str = field(default_factory=lambda: _env("TOGETHER_API_KEY"))
    cerebras_api_key: str = field(default_factory=lambda: _env("CEREBRAS_API_KEY"))
    cohere_api_key: str = field(default_factory=lambda: _env("COHERE_API_KEY"))
    custom_base_url: str = field(
        default_factory=lambda: _env("CUSTOM_BASE_URL", "http://localhost:11434/v1")
    )
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
            "mistral": self.mistral_api_key,
            "xai": self.xai_api_key,
            "deepseek": self.deepseek_api_key,
            "together": self.together_api_key,
            "cerebras": self.cerebras_api_key,
            "cohere": self.cohere_api_key,
            "custom": self.custom_api_key,
        }
        return mapping.get(self.provider, "")


@dataclass
class VoiceSettings:
    enabled: bool = field(default_factory=lambda: _bool("VOICE_ENABLED", True))
    stt_provider: str = field(default_factory=lambda: _env("STT_PROVIDER", "whisper_local").lower())
    tts_provider: str = field(default_factory=lambda: _env("TTS_PROVIDER", "edge").lower())
    whisper_api_key: str = field(default_factory=lambda: _env("WHISPER_API_KEY"))
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(
        default_factory=lambda: _env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    )
    mic_device_index: int | None = field(
        default_factory=lambda: int(_env("MIC_DEVICE_INDEX")) if _env("MIC_DEVICE_INDEX") else None
    )
    speaker_device_index: int | None = field(
        default_factory=lambda: int(_env("SPEAKER_DEVICE_INDEX"))
        if _env("SPEAKER_DEVICE_INDEX")
        else None
    )

    # Full-duplex voice: "off" | "auto" | "gemini_live" | "openai_realtime" | "local"
    duplex_mode: str = field(default_factory=lambda: _env("DUPLEX_MODE", "off").lower())
    # "auto" prefers a native cloud session when its key exists, else the local engine.
    duplex_idle_timeout: float = field(default_factory=lambda: _float("DUPLEX_IDLE_TIMEOUT", 30.0))
    # Native-session model ids (overridable for previews).
    gemini_live_model: str = field(
        default_factory=lambda: _env("GEMINI_LIVE_MODEL", "gemini-2.0-flash-live-001")
    )
    openai_realtime_model: str = field(
        default_factory=lambda: _env("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
    )
    # Native-session voices.
    gemini_live_voice: str = field(default_factory=lambda: _env("GEMINI_LIVE_VOICE", "Puck"))
    openai_realtime_voice: str = field(
        default_factory=lambda: _env("OPENAI_REALTIME_VOICE", "alloy")
    )
    # Local-engine voice (edge-tts voice name).
    duplex_edge_voice: str = field(
        default_factory=lambda: _env("DUPLEX_EDGE_VOICE", "en-US-AriaNeural")
    )
    # Local-engine speech-to-text model (faster-whisper when installed, else openai-whisper).
    streaming_stt_model: str = field(default_factory=lambda: _env("STREAMING_STT_MODEL", "small"))
    # Voice activity detection / barge-in sensitivity (0..1 RMS levels).
    vad_threshold: float = field(default_factory=lambda: _float("VAD_THRESHOLD", 0.02))
    barge_in_threshold: float = field(default_factory=lambda: _float("BARGE_IN_THRESHOLD", 0.03))


@dataclass
class AssistantSettings:
    name: str = field(default_factory=lambda: _env("ASSISTANT_NAME", "JAMES"))
    user_name: str = field(default_factory=lambda: _env("USER_NAME", "User"))
    wake_word: str = field(default_factory=lambda: _env("WAKE_WORD", "jarvis").lower())
    system_prompt: str = field(default_factory=lambda: _env("SYSTEM_PROMPT"))
    workspace_dir: Path = field(default_factory=lambda: _path("WORKSPACE_DIR", "./workspace"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())
    confirm_dangerous_actions: bool = field(
        default_factory=lambda: _bool("CONFIRM_DANGEROUS_ACTIONS", True)
    )

    # Safety / capability tiers
    # mode: "standard" (read-only + safe tools) or "full" (shell/delete/apps)
    mode: str = field(default_factory=lambda: _env("JAMES_MODE", "standard").lower())
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", False))
    audit_log: Path = field(
        default_factory=lambda: _path("AUDIT_LOG", "./workspace/james_audit.log")
    )

    # Memory / RAG
    memory_enabled: bool = field(default_factory=lambda: _bool("MEMORY_ENABLED", True))
    memory_file: Path = field(
        default_factory=lambda: _path("MEMORY_FILE", "./workspace/memory.jsonl")
    )
    # Semantic memory uses sentence-transformers when installed; set to "false" to force keyword fallback.
    memory_embedding: bool = field(default_factory=lambda: _bool("MEMORY_EMBEDDING", True))

    # Self-improving Skill Forge: auto-generate a native @tool from a successful multi-tool task.
    auto_skill: bool = field(default_factory=lambda: _bool("AUTO_SKILL", False))

    # Generated skills use a constrained loader. Arbitrary local Python plugins
    # are trusted code and must be enabled explicitly.
    external_plugins_enabled: bool = field(
        default_factory=lambda: _bool("ENABLE_TRUSTED_EXTERNAL_PLUGINS", False)
    )

    # Autonomous File Explorer Manager: let JAMES take 100% agentic control of the
    # filesystem in the background. AUTO_FILE_MANAGER launches a daemon that keeps the
    # user's main folders organised on an interval.
    auto_file_manager: bool = field(default_factory=lambda: _bool("AUTO_FILE_MANAGER", False))
    file_manager_interval: int = field(default_factory=lambda: _int("FILE_MANAGER_INTERVAL", 1800))
    file_manager_scopes: list[str] = field(
        default_factory=lambda: [
            s.strip()
            for s in _env("FILE_MANAGER_SCOPES", "Desktop,Documents,Downloads").split(",")
            if s.strip()
        ]
    )

    # Privacy-certified local mode: block ALL non-loopback network egress and audit every attempt.
    offline_mode: bool = field(default_factory=lambda: _bool("OFFLINE_MODE", False))
    egress_audit_log: Path = field(
        default_factory=lambda: _path("EGRESS_AUDIT_LOG", "./workspace/james_egress.log")
    )

    # Per-tool permission granularity: allow or deny specific tools by name.
    # Empty means use the default DANGEROUS_TOOLS binary mode.
    # When set, only tools in allowed_tools are permitted (denied_tools is ignored).
    # Can also be configured in .env as TOOL_<name>=true/false
    allowed_tools: list[str] = field(
        default_factory=lambda: [
            s.strip() for s in _env("ALLOWED_TOOLS", "").split(",") if s.strip()
        ]
    )
    denied_tools: list[str] = field(
        default_factory=lambda: [
            s.strip() for s in _env("DENIED_TOOLS", "").split(",") if s.strip()
        ]
    )

    history_file: Path = field(
        default_factory=lambda: _path(
            "CONVERSATION_HISTORY", "./workspace/conversation_history.enc"
        )
    )
    # Vision model for computer-use / image understanding (defaults to the main LLM model).
    vision_model: str = field(default_factory=lambda: _env("VISION_MODEL", ""))

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
        self.assistant.egress_audit_log.parent.mkdir(parents=True, exist_ok=True)
        self.assistant.history_file.parent.mkdir(parents=True, exist_ok=True)


# A single shared instance used across the application.
settings = Settings()
_load_tool_permissions()


def configure_settings(overrides: dict | None = None) -> Settings:
    """Create a fresh Settings instance, optionally with overrides.

    Useful for testing — avoids module-level singleton state leaking between tests.
    """
    if overrides:
        for key, value in overrides.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
    return settings
