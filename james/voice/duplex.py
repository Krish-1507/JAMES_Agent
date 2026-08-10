"""Full-duplex voice: simultaneous speak and listen with interruption.

Three interchangeable engines behind one controller:

* ``gemini_live``     — Gemini Live API (google-genai): true speech-to-speech,
  server-side VAD, native function calling. Audio is 16 kHz 16-bit PCM.
* ``openai_realtime`` — OpenAI Realtime API: speech-to-speech over WebSocket,
  server VAD and barge-in, function calling. Audio is 24 kHz 16-bit PCM.
* ``local``           — fully local streaming pipeline: VAD segmentation +
  faster-whisper (falls back to openai-whisper) + edge-tts streaming playback
  via ffmpeg, with mic-level barge-in detection. No cloud audio API.

The :class:`DuplexController` owns the always-on state machine:

    IDLE  (wake gate armed: "always" / "porcupine" / "none")
      └─ on wake word  →  ACTIVE (session open, duplex conversation)
      └─ on idle timeout or spoken exit  →  back to IDLE

Text stays first-class: :meth:`DuplexController.send_text` injects a typed
turn into the live session at any moment (even while idle it wakes it first).

The controller emits JAMES events through the assistant (``on_event``) so the
orb GUI and CLI show listening / transcribing / speaking states live.
"""

from __future__ import annotations

import array
import asyncio
import base64
import json
import logging
import math
import queue
import shutil
import subprocess  # nosec B404 - ffmpeg decoder child process (no shell)
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from ..config import VoiceSettings, settings

log = logging.getLogger("james")

GEMINI_SAMPLE_RATE = 16_000
OPENAI_SAMPLE_RATE = 24_000
LOCAL_SAMPLE_RATE = 16_000
_FRAME_MS = 30  # VAD frame size (30 ms works with webrtcvad at 16 kHz)

_VOICE_SYSTEM_HINT = (
    "\n\nYou are having a real-time voice conversation with the user. "
    "Keep replies conversational and concise (2-4 sentences unless the task "
    "needs more). No markdown, no bullet lists, no code fences unless essential. "
    "You can call tools to complete tasks, then report the result briefly."
)


class DuplexUnavailableError(RuntimeError):
    """Raised when a duplex engine cannot start (missing key / dependency)."""


# ---------------------------------------------------------------------------
# PCM helpers
# ---------------------------------------------------------------------------


def pcm_rms(frame: bytes, sample_width: int = 2) -> float:
    """Root-mean-square level of 16-bit mono PCM, normalized to 0..1."""
    if not frame:
        return 0.0
    usable = frame[: len(frame) - (len(frame) % sample_width)]
    if not usable:
        return 0.0
    samples = array.array("h")
    samples.frombytes(usable)
    if not samples:
        return 0.0
    mean_sq = sum(s * s for s in samples) / len(samples)
    return min(1.0, math.sqrt(mean_sq) / 32768.0)


# ---------------------------------------------------------------------------
# Voice activity detection
# ---------------------------------------------------------------------------


class VAD:
    """Energy-based voice activity detection over 16-bit mono PCM frames.

    Optionally votes with webrtcvad when installed (picks up quieter speech).
    ``feed`` returns state transitions:

    * ``("start", pcm)``  — speech began (includes ~300 ms pre-roll)
    * ``("speech", pcm)`` — speech continues
    * ``("end", pcm)``    — speech ended after ``min_silence_ms``
    * ``None``            — silence
    """

    def __init__(
        self,
        sample_rate: int = LOCAL_SAMPLE_RATE,
        threshold: float = 0.02,
        frame_ms: int = _FRAME_MS,
        min_speech_ms: int = 300,
        min_silence_ms: int = 700,
    ):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.frame_ms = frame_ms
        self.frame_bytes = sample_rate * frame_ms // 1000 * 2
        self.min_speech_ms = min_speech_ms
        self.min_silence_ms = min_silence_ms
        self._speech = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._pre_roll: list[bytes] = []
        self._pre_roll_ms = 300
        try:
            import webrtcvad  # type: ignore[import-not-found]

            self._webrtcvad = webrtcvad.Vad(2)
        except ImportError:
            self._webrtcvad = None

    def _is_voice(self, frame: bytes) -> bool:
        if pcm_rms(frame) >= self.threshold:
            return True
        if self._webrtcvad is not None and len(frame) == self.frame_bytes:
            try:
                return bool(self._webrtcvad.is_speech(frame, self.sample_rate))
            except Exception:
                return False
        return False

    def feed(self, frame: bytes) -> tuple[str, bytes] | None:
        if len(frame) < self.frame_bytes * 0.9:
            return None
        talking = self._is_voice(frame)

        if not self._speech:
            if talking:
                self._speech_ms += self.frame_ms
                self._pre_roll.append(frame)
                if len(self._pre_roll) > self._pre_roll_ms // self.frame_ms:
                    self._pre_roll.pop(0)
                if self._speech_ms >= self.min_speech_ms:
                    self._speech = True
                    self._silence_ms = 0
                    chunk = b"".join(self._pre_roll)
                    self._pre_roll = []
                    return ("start", chunk)
            else:
                self._speech_ms = 0
                self._pre_roll.clear()
            return None

        if talking:
            self._silence_ms = 0
            self._speech_ms += self.frame_ms
            return ("speech", frame)

        self._silence_ms += self.frame_ms
        if self._silence_ms >= self.min_silence_ms:
            self._speech = False
            self._speech_ms = 0
            self._silence_ms = 0
            return ("end", frame)
        return ("speech", frame)


# ---------------------------------------------------------------------------
# Audio device helpers (PyAudio)
# ---------------------------------------------------------------------------


