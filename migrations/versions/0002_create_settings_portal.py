"""create_settings_portal

Revision ID: 0002_settings_portal
Revises: 0001_core_schema
Create Date: 2026-09-08 08:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_settings_portal"
down_revision: str | Sequence[str] | None = "0001_core_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "external_identities",
        sa.Column("provider_username", sa.String(length=256), nullable=True),
    )
    op.alter_column(
        "user_preferences",
        "quiet_hours_start",
        new_column_name="notification_window_start",
    )
    op.alter_column(
        "user_preferences",
        "quiet_hours_end",
        new_column_name="notification_window_end",
    )
    op.add_column(
        "user_preferences",
        sa.Column(
            "notifications_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.create_table(
        "settings_login_requests",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expected_provider_user_id", sa.String(length=256), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("oidc_state_hash", sa.String(length=64), nullable=True),
        sa.Column("oidc_nonce_hash", sa.String(length=64), nullable=True),
        sa.Column("pkce_verifier", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_settings_login_requests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settings_login_requests")),
        sa.UniqueConstraint("request_hash", name="uq_settings_login_requests_request_hash"),
        sa.UniqueConstraint("oidc_state_hash", name="uq_settings_login_requests_oidc_state_hash"),
        comment=(
            "Short-lived requests that require a matching Telegram login before settings access "
            "is granted."
        ),
    )
    op.create_index(
        op.f("ix_settings_login_requests_user_id"),
        "settings_login_requests",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "settings_browser_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_settings_browser_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settings_browser_sessions")),
        sa.UniqueConstraint("session_hash", name="uq_settings_browser_sessions_session_hash"),
        comment="Revocable browser sessions authorized to manage Atlas settings.",
    )
    op.create_index(
        op.f("ix_settings_browser_sessions_user_id"),
        "settings_browser_sessions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_settings_browser_sessions_user_id"),
        table_name="settings_browser_sessions",
    )
    op.drop_table("settings_browser_sessions")
    op.drop_index(
        op.f("ix_settings_login_requests_user_id"),
        table_name="settings_login_requests",
    )
    op.drop_table("settings_login_requests")
    op.drop_column("user_preferences", "notifications_enabled")
    op.alter_column(
        "user_preferences",
        "notification_window_end",
        new_column_name="quiet_hours_end",
    )
    op.alter_column(
        "user_preferences",
        "notification_window_start",
        new_column_name="quiet_hours_start",
    )
    op.drop_column("external_identities", "provider_username")
