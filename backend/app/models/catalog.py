from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric, String,
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

    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    movement_type: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[int]
    reference_id: Mapped[str] = mapped_column(String(64))


class PriceHistory(Base):
    __tablename__ = "price_histories"
    __table_args__ = (
        CheckConstraint("old_price >= 0 AND new_price >= 0", name="ck_price_histories_amounts"),
    )

    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    old_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    new_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("shop_id", "id", name="uq_orders_shop_id"),
        UniqueConstraint("shop_id", "order_no", name="uq_orders_shop_number"),
        Index("ix_orders_shop_paid_at", "shop_id", "paid_at"),
        CheckConstraint(
            "paid_amount >= 0 AND (status <> 'paid' OR paid_at IS NOT NULL)", name="ck_orders_payment",
        ),
    )

    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    order_no: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
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
