from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import DEFAULT_SHOP_TIMEZONE
from app.models import Order, OrderItem, Product, Shop, Sku
from app.schemas.orders import OrderDetail, OrderLine, OrderSummary, SkuSales

# 采用支付件数口径：曾支付成功的订单均计入销量，后续退款不抵扣
PAID_ORDER_STATUSES = ("paid", "partially_refunded", "refunded")


def summarize(order: Order, sku_codes: list[str], quantity: int) -> OrderSummary:
    return OrderSummary(
        id=order.id, order_no=order.order_no, status=order.status, paid_amount=order.paid_amount,
        paid_at=order.paid_at, created_at=order.created_at, sku_codes=sku_codes, quantity=quantity,
    )


async def list_orders(session: AsyncSession, shop_id: int) -> list[OrderSummary]:
    lines = (
        select(
            OrderItem.order_id, func.array_agg(distinct(Sku.sku_code)).label("sku_codes"),
            func.sum(OrderItem.quantity).label("quantity"),
        )
        .join(Sku, and_(Sku.id == OrderItem.sku_id, Sku.shop_id == OrderItem.shop_id))
        .where(OrderItem.shop_id == shop_id)
        .group_by(OrderItem.order_id)
        .subquery()
    )
    query = (
        select(Order, lines.c.sku_codes, lines.c.quantity)
        .outerjoin(lines, lines.c.order_id == Order.id)
        .where(Order.shop_id == shop_id)
        .order_by(func.coalesce(Order.paid_at, Order.created_at).desc(), Order.id.desc())
    )
    return [summarize(order, sorted(codes or []), quantity or 0)
            for order, codes, quantity in await session.execute(query)]


async def get_order(session: AsyncSession, shop_id: int, order_id: int) -> OrderDetail | None:
    order = await session.scalar(select(Order).where(Order.shop_id == shop_id, Order.id == order_id))
    if order is None:
        return None
    query = (
        select(OrderItem.id, Sku.sku_code, Product.name.label("product_name"),
               OrderItem.quantity, OrderItem.unit_price, OrderItem.paid_amount)
        .join(Sku, and_(Sku.id == OrderItem.sku_id, Sku.shop_id == OrderItem.shop_id))
        .join(Product, and_(Product.id == Sku.product_id, Product.shop_id == Sku.shop_id))
        .where(OrderItem.shop_id == shop_id, OrderItem.order_id == order_id)
        .order_by(OrderItem.id)
    )
    items = [OrderLine.model_validate(row) for row in (await session.execute(query)).mappings()]
    summary = summarize(order, sorted({item.sku_code for item in items}), sum(item.quantity for item in items))
    return OrderDetail(**summary.model_dump(), items=items)


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
        sku_code=sku_code, units_sold=int(units), paid_orders=int(orders),
        start_date=start_date, end_date=as_of, timezone=shop_tz.key,
    )
