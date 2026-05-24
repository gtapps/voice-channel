"""
Per-agent Session — tracks connection state and config.

The Session is the addressable unit inside the core.  All four handler
functions resolve a agent_id → Session before acting.
"""

from __future__ import annotations
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentConfig:
    """Runtime config for one registered agent."""
    agent_id: str
    token: str
    triggers: list[str]
    language: Optional[str]     # None = auto-detect
    voice: str                  # Piper .onnx filename
    enable_permission_relay: bool = False


class Session:
    """
    Mutable per-agent state.  Thread-safe via a simple lock.

    Attributes
    ----------
    config : AgentConfig
        Immutable agent configuration.
    connected : bool
        True when the WebSocket adapter has an open connection for this agent.
    last_utterance_id : str | None
        Most-recent utterance dispatched to the agent.
    pending_permission_request_id : str | None
        request_id waiting for a verdict from the operator (at most one at a time).
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._connected = False
        self._last_utterance_id: Optional[str] = None
        self._pending_permission_request_id: Optional[str] = None

    # -- Connection state ------------------------------------------------------

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        with self._lock:
            self._connected = value

    # -- Last utterance --------------------------------------------------------

    @property
    def last_utterance_id(self) -> Optional[str]:
        with self._lock:
            return self._last_utterance_id

    @last_utterance_id.setter
    def last_utterance_id(self, value: str) -> None:
        with self._lock:
            self._last_utterance_id = value

    # -- Permission relay ------------------------------------------------------

    @property
    def pending_permission_request_id(self) -> Optional[str]:
        with self._lock:
            return self._pending_permission_request_id

    @pending_permission_request_id.setter
    def pending_permission_request_id(self, value: Optional[str]) -> None:
        with self._lock:
            self._pending_permission_request_id = value

    def __repr__(self) -> str:
        return (
            f"Session(agent_id={self.config.agent_id!r}, "
            f"connected={self.connected}, "
            f"last_utterance_id={self.last_utterance_id!r})"
        )


class SessionRegistry:
    """
    Holds all registered Sessions, keyed by agent_id.

    The WebSocket adapter registers sessions when agents connect.
    The core looks up sessions by agent_id.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def register(self, config: AgentConfig) -> Session:
        """Create (or replace) the session for an agent."""
        session = Session(config)
        with self._lock:
            self._sessions[config.agent_id] = session
        return session

    def get(self, agent_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(agent_id)

    def all(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    def remove(self, agent_id: str) -> None:
        with self._lock:
            self._sessions.pop(agent_id, None)
