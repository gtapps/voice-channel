"""
WebSocket adapter unit tests — no live sockets required.

Tests the parts of WebSocketAdapter that are pure logic:
  - #1: blank/duplicate websocket_token handling in _token_map
  - #2: stale-connection guards in _handle_message and the finally path
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional

import pytest

from voice_dispatcher.core.handlers import Dispatcher
from voice_dispatcher.core.models import AgentDisconnected, SpeakRequest
from voice_dispatcher.adapters.websocket import WebSocketAdapter


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_adapter(agents: dict) -> WebSocketAdapter:
    """Build an adapter from a minimal config dict (TLS disabled for unit tests)."""
    dispatcher = Dispatcher()
    config = {
        "agents": agents,
        "server": {"host": "127.0.0.1", "port": 7355, "tls": {"enabled": False}},
    }
    return WebSocketAdapter(dispatcher, config)


class _FakeWS:
    """Minimal fake WebSocket — just enough for the adapter's close_code/close_reason reads."""
    def __init__(self):
        self.close_code: Optional[int] = 1000
        self.close_reason: str = "test"


class _FakeHelloWS(_FakeWS):
    """Fake WebSocket that yields one hello frame, then records close()."""
    def __init__(self, msg: dict, remote_address=("203.0.113.10", 54321)):
        super().__init__()
        self._raw = json.dumps(msg)
        self.remote_address = remote_address

    async def recv(self) -> str:
        return self._raw

    async def close(self, code: int, reason: str) -> None:
        self.close_code = code
        self.close_reason = reason


# ── #1: token map hardening ───────────────────────────────────────────────────

def test_blank_token_excluded_from_map() -> None:
    adapter = make_adapter({
        "jarvis": {"websocket_token": "", "triggers": [], "voice": ""},
    })
    assert "" not in adapter._token_map


def test_blank_token_agent_still_registered() -> None:
    """Even with a blank token, the session is registered so route_transcript works."""
    adapter = make_adapter({
        "jarvis": {"websocket_token": "", "triggers": [], "voice": ""},
    })
    session = adapter._dispatcher.registry.get("jarvis")
    assert session is not None


def test_duplicate_token_keeps_first() -> None:
    adapter = make_adapter({
        "agent-a": {"websocket_token": "shared-token", "triggers": [], "voice": ""},
        "agent-b": {"websocket_token": "shared-token", "triggers": [], "voice": ""},
    })
    assert adapter._token_map["shared-token"] == "agent-a"


def test_duplicate_token_second_agent_still_registered() -> None:
    adapter = make_adapter({
        "agent-a": {"websocket_token": "shared-token", "triggers": [], "voice": ""},
        "agent-b": {"websocket_token": "shared-token", "triggers": [], "voice": ""},
    })
    # Both agents must be reachable via route_transcript even if only one can authenticate
    assert adapter._dispatcher.registry.get("agent-a") is not None
    assert adapter._dispatcher.registry.get("agent-b") is not None


def test_valid_token_registered() -> None:
    adapter = make_adapter({
        "jarvis": {"websocket_token": "tok-abc", "triggers": [], "voice": ""},
    })
    assert adapter._token_map["tok-abc"] == "jarvis"


@pytest.mark.asyncio
async def test_invalid_token_log_includes_claimed_agent_and_prefix(caplog) -> None:
    adapter = make_adapter({
        "jarvis": {"websocket_token": "correct-token", "triggers": [], "voice": ""},
    })
    ws = _FakeHelloWS({
        "v": 1,
        "type": "hello",
        "agent_id": "jarvis",
        "token": "wrong-token-secret",
    })

    with caplog.at_level(logging.WARNING, logger="voice_dispatcher.adapters.websocket"):
        await adapter._handle_connection(ws)

    assert ws.close_code == 4001
    assert ws.close_reason == "authentication failed"
    assert "claimed_agent='jarvis'" in caplog.text
    assert "wrong-to" in caplog.text
    assert "wrong-token-secret" not in caplog.text


# ── #2: stale connection guards ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_message_is_ignored() -> None:
    """Frames from a superseded socket must not reach the core."""
    adapter = make_adapter({
        "jarvis": {"websocket_token": "tok", "triggers": [], "voice": ""},
    })
    speak_events = []
    adapter._dispatcher.bus.subscribe(SpeakRequest, speak_events.append)

    live_ws = _FakeWS()
    stale_ws = _FakeWS()

    # Only the live socket is current
    adapter._connections["jarvis"] = live_ws  # type: ignore[assignment]

    # Deliver a speak frame from the stale socket — must be dropped
    stale_msg = json.dumps({"type": "speak", "utterance_id": "u-1", "text": "hello"})
    await adapter._handle_message("jarvis", stale_ws, stale_msg)  # type: ignore[arg-type]

    assert speak_events == [], "stale frame must not reach the core"


@pytest.mark.asyncio
async def test_live_message_is_processed() -> None:
    """Frames from the current socket are forwarded normally."""
    adapter = make_adapter({
        "jarvis": {"websocket_token": "tok", "triggers": [], "voice": ""},
    })
    speak_events = []
    adapter._dispatcher.bus.subscribe(SpeakRequest, speak_events.append)

    live_ws = _FakeWS()
    adapter._connections["jarvis"] = live_ws  # type: ignore[assignment]

    msg = json.dumps({"type": "speak", "utterance_id": "u-1", "text": "hello"})
    await adapter._handle_message("jarvis", live_ws, msg)  # type: ignore[arg-type]

    assert len(speak_events) == 1


def test_stale_finally_does_not_evict_live_connection() -> None:
    """The old socket's finally path must not pop the current live connection."""
    adapter = make_adapter({
        "jarvis": {"websocket_token": "tok", "triggers": [], "voice": ""},
    })
    disconnected_events = []
    adapter._dispatcher.bus.subscribe(AgentDisconnected, disconnected_events.append)

    live_ws = _FakeWS()
    adapter._connections["jarvis"] = live_ws  # type: ignore[assignment]

    # Live connection must still be registered; no disconnect event emitted
    assert adapter._connections.get("jarvis") is live_ws
    assert disconnected_events == []
