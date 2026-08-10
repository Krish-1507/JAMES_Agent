"""Phase 2 — full-duplex voice: VAD, sessions, controller, barge-in.

Everything here runs without hardware, network, or cloud keys: audio devices,
LLM SDKs and the whisper backends are injected or stubbed.
"""

from __future__ import annotations

import queue
import random
import threading
import time
from types import SimpleNamespace

from james.voice.duplex import (
    VAD,
    DuplexCallbacks,
    DuplexController,
    GeminiLiveSession,
    LocalDuplexEngine,
    OpenAIRealtimeSession,
    StreamTTS,
    pcm_rms,
    resolve_duplex_mode,
)

# ---------------------------------------------------------------------------
# PCM + VAD
# ---------------------------------------------------------------------------


def _frame(rms_level: float, n_samples: int = 480) -> bytes:
    amp = int(rms_level * 32767)
    rng = random.Random(42)
    return b"".join(
        max(-32768, min(32767, rng.randint(-amp, amp))).to_bytes(2, "little", signed=True)
        for _ in range(n_samples)
    )


def test_pcm_rms_silence_is_zero():
    assert pcm_rms(b"\x00" * 960) == 0.0


def test_pcm_rms_loud_speech_is_large():
    level = pcm_rms(_frame(0.5))
    assert 0.2 < level <= 1.0


def test_vad_detects_utterance_start_and_end():
    vad = VAD(sample_rate=16000, threshold=0.05, min_speech_ms=300, min_silence_ms=600)
    started = False
    for _ in range(5):
        assert vad.feed(b"\x00" * 960) is None  # quiet
    for _ in range(12):  # 360 ms of speech
        ev = vad.feed(_frame(0.5))
        if ev is not None:
            kind, chunk = ev
            assert kind == "start"
            assert len(chunk) >= 960
            started = True
            break
    assert started
    ended = False
    for _ in range(25):  # 750 ms of silence
        ev = vad.feed(b"\x00" * 960)
        if ev is not None and ev[0] == "end":
            ended = True
    assert ended


def test_vad_ignores_short_noise_bursts():
    vad = VAD(sample_rate=16000, threshold=0.05, min_speech_ms=300, min_silence_ms=600)
    for _ in range(5):  # 150 ms of loud noise only
        assert vad.feed(_frame(0.5)) is None
    assert vad.feed(b"\x00" * 960) is None


# ---------------------------------------------------------------------------
# Tool routing helpers used by sessions
# ---------------------------------------------------------------------------


def _fake_callbacks(**overrides) -> DuplexCallbacks:
    events: list = []

    def make(name):
        def handler(*args):
            events.append((name, args))

        return handler

    cb = DuplexCallbacks(
        on_state=make("state"),
        on_partial=make("partial"),
        on_user_text=make("user_text"),
        on_assistant_text=make("assistant_text"),
        on_audio=make("audio"),
        on_activity=make("activity"),
        on_interrupt=make("interrupt"),
        on_level=make("level"),
        on_error=make("error"),
    )
    cb._events = events  # type: ignore[attr-defined]
    for key, value in overrides.items():
        setattr(cb, key, value)
    return cb


class _ToolExecutor:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, call_id: str, name: str, args: dict) -> str:
        self.calls.append((call_id, name, args))
        return f"result-of-{name}"


# ---------------------------------------------------------------------------
# Gemini Live session (SDK stubbed)
# ---------------------------------------------------------------------------


class _FakeGeminiPart:
    def __init__(self, *, data=None, text=None, function_call=None):
        self.inline_data = SimpleNamespace(data=data) if data is not None else None
        self.text = text
        self.function_call = function_call


