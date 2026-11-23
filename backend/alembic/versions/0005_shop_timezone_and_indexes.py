"""Add shop timezone, order status constraint, and missing foreign-key indexes."""

import sqlalchemy as sa
from alembic import op

revision = "0005_shop_timezone_and_indexes"
down_revision = "0004_agent_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shops",
        sa.Column("timezone", sa.String(64), server_default="Asia/Shanghai", nullable=False),
    )
    op.create_check_constraint(
        "ck_orders_status",
        "orders",
        "status IN ('pending', 'paid', 'partially_refunded', 'refunded', 'cancelled')",
    )
    op.create_index("ix_order_items_shop_sku", "order_items", ["shop_id", "sku_id"])
    op.create_index("ix_order_items_shop_order", "order_items", ["shop_id", "order_id"])
    op.create_index("ix_inventory_movements_sku_created", "inventory_movements", ["sku_id", "created_at"])
    op.create_index("ix_price_histories_sku_changed", "price_histories", ["sku_id", "changed_at"])


def downgrade() -> None:
    op.drop_index("ix_price_histories_sku_changed", table_name="price_histories")
    op.drop_index("ix_inventory_movements_sku_created", table_name="inventory_movements")
    op.drop_index("ix_order_items_shop_order", table_name="order_items")
    op.drop_index("ix_order_items_shop_sku", table_name="order_items")
    op.drop_constraint("ck_orders_status", "orders", type_="check")
    op.drop_column("shops", "timezone")
