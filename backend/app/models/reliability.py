from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OperationRequest(Base):
    __tablename__ = "operation_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_operation_requests_shop_organization",
        ),
        UniqueConstraint(
            "organization_id",
            "operation_type",
            "idempotency_key",
            name="uq_operation_requests_idempotency",
        ),
        CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed')",
            name="ck_operation_requests_status",
        ),
    )

    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int]
    operation_type: Mapped[str] = mapped_column(String(48))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), server_default="processing")
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[int | None]
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_audit_logs_shop_organization",
        ),
        Index("ix_audit_logs_shop_resource", "shop_id", "resource_type", "resource_id", "created_at"),
    )

    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int]
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[int]
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_jobs_shop_organization",
        ),
        UniqueConstraint("shop_id", "job_no", name="uq_jobs_shop_no"),
        UniqueConstraint("organization_id", "job_type", "idempotency_key", name="uq_jobs_idempotency"),
        Index("ix_jobs_shop_status_updated", "shop_id", "status", "updated_at"),
        Index("ix_jobs_resource", "shop_id", "resource_type", "resource_id"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'retrying', 'failed', 'dead_lettered')",
            name="ck_jobs_status",
        ),
        CheckConstraint(
            "job_type IN ('promotion_schedule', 'promotion_activate', 'promotion_complete', "
            "'refund_execute', 'price_change_execute')",
            name="ck_jobs_type",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1 AND resource_version >= 1",
            name="ck_jobs_counters",
        ),
    )

    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int]
    job_no: Mapped[str] = mapped_column(String(32))
    job_type: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), server_default="pending")
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[int]
    resource_version: Mapped[int] = mapped_column(Integer, server_default="1")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="3")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_outbox_events_shop_organization",
        ),
        UniqueConstraint("event_id", name="uq_outbox_events_event_id"),
        Index("ix_outbox_pending_available", "status", "available_at"),
        CheckConstraint("status IN ('pending', 'published', 'failed')", name="ck_outbox_events_status"),
        CheckConstraint("retry_count >= 0", name="ck_outbox_events_retry_count"),
    )

    event_id: Mapped[str] = mapped_column(String(64))
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int]
    event_type: Mapped[str] = mapped_column(String(64))
    aggregate_type: Mapped[str] = mapped_column(String(40))
    aggregate_id: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), server_default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_notifications_shop_organization",
        ),
        Index("ix_notifications_user_shop_read", "user_id", "shop_id", "is_read", "created_at"),
        CheckConstraint(
            "category IN ('approval', 'promotion', 'job', 'refund', 'price_change')",
            name="ck_notifications_category",
        ),
    )

    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int]
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text())
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[int | None]
    is_read: Mapped[bool] = mapped_column(Boolean, server_default=false())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
