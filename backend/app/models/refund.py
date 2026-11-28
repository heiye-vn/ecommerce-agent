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


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_refunds_shop_organization",
        ),
        ForeignKeyConstraint(
            ["shop_id", "order_id"],
            ["orders.shop_id", "orders.id"],
            name="fk_refunds_shop_order",
        ),
        UniqueConstraint("shop_id", "refund_no", name="uq_refunds_shop_no"),
        UniqueConstraint("shop_id", "id", name="uq_refunds_shop_id"),
        CheckConstraint("amount > 0", name="ck_refunds_amount_positive"),
        CheckConstraint(
            "status IN ('pending_approval', 'approved', 'processing', "
            "'succeeded', 'rejected', 'cancelled', 'failed')",
            name="ck_refunds_status",
        ),
        # 数据库层兜底：同一订单同时仅允许存在一笔未终结退款申请，防止并发重复申请
        Index(
            "uq_refunds_active_order",
            "shop_id",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('pending_approval', 'approved', 'processing')"),
        ),
        Index("ix_refunds_shop_status_created", "shop_id", "status", "created_at"),
        Index("ix_refunds_shop_order", "shop_id", "order_id"),
    )

    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int]
    order_id: Mapped[int]
    refund_no: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), server_default="pending_approval")
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    failure_reason: Mapped[str | None] = mapped_column(Text())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
