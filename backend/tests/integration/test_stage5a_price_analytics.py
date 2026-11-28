import asyncio
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from app.core.clock import BusinessClock
from app.core.db import create_database_engine, read_setting
from app.core.errors import AppError
from app.main import app
from app.models import Job, PriceHistory, Sku
from app.schemas.price_changes import PriceChangeCreateRequest
from app.services.jobs import TransientJobError
from app.services.price_changes import assert_price_change_transition, submit_price_change
from app.services.seed import seed_demo_operator
from app.services.seed_catalog import seed_catalog
from app.services.seed_stage2 import APPROVER_USERNAME, SECOND_DEMO_SHOP, seed_stage2_sandbox
from app.services.seed_stage4 import seed_stage4_sandbox
from app.services.seed_stage5a import seed_stage5a_sandbox
from app.workers.consumer import handle_incoming_message
from app.workers.outbox_publisher import publish_pending_batch
from app.workers.topology import TopologyConfig, connect_rabbitmq, declare_topology

PASSWORD = "stage5a-test-password-123"


def _rabbitmq_url() -> str:
    return read_setting("RABBITMQ_URL") or "amqp://commerce_agent:commerce_agent@localhost:7602/"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def stage5a_client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RABBITMQ_URL", _rabbitmq_url())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("SANDBOX_BUSINESS_DATE", "2026-11-21")
    monkeypatch.setenv("ASYNC_EXECUTION_MODE", "outbox")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        asyncio.run(_seed_stage5a_world(database_url))
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        command.downgrade(config, "base")


async def _seed_stage5a_world(database_url: str) -> None:
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
        async with sessions.begin() as session:
            await seed_stage5a_sandbox(session)
    finally:
        await engine.dispose()


