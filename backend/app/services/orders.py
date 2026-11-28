from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import DEFAULT_SHOP_TIMEZONE
from app.models import Order, OrderItem, Product, Refund, Shop, Sku
from app.schemas.orders import OrderDetail, OrderLine, OrderRefundBrief, OrderSummary, SkuSales

# 采用支付件数口径：曾支付成功的订单均计入销量，后续退款不抵扣
PAID_ORDER_STATUSES = ("paid", "partially_refunded", "refunded")
REFUNDABLE_ORDER_STATUSES = ("paid", "partially_refunded")
IN_FLIGHT_REFUND_STATUSES = ("pending_approval", "approved", "processing")

EXCEPTION_ADVICE_MAP: dict[str, str] = {
    "buyer_cancelled": "买家已提交取消申请，建议先核对是否尚未打包发货，再评估发起退款申请。",
    "inventory_review": "待发货订单关联商品库存记录待复核，请先核对库存预占与流水后再决定是否发货。",
    "logistics_sync": "物流轨迹与履约状态待同步，请先核实承运商状态再处理后续售后请求。",
}


@dataclass(frozen=True)
class RefundEligibilityComputation:
    refunded_amount: Decimal
    in_flight_amount: Decimal
    refundable_amount: Decimal
    eligible: bool
    block_reason: str | None
    in_flight_refund: Refund | None


def exception_advice_for(exception_type: str | None) -> str | None:
    if not exception_type:
        return None
    return EXCEPTION_ADVICE_MAP.get(
        exception_type,
        "该订单存在待复核售后异常，请结合订单明细与库存流水核实后再处理。",
    )


def compute_order_refund_eligibility(
    order: Order,
    refunds: Sequence[Refund],
) -> RefundEligibilityComputation:
    refunded_amount = sum(
        (item.amount for item in refunds if item.status == "succeeded"),
        start=Decimal("0.00"),
    )
    in_flight_refund = next(
        (item for item in refunds if item.status in IN_FLIGHT_REFUND_STATUSES),
        None,
    )
    in_flight_amount = sum(
        (item.amount for item in refunds if item.status in IN_FLIGHT_REFUND_STATUSES),
        start=Decimal("0.00"),
    )
    refundable_amount = max(Decimal("0.00"), order.paid_amount - refunded_amount)

    if order.status not in REFUNDABLE_ORDER_STATUSES:
        return RefundEligibilityComputation(
            refunded_amount=refunded_amount,
            in_flight_amount=in_flight_amount,
            refundable_amount=Decimal("0.00"),
            eligible=False,
            block_reason="仅已支付或部分退款的订单支持发起退款，当前订单状态不可申请退款。",
            in_flight_refund=in_flight_refund,
        )
    if in_flight_refund is not None:
        return RefundEligibilityComputation(
            refunded_amount=refunded_amount,
            in_flight_amount=in_flight_amount,
            refundable_amount=refundable_amount,
            eligible=False,
            block_reason=(
                f"订单存在在途退款申请 {in_flight_refund.refund_no}"
                f"（¥{in_flight_refund.amount:.2f}），请勿重复发起，等待当前申请完结或撤回后再操作。"
            ),
            in_flight_refund=in_flight_refund,
        )
    if refundable_amount <= Decimal("0.00"):
        return RefundEligibilityComputation(
            refunded_amount=refunded_amount,
            in_flight_amount=in_flight_amount,
            refundable_amount=Decimal("0.00"),
            eligible=False,
            block_reason="该订单已完成全额退款，当前可退余额为 ¥0.00。",
            in_flight_refund=None,
        )
    return RefundEligibilityComputation(
        refunded_amount=refunded_amount,
        in_flight_amount=in_flight_amount,
        refundable_amount=refundable_amount,
        eligible=True,
        block_reason=None,
        in_flight_refund=None,
    )


def summarize(
    order: Order,
    sku_codes: list[str],
    quantity: int,
    *,
    refunded_amount: Decimal = Decimal("0.00"),
    in_flight_refund_amount: Decimal = Decimal("0.00"),
) -> OrderSummary:
    return OrderSummary(
        id=order.id,
        order_no=order.order_no,
        status=order.status,
        fulfillment_status=order.fulfillment_status,
        exception_type=order.exception_type,
        exception_note=exception_advice_for(order.exception_type),
        paid_amount=order.paid_amount,
        refunded_amount=refunded_amount,
        in_flight_refund_amount=in_flight_refund_amount,
        paid_at=order.paid_at,
        created_at=order.created_at,
        sku_codes=sku_codes,
        quantity=quantity,
    )


