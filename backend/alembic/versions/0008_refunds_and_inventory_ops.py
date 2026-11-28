"""Add refunds table, inventory movement snapshots, order fulfillment fields, and stage4 constraints."""

import sqlalchemy as sa
from alembic import op

revision = "0008_refunds_and_inventory_ops"
down_revision = "0007_jobs_outbox_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("refund_no", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending_approval", nullable=False),
        sa.Column("requester_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_refunds_shop_organization",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "order_id"],
            ["orders.shop_id", "orders.id"],
            name="fk_refunds_shop_order",
        ),
        sa.UniqueConstraint("shop_id", "refund_no", name="uq_refunds_shop_no"),
        sa.UniqueConstraint("shop_id", "id", name="uq_refunds_shop_id"),
        sa.CheckConstraint("amount > 0", name="ck_refunds_amount_positive"),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'approved', 'processing', "
            "'succeeded', 'rejected', 'cancelled', 'failed')",
            name="ck_refunds_status",
        ),
    )
    op.create_index(
        "uq_refunds_active_order",
        "refunds",
        ["shop_id", "order_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending_approval', 'approved', 'processing')"),
    )
    op.create_index("ix_refunds_shop_status_created", "refunds", ["shop_id", "status", "created_at"])
    op.create_index("ix_refunds_shop_order", "refunds", ["shop_id", "order_id"])

    op.add_column(
        "orders",
        sa.Column("fulfillment_status", sa.String(32), server_default="pending_shipment", nullable=False),
    )
    op.add_column("orders", sa.Column("exception_type", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_orders_fulfillment_status",
        "orders",
        "fulfillment_status IN ('pending_shipment', 'shipped', 'delivered', 'cancelled')",
    )

    op.add_column("inventory_movements", sa.Column("shop_id", sa.Integer(), nullable=True))
    op.add_column(
        "inventory_movements",
        sa.Column("available_delta", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "inventory_movements",
        sa.Column("reserved_delta", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("inventory_movements", sa.Column("before_available", sa.Integer(), nullable=True))
    op.add_column("inventory_movements", sa.Column("after_available", sa.Integer(), nullable=True))
    op.add_column("inventory_movements", sa.Column("before_reserved", sa.Integer(), nullable=True))
    op.add_column("inventory_movements", sa.Column("after_reserved", sa.Integer(), nullable=True))
    op.add_column(
        "inventory_movements",
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column("inventory_movements", sa.Column("note", sa.String(255), nullable=True))

    op.execute(
        "UPDATE inventory_movements AS im SET shop_id = s.shop_id "
        "FROM skus AS s WHERE s.id = im.sku_id AND im.shop_id IS NULL"
    )
    op.create_foreign_key(
        "fk_inventory_movements_shop_sku",
        "inventory_movements",
        "skus",
        ["shop_id", "sku_id"],
        ["shop_id", "id"],
    )
    op.create_index(
        "ix_inventory_movements_shop_sku_created",
        "inventory_movements",
        ["shop_id", "sku_id", "created_at"],
    )
    op.create_check_constraint(
        "ck_inventory_movements_type",
        "inventory_movements",
        "movement_type IN ('opening_balance', 'reserve', 'release', 'fulfill', 'restock', 'adjust')",
    )

    op.drop_constraint("ck_jobs_type", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "jobs",
        "job_type IN ('promotion_schedule', 'promotion_activate', 'promotion_complete', 'refund_execute')",
    )

    op.drop_constraint("ck_notifications_category", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_category",
        "notifications",
        "category IN ('approval', 'promotion', 'job', 'refund')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM notifications WHERE category = 'refund'")
    op.drop_constraint("ck_notifications_category", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_category",
        "notifications",
        "category IN ('approval', 'promotion', 'job')",
    )

    op.execute("DELETE FROM outbox_events WHERE event_type = 'refund.execute.requested'")
    op.execute("DELETE FROM jobs WHERE job_type = 'refund_execute'")
    op.drop_constraint("ck_jobs_type", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "jobs",
        "job_type IN ('promotion_schedule', 'promotion_activate', 'promotion_complete')",
    )

    op.drop_constraint("ck_inventory_movements_type", "inventory_movements", type_="check")
    op.drop_index("ix_inventory_movements_shop_sku_created", table_name="inventory_movements")
    op.drop_constraint("fk_inventory_movements_shop_sku", "inventory_movements", type_="foreignkey")
    op.drop_column("inventory_movements", "note")
    op.drop_column("inventory_movements", "actor_id")
    op.drop_column("inventory_movements", "after_reserved")
    op.drop_column("inventory_movements", "before_reserved")
    op.drop_column("inventory_movements", "after_available")
    op.drop_column("inventory_movements", "before_available")
    op.drop_column("inventory_movements", "reserved_delta")
    op.drop_column("inventory_movements", "available_delta")
    op.drop_column("inventory_movements", "shop_id")

    op.drop_constraint("ck_orders_fulfillment_status", "orders", type_="check")
    op.drop_column("orders", "exception_type")
    op.drop_column("orders", "fulfillment_status")

    op.drop_index("ix_refunds_shop_order", table_name="refunds")
    op.drop_index("ix_refunds_shop_status_created", table_name="refunds")
    op.drop_index("uq_refunds_active_order", table_name="refunds")
    op.drop_table("refunds")
