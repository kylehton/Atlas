from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime

from atlas.integrations.calendar.contracts import (
    CalendarEvent,
    CalendarEventChange,
    CalendarEventRequest,
)
from atlas.shared.field_types import ExternalId


class InMemoryCalendar:
    """Store calendar events in memory with stable IDs for local use and tests."""

    def __init__(self, events: Iterable[CalendarEvent] = ()) -> None:
        self._events = {event.id: event for event in events}
        self._next_id = 1

    async def list_events(
        self, *, starts_at: datetime, ends_at: datetime
    ) -> tuple[CalendarEvent, ...]:
        """Return overlapping events ordered by start time, then ID."""

        self._validate_window(starts_at, ends_at)
        matching = (
            event
            for event in self._events.values()
            if event.starts_at < ends_at and event.ends_at > starts_at
        )
        return tuple(sorted(matching, key=lambda event: (event.starts_at, event.id)))

    async def create_event(self, request: CalendarEventRequest) -> CalendarEvent:
        self._validate_window(request.starts_at, request.ends_at)
        event = CalendarEvent(
            id=self._take_id(),
            title=request.title,
            starts_at=request.starts_at,
            ends_at=request.ends_at,
            description=request.description,
            location=request.location,
        )
        self._events[event.id] = event
        return event

    async def update_event(
        self, event_id: ExternalId, change: CalendarEventChange
    ) -> CalendarEvent:
        """Apply supplied fields to an existing event and revalidate its time range."""

        current = self._events[event_id]
        event = replace(
            current,
            title=change.title if change.title is not None else current.title,
            starts_at=change.starts_at if change.starts_at is not None else current.starts_at,
            ends_at=change.ends_at if change.ends_at is not None else current.ends_at,
            description=(
                change.description if change.description is not None else current.description
            ),
            location=change.location if change.location is not None else current.location,
        )
        self._validate_window(event.starts_at, event.ends_at)
        self._events[event_id] = event
        return event

    async def delete_event(self, event_id: ExternalId) -> None:
        del self._events[event_id]

    def _take_id(self) -> ExternalId:
        """Return the next deterministic ID without colliding with seeded events."""

        while (event_id := f"calendar-{self._next_id}") in self._events:
            self._next_id += 1
        self._next_id += 1
        return event_id

    @staticmethod
    def _validate_window(starts_at: datetime, ends_at: datetime) -> None:
        """Reject an event whose end is not later than its start."""

        if ends_at <= starts_at:
            raise ValueError("calendar event must end after it starts")
