from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Inventory,
    InventoryMovement,
    Order,
    OrderItem,
    Organization,
    PriceHistory,
    Product,
    Promotion,
    PromotionItem,
    Refund,
    Shop,
    Sku,
    User,
)
from app.services.seed import DEMO_ORGANIZATION, DEMO_SHOP, DEMO_USERNAME
from app.services.seed_catalog import DEMO_DATE, SKU_CODE

TZ = ZoneInfo("Asia/Shanghai")


async def seed_stage5a_sandbox(session: AsyncSession) -> dict[str, bool]:
    """
    为本地沙箱补充阶段 5A 演练数据：
    - 新增 SKU-018（咖啡器具套装，含已完成促销 PRM-20261110-02）与 SKU-011（双层玻璃杯，含生效中促销 PRM-20261119-01）
    - 补齐 11-07 ~ 11-20 上期/当期订单与 1 笔成功退款（绝不往 SKU-001 的 11-14 ~ 11-21 窗口追加订单，保持 120 件基线！）
    - 为 SKU-001 补齐历史调价记录与 shop_id 归属
    幂等可重复执行。
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(726005)"))
    org = await session.scalar(
        select(Organization).where(Organization.name == DEMO_ORGANIZATION, Organization.status == "active")
    )
    if org is None:
        raise ValueError("Demo organization not found; run python -m scripts.seed first")

    shop = await session.scalar(
        select(Shop).where(Shop.organization_id == org.id, Shop.name == DEMO_SHOP, Shop.status == "active")
    )
    operator = await session.scalar(
        select(User).where(User.username == DEMO_USERNAME, User.status == "active")
    )
    if shop is None or operator is None:
        raise ValueError("Demo shop or operator not found; run python -m scripts.seed first")

    created = {
        "stage5a_skus": False,
        "stage5a_orders": False,
        "stage5a_promotions": False,
        "stage5a_price_history": False,
    }

    async def _ensure_sku(
        *,
        sku_code: str,
        product_name: str,
        category: str,
        cost: Decimal,
        price: Decimal,
        qty: int,
    ) -> Sku:
        existing = await session.scalar(select(Sku).where(Sku.shop_id == shop.id, Sku.sku_code == sku_code))
        if existing is not None:
            return existing
        prod = Product(shop_id=shop.id, name=product_name, category=category, status="active", created_at=DEMO_DATE)
        session.add(prod)
        await session.flush()
        sku = Sku(
            shop_id=shop.id,
            product_id=prod.id,
            sku_code=sku_code,
            cost=cost,
            current_price=price,
            status="active",
            created_at=DEMO_DATE,
        )
        session.add(sku)
        await session.flush()
        session.add(Inventory(sku_id=sku.id, available_qty=qty, reserved_qty=0, version=1, created_at=DEMO_DATE))
        session.add(
            InventoryMovement(
                shop_id=shop.id,
                sku_id=sku.id,
                movement_type="opening_balance",
                quantity=qty,
                available_delta=qty,
                reserved_delta=0,
                before_available=0,
                after_available=qty,
                before_reserved=0,
                after_reserved=0,
                reference_id=f"INIT-{sku_code}",
                actor_id=operator.id,
                note="阶段 5A 沙箱初始化库存",
                created_at=DEMO_DATE - timedelta(days=15),
            )
        )
        created["stage5a_skus"] = True
        return sku

    sku_coffee = await _ensure_sku(
        sku_code="SKU-018",
        product_name="便携咖啡手冲套装",
        category="器具",
        cost=Decimal("150.00"),
        price=Decimal("299.00"),
        qty=131,
    )
    sku_glass = await _ensure_sku(
        sku_code="SKU-011",
        product_name="双层隔热玻璃杯",
        category="器具",
        cost=Decimal("32.00"),
        price=Decimal("79.00"),
        qty=242,
    )

    sku1 = await session.scalar(select(Sku).where(Sku.shop_id == shop.id, Sku.sku_code == SKU_CODE))
    sku5 = await session.scalar(select(Sku).where(Sku.shop_id == shop.id, Sku.sku_code == "SKU-005"))

    # 1. 为 SKU-001 补齐一条更早的历史价格记录（¥219 -> ¥209，不改变当前售价 ¥199.00）
    if sku1 is not None:
        earlier_exists = await session.scalar(
            select(PriceHistory.id).where(
                PriceHistory.sku_id == sku1.id,
                PriceHistory.reason == "日常调价归档样例",
            )
        )
        if earlier_exists is None:
            session.add(
                PriceHistory(
                    shop_id=shop.id,
                    sku_id=sku1.id,
                    old_price=Decimal("219.00"),
                    new_price=Decimal("209.00"),
                    changed_at=DEMO_DATE - timedelta(days=25),
                    actor_id=operator.id,
                    approval_no="APR-20261026-03",
                    reason="日常调价归档样例",
                    created_at=DEMO_DATE - timedelta(days=25),
                )
            )
            created["stage5a_price_history"] = True

    # 2. 补充多日经营分析与活动复盘演示订单
    # SKU-001 仅写入 11-14 之前的上期对比订单，绝不污染 11-14 ~ 11-21 120 件基线
    marker_order = await session.scalar(
        select(Order.id).where(Order.shop_id == shop.id, Order.order_no == "ORD-S5A-20261112-01")
    )
    if marker_order is None:
        # 活动 PRM-20261110-02（SKU-018）：11/12..11/16 五日 vs 基线 11/07..11/11 五日
        baseline_units = (8, 9, 10, 8, 11)
        promo_units = (15, 19, 22, 20, 22)
        baseline_start = datetime(2026, 11, 7, 11, 0, tzinfo=TZ)
        promo_start = datetime(2026, 11, 12, 11, 0, tzinfo=TZ)

        created_orders: list[tuple[Order, int, int, Decimal]] = []

        for idx, qty in enumerate(baseline_units):
            dt = baseline_start + timedelta(days=idx)
            amt = sku_coffee.current_price * qty
            order = Order(
                shop_id=shop.id,
                order_no=f"ORD-S5A-{dt:%Y%m%d}-01",
                status="paid",
                fulfillment_status="delivered",
                paid_amount=amt,
                paid_at=dt,
                created_at=dt,
            )
            created_orders.append((order, sku_coffee.id, qty, sku_coffee.current_price))

        for idx, qty in enumerate(promo_units):
            dt = promo_start + timedelta(days=idx)
            unit_p = Decimal("269.00")
            amt = unit_p * qty
            order = Order(
                shop_id=shop.id,
                order_no=f"ORD-S5A-{dt:%Y%m%d}-01",
                status="paid",
                fulfillment_status="delivered",
                paid_amount=amt,
                paid_at=dt,
                created_at=dt,
            )
            created_orders.append((order, sku_coffee.id, qty, unit_p))

        # 为 SKU-011（及 SKU-005）补充当期（11/15~11/20）订单与 1 笔成功退款
        current_glass_days = [(15, 10), (17, 12), (18, 14), (19, 15), (20, 14)]
        refund_target_order: Order | None = None
        for day_num, qty in current_glass_days:
            dt = datetime(2026, 11, day_num, 14, 30, tzinfo=TZ)
            amt = sku_glass.current_price * qty
            order = Order(
                shop_id=shop.id,
                order_no=f"ORD-S5A-202611{day_num:02d}-02",
                status="partially_refunded" if day_num == 20 else "paid",
                fulfillment_status="delivered",
                paid_amount=amt,
                paid_at=dt,
                created_at=dt,
            )
            created_orders.append((order, sku_glass.id, qty, sku_glass.current_price))
            if day_num == 20:
                refund_target_order = order

        if sku5 is not None:
            for day_num, qty in [(16, 12), (18, 14), (20, 16)]:
                dt = datetime(2026, 11, day_num, 16, 0, tzinfo=TZ)
                amt = sku5.current_price * qty
                order = Order(
                    shop_id=shop.id,
                    order_no=f"ORD-S5A-202611{day_num:02d}-03",
                    status="paid",
                    fulfillment_status="delivered",
                    paid_amount=amt,
                    paid_at=dt,
                    created_at=dt,
                )
                created_orders.append((order, sku5.id, qty, sku5.current_price))

        if sku1 is not None:
            # 仅写入 11-08 ~ 11-10 上期区间订单（不触碰 11-14 ~ 11-21！）
            for day_num, qty in [(8, 12), (9, 14), (10, 15)]:
                dt = datetime(2026, 11, day_num, 10, 30, tzinfo=TZ)
                amt = sku1.current_price * qty
                order = Order(
                    shop_id=shop.id,
                    order_no=f"ORD-S5A-202611{day_num:02d}-04",
                    status="paid",
                    fulfillment_status="delivered",
                    paid_amount=amt,
                    paid_at=dt,
                    created_at=dt,
                )
                created_orders.append((order, sku1.id, qty, sku1.current_price))

        session.add_all([item[0] for item in created_orders])
        await session.flush()

        session.add_all([
            OrderItem(
                shop_id=shop.id,
                order_id=order.id,
                sku_id=sku_id,
                quantity=qty,
                unit_price=unit_p,
                paid_amount=order.paid_amount,
                created_at=order.created_at,
            )
            for order, sku_id, qty, unit_p in created_orders
        ])

        if refund_target_order is not None:
            session.add(
                Refund(
                    organization_id=org.id,
                    shop_id=shop.id,
                    order_id=refund_target_order.id,
                    refund_no="RFD-S5A-20261120-01",
                    amount=Decimal("79.00"),
                    reason="玻璃杯轻微划痕部分退差价",
                    status="succeeded",
                    requester_id=operator.id,
                    processed_at=datetime(2026, 11, 20, 18, 0, tzinfo=TZ),
                    created_at=datetime(2026, 11, 20, 17, 20, tzinfo=TZ),
                )
            )
        created["stage5a_orders"] = True

    # 3. 补充促销日历与活动复盘示例活动（避开 SKU-001，不干扰 SKU-001 排期冲突测试）
    if await session.scalar(
        select(Promotion.id).where(Promotion.shop_id == shop.id, Promotion.promotion_no == "PRM-20261110-02")
    ) is None:
        promo_completed = Promotion(
            shop_id=shop.id,
            promotion_no="PRM-20261110-02",
            name="咖啡器具周",
            version=1,
            status="completed",
            starts_at=datetime(2026, 11, 12, 0, 0, tzinfo=TZ),
            ends_at=datetime(2026, 11, 17, 0, 0, tzinfo=TZ),
            minimum_qty=100,
            created_by=operator.id,
            created_at=datetime(2026, 11, 10, 9, 0, tzinfo=TZ),
        )
        promo_active = Promotion(
            shop_id=shop.id,
            promotion_no="PRM-20261119-01",
            name="便携水具体验周",
            version=1,
            status="active",
            starts_at=datetime(2026, 11, 19, 0, 0, tzinfo=TZ),
            ends_at=datetime(2026, 11, 23, 0, 0, tzinfo=TZ),
            minimum_qty=100,
            created_by=operator.id,
            created_at=datetime(2026, 11, 18, 10, 0, tzinfo=TZ),
        )
        session.add_all([promo_completed, promo_active])
        await session.flush()

        session.add_all([
            PromotionItem(
                shop_id=shop.id,
                promotion_id=promo_completed.id,
                sku_id=sku_coffee.id,
                original_price=sku_coffee.current_price,
                cost_price=sku_coffee.cost,
                discount_rate=Decimal("0.9000"),
                promotion_price=Decimal("269.10"),
            ),
            PromotionItem(
                shop_id=shop.id,
                promotion_id=promo_active.id,
                sku_id=sku_glass.id,
                original_price=sku_glass.current_price,
                cost_price=sku_glass.cost,
                discount_rate=Decimal("0.8500"),
                promotion_price=Decimal("67.15"),
            ),
        ])
        created["stage5a_promotions"] = True

    return created
