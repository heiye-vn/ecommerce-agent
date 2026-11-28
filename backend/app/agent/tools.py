import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import DEFAULT_SHOP_TIMEZONE
from app.core.config import AppSettings
from app.core.errors import AppError
from app.models.org import Role
from app.schemas.agent import (
    AgentAnswer,
    KnowledgeQaResult,
    PendingPriceChangePlan,
    PendingPromotionPlan,
    PendingRefundPlan,
    PromotionContext,
)
from app.schemas.orders import SkuSales
from app.schemas.price_changes import PriceChangeCreateRequest, PriceChangeSubmitResponse
from app.schemas.promotions import PromotionCreateRequest, PromotionSubmitResponse
from app.schemas.refunds import RefundCreateRequest, RefundSubmitResponse
from app.services.knowledge import build_embedding_client, build_rerank_client, search_knowledge_base
from app.services.orders import sku_sales
from app.services.price_change_preview import preview_price_change
from app.services.price_changes import submit_price_change
from app.services.promotion_preview import preview_promotion
from app.services.promotions import submit_promotion
from app.services.refund_preview import preview_refund, query_orders_for_agent
from app.services.refunds import submit_refund

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCallRecord:
    tool_name: str
    request_json: dict[str, Any]
    response_summary: dict[str, Any] | None
    status: str
    error: str | None = None
    duration_ms: int = 0


async def query_sales(
    session: AsyncSession,
    shop_id: int,
    sku_code: str,
    as_of: date,
    timezone_name: str | None = None,
) -> SkuSales | None:
    # 仅保留参数透传以兼容单测桩函数签名 (session, shop_id, sku_code, as_of)
    result = (
        await sku_sales(session, shop_id, sku_code, as_of)
        if timezone_name is None
        else await sku_sales(session, shop_id, sku_code, as_of, timezone_name=timezone_name)
    )
    logger.info(
        "Read-only Agent tool completed",
        extra={"tool_name": "query_sales", "shop_id": shop_id, "found": result is not None},
    )
    return result


