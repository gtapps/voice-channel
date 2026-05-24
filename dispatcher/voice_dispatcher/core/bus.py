"""
Synchronous event bus — fanout publish/subscribe.

Subscribers register for a specific event class (by type).  When an event is
emitted all matching subscribers are called in registration order.  Callbacks
are called synchronously in the emitter's thread; heavy work should be
dispatched to a thread/queue inside the callback.

Usage::

    bus = EventBus()
    bus.subscribe(SpeakRequest, lambda e: tts_queue.put(e))
    bus.emit(SpeakRequest(hermit_id="jarvis", utterance_id="u-1", text="Hi"))
"""

from __future__ import annotations
import threading
from collections import defaultdict
from typing import Any, Callable, Type, TypeVar

T = TypeVar("T")


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # event_type → list of callbacks
        self._subscribers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: Type[T], callback: Callable[[T], None]) -> None:
        with self._lock:
            self._subscribers[event_type].append(callback)  # type: ignore[arg-type]

    def unsubscribe(self, event_type: Type[T], callback: Callable[[T], None]) -> None:
        with self._lock:
            subs = self._subscribers.get(event_type, [])
            try:
                subs.remove(callback)  # type: ignore[arg-type]
            except ValueError:
                pass

    def emit(self, event: Any) -> None:
        with self._lock:
            callbacks = list(self._subscribers.get(type(event), []))
        for cb in callbacks:
            cb(event)
