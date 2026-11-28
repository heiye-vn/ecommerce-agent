from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PriceChangeRequest(Base):
    __tablename__ = "price_change_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_price_changes_shop_organization",
        ),
        ForeignKeyConstraint(
            ["shop_id", "sku_id"],
            ["skus.shop_id", "skus.id"],
            name="fk_price_changes_shop_sku",
        ),
        UniqueConstraint("shop_id", "request_no", name="uq_price_changes_shop_no"),
        CheckConstraint("old_price >= 0 AND new_price > 0 AND cost_price >= 0", name="ck_price_changes_amounts"),
        CheckConstraint(
            "status IN ('pending_approval', 'approved', 'processing', "
            "'succeeded', 'rejected', 'cancelled', 'failed')",
            name="ck_price_changes_status",
        ),
        # 数据库层兜底：同一 SKU 同时只允许存在一笔未终结调价申请，避免并发覆盖或重复审批
        Index(
            "uq_price_changes_active_sku",
            "shop_id",
            "sku_id",
            unique=True,
            postgresql_where=text("status IN ('pending_approval', 'approved', 'processing')"),
        ),
        Index("ix_price_changes_shop_status_created", "shop_id", "status", "created_at"),
        Index("ix_price_changes_shop_sku", "shop_id", "sku_id"),
    )

    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int]
    sku_id: Mapped[int]
    request_no: Mapped[str] = mapped_column(String(32))
    old_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    new_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), server_default="pending_approval")
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    failure_reason: Mapped[str | None] = mapped_column(Text())
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
