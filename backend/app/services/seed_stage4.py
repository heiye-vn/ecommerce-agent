from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Inventory,
    InventoryMovement,
    Order,
    OrderItem,
    Organization,
    Product,
    Shop,
    Sku,
    User,
)
from app.services.seed import DEMO_ORGANIZATION, DEMO_SHOP, DEMO_USERNAME
from app.services.seed_catalog import DEMO_DATE, SKU_CODE

LOW_STOCK_DEMO_SKU = "SKU-005"


async def seed_stage4_sandbox(session: AsyncSession) -> dict[str, bool]:
    """
    为本地沙箱补充阶段 4 演练数据：低库存预警 SKU、历史库存流水样本及异常/取消边界订单。
    幂等可重复执行，不影响既有的 7 天销量基准（120 件）与阶段 2 演示数据。
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(726004)"))
    org = await session.scalar(
        select(Organization).where(Organization.name == DEMO_ORGANIZATION, Organization.status == "active")
    )
    if org is None:
        raise ValueError("Demo organization not found; run python -m scripts.seed first")

    demo_shop = await session.scalar(
        select(Shop).where(Shop.organization_id == org.id, Shop.name == DEMO_SHOP, Shop.status == "active")
    )
    demo_operator = await session.scalar(
        select(User).where(User.username == DEMO_USERNAME, User.status == "active")
    )
    if demo_shop is None or demo_operator is None:
        raise ValueError("Demo shop or operator not found; run python -m scripts.seed first")

    created = {"low_stock_sku": False, "exception_orders": False}

    existing_low_sku = await session.scalar(
        select(Sku).where(Sku.shop_id == demo_shop.id, Sku.sku_code == LOW_STOCK_DEMO_SKU)
    )
    if existing_low_sku is None:
        product = Product(
            shop_id=demo_shop.id,
            name="硅胶杯盖",
            category="配件",
            status="active",
            created_at=DEMO_DATE,
        )
        session.add(product)
        await session.flush()

        low_sku = Sku(
            shop_id=demo_shop.id,
            product_id=product.id,
            sku_code=LOW_STOCK_DEMO_SKU,
            cost=Decimal("11.00"),
            current_price=Decimal("29.00"),
            status="active",
            created_at=DEMO_DATE,
        )
        session.add(low_sku)
        await session.flush()

        session.add(
            Inventory(
                sku_id=low_sku.id,
                available_qty=18,
                reserved_qty=2,
                version=2,
                created_at=DEMO_DATE,
            )
        )
        session.add_all([
            InventoryMovement(
                shop_id=demo_shop.id,
                sku_id=low_sku.id,
                movement_type="restock",
                quantity=20,
                available_delta=20,
                reserved_delta=0,
                before_available=0,
                after_available=20,
                before_reserved=0,
                after_reserved=0,
                reference_id="STK-20261120-01",
                actor_id=demo_operator.id,
                note="阶段 4 沙箱初始化入库",
                created_at=DEMO_DATE - timedelta(hours=12),
            ),
            InventoryMovement(
                shop_id=demo_shop.id,
                sku_id=low_sku.id,
                movement_type="reserve",
                quantity=2,
                available_delta=-2,
                reserved_delta=2,
                before_available=20,
                after_available=18,
                before_reserved=0,
                after_reserved=2,
                reference_id="ORD-20261111-01",
                actor_id=demo_operator.id,
                note="待发货订单预占库存",
                created_at=DEMO_DATE - timedelta(hours=2),
            ),
        ])
        created["low_stock_sku"] = True

    sku1 = await session.scalar(
        select(Sku).where(Sku.shop_id == demo_shop.id, Sku.sku_code == SKU_CODE)
    )
    if sku1 is not None:
        existing_exception = await session.scalar(
            select(Order).where(Order.shop_id == demo_shop.id, Order.order_no == "ORD-20261111-01")
        )
        if existing_exception is None:
            # 避开 11-14 ~ 11-21 七天销量窗口，确保 SKU-001 最近 7 天销量仍严格保持 120 件
            early_paid_at = DEMO_DATE - timedelta(days=10)
            review_order = Order(
                shop_id=demo_shop.id,
                order_no="ORD-20261111-01",
                status="paid",
                fulfillment_status="pending_shipment",
                exception_type="inventory_review",
                paid_amount=Decimal("398.00"),
                paid_at=early_paid_at,
                created_at=early_paid_at,
            )
            cancelled_order = Order(
                shop_id=demo_shop.id,
                order_no="ORD-20261111-02",
                status="cancelled",
                fulfillment_status="cancelled",
                exception_type="buyer_cancelled",
                paid_amount=Decimal("199.00"),
                paid_at=None,
                created_at=early_paid_at + timedelta(hours=1),
            )
            session.add_all([review_order, cancelled_order])
            await session.flush()
            session.add_all([
                OrderItem(
                    shop_id=demo_shop.id,
                    order_id=review_order.id,
                    sku_id=sku1.id,
                    quantity=2,
                    unit_price=Decimal("199.00"),
                    paid_amount=Decimal("398.00"),
                    created_at=early_paid_at,
                ),
                OrderItem(
                    shop_id=demo_shop.id,
                    order_id=cancelled_order.id,
                    sku_id=sku1.id,
                    quantity=1,
                    unit_price=Decimal("199.00"),
                    paid_amount=Decimal("199.00"),
                    created_at=early_paid_at + timedelta(hours=1),
                ),
            ])
            created["exception_orders"] = True

    return created
