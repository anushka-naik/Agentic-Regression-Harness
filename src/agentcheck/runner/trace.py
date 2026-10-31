"""Trace sinks. Events are ordered, carry monotonic elapsed time, and may be
persisted incrementally so a crash does not erase an attempt."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agentcheck.schemas.results import EventType, TraceEvent

TOOL_EVENT_TYPES = frozenset({"tool_result", "tool_error", "tool_rejected"})


class TraceSink:
    def __init__(self, on_event: Callable[[TraceEvent], None] | None = None) -> None:
        self.events: list[TraceEvent] = []
        self._start = time.monotonic()
        self._tool_counter = 0
        self._on_event = on_event

    def next_observation_id(self) -> str:
        self._tool_counter += 1
        return f"tool-{self._tool_counter:04d}"

    def emit(
        self, event_type: EventType, payload: dict[str, Any], event_id: str | None = None
    ) -> TraceEvent:
        sequence = len(self.events) + 1
        event = TraceEvent(
            event_id=event_id or f"evt-{sequence:04d}",
            sequence=sequence,
            event_type=event_type,
            payload=payload,
            wall_time=datetime.now(UTC).isoformat(timespec="milliseconds"),
            elapsed_ms=round((time.monotonic() - self._start) * 1000, 3),
        )
        self.events.append(event)
        if self._on_event is not None:
            self._on_event(event)
        return event