class _FakeGeminiSession:
    def __init__(self):
        self.sent: list = []
        self._messages = [
            SimpleNamespace(
                server_content=SimpleNamespace(
                    interrupted=False,
                    parts=[
                        _FakeGeminiPart(data=b"\x01\x02" * 10),
                        _FakeGeminiPart(
                            function_call=SimpleNamespace(
                                id="fc-1", name="read_file", args={"path": "a.txt"}
                            )
                        ),
                    ],
                ),
                voice_activity_detection_signal=None,
            ),
            SimpleNamespace(
                server_content=None,
                voice_activity_detection_signal=SimpleNamespace(start=True),
            ),
        ]

    async def receive(self):
        for msg in self._messages:
            yield msg
        while True:  # keep the receive loop parked until the test closes
            await asyncio_sleep()

    async def send_realtime_input(self, *args, **kwargs):
        self.sent.append(("audio", kwargs.get("audio") or (args[0] if args else None)))

    async def send_client_content(self, *args, **kwargs):
        self.sent.append(("text", kwargs or args))

    async def send_tool_response(self, *args, **kwargs):
        self.sent.append(("tool", kwargs or args))

    async def close(self):
        pass


async def asyncio_sleep():
    import asyncio

    await asyncio.sleep(0.02)


def test_gemini_session_routes_audio_text_and_tools(monkeypatch):
    from google.genai import types as real_types

    fake_session = _FakeGeminiSession()
    executor = _ToolExecutor()

    class _FakeLive:
        @staticmethod
        async def connect(*args, **kwargs):
            yield fake_session

    class _FakeAio:
        live = _FakeLive

    class _FakeGenAI:
        types = real_types

        @staticmethod
        def Client(*args, **kwargs):
            return SimpleNamespace(aio=_FakeAio)

    monkeypatch.setattr("google.genai", _FakeGenAI)
    cb = _fake_callbacks(on_tool_call=executor)
    session = GeminiLiveSession(
        cb,
        api_key="test",
        tools=[
            {
                "function": {
                    "name": "read_file",
                    "description": "r",
                    "parameters": {"type": "object", "properties": {}},
                }
            }
        ],
    )
    session.open()
    time.sleep(0.3)
    session.send_audio(b"\x00" * 960)
    time.sleep(0.3)
    session.close()

    kinds = {item[0] for item in fake_session.sent}
    assert "audio" in kinds
    assert "tool" in kinds
    assert executor.calls and executor.calls[0][1] == "read_file"
    # audio chunks were delivered to the callback
    assert any(name == "audio" for name, _ in cb._events)  # type: ignore[attr-defined]


def test_openai_session_handles_speech_and_function_calls():
    executor = _ToolExecutor()
    created = []

    class FakeConn:
        def __init__(self):
            self.sent: list = []
            self.conversation = SimpleNamespace(
                item=SimpleNamespace(create=lambda item: created.append(item))
            )
            self.response = SimpleNamespace(
                create=lambda: self.sent.append(("response.create",)),
                cancel=lambda: self.sent.append(("response.cancel",)),
            )

        def session_update(self, session=None):
            self.sent.append(("session.update", session))

        def input_audio_buffer_append(self, audio=None):
            self.sent.append(("append", audio))

    conn = FakeConn()
    session = OpenAIRealtimeSession.__new__(OpenAIRealtimeSession)
    session._callbacks = _fake_callbacks(on_tool_call=executor)
    session._closed = threading.Event()
    session._send_q = queue.Queue()
    session._rx_q = queue.Queue()
    session._assistant_buf = ""
    session._fc_args = {}

    # 1) user speech + final transcript event
    session._handle_event(conn, SimpleNamespace(type="input_audio_buffer.speech_started"))
    session._handle_event(
        conn,
        SimpleNamespace(
            type="conversation.item.input_audio_transcription.completed", transcript="hello there"
        ),
    )
    # 2) assistant audio + text deltas
    import base64

    session._handle_event(
        conn,
        SimpleNamespace(type="response.audio.delta", delta=base64.b64encode(b"\x05" * 8).decode()),
    )
    session._handle_event(
        conn, SimpleNamespace(type="response.audio_transcript.delta", delta="Sure")
    )
    # 3) function call arguments + response.done with the function_call output
    session._handle_event(
        conn,
        SimpleNamespace(
            type="response.function_call_arguments.done", call_id="c1", arguments='{"q": "hi"}'
        ),
    )
    session._handle_event(
        conn,
        SimpleNamespace(
            type="response.done",
            response=SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="function_call", call_id="c1", name="web_search", arguments=""
                    )
                ],
            ),
        ),
    )

    assert any(name == "user_text" for name, _ in session._callbacks._events)  # type: ignore[attr-defined]
    assert any(name == "audio" for name, _ in session._callbacks._events)  # type: ignore[attr-defined]
    assert any(name == "interrupt" for name, _ in session._callbacks._events)  # type: ignore[attr-defined]
    assert executor.calls and executor.calls[0][1] == "web_search"
    assert any(item.get("type") == "function_call_output" for item in created)
    assert any(item[0] == "response.create" for item in conn.sent)  # continuation


