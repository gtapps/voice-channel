"""
Per-hermit Session — tracks connection state and config.

The Session is the addressable unit inside the core.  All four handler
functions resolve a hermit_id → Session before acting.
"""

from __future__ import annotations
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HermitConfig:
    """Runtime config for one registered hermit."""
    hermit_id: str
    token: str
    triggers: list[str]
    language: Optional[str]     # None = auto-detect
    voice: str                  # Piper .onnx filename
    enable_permission_relay: bool = False


class Session:
    """
    Mutable per-hermit state.  Thread-safe via a simple lock.

    Attributes
    ----------
    config : HermitConfig
        Immutable hermit configuration.
    connected : bool
        True when the WebSocket adapter has an open connection for this hermit.
    last_utterance_id : str | None
        Most-recent utterance dispatched to the hermit.
    pending_permission_request_id : str | None
        request_id waiting for a verdict from the operator (at most one at a time).
    """

    def __init__(self, config: HermitConfig) -> None:
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
            f"Session(hermit_id={self.config.hermit_id!r}, "
            f"connected={self.connected}, "
            f"last_utterance_id={self.last_utterance_id!r})"
        )


class SessionRegistry:
    """
    Holds all registered Sessions, keyed by hermit_id.

    The WebSocket adapter registers sessions when hermits connect.
    The core looks up sessions by hermit_id.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def register(self, config: HermitConfig) -> Session:
        """Create (or replace) the session for a hermit."""
        session = Session(config)
        with self._lock:
            self._sessions[config.hermit_id] = session
        return session

    def get(self, hermit_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(hermit_id)

    def all(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    def remove(self, hermit_id: str) -> None:
        with self._lock:
            self._sessions.pop(hermit_id, None)