async def query_sales_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    shop_id: int,
    sku_code: str,
    as_of: date,
    timezone_name: str = DEFAULT_SHOP_TIMEZONE,
) -> tuple[SkuSales | None, ToolCallRecord]:
    started = perf_counter()
    request_payload = {
        "shop_id": shop_id,
        "sku_code": sku_code,
        "as_of": as_of.isoformat(),
        "timezone": timezone_name,
    }
    try:
        async with session_factory() as session:
            result = await query_sales(
                session,
                shop_id,
                sku_code,
                as_of,
                timezone_name=None if timezone_name == DEFAULT_SHOP_TIMEZONE else timezone_name,
            )
        elapsed_ms = int((perf_counter() - started) * 1000)
        summary = (
            {
                "found": True,
                "sku_code": result.sku_code,
                "units_sold": result.units_sold,
                "paid_orders": result.paid_orders,
            }
            if result is not None
            else {"found": False, "sku_code": sku_code}
        )
        return result, ToolCallRecord(
            tool_name="query_sales",
            request_json=request_payload,
            response_summary=summary,
            status="succeeded",
            duration_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.warning("Agent tool query_sales failed: %s", type(exc).__name__)
        raise


async def preview_promotion_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    terms: PromotionContext,
    business_day: date,
    start_match: re.Match[str] | None,
    timezone_name: str = DEFAULT_SHOP_TIMEZONE,
) -> tuple[AgentAnswer, ToolCallRecord]:
    started = perf_counter()
    request_payload = {
        "shop_id": terms.shop_id,
        "sku_code": terms.sku_code,
        "discount": str(terms.discount),
        "duration_days": terms.duration_days,
        "business_day": business_day.isoformat(),
        "has_start_date": start_match is not None,
        "timezone": timezone_name,
    }
    try:
        async with session_factory() as session:
            answer = await preview_promotion(
                session,
                terms,
                business_day,
                start_match,
                timezone_name=timezone_name,
            )
        elapsed_ms = int((perf_counter() - started) * 1000)
        summary: dict[str, Any] = {"kind": answer.kind}
        if answer.promotion is not None:
            summary.update(
                {
                    "sku_code": answer.promotion.sku_code,
                    "promotion_price": str(answer.promotion.promotion_price),
                    "available_qty": answer.promotion.available_qty,
                    "below_cost": answer.promotion.below_cost,
                    "conflict_check": answer.promotion.conflict_check,
                }
            )
        if answer.sales is not None:
            summary["units_sold"] = answer.sales.units_sold
        logger.info(
            "Read-only Agent tool completed",
            extra={"tool_name": "preview_promotion", "shop_id": terms.shop_id, "kind": answer.kind},
        )
        return answer, ToolCallRecord(
            tool_name="preview_promotion",
            request_json=request_payload,
            response_summary=summary,
            status="succeeded",
            duration_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.warning("Agent tool preview_promotion failed: %s", type(exc).__name__)
        raise


async def submit_promotion_approval_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: int,
    shop_id: int,
    shop_name: str,
    timezone_name: str,
    actor_id: int,
    business_day: date,
    plan: PendingPromotionPlan,
    idempotency_key: str,
) -> tuple[PromotionSubmitResponse | None, str | None, ToolCallRecord]:
    """
    受控提交审批工具：只允许创建促销草案并进入人工审批，绝不暴露审批通过能力。
    返回 (提交结果, 业务失败原因, 工具调用记录)。
    """
    started = perf_counter()
    request_payload = {
        "shop_id": shop_id,
        "sku_code": plan.sku_code,
        "discount": str(plan.discount),
        "start_date": plan.start_date.isoformat(),
        "duration_days": plan.duration_days,
        "idempotency_key": idempotency_key,
    }
    try:
        async with session_factory.begin() as session:
            result = await submit_promotion(
                session,
                organization_id=organization_id,
                shop_id=shop_id,
                shop_name=shop_name,
                timezone_name=timezone_name,
                actor_id=actor_id,
                business_day=business_day,
                payload=PromotionCreateRequest(
                    name=plan.name,
                    sku_code=plan.sku_code,
                    discount=plan.discount,
                    start_date=plan.start_date,
                    duration_days=plan.duration_days,
                ),
                idempotency_key=idempotency_key,
            )
        elapsed_ms = int((perf_counter() - started) * 1000)
        logger.info(
            "Controlled Agent approval submission tool completed",
            extra={
                "tool_name": "submit_promotion_approval",
                "shop_id": shop_id,
                "approval_no": result.approval_no,
                "replayed": result.replayed,
            },
        )
        return (
            result,
            None,
            ToolCallRecord(
                tool_name="submit_promotion_approval",
                request_json=request_payload,
                response_summary={
                    "promotion_no": result.promotion_no,
                    "approval_no": result.approval_no,
                    "status": result.status,
                    "replayed": result.replayed,
                },
                status="succeeded",
                duration_ms=elapsed_ms,
            ),
        )
    except AppError as exc:
        elapsed_ms = int((perf_counter() - started) * 1000)
        return (
            None,
            exc.message,
            ToolCallRecord(
                tool_name="submit_promotion_approval",
                request_json=request_payload,
                response_summary={"code": exc.code},
                status="failed",
                error=exc.message,
                duration_ms=elapsed_ms,
            ),
        )


