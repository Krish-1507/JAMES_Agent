"""Text-to-speech providers.

* ``pyttsx3``  — offline, cross-platform (default).
* ``edge``     — Microsoft Edge online voices (free, high quality).
* ``openai``   — OpenAI TTS.
* ``elevenlabs`` — ElevenLabs voices.
* ``none``     — text only.
"""
from __future__ import annotations

import tempfile

from ..config import VoiceSettings


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


def _play_audio(path: str) -> None:
    try:
        import subprocess

        subprocess.run(["ffplay", "-nodisp", "-autoexit", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        import os

        os.system(f'start "" "{path}"' if os.name == "nt" else f'xdg-open "{path}"')


class EdgeTTS(TTSProvider):
    def __init__(self, voice="en-US-AriaNeural"):
        import edge_tts

        self.voice = voice
        self.edge_tts = edge_tts

    def speak(self, text: str) -> None:
        import asyncio

        async def _say():
            comm = self.edge_tts.Communicate(text, self.voice)
            path = tempfile.mktemp(suffix=".mp3")
            await comm.save(path)
            self._play(path)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _say())
                future.result()
        else:
            asyncio.run(_say())

    @staticmethod
    def _play(path):
        _play_audio(path)


class OpenAITTS(TTSProvider):
    def __init__(self, api_key: str, voice="alloy"):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.voice = voice

    def speak(self, text: str) -> None:
        resp = self.client.audio.speech.create(model="tts-1", voice=self.voice, input=text)
        path = tempfile.mktemp(suffix=".mp3")
        resp.stream_to_file(path)
        _play_audio(path)


class ElevenLabsTTS(TTSProvider):
    def __init__(self, api_key: str, voice_id: str):
        self.api_key = api_key
        self.voice_id = voice_id

    def speak(self, text: str) -> None:
        import requests

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        r = requests.post(
            url,
            json={"text": text, "voice_settings": {"stability": 0.5, "similarity_boost": 0.7}},
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            timeout=30,
        )
        path = tempfile.mktemp(suffix=".mp3")
        open(path, "wb").write(r.content)
        EdgeTTS._play(path)


class NoneTTS(TTSProvider):
    def speak(self, text: str) -> None:
        print(text)


def build_tts(cfg: VoiceSettings) -> TTSProvider:
    if cfg.tts_provider == "none" or not cfg.enabled:
        return NoneTTS()
    if cfg.tts_provider == "edge":
        return EdgeTTS()
    if cfg.tts_provider == "openai":
        return OpenAITTS(cfg.whisper_api_key)
    if cfg.tts_provider == "elevenlabs":
        return ElevenLabsTTS(cfg.elevenlabs_api_key, cfg.elevenlabs_voice_id)
    return Pyttsx3TTS()
