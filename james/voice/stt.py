"""Speech-to-text providers.

* ``whisper_local`` — fully offline transcription via openai-whisper.
* ``whisper_api``   — OpenAI's cloud Whisper.
* ``google``        — Google Speech Recognition (no key required).
* ``none``          — falls back to typed text input.
"""
from __future__ import annotations

import io
import tempfile
import wave

from ..config import VoiceSettings


class STTProvider:
    def listen(self) -> str:
        raise NotImplementedError


class GoogleSTT(STTProvider):
    def __init__(self, mic_index=None):
        self.mic_index = mic_index

    def listen(self) -> str:
        import speech_recognition as sr

        r = sr.Recognizer()
        with sr.Microphone(device_index=self.mic_index) as src:
            r.adjust_for_ambient_noise(src, duration=0.3)
            audio = r.listen(src)
        try:
            return r.recognize_google(audio)
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            return ""


class WhisperApiSTT(STTProvider):
    def __init__(self, api_key: str, mic_index=None):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.mic_index = mic_index

    def listen(self) -> str:
        audio = self._record()
        if not audio:
            return ""
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio.get_wav_data())
                path = f.name
            resp = self.client.audio.transcriptions.create(model="whisper-1", file=open(path, "rb"))
            return resp.text or ""
        finally:
            if path:
                try:
                    import os

                    os.unlink(path)
                except OSError:
                    pass


class WhisperLocalSTT(STTProvider):
    def __init__(self, mic_index=None, model="base"):
        import whisper

        self.model = whisper.load_model(model)
        self.mic_index = mic_index

    def listen(self) -> str:
        audio = self._record()
        if not audio:
            return ""
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio.get_wav_data())
                path = f.name
            result = self.model.transcribe(path)
            return result.get("text", "").strip()
        finally:
            if path:
                try:
                    import os

                    os.unlink(path)
                except OSError:
                    pass


class TextSTT(STTProvider):
    def listen(self) -> str:
        try:
            return input("You: ").strip()
        except EOFError:
            return ""


def _record(mic_index=None) -> "sr.AudioData | None":
    import speech_recognition as sr

    r = sr.Recognizer()
    with sr.Microphone(device_index=mic_index) as src:
        r.adjust_for_ambient_noise(src, duration=0.3)
        return r.listen(src)


def build_stt(cfg: VoiceSettings) -> STTProvider:
    if cfg.stt_provider == "none" or not cfg.enabled:
        return TextSTT()
    if cfg.stt_provider == "whisper_api":
        return WhisperApiSTT(cfg.whisper_api_key, cfg.mic_device_index)
    if cfg.stt_provider == "whisper_local":
        return WhisperLocalSTT(cfg.mic_device_index)
    return GoogleSTT(cfg.mic_device_index)