def test_openai_session_audio_append_is_base64():
    import base64

    conn = SimpleNamespace(sent=[])
    conn.input_audio_buffer = SimpleNamespace(
        append=lambda audio: conn.sent.append(("append", audio))
    )
    session = OpenAIRealtimeSession.__new__(OpenAIRealtimeSession)
    session._callbacks = _fake_callbacks()
    session._closed = threading.Event()
    session._send_q = queue.Queue()
    session._rx_q = queue.Queue()
    session._assistant_buf = ""
    session._fc_args = {}
    session._send_item(conn, ("audio", b"\x00" * 960))
    kind, payload = conn.sent[0]
    assert kind == "append"
    assert base64.b64decode(payload) == b"\x00" * 960


# ---------------------------------------------------------------------------
# Controller state machine
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self):
        self.opened = False
        self.closed = False
        self.texts: list[str] = []
        self.audios: list[bytes] = []
        self.interrupts = 0
        self._alive = True

    def open(self):
        self.opened = True

    def send_audio(self, chunk):
        self.audios.append(chunk)

    def send_text(self, text):
        self.texts.append(text)

    def interrupt(self):
        self.interrupts += 1

    def close(self):
        self.closed = True
        self._alive = False

    @property
    def alive(self):
        return self._alive


def _make_controller(
    monkeypatch, *, wake="none", idle_timeout=30.0
) -> tuple[DuplexController, _FakeSession]:
    sessions: list[_FakeSession] = []
    events: list[dict] = []

    def factory(controller):
        s = _FakeSession()
        sessions.append(s)
        return s

    controller = DuplexController(
        emit=events.append,
        print_user=lambda t: None,
        print_assistant=lambda t: None,
        session_factory=factory,
        tool_executor=lambda cid, n, a: f"ok-{n}",
        wake_engine=wake,
        wake_word="jarvis",
        stt=None,
        idle_timeout=idle_timeout,
    )
    controller._stt = None  # never used with wake="none"
    controller._mic_factory = lambda: SimpleNamespace()
    controller._greeter = _FakeTTS()  # never touch real audio hardware in tests
    return controller, sessions


