from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import (
    Inventory,
    Order,
    OrderItem,
    Product,
    Promotion,
    PromotionItem,
    Refund,
    Shop,
    Sku,
)
from app.schemas.analytics import (
    AnalyticsAnomalyItem,
    AnalyticsOverviewResponse,
    AnalyticsSkuRankItem,
    AnalyticsTrendPoint,
    DailyReportMetricItem,
    DailyReportResponse,
    InventoryTurnoverResponse,
    PromotionReviewDayPoint,
    PromotionReviewResponse,
    SkuTurnoverItem,
)
from app.services.inventory import LOW_STOCK_THRESHOLD
from app.services.orders import PAID_ORDER_STATUSES


@dataclass(frozen=True)
class WindowAggregate:
    paid_gmv: Decimal
    paid_orders: int
    avg_order_value: Decimal
    refunded_orders: int
    refunded_amount: Decimal
    refund_rate: Decimal
    daily_gmv: dict[date, Decimal]
    daily_orders: dict[date, int]


def _pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous <= Decimal("0.00"):
        return None
    return (((current - previous) * Decimal("100")) / previous).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


async def _get_shop_meta(session: AsyncSession, shop_id: int) -> tuple[str, str]:
    row = (await session.execute(select(Shop.name, Shop.timezone).where(Shop.id == shop_id))).one_or_none()
    if row is None:
        raise AppError(404, "SHOP_NOT_FOUND", "当前店铺不存在。", detail="Shop not found")
    return row[0], row[1]


