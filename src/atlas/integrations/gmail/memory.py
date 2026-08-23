from collections.abc import Iterable
from dataclasses import replace

from atlas.integrations.gmail.contracts import (
    EmailDraft,
    EmailDraftRequest,
    EmailMessage,
    EmailMutation,
)
from atlas.shared.field_types import ExternalId


class InMemoryGmail:
    """Store email and draft state in memory for local use and tests."""

    def __init__(self, messages: Iterable[EmailMessage] = ()) -> None:
        self._messages = {message.id: message for message in messages}
        self._drafts: dict[str, EmailDraft] = {}
        self._next_draft_id = 1

    @property
    def drafts(self) -> tuple[EmailDraft, ...]:
        return tuple(self._drafts.values())

    async def list_messages(self, *, query: str = "", limit: int = 20) -> tuple[EmailMessage, ...]:
        """Search sender, subject, and snippet; return newest matches first."""

        if limit < 1:
            raise ValueError("message limit must be positive")
        normalized_query = query.casefold().strip()
        messages = sorted(
            self._messages.values(),
            key=lambda message: (message.received_at, message.id),
            reverse=True,
        )
        if normalized_query:
            messages = [
                message
                for message in messages
                if normalized_query
                in " ".join((message.sender, message.subject, message.snippet)).casefold()
            ]
        return tuple(messages[:limit])

    async def get_message(self, message_id: ExternalId) -> EmailMessage:
        return self._messages[message_id]

    async def create_draft(self, request: EmailDraftRequest) -> EmailDraft:
        draft_id = f"draft-{self._next_draft_id}"
        self._next_draft_id += 1
        draft = EmailDraft(
            id=draft_id,
            recipients=request.recipients,
            subject=request.subject,
            body=request.body,
            thread_id=request.thread_id,
        )
        self._drafts[draft.id] = draft
        return draft

    async def modify_message(self, message_id: ExternalId, mutation: EmailMutation) -> EmailMessage:
        """Apply read, archive, and label changes without sending or deleting mail."""

        current = self._messages[message_id]
        labels = set(current.labels)
        labels.update(mutation.add_labels)
        labels.difference_update(mutation.remove_labels)
        if mutation.archive:
            labels.discard("INBOX")
        message = replace(
            current,
            is_read=mutation.is_read if mutation.is_read is not None else current.is_read,
            labels=frozenset(labels),
        )
        self._messages[message_id] = message
        return message