def _login(client: TestClient, username: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()


def _isolated_stage5a_topology() -> TopologyConfig:
    uid = uuid4().hex[:10]
    return TopologyConfig(
        exchange_name=f"test.stage5a.{uid}.events",
        dlx_name=f"test.stage5a.{uid}.dlx",
        queue_name=f"test.stage5a.{uid}.promo.queue",
        dlq_name=f"test.stage5a.{uid}.promo.dlq",
        price_change_queue_name=f"test.stage5a.{uid}.price.queue",
        price_change_dlq_name=f"test.stage5a.{uid}.price.dlq",
    )


async def _drain_price_change_queue(
    database_url: str,
    topo: TopologyConfig,
    *,
    business_date: date = date(2026, 11, 21),
    fault_injector=None,
    backoff_base_seconds: int = 0,
    max_cycles: int = 10,
) -> tuple[list[str], list[str]]:
    engine = create_database_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    clock = BusinessClock(business_date)
    connection = await connect_rabbitmq(_rabbitmq_url())
    outcomes: list[str] = []
    dlq_ids: list[str] = []
    try:
        async with connection:
            channel = await connection.channel(publisher_confirms=True)
            declared = await declare_topology(channel, topo)
            assert declared.price_change_queue is not None
            assert declared.price_change_dlq is not None
            try:
                for _ in range(max_cycles):
                    published = await publish_pending_batch(
                        session_factory,
                        declared.exchange,
                        backoff_base_seconds=backoff_base_seconds,
                    )
                    consumed_any = False
                    while True:
                        msg = await declared.price_change_queue.get(timeout=2, fail=False)
                        if msg is None:
                            break
                        consumed_any = True
                        outcome = await handle_incoming_message(
                            msg,
                            session_factory,
                            clock,
                            fault_injector=fault_injector,
                            backoff_base_seconds=backoff_base_seconds,
                        )
                        outcomes.append(outcome)
                    if published == 0 and not consumed_any:
                        break
                while True:
                    dlq_msg = await declared.price_change_dlq.get(timeout=1, fail=False)
                    if dlq_msg is None:
                        break
                    if dlq_msg.message_id:
                        dlq_ids.append(dlq_msg.message_id)
                    await dlq_msg.ack()
            finally:
                await declared.queue.delete(if_unused=False, if_empty=False)
                await declared.dlq.delete(if_unused=False, if_empty=False)
                await declared.price_change_queue.delete(if_unused=False, if_empty=False)
                await declared.price_change_dlq.delete(if_unused=False, if_empty=False)
                await declared.exchange.delete(if_unused=False)
                await declared.dlx.delete(if_unused=False)
    finally:
        await engine.dispose()
    return outcomes, dlq_ids


def test_price_change_lifecycle_drift_guard_concurrency_and_dlq(
    stage5a_client: TestClient, database_url: str
) -> None:
    ops = _login(stage5a_client, "ops.demo")
    approver = _login(stage5a_client, APPROVER_USERNAME)
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    second_shop_id = next(s["id"] for s in ops["shops"] if s["name"] == SECOND_DEMO_SHOP)
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}

    # 1. 规则拦截：低于成本、未在售 SKU、原价未变、非法状态流转
    below_cost = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes",
        headers=ops_headers,
        json={"sku_code": "SKU-001", "new_price": "99.00", "reason": "尝试低于成本调价"},
    )
    assert below_cost.status_code == 422
    assert below_cost.json()["code"] == "PRICE_BELOW_COST"

    inactive_sku = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes",
        headers=ops_headers,
        json={"sku_code": "SKU-003", "new_price": "59.00", "reason": "下架商品调价拦截"},
    )
    assert inactive_sku.status_code == 409
    assert inactive_sku.json()["code"] == "SKU_NOT_ACTIVE"

    unchanged = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes",
        headers=ops_headers,
        json={"sku_code": "SKU-001", "new_price": "199.00", "reason": "同价提交拦截"},
    )
    assert unchanged.status_code == 422
    assert unchanged.json()["code"] == "PRICE_UNCHANGED"

    with pytest.raises(AppError) as bad_state:
        assert_price_change_transition("succeeded", "pending_approval")
    assert bad_state.value.code == "INVALID_PRICE_CHANGE_STATE"

    # 2. 幂等重放 + 同键异参冲突 + 撤回闭环
    submit_1 = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes",
        headers={**ops_headers, "Idempotency-Key": "prc-idem-sku1-01"},
        json={"sku_code": "SKU-001", "new_price": "189.00", "reason": "调整冬季常规售价"},
    )
    assert submit_1.status_code == 200
    s1_body = submit_1.json()
    assert s1_body["replayed"] is False
    assert s1_body["status"] == "pending_approval"

    replay_1 = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes",
        headers={**ops_headers, "Idempotency-Key": "prc-idem-sku1-01"},
        json={"sku_code": "SKU-001", "new_price": "189.00", "reason": "调整冬季常规售价"},
    )
    assert replay_1.status_code == 200
    assert replay_1.json()["replayed"] is True
    assert replay_1.json()["request_no"] == s1_body["request_no"]

    conflict_1 = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes",
        headers={**ops_headers, "Idempotency-Key": "prc-idem-sku1-01"},
        json={"sku_code": "SKU-001", "new_price": "179.00", "reason": "改价同键异参"},
    )
    assert conflict_1.status_code == 409

    # 在途期间重复提单被拦截 -> 撤回后可重新发起
    dup_in_flight = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes",
        headers={**ops_headers, "Idempotency-Key": "prc-idem-sku1-dup"},
        json={"sku_code": "SKU-001", "new_price": "185.00", "reason": "同SKU并发拦截"},
    )
    assert dup_in_flight.status_code == 409
    assert dup_in_flight.json()["code"] == "PRICE_CHANGE_IN_FLIGHT_EXISTS"

    cancelled = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes/{s1_body['request_id']}/cancel",
        headers=ops_headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["approval_status"] == "cancelled"

    # 3. 重提 SKU-001 调价 -> 禁止跨店审批 -> 审批通过 -> RabbitMQ 队列执行改价并写历史流水
    submit_2 = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes",
        headers={**ops_headers, "Idempotency-Key": "prc-idem-sku1-02"},
        json={"sku_code": "SKU-001", "new_price": "189.00", "reason": "调整冬季常规售价"},
    )
    assert submit_2.status_code == 200
    s2_body = submit_2.json()

    cross_approve = stage5a_client.post(
        f"/api/shops/{second_shop_id}/approvals/{s2_body['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "尝试跨店审批调价"},
    )
    assert cross_approve.status_code == 403

    approve_2 = stage5a_client.post(
        f"/api/shops/{shop_id}/approvals/{s2_body['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "同意冬季调价"},
    )
    assert approve_2.status_code == 200
    a2_body = approve_2.json()
    assert a2_body["execution_status"] == "queued"

    outcomes_2, _ = asyncio.run(_drain_price_change_queue(database_url, _isolated_stage5a_topology()))
    assert outcomes_2 == ["succeeded"]

    detail_2 = stage5a_client.get(
        f"/api/shops/{shop_id}/price-changes/{s2_body['request_id']}",
        headers=ops_headers,
    ).json()
    assert detail_2["status"] == "succeeded"
    assert Decimal(detail_2["current_price"]) == Decimal("189.00")

    histories = stage5a_client.get(
        f"/api/shops/{shop_id}/price-changes/histories",
        params={"sku_code": "SKU-001"},
        headers=ops_headers,
    ).json()
    assert Decimal(histories[0]["old_price"]) == Decimal("199.00")
    assert Decimal(histories[0]["new_price"]) == Decimal("189.00")
    assert histories[0]["approval_no"] == s2_body["approval_no"]
    assert histories[0]["reason"] == "调整冬季常规售价"

    # 4. 原售价变化复核失败场景（原型 54）：批准后执行前数据库现价已漂移 -> 标记 failed 且不改价、禁止重试
    drift_submit = stage5a_client.post(
        f"/api/shops/{shop_id}/price-changes",
        headers={**ops_headers, "Idempotency-Key": "prc-drift-sku11"},
        json={"sku_code": "SKU-011", "new_price": "69.00", "reason": "测试原价变化复核拦截"},
    ).json()
    stage5a_client.post(
        f"/api/shops/{shop_id}/approvals/{drift_submit['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "批准后模拟原价变化"},
    )

    async def _mutate_sku11_price() -> None:
        engine = create_database_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as session:
                await session.execute(
                    update(Sku)
                    .where(Sku.shop_id == shop_id, Sku.sku_code == "SKU-011")
                    .values(current_price=Decimal("75.00"))
                )
        finally:
            await engine.dispose()

    asyncio.run(_mutate_sku11_price())
    drift_outcomes, _ = asyncio.run(_drain_price_change_queue(database_url, _isolated_stage5a_topology()))
    assert drift_outcomes == ["failed"]

    drift_detail = stage5a_client.get(
        f"/api/shops/{shop_id}/price-changes/{drift_submit['request_id']}",
        headers=ops_headers,
    ).json()
    assert drift_detail["status"] == "failed"
    assert "¥79.00" in drift_detail["failure_reason"]
    assert "¥75.00" in drift_detail["failure_reason"]
    assert Decimal(drift_detail["current_price"]) == Decimal("75.00")

    bad_retry = stage5a_client.post(
        f"/api/shops/{shop_id}/jobs/{drift_detail['job_id']}/retry",
        headers=ops_headers,
    )
    assert bad_retry.status_code == 409
    assert bad_retry.json()["code"] == "JOB_BUSINESS_FAILURE_NOT_RETRYABLE"

    # 5. asyncio.gather 并发提交同一 SKU + 3次暂态异常入 DLQ 后人工重试最终成功
    async def _concurrent_price_submits() -> tuple[int, int]:
        engine = create_database_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async def _submit_one(key: str) -> str:
                async with sessions.begin() as session:
                    try:
                        await submit_price_change(
                            session,
                            organization_id=ops["shops"][0]["organization_id"],
                            shop_id=shop_id,
                            shop_name="轻食旗舰店",
                            actor_id=ops["user"]["id"],
                            business_day=date(2026, 11, 21),
                            payload=PriceChangeCreateRequest(
                                sku_code="SKU-018",
                                new_price=Decimal("279.00"),
                                reason="并发提交调价测试",
                            ),
                            idempotency_key=key,
                        )
                        return "ok"
                    except AppError as exc:
                        return exc.code

            r1, r2 = await asyncio.gather(_submit_one("conc-prc-1"), _submit_one("conc-prc-2"))
            return [r1, r2].count("ok"), [r1, r2].count("PRICE_CHANGE_IN_FLIGHT_EXISTS")
        finally:
            await engine.dispose()

    ok_n, conflict_n = asyncio.run(_concurrent_price_submits())
    assert ok_n == 1
    assert conflict_n == 1

    sku18_req = stage5a_client.get(
        f"/api/shops/{shop_id}/price-changes",
        params={"sku_code": "SKU-018"},
        headers=ops_headers,
    ).json()[0]
    approve_18 = stage5a_client.post(
        f"/api/shops/{shop_id}/approvals/{sku18_req['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "DLQ 重试恢复验证"},
    ).json()

    def _always_transient(_job: Job) -> None:
        raise TransientJobError("模拟价格下游连接抖动")

    dlq_outcomes, dlq_ids = asyncio.run(
        _drain_price_change_queue(
            database_url,
            _isolated_stage5a_topology(),
            fault_injector=_always_transient,
            backoff_base_seconds=0,
        )
    )
    assert dlq_outcomes == ["retrying", "retrying", "dead_lettered"]
    assert len(dlq_ids) == 1

    retried = stage5a_client.post(
        f"/api/shops/{shop_id}/jobs/{approve_18['job_id']}/retry",
        headers=ops_headers,
    )
    assert retried.status_code == 200
    assert retried.json()["job"]["status"] == "pending"

    recovery_outcomes, _ = asyncio.run(
        _drain_price_change_queue(database_url, _isolated_stage5a_topology())
    )
    assert recovery_outcomes == ["succeeded"]

    sku18_after = stage5a_client.get(
        f"/api/shops/{shop_id}/price-changes/{sku18_req['id']}",
        headers=ops_headers,
    ).json()
    assert sku18_after["status"] == "succeeded"
    assert Decimal(sku18_after["current_price"]) == Decimal("279.00")

    async def _count_sku18_history() -> int:
        engine = create_database_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                return int(
                    (
                        await session.scalar(
                            select(func.count(PriceHistory.id)).where(
                                PriceHistory.price_change_request_id == sku18_req["id"]
                            )
                        )
                    )
                    or 0
                )
        finally:
            await engine.dispose()

    assert asyncio.run(_count_sku18_history()) == 1


