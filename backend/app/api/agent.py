from dataclasses import asdict

from fastapi import APIRouter, Depends

from app.agent.graph import query_graph
from app.agent.llm_gateway import ModelUnavailable
from app.api.auth import AppConfig, Clock, OperatorShop, SessionFactory, reject_cross_origin
from app.core.errors import AppError
from app.schemas.agent import AgentAnswer, AgentQuery
from app.services import agent as agent_service

router = APIRouter(prefix="/api/shops/{shop_id}/agent", tags=["agent"])


@router.post("/query", dependencies=[Depends(reject_cross_origin)])
async def query_agent(
    payload: AgentQuery,
    shop: OperatorShop,
    session_factory: SessionFactory,
    settings: AppConfig,
    clock: Clock,
) -> AgentAnswer:
    # 1. 短只读会话校验历史 session 归属与跨店边界，释放连接后再进入模型推理
    async with session_factory() as read_session:
        loaded = await agent_service.load_session_context(
            read_session,
            user_id=shop.user_id,
            shop_id=shop.id,
            session_id=payload.session_id,
        )

    if loaded.cross_shop:
        return AgentAnswer(
            kind="unsupported",
            message="该会话属于另一家店铺，请切回原店铺继续操作，或在当前店铺新建会话。",
            session_id=loaded.session_id,
            tool_calls=0,
        )

    # 2. 无 DB 事务状态下运行 LangGraph 编排与 LLM 调用（内部工具按需申请短连接）
    business_day = clock.business_date(shop.timezone)
    try:
        outcome = await query_graph.ainvoke(
            {
                "message": payload.message,
                "prior_context": loaded.prior_promotion,
                "pending_plan": loaded.pending_plan,
            },
            context={
                "organization_id": shop.organization_id,
                "shop_id": shop.id,
                "shop_name": shop.name,
                "timezone_name": shop.timezone,
                "actor_id": shop.user_id,
                "session_id": loaded.session_id,
                "business_day": business_day,
                "session_factory": session_factory,
                "settings": settings,
            },
        )
    except ModelUnavailable:
        raise AppError(
            503,
            "MODEL_UNAVAILABLE",
            "模型服务暂时不可用，请稍后重试。",
            detail="Model temporarily unavailable",
        ) from None

    answer: AgentAnswer = outcome["answer"]
    tool_records = outcome.get("tool_records", [])
    task_type: str = outcome.get("task_type", answer.kind)

    # 3. 独立短事务落库会话、消息、任务与工具调用审计记录
    async with session_factory.begin() as write_session:
        session_id, task_id = await agent_service.record_turn(
            write_session,
            user_id=shop.user_id,
            shop_id=shop.id,
            session_id=loaded.session_id,
            user_message=payload.message,
            answer=answer,
            task_type=task_type,
            llm_provider=outcome.get("llm_provider"),
            model_name=outcome.get("model_name"),
            tool_calls=[asdict(record) for record in tool_records],
        )

    answer.session_id = session_id
    answer.task_id = task_id
    return answer
