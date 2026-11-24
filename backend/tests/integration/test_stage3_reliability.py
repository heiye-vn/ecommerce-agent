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
from app.main import app
from app.models import AuditLog, Job, Notification, OutboxEvent, Promotion, Sku
from app.services.jobs import TransientJobError
from app.services.seed import seed_demo_operator
from app.services.seed_catalog import seed_catalog
from app.services.seed_stage2 import APPROVER_USERNAME, seed_stage2_sandbox
from app.workers.consumer import AfterCommitHook, handle_incoming_message
from app.workers.outbox_publisher import publish_pending_batch
from app.workers.scheduler import scan_due_promotions
from app.workers.topology import TopologyConfig, connect_rabbitmq, declare_topology

PASSWORD = "stage3-test-password-123"


def _rabbitmq_url() -> str:
    return read_setting("RABBITMQ_URL") or "amqp://commerce_agent:commerce_agent@localhost:7602/"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def stage3_client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RABBITMQ_URL", _rabbitmq_url())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("SANDBOX_BUSINESS_DATE", "2026-11-21")
    monkeypatch.setenv("ASYNC_EXECUTION_MODE", "outbox")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        asyncio.run(_seed_stage3_world(database_url))
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        command.downgrade(config, "base")


async def _seed_stage3_world(database_url: str) -> None:
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


def _isolated_topology() -> TopologyConfig:
    uid = uuid4().hex[:10]
    return TopologyConfig(
        exchange_name=f"test.stage3.{uid}.events",
        dlx_name=f"test.stage3.{uid}.dlx",
        queue_name=f"test.stage3.{uid}.queue",
        dlq_name=f"test.stage3.{uid}.dlq",
        dlq_routing_key="promotion.dlq",
    )


async def _drain_outbox_and_consume(
    database_url: str,
    topo: TopologyConfig,
    *,
    business_date: date = date(2026, 11, 21),
    fault_injector=None,
    after_commit_hook: AfterCommitHook | None = None,
    backoff_base_seconds: int = 0,
    max_cycles: int = 10,
) -> tuple[list[str], list[str]]:
    engine = create_database_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    clock = BusinessClock(business_date)
    connection = await connect_rabbitmq(_rabbitmq_url())
    outcomes: list[str] = []
    dlq_message_ids: list[str] = []
    try:
        async with connection:
            channel = await connection.channel(publisher_confirms=True)
            declared = await declare_topology(channel, topo)
            try:
                for _ in range(max_cycles):
                    published = await publish_pending_batch(
                        session_factory,
                        declared.exchange,
                        backoff_base_seconds=backoff_base_seconds,
                    )
                    consumed_any = False
                    while True:
                        msg = await declared.queue.get(timeout=2, fail=False)
                        if msg is None:
                            break
                        consumed_any = True
                        outcome = await handle_incoming_message(
                            msg,
                            session_factory,
                            clock,
                            fault_injector=fault_injector,
                            after_commit_hook=after_commit_hook,
                            backoff_base_seconds=backoff_base_seconds,
                        )
                        outcomes.append(outcome)
                    if published == 0 and not consumed_any:
                        break
                while True:
                    dlq_msg = await declared.dlq.get(timeout=1, fail=False)
                    if dlq_msg is None:
                        break
                    if dlq_msg.message_id:
                        dlq_message_ids.append(dlq_msg.message_id)
                    await dlq_msg.ack()
            finally:
                await declared.queue.delete(if_unused=False, if_empty=False)
                await declared.dlq.delete(if_unused=False, if_empty=False)
                await declared.exchange.delete(if_unused=False)
                await declared.dlx.delete(if_unused=False)
    finally:
        await engine.dispose()
    return outcomes, dlq_message_ids