class AudioInput:
    """16-bit mono PCM input stream. ``read()`` blocks for one frame."""

    def __init__(
        self,
        device_index: int | None = None,
        rate: int = LOCAL_SAMPLE_RATE,
        frame_ms: int = _FRAME_MS,
    ):
        self.device_index = device_index
        self.rate = rate
        self.frame_bytes = rate * frame_ms // 1000 * 2
        self._pa = None
        self._stream = None

    def open(self) -> None:
        import pyaudio

        self._pa = pyaudio.PyAudio()
        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.frame_bytes // 2,
            )
        except Exception:
            self._pa.terminate()
            raise
        log.info("AudioInput open: device=%s rate=%d", self.device_index, self.rate)

    def read(self) -> bytes:
        if self._stream is None:
            raise RuntimeError("AudioInput not open")
        data = self._stream.read(self.frame_bytes // 2, exception_on_overflow=False)
        return data if isinstance(data, bytes) else b"".join(data)

    def close(self) -> None:
        with suppress(Exception):
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        self._stream = None
        with suppress(Exception):
            if self._pa is not None:
                self._pa.terminate()
        self._pa = None

    def __enter__(self) -> AudioInput:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class AudioOutput:
    """16-bit mono PCM output stream. ``reset()`` hard-stops playback."""

    def __init__(self, device_index: int | None = None, rate: int = OPENAI_SAMPLE_RATE):
        self.device_index = device_index
        self.rate = rate
        self._pa = None
        self._stream = None
        self._lock = threading.Lock()

    def open(self) -> None:
        import pyaudio

        self._pa = pyaudio.PyAudio()
        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.rate,
                output=True,
                output_device_index=self.device_index,
                frames_per_buffer=480,
            )
        except Exception:
            self._pa.terminate()
            raise

    def write(self, pcm: bytes) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.write(pcm)

    def reset(self) -> None:
        """Close and reopen the stream — used to cut audio on interrupt."""
        with self._lock:
            self.close()
            with suppress(Exception):
                self.open()

    def close(self) -> None:
        with self._lock:
            with suppress(Exception):
                if self._stream is not None:
                    self._stream.stop_stream()
                    self._stream.close()
            self._stream = None
            with suppress(Exception):
                if self._pa is not None:
                    self._pa.terminate()
            self._pa = None

    def __enter__(self) -> AudioOutput:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Session callbacks
# ---------------------------------------------------------------------------


@dataclass
class DuplexCallbacks:
    on_state: Callable[[str], None] | None = (
        None  # idle|listening|transcribing|thinking|speaking|error
    )
    on_partial: Callable[[str], None] | None = None
    on_user_text: Callable[[str], None] | None = None
    on_assistant_text: Callable[[str], None] | None = None
    on_audio: Callable[[bytes], None] | None = None
    on_activity: Callable[[], None] | None = None
    on_interrupt: Callable[[], None] | None = None
    on_tool_call: Callable[[str, str, dict], str] | None = None  # (call_id, name, args) -> result
    on_level: Callable[[float], None] | None = None
    on_error: Callable[[str], None] | None = None


