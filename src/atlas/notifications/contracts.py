from typing import Literal, Protocol

from pydantic.dataclasses import dataclass

from atlas.shared.field_types import AtlasId, LongText, ShortText

NotificationPriority = Literal["normal", "urgent"]


@dataclass(frozen=True, slots=True)
class NotificationRequest:
    user_id: AtlasId
    text: LongText
    priority: NotificationPriority = "normal"
    deduplication_key: ShortText | None = None


@dataclass(frozen=True, slots=True)
class DeliveredNotification:
    id: AtlasId
    request: NotificationRequest


class NotificationCapability(Protocol):
    async def deliver(self, request: NotificationRequest) -> DeliveredNotification:
        """Deliver a user notification."""