async def query_order_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    shop_id: int,
    order_no: str | None = None,
    exception_only: bool = False,
) -> tuple[AgentAnswer, ToolCallRecord]:
    started = perf_counter()
    request_payload = {
        "shop_id": shop_id,
        "order_no": order_no,
        "exception_only": exception_only,
    }
    try:
        async with session_factory() as session:
            answer = await query_orders_for_agent(
                session,
                shop_id=shop_id,
                order_no=order_no,
                exception_only=exception_only,
            )
        elapsed_ms = int((perf_counter() - started) * 1000)
        summary: dict[str, Any] = {
            "kind": answer.kind,
            "mode": answer.order_result.mode if answer.order_result else None,
            "count": len(answer.order_result.items) if answer.order_result else 0,
        }
        return answer, ToolCallRecord(
            tool_name="query_order",
            request_json=request_payload,
            response_summary=summary,
            status="succeeded",
            duration_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.warning("Agent tool query_order failed: %s", type(exc).__name__)
        raise


async def preview_refund_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    shop_id: int,
    order_no: str,
    amount: Decimal | None,
    reason: str | None,
) -> tuple[AgentAnswer, ToolCallRecord]:
    started = perf_counter()
    request_payload = {
        "shop_id": shop_id,
        "order_no": order_no,
        "amount": str(amount) if amount is not None else None,
        "has_reason": bool(reason),
    }
    try:
        async with session_factory() as session:
            answer = await preview_refund(
                session,
                shop_id=shop_id,
                order_no=order_no,
                amount=amount,
                reason=reason,
            )
        elapsed_ms = int((perf_counter() - started) * 1000)
        summary: dict[str, Any] = {"kind": answer.kind, "order_no": order_no}
        if answer.refund is not None:
            summary.update(
                {
                    "eligible": answer.refund.eligible,
                    "refundable_amount": str(answer.refund.refundable_amount),
                    "proposed_amount": str(answer.refund.proposed_amount),
                }
            )
        return answer, ToolCallRecord(
            tool_name="preview_refund",
            request_json=request_payload,
            response_summary=summary,
            status="succeeded",
            duration_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.warning("Agent tool preview_refund failed: %s", type(exc).__name__)
        raise


async def submit_refund_approval_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: int,
    shop_id: int,
    shop_name: str,
    actor_id: int,
    business_day: date,
    plan: PendingRefundPlan,
    idempotency_key: str,
) -> tuple[RefundSubmitResponse | None, str | None, ToolCallRecord]:
    """
    受控提交退款审批工具：只允许创建待审批退款单与审批单，绝不暴露审批通过或直接退款能力。
    """
    started = perf_counter()
    request_payload = {
        "shop_id": shop_id,
        "order_id": plan.order_id,
        "order_no": plan.order_no,
        "amount": str(plan.amount),
        "idempotency_key": idempotency_key,
    }
    try:
        async with session_factory.begin() as session:
            result = await submit_refund(
                session,
                organization_id=organization_id,
                shop_id=shop_id,
                shop_name=shop_name,
                actor_id=actor_id,
                business_day=business_day,
                payload=RefundCreateRequest(
                    order_id=plan.order_id,
                    amount=plan.amount,
                    reason=plan.reason,
                ),
                idempotency_key=idempotency_key,
            )
        elapsed_ms = int((perf_counter() - started) * 1000)
        return (
            result,
            None,
            ToolCallRecord(
                tool_name="submit_refund_approval",
                request_json=request_payload,
                response_summary={
                    "refund_no": result.refund_no,
                    "approval_no": result.approval_no,
                    "status": result.status,
                    "replayed": result.replayed,
                },
                status="succeeded",
                duration_ms=elapsed_ms,
            ),
        )
    except AppError as exc:
        elapsed_ms = int((perf_counter() - started) * 1000)
        return (
            None,
            exc.message,
            ToolCallRecord(
                tool_name="submit_refund_approval",
                request_json=request_payload,
                response_summary={"code": exc.code},
                status="failed",
                error=exc.message,
                duration_ms=elapsed_ms,
            ),
        )


