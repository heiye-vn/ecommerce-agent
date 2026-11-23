from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        Index("ix_agent_sessions_user_shop", "user_id", "shop_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(200))
    context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_agent_messages_role"),
        Index("ix_agent_messages_session_created", "session_id", "created_at"),
    )

    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text())
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("ix_agent_tasks_session_created", "session_id", "created_at"),
    )

    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"))
    task_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32))
    llm_provider: Mapped[str | None] = mapped_column(String(32))
    model_name: Mapped[str | None] = mapped_column(String(120))
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed')", name="ck_agent_tool_calls_status"),
        Index("ix_agent_tool_calls_task_id", "task_id"),
    )

    task_id: Mapped[int] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"))
    tool_name: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text())
    duration_ms: Mapped[int | None] = mapped_column(Integer)
