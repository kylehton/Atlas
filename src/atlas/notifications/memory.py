from uuid import UUID

from atlas.notifications.contracts import DeliveredNotification, NotificationRequest


class InMemoryNotifications:
    """Record notification deliveries instead of contacting a delivery provider."""

    def __init__(self) -> None:
        self.deliveries: list[DeliveredNotification] = []

    async def deliver(self, request: NotificationRequest) -> DeliveredNotification:
        """Create a deterministic delivery record and return it."""

        delivery = DeliveredNotification(
            id=UUID(int=len(self.deliveries) + 1),
            request=request,
        )
        self.deliveries.append(delivery)
        return delivery
