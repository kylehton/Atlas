from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

from atlas.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from atlas.shared.field_types import (
    CAPABILITY_NAME_MAX_LENGTH,
    PROVIDER_NAME_MAX_LENGTH,
    SHORT_TEXT_MAX_LENGTH,
)


class IntegrationStatus(StrEnum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    REAUTHORIZATION_REQUIRED = "reauthorization_required"
    DISCONNECTED = "disconnected"


class Integration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "capability",
            "account_identifier",
            name="uq_integrations_user_provider_capability_account",
        ),
        CheckConstraint(
            "status IN ('connected', 'degraded', 'reauthorization_required', 'disconnected')",
            name="valid_status",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(PROVIDER_NAME_MAX_LENGTH))
    capability: Mapped[str] = mapped_column(String(CAPABILITY_NAME_MAX_LENGTH))
    account_identifier: Mapped[str] = mapped_column(String(SHORT_TEXT_MAX_LENGTH))
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(
            IntegrationStatus,
            name="integration_status",
            native_enum=False,
            values_callable=lambda choices: [choice.value for choice in choices],
        ),
        default=IntegrationStatus.CONNECTED,
        server_default=IntegrationStatus.CONNECTED.value,
    )
    scopes: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSON()), default=list)
    encrypted_refresh_token: Mapped[bytes | None] = mapped_column(LargeBinary())
    # MutableDict tracks top-level edits; nested structures must be replaced when changed.
    token_metadata: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON()),
        default=dict,
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
