import re
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.llm_gateway import classify as _gateway_classify
from app.agent.llm_gateway import classify_with_metadata
from app.agent.tools import (
    ToolCallRecord,
    preview_promotion_tool,
    preview_refund_tool,
    query_order_tool,
    query_sales_tool,
    submit_promotion_approval_tool,
    submit_refund_approval_tool,
)
from app.core.config import AppSettings
from app.schemas.agent import (
    ORDER_NO_PATTERN,
    SKU_PATTERN,
    AgentAnswer,
    PendingPromotionPlan,
    PendingRefundPlan,
    PromotionContext,
    QueryIntent,
    RefundContext,
)
from app.services.promotion_preview import (
    is_confirm_message,
    parse_terms,
    resolve_start_date_match,
)
from app.services.refund_preview import parse_refund_amount, parse_refund_reason

# 保留模块级引用，兼容测试通过 monkeypatch.setattr(graph, "classify", ...) 注入意图桩
classify = _gateway_classify


class QueryState(TypedDict, total=False):
    message: str
    prior_context: PromotionContext | None
    pending_plan: PendingPromotionPlan | None
    prior_refund_context: RefundContext | None
    pending_refund_plan: PendingRefundPlan | None
    intent: QueryIntent
    llm_provider: str | None
    model_name: str | None
    task_type: str
    tool_records: list[ToolCallRecord]
    answer: AgentAnswer


class QueryContext(TypedDict):
    organization_id: int
    shop_id: int
    shop_name: str
    timezone_name: str
    actor_id: int
    session_id: int | None
    business_day: date
    session_factory: async_sessionmaker[AsyncSession]
    settings: AppSettings | None


def route_entry(state: QueryState) -> str:
    pending_refund = state.get("pending_refund_plan")
    pending_promo = state.get("pending_plan")
    if (pending_refund is not None or pending_promo is not None) and is_confirm_message(state["message"]):
        return "confirm_active_plan"
    has_new_entity = bool(
        SKU_PATTERN.search(state["message"]) or ORDER_NO_PATTERN.search(state["message"])
    )
    if state.get("prior_refund_context") is not None and not has_new_entity:
        return "follow_up_refund"
    if state.get("prior_context") is not None and not has_new_entity:
        return "follow_up_promotion"
    return "recognize"


async def _submit_pending_plan(
    plan: PendingPromotionPlan,
    ctx: QueryContext,
) -> dict[str, object]:
    if plan.shop_id != ctx["shop_id"]:
        return {
            "task_type": "promotion_submit",
            "tool_records": [],
            "answer": AgentAnswer(
                kind="unsupported",
                message="该方案属于另一家店铺，请切回原店铺后再确认提交。",
                tool_calls=0,
            ),
        }
    session_prefix = f"agent-session-{ctx['session_id']}" if ctx["session_id"] is not None else "agent-plan"
    idempotency_key = f"{session_prefix}-plan-{plan.plan_key}"
    submission, error_msg, record = await submit_promotion_approval_tool(
        ctx["session_factory"],
        organization_id=ctx["organization_id"],
        shop_id=ctx["shop_id"],
        shop_name=ctx["shop_name"],
        timezone_name=ctx["timezone_name"],
        actor_id=ctx["actor_id"],
        business_day=ctx["business_day"],
        plan=plan,
        idempotency_key=idempotency_key,
    )
    if submission is None:
        return {
            "task_type": "promotion_submit",
            "tool_records": [record],
            "answer": AgentAnswer(
                kind="promotion_blocked",
                message=error_msg or "促销方案校验未通过，无法提交审批。",
                tool_calls=1,
                pending_plan=plan,
            ),
        }
    if submission.replayed:
        msg = (
            f"同一版本方案已提交审批，直接返回原审批单 {submission.approval_no}"
            f"（活动 {submission.promotion_no} · V{submission.version}），未重复建单。"
        )
    else:
        msg = (
            f"已提交人工审批，审批单号 {submission.approval_no}"
            f"（活动 {submission.promotion_no} · V{submission.version}），等待本店其他审批员处理。"
        )
    return {
        "task_type": "promotion_submit",
        "tool_records": [record],
        "answer": AgentAnswer(
            kind="promotion_submitted",
            message=msg,
            tool_calls=1,
            submission=submission,
            pending_plan=plan,
        ),
    }


