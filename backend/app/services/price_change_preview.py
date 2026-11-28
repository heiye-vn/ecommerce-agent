import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PriceChangeRequest, PriceHistory, Product, Sku
from app.schemas.agent import (
    AgentAnswer,
    PendingPriceChangePlan,
    PriceChangeContext,
    PriceChangePreview,
)
from app.services.price_changes import IN_FLIGHT_PRICE_CHANGE_STATUSES

AMBIGUOUS_PRICE = re.compile(r"[0-9.]+\s*(?:到|至|或|[~～–—-])\s*[0-9.]+")
TARGET_PRICE_MARKER = re.compile(
    r"(?:调(?:整)?(?:至|到|为)|改(?:至|到|为)|设(?:定)?(?:至|到|为)|新(?:售价|价格)(?:为|是)?|目标(?:售价|价格)(?:为|是)?)"
    r"\s*¥?\s*([0-9]+(?:\.[0-9]{1,2})?)\s*(?:元|块)?"
)
GENERAL_PRICE_WITH_UNIT = re.compile(
    r"(?:(?:¥|￥)\s*([0-9]+(?:\.[0-9]{1,2})?)|([0-9]+(?:\.[0-9]{1,2})?)\s*(?:元|块))"
)
PURE_PRICE = re.compile(r"^\s*¥?\s*([0-9]+(?:\.[0-9]{1,2})?)\s*(?:元|块)?\s*$")
REASON_EXTRACT = re.compile(r"(?:原因(?:是|为)?[:：]?|因为|用于|理由(?:是|为)?[:：]?)\s*([^，。；\n]+)")


def build_price_change_plan_key(shop_id: int, sku_id: int, target_price: Decimal, reason: str) -> str:
    raw = f"{shop_id}:{sku_id}:{target_price.quantize(Decimal('0.01'))}:{reason.strip()}"
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def _to_money(raw: str) -> Decimal | None:
    try:
        value = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return value if value > Decimal("0.00") else None


def parse_target_price(
    message: str,
    *,
    price_hint: Decimal | None = None,
    allow_bare_number: bool = False,
) -> Decimal | None:
    if AMBIGUOUS_PRICE.search(message):
        return None

    marked = TARGET_PRICE_MARKER.findall(message)
    if len(marked) == 1:
        return _to_money(marked[0])
    if len(marked) > 1:
        return None

    general = GENERAL_PRICE_WITH_UNIT.findall(message)
    if len(general) == 1:
        return _to_money(general[0][0] or general[0][1])
    if len(general) > 1:
        return None

    if allow_bare_number:
        pure = PURE_PRICE.fullmatch(message)
        if pure:
            return _to_money(pure.group(1))

    if price_hint is not None and price_hint > Decimal("0.00"):
        return price_hint.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return None