def test_analytics_overview_daily_report_turnover_and_promotion_review(
    stage5a_client: TestClient,
) -> None:
    ops = _login(stage5a_client, "ops.demo")
    approver = _login(stage5a_client, APPROVER_USERNAME)
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    second_shop_id = next(s["id"] for s in ops["shops"] if s["name"] == SECOND_DEMO_SHOP)
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}

    # 1. 红线基线验证：SKU-001 近 7 天销量严格维持 120 件不变
    sku1_sales = stage5a_client.get(
        f"/api/shops/{shop_id}/sales",
        params={"sku_code": "SKU-001", "as_of": "2026-11-21"},
        headers=ops_headers,
    )
    assert sku1_sales.status_code == 200
    assert sku1_sales.json()["units_sold"] == 120

    # 2. 经营看板（选项 A 客单价 = paid_gmv / paid_orders）与 7 日走势
    overview_resp = stage5a_client.get(
        f"/api/shops/{shop_id}/analytics/overview",
        params={"days": 7, "as_of": "2026-11-21"},
        headers=ops_headers,
    )
    assert overview_resp.status_code == 200
    ov = overview_resp.json()
    assert len(ov["trend"]) == 7
    assert ov["paid_orders"] > 0
    assert Decimal(ov["refund_rate"]) > Decimal("0.00")
    assert ov["sku_ranking"][0]["sku_code"] == "SKU-001"
    assert ov["sku_ranking"][0]["units_sold"] == 120
    expected_aov = (Decimal(ov["paid_gmv"]) / Decimal(ov["paid_orders"])).quantize(Decimal("0.01"))
    assert Decimal(ov["avg_order_value"]) == expected_aov

    # 3. 经营日报（默认 2026-11-20 完整自然日，清晰分离数据库事实与分析边界）
    daily_resp = stage5a_client.get(
        f"/api/shops/{shop_id}/analytics/daily-report",
        params={"report_date": "2026-11-20"},
        headers=ops_headers,
    )
    assert daily_resp.status_code == 200
    daily = daily_resp.json()
    assert daily["metrics"]["refunded_orders"] == 1
    assert any("SKU-005" in fact for fact in daily["facts"])
    assert "因果" in daily["analysis_boundary"]

    # 4. 库存周转与异常指标
    turnover_resp = stage5a_client.get(
        f"/api/shops/{shop_id}/analytics/inventory-turnover",
        params={"days": 7, "as_of": "2026-11-21"},
        headers=ops_headers,
    )
    assert turnover_resp.status_code == 200
    tv = turnover_resp.json()
    assert tv["low_stock_count"] >= 1
    sku5_row = next(item for item in tv["items"] if item["sku_code"] == "SKU-005")
    assert sku5_row["low_stock"] is True
    assert sku5_row["available_qty"] == 18
    assert any(anom["kind"] == "low_stock" and "SKU-005" in anom["title"] for anom in tv["anomalies"])

    # 5. 促销活动复盘（PRM-20261110-02 咖啡器具周：5 日活动 98 件 vs 5 日基线 46 件）
    promos = stage5a_client.get(f"/api/shops/{shop_id}/promotions", headers=ops_headers).json()
    coffee_promo = next(p for p in promos if p["promotion_no"] == "PRM-20261110-02")
    review_resp = stage5a_client.get(
        f"/api/shops/{shop_id}/promotions/{coffee_promo['id']}/review",
        headers=ops_headers,
    )
    assert review_resp.status_code == 200
    rv = review_resp.json()
    assert rv["duration_days"] == 5
    assert rv["promo_units"] == 98
    assert rv["baseline_units"] == 46
    assert len(rv["daily_comparison"]) == 5
    assert [pt["day_label"] for pt in rv["daily_comparison"]] == ["D1", "D2", "D3", "D4", "D5"]
    assert "分析推测" in rv["review_note"]

    # 6. 跨店隔离：尝试用「茶饮专营店」读取轻食旗舰店促销复盘应返回 404；非授权店铺访问分析返回 403
    cross_promo = stage5a_client.get(
        f"/api/shops/{second_shop_id}/promotions/{coffee_promo['id']}/review",
        headers=approver_headers,
    )
    assert cross_promo.status_code == 404
