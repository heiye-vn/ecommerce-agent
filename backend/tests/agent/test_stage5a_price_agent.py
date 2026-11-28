import asyncio
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from app.agent import graph
from app.core.db import create_database_engine
from app.main import app
from app.models import Approval, PriceChangeRequest
from app.schemas.agent import QueryIntent
from app.services.seed import seed_demo_operator
from app.services.seed_catalog import seed_catalog
from app.services.seed_stage2 import SECOND_DEMO_SHOP, seed_stage2_sandbox

PASSWORD = "stage5a-agent-password-123"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def agent_client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("SANDBOX_BUSINESS_DATE", "2026-11-21")
    monkeypatch.setenv("ASYNC_EXECUTION_MODE", "outbox")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        asyncio.run(_seed_world(database_url))
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        command.downgrade(config, "base")


async def _seed_world(database_url: str) -> None:
    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            await seed_demo_operator(session, PASSWORD)
        async with sessions.begin() as session:
            await seed_catalog(session)
        async with sessions.begin() as session:
            await seed_stage2_sandbox(session, PASSWORD)
    finally:
        await engine.dispose()


def _login(client: TestClient, username: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()


async def _count_price_change_rows(database_url: str) -> tuple[int, int]:
    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            req_n = (await session.scalar(select(func.count(PriceChangeRequest.id)))) or 0
            approvals_n = (
                await session.scalar(
                    select(func.count(Approval.id)).where(Approval.resource_type == "price_change")
                )
            ) or 0
            return req_n, approvals_n
    finally:
        await engine.dispose()


def test_stage5a_price_change_agent_clarify_preview_confirm_and_guards(
    agent_client: TestClient,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stub_classify(message: str) -> QueryIntent:
        if "直接改" in message or "直接批准" in message:
            return QueryIntent(kind="unsupported")
        if "确认" in message:
            return QueryIntent(kind="confirm_plan")
        if "调价" in message or "调到" in message or "改价" in message:
            price = Decimal("189.00") if "189" in message else None
            reason = "冬季调整常规售价" if "冬季" in message else None
            return QueryIntent(
                kind="price_change",
                sku_code="SKU-001" if "SKU-001" in message else None,
                target_price=price,
                price_change_reason=reason,
            )
        return QueryIntent(kind="unsupported")

    monkeypatch.setattr(graph, "classify", stub_classify)

    ops = _login(agent_client, "ops.demo")
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    second_shop_id = next(s["id"] for s in ops["shops"] if s["name"] == SECOND_DEMO_SHOP)
    headers = {"Authorization": f"Bearer {ops['access_token']}"}

    # 1. 缺少目标售价与原因 -> 多轮追问无写入
    t1 = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "给 SKU-001 发起常规调价"},
    )
    assert t1.status_code == 200
    t1_body = t1.json()
    assert t1_body["kind"] == "price_change_clarify"
    assert t1_body["price_change_context"]["sku_code"] == "SKU-001"
    session_id = t1_body["session_id"]
    assert asyncio.run(_count_price_change_rows(database_url)) == (0, 0)

    # 2. 补充售价与原因 -> 预览方案（零写库）
    t2 = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "调到 189 元，原因冬季调整常规售价", "session_id": session_id},
    )
    assert t2.status_code == 200
    t2_body = t2.json()
    assert t2_body["kind"] == "price_change_preview"
    assert Decimal(t2_body["price_change"]["proposed_price"]) == Decimal("189.00")
    assert t2_body["pending_price_change_plan"] is not None
    assert asyncio.run(_count_price_change_rows(database_url)) == (0, 0)

    # 3. 确认提交创建 1 笔调价审批单；重复确认幂等返回同一单号；换店拒绝
    c1 = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "确认并提交调价审批", "session_id": session_id},
    )
    assert c1.status_code == 200
    c1_body = c1.json()
    assert c1_body["kind"] == "price_change_submitted"
    assert c1_body["price_change_submission"]["replayed"] is False
    assert asyncio.run(_count_price_change_rows(database_url)) == (1, 1)

    c2 = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "确认并提交调价审批", "session_id": session_id},
    )
    assert c2.status_code == 200
    c2_body = c2.json()
    assert c2_body["price_change_submission"]["replayed"] is True
    assert c2_body["price_change_submission"]["approval_no"] == c1_body["price_change_submission"]["approval_no"]
    assert asyncio.run(_count_price_change_rows(database_url)) == (1, 1)

    cross = agent_client.post(
        f"/api/shops/{second_shop_id}/agent/query",
        headers=headers,
        json={"message": "确认提交", "session_id": session_id},
    )
    assert cross.status_code == 200
    assert cross.json()["kind"] == "unsupported"
    assert asyncio.run(_count_price_change_rows(database_url)) == (1, 1)

    # 4. 拒绝直接修改线上售价或批准审批
    for prompt in ("直接把 SKU-001 售价改成 189 元不走审批", "请直接批准这份调价审批"):
        denied = agent_client.post(
            f"/api/shops/{shop_id}/agent/query",
            headers=headers,
            json={"message": prompt},
        )
        assert denied.status_code == 200
        assert denied.json()["kind"] == "unsupported"
