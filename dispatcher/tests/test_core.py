"""
Core unit tests — no audio hardware, no WebSocket.

Drives the Dispatcher with synthetic events and asserts the correct events
are emitted on the bus.  Everything runs in-process with no I/O.
"""

import pytest
from voice_dispatcher.core.bus import EventBus
from voice_dispatcher.core.handlers import Dispatcher
from voice_dispatcher.core.models import (
    TranscriptDispatched,
    SpeakRequest,
    PermissionRequested,
    PermissionVerdict,
    HermitConnected,
    HermitDisconnected,
)
from voice_dispatcher.core.session import HermitConfig, SessionRegistry


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_dispatcher(enable_permission_relay: bool = False) -> Dispatcher:
    bus = EventBus()
    registry = SessionRegistry()
    cfg = HermitConfig(
        hermit_id="jarvis",
        token="test-token",
        triggers=["hey jarvis", "hermit"],
        language="en",
        voice="en_US-lessac-medium.onnx",
        enable_permission_relay=enable_permission_relay,
    )
    registry.register(cfg)
    return Dispatcher(bus=bus, registry=registry)


def collect(dispatcher: Dispatcher, event_type: type) -> list:
    """Subscribe to event_type and return the list that accumulates events."""
    received: list = []
    dispatcher.bus.subscribe(event_type, received.append)
    return received


# ── Bus ────────────────────────────────────────────────────────────────────────

def test_bus_basic_fanout() -> None:
    bus = EventBus()
    received: list[TranscriptDispatched] = []
    bus.subscribe(TranscriptDispatched, received.append)

    evt = TranscriptDispatched(
        hermit_id="jarvis", utterance_id="u-1", text="hello",
        lang="en", trigger="hey jarvis", ts="2026-01-01T00:00:00Z",
    )
    bus.emit(evt)
    assert received == [evt]


def test_bus_multiple_subscribers() -> None:
    bus = EventBus()
    a: list = []
    b: list = []
    bus.subscribe(SpeakRequest, a.append)
    bus.subscribe(SpeakRequest, b.append)

    evt = SpeakRequest(hermit_id="jarvis", utterance_id="u-1", text="hi")
    bus.emit(evt)
    assert a == [evt]
    assert b == [evt]


def test_bus_unsubscribe() -> None:
    bus = EventBus()
    received: list = []
    bus.subscribe(SpeakRequest, received.append)
    bus.unsubscribe(SpeakRequest, received.append)
    bus.emit(SpeakRequest(hermit_id="jarvis", utterance_id="u-1", text="hi"))
    assert received == []


def test_bus_no_cross_type_fanout() -> None:
    bus = EventBus()
    speak_received: list = []
    transcript_received: list = []
    bus.subscribe(SpeakRequest, speak_received.append)
    bus.subscribe(TranscriptDispatched, transcript_received.append)

    bus.emit(SpeakRequest(hermit_id="j", utterance_id="u-1", text="x"))
    assert speak_received and not transcript_received


# ── route_transcript ──────────────────────────────────────────────────────────

def test_route_transcript_emits_dispatched() -> None:
    d = make_dispatcher()
    events = collect(d, TranscriptDispatched)

    d.route_transcript("jarvis", "u-1", "turn on the lights", "en", "hey jarvis", "2026-01-01T00:00:00Z")

    assert len(events) == 1
    e = events[0]
    assert e.hermit_id == "jarvis"
    assert e.utterance_id == "u-1"
    assert e.text == "turn on the lights"
    assert e.trigger == "hey jarvis"


def test_route_transcript_generates_utterance_id_when_none() -> None:
    d = make_dispatcher()
    events = collect(d, TranscriptDispatched)

    d.route_transcript("jarvis", None, "what time is it", "en", "hermit")

    assert len(events) == 1
    uid = events[0].utterance_id
    assert uid.startswith("u-")


def test_route_transcript_updates_session() -> None:
    d = make_dispatcher()
    d.route_transcript("jarvis", "u-42", "hello", "en", "hey jarvis")
    session = d.registry.get("jarvis")
    assert session is not None
    assert session.last_utterance_id == "u-42"


def test_route_transcript_unknown_hermit_drops() -> None:
    d = make_dispatcher()
    events = collect(d, TranscriptDispatched)
    d.route_transcript("unknown-hermit", "u-1", "hello", "en", "hey")
    assert events == []


