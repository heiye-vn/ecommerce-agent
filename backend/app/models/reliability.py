from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
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