async def _submit_pending_refund_plan(
    plan: PendingRefundPlan,
    ctx: QueryContext,
) -> dict[str, object]:
    if plan.shop_id != ctx["shop_id"]:
        return {
            "task_type": "refund_submit",
            "tool_records": [],
            "answer": AgentAnswer(
                kind="unsupported",
                message="该退款方案属于另一家店铺，请切回原店铺后再确认提交。",
                tool_calls=0,
            ),
        }
    session_prefix = f"agent-session-{ctx['session_id']}" if ctx["session_id"] is not None else "agent-refund"
    idempotency_key = f"{session_prefix}-refund-{plan.plan_key}"
    submission, error_msg, record = await submit_refund_approval_tool(
        ctx["session_factory"],
        organization_id=ctx["organization_id"],
        shop_id=ctx["shop_id"],
        shop_name=ctx["shop_name"],
        actor_id=ctx["actor_id"],
        business_day=ctx["business_day"],
        plan=plan,
        idempotency_key=idempotency_key,
    )
    if submission is None:
        return {
            "task_type": "refund_submit",
            "tool_records": [record],
            "answer": AgentAnswer(
                kind="refund_blocked",
                message=error_msg or "退款方案校验未通过，无法提交审批。",
                tool_calls=1,
                pending_refund_plan=plan,
            ),
        }
    if submission.replayed:
        msg = (
            f"同一退款方案已提交审批，直接返回原审批单 {submission.approval_no}"
            f"（退款单 {submission.refund_no}），未重复建单。"
        )
    else:
        msg = (
            f"已提交退款人工审批，审批单号 {submission.approval_no}"
            f"（退款单 {submission.refund_no} · ¥{submission.amount:.2f}），等待本店其他审批员处理。"
        )
    return {
        "task_type": "refund_submit",
        "tool_records": [record],
        "answer": AgentAnswer(
            kind="refund_submitted",
            message=msg,
            tool_calls=1,
            refund_submission=submission,
            pending_refund_plan=plan,
        ),
    }


async def confirm_active_plan(state: QueryState, runtime: Runtime[QueryContext]) -> dict[str, object]:
    pending_refund = state.get("pending_refund_plan")
    if pending_refund is not None:
        return await _submit_pending_refund_plan(pending_refund, runtime.context)
    plan = state["pending_plan"]
    assert plan is not None
    return await _submit_pending_plan(plan, runtime.context)


async def follow_up_promotion(state: QueryState, runtime: Runtime[QueryContext]) -> dict[str, object]:
    prior = state["prior_context"]
    assert prior is not None
    ctx = runtime.context
    match, ambiguous = resolve_start_date_match(state["message"], is_follow_up=True)
    if ambiguous:
        return {
            "task_type": "promotion_preview",
            "tool_records": [],
            "answer": AgentAnswer(
                kind="promotion_clarify",
                message="请指定一个活动开始日期，例如 11 月 22 日开始。活动从店铺当地日期的 00:00 开始。",
                promotion_context=prior,
            ),
        }
    answer, record = await preview_promotion_tool(
        ctx["session_factory"],
        terms=prior,
        business_day=ctx["business_day"],
        start_match=match,
        timezone_name=ctx["timezone_name"],
    )
    answer.tool_calls = 1
    return {
        "task_type": "promotion_preview",
        "tool_records": [record],
        "answer": answer,
    }


async def follow_up_refund(state: QueryState, runtime: Runtime[QueryContext]) -> dict[str, object]:
    prior = state["prior_refund_context"]
    assert prior is not None
    ctx = runtime.context
    amount = parse_refund_amount(
        state["message"],
        amount_hint=prior.amount,
        allow_bare_number=(prior.amount is None),
    )
    reason = parse_refund_reason(
        state["message"],
        reason_hint=prior.reason,
        allow_free_text=(prior.reason is None and amount is not None),
    )
    answer, record = await preview_refund_tool(
        ctx["session_factory"],
        shop_id=ctx["shop_id"],
        order_no=prior.order_no,
        amount=amount,
        reason=reason,
    )
    answer.tool_calls = 1
    return {
        "task_type": "refund_preview",
        "tool_records": [record],
        "answer": answer,
    }


