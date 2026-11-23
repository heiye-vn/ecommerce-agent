import logging
import re
from dataclasses import dataclass
from datetime import date
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import DEFAULT_SHOP_TIMEZONE
from app.core.errors import AppError
from app.schemas.agent import AgentAnswer, PendingPromotionPlan, PromotionContext
from app.schemas.orders import SkuSales
from app.schemas.promotions import PromotionCreateRequest, PromotionSubmitResponse
from app.services.orders import sku_sales
from app.services.promotion_preview import preview_promotion
from app.services.promotions import submit_promotion

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
