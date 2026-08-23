"""Gmail capability."""

from atlas.integrations.gmail.contracts import (
    EmailDraft,
    EmailDraftRequest,
    EmailMessage,
    EmailMutation,
    GmailCapability,
)
from atlas.integrations.gmail.memory import InMemoryGmail

__all__ = [
    "EmailDraft",
    "EmailDraftRequest",
    "EmailMessage",
    "EmailMutation",
    "GmailCapability",
    "InMemoryGmail",
]
