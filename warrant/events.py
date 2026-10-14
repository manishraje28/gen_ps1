"""Tiny event bus so the UI can watch every pipeline stage as it happens."""
from __future__ import annotations

import time
from typing import Any, Callable

EventSink = Callable[[dict[str, Any]], None]


class Emitter:
    """Emits structured events tagged with a lane (``undefended`` / ``warrant``)."""

    def __init__(self, lane: str = "warrant", sink: EventSink | None = None) -> None:
        self.lane = lane
        self.sink = sink
        self.events: list[dict[str, Any]] = []
        self._t0 = time.time()

    def __call__(self, type_: str, **data: Any) -> None:
        event = {"lane": self.lane, "type": type_, "t": round(time.time() - self._t0, 3), **data}
        self.events.append(event)
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception:  # never let the UI break the agent
                pass


NULL_EMITTER = Emitter(lane="null")