def test_outbox_rabbitmq_consumer_and_notification_flow(
    stage3_client: TestClient, database_url: str
) -> None:
    ops = _login(stage3_client, "ops.demo")
    approver = _login(stage3_client, APPROVER_USERNAME)
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}

    created = stage3_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={
            "name": "Stage3 异步排期测试",
            "sku_code": "SKU-001",
            "discount": "8",
            "start_date": "2026-11-22",
            "duration_days": 3,
        },
        headers=ops_headers,
    ).json()

    # 审批员应收到待审批通知，并可读取未读列表
    approver_notifs = stage3_client.get(
        f"/api/shops/{shop_id}/notifications?unread_only=true", headers=approver_headers
    ).json()
    assert approver_notifs["unread_count"] == 1
    assert approver_notifs["items"][0]["resource_id"] == created["approval_id"]

    # Outbox 模式下批准直接入队并返回 job_id/job_no
    approved = stage3_client.post(
        f"/api/shops/{shop_id}/approvals/{created['approval_id']}/approve",
        json={"comment": "异步批准"},
        headers=approver_headers,
    ).json()
    assert approved["execution_status"] == "queued"
    assert approved["job_id"] is not None and approved["job_no"].startswith("JOB-")
    assert approved["approval"]["resource_status"] == "approved"

    # 运行真实 Outbox Publisher -> RabbitMQ -> Consumer 消费链路
    topo = _isolated_topology()
    outcomes, dlq_ids = asyncio.run(_drain_outbox_and_consume(database_url, topo))
    assert outcomes == ["scheduled"]
    assert dlq_ids == []

    promo_detail = stage3_client.get(
        f"/api/shops/{shop_id}/promotions/{created['promotion_id']}", headers=ops_headers
    ).json()
    assert promo_detail["status"] == "scheduled"

    job_detail = stage3_client.get(
        f"/api/shops/{shop_id}/jobs/{approved['job_id']}", headers=ops_headers
    ).json()
    assert job_detail["status"] == "succeeded"
    assert job_detail["can_retry"] is False

    # 运营应收到审批通过 + 排期成功通知，并验证单条/全部已读接口
    ops_notifs = stage3_client.get(f"/api/shops/{shop_id}/notifications", headers=ops_headers).json()
    assert ops_notifs["unread_count"] == 2
    first_id = ops_notifs["items"][0]["id"]
    marked = stage3_client.post(
        f"/api/shops/{shop_id}/notifications/{first_id}/read", headers=ops_headers
    ).json()
    assert marked["is_read"] is True
    read_all = stage3_client.post(
        f"/api/shops/{shop_id}/notifications/read-all", headers=ops_headers
    ).json()
    assert read_all["updated_count"] == 1


def test_consumer_crash_before_ack_redelivers_idempotently(
    stage3_client: TestClient, database_url: str
) -> None:
    """验证第 9 节核心验收点：提交数据库事务后、ACK 前中断，重投后仍只有一次业务状态变更。"""
    ops = _login(stage3_client, "ops.demo")
    approver = _login(stage3_client, APPROVER_USERNAME)
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}

    created = stage3_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={
            "name": "ACK前崩溃重投幂等测试",
            "sku_code": "SKU-001",
            "discount": "8",
            "start_date": "2026-11-22",
            "duration_days": 3,
        },
        headers=ops_headers,
    ).json()
    approved = stage3_client.post(
        f"/api/shops/{shop_id}/approvals/{created['approval_id']}/approve",
        json={"comment": "批准"},
        headers=approver_headers,
    ).json()

    delivered = 0

    async def simulate_crash_before_first_ack(message) -> None:
        nonlocal delivered
        delivered += 1
        if delivered == 1:
            # DB 事务已提交，但在 ACK 前连接中断 → RabbitMQ 重新入队投递
            await message.nack(requeue=True)
            return
        await message.ack()

    topo = _isolated_topology()
    outcomes, dlq_ids = asyncio.run(
        _drain_outbox_and_consume(
            database_url,
            topo,
            after_commit_hook=simulate_crash_before_first_ack,
        )
    )
    assert delivered == 2
    assert outcomes == ["after_commit_hook", "after_commit_hook"]
    assert dlq_ids == []

    async def verify_once() -> tuple[str, str, int, int]:
        engine = create_database_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as db:
                promo = (
                    await db.scalars(select(Promotion).where(Promotion.id == created["promotion_id"]))
                ).one()
                job = (await db.scalars(select(Job).where(Job.id == approved["job_id"]))).one()
                sched_audit_count = await db.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.resource_id == promo.id,
                        AuditLog.action == "promotion.scheduled",
                    )
                )
                sched_notif_count = await db.scalar(
                    select(func.count(Notification.id)).where(
                        Notification.resource_id == promo.id,
                        Notification.title.like("%已成功排期%"),
                    )
                )
                return promo.status, job.status, int(sched_audit_count or 0), int(sched_notif_count or 0)
        finally:
            await engine.dispose()

    promo_status, job_status, audit_cnt, notif_cnt = asyncio.run(verify_once())
    assert promo_status == "scheduled"
    assert job_status == "succeeded"
    assert audit_cnt == 1
    assert notif_cnt == 1


