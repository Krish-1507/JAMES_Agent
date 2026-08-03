"""Text-to-speech providers."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess  # nosec B404 - required for local audio playback fallbacks
import tempfile
import threading
from contextlib import suppress
from pathlib import Path

from ..config import VoiceSettings

_AUDIO_CLEANUP_DELAY_SECONDS = 300


class TTSProvider:
    def speak(self, text: str) -> None:
        raise NotImplementedError


class Pyttsx3TTS(TTSProvider):
    def __init__(self):
        import pyttsx3

        self.engine = pyttsx3.init()

    def speak(self, text: str) -> None:
        self.engine.say(text)
        self.engine.runAndWait()


def _new_audio_path() -> str:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
        return handle.name


def _schedule_audio_cleanup(path: str) -> None:
    def cleanup() -> None:
        with suppress(OSError):
            Path(path).unlink(missing_ok=True)

    timer = threading.Timer(_AUDIO_CLEANUP_DELAY_SECONDS, cleanup)
    timer.daemon = True
    timer.start()


def _play_audio(path: str) -> None:
    """Play an audio file without invoking a command shell."""
    ffplay = shutil.which("ffplay")
    if ffplay:
        try:
            subprocess.run(  # nosec B603 - argv list, no shell; path is a silent, generated tmpfile arg
                [ffplay, "-nodisp", "-autoexit", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            return
        except (OSError, subprocess.CalledProcessError):
            pass

    system = platform.system()
    if system == "Windows":
        os.startfile(path)  # nosec B606 - platform API, not a shell  # type: ignore[attr-defined]
    elif system == "Darwin":
        opener = shutil.which("open")
        if opener:
            subprocess.run([opener, path], check=True)  # nosec B603 - argv list, no shell
    else:
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.run([opener, path], check=True)  # nosec B603 - argv list, no shell


def _play_and_cleanup(path: str) -> None:
    try:
        _play_audio(path)
    finally:
        _schedule_audio_cleanup(path)


class EdgeTTS(TTSProvider):
    def __init__(self, voice: str = "en-US-AriaNeural"):
        import edge_tts

        self.voice = voice
        self.edge_tts = edge_tts

    def speak(self, text: str) -> None:
        async def say() -> None:
            path = _new_audio_path()
            try:
                communication = self.edge_tts.Communicate(text, self.voice)
                await communication.save(path)
                _play_and_cleanup(path)
            except Exception:
                _schedule_audio_cleanup(path)
                raise

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, say()).result()
        else:
            asyncio.run(say())

    @staticmethod
    def _play(path: str) -> None:
        _play_and_cleanup(path)


class OpenAITTS(TTSProvider):
    def __init__(self, api_key: str, voice: str = "alloy"):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.voice = voice

    def speak(self, text: str) -> None:
        path = _new_audio_path()
        try:
            response = self.client.audio.speech.create(model="tts-1", voice=self.voice, input=text)
            response.stream_to_file(path)
            _play_and_cleanup(path)
        except Exception:
            _schedule_audio_cleanup(path)
            raise


class ElevenLabsTTS(TTSProvider):
    def __init__(self, api_key: str, voice_id: str):
        self.api_key = api_key
        self.voice_id = voice_id

    def speak(self, text: str) -> None:
        import requests

        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
            json={"text": text, "voice_settings": {"stability": 0.5, "similarity_boost": 0.7}},
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        path = _new_audio_path()
        try:
            Path(path).write_bytes(response.content)
            _play_and_cleanup(path)
        except Exception:
            _schedule_audio_cleanup(path)
            raise


class NoneTTS(TTSProvider):
    def speak(self, text: str) -> None:
        print(text)


def build_tts(cfg: VoiceSettings) -> TTSProvider:
    if cfg.tts_provider == "none" or not cfg.enabled:
        return NoneTTS()
    try:
        if cfg.tts_provider == "edge":
            return EdgeTTS()
        if cfg.tts_provider == "openai":
            return OpenAITTS(cfg.whisper_api_key)
        if cfg.tts_provider == "elevenlabs":
            return ElevenLabsTTS(cfg.elevenlabs_api_key, cfg.elevenlabs_voice_id)
        return Pyttsx3TTS()
    except ImportError:
        pass
    # The requested provider's optional dependency is missing. If edge was
    # requested, fall back to pyttsx3 (still audible) before giving up to text.
    if cfg.tts_provider == "edge":
        try:
            return Pyttsx3TTS()
        except ImportError:
            pass
    return NoneTTS()
