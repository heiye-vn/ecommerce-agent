import re
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import DEFAULT_SHOP_TIMEZONE
from app.models import Inventory, Product, Sku
from app.schemas.agent import AgentAnswer, PendingPromotionPlan, PromotionContext, PromotionPreview
from app.services.orders import sku_sales
from app.services.promotions import find_conflicting_promotions

MINIMUM_QTY = 100
DISCOUNT = re.compile(r"(?<![\d.])([0-9]{1,2}(?:\.[0-9]{1,6})?)\s*折")
DURATION = re.compile(
    r"(?:持续\s*|做\s*|为期\s*)?([0-9]{1,7})\s*天"
    r"(?!\s*(?:半|到|至|或|[~～–—\-]|[0-9一二三四五六七八九十]+\s*(?:小时|分钟)))"
)
INVALID_DURATION = re.compile(
    r"(?:[0-9]+\.[0-9]+\s*天|[0-9一二三四五六七八九十]+\s*天\s*(?:半|到|至|或|[~～–—\-]|[0-9一二三四五六七八九十]+\s*(?:小时|分钟)))"
)
DISCOUNT_PREFIX = re.compile(r"(?:[+＋\-－−~～–—]|负|正|[0-9一二三四五六七八九十]\s*(?:到|至|或))\s*$")
AMBIGUOUS_DISCOUNT = re.compile(
    r"(?:[0-9一二三四五六七八九十.]+\s*(?:到|至|或|[~～–—\-])\s*[0-9一二三四五六七八九十.]+\s*折)"
)
CUSTOM_STOCK = re.compile(r"库存[^，。；、,.!?！？]{0,24}[0-9]+\s*件")
CLOCK_TIME = re.compile(r"(?:上午|下午|中午|凌晨|晚上|[0-9]{1,2}[:：][0-9]{2}|[0-9]{1,2}\s*[点时])")
START_DATE = (
    r"(?:(?P<iso>[0-9]{4}-[0-9]{2}-[0-9]{2})"
    r"|(?:(?P<year>[0-9]{4})\s*年\s*)?(?P<month>[0-9]{1,2})\s*月\s*(?P<day>[0-9]{1,2})\s*[日号])"
)
FOLLOW_UP = re.compile(rf"(?:从\s*)?{START_DATE}\s*(?:开始)?[。！!]?\s*")
INLINE_START = re.compile(rf"(?<![0-9])(?:从\s*)?{START_DATE}\s*开始")
DATE_MENTION = re.compile(rf"(?<![0-9]){START_DATE}")
# 排除“最近 7 天销量”这类查数时间窗口，避免误识别为促销持续天数
SALES_WINDOW = re.compile(r"(?:最近|近)\s*[0-9一二三四五六七八九十]+\s*天")
CONFIRM_PHRASE = re.compile(
    r"^(?:好的[，,]?\s*)?(?:我)?(?:确认|同意)(?:刚才的|上面的|这个|当前|该)?(?:促销)?(?:方案|预览)?(?:并)?(?:提交(?:审批)?)?[。！!]?$"
)


def is_confirm_message(message: str) -> bool:
    return CONFIRM_PHRASE.fullmatch(message.strip()) is not None