def test_transient_failures_dlq_manual_retry_and_business_failure_guard(
    stage3_client: TestClient, database_url: str
) -> None:
    ops = _login(stage3_client, "ops.demo")
    approver = _login(stage3_client, APPROVER_USERNAME)
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}

    # 1. 提交并通过审批：注入连续 3 次暂态故障使其进死信队列
    created = stage3_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={
            "name": "死信人工重试测试",
            "sku_code": "SKU-001",
            "discount": "8",
            "start_date": "2026-11-22",
            "duration_days": 3,
        },
        headers=ops_headers,
    ).json()
    approved = stage3_client.post(
        f"/api/shops/{shop_id}/approvals/{created['approval_id']}/approve",
        json={"comment": "批准"},
        headers=approver_headers,
    ).json()

    def always_transient_fail(_job: Job) -> None:
        raise TransientJobError("下游排期网关超时")

    topo = _isolated_topology()
    outcomes, dlq_ids = asyncio.run(
        _drain_outbox_and_consume(
            database_url,
            topo,
            fault_injector=always_transient_fail,
            backoff_base_seconds=0,
        )
    )
    assert outcomes == ["retrying", "retrying", "dead_lettered"]
    assert len(dlq_ids) == 1

    # 验证处于 dead_lettered，活动保持 approved
    dlq_job = stage3_client.get(
        f"/api/shops/{shop_id}/jobs/{approved['job_id']}", headers=ops_headers
    ).json()
    assert dlq_job["status"] == "dead_lettered"
    assert dlq_job["attempts"] == 3
    assert dlq_job["can_retry"] is True
    assert dlq_job["resource_status"] == "approved"

    needs_attention = stage3_client.get(
        f"/api/shops/{shop_id}/jobs?tab=needs_attention", headers=ops_headers
    ).json()
    assert any(item["id"] == approved["job_id"] for item in needs_attention)

    # 2. 人工点击按原操作重试后正常消费成功
    retried = stage3_client.post(
        f"/api/shops/{shop_id}/jobs/{approved['job_id']}/retry", headers=ops_headers
    )
    assert retried.status_code == 200
    assert retried.json()["job"]["status"] == "pending"

    topo_retry = _isolated_topology()
    retry_outcomes, retry_dlq = asyncio.run(_drain_outbox_and_consume(database_url, topo_retry))
    assert retry_outcomes == ["scheduled"] and retry_dlq == []

    promo_after = stage3_client.get(
        f"/api/shops/{shop_id}/promotions/{created['promotion_id']}", headers=ops_headers
    ).json()
    assert promo_after["status"] == "scheduled"
    assert any(log["action"] == "job.dead_lettered" for log in promo_after["audit_logs"])
    assert any(log["action"] == "job.manual_retry" for log in promo_after["audit_logs"])

    # 3. 业务复核失败（如涨价导致 failed）的作业禁止直接重试
    biz_fail_promo = stage3_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={
            "name": "业务复核失败不可重试",
            "sku_code": "SKU-001",
            "discount": "8",
            "start_date": "2026-12-01",
            "duration_days": 2,
        },
        headers=ops_headers,
    ).json()
    biz_fail_approved = stage3_client.post(
        f"/api/shops/{shop_id}/approvals/{biz_fail_promo['approval_id']}/approve",
        json={"comment": "批准"},
        headers=approver_headers,
    ).json()

    async def raise_sku_price() -> None:
        engine = create_database_engine(database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    update(Sku)
                    .where(Sku.shop_id == shop_id, Sku.sku_code == "SKU-001")
                    .values(current_price=Decimal("219.00"))
                )
        finally:
            await engine.dispose()

    asyncio.run(raise_sku_price())
    topo_biz = _isolated_topology()
    biz_outcomes, _ = asyncio.run(_drain_outbox_and_consume(database_url, topo_biz))
    assert biz_outcomes == ["failed"]

    rejected_retry = stage3_client.post(
        f"/api/shops/{shop_id}/jobs/{biz_fail_approved['job_id']}/retry", headers=ops_headers
    )
    assert rejected_retry.status_code == 409
    assert rejected_retry.json()["code"] == "JOB_BUSINESS_FAILURE_NOT_RETRYABLE"