async def _aggregate_window(
    session: AsyncSession,
    *,
    shop_id: int,
    shop_tz: ZoneInfo,
    start_date: date,
    end_date: date,
) -> WindowAggregate:
    win_start = datetime.combine(start_date, time.min, shop_tz)
    win_end = datetime.combine(end_date, time.min, shop_tz)

    paid_rows = (
        await session.execute(
            select(Order.paid_amount, Order.paid_at)
            .where(
                Order.shop_id == shop_id,
                Order.status.in_(PAID_ORDER_STATUSES),
                Order.paid_at >= win_start,
                Order.paid_at < win_end,
            )
        )
    ).all()

    paid_gmv = Decimal("0.00")
    daily_gmv: dict[date, Decimal] = defaultdict(lambda: Decimal("0.00"))
    daily_orders: dict[date, int] = defaultdict(int)
    for amount, paid_at in paid_rows:
        amt = Decimal(amount or 0).quantize(Decimal("0.01"))
        paid_gmv += amt
        if paid_at is not None:
            local_day = paid_at.astimezone(shop_tz).date()
            daily_gmv[local_day] += amt
            daily_orders[local_day] += 1

    paid_orders = len(paid_rows)
    avg_order_value = (
        (paid_gmv / Decimal(paid_orders)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if paid_orders > 0
        else Decimal("0.00")
    )

    refund_ts = func.coalesce(Refund.processed_at, Refund.created_at)
    refund_row = (
        await session.execute(
            select(
                func.count(distinct(Refund.order_id)),
                func.coalesce(func.sum(Refund.amount), Decimal("0.00")),
            ).where(
                Refund.shop_id == shop_id,
                Refund.status == "succeeded",
                refund_ts >= win_start,
                refund_ts < win_end,
            )
        )
    ).one()
    refunded_orders = int(refund_row[0] or 0)
    refunded_amount = Decimal(refund_row[1] or 0).quantize(Decimal("0.01"))
    refund_rate = (
        ((Decimal(refunded_orders) * Decimal("100")) / Decimal(paid_orders)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if paid_orders > 0
        else Decimal("0.00")
    )

    return WindowAggregate(
        paid_gmv=paid_gmv.quantize(Decimal("0.01")),
        paid_orders=paid_orders,
        avg_order_value=avg_order_value,
        refunded_orders=refunded_orders,
        refunded_amount=refunded_amount,
        refund_rate=refund_rate,
        daily_gmv=daily_gmv,
        daily_orders=daily_orders,
    )


async def get_analytics_overview(
    session: AsyncSession,
    *,
    shop_id: int,
    as_of: date,
    days: int = 7,
) -> AnalyticsOverviewResponse:
    shop_name, tz_name = await _get_shop_meta(session, shop_id)
    shop_tz = ZoneInfo(tz_name)
    start_date = as_of - timedelta(days=days)
    prev_end_date = start_date
    prev_start_date = prev_end_date - timedelta(days=days)

    current = await _aggregate_window(
        session, shop_id=shop_id, shop_tz=shop_tz, start_date=start_date, end_date=as_of
    )
    previous = await _aggregate_window(
        session, shop_id=shop_id, shop_tz=shop_tz, start_date=prev_start_date, end_date=prev_end_date
    )

    trend: list[AnalyticsTrendPoint] = []
    for offset in range(days):
        cur_day = start_date + timedelta(days=offset)
        prev_day = prev_start_date + timedelta(days=offset)
        trend.append(
            AnalyticsTrendPoint(
                date_label=cur_day.strftime("%m/%d"),
                full_date=cur_day,
                current_gmv=current.daily_gmv.get(cur_day, Decimal("0.00")),
                previous_gmv=previous.daily_gmv.get(prev_day, Decimal("0.00")),
                current_orders=current.daily_orders.get(cur_day, 0),
                previous_orders=previous.daily_orders.get(prev_day, 0),
            )
        )

    win_start = datetime.combine(start_date, time.min, shop_tz)
    win_end = datetime.combine(as_of, time.min, shop_tz)
    rank_rows = (
        await session.execute(
            select(
                Sku.sku_code,
                Product.name,
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
                func.coalesce(func.sum(OrderItem.paid_amount), Decimal("0.00")).label("gmv"),
            )
            .join(Product, and_(Product.id == Sku.product_id, Product.shop_id == Sku.shop_id))
            .join(OrderItem, and_(OrderItem.sku_id == Sku.id, OrderItem.shop_id == Sku.shop_id))
            .join(Order, and_(Order.id == OrderItem.order_id, Order.shop_id == OrderItem.shop_id))
            .where(
                Sku.shop_id == shop_id,
                Order.status.in_(PAID_ORDER_STATUSES),
                Order.paid_at >= win_start,
                Order.paid_at < win_end,
            )
            .group_by(Sku.sku_code, Product.name)
            .order_by(func.sum(OrderItem.quantity).desc(), Sku.sku_code.asc())
            .limit(8)
        )
    ).all()

    sku_ranking = [
        AnalyticsSkuRankItem(
            sku_code=code,
            product_name=name,
            units_sold=int(units or 0),
            paid_gmv=Decimal(gmv or 0).quantize(Decimal("0.01")),
        )
        for code, name, units, gmv in rank_rows
    ]

    methodology = (
        f"当前区间为 {start_date:%m/%d} 00:00（含）至 {as_of:%m/%d} 00:00（不含），"
        f"仅含{shop_name}（{tz_name}）；销售额与销量按支付口径统计，不扣除后续退款；"
        "客单价 = 支付销售额 ÷ 支付订单数；退款率 = 当期成功退款涉及订单数 ÷ 支付订单数。"
    )

    return AnalyticsOverviewResponse(
        shop_id=shop_id,
        shop_name=shop_name,
        timezone=tz_name,
        days=days,
        start_date=start_date,
        end_date=as_of,
        prev_start_date=prev_start_date,
        prev_end_date=prev_end_date,
        paid_gmv=current.paid_gmv,
        prev_paid_gmv=previous.paid_gmv,
        gmv_change_pct=_pct_change(current.paid_gmv, previous.paid_gmv),
        paid_orders=current.paid_orders,
        prev_paid_orders=previous.paid_orders,
        avg_order_value=current.avg_order_value,
        prev_avg_order_value=previous.avg_order_value,
        refunded_orders=current.refunded_orders,
        refund_rate=current.refund_rate,
        prev_refund_rate=previous.refund_rate,
        trend=trend,
        sku_ranking=sku_ranking,
        methodology=methodology,
    )


async def get_daily_report(
    session: AsyncSession,
    *,
    shop_id: int,
    report_date: date,
) -> DailyReportResponse:
    shop_name, tz_name = await _get_shop_meta(session, shop_id)
    shop_tz = ZoneInfo(tz_name)
    next_day = report_date + timedelta(days=1)
    prev_day = report_date - timedelta(days=1)

    cur = await _aggregate_window(
        session, shop_id=shop_id, shop_tz=shop_tz, start_date=report_date, end_date=next_day
    )
    prev = await _aggregate_window(
        session, shop_id=shop_id, shop_tz=shop_tz, start_date=prev_day, end_date=report_date
    )
    gmv_delta = (cur.paid_gmv - prev.paid_gmv).quantize(Decimal("0.01"))
    change_pct = _pct_change(cur.paid_gmv, prev.paid_gmv)

    low_stock_rows = (
        await session.execute(
            select(Sku.sku_code, Product.name, Inventory.available_qty)
            .join(Product, and_(Product.id == Sku.product_id, Product.shop_id == Sku.shop_id))
            .join(Inventory, Inventory.sku_id == Sku.id)
            .where(Sku.shop_id == shop_id, Sku.status == "active", Inventory.available_qty < LOW_STOCK_THRESHOLD)
            .order_by(Inventory.available_qty.asc(), Sku.sku_code.asc())
        )
    ).all()

    top_sku_rows = (
        await session.execute(
            select(
                Sku.sku_code,
                func.coalesce(func.sum(OrderItem.quantity), 0),
            )
            .join(OrderItem, and_(OrderItem.sku_id == Sku.id, OrderItem.shop_id == Sku.shop_id))
            .join(Order, and_(Order.id == OrderItem.order_id, Order.shop_id == OrderItem.shop_id))
            .where(
                Sku.shop_id == shop_id,
                Order.status.in_(PAID_ORDER_STATUSES),
                Order.paid_at >= datetime.combine(report_date - timedelta(days=6), time.min, shop_tz),
                Order.paid_at < datetime.combine(next_day, time.min, shop_tz),
            )
            .group_by(Sku.sku_code)
            .order_by(func.sum(OrderItem.quantity).desc(), Sku.sku_code.asc())
            .limit(2)
        )
    ).all()

    delta_desc = f"比前日{'增长' if gmv_delta >= 0 else '回落'} ¥{abs(gmv_delta):,.2f}"
    facts = [
        (
            f"{report_date:%m 月 %d 日}支付销售额为 ¥{cur.paid_gmv:,.2f}（{delta_desc}），"
            f"共成交 {cur.paid_orders} 笔支付订单，客单价 ¥{cur.avg_order_value:,.2f}。"
        ),
    ]
    for code, units in top_sku_rows:
        facts.append(f"{code} 近 7 个完整自然日累计售出 {int(units)} 件（按支付件数统计）。")
    if low_stock_rows:
        low_summary = "、".join(f"{code}（可售 {qty} 件）" for code, _name, qty in low_stock_rows)
        facts.append(f"低库存预警：{low_summary}低于阈值 {LOW_STOCK_THRESHOLD} 件。")
    if cur.refunded_orders > 0:
        facts.append(
            f"当日共有 {cur.refunded_orders} 笔订单完成沙箱退款"
            f"（退款总额 ¥{cur.refunded_amount:,.2f}），退款率 {cur.refund_rate:.2f}%。"
        )
    else:
        facts.append("当日暂无新增成功退款记录。")

    if low_stock_rows and gmv_delta < 0:
        headline = f"销售较前日回落 ¥{abs(gmv_delta):,.2f}，低库存 SKU 需优先核查"
    elif low_stock_rows:
        headline = "销售保持稳健，低库存 SKU 建议及时跟进"
    elif gmv_delta < 0:
        headline = f"单日销售小幅回落 ¥{abs(gmv_delta):,.2f}，建议对照排期复核"
    else:
        headline = "单日经营指标稳健，无低库存预警"

    recommendations: list[str] = []
    if low_stock_rows:
        codes = "、".join(code for code, *_ in low_stock_rows)
        recommendations.append(f"优先核查 {codes} 库存与在途预占流水，确认是否需要登记补货入库。")
    if cur.refunded_orders > 0:
        recommendations.append("进入订单售后查看当日退款原因分布，排查是否集中在单一 SKU。")
    recommendations.append("系统仅呈现数据库统计事实，不会自动触发补货、改价或促销动作。")

    methodology = (
        f"时间区间：{report_date:%m/%d} 00:00（含）至 {next_day:%m/%d} 00:00（不含），"
        f"比较区间：前一完整自然日（{prev_day:%m/%d}），数据范围：{shop_name}（{tz_name}）。"
    )
    analysis_boundary = (
        "分析推测与边界：销售波动可能受促销时段切换、商品备货或外部流量变化影响，"
        "当前报表仅反映本店订单与库存事实，无法直接证明因果关系，请勿自动调价或补货。"
    )

    return DailyReportResponse(
        shop_id=shop_id,
        shop_name=shop_name,
        metrics=DailyReportMetricItem(
            report_date=report_date,
            prev_date=prev_day,
            timezone=tz_name,
            paid_gmv=cur.paid_gmv,
            prev_paid_gmv=prev.paid_gmv,
            gmv_delta=gmv_delta,
            gmv_change_pct=change_pct,
            paid_orders=cur.paid_orders,
            avg_order_value=cur.avg_order_value,
            refunded_orders=cur.refunded_orders,
            refunded_amount=cur.refunded_amount,
            refund_rate=cur.refund_rate,
        ),
        headline=headline,
        facts=facts,
        analysis_boundary=analysis_boundary,
        recommendations=recommendations,
        methodology=methodology,
    )


async def get_inventory_turnover(
    session: AsyncSession,
    *,
    shop_id: int,
    as_of: date,
    days: int = 7,
) -> InventoryTurnoverResponse:
    shop_name, tz_name = await _get_shop_meta(session, shop_id)
    shop_tz = ZoneInfo(tz_name)
    start_date = as_of - timedelta(days=days)
    prev_start_date = start_date - timedelta(days=days)
    win_start = datetime.combine(start_date, time.min, shop_tz)
    win_end = datetime.combine(as_of, time.min, shop_tz)

    sales_sub = (
        select(
            OrderItem.sku_id,
            func.coalesce(func.sum(OrderItem.quantity), 0).label("units_sold"),
        )
        .join(Order, and_(Order.id == OrderItem.order_id, Order.shop_id == OrderItem.shop_id))
        .where(
            OrderItem.shop_id == shop_id,
            Order.status.in_(PAID_ORDER_STATUSES),
            Order.paid_at >= win_start,
            Order.paid_at < win_end,
        )
        .group_by(OrderItem.sku_id)
        .subquery()
    )

    rows = (
        await session.execute(
            select(
                Sku.sku_code,
                Product.name,
                Product.category,
                Inventory.available_qty,
                Inventory.reserved_qty,
                func.coalesce(sales_sub.c.units_sold, 0),
            )
            .join(Product, and_(Product.id == Sku.product_id, Product.shop_id == Sku.shop_id))
            .join(Inventory, Inventory.sku_id == Sku.id)
            .outerjoin(sales_sub, sales_sub.c.sku_id == Sku.id)
            .where(Sku.shop_id == shop_id, Sku.status == "active")
            .order_by(func.coalesce(sales_sub.c.units_sold, 0).desc(), Sku.sku_code.asc())
        )
    ).all()

    items: list[SkuTurnoverItem] = []
    total_units = 0
    total_available = 0
    low_stock_items: list[SkuTurnoverItem] = []

    for code, name, category, available, reserved, units in rows:
        sold = int(units or 0)
        avail = int(available or 0)
        resv = int(reserved or 0)
        total_units += sold
        total_available += avail
        turnover = (
            (Decimal(sold) / Decimal(avail)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if avail > 0
            else Decimal("0.00")
        )
        is_low = avail < LOW_STOCK_THRESHOLD
        entry = SkuTurnoverItem(
            sku_code=code,
            product_name=name,
            category=category,
            units_sold=sold,
            available_qty=avail,
            reserved_qty=resv,
            turnover_rate=turnover,
            low_stock=is_low,
            low_stock_threshold=LOW_STOCK_THRESHOLD,
        )
        items.append(entry)
        if is_low:
            low_stock_items.append(entry)

    overall_turnover = (
        (Decimal(total_units) / Decimal(total_available)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if total_available > 0
        else Decimal("0.00")
    )

    cur_agg = await _aggregate_window(
        session, shop_id=shop_id, shop_tz=shop_tz, start_date=start_date, end_date=as_of
    )
    prev_agg = await _aggregate_window(
        session, shop_id=shop_id, shop_tz=shop_tz, start_date=prev_start_date, end_date=start_date
    )

    anomalies: list[AnalyticsAnomalyItem] = []
    for entry in low_stock_items:
        anomalies.append(
            AnalyticsAnomalyItem(
                kind="low_stock",
                title=(
                    f"{entry.sku_code} 可售 {entry.available_qty} 件，"
                    f"低于预警线 {entry.low_stock_threshold} 件"
                ),
                detail=(
                    f"{entry.product_name}近 {days} 日售出 {entry.units_sold} 件，"
                    f"周转率 {entry.turnover_rate:.2f} 次。"
                ),
                badge="低库存",
                severity="warning",
            )
        )
    if cur_agg.refund_rate > prev_agg.refund_rate:
        diff_pp = (cur_agg.refund_rate - prev_agg.refund_rate).quantize(Decimal("0.01"))
        anomalies.append(
            AnalyticsAnomalyItem(
                kind="refund_rate",
                title=f"退款率 {cur_agg.refund_rate:.2f}% 高于上期 {prev_agg.refund_rate:.2f}%",
                detail=f"较上期上升 {diff_pp:.2f} 个百分点（当期成功退款订单 {cur_agg.refunded_orders} 笔）。",
                badge="较上期上升",
                severity="info",
            )
        )

    methodology = (
        f"周转率 = 当期支付件数（{start_date:%m/%d}~{as_of:%m/%d}）÷ 当前可售库存。"
        "此指标按件数示例口径计算，非财务成本周转率；高周转且低库存商品建议人工核对补货。"
    )

    return InventoryTurnoverResponse(
        shop_id=shop_id,
        shop_name=shop_name,
        timezone=tz_name,
        days=days,
        start_date=start_date,
        end_date=as_of,
        total_units_sold=total_units,
        total_available_qty=total_available,
        overall_turnover_rate=overall_turnover,
        low_stock_count=len(low_stock_items),
        low_stock_threshold=LOW_STOCK_THRESHOLD,
        items=items,
        anomalies=anomalies,
        methodology=methodology,
    )


async def get_promotion_review(
    session: AsyncSession,
    *,
    shop_id: int,
    promotion_id: int,
) -> PromotionReviewResponse:
    promo = await session.scalar(
        select(Promotion).where(Promotion.shop_id == shop_id, Promotion.id == promotion_id)
    )
    if promo is None:
        raise AppError(404, "PROMOTION_NOT_FOUND", "当前店铺没有找到该促销活动。", detail="Promotion not found")

    shop_name, tz_name = await _get_shop_meta(session, shop_id)
    shop_tz = ZoneInfo(tz_name)

    item_rows = (
        await session.execute(
            select(PromotionItem.sku_id, Sku.sku_code)
            .join(Sku, and_(Sku.shop_id == PromotionItem.shop_id, Sku.id == PromotionItem.sku_id))
            .where(PromotionItem.shop_id == shop_id, PromotionItem.promotion_id == promo.id)
            .order_by(Sku.sku_code.asc())
        )
    ).all()
    sku_ids = [row[0] for row in item_rows]
    sku_codes = [row[1] for row in item_rows]

    local_start = promo.starts_at.astimezone(shop_tz)
    local_end = promo.ends_at.astimezone(shop_tz)
    promo_start_date = local_start.date()
    promo_end_date = local_end.date()
    duration_days = max(1, (promo_end_date - promo_start_date).days)
    baseline_start_date = promo_start_date - timedelta(days=duration_days)
    baseline_end_date = promo_start_date

    async def _daily_sku_totals(start_d: date, end_d: date) -> dict[date, tuple[int, Decimal]]:
        if not sku_ids:
            return {}
        rows = (
            await session.execute(
                select(Order.paid_at, OrderItem.quantity, OrderItem.paid_amount)
                .join(OrderItem, and_(OrderItem.order_id == Order.id, OrderItem.shop_id == Order.shop_id))
                .where(
                    Order.shop_id == shop_id,
                    OrderItem.sku_id.in_(sku_ids),
                    Order.status.in_(PAID_ORDER_STATUSES),
                    Order.paid_at >= datetime.combine(start_d, time.min, shop_tz),
                    Order.paid_at < datetime.combine(end_d, time.min, shop_tz),
                )
            )
        ).all()
        bucket_units: dict[date, int] = defaultdict(int)
        bucket_gmv: dict[date, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for paid_at, qty, amt in rows:
            if paid_at is None:
                continue
            d = paid_at.astimezone(shop_tz).date()
            bucket_units[d] += int(qty or 0)
            bucket_gmv[d] += Decimal(amt or 0).quantize(Decimal("0.01"))
        return {
            start_d + timedelta(days=i): (
                bucket_units.get(start_d + timedelta(days=i), 0),
                bucket_gmv.get(start_d + timedelta(days=i), Decimal("0.00")),
            )
            for i in range(duration_days)
        }

    promo_daily = await _daily_sku_totals(promo_start_date, promo_end_date)
    base_daily = await _daily_sku_totals(baseline_start_date, baseline_end_date)

    daily_comparison: list[PromotionReviewDayPoint] = []
    promo_units = 0
    promo_gmv = Decimal("0.00")
    baseline_units = 0
    baseline_gmv = Decimal("0.00")

    for i in range(duration_days):
        p_date = promo_start_date + timedelta(days=i)
        b_date = baseline_start_date + timedelta(days=i)
        p_u, p_g = promo_daily.get(p_date, (0, Decimal("0.00")))
        b_u, b_g = base_daily.get(b_date, (0, Decimal("0.00")))
        promo_units += p_u
        promo_gmv += p_g
        baseline_units += b_u
        baseline_gmv += b_g
        daily_comparison.append(
            PromotionReviewDayPoint(
                day_label=f"D{i + 1}",
                promo_date=p_date.strftime("%m/%d"),
                baseline_date=b_date.strftime("%m/%d"),
                promo_units=p_u,
                baseline_units=b_u,
                promo_gmv=p_g,
                baseline_gmv=b_g,
            )
        )

    methodology = (
        f"数据事实：活动期 {promo_start_date:%m/%d}–{promo_end_date - timedelta(days=1):%m/%d}"
        f"（{duration_days} 个自然日）对照活动前等长基线期"
        f" {baseline_start_date:%m/%d}–{baseline_end_date - timedelta(days=1):%m/%d}；"
        f"统计范围：{shop_name} · {'、'.join(sku_codes) or '未关联 SKU'}；"
        "销量口径：支付件数，后续退款不抵扣。"
    )
    review_note = (
        "分析推测：活动期销量变化反映当前周期的支付结果，"
        "但无法仅凭前后对比单独归因于折扣，建议结合同时段调价记录与流量变化综合复盘。"
    )

    return PromotionReviewResponse(
        promotion_id=promo.id,
        promotion_no=promo.promotion_no,
        promotion_name=promo.name,
        status=promo.status,
        sku_codes=sku_codes,
        timezone=tz_name,
        starts_at=promo.starts_at,
        ends_at=promo.ends_at,
        promo_start_date=promo_start_date,
        promo_end_date=promo_end_date,
        baseline_start_date=baseline_start_date,
        baseline_end_date=baseline_end_date,
        duration_days=duration_days,
        promo_units=promo_units,
        promo_gmv=promo_gmv.quantize(Decimal("0.01")),
        baseline_units=baseline_units,
        baseline_gmv=baseline_gmv.quantize(Decimal("0.01")),
        units_change_pct=_pct_change(Decimal(promo_units), Decimal(baseline_units)),
        daily_comparison=daily_comparison,
        methodology=methodology,
        review_note=review_note,
    )
