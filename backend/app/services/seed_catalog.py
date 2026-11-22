from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Inventory, InventoryMovement, Membership, Order, OrderItem, Organization, PriceHistory,
    Product, Role, Shop, Sku, User,
)
from app.services.seed import DEMO_ORGANIZATION, DEMO_SHOP, DEMO_USERNAME


SKU_CODE = "SKU-001"
DEMO_DATE = datetime(2026, 11, 21, tzinfo=ZoneInfo("Asia/Shanghai"))
DAILY_QUANTITIES = (12, 18, 15, 20, 17, 16, 22)


async def seed_catalog(session: AsyncSession) -> bool:
    await session.execute(text("SELECT pg_advisory_xact_lock(726002)"))
    shops = await session.scalars(
        select(Shop.id)
        .join(Organization, Shop.organization_id == Organization.id)
        .join(Membership, Membership.shop_id == Shop.id)
        .join(User, Membership.user_id == User.id)
        .where(
            Shop.platform == "sandbox", Shop.name == DEMO_SHOP, Shop.status == "active",
            Organization.name == DEMO_ORGANIZATION, Organization.status == "active",
            User.username == DEMO_USERNAME, User.status == "active", Membership.role == Role.OPERATOR,
        )
    )
    shop_ids = shops.all()
    if len(shop_ids) != 1:
        raise ValueError("Expected one active demo sandbox shop; initialize it with python -m scripts.seed first")
    shop_id = shop_ids[0]

    if await session.scalar(select(Sku.id).where(Sku.shop_id == shop_id, Sku.sku_code == SKU_CODE)) is not None:
        return False

    sales_start = DEMO_DATE - timedelta(days=7)
    price_changed_at = sales_start - timedelta(days=1)
    price = Decimal("199.00")
    product = Product(shop_id=shop_id, name="轻食谷物礼盒", category="食品", created_at=price_changed_at)
    session.add(product)
    await session.flush()
    sku = Sku(
        shop_id=shop_id, product_id=product.id, sku_code=SKU_CODE, cost=Decimal("119.00"),
        current_price=price, created_at=price_changed_at,
    )
    session.add(sku)
    await session.flush()

    session.add_all([
        Inventory(sku_id=sku.id, available_qty=300, created_at=DEMO_DATE),
        InventoryMovement(
            sku_id=sku.id, movement_type="opening_balance", quantity=300,
            reference_id="DEMO-20261121", created_at=DEMO_DATE,
        ),
        PriceHistory(
            sku_id=sku.id, old_price=Decimal("229.00"), new_price=price,
            changed_at=price_changed_at, created_at=price_changed_at,
        ),
    ])
    orders = []
    for offset, quantity in enumerate(DAILY_QUANTITIES):
        paid_at = sales_start + timedelta(days=offset, hours=12)
        orders.append(Order(
            shop_id=shop_id, order_no=f"DEMO-{paid_at:%Y%m%d}", status="paid",
            paid_amount=price * quantity, paid_at=paid_at, created_at=paid_at,
        ))
    session.add_all(orders)
    await session.flush()
    session.add_all([
        OrderItem(
            shop_id=shop_id, order_id=order.id, sku_id=sku.id, quantity=quantity,
            unit_price=price, paid_amount=order.paid_amount, created_at=order.created_at,
        )
        for order, quantity in zip(orders, DAILY_QUANTITIES, strict=True)
    ])
    return True
