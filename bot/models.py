"""SQLAlchemy 2 ORM models for the RSTELBOT MVP."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow_naive() -> datetime:
    """Return UTC as a naive datetime for MySQL DATETIME columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ContentType(str, Enum):
    FREE = "free"
    PAID = "paid"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    terms_version: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Content(Base):
    __tablename__ = "contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    type: Mapped[ContentType] = mapped_column(
        SqlEnum(
            ContentType,
            name="content_type",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    teaser: Mapped[str | None] = mapped_column(Text, nullable=True)
    stars_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_msg_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    preview_storage_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )


class AccessCode(Base):
    __tablename__ = "access_codes"
    __table_args__ = (Index("ix_access_codes_telegram_id", "telegram_id"),)

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )


class Access(Base):
    __tablename__ = "access"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    code: Mapped[str | None] = mapped_column(
        String(16),
        ForeignKey("access_codes.code", ondelete="SET NULL"),
        nullable=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )


class CodeAttempt(Base):
    __tablename__ = "code_attempts"
    __table_args__ = (Index("ix_code_attempts_telegram_id_at", "telegram_id", "at"),)

    # The source schema has no primary key here; an auto-increment key allows
    # SQLAlchemy to map every attempt while preserving the specified columns.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class StagedUpload(Base):
    """Durable mapping from an admin's source message to the storage copy."""

    __tablename__ = "upload_staging"
    __table_args__ = (
        Index("ix_upload_staging_chat_album", "source_chat_id", "media_group_id"),
    )

    source_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )


class DefaultPreview(Base):
    """Single reusable fallback preview stored in the Telegram storage channel."""

    __tablename__ = "default_preview"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    storage_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )


class ScheduledPublication(Base):
    """Durable queue item for a free channel post or paid-content teaser."""

    __tablename__ = "scheduled_publications"
    __table_args__ = (
        Index("ix_scheduled_publications_status_at", "status", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    teaser: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    channel_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class StarPayment(Base):
    """Telegram Stars invoice intent and its verified successful payment."""

    __tablename__ = "star_payments"
    __table_args__ = (
        Index("uq_star_payments_telegram_charge_id", "telegram_charge_id", unique=True),
        Index("ix_star_payments_user_content", "telegram_id", "content_id"),
    )

    payload: Mapped[str] = mapped_column(String(64), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="XTR")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    telegram_charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )
    prechecked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
