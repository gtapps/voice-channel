"""
Shared data models — transport-neutral.

These dataclasses are the only currency exchanged between the audio pipeline,
the core handlers, and any adapter (WebSocket, REST, ...).  Nothing here knows
about sockets or wire formats.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


# ── Inbound events (audio pipeline → core) ───────────────────────────────────

@dataclass(frozen=True)
class TranscriptEvent:
    """A voice utterance matched a trigger and was transcribed."""
    agent_id: str
    utterance_id: str   # e.g. "u-1748012345-abc"
    text: str           # trigger-stripped command text
    lang: str           # ISO-639-1 language code (from Whisper or config hint)
    trigger: str        # the trigger phrase that matched
    ts: str             # ISO-8601 timestamp (set by the audio pipeline)


# ── Outbound events (core → adapters / audio) ────────────────────────────────

@dataclass(frozen=True)
class TranscriptDispatched:
    """Emitted by the core after route_transcript() enqueues the utterance."""
    agent_id: str
    utterance_id: str
    text: str
    lang: str
    trigger: str
    ts: str


@dataclass(frozen=True)
class SpeakRequest:
    """Core asks the audio subsystem to synthesise and play text."""
    agent_id: str
    utterance_id: str
    text: str


@dataclass(frozen=True)
class PermissionRequested:
    """Core forwards an inbound permission request from the plugin to adapters."""
    agent_id: str
    request_id: str
    tool_name: str
    description: str
    input_preview: str


@dataclass(frozen=True)
class PermissionVerdict:
    """Core forwards an operator verdict back to the plugin."""
    agent_id: str
    request_id: str
    behavior: Literal["allow", "deny"]


# ── Internal state events ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentConnected:
    agent_id: str


@dataclass(frozen=True)
class AgentDisconnected:
    agent_id: str
    code: int
    reason: str


# Type alias for the event union (open — new event types are additive)
Event = (
    TranscriptEvent
    | TranscriptDispatched
    | SpeakRequest
    | PermissionRequested
    | PermissionVerdict
    | AgentConnected
    | AgentDisconnected
)
