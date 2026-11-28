import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, Refund
from app.schemas.agent import (
    AgentAnswer,
    OrderDiagnosticItem,
    OrderQueryResult,
    PendingRefundPlan,
    RefundContext,
    RefundPreview,
)
from app.services.orders import compute_order_refund_eligibility, get_order, list_orders

AMBIGUOUS_AMOUNT = re.compile(r"[0-9.]+\s*(?:到|至|或|[~～–—-])\s*[0-9.]+")
AMOUNT_WITH_UNIT = re.compile(
    r"(?:(?:退|退款|金额|¥|￥)\s*([0-9]+(?:\.[0-9]{1,2})?)|([0-9]+(?:\.[0-9]{1,2})?)\s*(?:元|块))"
)
PURE_AMOUNT = re.compile(r"^\s*¥?\s*([0-9]+(?:\.[0-9]{1,2})?)\s*元?\s*$")
REASON_EXTRACT = re.compile(r"(?:原因(?:是|为)?[:：]?|因为|理由(?:是|为)?[:：]?)\s*([^，。；\n]+)")


def build_refund_plan_key(shop_id: int, order_id: int, amount: Decimal, reason: str) -> str:
    raw = f"{shop_id}:{order_id}:{amount.quantize(Decimal('0.01'))}:{reason.strip()}"
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def parse_refund_amount(
    message: str,
    *,
    amount_hint: Decimal | None = None,
    allow_bare_number: bool = False,
) -> Decimal | None:
    if AMBIGUOUS_AMOUNT.search(message):
        return None
    matches = AMOUNT_WITH_UNIT.findall(message)
    if len(matches) == 1:
        raw = matches[0][0] or matches[0][1]
        try:
            value = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            return None
        return value if value > Decimal("0.00") else None
    if len(matches) > 1:
        return None

    if allow_bare_number:
        pure = PURE_AMOUNT.fullmatch(message)
        if pure:
            value = Decimal(pure.group(1)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return value if value > Decimal("0.00") else None

    if amount_hint is not None and amount_hint > Decimal("0.00"):
        return amount_hint.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return None


def parse_refund_reason(
    message: str,
    *,
    reason_hint: str | None = None,
    allow_free_text: bool = False,
) -> str | None:
    match = REASON_EXTRACT.search(message)
    if match:
        cleaned = match.group(1).strip(" ，。；;！!")
        if len(cleaned) >= 2:
            return cleaned[:500]
    if reason_hint:
        cleaned_hint = reason_hint.strip()
        if len(cleaned_hint) >= 2:
            return cleaned_hint[:500]
    if allow_free_text:
        # 仅在专门追问原因的轮次中，把整句非纯数字描述作为退款原因
        stripped = message.strip(" ，。；;！!")
        if stripped and not PURE_AMOUNT.fullmatch(stripped) and len(stripped) >= 2:
            return stripped[:500]
    return None


async def query_orders_for_agent(
    session: AsyncSession,
    *,
    shop_id: int,
    order_no: str | None = None,
    exception_only: bool = False,
) -> AgentAnswer:
    summaries = await list_orders(session, shop_id)
    if order_no:
        target = next(
            (item for item in summaries if item.order_no.upper() == order_no.upper()),
            None,
        )
        if target is None:
            return AgentAnswer(
                kind="not_found",
                message=f"当前店铺没有找到订单 {order_no.upper()}，请核对订单号后重试。",
            )
        detail = await get_order(session, shop_id, target.id)
        assert detail is not None
        diag = OrderDiagnosticItem(
            order_id=detail.id,
            order_no=detail.order_no,
            status=detail.status,
            fulfillment_status=detail.fulfillment_status,
            exception_type=detail.exception_type,
            exception_note=detail.exception_note,
            paid_amount=detail.paid_amount,
            refunded_amount=detail.refunded_amount,
            refundable_amount=detail.refundable_amount,
            can_refund=detail.can_refund,
            refund_block_reason=detail.refund_block_reason,
            sku_codes=detail.sku_codes,
        )
        note_part = f"；异常提示：{detail.exception_note}" if detail.exception_note else ""
        refund_part = (
            f"可退余额 ¥{detail.refundable_amount:.2f}"
            if detail.can_refund
            else (detail.refund_block_reason or "当前不可申请退款")
        )
        return AgentAnswer(
            kind="order_info",
            message=(
                f"订单 {detail.order_no} 状态为 {detail.status}（履约：{detail.fulfillment_status}），"
                f"实付 ¥{detail.paid_amount:.2f}，已成功退款 ¥{detail.refunded_amount:.2f}，{refund_part}{note_part}。"
            ),
            order_result=OrderQueryResult(mode="detail", items=[diag]),
        )

    candidates = (
        [item for item in summaries if item.exception_type is not None]
        if exception_only
        else summaries[:5]
    )
    if not candidates:
        return AgentAnswer(
            kind="order_info",
            message="当前店铺暂未发现待处理的异常订单。",
            order_result=OrderQueryResult(mode="exception_list", items=[]),
        )

    items: list[OrderDiagnosticItem] = []
    for item in candidates[:5]:
        refundable = max(Decimal("0.00"), item.paid_amount - item.refunded_amount)
        can_refund = (
            item.status in ("paid", "partially_refunded")
            and item.in_flight_refund_amount <= Decimal("0.00")
            and refundable > Decimal("0.00")
        )
        items.append(
            OrderDiagnosticItem(
                order_id=item.id,
                order_no=item.order_no,
                status=item.status,
                fulfillment_status=item.fulfillment_status,
                exception_type=item.exception_type,
                exception_note=item.exception_note,
                paid_amount=item.paid_amount,
                refunded_amount=item.refunded_amount,
                refundable_amount=refundable,
                can_refund=can_refund,
                refund_block_reason=None if can_refund else "请进入订单详情核对最新退款资格。",
                sku_codes=item.sku_codes,
            )
        )
    header = "以下为当前店铺需要复核的异常订单及只读处理建议（未修改任何库存或订单状态）："
    return AgentAnswer(
        kind="order_info",
        message=f"{header}共 {len(items)} 笔。",
        order_result=OrderQueryResult(mode="exception_list", items=items),
    )


async def preview_refund(
    session: AsyncSession,
    *,
    shop_id: int,
    order_no: str,
    amount: Decimal | None,
    reason: str | None,
) -> AgentAnswer:
    order = await session.scalar(
        select(Order).where(Order.shop_id == shop_id, Order.order_no == order_no.upper())
    )
    if order is None:
        return AgentAnswer(
            kind="not_found",
            message=f"当前店铺没有找到订单 {order_no.upper()}，无法核验退款资格。",
        )

    refunds = list(
        (
            await session.scalars(
                select(Refund).where(Refund.shop_id == shop_id, Refund.order_id == order.id)
            )
        ).all()
    )
    eligibility = compute_order_refund_eligibility(order, refunds)
    if not eligibility.eligible:
        preview = RefundPreview(
            order_id=order.id,
            order_no=order.order_no,
            order_status=order.status,
            paid_amount=order.paid_amount,
            refunded_amount=eligibility.refunded_amount,
            in_flight_amount=eligibility.in_flight_amount,
            refundable_amount=eligibility.refundable_amount,
            proposed_amount=amount or Decimal("0.00"),
            reason=reason or "待核对",
            projected_order_status=order.status,
            eligible=False,
            block_reason=eligibility.block_reason,
            plan_key=None,
        )
        return AgentAnswer(
            kind="refund_blocked",
            message=eligibility.block_reason or "当前订单不满足退款条件。",
            refund=preview,
        )

    missing_parts: list[str] = []
    if amount is None:
        missing_parts.append(f"退款金额（最高可退 ¥{eligibility.refundable_amount:.2f}）")
    if not reason:
        missing_parts.append("退款原因")
    if missing_parts:
        ctx = RefundContext(
            shop_id=shop_id,
            order_id=order.id,
            order_no=order.order_no,
            amount=amount,
            reason=reason,
        )
        return AgentAnswer(
            kind="refund_clarify",
            message=(
                f"订单 {order.order_no}（实付 ¥{order.paid_amount:.2f}，当前可退 ¥{eligibility.refundable_amount:.2f}）"
                f"满足退款资格。请补充{'与'.join(missing_parts)}，例如：退 50 元，原因部分商品售后。"
            ),
            refund_context=ctx,
        )

    assert amount is not None and reason is not None
    if amount > eligibility.refundable_amount:
        reason_text = f"拟退款 ¥{amount:.2f} 超过当前可退余额 ¥{eligibility.refundable_amount:.2f}。"
        preview = RefundPreview(
            order_id=order.id,
            order_no=order.order_no,
            order_status=order.status,
            paid_amount=order.paid_amount,
            refunded_amount=eligibility.refunded_amount,
            in_flight_amount=eligibility.in_flight_amount,
            refundable_amount=eligibility.refundable_amount,
            proposed_amount=amount,
            reason=reason,
            projected_order_status=order.status,
            eligible=False,
            block_reason=reason_text,
            plan_key=None,
        )
        return AgentAnswer(
            kind="refund_blocked",
            message=reason_text,
            refund=preview,
        )

    projected = (
        "refunded"
        if eligibility.refunded_amount + amount >= order.paid_amount
        else "partially_refunded"
    )
    plan_key = build_refund_plan_key(shop_id, order.id, amount, reason)
    preview = RefundPreview(
        order_id=order.id,
        order_no=order.order_no,
        order_status=order.status,
        paid_amount=order.paid_amount,
        refunded_amount=eligibility.refunded_amount,
        in_flight_amount=eligibility.in_flight_amount,
        refundable_amount=eligibility.refundable_amount,
        proposed_amount=amount,
        reason=reason,
        projected_order_status=projected,
        eligible=True,
        block_reason=None,
        plan_key=plan_key,
    )
    pending_plan = PendingRefundPlan(
        plan_key=plan_key,
        shop_id=shop_id,
        order_id=order.id,
        order_no=order.order_no,
        amount=amount,
        reason=reason,
    )
    return AgentAnswer(
        kind="refund_preview",
        message=(
            f"已核对订单 {order.order_no}（实付 ¥{order.paid_amount:.2f}，"
            f"当前可退 ¥{eligibility.refundable_amount:.2f}），"
            f"拟申请退款 ¥{amount:.2f}（原因：{reason}）。确认后将提交人工审批，不会直接执行资金退款。"
        ),
        refund=preview,
        pending_refund_plan=pending_plan,
    )
