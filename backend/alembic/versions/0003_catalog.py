"""Create shop-scoped catalog, inventory, and order tables."""

import sqlalchemy as sa
from alembic import op


revision = "0003_catalog"
down_revision = "0002_login_auth"
branch_labels = None
depends_on = None


def create_table(name: str, *fields: sa.Column | sa.Constraint) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        *fields,
    )


def upgrade() -> None:
    create_table(
        "products",
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.UniqueConstraint("shop_id", "id", name="uq_products_shop_id"),
    )
    create_table(
        "skus",
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sku_code", sa.String(64), nullable=False),
        sa.Column("cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("current_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.ForeignKeyConstraint(
            ["shop_id", "product_id"], ["products.shop_id", "products.id"], name="fk_skus_shop_product",
        ),
        sa.UniqueConstraint("shop_id", "sku_code", name="uq_skus_shop_code"),
        sa.UniqueConstraint("shop_id", "id", name="uq_skus_shop_id"),
        sa.CheckConstraint("cost >= 0 AND current_price >= 0", name="ck_skus_amounts"),
    )
    create_table(
        "inventory",
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("available_qty", sa.Integer(), nullable=False),
        sa.Column("reserved_qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("sku_id", name="uq_inventory_sku"),
        sa.CheckConstraint("available_qty >= 0 AND reserved_qty >= 0 AND version >= 1", name="ck_inventory_snapshot"),
    )
    create_table(
        "inventory_movements",
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("movement_type", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reference_id", sa.String(64), nullable=False),
    )
    create_table(
        "price_histories",
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("old_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("new_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("old_price >= 0 AND new_price >= 0", name="ck_price_histories_amounts"),
    )
    create_table(
        "orders",
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("order_no", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("paid_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("shop_id", "id", name="uq_orders_shop_id"),
        sa.UniqueConstraint("shop_id", "order_no", name="uq_orders_shop_number"),
        sa.CheckConstraint(
            "paid_amount >= 0 AND (status <> 'paid' OR paid_at IS NOT NULL)", name="ck_orders_payment",
        ),
    )
    op.create_index("ix_orders_shop_paid_at", "orders", ["shop_id", "paid_at"])
    create_table(
        "order_items",
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["shop_id", "order_id"], ["orders.shop_id", "orders.id"], name="fk_order_items_shop_order",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "sku_id"], ["skus.shop_id", "skus.id"], name="fk_order_items_shop_sku",
        ),
        sa.CheckConstraint(
            "quantity > 0 AND unit_price >= 0 AND paid_amount >= 0", name="ck_order_items_amounts",
        ),
    )


def downgrade() -> None:
    for table in ("order_items", "orders", "price_histories", "inventory_movements", "inventory", "skus", "products"):
        op.drop_table(table)
