"""add_google_oauth_requests

Revision ID: 0004_google_oauth
Revises: 0003_access_allowlist
Create Date: 2026-09-08 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_google_oauth"
down_revision: str | Sequence[str] | None = "0003_access_allowlist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_integrations_valid_status"),
        "integrations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_integrations_valid_status"),
        "integrations",
        "status IN ('connected', 'degraded', 'reauthorization_required', "
        "'disconnecting', 'disconnected')",
    )
    op.add_column(
        "integrations",
        sa.Column(
            "encrypted_pending_revocation_tokens",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "integrations",
        sa.Column(
            "credential_version",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_index(
        "uq_integrations_google_workspace_account",
        "integrations",
        ["account_identifier"],
        unique=True,
        postgresql_where=sa.text(
            "provider = 'google' AND capability = 'workspace' AND status != 'disconnected'"
        ),
    )
    op.create_table(
        "google_oauth_requests",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("portal_session_id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("browser_binding_hash", sa.String(length=64), nullable=False),
        sa.Column("requested_capabilities", sa.JSON(), nullable=False),
        sa.Column("pkce_verifier", sa.String(length=128), nullable=True),
        sa.Column("expected_integration_id", sa.Uuid(), nullable=True),
        sa.Column("expected_credential_version", sa.Integer(), nullable=False),
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
            ["portal_session_id"],
            ["settings_browser_sessions.id"],
            name=op.f("fk_google_oauth_requests_portal_session_id_settings_browser_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_google_oauth_requests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_google_oauth_requests")),
        sa.UniqueConstraint("state_hash", name="uq_google_oauth_requests_state_hash"),
        comment=(
            "Short-lived, single-use Google OAuth attempts created by authenticated portal users."
        ),
    )
    op.create_index(
        op.f("ix_google_oauth_requests_portal_session_id"),
        "google_oauth_requests",
        ["portal_session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_google_oauth_requests_user_id"),
        "google_oauth_requests",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_google_oauth_requests_portal_session_id"),
        table_name="google_oauth_requests",
    )
    op.drop_index(
        op.f("ix_google_oauth_requests_user_id"),
        table_name="google_oauth_requests",
    )
    op.drop_table("google_oauth_requests")
    op.drop_index(
        "uq_integrations_google_workspace_account",
        table_name="integrations",
    )
    op.drop_column("integrations", "credential_version")
    op.drop_column("integrations", "encrypted_pending_revocation_tokens")
    op.drop_constraint(
        op.f("ck_integrations_valid_status"),
        "integrations",
        type_="check",
    )
    op.execute(
        "UPDATE integrations SET status = 'reauthorization_required' WHERE status = 'disconnecting'"
    )
    op.create_check_constraint(
        op.f("ck_integrations_valid_status"),
        "integrations",
        "status IN ('connected', 'degraded', 'reauthorization_required', 'disconnected')",
    )
