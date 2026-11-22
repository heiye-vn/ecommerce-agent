"""Create organization, user, shop and membership tables."""

import sqlalchemy as sa
from alembic import op


revision = "0001_organization_access"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("organizations", "users"):
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("status", sa.String(20), server_default="active", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    op.create_table(
        "shops",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "id", name="uq_shops_organization_id"),
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "admin", "operator", "approver", name="membership_role",
                native_enum=False, create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"], ["shops.organization_id", "shops.id"],
            name="fk_memberships_shop_organization",
        ),
        sa.UniqueConstraint(
            "user_id", "organization_id", "shop_id", "role",
            name="uq_memberships_scope_role", postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    for table in ("memberships", "shops", "users", "organizations"):
        op.drop_table(table)
