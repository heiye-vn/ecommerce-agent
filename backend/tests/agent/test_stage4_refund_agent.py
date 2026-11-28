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
from app.models import Approval, Refund
from app.schemas.agent import QueryIntent
from app.services.seed import seed_demo_operator
from app.services.seed_catalog import seed_catalog
from app.services.seed_stage2 import SECOND_DEMO_SHOP, seed_stage2_sandbox
from app.services.seed_stage4 import seed_stage4_sandbox

PASSWORD = "stage4-agent-password-123"


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
        async with sessions.begin() as session:
            await seed_stage4_sandbox(session)
    finally:
        await engine.dispose()


def _login(client: TestClient, username: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()


async def _count_refund_rows(database_url: str) -> tuple[int, int]:
    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            refunds_n = (await session.scalar(select(func.count(Refund.id)))) or 0
            approvals_n = (
                await session.scalar(
                    select(func.count(Approval.id)).where(Approval.resource_type == "refund")
                )
            ) or 0
            return refunds_n, approvals_n
    finally:
        await engine.dispose()


def test_stage4_order_diagnosis_refund_clarify_confirm_and_cross_shop_guard(
    agent_client: TestClient,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stub_classify(message: str) -> QueryIntent:
        if "批准" in message or "扣除库存" in message or "预占库存" in message:
            return QueryIntent(kind="unsupported")
        if "异常订单" in message:
            return QueryIntent(kind="order_query", exception_only=True)
        if "查看订单" in message:
            return QueryIntent(kind="order_query", order_no="ORD-20261111-01")
        if "确认" in message:
            return QueryIntent(kind="confirm_plan")
        if "退款" in message or "退" in message:
            amount = Decimal("89.00") if "89" in message else None
            reason = "部分商品破损" if "破损" in message else None
            return QueryIntent(
                kind="refund",
                order_no="ORD-20261111-01" if "ORD-20261111-01" in message else None,
                refund_amount=amount,
                refund_reason=reason,
            )
        return QueryIntent(kind="unsupported")

    monkeypatch.setattr(graph, "classify", stub_classify)

    ops = _login(agent_client, "ops.demo")
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    second_shop_id = next(s["id"] for s in ops["shops"] if s["name"] == SECOND_DEMO_SHOP)
    headers = {"Authorization": f"Bearer {ops['access_token']}"}

    # 1. 只读异常订单排查与单笔订单查询，零数据库写入
    ex_resp = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "帮我看下有哪些异常订单需要处理"},
    )
    assert ex_resp.status_code == 200
    ex_body = ex_resp.json()
    assert ex_body["kind"] == "order_info"
    assert ex_body["order_result"]["mode"] == "exception_list"
    assert any(item["order_no"] == "ORD-20261111-01" for item in ex_body["order_result"]["items"])
    assert asyncio.run(_count_refund_rows(database_url)) == (0, 0)

    detail_resp = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "查看订单 ORD-20261111-01 的售后资格"},
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["kind"] == "order_info"
    assert detail_resp.json()["order_result"]["items"][0]["can_refund"] is True
    assert asyncio.run(_count_refund_rows(database_url)) == (0, 0)

    # 2. 发起退款缺少金额/原因 -> 多轮追问无写入 -> 补齐生成预览方案
    turn_1 = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "给订单 ORD-20261111-01 申请退款"},
    )
    assert turn_1.status_code == 200
    t1_body = turn_1.json()
    assert t1_body["kind"] == "refund_clarify"
    assert t1_body["refund_context"]["order_no"] == "ORD-20261111-01"
    session_id = t1_body["session_id"]
    assert asyncio.run(_count_refund_rows(database_url)) == (0, 0)

    turn_2 = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "退 89 元，原因部分商品破损", "session_id": session_id},
    )
    assert turn_2.status_code == 200
    t2_body = turn_2.json()
    assert t2_body["kind"] == "refund_preview"
    assert Decimal(t2_body["refund"]["proposed_amount"]) == Decimal("89.00")
    assert t2_body["refund"]["reason"] == "部分商品破损"
    assert t2_body["pending_refund_plan"] is not None
    assert asyncio.run(_count_refund_rows(database_url)) == (0, 0)

    # 3. 确认提交创建 1 条退款审批单；重复确认幂等返回同一单号；换店拦截
    confirm_1 = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "确认并提交退款审批", "session_id": session_id},
    )
    assert confirm_1.status_code == 200
    c1_body = confirm_1.json()
    assert c1_body["kind"] == "refund_submitted"
    assert c1_body["refund_submission"]["replayed"] is False
    assert asyncio.run(_count_refund_rows(database_url)) == (1, 1)

    confirm_2 = agent_client.post(
        f"/api/shops/{shop_id}/agent/query",
        headers=headers,
        json={"message": "确认并提交退款审批", "session_id": session_id},
    )
    assert confirm_2.status_code == 200
    c2_body = confirm_2.json()
    assert c2_body["kind"] == "refund_submitted"
    assert c2_body["refund_submission"]["replayed"] is True
    assert c2_body["refund_submission"]["approval_no"] == c1_body["refund_submission"]["approval_no"]
    assert asyncio.run(_count_refund_rows(database_url)) == (1, 1)

    cross_shop_confirm = agent_client.post(
        f"/api/shops/{second_shop_id}/agent/query",
        headers=headers,
        json={"message": "确认提交", "session_id": session_id},
    )
    assert cross_shop_confirm.status_code == 200
    assert cross_shop_confirm.json()["kind"] == "unsupported"
    assert asyncio.run(_count_refund_rows(database_url)) == (1, 1)

    # 4. 拒绝要求 Agent 直接批准退款或预占库存
    for forbidden_prompt in ("请直接批准这笔退款审批", "帮我直接预占库存 5 件"):
        denied = agent_client.post(
            f"/api/shops/{shop_id}/agent/query",
            headers=headers,
            json={"message": forbidden_prompt},
        )
        assert denied.status_code == 200
        assert denied.json()["kind"] == "unsupported"
