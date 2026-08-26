from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from atlas.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from atlas.shared.field_types import EXTERNAL_ID_MAX_LENGTH


class WorkflowStatus(StrEnum):
    ACTIVE = "active"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Workflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_workflows_user_idempotency_key",
        ),
        CheckConstraint(
            "status IN "
            "('active', 'waiting_for_input', 'waiting_for_approval', "
            "'completed', 'cancelled', 'failed')",
            name="valid_status",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(
            WorkflowStatus,
            name="workflow_status",
            native_enum=False,
            values_callable=lambda choices: [choice.value for choice in choices],
        ),
        default=WorkflowStatus.ACTIVE,
        server_default=WorkflowStatus.ACTIVE.value,
    )
    # MutableDict tracks top-level edits; nested structures must be replaced when changed.
    state: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON()),
        default=dict,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(EXTERNAL_ID_MAX_LENGTH))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
