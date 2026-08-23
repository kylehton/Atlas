from datetime import datetime
from typing import Protocol

from pydantic.dataclasses import dataclass

from atlas.shared.field_types import ExternalId, LongText, ShortText


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    id: ExternalId
    title: ShortText
    starts_at: datetime
    ends_at: datetime
    description: LongText | None = None
    location: ShortText | None = None


@dataclass(frozen=True, slots=True)
class CalendarEventRequest:
    title: ShortText
    starts_at: datetime
    ends_at: datetime
    description: LongText | None = None
    location: ShortText | None = None


@dataclass(frozen=True, slots=True)
class CalendarEventChange:
    title: ShortText | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    description: LongText | None = None
    location: ShortText | None = None


class CalendarCapability(Protocol):
    async def list_events(
        self, *, starts_at: datetime, ends_at: datetime
    ) -> tuple[CalendarEvent, ...]:
        """Return events overlapping the requested time range."""

    async def create_event(self, request: CalendarEventRequest) -> CalendarEvent:
        """Create and return an event."""

    async def update_event(
        self, event_id: ExternalId, change: CalendarEventChange
    ) -> CalendarEvent:
        """Update and return an event."""

    async def delete_event(self, event_id: ExternalId) -> None:
        """Delete an event."""
