from datetime import time
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Time, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from atlas.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from atlas.shared.field_types import (
    EXTERNAL_ID_MAX_LENGTH,
    PROVIDER_NAME_MAX_LENGTH,
    SHORT_TEXT_MAX_LENGTH,
)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    display_name: Mapped[str | None] = mapped_column(String(SHORT_TEXT_MAX_LENGTH))


class UserPreference(TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    timezone: Mapped[str] = mapped_column(String(64), server_default="UTC")
    morning_briefing_time: Mapped[time | None] = mapped_column(Time())
    quiet_hours_start: Mapped[time | None] = mapped_column(Time())
    quiet_hours_end: Mapped[time | None] = mapped_column(Time())
    notifications_on_weekends: Mapped[bool] = mapped_column(
        Boolean(),
        server_default=text("true"),
    )


class ExternalIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_external_identities_provider_user_id",
            comment=("Ensures one external provider user ID maps to at most one Atlas user."),
        ),
        UniqueConstraint(
            "user_id",
            "provider",
            name="uq_external_identities_provider",
            comment="Ensures an Atlas user has at most one external identity for a given provider.",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(PROVIDER_NAME_MAX_LENGTH))
    provider_user_id: Mapped[str] = mapped_column(String(EXTERNAL_ID_MAX_LENGTH))
    provider_chat_id: Mapped[str] = mapped_column(String(EXTERNAL_ID_MAX_LENGTH))
