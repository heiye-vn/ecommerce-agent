from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.agent import AgentMessage, AgentSession, AgentTask, AgentToolCall
from app.schemas.agent import (
    AgentAnswer,
    PendingPromotionPlan,
    PendingRefundPlan,
    PromotionContext,
    RefundContext,
)


@dataclass(frozen=True)
class LoadedSessionContext:
    session_id: int | None
    prior_promotion: PromotionContext | None
    pending_plan: PendingPromotionPlan | None = None
    prior_refund: RefundContext | None = None
    pending_refund_plan: PendingRefundPlan | None = None
    cross_shop: bool = False


async def load_session_context(
    session: AsyncSession,
    *,
    user_id: int,
    shop_id: int,
    session_id: int | None,
) -> LoadedSessionContext:
    if session_id is None:
        return LoadedSessionContext(session_id=None, prior_promotion=None)

    agent_session = await session.scalar(select(AgentSession).where(AgentSession.id == session_id))
    if agent_session is None:
        raise AppError(
            404,
            "AGENT_SESSION_NOT_FOUND",
            "未找到对应的会话，请新建会话后重试。",
            detail="Agent session not found",
        )
    if agent_session.user_id != user_id:
        raise AppError(
            403,
            "AGENT_SESSION_DENIED",
            "无权访问该会话。",
            detail="Agent session access denied",
        )
    if agent_session.shop_id != shop_id:
        return LoadedSessionContext(
            session_id=agent_session.id,
            prior_promotion=None,
            cross_shop=True,
        )

    prior_promotion: PromotionContext | None = None
    pending_plan: PendingPromotionPlan | None = None
    prior_refund: RefundContext | None = None
    pending_refund_plan: PendingRefundPlan | None = None
    if agent_session.context_json:
        if "promotion_context" in agent_session.context_json:
            prior_promotion = PromotionContext.model_validate(
                agent_session.context_json["promotion_context"]
            )
        if "pending_plan" in agent_session.context_json:
            pending_plan = PendingPromotionPlan.model_validate(
                agent_session.context_json["pending_plan"]
            )
        if "refund_context" in agent_session.context_json:
            prior_refund = RefundContext.model_validate(
                agent_session.context_json["refund_context"]
            )
        if "pending_refund_plan" in agent_session.context_json:
            pending_refund_plan = PendingRefundPlan.model_validate(
                agent_session.context_json["pending_refund_plan"]
            )
    return LoadedSessionContext(
        session_id=agent_session.id,
        prior_promotion=prior_promotion,
        pending_plan=pending_plan,
        prior_refund=prior_refund,
        pending_refund_plan=pending_refund_plan,
    )


async def record_turn(
    session: AsyncSession,
    *,
    user_id: int,
    shop_id: int,
    session_id: int | None,
    user_message: str,
    answer: AgentAnswer,
    task_type: str,
    llm_provider: str | None,
    model_name: str | None,
    tool_calls: list[dict[str, Any]],
) -> tuple[int, int]:
    agent_session: AgentSession | None = None
    if session_id is not None:
        agent_session = await session.scalar(
            select(AgentSession).where(
                AgentSession.id == session_id,
                AgentSession.user_id == user_id,
                AgentSession.shop_id == shop_id,
            )
        )
    if agent_session is None:
        title = user_message[:80]
        agent_session = AgentSession(user_id=user_id, shop_id=shop_id, title=title)
        session.add(agent_session)
        await session.flush()

    next_context: dict[str, Any] = {}
    if answer.promotion_context is not None:
        next_context["promotion_context"] = answer.promotion_context.model_dump(mode="json")
    if answer.pending_plan is not None:
        next_context["pending_plan"] = answer.pending_plan.model_dump(mode="json")
    if answer.refund_context is not None:
        next_context["refund_context"] = answer.refund_context.model_dump(mode="json")
    if answer.pending_refund_plan is not None:
        next_context["pending_refund_plan"] = answer.pending_refund_plan.model_dump(mode="json")
    agent_session.context_json = next_context or None

    session.add_all([
        AgentMessage(session_id=agent_session.id, role="user", content=user_message),
        AgentMessage(
            session_id=agent_session.id,
            role="assistant",
            content=answer.message,
            metadata_json={"kind": answer.kind, "tool_calls": answer.tool_calls},
        ),
    ])

    plan_payload: dict[str, Any] | None = None
    if answer.submission is not None:
        plan_payload = answer.submission.model_dump(mode="json")
    elif answer.refund_submission is not None:
        plan_payload = answer.refund_submission.model_dump(mode="json")
    elif answer.promotion is not None:
        plan_payload = answer.promotion.model_dump(mode="json")
    elif answer.refund is not None:
        plan_payload = answer.refund.model_dump(mode="json")
    elif answer.order_result is not None:
        plan_payload = answer.order_result.model_dump(mode="json")
    elif answer.sales is not None:
        plan_payload = answer.sales.model_dump(mode="json")

    task = AgentTask(
        session_id=agent_session.id,
        task_type=task_type,
        status=answer.kind,
        llm_provider=llm_provider,
        model_name=model_name,
        input_json={"message": user_message},
        plan_json=plan_payload,
    )
    session.add(task)
    await session.flush()

    for call in tool_calls:
        session.add(
            AgentToolCall(
                task_id=task.id,
                tool_name=call["tool_name"],
                request_json=call.get("request_json"),
                response_summary=call.get("response_summary"),
                status=call["status"],
                error=call.get("error"),
                duration_ms=call.get("duration_ms"),
            )
        )
    await session.flush()
    return agent_session.id, task.id
