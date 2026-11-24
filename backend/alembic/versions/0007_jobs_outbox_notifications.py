"""Create jobs, outbox_events, and notifications tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_jobs_outbox_notifications"
down_revision = "0006_promotions_approvals_audit"
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
        "jobs",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("job_no", sa.String(32), nullable=False),
        sa.Column("job_type", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("resource_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_jobs_shop_organization",
        ),
        sa.UniqueConstraint("shop_id", "job_no", name="uq_jobs_shop_no"),
        sa.UniqueConstraint("organization_id", "job_type", "idempotency_key", name="uq_jobs_idempotency"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'retrying', 'failed', 'dead_lettered')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "job_type IN ('promotion_schedule', 'promotion_activate', 'promotion_complete')",
            name="ck_jobs_type",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1 AND resource_version >= 1",
            name="ck_jobs_counters",
        ),
    )
    op.create_index("ix_jobs_shop_status_updated", "jobs", ["shop_id", "status", "updated_at"])
    op.create_index("ix_jobs_resource", "jobs", ["shop_id", "resource_type", "resource_id"])

    create_table(
        "outbox_events",
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(40), nullable=False),
        sa.Column("aggregate_id", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_outbox_events_shop_organization",
        ),
        sa.UniqueConstraint("event_id", name="uq_outbox_events_event_id"),
        sa.CheckConstraint("status IN ('pending', 'published', 'failed')", name="ck_outbox_events_status"),
        sa.CheckConstraint("retry_count >= 0", name="ck_outbox_events_retry_count"),
    )
    op.create_index("ix_outbox_pending_available", "outbox_events", ["status", "available_at"])

    create_table(
        "notifications",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=True),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_notifications_shop_organization",
        ),
        sa.CheckConstraint("category IN ('approval', 'promotion', 'job')", name="ck_notifications_category"),
    )
    op.create_index(
        "ix_notifications_user_shop_read",
        "notifications",
        ["user_id", "shop_id", "is_read", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_shop_read", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_outbox_pending_available", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_jobs_resource", table_name="jobs")
    op.drop_index("ix_jobs_shop_status_updated", table_name="jobs")
    op.drop_table("jobs")