def build_plan_key(shop_id: int, sku_code: str, discount: Decimal, start_day: date, duration_days: int) -> str:
    raw = f"{shop_id}:{sku_code.upper()}:{discount.normalize()}:{start_day.isoformat()}:{duration_days}"
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def parse_terms(
    shop_id: int,
    sku_code: str,
    message: str,
    *,
    discount_hint: Decimal | None = None,
    duration_hint: int | None = None,
    ambiguous_hint: bool = False,
) -> PromotionContext | None:
    if (
        ambiguous_hint
        or CUSTOM_STOCK.search(message)
        or INVALID_DURATION.search(message)
        or AMBIGUOUS_DISCOUNT.search(message)
    ):
        return None

    discounts = list(DISCOUNT.finditer(message))
    if len(discounts) > 1:
        return None
    discount: Decimal | None = None
    if len(discounts) == 1:
        discount_match = discounts[0]
        if DISCOUNT_PREFIX.search(message[:discount_match.start()]):
            return None
        discount = Decimal(discount_match[1])
    elif discount_hint is not None:
        discount = discount_hint

    cleaned_for_duration = SALES_WINDOW.sub("", message)
    durations = DURATION.findall(cleaned_for_duration)
    if len(durations) > 1:
        return None
    duration: int | None = None
    if len(durations) == 1:
        duration = int(durations[0])
    elif duration_hint is not None:
        duration = duration_hint

    if discount is None or duration is None:
        return None
    if not (Decimal(0) < discount < Decimal(10)) or not (0 < duration <= 365):
        return None
    return PromotionContext(shop_id=shop_id, sku_code=sku_code, discount=discount, duration_days=duration)


def resolve_start_date_match(
    message: str,
    *,
    is_follow_up: bool,
    start_date_hint: str | None = None,
) -> tuple[re.Match[str] | None, bool]:
    """解析开始日期；返回 (正则匹配, 是否存在歧义/非法时钟点)。"""
    if CLOCK_TIME.search(message):
        return None, True
    if is_follow_up:
        direct = FOLLOW_UP.fullmatch(message)
        if direct is not None:
            return direct, False
        mentions = list(DATE_MENTION.finditer(message))
        if len(mentions) > 1:
            return None, True
        if len(mentions) == 1:
            return mentions[0], False
        if start_date_hint:
            hint_match = FOLLOW_UP.fullmatch(start_date_hint.strip())
            if hint_match is not None:
                return hint_match, False
        return None, False

    mentions = list(DATE_MENTION.finditer(message))
    start = INLINE_START.search(message)
    if mentions and (start is None or len(mentions) != 1):
        return None, True
    if start is not None:
        return start, False
    if start_date_hint:
        hint_match = FOLLOW_UP.fullmatch(start_date_hint.strip())
        if hint_match is not None:
            return hint_match, False
    return None, False


def activity_window(
    match: re.Match[str],
    business_day: date,
    duration_days: int,
    timezone_name: str = DEFAULT_SHOP_TIMEZONE,
) -> tuple[datetime, datetime]:
    shop_tz = ZoneInfo(timezone_name)
    iso = match["iso"]
    start = (
        date.fromisoformat(iso)
        if iso
        else date(int(match["year"] or business_day.year), int(match["month"]), int(match["day"]))
    )
    if start <= business_day:
        raise ValueError("Start date must follow the sandbox business day")
    end = start + timedelta(days=duration_days)
    return datetime.combine(start, time.min, shop_tz), datetime.combine(end, time.min, shop_tz)


