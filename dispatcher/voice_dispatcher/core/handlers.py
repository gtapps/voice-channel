"""
Dispatcher core — transport-neutral handler functions.

These four functions are the ONLY public surface that adapters (WebSocket,
REST, ...) and the audio pipeline call.  They never know about sockets or
wire formats; they emit events onto the bus and update Session state.

The audio pipeline calls:
    route_transcript(agent_id, utterance_id, text, lang, trigger, ts)

The WebSocket adapter calls:
    speak(agent_id, utterance_id, text)               — inbound speak frame
    request_permission(agent_id, ...)                 — inbound permission_request
    submit_permission_verdict(agent_id, ...)          — inbound permission_verdict

All four are synchronous and thread-safe.
"""

from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from .bus import EventBus
from .models import (
    TranscriptDispatched,
    SpeakRequest,
    PermissionRequested,
    PermissionVerdict,
    AgentConnected,
    AgentDisconnected,
)
from .session import SessionRegistry

logger = logging.getLogger(__name__)


class Dispatcher:
    """
    Stateful core — wires together the bus and the session registry.

    Instantiate once per process; inject into adapters and the audio pipeline.
    """

    def __init__(self, bus: Optional[EventBus] = None, registry: Optional[SessionRegistry] = None) -> None:
        self.bus = bus or EventBus()
        self.registry = registry or SessionRegistry()

    # ── Public handler API ────────────────────────────────────────────────────

    def route_transcript(
        self,
        agent_id: str,
        utterance_id: Optional[str],
        text: str,
        lang: str,
        trigger: str,
        ts: Optional[str] = None,
    ) -> None:
        """
        Called by the audio pipeline when a trigger-matched transcript is ready.

        Emits TranscriptDispatched on the bus.  Adapters subscribed to that
        event push the transcript to the agent (e.g. as a WS frame).
        """
        uid = utterance_id or _generate_utterance_id()
        timestamp = ts or _now_iso()

        session = self.registry.get(agent_id)
        if session is None:
            logger.warning("route_transcript: unknown agent %r — dropping", agent_id)
            return

        session.last_utterance_id = uid
        logger.info("route_transcript: agent=%r uid=%r text=%r", agent_id, uid, text)

        self.bus.emit(TranscriptDispatched(
            agent_id=agent_id,
            utterance_id=uid,
            text=text,
            lang=lang,
            trigger=trigger,
            ts=timestamp,
        ))

    def speak(self, agent_id: str, utterance_id: str, text: str) -> None:
        """
        Called by the WebSocket adapter when the agent sends a speak frame.

        Emits SpeakRequest on the bus.  The audio subsystem (subscribed to
        SpeakRequest) synthesises TTS and plays it aloud.
        """
        session = self.registry.get(agent_id)
        if session is None:
            logger.warning("speak: unknown agent %r — dropping", agent_id)
            return

        if not utterance_id or not text:
            logger.warning("speak: empty utterance_id/text from %r — dropping", agent_id)
            return

        logger.info("speak: agent=%r uid=%r text=%r", agent_id, utterance_id, text)
        self.bus.emit(SpeakRequest(
            agent_id=agent_id,
            utterance_id=utterance_id,
            text=text,
        ))

    def request_permission(
        self,
        agent_id: str,
        request_id: str,
        tool_name: str,
        description: str,
        input_preview: str,
    ) -> None:
        """
        Called by the WebSocket adapter when the agent sends a permission_request.

        Emits PermissionRequested on the bus.  The audio subsystem (if subscribed)
        speaks the prompt aloud; the human operator then voices a verdict.
        """
        session = self.registry.get(agent_id)
        if session is None:
            logger.warning("request_permission: unknown agent %r — dropping", agent_id)
            return

        if not session.config.enable_permission_relay:
            logger.debug(
                "request_permission: agent %r has permission relay disabled — dropping",
                agent_id,
            )
            return

        if session.pending_permission_request_id is not None:
            logger.warning(
                "request_permission: already pending %r — deferring %r to terminal",
                session.pending_permission_request_id, request_id,
            )
            return

        session.pending_permission_request_id = request_id
        logger.info(
            "request_permission: agent=%r id=%r tool=%r",
            agent_id, request_id, tool_name,
        )
        self.bus.emit(PermissionRequested(
            agent_id=agent_id,
            request_id=request_id,
            tool_name=tool_name,
            description=description,
            input_preview=input_preview,
        ))

    def submit_permission_verdict(
        self,
        agent_id: str,
        request_id: str,
        behavior: str,
    ) -> None:
        """
        Called when the operator voices (or types) a verdict.

        Emits PermissionVerdict on the bus.  The WebSocket adapter pushes it
        to the agent as a permission_verdict frame.
        """
        if behavior not in ("allow", "deny"):
            logger.warning(
                "submit_permission_verdict: invalid behavior %r — dropping", behavior
            )
            return

        session = self.registry.get(agent_id)
        if session is None:
            logger.warning("submit_permission_verdict: unknown agent %r — dropping", agent_id)
            return

        pending = session.pending_permission_request_id
        if pending != request_id:
            logger.warning(
                "submit_permission_verdict: request_id %r does not match pending %r — dropping",
                request_id, pending,
            )
            return

        session.pending_permission_request_id = None
        logger.info(
            "submit_permission_verdict: agent=%r id=%r behavior=%r",
            agent_id, request_id, behavior,
        )
        self.bus.emit(PermissionVerdict(
            agent_id=agent_id,
            request_id=request_id,
            behavior=behavior,  # type: ignore[arg-type]
        ))

    # ── Connection lifecycle (called by the WS adapter) ───────────────────────

    def on_connected(self, agent_id: str) -> None:
        session = self.registry.get(agent_id)
        if session:
            session.connected = True
        self.bus.emit(AgentConnected(agent_id=agent_id))

    def on_disconnected(self, agent_id: str, code: int = 0, reason: str = "") -> None:
        session = self.registry.get(agent_id)
        if session:
            session.connected = False
        self.bus.emit(AgentDisconnected(agent_id=agent_id, code=code, reason=reason))

    def cancel_pending_permission(self, agent_id: str, request_id: str) -> None:
        """Clear the pending permission if it matches request_id."""
        session = self.registry.get(agent_id)
        if session is None:
            return
        if session.pending_permission_request_id == request_id:
            session.pending_permission_request_id = None
            logger.info(
                "cancel_pending_permission: agent=%r id=%r (window expired)",
                agent_id, request_id,
            )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generate_utterance_id() -> str:
    short = str(uuid.uuid4()).replace("-", "")[:8]
    ts = int(datetime.now(timezone.utc).timestamp())
    return f"u-{ts}-{short}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
