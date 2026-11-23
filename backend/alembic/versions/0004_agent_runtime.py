"""Create Agent session, message, task, and tool call tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_agent_runtime"
down_revision = "0003_catalog"
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
        "agent_sessions",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_sessions_user_shop", "agent_sessions", ["user_id", "shop_id"])

    create_table(
        "agent_messages",
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_agent_messages_role"),
    )
    op.create_index("ix_agent_messages_session_created", "agent_messages", ["session_id", "created_at"])

    create_table(
        "agent_tasks",
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("llm_provider", sa.String(32), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_agent_tasks_session_created", "agent_tasks", ["session_id", "created_at"])

    create_table(
        "agent_tool_calls",
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_agent_tool_calls_status"),
    )
    op.create_index("ix_agent_tool_calls_task_id", "agent_tool_calls", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_tool_calls_task_id", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_tasks_session_created", table_name="agent_tasks")
    op.drop_table("agent_tasks")
    op.drop_index("ix_agent_messages_session_created", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_sessions_user_shop", table_name="agent_sessions")
    op.drop_table("agent_sessions")
