from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from atlas.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from atlas.shared.field_types import EXTERNAL_ID_MAX_LENGTH, SHA256_HEX_LENGTH


class SettingsLoginRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bind one short-lived browser login attempt to an expected Atlas user."""

    __tablename__ = "settings_login_requests"
    __table_args__ = (
        UniqueConstraint("request_hash", name="uq_settings_login_requests_request_hash"),
        UniqueConstraint("oidc_state_hash", name="uq_settings_login_requests_oidc_state_hash"),
        {
            "comment": (
                "Short-lived requests that require a matching Telegram login before settings "
                "access is granted."
            )
        },
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    expected_provider_user_id: Mapped[str] = mapped_column(String(EXTERNAL_ID_MAX_LENGTH))
    request_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH))
    oidc_state_hash: Mapped[str | None] = mapped_column(String(SHA256_HEX_LENGTH))
    oidc_nonce_hash: Mapped[str | None] = mapped_column(String(SHA256_HEX_LENGTH))
    pkce_verifier: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SettingsBrowserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Store a hashed browser session after Telegram verifies the expected user."""

    __tablename__ = "settings_browser_sessions"
    __table_args__ = (
        UniqueConstraint("session_hash", name="uq_settings_browser_sessions_session_hash"),
        {"comment": "Revocable browser sessions authorized to manage Atlas settings."},
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    session_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