class DuplexSession(ABC):
    """A live full-duplex conversation session. Runs its own threads once
    ``open()`` succeeds; the controller pumps mic audio via ``send_audio``."""

    sample_rate: int = LOCAL_SAMPLE_RATE

    @abstractmethod
    def open(self) -> None:
        """Connect and start session threads. Raises on failure."""

    @abstractmethod
    def send_audio(self, chunk: bytes) -> None:
        """Feed one 16-bit mono PCM chunk (at ``sample_rate``)."""

    @abstractmethod
    def send_text(self, text: str) -> None:
        """Inject a typed user turn."""

    @abstractmethod
    def interrupt(self) -> None:
        """Stop the current assistant response immediately."""

    @abstractmethod
    def close(self) -> None:
        """Tear the session down."""

    @property
    def alive(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Gemini Live session (native duplex, asynchronous)
# ---------------------------------------------------------------------------


class GeminiLiveSession(DuplexSession):
    """Gemini Live API speech-to-speech session (google-genai, asyncio thread)."""

    sample_rate = GEMINI_SAMPLE_RATE

    def __init__(
        self,
        callbacks: DuplexCallbacks,
        *,
        api_key: str,
        model: str = "gemini-2.0-flash-live-001",
        system_prompt: str = "",
        tools: list[dict] | None = None,
        voice: str = "Puck",
    ):
        self._callbacks = callbacks
        self._api_key = api_key
        self._model = model
        self._system_prompt = system_prompt
        self._tools = tools or []
        self._voice = voice
        self._send_q: queue.Queue = queue.Queue()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = None

    # -- public API ----------------------------------------------------------

    def open(self) -> None:
        if not self._api_key:
            raise DuplexUnavailableError(
                "GEMINI_API_KEY is not set (needed for DUPLEX_MODE=gemini_live)"
            )
        self._closed.clear()
        self._thread = threading.Thread(target=self._run, name="gemini-live", daemon=True)
        self._thread.start()

    def send_audio(self, chunk: bytes) -> None:
        if not self._closed.is_set():
            self._send_q.put(("audio", chunk))

    def send_text(self, text: str) -> None:
        if not self._closed.is_set():
            self._send_q.put(("text", text))

    def interrupt(self) -> None:
        # Gemini's server-side VAD handles interruptions when the user speaks;
        # a UI interrupt just cuts local playback (controller side) and nudges
        # the model with an activity restart signal.
        if not self._closed.is_set():
            self._send_q.put(("interrupt",))

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._send_q.put(("close",))
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- internals -----------------------------------------------------------

    def _run(self) -> None:
        with suppress(Exception):
            asyncio.run(self._amain())

    async def _amain(self) -> None:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        decls = [
            types.FunctionDeclaration(
                name=t["function"]["name"],
                description=t["function"]["description"],
                parameters_json_schema=t["function"]["parameters"],
            )
            for t in self._tools
        ]
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice)
                )
            ),
            system_instruction=types.Content(parts=[types.Part(text=self._system_prompt)]),
            tools=[types.Tool(function_declarations=decls)] if decls else None,
        )
        try:
            async for session in client.aio.live.connect(model=self._model, config=config):
                self._session = session
                try:
                    await self._pump_and_receive(session)
                finally:
                    with suppress(Exception):
                        await session.close()
                break  # connection ended; do not silently reconnect
        except Exception as exc:
            self._closed.set()
            if self._callbacks.on_error:
                with suppress(Exception):
                    self._callbacks.on_error(f"Gemini Live error: {exc}")
            log.warning("Gemini Live session ended: %s", exc)

    async def _pump_and_receive(self, session) -> None:
        recv = asyncio.create_task(self._receive_loop(session))
        send = asyncio.create_task(self._send_loop(session))
        done, pending = await asyncio.wait({recv, send}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                raise task.exception()

    async def _send_loop(self, session) -> None:
        from google.genai import types

        while True:
            try:
                item = await asyncio.to_thread(self._send_q.get, True, 0.2)
            except queue.Empty:
                if self._closed.is_set():
                    return
                continue
            kind = item[0]
            if kind == "close":
                return
            if kind == "audio":
                await session.send_realtime_input(audio=item[1])
            elif kind == "text":
                await session.send_client_content(
                    types.LiveClientContent(
                        turns=[types.Content(role="user", parts=[types.Part(text=item[1])])]
                    )
                )
            elif kind == "interrupt":
                await session.send_realtime_input(types.LiveClientRealtimeInput(activity_end=True))
            elif kind == "tool":
                _, fc_id, name, result = item
                await session.send_tool_response(
                    types.LiveClientToolResponse(
                        function_responses=[
                            types.FunctionResponse(id=fc_id, name=name, response={"result": result})
                        ]
                    )
                )

    async def _receive_loop(self, session) -> None:
        async for msg in session.receive():
            if msg is None:
                continue
            if msg.voice_activity_detection_signal is not None:
                sig = msg.voice_activity_detection_signal
                started = getattr(sig, "start", None)
                if started is True:
                    self._fire(self._callbacks.on_activity)
                    self._fire_state("listening")
                if started is False:
                    self._fire_state("transcribing")
            sc = msg.server_content
            if sc is None:
                continue
            if getattr(sc, "interrupted", False):
                self._fire_state("listening")
            for part in getattr(sc, "parts", None) or []:
                if getattr(part, "inline_data", None) is not None and part.inline_data.data:
                    self._fire_state("speaking")
                    self._fire(self._callbacks.on_audio, part.inline_data.data)
                elif getattr(part, "text", None):
                    self._fire(self._callbacks.on_assistant_text, part.text)
                elif getattr(part, "function_call", None) is not None:
                    fc = part.function_call
                    self._fire_state("thinking")
                    args = dict(getattr(fc, "args", None) or {})
                    result = await asyncio.to_thread(
                        self._exec_tool, getattr(fc, "id", ""), fc.name, args
                    )
                    self._send_q.put(("tool", getattr(fc, "id", ""), fc.name, result))
        self._closed.set()

    def _exec_tool(self, call_id: str, name: str, args: dict) -> str:
        cb = self._callbacks.on_tool_call
        if cb is None:
            return f"Tool '{name}' is not available."
        with suppress(Exception):
            return cb(call_id, name, args) or "(no output)"
        return f"Tool '{name}' failed."

    @staticmethod
    def _fire(cb: Callable | None, *args) -> None:
        if cb:
            with suppress(Exception):
                cb(*args)

    def _fire_state(self, state: str) -> None:
        self._fire(self._callbacks.on_state, state)


# ---------------------------------------------------------------------------
# OpenAI Realtime session (native duplex, synchronous WebSocket)
# ---------------------------------------------------------------------------


class OpenAIRealtimeSession(DuplexSession):
    """OpenAI Realtime API session. Server VAD, automatic barge-in,
    function calling via conversation items. 24 kHz 16-bit PCM audio."""

    sample_rate = OPENAI_SAMPLE_RATE

    def __init__(
        self,
        callbacks: DuplexCallbacks,
        *,
        api_key: str,
        model: str = "gpt-4o-realtime-preview",
        system_prompt: str = "",
        tools: list[dict] | None = None,
        voice: str = "alloy",
    ):
        self._callbacks = callbacks
        self._api_key = api_key
        self._model = model
        self._system_prompt = system_prompt
        self._tools = tools or []
        self._voice = voice
        self._send_q: queue.Queue = queue.Queue()
        self._rx_q: queue.Queue = queue.Queue()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn = None
        self._assistant_buf = ""
        self._fc_args: dict[str, str] = {}

    # -- public API ----------------------------------------------------------

    def open(self) -> None:
        if not self._api_key:
            raise DuplexUnavailableError(
                "OPENAI_API_KEY is not set (needed for DUPLEX_MODE=openai_realtime)"
            )
        self._closed.clear()
        self._thread = threading.Thread(target=self._run, name="openai-realtime", daemon=True)
        self._thread.start()

    def send_audio(self, chunk: bytes) -> None:
        if not self._closed.is_set():
            self._send_q.put(("audio", chunk))

    def send_text(self, text: str) -> None:
        if not self._closed.is_set():
            self._send_q.put(("text", text))

    def interrupt(self) -> None:
        if not self._closed.is_set():
            self._send_q.put(("interrupt",))

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._send_q.put(("close",))
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- internals -----------------------------------------------------------

    def _run(self) -> None:
        from openai import OpenAI

        try:
            client = OpenAI(api_key=self._api_key)
            with client.beta.realtime.connect(model=self._model) as conn:
                self._conn = conn
                conn.session.update(
                    session={
                        "instructions": self._system_prompt,
                        "voice": self._voice,
                        "modalities": ["audio", "text"],
                        "tools": self._tools or [],
                        "input_audio_transcription": {"model": "whisper-1"},
                    }
                )
                rx = threading.Thread(target=self._receive_pump, args=(conn,), daemon=True)
                rx.start()
                try:
                    self._event_loop(conn)
                finally:
                    self._closed.set()
                    rx.join(timeout=2)
                    with suppress(Exception):
                        conn.close()
        except Exception as exc:
            self._closed.set()
            if self._callbacks.on_error:
                with suppress(Exception):
                    self._callbacks.on_error(f"Realtime error: {exc}")
            log.warning("OpenAI Realtime session ended: %s", exc)

    def _receive_pump(self, conn) -> None:
        try:
            for event in conn:
                if self._closed.is_set():
                    return
                self._rx_q.put(event)
        except Exception as exc:
            log.debug("Realtime receive ended: %s", exc)
        finally:
            with suppress(Exception):
                self._rx_q.put(None)

    def _event_loop(self, conn) -> None:
        while True:
            try:
                item = self._send_q.get(timeout=0.05)
            except queue.Empty:
                item = None
            if item is not None:
                if item[0] == "close":
                    return
                self._send_item(conn, item)
            while True:
                try:
                    event = self._rx_q.get_nowait()
                except queue.Empty:
                    break
                if event is None:
                    return  # connection closed
                self._handle_event(conn, event)

    def _send_item(self, conn, item: tuple) -> None:
        kind = item[0]
        try:
            if kind == "audio":
                conn.input_audio_buffer.append(audio=base64.b64encode(item[1]).decode())
            elif kind == "text":
                conn.conversation.item.create(
                    item={
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": item[1]}],
                    }
                )
                conn.response.create()
            elif kind == "interrupt":
                # The server VAD handles the user speaking over the model; an
                # explicit UI interrupt truncates the in-flight response.
                with suppress(Exception):
                    conn.response.cancel()
        except Exception as exc:
            log.warning("Realtime send error: %s", exc)

    def _handle_event(self, conn, event) -> None:
        t = getattr(event, "type", "")
        try:
            if t == "conversation.item.input_audio_transcription.completed":
                text = (getattr(event, "transcript", "") or "").strip()
                if text:
                    self._fire(self._callbacks.on_activity)
                    self._fire(self._callbacks.on_user_text, text)
            elif t == "conversation.item.input_audio_transcription.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    self._fire(self._callbacks.on_partial, delta)
            elif t == "response.audio_transcript.delta":
                self._assistant_buf += getattr(event, "delta", "") or ""
            elif t == "response.audio.delta":
                self._fire_state("speaking")
                with suppress(Exception):
                    pcm = base64.b64decode(getattr(event, "delta", "") or "")
                    if pcm:
                        self._fire(self._callbacks.on_audio, pcm)
            elif t == "response.function_call_arguments.done":
                self._fc_args[getattr(event, "call_id", "")] = getattr(event, "arguments", "") or ""
            elif t == "input_audio_buffer.speech_started":
                self._fire(self._callbacks.on_activity)
                self._fire(self._callbacks.on_interrupt)
                self._fire_state("listening")
            elif t == "input_audio_buffer.speech_stopped":
                self._fire_state("transcribing")
            elif t == "response.done":
                self._finalize_response(conn, event)
            elif t == "error":
                err = getattr(event, "error", None) or {}
                msg = getattr(err, "message", "") if hasattr(err, "message") else str(err)
                self._fire(self._callbacks.on_error, f"Realtime API error: {msg}")
        except Exception as exc:
            log.warning("Realtime event error (%s): %s", t, exc)

    def _finalize_response(self, conn, event) -> None:
        buf, self._assistant_buf = self._assistant_buf, ""
        text = buf.strip()
        if text:
            self._fire(self._callbacks.on_assistant_text, text)

        response = getattr(event, "response", None)
        outputs = getattr(response, "output", None) or []
        tool_calls = [o for o in outputs if getattr(o, "type", "") == "function_call"]
        if tool_calls:
            self._fire_state("thinking")
            for item in tool_calls:
                call_id = getattr(item, "call_id", "")
                name = getattr(item, "name", "")
                raw_args = (
                    self._fc_args.pop(call_id, None) or getattr(item, "arguments", "") or "{}"
                )
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except (json.JSONDecodeError, TypeError):
                    args = {"_raw": raw_args}
                result = self._exec_tool(call_id, name, args)
                with suppress(Exception):
                    conn.conversation.item.create(
                        item={"type": "function_call_output", "call_id": call_id, "output": result}
                    )
            with suppress(Exception):
                conn.response.create()  # continue after tool results
            return
        self._fire_state("listening")

    def _exec_tool(self, call_id: str, name: str, args: dict) -> str:
        cb = self._callbacks.on_tool_call
        if cb is None:
            return f"Tool '{name}' is not available."
        with suppress(Exception):
            return cb(call_id, name, args) or "(no output)"
        return f"Tool '{name}' failed."

    @staticmethod
    def _fire(cb: Callable | None, *args) -> None:
        if cb:
            with suppress(Exception):
                cb(*args)

    def _fire_state(self, state: str) -> None:
        self._fire(self._callbacks.on_state, state)


# ---------------------------------------------------------------------------
# Local streaming speech-to-text (faster-whisper, openai-whisper fallback)
# ---------------------------------------------------------------------------


class LocalStreamingSTT:
    """Incremental transcription of a growing PCM utterance.

    faster-whisper (if installed) provides low-latency partials; otherwise
    openai-whisper transcribes at utterance end. Fully offline either way.
    """

    def __init__(self, model_size: str = "small", sample_rate: int = LOCAL_SAMPLE_RATE):
        import numpy as np

        self._np = np
        self.sample_rate = sample_rate
        self._fw = None
        self._ow = None
        self._ow_model = None
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            self._fw = WhisperModel(model_size, device="auto", compute_type="int8")
            log.info("LocalStreamingSTT: faster-whisper (%s)", model_size)
        except ImportError:
            try:
                import whisper  # type: ignore[import-not-found]

                self._ow = whisper
                self._ow_model = whisper.load_model("base")
                log.info("LocalStreamingSTT: openai-whisper (base)")
            except ImportError as exc:  # pragma: no cover - both extras missing
                raise DuplexUnavailableError(
                    "Local duplex STT needs faster-whisper or openai-whisper. "
                    "Install the [voice] extra: pip install -e '.[voice]'"
                ) from exc

    @property
    def supports_partials(self) -> bool:
        return self._fw is not None

    def _to_float(self, pcm: bytes) -> np.ndarray:  # noqa: F821 - numpy is a lazy dep
        raw = self._np.frombuffer(pcm, dtype=self._np.int16)
        return raw.astype(self._np.float32) / 32768.0

    def partial(self, pcm: bytes) -> str:
        if self._fw is None or len(pcm) < self.sample_rate // 2:
            return ""
        try:
            segments, _info = self._fw.transcribe(
                self._to_float(pcm),
                beam_size=1,
                condition_on_previous_text=False,
                vad_filter=True,
            )
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception:
            return ""

    def final(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        if self._fw is not None:
            try:
                segments, _info = self._fw.transcribe(
                    self._to_float(pcm),
                    beam_size=5,
                    condition_on_previous_text=False,
                    vad_filter=False,
                )
                return " ".join(s.text.strip() for s in segments).strip()
            except Exception as exc:
                log.warning("faster-whisper final failed: %s", exc)
        if self._ow is not None:
            import tempfile
            import wave

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                name = handle.name
            try:
                with wave.open(name, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(self.sample_rate)
                    wav.writeframes(pcm)
                result = self._ow_model.transcribe(name)
                return (result.get("text") or "").strip()
            finally:
                with suppress(OSError):
                    import os

                    os.unlink(name)
        return ""


# ---------------------------------------------------------------------------
# Streaming TTS with barge-in (local engine)
# ---------------------------------------------------------------------------


class _NullGreeter:
    """No-op stand-in used when the greeting TTS cannot be built."""

    def speak(self, text: str, **kwargs) -> bool:
        return True

    def stop(self) -> None:
        pass


class StreamTTS:
    """edge-tts streaming synthesis → ffmpeg decode → PyAudio playback.

    While the assistant speaks, a monitor thread watches the microphone; if
    the mic level crosses ``barge_threshold`` the playback is cut and
    ``on_barge_in`` fires — that is local full-duplex interruption.
    Falls back to plain (non-interruptible) provider TTS if edge-tts fails.
    """

    def __init__(
        self,
        voice: str = "en-US-AriaNeural",
        *,
        output_device: int | None = None,
        mic_device: int | None = None,
        barge_threshold: float = 0.03,
        fallback: Callable[[str], None] | None = None,
    ):
        self.voice = voice
        self.output_device = output_device
        self.mic_device = mic_device
        self.barge_threshold = barge_threshold
        self._fallback = fallback
        self._stop = threading.Event()
        self._edge = None
        try:
            import edge_tts  # type: ignore[import-not-found]

            self._edge = edge_tts
        except ImportError:
            self._edge = None

    def stop(self) -> None:
        self._stop.set()

    def speak(self, text: str, *, on_barge_in: Callable[[], None] | None = None) -> bool:
        """Speak ``text``. Returns True if finished, False if interrupted."""
        text = (text or "").strip()
        if not text:
            return True
        self._stop.clear()
        if self._edge is None or not shutil.which("ffmpeg"):
            self._fallback_speak(text)
            return True
        try:
            return self._speak_streamed(text, on_barge_in)
        except Exception as exc:
            log.warning("StreamTTS failed, falling back: %s", exc)
            self._fallback_speak(text)
            return True

    def _fallback_speak(self, text: str) -> None:
        if self._fallback is not None:
            with suppress(Exception):
                self._fallback(text)

    def _speak_streamed(self, text: str, on_barge_in: Callable[[], None] | None) -> bool:
        ffmpeg = shutil.which("ffmpeg")
        out_rate = OPENAI_SAMPLE_RATE
        proc = subprocess.Popen(  # nosec B603 - argv list, no shell
            [
                ffmpeg,
                "-i",
                "pipe:0",
                "-f",
                "s16le",
                "-ar",
                str(out_rate),
                "-ac",
                "1",
                "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        stop = self._stop
        try:

            def _feed() -> None:
                async def synthesize() -> None:
                    com = self._edge.Communicate(text, self.voice)
                    async for chunk in com.stream():
                        if stop.is_set():
                            break
                        if chunk["type"] == "audio":
                            proc.stdin.write(chunk["data"])  # type: ignore[union-attr]

                try:
                    asyncio.run(synthesize())
                except Exception as exc:
                    log.warning("edge-tts stream error: %s", exc)
                finally:
                    with suppress(Exception):
                        proc.stdin.close()  # type: ignore[union-attr]

            feeder = threading.Thread(target=_feed, daemon=True)
            feeder.start()

            barge_in = threading.Event()

            def _monitor() -> None:
                try:
                    with AudioInput(device_index=self.mic_device, rate=LOCAL_SAMPLE_RATE) as mic:
                        while not stop.is_set() and not barge_in.is_set():
                            frame = mic.read()
                            if pcm_rms(frame) > self.barge_threshold:
                                barge_in.set()
                                break
                except Exception as exc:
                    log.debug("Barge-in monitor unavailable: %s", exc)

            monitor = threading.Thread(target=_monitor, daemon=True)
            monitor.start()

            with AudioOutput(device_index=self.output_device, rate=out_rate) as out:
                while not stop.is_set() and not barge_in.is_set():
                    pcm = proc.stdout.read(4096)  # type: ignore[union-attr]
                    if not pcm:
                        break
                    out.write(pcm)
                out.reset()  # cut any trailing audio instantly
        finally:
            stop.set()
            for handle in (proc.stdin, proc.stdout):
                with suppress(Exception):
                    if handle is not None:
                        handle.close()
            with suppress(Exception):
                proc.kill()
                proc.wait(timeout=2)

        interrupted = barge_in.is_set()
        if interrupted and on_barge_in is not None:
            with suppress(Exception):
                on_barge_in()
        return not interrupted


# ---------------------------------------------------------------------------
# Local duplex engine (VAD + streaming STT + streaming TTS, interruptible)
# ---------------------------------------------------------------------------


class LocalDuplexEngine(DuplexSession):
    """The fully local full-duplex pipeline. Owns one mic stream: VAD
    segments speech, streaming STT transcribes it, the reply is spoken with
    edge-tts streaming while the mic keeps running (barge-in supported)."""

    sample_rate = LOCAL_SAMPLE_RATE

    def __init__(
        self,
        callbacks: DuplexCallbacks,
        *,
        stt: LocalStreamingSTT,
        tts: StreamTTS,
        vad_threshold: float = 0.02,
        input_device: int | None = None,
        mic_factory: Callable[[], AudioInput] | None = None,
    ):
        self._callbacks = callbacks
        self._stt = stt
        self._tts = tts
        self._vad = VAD(LOCAL_SAMPLE_RATE, threshold=vad_threshold)
        self._input_device = input_device
        self._mic_factory = mic_factory
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._turn_lock = threading.Lock()
        self._level_ts = 0.0

    def open(self) -> None:
        self._closed.clear()
        self._thread = threading.Thread(target=self._run, name="local-duplex", daemon=True)
        self._thread.start()

    def send_audio(self, chunk: bytes) -> None:
        pass  # the engine owns the mic stream in local mode

    def send_text(self, text: str) -> None:
        # A typed turn becomes a reply immediately (text stays first-class).
        def _task() -> None:
            reply = self._ask(text)
            self._fire(self._callbacks.on_assistant_text, reply)
            self._speak(reply)

        threading.Thread(target=_task, daemon=True).start()

    def interrupt(self) -> None:
        self._tts.stop()

    def close(self) -> None:
        self._closed.set()
        self._tts.stop()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _ask(self, text: str) -> str:
        cb = self._callbacks.on_tool_call  # tool executor provided by the controller
        # The local engine routes the utterance through the JAMES agent brain.
        reply = None
        if self._callbacks.on_user_text:
            with suppress(Exception):
                self._callbacks.on_user_text(text)
        if cb is not None:
            # fallback path: no full agent available
            with suppress(Exception):
                reply = cb("local-0", "__text__", {"text": text})
        return reply or ""

    def _run(self) -> None:
        mic = (
            self._mic_factory()
            if self._mic_factory
            else AudioInput(device_index=self._input_device)
        )
        try:
            mic.open()
        except Exception as exc:
            self._fire_error(f"Local duplex mic error: {exc}")
            self._closed.set()
            return
        self._fire_state("listening")
        try:
            while not self._closed.is_set():
                frame = mic.read()
                self._emit_level(frame)
                transition = self._vad.feed(frame)
                if transition is None:
                    continue
                kind, chunk = transition
                if kind == "start":
                    self._tts.stop()  # user started talking — cut assistant audio
                    self._utterance = bytearray(chunk)
                    self._fire(self._callbacks.on_activity)
                    self._fire_state("listening")
                elif kind == "speech":
                    if not hasattr(self, "_utterance"):
                        self._utterance = bytearray()
                    self._utterance += chunk
                    partial = self._stt.partial(bytes(self._utterance))
                    if partial:
                        self._fire(self._callbacks.on_partial, partial)
                elif kind == "end":
                    self._utterance += chunk
                    self._fire_state("transcribing")
                    text = self._stt.final(bytes(self._utterance))
                    self._utterance = bytearray()
                    if not text.strip():
                        self._fire_state("listening")
                        continue
                    reply = self._ask(text.strip())
                    if reply:
                        self._fire(self._callbacks.on_assistant_text, reply)
                        self._speak(reply)
                    self._fire_state("listening")
        except Exception as exc:
            if not self._closed.is_set():
                self._fire_error(f"Local duplex engine error: {exc}")
        finally:
            mic.close()
            self._closed.set()

    def _speak(self, text: str) -> None:
        with self._turn_lock:
            self._fire_state("speaking")
            self._tts.speak(text)
            self._fire_state("listening")

    def _emit_level(self, frame: bytes) -> None:
        now = time.monotonic()
        if now - self._level_ts < 0.1 or self._callbacks.on_level is None:
            return
        self._level_ts = now
        with suppress(Exception):
            self._callbacks.on_level(pcm_rms(frame))

    @staticmethod
    def _fire(cb: Callable | None, *args) -> None:
        if cb:
            with suppress(Exception):
                cb(*args)

    def _fire_state(self, state: str) -> None:
        self._fire(self._callbacks.on_state, state)

    def _fire_error(self, msg: str) -> None:
        log.warning("%s", msg)
        self._fire(self._callbacks.on_error, msg)


# ---------------------------------------------------------------------------
# Wake gate (always-on listening while idle)
# ---------------------------------------------------------------------------


class WakeGate:
    """Idle-state trigger.

    * ``always``    — continuous mic; transcribes each utterance and fires on
      the wake word (existing behaviour, now VAD-gated so silence is cheap).
    * ``porcupine`` — Picovoice Porcupine low-power engine when installed.
    * ``none``      — never blocks; the controller stays active.
    """

    def __init__(
        self,
        engine: str,
        wake_word: str,
        *,
        stt: LocalStreamingSTT,
        mic_device: int | None = None,
        vad_threshold: float = 0.02,
        level_cb: Callable[[float], None] | None = None,
    ):
        self.engine = (engine or "always").lower()
        self.wake_word = (wake_word or "jarvis").lower()
        self._stt = stt
        self._mic_device = mic_device
        self._vad_threshold = vad_threshold
        self._level_cb = level_cb
        self._level_ts = 0.0

    def wait(
        self, cancel: threading.Event, mic_factory: Callable[[], AudioInput] | None = None
    ) -> str | None:
        """Block until woken. Returns the spoken command with the wake word
        stripped ("" for a bare wake, None for non-wake speech)."""
        if self.engine == "porcupine":
            try:
                from ..core.porcupine_engine import PorcupineWakeEngine

                engine = PorcupineWakeEngine(
                    settings.assistant.porcupine_key, keyword=self.wake_word
                )
                try:
                    heard = engine.listen(timeout=30.0)
                    return "" if heard else None
                finally:
                    engine.close()
            except Exception as exc:
                log.warning("Porcupine unavailable (%s); falling back to 'always'.", exc)
                self.engine = "always"
        if self.engine == "none":
            return ""
        return self._listen_always(cancel, mic_factory)

    def _listen_always(
        self, cancel: threading.Event, mic_factory: Callable[[], AudioInput] | None
    ) -> str | None:
        import re

        wake_re = re.compile(r"\b" + re.escape(self.wake_word) + r"\b", re.IGNORECASE)
        vad = VAD(LOCAL_SAMPLE_RATE, threshold=self._vad_threshold)
        mic = mic_factory() if mic_factory else AudioInput(device_index=self._mic_device)
        try:
            mic.open()
        except Exception as exc:
            log.warning("Wake gate mic error: %s", exc)
            return None
        try:
            while not cancel.is_set():
                frame = mic.read()
                self._emit_level(frame)
                transition = vad.feed(frame)
                if transition is None:
                    continue
                kind, chunk = transition
                if kind == "start":
                    self._buf = bytearray(chunk)
                elif kind == "speech":
                    if not hasattr(self, "_buf"):
                        self._buf = bytearray()
                    self._buf += chunk
                elif kind == "end":
                    self._buf += chunk
                    text = self._stt.final(bytes(self._buf)).strip().lower()
                    self._buf = bytearray()
                    if not text:
                        continue
                    if wake_re.search(text):
                        return text.replace(self.wake_word, "", 1).strip() or ""
                    # Non-wake speech while idle: ignore silently.
        finally:
            mic.close()
        return None

    def _emit_level(self, frame: bytes) -> None:
        if self._level_cb is None:
            return
        now = time.monotonic()
        if now - self._level_ts < 0.1:
            return
        self._level_ts = now
        with suppress(Exception):
            self._level_cb(pcm_rms(frame))


# ---------------------------------------------------------------------------
# Controller — always-on state machine + JAMES integration
# ---------------------------------------------------------------------------


class DuplexController:
    """Owns wake gating, session lifecycle, mute, interruption, and typed text.

    ``run()`` blocks (the assistant voice thread). All control methods are
    thread-safe and may be called from the GUI thread.
    """

    def __init__(
        self,
        *,
        emit: Callable[[dict], None],
        print_user: Callable[[str], None],
        print_assistant: Callable[[str], None],
        session_factory: Callable[[DuplexController], DuplexSession],
        tool_executor: Callable[[str, str, dict], str],
        wake_engine: str,
        wake_word: str,
        stt: LocalStreamingSTT,
        mic_device: int | None = None,
        speaker_device: int | None = None,
        vad_threshold: float = 0.02,
        barge_threshold: float = 0.03,
        edge_voice: str = "en-US-AriaNeural",
        idle_timeout: float = 30.0,
        tts_fallback: Callable[[str], None] | None = None,
    ):
        self.emit = emit
        self.print_user = print_user
        self.print_assistant = print_assistant
        self._session_factory = session_factory
        self._tool_executor = tool_executor
        self.wake_engine = (wake_engine or "always").lower()
        self.wake_word = (wake_word or "jarvis").lower()
        self._stt = stt
        self.mic_device = mic_device
        self.speaker_device = speaker_device
        self.vad_threshold = vad_threshold
        self.barge_threshold = barge_threshold
        self.edge_voice = edge_voice
        self.idle_timeout = idle_timeout
        self._tts_fallback = tts_fallback
        self._mic_factory: Callable[[], AudioInput] | None = None

        # Control state (thread-safe).
        self.muted = False
        self.voice_only = False
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._text_q: queue.Queue = queue.Queue()
        self._last_activity = time.monotonic()
        self._speaking = False

        # Live session + playback.
        self.session: DuplexSession | None = None
        self._output: AudioOutput | None = None
        self._greeter: StreamTTS | None = None
        self._greet_ts = 0.0

    # ------------------------------------------------------------------ controls

    def mute(self, muted: bool) -> None:
        with self._lock:
            self.muted = bool(muted)
        self._emit_state("muted" if muted else ("speaking" if self._speaking else "listening"))

    def interrupt(self) -> None:
        if self._greeter is not None:
            self._greeter.stop()
        with self._lock:
            session = self.session
        if session is not None:
            with suppress(Exception):
                session.interrupt()
        self._cut_output()

    def send_text(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._text_q.put(text)

    def stop(self) -> None:
        self._stop_evt.set()
        self.interrupt()

    def _cut_output(self) -> None:
        with self._lock:
            out = self._output
        if out is not None:
            with suppress(Exception):
                out.reset()

    # ----------------------------------------------------------------- callbacks

    def _on_activity(self) -> None:
        self._last_activity = time.monotonic()

    def _on_state(self, state: str) -> None:
        with self._lock:
            self._speaking = state == "speaking"
        self._emit_state(state)

    def _on_partial(self, text: str) -> None:
        self.emit({"type": "voice_partial", "text": text})

    def _on_user_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._on_activity()
        self.emit({"type": "user", "text": text})
        self.print_user(text)
        if text.lower().strip() in {"exit", "stop", "quit", "goodbye"}:
            self.print_assistant("Goodbye!")
            self.emit({"type": "reply", "text": "Goodbye!"})
            self.stop()

    def _on_assistant_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.emit({"type": "reply", "text": text})
        self.print_assistant(text)

    def _on_audio(self, pcm: bytes) -> None:
        with self._lock:
            if self._output is None:
                self._output = AudioOutput(device_index=self.speaker_device)
                try:
                    self._output.open()
                except Exception:
                    self._output = None
                    return
            out = self._output
        if out is not None:
            with suppress(Exception):
                out.write(pcm)

    def _on_tool_call(self, call_id: str, name: str, args: dict) -> str:
        return self._tool_executor(call_id, name, args)

    def _on_error(self, msg: str) -> None:
        log.warning("%s", msg)
        self.emit({"type": "voice_error", "text": msg})

    def _on_level(self, level: float) -> None:
        self.emit({"type": "voice_level", "level": round(level, 3)})

    # ------------------------------------------------------------------ events

    def _emit_state(self, state: str) -> None:
        self.emit({"type": "voice", "state": state})

    # ------------------------------------------------------------------- main

    def run(self) -> None:
        """Blocking loop: idle (wake gate) ⇄ active (duplex session)."""
        self._emit_state("idle")
        gate = WakeGate(
            self.wake_engine,
            self.wake_word,
            stt=self._stt,
            mic_device=self.mic_device,
            vad_threshold=self.vad_threshold,
            level_cb=self._on_level,
        )
        while not self._stop_evt.is_set():
            if self.wake_engine == "none":
                command = ""
            else:
                command = gate.wait(self._stop_evt, mic_factory=self._mic_factory)
                if self._stop_evt.is_set():
                    break
                if command is None:
                    continue
            self._enter_active(command)

    def _enter_active(self, command: str | None) -> None:
        with self._lock:
            if self.muted:
                return
        try:
            session = self._session_factory(self)
        except Exception as exc:
            self._on_error(f"Duplex session failed to start: {exc}")
            time.sleep(2)
            return
        with self._lock:
            self.session = session
        self._last_activity = time.monotonic()
        try:
            session.open()
        except Exception as exc:
            self._on_error(f"Duplex session failed to open: {exc}")
            self.session = None
            time.sleep(2)
            return
        self._emit_state("ready")
        if command:
            session.send_text(command)
        else:
            self._greet()
        while not self._stop_evt.is_set():
            self._drain_text(session)
            if not session.alive:
                self._on_error("Duplex session ended; re-arming wake word.")
                break
            with self._lock:
                speaking = self._speaking
            idle_for = time.monotonic() - self._last_activity
            if (
                not speaking
                and self.idle_timeout > 0
                and idle_for > self.idle_timeout
                and self._text_q.empty()
            ):
                self.print_assistant("Going quiet — wake me anytime.")
                self.emit({"type": "voice", "state": "idle"})
                break
            time.sleep(0.25)
        with suppress(Exception):
            session.close()
        with self._lock:
            self.session = None
            out, self._output = self._output, None
        if out is not None:
            with suppress(Exception):
                out.close()

    def _drain_text(self, session: DuplexSession) -> None:
        while not self._text_q.empty():
            try:
                text = self._text_q.get_nowait()
            except queue.Empty:
                return
            if not self._stop_evt.is_set():
                with suppress(Exception):
                    session.send_text(text)

    def _greet(self) -> None:
        if time.monotonic() - self._greet_ts < 5.0:
            return
        self._greet_ts = time.monotonic()
        if self._greeter is None:
            try:
                self._greeter = StreamTTS(
                    voice=self.edge_voice,
                    output_device=self.speaker_device,
                    mic_device=self.mic_device,
                    barge_threshold=self.barge_threshold,
                    fallback=self._tts_fallback,
                )
            except Exception as exc:
                log.debug("Greeting TTS unavailable: %s", exc)
                self._greeter = _NullGreeter()
        try:
            threading.Thread(target=lambda: self._greeter.speak("Yes?"), daemon=True).start()
        except Exception as exc:
            log.debug("Greeting failed: %s", exc)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def resolve_duplex_mode(voice: VoiceSettings) -> str:
    """Resolve DUPLEX_MODE, including 'auto' preference."""
    mode = (getattr(voice, "duplex_mode", "off") or "off").lower()
    if mode == "auto":
        if settings.llm.gemini_api_key:
            return "gemini_live"
        if settings.llm.openai_api_key:
            return "openai_realtime"
        return "local"
    return mode


def build_duplex(assistant) -> DuplexController:
    """Construct the controller for the current settings + assistant.

    The assistant provides the event sink, CLI printing, the tool registry
    (gated, audited, confirmed) and the TTS fallback. Returns None when
    duplex is disabled (callers should fall back to the turn-based loop).
    """
    mode = resolve_duplex_mode(settings.voice)
    if mode == "off":
        return None

    emit = getattr(assistant, "_emit", lambda ev: None)
    cli = getattr(assistant, "cli", None)
    registry = getattr(assistant, "registry", None)
    if registry is None:
        from ..tools.registry import ToolRegistry

        registry = ToolRegistry()

    def _print_user(text: str) -> None:
        if cli is not None:
            with suppress(Exception):
                cli.print_user(text)

    def _print_assistant(text: str) -> None:
        if cli is not None:
            with suppress(Exception):
                cli.print_assistant(settings.assistant.name, text)

    from ..core.agent import request_confirmation
    from ..core.personality import build_system_prompt
    from ..tools.registry import is_dangerous_tool_call

    system_prompt = (build_system_prompt() + _VOICE_SYSTEM_HINT).strip()
    tools = registry.schemas()
    if mode in ("openai_realtime",):
        tools = tools[:64]
    shared_stt = LocalStreamingSTT(settings.voice.streaming_stt_model)

    def _exec_tool(call_id: str, name: str, args: dict) -> str:
        if is_dangerous_tool_call(name, args) and settings.assistant.confirm_dangerous_actions:
            pending = getattr(assistant, "_tool_pending_hook", None)
            if pending:
                with suppress(Exception):
                    pending(call_id, name, args)
            allowed = request_confirmation(name, args)
            if not allowed:
                return f"Action '{name}' was denied by the user."
        start = getattr(assistant, "_tool_start_hook", None)
        if start:
            with suppress(Exception):
                start(call_id, name, args)
        try:
            result = registry.execute(name, args)
        except Exception as exc:
            text = f"Error: {exc}"
        else:
            text = result.output
        done = getattr(assistant, "_tool_hook", None)
        if done:
            with suppress(Exception):
                done(call_id, name, args, text)
        return text

    def _make_callbacks(controller: DuplexController) -> DuplexCallbacks:
        return DuplexCallbacks(
            on_state=controller._on_state,
            on_partial=controller._on_partial,
            on_user_text=controller._on_user_text,
            on_assistant_text=controller._on_assistant_text,
            on_audio=controller._on_audio,
            on_activity=controller._on_activity,
            on_interrupt=controller.interrupt,
            on_tool_call=controller._on_tool_call,
            on_level=controller._on_level,
            on_error=controller._on_error,
        )

    def _session_factory(controller: DuplexController) -> DuplexSession:
        callbacks = _make_callbacks(controller)
        if mode == "gemini_live":
            return GeminiLiveSession(
                callbacks,
                api_key=settings.llm.gemini_api_key,
                model=settings.voice.gemini_live_model,
                system_prompt=system_prompt,
                tools=tools,
                voice=settings.voice.gemini_live_voice,
            )
        if mode == "openai_realtime":
            return OpenAIRealtimeSession(
                callbacks,
                api_key=settings.llm.openai_api_key,
                model=settings.voice.openai_realtime_model,
                system_prompt=system_prompt,
                tools=tools,
                voice=settings.voice.openai_realtime_voice,
            )
        # local
        tts = StreamTTS(
            voice=settings.voice.duplex_edge_voice,
            output_device=settings.voice.speaker_device_index,
            mic_device=settings.voice.mic_device_index,
            barge_threshold=settings.voice.barge_in_threshold,
            fallback=getattr(assistant, "tts", None).speak
            if getattr(assistant, "tts", None)
            else None,
        )
        return LocalDuplexEngine(
            callbacks,
            stt=shared_stt,
            tts=tts,
            vad_threshold=settings.voice.vad_threshold,
            input_device=settings.voice.mic_device_index,
        )

    controller = DuplexController(
        emit=emit,
        print_user=_print_user,
        print_assistant=_print_assistant,
        session_factory=_session_factory,
        tool_executor=_exec_tool,
        wake_engine=settings.assistant.wake_engine,
        wake_word=settings.assistant.wake_word,
        stt=shared_stt,
        mic_device=settings.voice.mic_device_index,
        speaker_device=settings.voice.speaker_device_index,
        vad_threshold=settings.voice.vad_threshold,
        barge_threshold=settings.voice.barge_in_threshold,
        edge_voice=settings.voice.duplex_edge_voice,
        idle_timeout=settings.voice.duplex_idle_timeout,
        tts_fallback=getattr(assistant, "tts", None).speak
        if getattr(assistant, "tts", None)
        else None,
    )
    # The controller builds its own STT inside the gate; reuse the session one
    # when local mode is active to avoid double model loads.
    return controller
