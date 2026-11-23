"""Create promotions, approvals, operation_requests, and audit_logs tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_promotions_approvals_audit"
down_revision = "0005_shop_timezone_and_indexes"
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
        "promotions",
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("promotion_no", sa.String(32), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minimum_qty", sa.Integer(), server_default="100", nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("conflict_promotion_no", sa.String(32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("shop_id", "id", name="uq_promotions_shop_id"),
        sa.UniqueConstraint("shop_id", "promotion_no", name="uq_promotions_shop_no"),
        sa.CheckConstraint("version >= 1", name="ck_promotions_version"),
        sa.CheckConstraint("minimum_qty >= 0", name="ck_promotions_minimum_qty"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_promotions_window"),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'approved', 'scheduled', 'active', "
            "'completed', 'rejected', 'cancelled', 'failed')",
            name="ck_promotions_status",
        ),
    )
    op.create_index("ix_promotions_shop_status_window", "promotions", ["shop_id", "status", "starts_at", "ends_at"])

    create_table(
        "promotion_items",
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("original_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("promotion_price", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["shop_id", "promotion_id"],
            ["promotions.shop_id", "promotions.id"],
            ondelete="CASCADE",
            name="fk_promotion_items_shop_promotion",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "sku_id"],
            ["skus.shop_id", "skus.id"],
            name="fk_promotion_items_shop_sku",
        ),
        sa.UniqueConstraint("promotion_id", "sku_id", name="uq_promotion_items_promotion_sku"),
        sa.CheckConstraint(
            "original_price >= 0 AND cost_price >= 0 AND promotion_price >= 0 "
            "AND discount_rate > 0 AND discount_rate < 1",
            name="ck_promotion_items_amounts",
        ),
    )
    op.create_index("ix_promotion_items_shop_sku", "promotion_items", ["shop_id", "sku_id"])

    create_table(
        "approvals",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("approval_no", sa.String(32), nullable=False),
        sa.Column("action_type", sa.String(40), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("resource_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("payload_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("requester_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approver_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_approvals_shop_organization",
        ),
        sa.UniqueConstraint("shop_id", "approval_no", name="uq_approvals_shop_no"),
        sa.UniqueConstraint(
            "resource_type",
            "resource_id",
            "resource_version",
            name="uq_approvals_resource_version",
        ),
        sa.CheckConstraint("resource_version >= 1", name="ck_approvals_version"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_approvals_status",
        ),
    )
    op.create_index("ix_approvals_shop_status_created", "approvals", ["shop_id", "status", "created_at"])

    create_table(
        "operation_requests",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(48), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), server_default="processing", nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=True),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_operation_requests_shop_organization",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "operation_type",
            "idempotency_key",
            name="uq_operation_requests_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed')",
            name="ck_operation_requests_status",
        ),
    )

    create_table(
        "audit_logs",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_audit_logs_shop_organization",
        ),
    )
    op.create_index(
        "ix_audit_logs_shop_resource",
        "audit_logs",
        ["shop_id", "resource_type", "resource_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_shop_resource", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("operation_requests")
    op.drop_index("ix_approvals_shop_status_created", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_promotion_items_shop_sku", table_name="promotion_items")
    op.drop_table("promotion_items")
    op.drop_index("ix_promotions_shop_status_window", table_name="promotions")
    op.drop_table("promotions")