def test_controller_wake_none_starts_session_and_closes_on_stop(monkeypatch):
    controller, sessions = _make_controller(monkeypatch, wake="none", idle_timeout=30)
    t = threading.Thread(target=controller.run, daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not (sessions and sessions[0].opened):
        time.sleep(0.05)
    assert sessions and sessions[0].opened
    controller.stop()
    t.join(timeout=5)
    assert sessions[0].closed


def test_controller_idle_timeout_returns_to_wake(monkeypatch):
    controller, sessions = _make_controller(monkeypatch, wake="none", idle_timeout=0.3)
    t = threading.Thread(target=controller.run, daemon=True)
    t.start()
    time.sleep(0.8)
    controller.stop()
    t.join(timeout=5)
    assert sessions and sessions[0].opened and sessions[0].closed


def test_controller_send_text_wakes_and_injects(monkeypatch):
    controller, sessions = _make_controller(monkeypatch, wake="none", idle_timeout=30)
    t = threading.Thread(target=controller.run, daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not (sessions and sessions[0].opened):
        time.sleep(0.05)
    controller.send_text("hello by text")
    deadline = time.time() + 5
    while time.time() < deadline and not sessions[0].texts:
        time.sleep(0.05)
    assert sessions[0].texts == ["hello by text"]
    controller.stop()
    t.join(timeout=5)


def test_controller_interrupt_and_mute(monkeypatch):
    controller, sessions = _make_controller(monkeypatch, wake="none", idle_timeout=30)
    controller._greeter = _FakeTTS()  # never touch real audio hardware in tests
    controller._output = SimpleNamespace(reset=lambda: None)
    controller.mute(True)
    assert controller.muted
    controller.mute(False)
    assert not controller.muted
    t = threading.Thread(target=controller._enter_active, args=(None,), daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not (sessions and sessions[0].opened):
        time.sleep(0.05)
    assert sessions and sessions[0].opened
    controller.interrupt()
    assert sessions[0].interrupts == 1
    controller.stop()
    t.join(timeout=5)


def test_controller_exit_command_stops(monkeypatch):
    controller, sessions = _make_controller(monkeypatch, wake="none", idle_timeout=30)
    controller._on_user_text("exit")
    assert controller._stop_evt.is_set()


# ---------------------------------------------------------------------------
# Local engine turn flow (mic injected)
# ---------------------------------------------------------------------------


class _FakeMic:
    def __init__(self, frames: list[bytes], done: threading.Event):
        self.frames = frames
        self.done = done
        self.closed = False

    def open(self):
        pass

    def read(self):
        if self.frames:
            return self.frames.pop(0)
        self.done.set()
        time.sleep(0.05)
        return b"\x00" * 960

    def close(self):
        self.closed = True


class _FakeSTT:
    def __init__(self, final_text="turn the lights on"):
        self.final_text = final_text
        self.partials: list[str] = []

    @property
    def supports_partials(self):
        return True

    def partial(self, pcm):
        self.partials.append("partial…")
        return "partial…"

    def final(self, pcm):
        return self.final_text


class _FakeTTS:
    def __init__(self):
        self.speeches: list[str] = []
        self.stopped = False

    def stop(self):
        self.stopped = True

    def speak(self, text, *, on_barge_in=None):
        self.speeches.append(text)
        return True


def _speech_then_silence() -> list[bytes]:
    frames = [b"\x00" * 960] * 5  # leading silence
    frames += [_frame(0.5) for _ in range(12)]  # speech (360 ms)
    frames += [b"\x00" * 960] * 30  # trailing silence (900 ms)
    return frames


def test_local_engine_full_turn():
    events = []
    done = threading.Event()
    mic = _FakeMic(_speech_then_silence(), done)
    stt = _FakeSTT("turn the lights on")
    tts = _FakeTTS()
    replies: list[str] = []

    def on_user_text(text):
        replies.append(text)

    cb = _fake_callbacks(
        on_user_text=on_user_text,
        on_state=lambda s: events.append(("state", s)),
        on_tool_call=lambda call_id, name, args: f"ok: {args.get('text', '')}",
    )
    engine = LocalDuplexEngine(
        cb,
        stt=stt,
        tts=tts,
        vad_threshold=0.05,
        mic_factory=lambda: mic,
    )
    engine.open()
    done.wait(timeout=10)
    engine.close()

    assert replies == ["turn the lights on"]
    assert tts.speeches == ["ok: turn the lights on"]
    assert mic.closed


def test_local_engine_barge_in_stops_tts():
    done = threading.Event()
    mic = _FakeMic(_speech_then_silence(), done)
    stt = _FakeSTT()
    tts = _FakeTTS()
    cb = _fake_callbacks()
    engine = LocalDuplexEngine(cb, stt=stt, tts=tts, vad_threshold=0.05, mic_factory=lambda: mic)
    engine.open()
    engine._ask = lambda text: "long reply that would be interrupted"
    done.wait(timeout=10)
    engine.interrupt()  # user hits the Stop button mid-turn
    engine.close()
    assert tts.stopped


# ---------------------------------------------------------------------------
# Streaming TTS barge-in (decoder + output injected)
# ---------------------------------------------------------------------------


class _FakeDecoder:
    def __init__(self, eof=True):
        self.closed = False
        self._out = _FakeStdout(eof=eof)

    @property
    def stdin(self):
        class _In:
            def write(self, data):
                pass

            def close(self):
                pass

        return _In()

    @property
    def stdout(self):
        return self._out

    def kill(self):
        pass

    def wait(self, timeout=None):
        pass


class _FakeStdout:
    def __init__(self, eof=True):
        self.n = 0
        self.eof = eof

    def read(self, n):
        self.n += 1
        if self.eof and self.n >= 6:
            return b""  # EOF → synthesis finished
        return b"\x00" * 4096


class _FakeOutput:
    def __init__(self, device_index=None, rate=24000):
        self.device_index = device_index
        self.rate = rate
        self.writes = 0
        self.reset_calls = 0

    def open(self):
        pass

    def write(self, pcm):
        self.writes += 1

    def reset(self):
        self.reset_calls += 1

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_stream_tts_completes_without_barge(monkeypatch):
    monkeypatch.setattr("james.voice.duplex.shutil.which", lambda name: f"fake-{name}")
    monkeypatch.setattr("james.voice.duplex.subprocess.Popen", lambda *a, **k: _FakeDecoder())
    monkeypatch.setattr("james.voice.duplex.AudioOutput", _FakeOutput)

    class _NoBargeMic:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            time.sleep(0.05)
            return b"\x00" * 960

    monkeypatch.setattr("james.voice.duplex.AudioInput", _NoBargeMic)
    tts = StreamTTS(voice="en-US-AriaNeural", barge_threshold=0.5, fallback=lambda t: None)
    tts._edge = SimpleNamespace(Communicate=lambda text, voice: _Streamer())  # type: ignore[attr-defined]
    finished = tts.speak("hello world")
    assert finished is True


def test_stream_tts_barge_in_interrupts(monkeypatch):
    monkeypatch.setattr("james.voice.duplex.shutil.which", lambda name: f"fake-{name}")
    monkeypatch.setattr(
        "james.voice.duplex.subprocess.Popen", lambda *a, **k: _FakeDecoder(eof=False)
    )
    monkeypatch.setattr("james.voice.duplex.AudioOutput", _FakeOutput)

    class _LoudMic:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            time.sleep(0.02)
            return _frame(0.5)  # loud — the user is interrupting

    monkeypatch.setattr("james.voice.duplex.AudioInput", _LoudMic)
    tts = StreamTTS(voice="en-US-AriaNeural", barge_threshold=0.1, fallback=lambda t: None)
    tts._edge = SimpleNamespace(Communicate=lambda text, voice: _Streamer())  # type: ignore[attr-defined]
    barged = threading.Event()

    def on_barge():
        barged.set()

    finished = tts.speak("hello world", on_barge_in=on_barge)
    assert finished is False
    assert barged.is_set()


class _Streamer:
    def __init__(self):
        self.i = 0

    async def stream(self):
        for _ in range(4):
            yield {"type": "audio", "data": b"\xff\xfb" * 200}
        yield {"type": "word_boundary", "data": None}


# ---------------------------------------------------------------------------
# Factory / mode resolution
# ---------------------------------------------------------------------------


def test_resolve_duplex_mode_off(monkeypatch):
    from james.config import VoiceSettings

    monkeypatch.setattr("james.config.settings.voice", VoiceSettings(duplex_mode="off"))
    assert resolve_duplex_mode(settings_voice()) == "off"


def settings_voice():
    from james.config import settings

    return settings.voice


def test_resolve_duplex_mode_auto_prefers_gemini(monkeypatch):
    from james.config import settings

    monkeypatch.setattr(settings.voice, "duplex_mode", "auto")
    monkeypatch.setattr(settings.llm, "gemini_api_key", "g-key")
    monkeypatch.setattr(settings.llm, "openai_api_key", "")
    assert resolve_duplex_mode(settings.voice) == "gemini_live"


def test_resolve_duplex_mode_auto_falls_to_local(monkeypatch):
    from james.config import settings

    monkeypatch.setattr(settings.voice, "duplex_mode", "auto")
    monkeypatch.setattr(settings.llm, "gemini_api_key", "")
    monkeypatch.setattr(settings.llm, "openai_api_key", "")
    assert resolve_duplex_mode(settings.voice) == "local"


def test_config_defaults():
    from james.config import settings

    voice = settings.voice
    assert voice.duplex_mode == "off"
    assert voice.speaker_device_index is None
    assert voice.vad_threshold > 0
    assert voice.barge_in_threshold > 0
    assert voice.gemini_live_model
    assert voice.openai_realtime_model
    assert voice.streaming_stt_model
