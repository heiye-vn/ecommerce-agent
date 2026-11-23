from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import PASSWORD_HASHER
from app.models import (
    Inventory,
    InventoryMovement,
    Membership,
    Organization,
    Product,
    Role,
    Shop,
    Sku,
    User,
)
from app.services.seed import DEMO_ORGANIZATION, DEMO_SHOP, DEMO_USERNAME
from app.services.seed_catalog import DEMO_DATE

APPROVER_USERNAME = "approver.demo"
SECOND_DEMO_SHOP = "茶饮专营店"


async def seed_stage2_sandbox(session: AsyncSession, approver_password: str) -> dict[str, bool]:
    """
    为本地沙箱补充阶段 2 演示所需的审批员、第二家授权店铺和边界测试 SKU。
    幂等可重复执行，不影响既有 seed.py / seed_catalog.py 的基础数据。
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(726003)"))
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

    created = {"approver": False, "second_shop": False, "boundary_skus": False}

    # 1. 创建独立审批员 approver.demo（组织级审批员权限）
    approver = await session.scalar(select(User).where(User.username == APPROVER_USERNAME))
    if approver is None:
        approver = User(
            username=APPROVER_USERNAME,
            name="沙箱审批员",
            password_hash=await run_in_threadpool(PASSWORD_HASHER.hash, approver_password),
        )
        session.add(approver)
        await session.flush()
        session.add(
            Membership(
                user_id=approver.id,
                organization_id=org.id,
                shop_id=None,
                role=Role.APPROVER,
            )
        )
        created["approver"] = True

    # 2. 创建第二家授权店铺「茶饮专营店」，授予 ops.demo 运营权限，方便演示多店切换与跨店拦截
    second_shop = await session.scalar(
        select(Shop).where(Shop.organization_id == org.id, Shop.name == SECOND_DEMO_SHOP)
    )
    if second_shop is None:
        second_shop = Shop(organization_id=org.id, name=SECOND_DEMO_SHOP, platform="sandbox")
        session.add(second_shop)
        await session.flush()
        session.add(
            Membership(
                user_id=demo_operator.id,
                organization_id=org.id,
                shop_id=second_shop.id,
                role=Role.OPERATOR,
            )
        )
        tea_product = Product(shop_id=second_shop.id, name="冷萃乌龙茶礼盒", category="茶饮", created_at=DEMO_DATE)
        session.add(tea_product)
        await session.flush()
        tea_sku = Sku(
            shop_id=second_shop.id,
            product_id=tea_product.id,
            sku_code="SKU-TEA-01",
            cost=Decimal("68.00"),
            current_price=Decimal("129.00"),
            created_at=DEMO_DATE,
        )
        session.add(tea_sku)
        await session.flush()
        session.add(Inventory(sku_id=tea_sku.id, available_qty=180, created_at=DEMO_DATE))
        created["second_shop"] = True

    # 3. 为轻食旗舰店补充边界测试 SKU：SKU-002（库存 99 件）与 SKU-003（下架）
    if await session.scalar(select(Sku.id).where(Sku.shop_id == demo_shop.id, Sku.sku_code == "SKU-002")) is None:
        low_product = Product(
            shop_id=demo_shop.id, name="全麦能量棒组合", category="食品", status="active", created_at=DEMO_DATE
        )
        inactive_product = Product(
            shop_id=demo_shop.id, name="季节限定坚果包", category="食品", status="inactive", created_at=DEMO_DATE
        )
        session.add_all([low_product, inactive_product])
        await session.flush()

        low_sku = Sku(
            shop_id=demo_shop.id,
            product_id=low_product.id,
            sku_code="SKU-002",
            cost=Decimal("42.00"),
            current_price=Decimal("79.00"),
            status="active",
            created_at=DEMO_DATE,
        )
        inactive_sku = Sku(
            shop_id=demo_shop.id,
            product_id=inactive_product.id,
            sku_code="SKU-003",
            cost=Decimal("35.00"),
            current_price=Decimal("69.00"),
            status="inactive",
            created_at=DEMO_DATE,
        )
        session.add_all([low_sku, inactive_sku])
        await session.flush()

        session.add_all([
            Inventory(sku_id=low_sku.id, available_qty=99, created_at=DEMO_DATE),
            InventoryMovement(
                sku_id=low_sku.id,
                movement_type="opening_balance",
                quantity=99,
                reference_id="DEMO-STAGE2-LOW",
                created_at=DEMO_DATE,
            ),
            Inventory(sku_id=inactive_sku.id, available_qty=200, created_at=DEMO_DATE),
        ])
        created["boundary_skus"] = True

    return created
