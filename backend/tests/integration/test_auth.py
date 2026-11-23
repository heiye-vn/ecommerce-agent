import asyncio
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import jwt
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from app.core.db import create_database_engine
from app.main import app
from app.models import Order, OrderItem, Product, Sku
from app.models.org import Organization, Shop
from app.services.seed_catalog import seed_catalog

PASSWORD = "local-test-password-123"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def auth_client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        command.downgrade(config, "base")


def test_missing_and_invalid_credentials_are_rejected(auth_client: TestClient) -> None:
    for username in ("ops.demo", "unknown"):
        response = auth_client.post(
            "/api/auth/login", json={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 401
        assert "set-cookie" not in response.headers
    assert auth_client.get("/api/shops/1").status_code == 401


async def seed_and_add_other_shops(database_url: str) -> tuple[int, int]:
    from app.services.seed import seed_demo_operator

    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            assert await seed_demo_operator(session, PASSWORD) is True
        async with sessions.begin() as session:
            assert await seed_demo_operator(session, "unused-replacement-password") is False
            demo_shop = (await session.scalars(select(Shop))).one()
            unassigned_shop = Shop(
                organization_id=demo_shop.organization_id, name="同组织未授权店铺", platform="sandbox"
            )
            other_org = Organization(name="外部组织")
            session.add(other_org)
            await session.flush()
            other_shop = Shop(organization_id=other_org.id, name="未授权店铺", platform="sandbox")
            session.add_all([unassigned_shop, other_shop])
            await session.flush()
            return unassigned_shop.id, other_shop.id
    finally:
        await engine.dispose()


def test_seeded_operator_can_login_refresh_and_logout(
    auth_client: TestClient, database_url: str
) -> None:
    other_shops = asyncio.run(seed_and_add_other_shops(database_url))
    credentials = {"username": "ops.demo", "password": PASSWORD}
    denied = auth_client.post(
        "/api/auth/login", json={**credentials, "password": "wrong-password"}
    )
    assert denied.status_code == 401
    login = auth_client.post("/api/auth/login", json=credentials)
    assert login.status_code == 200
    body = login.json()
    assert body["user"]["username"] == "ops.demo"
    assert len(body["shops"]) == 1
    allowed_shop = body["shops"][0]
    assert allowed_shop["roles"] == ["operator"]
    assert "password_hash" not in body["user"]
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 15 * 60
    original_refresh = auth_client.cookies["commerce_refresh"]

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert auth_client.get(f"/api/shops/{allowed_shop['id']}", headers=headers).json() == allowed_shop
    for shop_id in other_shops:
        assert auth_client.get(f"/api/shops/{shop_id}", headers=headers).status_code == 403
    assert auth_client.post(
        "/api/auth/refresh", headers={"Origin": "https://unrelated.example"}
    ).status_code == 403
    assert auth_client.post("/api/auth/refresh").status_code == 200
    assert auth_client.post(
        "/api/auth/refresh", headers={"Cookie": f"commerce_refresh={original_refresh}"}
    ).status_code == 401
    assert auth_client.post("/api/auth/logout").status_code == 204
    assert auth_client.post("/api/auth/refresh").status_code == 401
    assert auth_client.get(f"/api/shops/{allowed_shop['id']}", headers=headers).status_code == 401


def test_logout_revokes_session_when_another_tab_just_rotated_cookie(
    auth_client: TestClient, database_url: str
) -> None:
    asyncio.run(seed_and_add_other_shops(database_url))
    login = auth_client.post("/api/auth/login", json={"username": "ops.demo", "password": PASSWORD})
    original_refresh = auth_client.cookies["commerce_refresh"]
    rotated = auth_client.post("/api/auth/refresh")
    assert rotated.status_code == 200
    new_refresh = auth_client.cookies["commerce_refresh"]

    assert auth_client.post("/api/auth/logout", headers={
        "Authorization": f"Bearer {login.json()['access_token']}",
        "Cookie": f"commerce_refresh={original_refresh}",
    }).status_code == 204
    assert auth_client.post(
        "/api/auth/refresh", headers={"Cookie": f"commerce_refresh={new_refresh}"}
    ).status_code == 401


async def seed_catalogs(database_url: str) -> tuple[int, int]:
    unassigned_shop_id, external_shop_id = await seed_and_add_other_shops(database_url)
    engine = create_database_engine(database_url)
    try:
        async with async_sessionmaker(engine).begin() as session:
            await seed_catalog(session)
            demo_shop_id = await session.scalar(select(Shop.id).where(Shop.name == "轻食旗舰店"))
            assert demo_shop_id is not None
            product = Product(shop_id=unassigned_shop_id, name="别店商品", category="食品")
            no_inventory = Product(shop_id=demo_shop_id, name="未盘点商品", category="食品", status="inactive")
            session.add_all([product, no_inventory])
            await session.flush()
            session.add(Sku(
                shop_id=unassigned_shop_id, product_id=product.id, sku_code="SKU-001",
                cost=Decimal("10.00"), current_price=Decimal("20.00"),
            ))
            session.add(Sku(
                shop_id=demo_shop_id, product_id=no_inventory.id, sku_code="SKU-002",
                cost=Decimal("10.00"), current_price=Decimal("20.00"),
            ))
    finally:
        await engine.dispose()
    return unassigned_shop_id, external_shop_id


def test_catalog_requires_session_and_only_returns_authorized_shop_rows(
    auth_client: TestClient, database_url: str
) -> None:
    denied_shop_ids = asyncio.run(seed_catalogs(database_url))
    login = auth_client.post("/api/auth/login", json={"username": "ops.demo", "password": PASSWORD})
    assert login.status_code == 200
    body = login.json()
    url = f"/api/shops/{body['shops'][0]['id']}/catalog"

    assert auth_client.get(url).status_code == 401
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    response = auth_client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == [{
        "sku_code": "SKU-001", "product_name": "轻食谷物礼盒", "category": "食品", "status": "active",
        "current_price": "199.00", "available_qty": 300, "reserved_qty": 0,
    }, {
        "sku_code": "SKU-002", "product_name": "未盘点商品", "category": "食品", "status": "inactive",
        "current_price": "20.00", "available_qty": None, "reserved_qty": None,
    }]
    for shop_id in denied_shop_ids:
        assert auth_client.get(f"/api/shops/{shop_id}/catalog", headers=headers).status_code == 403
    assert auth_client.post("/api/auth/logout", headers=headers).status_code == 204
    assert auth_client.get(url, headers=headers).status_code == 401


async def seed_order_query_cases(database_url: str) -> tuple[int, int, int]:
    other_shop_id, external_shop_id = await seed_catalogs(database_url)
    engine = create_database_engine(database_url)
    try:
        async with async_sessionmaker(engine).begin() as session:
            shop_id = await session.scalar(select(Shop.id).where(Shop.name == "轻食旗舰店"))
            assert shop_id is not None
            cases = [
                (shop_id, "已退款订单", "refunded", "2026-11-14T00:00:00+08:00", 2),
                (shop_id, "区间之前", "paid", "2026-11-13T23:59:59+08:00", 100),
                (shop_id, "区间之后", "paid", "2026-11-21T00:00:00+08:00", 100),
                (shop_id, "未支付订单", "pending", None, 100),
                (other_shop_id, "别店订单", "paid", "2026-11-14T00:00:00+08:00", 100),
            ]
            for owner, number, status, paid_at, quantity in cases:
                sku = (await session.scalars(select(Sku).where(Sku.shop_id == owner, Sku.sku_code == "SKU-001"))).one()
                amount = Decimal("199.00") * quantity if paid_at else Decimal("0.00")
                order = Order(
                    shop_id=owner, order_no=number, status=status, paid_amount=amount,
                    paid_at=datetime.fromisoformat(paid_at) if paid_at else None,
                )
                session.add(order)
                await session.flush()
                session.add(OrderItem(
                    shop_id=owner, order_id=order.id, sku_id=sku.id, quantity=quantity,
                    unit_price=Decimal("199.00"), paid_amount=amount,
                ))
            return other_shop_id, external_shop_id, order.id
    finally:
        await engine.dispose()


def test_order_reads_are_shop_scoped_and_sales_use_complete_local_days(
    auth_client: TestClient, database_url: str
) -> None:
    other_shop, external_shop, other_order = asyncio.run(seed_order_query_cases(database_url))
    login = auth_client.post("/api/auth/login", json={"username": "ops.demo", "password": PASSWORD})
    body = login.json()
    shop_id = body["shops"][0]["id"]
    orders = f"/api/shops/{shop_id}/orders"
    sales = "/sales?sku_code=SKU-001&as_of=2026-11-21"
    assert auth_client.get(orders).status_code == 401
    assert auth_client.get(f"{orders}/1").status_code == 401
    assert auth_client.get(f"/api/shops/{shop_id}{sales}").status_code == 401
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    response = auth_client.get(orders, headers=headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert len(response.json()) == 11
    order = next(row for row in response.json() if row["order_no"] == "DEMO-20261120")
    assert order["sku_codes"] == ["SKU-001"]
    assert order["quantity"] == 22
    detail = auth_client.get(f"{orders}/{order['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["items"][0] | {"id": 0} == {
        "id": 0, "sku_code": "SKU-001", "product_name": "轻食谷物礼盒", "quantity": 22,
        "unit_price": "199.00", "paid_amount": "4378.00",
    }
    assert auth_client.get(f"{orders}/{other_order}", headers=headers).status_code == 404
    assert auth_client.get("/api/shops/2147483648/orders", headers=headers).status_code == 422
    for denied_shop in (other_shop, external_shop):
        assert auth_client.get(f"/api/shops/{denied_shop}/orders", headers=headers).status_code == 403
        assert auth_client.get(f"/api/shops/{denied_shop}/orders/{other_order}", headers=headers).status_code == 403
        assert auth_client.get(f"/api/shops/{denied_shop}{sales}", headers=headers).status_code == 403
    summary = auth_client.get(f"/api/shops/{shop_id}{sales}", headers=headers)
    assert summary.status_code == 200
    assert summary.json() == {
        "sku_code": "SKU-001", "units_sold": 122, "paid_orders": 8,
        "start_date": "2026-11-14", "end_date": "2026-11-21", "timezone": "Asia/Shanghai",
    }
    no_sales = auth_client.get(
        f"/api/shops/{shop_id}/sales?sku_code=SKU-002&as_of=2026-11-21", headers=headers
    )
    assert no_sales.status_code == 200 and no_sales.json()["units_sold"] == 0
    assert auth_client.get(
        f"/api/shops/{shop_id}/sales?sku_code=UNKNOWN&as_of=2026-11-21", headers=headers
    ).status_code == 404


def test_agent_queries_sales_once_without_writing_business_data(
    auth_client: TestClient, database_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import func

    async def snapshot() -> tuple[int, Decimal]:
        engine = create_database_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                row = (await session.execute(select(func.count(Order.id), func.sum(Order.paid_amount)))).one()
                return row.count, row.sum
        finally:
            await engine.dispose()

    other_shop, _ = asyncio.run(seed_catalogs(database_url))
    before = asyncio.run(snapshot())
    login = auth_client.post("/api/auth/login", json={"username": "ops.demo", "password": PASSWORD})
    session = login.json()
    url = f"/api/shops/{session['shops'][0]['id']}/agent/query"
    prompt = {"message": "查一下 sku-001 最近 7 天销量"}
    assert auth_client.post(url, json=prompt).status_code == 401
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    assert auth_client.post(f"/api/shops/{other_shop}/agent/query", json=prompt, headers=headers).status_code == 403

    from app.agent import graph, tools
    from app.schemas.agent import QueryIntent

    calls: list[tuple[int, str]] = []
    original_sales = tools.sku_sales

    async def record_sale_query(session, shop_id, sku_code, as_of):
        calls.append((shop_id, sku_code))
        return await original_sales(session, shop_id, sku_code, as_of)

    async def classify(message: str) -> QueryIntent:
        if "促销" in message:
            return QueryIntent(kind="unsupported")
        return QueryIntent(kind="sales", sku_code="sku-001" if "sku-001" in message else None)

    monkeypatch.setattr(graph, "classify", classify)
    monkeypatch.setattr(tools, "sku_sales", record_sale_query)

    response = auth_client.post(url, json=prompt, headers=headers)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    answer = response.json()
    assert answer["kind"] == "sales" and answer["tool_calls"] == 1
    assert answer["sales"] == {
        "sku_code": "SKU-001", "units_sold": 120, "paid_orders": 7,
        "start_date": "2026-11-14", "end_date": "2026-11-21", "timezone": "Asia/Shanghai",
    }
    for message, kind in [("最近 7 天卖了多少件", "clarify"), ("查销量并创建促销", "unsupported")]:
        reply = auth_client.post(url, json={"message": message}, headers=headers).json()
        assert reply["kind"] == kind and not reply["tool_calls"] and reply["sales"] is None
    assert calls == [(session["shops"][0]["id"], "SKU-001")]
    assert asyncio.run(snapshot()) == before


def test_agent_previews_promotion_in_two_turns_with_live_inventory(
    auth_client: TestClient, database_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import func, inspect, update

    from app.agent import graph
    from app.core.security import PASSWORD_HASHER
    from app.models import (
        AgentMessage,
        AgentSession,
        AgentTask,
        AgentToolCall,
        Inventory,
        Membership,
        Role,
        User,
    )
    from app.schemas.agent import QueryIntent

    other_shop, _ = asyncio.run(seed_catalogs(database_url))

    # 为 ops.demo 增加第二家店铺权限，同时新建一个纯审批员账号验证角色守卫
    async def setup_extra_access() -> None:
        engine = create_database_engine(database_url)
        try:
            async with async_sessionmaker(engine).begin() as db:
                demo_user = (await db.scalars(select(User).where(User.username == "ops.demo"))).one()
                second_shop = (await db.scalars(select(Shop).where(Shop.id == other_shop))).one()
                db.add(
                    Membership(
                        user_id=demo_user.id,
                        organization_id=second_shop.organization_id,
                        shop_id=second_shop.id,
                        role=Role.OPERATOR,
                    )
                )
                approver = User(
                    name="只读审批员",
                    username="approver.demo",
                    password_hash=PASSWORD_HASHER.hash(PASSWORD),
                )
                db.add(approver)
                await db.flush()
                db.add(
                    Membership(
                        user_id=approver.id,
                        organization_id=second_shop.organization_id,
                        shop_id=None,
                        role=Role.APPROVER,
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(setup_extra_access())

    login = auth_client.post("/api/auth/login", json={"username": "ops.demo", "password": PASSWORD}).json()
    demo_shop = next(shop for shop in login["shops"] if shop["name"] == "轻食旗舰店")
    assert demo_shop["timezone"] == "Asia/Shanghai"
    shop_id = demo_shop["id"]
    url = f"/api/shops/{shop_id}/agent/query"
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    question = {"message": "帮我看看 SKU-001 最近 7 天销量，如果库存充足，就创建一个 8 折、持续 3 天的促销。"}

    # 纯审批员调用 Agent 必须被角色守卫拒绝
    approver_login = auth_client.post(
        "/api/auth/login", json={"username": "approver.demo", "password": PASSWORD}
    ).json()
    approver_denied = auth_client.post(
        url,
        json=question,
        headers={"Authorization": f"Bearer {approver_login['access_token']}"},
    )
    assert approver_denied.status_code == 403
    assert approver_denied.json()["code"] == "SHOP_ROLE_FORBIDDEN"

    async def classify(message: str) -> QueryIntent:
        if message == "查一下 SKU-001 最近 7 天销量":
            return QueryIntent(kind="sales", sku_code="SKU-001")
        if "打八折" in message:
            return QueryIntent(kind="promotion", sku_code="SKU-001", discount=Decimal("8"), duration_days=3)
        return QueryIntent(kind="promotion", sku_code="SKU-001")

    async def inventory(quantity: int) -> None:
        engine = create_database_engine(database_url)
        try:
            async with engine.begin() as connection:
                tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
                assert {"promotions", "promotion_items", "approvals"} <= set(tables)
                await connection.execute(update(Inventory).values(available_qty=quantity))
        finally:
            await engine.dispose()

    monkeypatch.setattr(graph, "classify", classify)
    first = auth_client.post(url, json=question, headers=headers)
    assert first.status_code == 200
    reply = first.json()
    assert reply["kind"] == "promotion_clarify"
    assert reply["session_id"] is not None and reply["task_id"] is not None
    assert reply["tool_calls"] == 1
    assert reply["sales"]["units_sold"] == 120
    assert reply["promotion"]["available_qty"] == 300
    assert reply["promotion"]["starts_at"] is None
    assert reply["promotion"]["below_cost"] is False

    # 切到同用户另一家已授权店铺后携带原 session_id，应被跨店拦截
    cross_shop = auth_client.post(
        f"/api/shops/{other_shop}/agent/query",
        json={"message": "11 月 22 日开始。", "session_id": reply["session_id"]},
        headers=headers,
    )
    assert cross_shop.status_code == 200
    assert cross_shop.json()["kind"] == "unsupported"
    assert "另一家店铺" in cross_shop.json()["message"]

    follow_up = {"message": "11 月 22 日开始。", "session_id": reply["session_id"]}
    preview = auth_client.post(url, json=follow_up, headers=headers).json()
    assert preview["kind"] == "promotion_preview"
    assert preview["tool_calls"] == 1
    assert preview["promotion"]["promotion_price"] == "159.20"
    assert preview["promotion"]["cost_price"] == "119.00"
    assert preview["promotion"]["below_cost"] is False
    assert preview["promotion"]["starts_at"] == "2026-11-22T00:00:00+08:00"
    assert preview["promotion"]["ends_at"] == "2026-11-25T00:00:00+08:00"
    assert preview["promotion"]["conflict_check"] == "clear"

    # 验证破价/负毛利风控预警（5 折 ¥99.50 < 成本 ¥119.00）
    below_cost_reply = auth_client.post(
        url,
        json={"message": "给 SKU-001 做一个 5 折、持续 3 天的促销，11 月 22 日开始"},
        headers=headers,
    ).json()
    assert below_cost_reply["kind"] == "promotion_preview"
    assert below_cost_reply["promotion"]["promotion_price"] == "99.50"
    assert below_cost_reply["promotion"]["below_cost"] is True
    assert len(below_cost_reply["promotion"]["risk_warnings"]) == 1

    # 验证 LLM 结构化槽位提取对中文口语的支持
    natural_reply = auth_client.post(
        url,
        json={"message": "帮我给 SKU-001 打八折，活动做三天"},
        headers=headers,
    ).json()
    assert natural_reply["kind"] == "promotion_clarify"
    assert natural_reply["promotion"]["promotion_price"] == "159.20"

    # 无效日期回到追问，且保留上下文；新查销量请求自动切回销量查询
    invalid = auth_client.post(
        url, json={"message": "2026-11-31", "session_id": natural_reply["session_id"]}, headers=headers
    ).json()
    assert invalid["kind"] == "promotion_clarify"
    sales_reply = auth_client.post(
        url,
        json={"message": "查一下 SKU-001 最近 7 天销量", "session_id": natural_reply["session_id"]},
        headers=headers,
    ).json()
    assert sales_reply["kind"] == "sales"

    for terms in (
        "8 折、持续 3 天，11 月 22 日或 11 月 23 日开始",
        "8 折、持续 3 天，11 月 22 日开始，上午 10 点",
        "库存超过 500 件才做 8 折、持续 3 天的促销",
    ):
        ambiguous = auth_client.post(url, json={"message": f"SKU-001 {terms}"}, headers=headers).json()
        assert ambiguous["kind"] == "clarify"
        assert ambiguous["promotion"] is None

    # 验证库存边界 99 拦截 / 100 通过（使用新会话驱动实时库存读取）
    live_session = auth_client.post(url, json=question, headers=headers).json()["session_id"]
    asyncio.run(inventory(99))
    blocked = auth_client.post(
        url, json={"message": "11 月 22 日开始。", "session_id": live_session}, headers=headers
    ).json()
    assert blocked["kind"] == "promotion_blocked"
    assert blocked["promotion"]["available_qty"] == 99

    asyncio.run(inventory(100))
    live_session_ok = auth_client.post(url, json=question, headers=headers).json()["session_id"]
    passed = auth_client.post(
        url, json={"message": "11 月 22 日开始。", "session_id": live_session_ok}, headers=headers
    ).json()
    assert passed["kind"] == "promotion_preview"

    # 验证 Agent 运行时 4 张表真实落库
    async def runtime_counts() -> tuple[int, int, int, int]:
        engine = create_database_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as db:
                sessions_cnt = await db.scalar(select(func.count(AgentSession.id)))
                messages_cnt = await db.scalar(select(func.count(AgentMessage.id)))
                tasks_cnt = await db.scalar(select(func.count(AgentTask.id)))
                tools_cnt = await db.scalar(select(func.count(AgentToolCall.id)))
                return int(sessions_cnt or 0), int(messages_cnt or 0), int(tasks_cnt or 0), int(tools_cnt or 0)
        finally:
            await engine.dispose()

    s_cnt, m_cnt, t_cnt, tc_cnt = asyncio.run(runtime_counts())
    assert s_cnt >= 3 and m_cnt >= 10 and t_cnt >= 5 and tc_cnt >= 5


def test_health_ready_returns_ok_when_postgresql_is_reachable(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 清除 Redis/RabbitMQ 配置，专注验证已连上的 PostgreSQL 真实探活
    from app.core.config import AppSettings
    from app.main import app

    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("RABBITMQ_URL", "")
    app.state.settings = AppSettings.load()

    response = auth_client.get("/api/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["postgresql"] == "ok"
