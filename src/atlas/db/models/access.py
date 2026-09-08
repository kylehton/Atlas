from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlas.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from atlas.shared.field_types import SHORT_TEXT_MAX_LENGTH

ACCESS_REFERENCE_CODE_LENGTH = 8


class TelegramAccessRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class TelegramAccessRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Track one allowlist decision for a Telegram identity before creating its Atlas user."""

    __tablename__ = "telegram_access_requests"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id",
            name="uq_telegram_access_requests_telegram_user_id",
        ),
        UniqueConstraint(
            "reference_code",
            name="uq_telegram_access_requests_reference_code",
        ),
        UniqueConstraint("user_id", name="uq_telegram_access_requests_user_id"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied')",
            name="valid_status",
        ),
        {
            "comment": (
                "Admin-reviewed Telegram access requests. Atlas users are created only after "
                "approval."
            )
        },
    )

    telegram_user_id: Mapped[int] = mapped_column(BigInteger())
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger())
    telegram_username: Mapped[str | None] = mapped_column(String(SHORT_TEXT_MAX_LENGTH))
    display_name: Mapped[str] = mapped_column(String(SHORT_TEXT_MAX_LENGTH))
    reference_code: Mapped[str] = mapped_column(String(ACCESS_REFERENCE_CODE_LENGTH))
    status: Mapped[TelegramAccessRequestStatus] = mapped_column(
        Enum(
            TelegramAccessRequestStatus,
            name="telegram_access_request_status",
            native_enum=False,
            values_callable=lambda choices: [choice.value for choice in choices],
        ),
        default=TelegramAccessRequestStatus.PENDING,
        server_default=TelegramAccessRequestStatus.PENDING.value,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger())
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
