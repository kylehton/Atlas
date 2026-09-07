from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from atlas.db.base import Base


class ProcessedTelegramUpdate(Base):
    """Retain only Telegram update IDs needed to reject duplicate webhook deliveries."""

    __tablename__ = "telegram_processed_update_ids"
    __table_args__ = {
        "comment": (
            "Stores only Telegram update IDs and receipt timestamps to prevent duplicate webhook "
            "processing. Does not store message payloads."
        )
    }

    update_id: Mapped[int] = mapped_column(
        BigInteger(),
        primary_key=True,
        autoincrement=False,
        comment="Telegram webhook update identifier used as the deduplication key.",
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="Time the Telegram update was first accepted by Atlas.",
    )