async def recognize(state: QueryState, runtime: Runtime[QueryContext]) -> dict[str, object]:
    if classify is not _gateway_classify:
        intent = await classify(state["message"])
        return {"intent": intent, "llm_provider": "stub", "model_name": "stub"}
    result = await classify_with_metadata(state["message"], settings=runtime.context["settings"])
    return {
        "intent": result.intent,
        "llm_provider": result.llm_provider,
        "model_name": result.model_name,
    }


async def _handle_confirm_plan(state: QueryState, ctx: QueryContext) -> dict[str, object]:
    pending_refund = state.get("pending_refund_plan")
    if pending_refund is not None:
        return await _submit_pending_refund_plan(pending_refund, ctx)
    pending_plan = state.get("pending_plan")
    if pending_plan is not None:
        return await _submit_pending_plan(pending_plan, ctx)
    return {
        "task_type": "plan_submit",
        "tool_records": [],
        "answer": AgentAnswer(
            kind="clarify",
            message="当前会话还没有待确认的完整方案，请先生成促销或退款预览并补齐必要参数。",
        ),
    }


async def _handle_order_query(
    message: str,
    intent: QueryIntent,
    ctx: QueryContext,
) -> dict[str, object]:
    order_nos = {m.group().upper() for m in ORDER_NO_PATTERN.finditer(message)}
    if len(order_nos) > 1:
        return {
            "task_type": "order_query",
            "tool_records": [],
            "answer": AgentAnswer(
                kind="clarify",
                message="一次仅支持查询单笔订单，请指定一个明确的订单编号，例如：查看订单 ORD-20261111-01。",
            ),
        }
    target_order_no: str | None = None
    if len(order_nos) == 1:
        mentioned = next(iter(order_nos))
        if intent.order_no and intent.order_no.upper() != mentioned:
            return {
                "task_type": "order_query",
                "tool_records": [],
                "answer": AgentAnswer(
                    kind="clarify",
                    message="请确认需要查询的单个订单编号，例如：查看订单 ORD-20261111-01。",
                ),
            }
        target_order_no = mentioned
    exception_only = intent.exception_only or ("异常" in message or "复核" in message)
    answer, record = await query_order_tool(
        ctx["session_factory"],
        shop_id=ctx["shop_id"],
        order_no=target_order_no,
        exception_only=exception_only,
    )
    answer.tool_calls = 1
    return {
        "task_type": "order_query",
        "tool_records": [record],
        "answer": answer,
    }


async def _handle_refund(
    message: str,
    intent: QueryIntent,
    ctx: QueryContext,
) -> dict[str, object]:
    order_nos = {m.group().upper() for m in ORDER_NO_PATTERN.finditer(message)}
    code = intent.order_no
    if not code or len(order_nos) != 1 or code.upper() not in order_nos:
        return {
            "task_type": "refund_preview",
            "tool_records": [],
            "answer": AgentAnswer(
                kind="clarify",
                message="请提供一条明确的订单编号发起退款，例如：给 ORD-20261111-01 退 50 元，原因部分商品售后。",
            ),
        }
    order_no = code.upper()
    amount = parse_refund_amount(message, amount_hint=intent.refund_amount)
    reason = parse_refund_reason(message, reason_hint=intent.refund_reason)
    answer, record = await preview_refund_tool(
        ctx["session_factory"],
        shop_id=ctx["shop_id"],
        order_no=order_no,
        amount=amount,
        reason=reason,
    )
    answer.tool_calls = 1
    return {
        "task_type": "refund_preview",
        "tool_records": [record],
        "answer": answer,
    }


