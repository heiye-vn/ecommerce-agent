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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("shop_id", "id", name="uq_products_shop_id"),)

    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), server_default="active")


class Sku(Base):
    __tablename__ = "skus"
    __table_args__ = (
        ForeignKeyConstraint(
            ["shop_id", "product_id"], ["products.shop_id", "products.id"],
            name="fk_skus_shop_product",
        ),
        UniqueConstraint("shop_id", "sku_code", name="uq_skus_shop_code"),
        UniqueConstraint("shop_id", "id", name="uq_skus_shop_id"),
        CheckConstraint("cost >= 0 AND current_price >= 0", name="ck_skus_amounts"),
    )

    shop_id: Mapped[int]
    product_id: Mapped[int]
    sku_code: Mapped[str] = mapped_column(String(64))
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    current_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(20), server_default="active")


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("sku_id", name="uq_inventory_sku"),
        CheckConstraint("available_qty >= 0 AND reserved_qty >= 0 AND version >= 1", name="ck_inventory_snapshot"),
    )

    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    available_qty: Mapped[int]
    reserved_qty: Mapped[int] = mapped_column(server_default="0")
    version: Mapped[int] = mapped_column(server_default="1")


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["shop_id", "sku_id"],
            ["skus.shop_id", "skus.id"],
            name="fk_inventory_movements_shop_sku",
        ),
        Index("ix_inventory_movements_sku_created", "sku_id", "created_at"),
        Index("ix_inventory_movements_shop_sku_created", "shop_id", "sku_id", "created_at"),
        CheckConstraint(
            "movement_type IN ('opening_balance', 'reserve', 'release', 'fulfill', 'restock', 'adjust')",
            name="ck_inventory_movements_type",
        ),
    )

    shop_id: Mapped[int | None]
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    movement_type: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[int]
    available_delta: Mapped[int] = mapped_column(server_default="0")
    reserved_delta: Mapped[int] = mapped_column(server_default="0")
    before_available: Mapped[int | None]
    after_available: Mapped[int | None]
    before_reserved: Mapped[int | None]
    after_reserved: Mapped[int | None]
    reference_id: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(String(255))


class PriceHistory(Base):
    __tablename__ = "price_histories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["shop_id", "sku_id"],
            ["skus.shop_id", "skus.id"],
            name="fk_price_histories_shop_sku",
        ),
        CheckConstraint("old_price >= 0 AND new_price >= 0", name="ck_price_histories_amounts"),
        Index("ix_price_histories_sku_changed", "sku_id", "changed_at"),
        Index("ix_price_histories_shop_sku_changed", "shop_id", "sku_id", "changed_at"),
    )

    shop_id: Mapped[int | None]
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    old_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    new_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approval_no: Mapped[str | None] = mapped_column(String(32))
    price_change_request_id: Mapped[int | None] = mapped_column(ForeignKey("price_change_requests.id"))
    reason: Mapped[str | None] = mapped_column(String(500))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("shop_id", "id", name="uq_orders_shop_id"),
        UniqueConstraint("shop_id", "order_no", name="uq_orders_shop_number"),
        Index("ix_orders_shop_paid_at", "shop_id", "paid_at"),
        CheckConstraint(
            "status IN ('pending', 'paid', 'partially_refunded', 'refunded', 'cancelled')",
            name="ck_orders_status",
        ),
        CheckConstraint(
            "fulfillment_status IN ('pending_shipment', 'shipped', 'delivered', 'cancelled')",
            name="ck_orders_fulfillment_status",
        ),
        CheckConstraint(
            "paid_amount >= 0 AND (status <> 'paid' OR paid_at IS NOT NULL)", name="ck_orders_payment",
        ),
    )

    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    order_no: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    fulfillment_status: Mapped[str] = mapped_column(String(32), server_default="pending_shipment")
    exception_type: Mapped[str | None] = mapped_column(String(64))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["shop_id", "order_id"], ["orders.shop_id", "orders.id"], name="fk_order_items_shop_order",
        ),
        ForeignKeyConstraint(
            ["shop_id", "sku_id"], ["skus.shop_id", "skus.id"], name="fk_order_items_shop_sku",
        ),
        Index("ix_order_items_shop_sku", "shop_id", "sku_id"),
        Index("ix_order_items_shop_order", "shop_id", "order_id"),
        CheckConstraint(
            "quantity > 0 AND unit_price >= 0 AND paid_amount >= 0", name="ck_order_items_amounts",
        ),
    )

    shop_id: Mapped[int]
    order_id: Mapped[int]
    sku_id: Mapped[int]
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