async def list_orders(session: AsyncSession, shop_id: int) -> list[OrderSummary]:
    lines = (
        select(
            OrderItem.order_id,
            func.array_agg(distinct(Sku.sku_code)).label("sku_codes"),
            func.sum(OrderItem.quantity).label("quantity"),
        )
        .join(Sku, and_(Sku.id == OrderItem.sku_id, Sku.shop_id == OrderItem.shop_id))
        .where(OrderItem.shop_id == shop_id)
        .group_by(OrderItem.order_id)
        .subquery()
    )
    refund_totals = (
        select(
            Refund.order_id,
            func.coalesce(
                func.sum(case((Refund.status == "succeeded", Refund.amount), else_=Decimal("0.00"))),
                Decimal("0.00"),
            ).label("refunded_amount"),
            func.coalesce(
                func.sum(
                    case(
                        (Refund.status.in_(IN_FLIGHT_REFUND_STATUSES), Refund.amount),
                        else_=Decimal("0.00"),
                    )
                ),
                Decimal("0.00"),
            ).label("in_flight_refund_amount"),
        )
        .where(Refund.shop_id == shop_id)
        .group_by(Refund.order_id)
        .subquery()
    )
    query = (
        select(
            Order,
            lines.c.sku_codes,
            lines.c.quantity,
            func.coalesce(refund_totals.c.refunded_amount, Decimal("0.00")),
            func.coalesce(refund_totals.c.in_flight_refund_amount, Decimal("0.00")),
        )
        .outerjoin(lines, lines.c.order_id == Order.id)
        .outerjoin(refund_totals, refund_totals.c.order_id == Order.id)
        .where(Order.shop_id == shop_id)
        .order_by(func.coalesce(Order.paid_at, Order.created_at).desc(), Order.id.desc())
    )
    return [
        summarize(
            order,
            sorted(codes or []),
            quantity or 0,
            refunded_amount=Decimal(refunded_amt or 0),
            in_flight_refund_amount=Decimal(in_flight_amt or 0),
        )
        for order, codes, quantity, refunded_amt, in_flight_amt in await session.execute(query)
    ]


async def get_order(session: AsyncSession, shop_id: int, order_id: int) -> OrderDetail | None:
    order = await session.scalar(select(Order).where(Order.shop_id == shop_id, Order.id == order_id))
    if order is None:
        return None
    query = (
        select(
            OrderItem.id,
            Sku.sku_code,
            Product.name.label("product_name"),
            OrderItem.quantity,
            OrderItem.unit_price,
            OrderItem.paid_amount,
        )
        .join(Sku, and_(Sku.id == OrderItem.sku_id, Sku.shop_id == OrderItem.shop_id))
        .join(Product, and_(Product.id == Sku.product_id, Product.shop_id == Sku.shop_id))
        .where(OrderItem.shop_id == shop_id, OrderItem.order_id == order_id)
        .order_by(OrderItem.id)
    )
    items = [OrderLine.model_validate(row) for row in (await session.execute(query)).mappings()]
    refunds = list(
        (
            await session.scalars(
                select(Refund)
                .where(Refund.shop_id == shop_id, Refund.order_id == order_id)
                .order_by(Refund.created_at.desc(), Refund.id.desc())
            )
        ).all()
    )
    eligibility = compute_order_refund_eligibility(order, refunds)
    summary = summarize(
        order,
        sorted({item.sku_code for item in items}),
        sum(item.quantity for item in items),
        refunded_amount=eligibility.refunded_amount,
        in_flight_refund_amount=eligibility.in_flight_amount,
    )
    return OrderDetail(
        **summary.model_dump(),
        items=items,
        refundable_amount=eligibility.refundable_amount,
        can_refund=eligibility.eligible,
        refund_block_reason=eligibility.block_reason,
        refunds=[
            OrderRefundBrief(
                id=item.id,
                refund_no=item.refund_no,
                amount=item.amount,
                reason=item.reason,
                status=item.status,
                failure_reason=item.failure_reason,
                created_at=item.created_at,
            )
            for item in refunds
        ],
    )


async def sku_sales(
    session: AsyncSession,
    shop_id: int,
    sku_code: str,
    as_of: date,
    timezone_name: str | None = None,
) -> SkuSales | None:
    sku_id = await session.scalar(select(Sku.id).where(Sku.shop_id == shop_id, Sku.sku_code == sku_code))
    if sku_id is None:
        return None
    resolved_tz_name = timezone_name or (
        await session.scalar(select(Shop.timezone).where(Shop.id == shop_id))
    ) or DEFAULT_SHOP_TIMEZONE
    shop_tz = ZoneInfo(resolved_tz_name)
    start_date = as_of - timedelta(days=7)
    query = (
        select(func.coalesce(func.sum(OrderItem.quantity), 0), func.count(distinct(Order.id)))
        .join(Order, and_(Order.id == OrderItem.order_id, Order.shop_id == OrderItem.shop_id))
        .where(
            Order.shop_id == shop_id,
            OrderItem.sku_id == sku_id,
            Order.status.in_(PAID_ORDER_STATUSES),
            Order.paid_at >= datetime.combine(start_date, time.min, shop_tz),
            Order.paid_at < datetime.combine(as_of, time.min, shop_tz),
        )
    )
    units, orders = (await session.execute(query)).one()
    return SkuSales(
        sku_code=sku_code,
        units_sold=int(units),
        paid_orders=int(orders),
        start_date=start_date,
        end_date=as_of,
        timezone=shop_tz.key,
    )