async def respond(state: QueryState, runtime: Runtime[QueryContext]) -> dict[str, object]:
    intent = state["intent"]
    ctx = runtime.context
    if intent.kind == "confirm_plan":
        return await _handle_confirm_plan(state, ctx)

    if intent.kind == "order_query":
        return await _handle_order_query(state["message"], intent, ctx)

    if intent.kind == "refund":
        return await _handle_refund(state["message"], intent, ctx)

    if intent.kind == "unsupported":
        return {
            "task_type": "unsupported",
            "tool_records": [],
            "answer": AgentAnswer(
                kind="unsupported",
                message=(
                    "当前支持本店单 SKU 近 7 天销量查询、促销预览与送审、订单/异常订单诊断，以及退款预览与送审"
                    "（审批裁决需由审批员完成，库存操作请在商品运营页执行）。"
                ),
            ),
        }

    codes = {match.group().upper() for match in SKU_PATTERN.finditer(state["message"])}
    code = intent.sku_code
    if not code or len(codes) != 1 or code.upper() not in codes or not re.fullmatch(SKU_PATTERN, code):
        return {
            "task_type": intent.kind,
            "tool_records": [],
            "answer": AgentAnswer(
                kind="clarify",
                message="请提供一个明确的 SKU 编码，例如：查一下 SKU-001 最近 7 天销量。",
            ),
        }
    sku_code = code.upper()

    if intent.kind == "promotion":
        terms = parse_terms(
            ctx["shop_id"],
            sku_code,
            state["message"],
            discount_hint=intent.discount,
            duration_hint=intent.duration_days,
            ambiguous_hint=intent.ambiguous_terms,
        )
        if terms is None:
            return {
                "task_type": "promotion_preview",
                "tool_records": [],
                "answer": AgentAnswer(
                    kind="clarify",
                    message=(
                        "请提供单一折扣和整数天数。库存使用沙箱固定门槛 100 件，"
                        "例如：给 SKU-001 做 8 折、持续 3 天的促销。"
                    ),
                ),
            }
        start_match, ambiguous_date = resolve_start_date_match(
            state["message"],
            is_follow_up=False,
            start_date_hint=intent.start_date,
        )
        if ambiguous_date:
            return {
                "task_type": "promotion_preview",
                "tool_records": [],
                "answer": AgentAnswer(
                    kind="clarify",
                    message="请指定一个活动开始日期，例如 11 月 22 日开始。活动从店铺当地日期的 00:00 开始。",
                ),
            }
        answer, record = await preview_promotion_tool(
            ctx["session_factory"],
            terms=terms,
            business_day=ctx["business_day"],
            start_match=start_match,
            timezone_name=ctx["timezone_name"],
        )
        answer.tool_calls = 1
        return {
            "task_type": "promotion_preview",
            "tool_records": [record],
            "answer": answer,
        }

    sales, record = await query_sales_tool(
        ctx["session_factory"],
        shop_id=ctx["shop_id"],
        sku_code=sku_code,
        as_of=ctx["business_day"],
        timezone_name=ctx["timezone_name"],
    )
    if sales is None:
        return {
            "task_type": "sales_query",
            "tool_records": [record],
            "answer": AgentAnswer(
                kind="not_found",
                message=f"当前店铺没有找到 {sku_code}。请核对编码后重试。",
                tool_calls=1,
            ),
        }
    return {
        "task_type": "sales_query",
        "tool_records": [record],
        "answer": AgentAnswer(
            kind="sales",
            message=f"{sales.sku_code} 在当前店铺最近 7 个完整自然日共售出 {sales.units_sold} 件。",
            sales=sales,
            tool_calls=1,
        ),
    }


builder = StateGraph(QueryState, context_schema=QueryContext)
builder.add_node("confirm_active_plan", confirm_active_plan)
builder.add_node("follow_up_promotion", follow_up_promotion)
builder.add_node("follow_up_refund", follow_up_refund)
builder.add_node("recognize", recognize)
builder.add_node("respond", respond)
builder.add_conditional_edges(
    START,
    route_entry,
    {
        "confirm_active_plan": "confirm_active_plan",
        "follow_up_promotion": "follow_up_promotion",
        "follow_up_refund": "follow_up_refund",
        "recognize": "recognize",
    },
)
builder.add_edge("confirm_active_plan", END)
builder.add_edge("follow_up_promotion", END)
builder.add_edge("follow_up_refund", END)
builder.add_edge("recognize", "respond")
builder.add_edge("respond", END)
query_graph = builder.compile()