# ── speak ─────────────────────────────────────────────────────────────────────

def test_speak_emits_speak_request() -> None:
    d = make_dispatcher()
    events = collect(d, SpeakRequest)

    d.speak("jarvis", "u-1", "Lights are now on.")

    assert len(events) == 1
    assert events[0].hermit_id == "jarvis"
    assert events[0].utterance_id == "u-1"
    assert events[0].text == "Lights are now on."


def test_speak_unknown_hermit_drops() -> None:
    d = make_dispatcher()
    events = collect(d, SpeakRequest)
    d.speak("ghost", "u-1", "hi")
    assert events == []


# ── request_permission ────────────────────────────────────────────────────────

def test_request_permission_disabled_by_default() -> None:
    d = make_dispatcher(enable_permission_relay=False)
    events = collect(d, PermissionRequested)
    d.request_permission("jarvis", "abcde", "Bash", "Run a shell command", '{"command":"pwd"}')
    assert events == []


def test_request_permission_enabled_emits_event() -> None:
    d = make_dispatcher(enable_permission_relay=True)
    events = collect(d, PermissionRequested)

    d.request_permission("jarvis", "abcde", "Bash", "Run a shell command", '{"command":"pwd"}')

    assert len(events) == 1
    e = events[0]
    assert e.hermit_id == "jarvis"
    assert e.request_id == "abcde"
    assert e.tool_name == "Bash"
    assert e.description == "Run a shell command"
    assert e.input_preview == '{"command":"pwd"}'


def test_request_permission_sets_pending_id() -> None:
    d = make_dispatcher(enable_permission_relay=True)
    d.request_permission("jarvis", "abcde", "Bash", "Run pwd", '{}')
    session = d.registry.get("jarvis")
    assert session is not None
    assert session.pending_permission_request_id == "abcde"


# ── submit_permission_verdict ─────────────────────────────────────────────────

def test_submit_verdict_emits_event() -> None:
    d = make_dispatcher(enable_permission_relay=True)
    events = collect(d, PermissionVerdict)

    d.request_permission("jarvis", "abcde", "Bash", "pwd", "{}")
    d.submit_permission_verdict("jarvis", "abcde", "allow")

    assert len(events) == 1
    v = events[0]
    assert v.hermit_id == "jarvis"
    assert v.request_id == "abcde"
    assert v.behavior == "allow"


def test_submit_verdict_deny() -> None:
    d = make_dispatcher(enable_permission_relay=True)
    events = collect(d, PermissionVerdict)
    d.request_permission("jarvis", "fghij", "Write", "write a file", "{}")
    d.submit_permission_verdict("jarvis", "fghij", "deny")
    assert events[0].behavior == "deny"


def test_submit_verdict_clears_pending() -> None:
    d = make_dispatcher(enable_permission_relay=True)
    d.request_permission("jarvis", "abcde", "Bash", "pwd", "{}")
    d.submit_permission_verdict("jarvis", "abcde", "allow")
    session = d.registry.get("jarvis")
    assert session is not None
    assert session.pending_permission_request_id is None


def test_submit_verdict_invalid_behavior_drops() -> None:
    d = make_dispatcher()
    events = collect(d, PermissionVerdict)
    d.submit_permission_verdict("jarvis", "abcde", "maybe")
    assert events == []


# ── Connection lifecycle ──────────────────────────────────────────────────────

def test_on_connected_emits_event() -> None:
    d = make_dispatcher()
    events = collect(d, HermitConnected)
    d.on_connected("jarvis")
    assert len(events) == 1
    assert events[0].hermit_id == "jarvis"


def test_on_connected_sets_session_connected() -> None:
    d = make_dispatcher()
    d.on_connected("jarvis")
    session = d.registry.get("jarvis")
    assert session is not None
    assert session.connected is True


def test_on_disconnected_emits_event() -> None:
    d = make_dispatcher()
    events = collect(d, HermitDisconnected)
    d.on_connected("jarvis")
    d.on_disconnected("jarvis", code=1000, reason="normal close")
    assert len(events) == 1
    assert events[0].hermit_id == "jarvis"
    assert events[0].code == 1000


def test_on_disconnected_clears_connected() -> None:
    d = make_dispatcher()
    d.on_connected("jarvis")
    d.on_disconnected("jarvis")
    session = d.registry.get("jarvis")
    assert session is not None
    assert session.connected is False
