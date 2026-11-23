from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Promotion(Base):
    __tablename__ = "promotions"
    __table_args__ = (
        UniqueConstraint("shop_id", "id", name="uq_promotions_shop_id"),
        UniqueConstraint("shop_id", "promotion_no", name="uq_promotions_shop_no"),
        Index("ix_promotions_shop_status_window", "shop_id", "status", "starts_at", "ends_at"),
        CheckConstraint("version >= 1", name="ck_promotions_version"),
        CheckConstraint("minimum_qty >= 0", name="ck_promotions_minimum_qty"),
        CheckConstraint("ends_at > starts_at", name="ck_promotions_window"),
        CheckConstraint(
            "status IN ('pending_approval', 'approved', 'scheduled', 'active', "
            "'completed', 'rejected', 'cancelled', 'failed')",
            name="ck_promotions_status",
        ),
    )

    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    promotion_no: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    status: Mapped[str] = mapped_column(String(32))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    minimum_qty: Mapped[int] = mapped_column(Integer, server_default="100")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    failure_reason: Mapped[str | None] = mapped_column(Text())
    conflict_promotion_no: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PromotionItem(Base):
    __tablename__ = "promotion_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["shop_id", "promotion_id"],
            ["promotions.shop_id", "promotions.id"],
            ondelete="CASCADE",
            name="fk_promotion_items_shop_promotion",
        ),
        ForeignKeyConstraint(
            ["shop_id", "sku_id"],
            ["skus.shop_id", "skus.id"],
            name="fk_promotion_items_shop_sku",
        ),
        UniqueConstraint("promotion_id", "sku_id", name="uq_promotion_items_promotion_sku"),
        Index("ix_promotion_items_shop_sku", "shop_id", "sku_id"),
        CheckConstraint(
            "original_price >= 0 AND cost_price >= 0 AND promotion_price >= 0 "
            "AND discount_rate > 0 AND discount_rate < 1",
            name="ck_promotion_items_amounts",
        ),
    )

    shop_id: Mapped[int]
    promotion_id: Mapped[int]
    sku_id: Mapped[int]
    original_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    promotion_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_approvals_shop_organization",
        ),
        UniqueConstraint("shop_id", "approval_no", name="uq_approvals_shop_no"),
        UniqueConstraint(
            "resource_type",
            "resource_id",
            "resource_version",
            name="uq_approvals_resource_version",
        ),
        Index("ix_approvals_shop_status_created", "shop_id", "status", "created_at"),
        CheckConstraint("resource_version >= 1", name="ck_approvals_version"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_approvals_status",
        ),
    )

    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int]
    approval_no: Mapped[str] = mapped_column(String(32))
    action_type: Mapped[str] = mapped_column(String(40))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[int]
    resource_version: Mapped[int] = mapped_column(Integer, server_default="1")
    payload_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), server_default="pending")
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decision_comment: Mapped[str | None] = mapped_column(Text())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
