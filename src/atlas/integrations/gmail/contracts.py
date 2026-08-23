from datetime import datetime
from typing import Protocol

from pydantic.dataclasses import dataclass

from atlas.shared.field_types import ExternalId, LongText, ShortText


@dataclass(frozen=True, slots=True)
class EmailMessage:
    id: ExternalId
    thread_id: ExternalId
    sender: ShortText
    subject: ShortText
    snippet: LongText
    received_at: datetime
    is_read: bool = False
    labels: frozenset[ShortText] = frozenset()


@dataclass(frozen=True, slots=True)
class EmailDraftRequest:
    recipients: tuple[ShortText, ...]
    subject: ShortText
    body: LongText
    thread_id: ExternalId | None = None


@dataclass(frozen=True, slots=True)
class EmailDraft:
    id: ExternalId
    recipients: tuple[ShortText, ...]
    subject: ShortText
    body: LongText
    thread_id: ExternalId | None = None


@dataclass(frozen=True, slots=True)
class EmailMutation:
    is_read: bool | None = None
    archive: bool = False
    add_labels: frozenset[ShortText] = frozenset()
    remove_labels: frozenset[ShortText] = frozenset()


class GmailCapability(Protocol):
    async def list_messages(self, *, query: str = "", limit: int = 20) -> tuple[EmailMessage, ...]:
        """Return messages matching a simple provider-neutral query."""

    async def get_message(self, message_id: ExternalId) -> EmailMessage:
        """Return a message by ID."""

    async def create_draft(self, request: EmailDraftRequest) -> EmailDraft:
        """Create a draft without sending it."""

    async def modify_message(self, message_id: ExternalId, mutation: EmailMutation) -> EmailMessage:
        """Apply safe mailbox state changes."""