async def preview_promotion(
    session: AsyncSession,
    terms: PromotionContext,
    business_day: date,
    match: re.Match[str] | None,
    timezone_name: str = DEFAULT_SHOP_TIMEZONE,
) -> AgentAnswer:
    query = (
        select(
            Sku.id.label("sku_id"),
            Sku.current_price,
            Sku.cost,
            Sku.status,
            Product.name.label("product_name"),
            Product.status.label("product_status"),
            Inventory.available_qty,
        )
        .join(Product, and_(Sku.shop_id == Product.shop_id, Sku.product_id == Product.id))
        .outerjoin(Inventory, Inventory.sku_id == Sku.id)
        .where(Sku.shop_id == terms.shop_id, Sku.sku_code == terms.sku_code)
    )
    facts = (await session.execute(query)).one_or_none()
    if facts is None:
        return AgentAnswer(kind="not_found", message=f"当前店铺没有找到 {terms.sku_code}。请核对编码后重试。")

    sales = await sku_sales(session, terms.shop_id, terms.sku_code, business_day, timezone_name=timezone_name)
    promotion_price = (facts.current_price * terms.discount / 10).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    below_cost = promotion_price < facts.cost
    risk_warnings: list[str] = []
    if below_cost:
        risk_warnings.append(
            f"促销价 ¥{promotion_price} 低于商品成本价 ¥{facts.cost}，存在负毛利风险，请谨慎确认。"
        )

    preview = PromotionPreview(
        sku_code=terms.sku_code,
        original_price=facts.current_price,
        cost_price=facts.cost,
        promotion_price=promotion_price,
        below_cost=below_cost,
        risk_warnings=risk_warnings,
        discount=terms.discount,
        duration_days=terms.duration_days,
        available_qty=facts.available_qty,
        minimum_qty=MINIMUM_QTY,
        timezone=timezone_name,
        conflict_check="unavailable",
    )
    if facts.status != "active" or facts.product_status != "active":
        block = "该商品或 SKU 当前不在售。"
    elif facts.available_qty is None:
        block = "当前没有库存记录，无法校验库存门槛。"
    elif facts.available_qty < MINIMUM_QTY:
        block = f"可售库存 {facts.available_qty} 件，低于 {MINIMUM_QTY} 件门槛。"
    else:
        block = None
    if block:
        return AgentAnswer(kind="promotion_blocked", message=block, promotion=preview, sales=sales)

    if match is None:
        clarification = "请提供活动开始日期。"
    else:
        try:
            starts_at, ends_at = activity_window(
                match, business_day, terms.duration_days, timezone_name=timezone_name
            )
        except (ValueError, OverflowError):
            clarification = f"请提供晚于沙箱业务日 {business_day} 且能计算结束日期的有效开始日期。"
        else:
            preview.starts_at = starts_at
            preview.ends_at = ends_at
            conflicts = await find_conflicting_promotions(
                session,
                shop_id=terms.shop_id,
                sku_id=facts.sku_id,
                starts_at=starts_at,
                ends_at=ends_at,
            )
            if conflicts:
                preview.conflict_check = "conflict"
                preview.conflicting_promotions = conflicts
                return AgentAnswer(
                    kind="promotion_blocked",
                    message=(
                        f"所选时段与已有已排期/生效活动 {', '.join(conflicts)} 存在重叠，"
                        "请调整开始日期后再试。"
                    ),
                    promotion=preview,
                    sales=sales,
                    promotion_context=terms,
                )

            preview.conflict_check = "clear"
            start_day = starts_at.date()
            plan_key = build_plan_key(
                terms.shop_id, terms.sku_code, terms.discount, start_day, terms.duration_days
            )
            preview.plan_key = plan_key
            pending_plan = PendingPromotionPlan(
                plan_key=plan_key,
                shop_id=terms.shop_id,
                sku_code=terms.sku_code,
                discount=terms.discount,
                start_date=start_day,
                duration_days=terms.duration_days,
                name=f"{facts.product_name} {terms.discount.normalize()} 折促销",
            )
            note = "已按当前商品、库存和排期校验生成促销方案，确认后可提交人工审批。"
            if below_cost:
                note += f" 注意：促销价 ¥{promotion_price} 低于成本价 ¥{facts.cost}。"
            return AgentAnswer(
                kind="promotion_preview",
                message=note,
                promotion=preview,
                sales=sales,
                pending_plan=pending_plan,
            )

    summary = f"当前可售库存 {facts.available_qty} 件，达到 {MINIMUM_QTY} 件门槛。"
    if sales:
        summary = f"{terms.sku_code} 最近 7 天售出 {sales.units_sold} 件，" + summary
    if below_cost:
        summary += f" 风险提示：按当前折扣折算后促销价 ¥{promotion_price} 低于成本价 ¥{facts.cost}。"
    return AgentAnswer(
        kind="promotion_clarify",
        message=summary + clarification,
        promotion=preview,
        sales=sales,
        promotion_context=terms,
    )