def parse_price_change_reason(
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
        stripped = message.strip(" ，。；;！!")
        if stripped and not PURE_PRICE.fullmatch(stripped) and len(stripped) >= 2:
            return stripped[:500]
    return None


async def preview_price_change(
    session: AsyncSession,
    *,
    shop_id: int,
    sku_code: str,
    target_price: Decimal | None,
    reason: str | None,
) -> AgentAnswer:
    normalized_sku = sku_code.strip().upper()
    row = (
        await session.execute(
            select(Sku, Product.name)
            .join(Product, and_(Product.shop_id == Sku.shop_id, Product.id == Sku.product_id))
            .where(Sku.shop_id == shop_id, Sku.sku_code == normalized_sku)
        )
    ).one_or_none()
    if row is None:
        return AgentAnswer(
            kind="not_found",
            message=f"当前店铺没有找到 {normalized_sku}，请核对 SKU 编码后重试。",
        )
    sku, product_name = row

    last_history = (
        await session.scalars(
            select(PriceHistory)
            .where(PriceHistory.sku_id == sku.id)
            .order_by(PriceHistory.changed_at.desc(), PriceHistory.id.desc())
        )
    ).first()

    def _build_preview(
        *,
        proposed: Decimal,
        proposed_reason: str,
        eligible: bool,
        block_reason: str | None,
        plan_key: str | None,
    ) -> PriceChangePreview:
        change_amount = (proposed - sku.current_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        change_pct = (
            ((proposed - sku.current_price) * Decimal("100") / sku.current_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if sku.current_price > Decimal("0.00")
            else Decimal("0.00")
        )
        return PriceChangePreview(
            sku_id=sku.id,
            sku_code=sku.sku_code,
            product_name=product_name,
            sku_status=sku.status,
            current_price=sku.current_price,
            cost_price=sku.cost,
            proposed_price=proposed,
            change_amount=change_amount,
            change_pct=change_pct,
            reason=proposed_reason,
            last_old_price=last_history.old_price if last_history else None,
            last_new_price=last_history.new_price if last_history else None,
            last_changed_at=last_history.changed_at if last_history else None,
            eligible=eligible,
            block_reason=block_reason,
            plan_key=plan_key,
        )

    in_flight = await session.scalar(
        select(PriceChangeRequest).where(
            PriceChangeRequest.shop_id == shop_id,
            PriceChangeRequest.sku_id == sku.id,
            PriceChangeRequest.status.in_(IN_FLIGHT_PRICE_CHANGE_STATUSES),
        )
    )
    if in_flight is not None:
        block = f"{sku.sku_code} 已有在途调价申请 {in_flight.request_no}，请等待处理完成或撤回后再操作。"
        return AgentAnswer(
            kind="price_change_blocked",
            message=block,
            price_change=_build_preview(
                proposed=target_price or in_flight.new_price,
                proposed_reason=reason or in_flight.reason,
                eligible=False,
                block_reason=block,
                plan_key=None,
            ),
        )

    if sku.status != "active":
        block = f"SKU {sku.sku_code} 当前状态为 {sku.status}（非在售），无法生成调价方案。"
        return AgentAnswer(
            kind="price_change_blocked",
            message=block,
            price_change=_build_preview(
                proposed=target_price or sku.current_price,
                proposed_reason=reason or "待核对",
                eligible=False,
                block_reason=block,
                plan_key=None,
            ),
        )

    missing_parts: list[str] = []
    if target_price is None:
        missing_parts.append(f"目标售价（当前售价 ¥{sku.current_price:.2f}，成本 ¥{sku.cost:.2f}）")
    if not reason:
        missing_parts.append("调价原因")
    if missing_parts:
        ctx = PriceChangeContext(
            shop_id=shop_id,
            sku_id=sku.id,
            sku_code=sku.sku_code,
            target_price=target_price,
            reason=reason,
        )
        return AgentAnswer(
            kind="price_change_clarify",
            message=(
                f"已读取 {sku.sku_code}（{product_name}）当前售价 ¥{sku.current_price:.2f}。"
                f"请补充{'与'.join(missing_parts)}，例如：调到 189 元，原因调整冬季常规售价。"
            ),
            price_change_context=ctx,
        )

    assert target_price is not None and reason is not None
    if target_price == sku.current_price:
        block = f"拟调整售价 ¥{target_price:.2f} 与当前售价一致，无需提交调价。"
        return AgentAnswer(
            kind="price_change_blocked",
            message=block,
            price_change=_build_preview(
                proposed=target_price,
                proposed_reason=reason,
                eligible=False,
                block_reason=block,
                plan_key=None,
            ),
        )
    if target_price < sku.cost:
        block = f"拟调整售价 ¥{target_price:.2f} 低于成本价 ¥{sku.cost:.2f}，已被规则拦截。"
        return AgentAnswer(
            kind="price_change_blocked",
            message=block,
            price_change=_build_preview(
                proposed=target_price,
                proposed_reason=reason,
                eligible=False,
                block_reason=block,
                plan_key=None,
            ),
        )

    plan_key = build_price_change_plan_key(shop_id, sku.id, target_price, reason)
    preview = _build_preview(
        proposed=target_price,
        proposed_reason=reason,
        eligible=True,
        block_reason=None,
        plan_key=plan_key,
    )
    pending_plan = PendingPriceChangePlan(
        plan_key=plan_key,
        shop_id=shop_id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        new_price=target_price,
        reason=reason,
    )
    return AgentAnswer(
        kind="price_change_preview",
        message=(
            f"已读取本店 {sku.sku_code}（{product_name}）当前售价 ¥{sku.current_price:.2f} 及历史价格，"
            f"拟调整为 ¥{target_price:.2f}（原因：{reason}）。确认后仅提交人工审批，批准前不会修改线上售价。"
        ),
        price_change=preview,
        pending_price_change_plan=pending_plan,
    )
