"""Add price_change_requests table, price_histories metadata, and stage5a job/notification types."""

import sqlalchemy as sa
from alembic import op

revision = "0009_price_changes_and_analytics"
down_revision = "0008_refunds_and_inventory_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_change_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("request_no", sa.String(32), nullable=False),
        sa.Column("old_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("new_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending_approval", nullable=False),
        sa.Column("requester_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_price_changes_shop_organization",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "sku_id"],
            ["skus.shop_id", "skus.id"],
            name="fk_price_changes_shop_sku",
        ),
        sa.UniqueConstraint("shop_id", "request_no", name="uq_price_changes_shop_no"),
        sa.CheckConstraint(
            "old_price >= 0 AND new_price > 0 AND cost_price >= 0",
            name="ck_price_changes_amounts",
        ),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'approved', 'processing', "
            "'succeeded', 'rejected', 'cancelled', 'failed')",
            name="ck_price_changes_status",
        ),
    )
    op.create_index(
        "uq_price_changes_active_sku",
        "price_change_requests",
        ["shop_id", "sku_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending_approval', 'approved', 'processing')"),
    )
    op.create_index(
        "ix_price_changes_shop_status_created",
        "price_change_requests",
        ["shop_id", "status", "created_at"],
    )
    op.create_index("ix_price_changes_shop_sku", "price_change_requests", ["shop_id", "sku_id"])

    op.add_column("price_histories", sa.Column("shop_id", sa.Integer(), nullable=True))
    op.add_column("price_histories", sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("price_histories", sa.Column("approval_no", sa.String(32), nullable=True))
    op.add_column(
        "price_histories",
        sa.Column("price_change_request_id", sa.Integer(), sa.ForeignKey("price_change_requests.id"), nullable=True),
    )
    op.add_column("price_histories", sa.Column("reason", sa.String(500), nullable=True))

    op.execute(
        "UPDATE price_histories AS ph SET shop_id = s.shop_id "
        "FROM skus AS s WHERE s.id = ph.sku_id AND ph.shop_id IS NULL"
    )
    op.create_foreign_key(
        "fk_price_histories_shop_sku",
        "price_histories",
        "skus",
        ["shop_id", "sku_id"],
        ["shop_id", "id"],
    )
    op.create_index(
        "ix_price_histories_shop_sku_changed",
        "price_histories",
        ["shop_id", "sku_id", "changed_at"],
    )

    op.drop_constraint("ck_jobs_type", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "jobs",
        "job_type IN ('promotion_schedule', 'promotion_activate', 'promotion_complete', "
        "'refund_execute', 'price_change_execute')",
    )

    op.drop_constraint("ck_notifications_category", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_category",
        "notifications",
        "category IN ('approval', 'promotion', 'job', 'refund', 'price_change')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM notifications WHERE category = 'price_change'")
    op.drop_constraint("ck_notifications_category", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_category",
        "notifications",
        "category IN ('approval', 'promotion', 'job', 'refund')",
    )

    op.execute("DELETE FROM outbox_events WHERE event_type = 'price_change.execute.requested'")
    op.execute("DELETE FROM jobs WHERE job_type = 'price_change_execute'")
    op.drop_constraint("ck_jobs_type", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "jobs",
        "job_type IN ('promotion_schedule', 'promotion_activate', 'promotion_complete', 'refund_execute')",
    )

    op.drop_index("ix_price_histories_shop_sku_changed", table_name="price_histories")
    op.drop_constraint("fk_price_histories_shop_sku", "price_histories", type_="foreignkey")
    op.drop_column("price_histories", "reason")
    op.drop_column("price_histories", "price_change_request_id")
    op.drop_column("price_histories", "approval_no")
    op.drop_column("price_histories", "actor_id")
    op.drop_column("price_histories", "shop_id")

    op.drop_index("ix_price_changes_shop_sku", table_name="price_change_requests")
    op.drop_index("ix_price_changes_shop_status_created", table_name="price_change_requests")
    op.drop_index("uq_price_changes_active_sku", table_name="price_change_requests")
    op.drop_table("price_change_requests")
