from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

from atlas.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from atlas.shared.field_types import (
    CAPABILITY_NAME_MAX_LENGTH,
    PROVIDER_NAME_MAX_LENGTH,
    SHA256_HEX_LENGTH,
    SHORT_TEXT_MAX_LENGTH,
)


class IntegrationStatus(StrEnum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    REAUTHORIZATION_REQUIRED = "reauthorization_required"
    DISCONNECTING = "disconnecting"
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
        Index(
            "uq_integrations_google_workspace_account",
            "account_identifier",
            unique=True,
            postgresql_where=text(
                "provider = 'google' AND capability = 'workspace' "
                "AND status != 'disconnected'"
            ),
        ),
        CheckConstraint(
            "status IN ('connected', 'degraded', 'reauthorization_required', "
            "'disconnecting', 'disconnected')",
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
    # Superseded credentials remain encrypted as fallback handles for explicit disconnect.
    encrypted_pending_revocation_tokens: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON()),
        default=list,
        server_default=text("'[]'::json"),
    )
    credential_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    # MutableDict tracks top-level edits; nested structures must be replaced when changed.
    token_metadata: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON()),
        default=dict,
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GoogleOAuthRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bind one expiring Google OAuth attempt to its authenticated portal user."""

    __tablename__ = "google_oauth_requests"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_google_oauth_requests_state_hash"),
        {
            "comment": (
                "Short-lived, single-use Google OAuth attempts created by authenticated portal "
                "users."
            )
        },
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    portal_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("settings_browser_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    state_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH))
    browser_binding_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH))
    requested_capabilities: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON()),
        default=list,
    )
    pkce_verifier: Mapped[str | None] = mapped_column(String(128))
    expected_integration_id: Mapped[UUID | None] = mapped_column(Uuid())
    expected_credential_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