async def preview_price_change_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    shop_id: int,
    sku_code: str,
    target_price: Decimal | None,
    reason: str | None,
) -> tuple[AgentAnswer, ToolCallRecord]:
    started = perf_counter()
    request_payload = {
        "shop_id": shop_id,
        "sku_code": sku_code,
        "target_price": str(target_price) if target_price is not None else None,
        "has_reason": bool(reason),
    }
    try:
        async with session_factory() as session:
            answer = await preview_price_change(
                session,
                shop_id=shop_id,
                sku_code=sku_code,
                target_price=target_price,
                reason=reason,
            )
        elapsed_ms = int((perf_counter() - started) * 1000)
        summary: dict[str, Any] = {"kind": answer.kind, "sku_code": sku_code}
        if answer.price_change is not None:
            summary.update(
                {
                    "eligible": answer.price_change.eligible,
                    "current_price": str(answer.price_change.current_price),
                    "proposed_price": str(answer.price_change.proposed_price),
                }
            )
        return answer, ToolCallRecord(
            tool_name="preview_price_change",
            request_json=request_payload,
            response_summary=summary,
            status="succeeded",
            duration_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.warning("Agent tool preview_price_change failed: %s", type(exc).__name__)
        raise


async def submit_price_change_approval_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: int,
    shop_id: int,
    shop_name: str,
    actor_id: int,
    business_day: date,
    plan: PendingPriceChangePlan,
    idempotency_key: str,
) -> tuple[PriceChangeSubmitResponse | None, str | None, ToolCallRecord]:
    """受控提交调价审批工具：只允许创建待审批调价申请与审批单，绝不直接改价或审批通过。"""
    started = perf_counter()
    request_payload = {
        "shop_id": shop_id,
        "sku_code": plan.sku_code,
        "new_price": str(plan.new_price),
        "idempotency_key": idempotency_key,
    }
    try:
        async with session_factory.begin() as session:
            result = await submit_price_change(
                session,
                organization_id=organization_id,
                shop_id=shop_id,
                shop_name=shop_name,
                actor_id=actor_id,
                business_day=business_day,
                payload=PriceChangeCreateRequest(
                    sku_code=plan.sku_code,
                    new_price=plan.new_price,
                    reason=plan.reason,
                ),
                idempotency_key=idempotency_key,
            )
        elapsed_ms = int((perf_counter() - started) * 1000)
        return (
            result,
            None,
            ToolCallRecord(
                tool_name="submit_price_change_approval",
                request_json=request_payload,
                response_summary={
                    "request_no": result.request_no,
                    "approval_no": result.approval_no,
                    "status": result.status,
                    "replayed": result.replayed,
                },
                status="succeeded",
                duration_ms=elapsed_ms,
            ),
        )
    except AppError as exc:
        elapsed_ms = int((perf_counter() - started) * 1000)
        return (
            None,
            exc.message,
            ToolCallRecord(
                tool_name="submit_price_change_approval",
                request_json=request_payload,
                response_summary={"code": exc.code},
                status="failed",
                error=exc.message,
                duration_ms=elapsed_ms,
            ),
        )


async def search_knowledge_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: int,
    shop_id: int,
    actor_roles: list[Role],
    query: str,
    settings: AppSettings | None = None,
    top_n: int = 4,
) -> tuple[KnowledgeQaResult, ToolCallRecord]:
    """只读混合检索运营/售后规则文档，严禁写入业务或审批数据。"""
    started = perf_counter()
    resolved_settings = settings if settings is not None else AppSettings.load()
    embedding_client = (
        build_embedding_client(resolved_settings) if resolved_settings.dashscope_api_key else None
    )
    rerank_client = (
        build_rerank_client(resolved_settings) if resolved_settings.dashscope_api_key else None
    )
    request_payload = {
        "shop_id": shop_id,
        "query": query[:200],
        "top_n": top_n,
    }
    try:
        async with session_factory() as session:
            search_res = await search_knowledge_base(
                session,
                organization_id=organization_id,
                shop_id=shop_id,
                actor_roles=actor_roles,
                query=query,
                embedding_client=embedding_client,
                rerank_client=rerank_client,
                embedding_model=resolved_settings.dashscope_embedding_model,
                embedding_dim=resolved_settings.dashscope_embedding_dimensions,
                top_n=top_n,
            )
        elapsed_ms = int((perf_counter() - started) * 1000)
        qa_result = KnowledgeQaResult(
            citations=search_res.items,
            vector_candidates=search_res.vector_candidates,
            lexical_candidates=search_res.lexical_candidates,
            vector_degraded=search_res.vector_degraded,
            rerank_degraded=search_res.rerank_degraded,
            embedding_model=search_res.embedding_model,
            rerank_model=search_res.rerank_model,
        )
        return qa_result, ToolCallRecord(
            tool_name="search_knowledge",
            request_json=request_payload,
            response_summary={
                "hit_count": len(search_res.items),
                "doc_nos": [item.doc_no for item in search_res.items],
                "vector_candidates": search_res.vector_candidates,
                "lexical_candidates": search_res.lexical_candidates,
                "rerank_degraded": search_res.rerank_degraded,
            },
            status="succeeded",
            duration_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.warning("Agent tool search_knowledge failed: %s", type(exc).__name__)
        raise