def test_scheduler_activates_completes_and_recovers_missed_windows(
    stage3_client: TestClient, database_url: str
) -> None:
    ops = _login(stage3_client, "ops.demo")
    approver = _login(stage3_client, APPROVER_USERNAME)
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}

    # 创建两档不重叠促销：活动 A（11/22–11/25）正常生效与结束；活动 B（11/25–11/28）模拟停机错过窗口
    promo_a = stage3_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={
            "name": "到点生效活动",
            "sku_code": "SKU-001",
            "discount": "8",
            "start_date": "2026-11-22",
            "duration_days": 3,
        },
        headers=ops_headers,
    ).json()
    promo_b = stage3_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={
            "name": "错过时段活动",
            "sku_code": "SKU-001",
            "discount": "8.5",
            "start_date": "2026-11-25",
            "duration_days": 3,
        },
        headers=ops_headers,
    ).json()

    for apr_id in (promo_a["approval_id"], promo_b["approval_id"]):
        res = stage3_client.post(
            f"/api/shops/{shop_id}/approvals/{apr_id}/approve",
            json={"comment": "批准排期"},
            headers=approver_headers,
        )
        assert res.status_code == 200

    # 先执行排期任务使两活动进入 scheduled
    asyncio.run(_drain_outbox_and_consume(database_url, _isolated_topology()))

    async def run_scan(target_date: date) -> int:
        engine = create_database_engine(database_url)
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            return await scan_due_promotions(sessions, BusinessClock(target_date))
        finally:
            await engine.dispose()

    # 1. 时钟推进到 2026-11-22：连续扫描两次，仅创建 1 条激活任务（幂等）
    assert asyncio.run(run_scan(date(2026, 11, 22))) == 1
    assert asyncio.run(run_scan(date(2026, 11, 22))) == 0
    outcomes_act, _ = asyncio.run(
        _drain_outbox_and_consume(database_url, _isolated_topology(), business_date=date(2026, 11, 22))
    )
    assert outcomes_act == ["active"]

    detail_a = stage3_client.get(
        f"/api/shops/{shop_id}/promotions/{promo_a['promotion_id']}", headers=ops_headers
    ).json()
    assert detail_a["status"] == "active"

    # 2. 时钟推进到 2026-11-29（跨越 11/25 与 11/28）：活动 A 到期结束，活动 B 错过生效时段直接结束
    assert asyncio.run(run_scan(date(2026, 11, 29))) == 2
    outcomes_end, _ = asyncio.run(
        _drain_outbox_and_consume(database_url, _isolated_topology(), business_date=date(2026, 11, 29))
    )
    assert outcomes_end == ["completed", "completed"]

    detail_a_end = stage3_client.get(
        f"/api/shops/{shop_id}/promotions/{promo_a['promotion_id']}", headers=ops_headers
    ).json()
    detail_b_end = stage3_client.get(
        f"/api/shops/{shop_id}/promotions/{promo_b['promotion_id']}", headers=ops_headers
    ).json()
    assert detail_a_end["status"] == "completed"
    assert any(log["action"] == "promotion.activated" for log in detail_a_end["audit_logs"])
    assert any(log["action"] == "promotion.completed" for log in detail_a_end["audit_logs"])

    assert detail_b_end["status"] == "completed"
    assert "错过生效时段" in (detail_b_end["failure_reason"] or "")
    assert any(log["action"] == "promotion.missed_window" for log in detail_b_end["audit_logs"])

    # 验证 Outbox 事件均已投递完毕
    async def pending_outbox_count() -> int:
        engine = create_database_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as db:
                return int(
                    (
                        await db.scalar(
                            select(func.count(OutboxEvent.id)).where(OutboxEvent.status != "published")
                        )
                    )
                    or 0
                )
        finally:
            await engine.dispose()

    assert asyncio.run(pending_outbox_count()) == 0
