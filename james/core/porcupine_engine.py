"""Low-power wake-word detection via Picovoice Porcupine (optional extra).

Install with: pip install pvporcupine
The engine is only constructed when ``WAKE_ENGINE=porcupine`` and the package
is present; otherwise the Assistant falls back to continuous-listening mode.
"""

from __future__ import annotations

import logging
from contextlib import suppress

log = logging.getLogger("james")


class PorcupineWakeEngine:
    def __init__(self, access_key: str | None = None, keyword: str = "jarvis"):
        import pvporcupine
        import sounddevice as sd  # noqa: F401

        self._pv = pvporcupine
        self.porcupine = pvporcupine.create(
            access_key=access_key or "",
            keywords=[keyword],
        )
        self.sample_rate = self.porcupine.sample_rate
        self.frame_length = self.porcupine.frame_length
        self._channels = 1

    def listen(self, timeout: float = 30.0) -> bool:
        """Block until the wake word is heard. Returns True if triggered."""
        import sounddevice as sd

        frames = []
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self._channels,
                dtype="int16",
            ) as stream:
                deadline = None if not timeout else __import__("time").time() + timeout
                while True:
                    audio = stream.read(self.frame_length, dtype="int16")
                    pcm = audio[0]
                    pcm = pcm.flatten() if hasattr(pcm, "flatten") else pcm
                    frames.append(pcm)
                    keyword_index = self.porcupine.process(pcm)
                    if keyword_index >= 0:
                        return True
                    if deadline is not None and __import__("time").time() > deadline:
                        return False
        except Exception as exc:
            log.warning("Porcupine listen error: %s", exc)
            return False

    def close(self) -> None:
        with suppress(Exception):
            self.porcupine.delete()
