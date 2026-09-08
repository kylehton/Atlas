"""add_telegram_access_allowlist

Revision ID: 0003_access_allowlist
Revises: 0002_settings_portal
Create Date: 2026-09-08 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_access_allowlist"
down_revision: str | Sequence[str] | None = "0002_settings_portal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_access_requests",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=256), nullable=True),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("reference_code", sa.String(length=8), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "denied",
                name="telegram_access_request_status",
                native_enum=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied')",
            name=op.f("ck_telegram_access_requests_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telegram_access_requests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_access_requests")),
        sa.UniqueConstraint(
            "reference_code",
            name="uq_telegram_access_requests_reference_code",
        ),
        sa.UniqueConstraint(
            "telegram_user_id",
            name="uq_telegram_access_requests_telegram_user_id",
        ),
        sa.UniqueConstraint("user_id", name="uq_telegram_access_requests_user_id"),
        comment=(
            "Admin-reviewed Telegram access requests. Atlas users are created only after approval."
        ),
    )


def downgrade() -> None:
    op.drop_table("telegram_access_requests")
