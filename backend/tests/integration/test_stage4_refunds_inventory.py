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
from app.core.security import PASSWORD_HASHER
from app.main import app
from app.models import (
    Inventory,
    InventoryMovement,
    Job,
    Membership,
    Order,
    Role,
    Shop,
    Sku,
    User,
)
from app.schemas.inventory import InventoryMovementCreateRequest
from app.schemas.refunds import RefundCreateRequest
from app.services.inventory import apply_inventory_movement
from app.services.jobs import TransientJobError
from app.services.refunds import assert_refund_transition, submit_refund
from app.services.seed import seed_demo_operator
from app.services.seed_catalog import seed_catalog
from app.services.seed_stage2 import APPROVER_USERNAME, SECOND_DEMO_SHOP, seed_stage2_sandbox
from app.services.seed_stage4 import seed_stage4_sandbox
from app.workers.consumer import AfterCommitHook, handle_incoming_message
from app.workers.outbox_publisher import publish_pending_batch
from app.workers.topology import TopologyConfig, connect_rabbitmq, declare_topology

PASSWORD = "stage4-test-password-123"


def _rabbitmq_url() -> str:
    return read_setting("RABBITMQ_URL") or "amqp://commerce_agent:commerce_agent@localhost:7602/"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def stage4_client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RABBITMQ_URL", _rabbitmq_url())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("SANDBOX_BUSINESS_DATE", "2026-11-21")
    monkeypatch.setenv("ASYNC_EXECUTION_MODE", "outbox")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        asyncio.run(_seed_stage4_world(database_url))
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        command.downgrade(config, "base")


async def _seed_stage4_world(database_url: str) -> None:
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
            shop = (await session.scalars(select(Shop).where(Shop.name == "轻食旗舰店"))).one()
            dual_user = User(
                name="双角色测试账号",
                username="dual.demo",
                password_hash=PASSWORD_HASHER.hash(PASSWORD),
            )
            session.add(dual_user)
            await session.flush()
            session.add_all([
                Membership(
                    user_id=dual_user.id,
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    role=Role.OPERATOR,
                ),
                Membership(
                    user_id=dual_user.id,
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    role=Role.APPROVER,
                ),
            ])
    finally:
        await engine.dispose()


def _login(client: TestClient, username: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()


def _isolated_stage4_topology() -> TopologyConfig:
    uid = uuid4().hex[:10]
    return TopologyConfig(
        exchange_name=f"test.stage4.{uid}.events",
        dlx_name=f"test.stage4.{uid}.dlx",
        queue_name=f"test.stage4.{uid}.promo.queue",
        dlq_name=f"test.stage4.{uid}.promo.dlq",
        refund_queue_name=f"test.stage4.{uid}.refund.queue",
        refund_dlq_name=f"test.stage4.{uid}.refund.dlq",
    )


async def _drain_refund_queue_and_consume(
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
            assert declared.refund_queue is not None
            assert declared.refund_dlq is not None
            try:
                for _ in range(max_cycles):
                    published = await publish_pending_batch(
                        session_factory,
                        declared.exchange,
                        backoff_base_seconds=backoff_base_seconds,
                    )
                    consumed_any = False
                    while True:
                        msg = await declared.refund_queue.get(timeout=2, fail=False)
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
                    dlq_msg = await declared.refund_dlq.get(timeout=1, fail=False)
                    if dlq_msg is None:
                        break
                    if dlq_msg.message_id:
                        dlq_message_ids.append(dlq_msg.message_id)
                    await dlq_msg.ack()
            finally:
                await declared.queue.delete(if_unused=False, if_empty=False)
                await declared.dlq.delete(if_unused=False, if_empty=False)
                await declared.refund_queue.delete(if_unused=False, if_empty=False)
                await declared.refund_dlq.delete(if_unused=False, if_empty=False)
                await declared.exchange.delete(if_unused=False)
                await declared.dlx.delete(if_unused=False)
    finally:
        await engine.dispose()
    return outcomes, dlq_message_ids


def test_refund_outbox_rabbitmq_partial_to_full_and_idempotency(
    stage4_client: TestClient, database_url: str
) -> None:
    ops = _login(stage4_client, "ops.demo")
    approver = _login(stage4_client, APPROVER_USERNAME)
    dual = _login(stage4_client, "dual.demo")
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    second_shop_id = next(s["id"] for s in ops["shops"] if s["name"] == SECOND_DEMO_SHOP)
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}
    dual_headers = {"Authorization": f"Bearer {dual['access_token']}"}

    orders_resp = stage4_client.get(f"/api/shops/{shop_id}/orders", headers=ops_headers)
    assert orders_resp.status_code == 200
    review_order = next(o for o in orders_resp.json() if o["order_no"] == "ORD-20261111-01")
    cancelled_order = next(o for o in orders_resp.json() if o["order_no"] == "ORD-20261111-02")
    assert review_order["exception_type"] == "inventory_review"

    # 1. 已取消订单拦截 + 超额退款拦截 + 状态机非法流转校验
    bad_cancel = stage4_client.post(
        f"/api/shops/{shop_id}/refunds",
        headers=ops_headers,
        json={"order_id": cancelled_order["id"], "amount": "50.00", "reason": "尝试对取消单退款"},
    )
    assert bad_cancel.status_code == 409
    assert bad_cancel.json()["code"] == "ORDER_NOT_REFUNDABLE"

    too_much = stage4_client.post(
        f"/api/shops/{shop_id}/refunds",
        headers=ops_headers,
        json={"order_id": review_order["id"], "amount": "999.00", "reason": "金额超限测试"},
    )
    assert too_much.status_code == 422
    assert too_much.json()["code"] == "REFUND_AMOUNT_EXCEEDS_REMAINING"

    with pytest.raises(AppError) as invalid_state_exc:
        assert_refund_transition("succeeded", "pending_approval")
    assert invalid_state_exc.value.code == "INVALID_REFUND_STATE"

    # 2. 幂等重放 + 同键异参冲突 + 在途重复提交拦截
    idem_key = "stage4-refund-partial-01"
    first_submit = stage4_client.post(
        f"/api/shops/{shop_id}/refunds",
        headers={**ops_headers, "Idempotency-Key": idem_key},
        json={"order_id": review_order["id"], "amount": "100.00", "reason": "部分破损退差价"},
    )
    assert first_submit.status_code == 200
    first_body = first_submit.json()
    assert first_body["replayed"] is False
    assert first_body["status"] == "pending_approval"

    replay_submit = stage4_client.post(
        f"/api/shops/{shop_id}/refunds",
        headers={**ops_headers, "Idempotency-Key": idem_key},
        json={"order_id": review_order["id"], "amount": "100.00", "reason": "部分破损退差价"},
    )
    assert replay_submit.status_code == 200
    assert replay_submit.json()["replayed"] is True
    assert replay_submit.json()["refund_id"] == first_body["refund_id"]
    assert replay_submit.json()["approval_id"] == first_body["approval_id"]

    conflict_submit = stage4_client.post(
        f"/api/shops/{shop_id}/refunds",
        headers={**ops_headers, "Idempotency-Key": idem_key},
        json={"order_id": review_order["id"], "amount": "120.00", "reason": "同键异参"},
    )
    assert conflict_submit.status_code == 409
    assert conflict_submit.json()["code"] == "IDEMPOTENCY_CONFLICT"

    duplicate_in_flight = stage4_client.post(
        f"/api/shops/{shop_id}/refunds",
        headers={**ops_headers, "Idempotency-Key": "stage4-refund-dup-01"},
        json={"order_id": review_order["id"], "amount": "50.00", "reason": "重复提交验证"},
    )
    assert duplicate_in_flight.status_code == 409
    assert duplicate_in_flight.json()["code"] == "REFUND_IN_FLIGHT_EXISTS"

    # 3. 跨店审批拦截 + 批准进入 RabbitMQ Outbox 异步执行部分退款
    cross_shop = stage4_client.post(
        f"/api/shops/{second_shop_id}/approvals/{first_body['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "跨店测试"},
    )
    assert cross_shop.status_code == 403
    assert cross_shop.json()["code"] == "CROSS_SHOP_APPROVAL_DENIED"

    no_role = stage4_client.post(
        f"/api/shops/{shop_id}/approvals/{first_body['approval_id']}/approve",
        headers=ops_headers,
        json={"comment": "无权限测试"},
    )
    assert no_role.status_code == 403
    assert no_role.json()["code"] == "SHOP_ROLE_FORBIDDEN"

    approve_resp = stage4_client.post(
        f"/api/shops/{shop_id}/approvals/{first_body['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "准予部分退款"},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["execution_status"] == "queued"
    job1_id = approve_resp.json()["job_id"]

    outcomes, dlq_ids = asyncio.run(_drain_refund_queue_and_consume(database_url, _isolated_stage4_topology()))
    assert outcomes == ["succeeded"]
    assert dlq_ids == []

    order_detail_1 = stage4_client.get(
        f"/api/shops/{shop_id}/orders/{review_order['id']}", headers=ops_headers
    ).json()
    assert order_detail_1["status"] == "partially_refunded"
    assert Decimal(order_detail_1["refunded_amount"]) == Decimal("100.00")
    assert Decimal(order_detail_1["refundable_amount"]) == Decimal("298.00")
    assert order_detail_1["can_refund"] is True

    refund_detail_1 = stage4_client.get(
        f"/api/shops/{shop_id}/refunds/{first_body['refund_id']}", headers=ops_headers
    ).json()
    assert refund_detail_1["status"] == "succeeded"
    assert refund_detail_1["job_id"] == job1_id
    assert refund_detail_1["job_status"] == "succeeded"

    # 4. 第二笔由 dual.demo 提交余款并测自审拦截，再由 approver.demo 批准退完至 refunded
    second_submit = stage4_client.post(
        f"/api/shops/{shop_id}/refunds",
        headers={**dual_headers, "Idempotency-Key": "stage4-refund-remaining-02"},
        json={"order_id": review_order["id"], "amount": "298.00", "reason": "退完剩余货款"},
    )
    assert second_submit.status_code == 200
    second_body = second_submit.json()

    self_approve = stage4_client.post(
        f"/api/shops/{shop_id}/approvals/{second_body['approval_id']}/approve",
        headers=dual_headers,
        json={"comment": "尝试自审"},
    )
    assert self_approve.status_code == 403
    assert self_approve.json()["code"] == "SELF_APPROVAL_FORBIDDEN"

    approve_2 = stage4_client.post(
        f"/api/shops/{shop_id}/approvals/{second_body['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "准予退清余款"},
    )
    assert approve_2.status_code == 200

    outcomes_2, _ = asyncio.run(_drain_refund_queue_and_consume(database_url, _isolated_stage4_topology()))
    assert outcomes_2 == ["succeeded"]

    order_detail_2 = stage4_client.get(
        f"/api/shops/{shop_id}/orders/{review_order['id']}", headers=ops_headers
    ).json()
    assert order_detail_2["status"] == "refunded"
    assert Decimal(order_detail_2["refunded_amount"]) == Decimal("398.00")
    assert Decimal(order_detail_2["refundable_amount"]) == Decimal("0.00")
    assert order_detail_2["can_refund"] is False

    # 校验通知中心同时收到审批与退款完成通知
    notifs = stage4_client.get(f"/api/shops/{shop_id}/notifications", headers=ops_headers).json()
    assert any(item["category"] == "refund" and item["resource_type"] == "refund" for item in notifs["items"])


def test_concurrent_refund_submission_crash_idempotency_and_dlq_retry(
    stage4_client: TestClient, database_url: str
) -> None:
    ops = _login(stage4_client, "ops.demo")
    approver = _login(stage4_client, APPROVER_USERNAME)
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}

    orders_resp = stage4_client.get(f"/api/shops/{shop_id}/orders", headers=ops_headers).json()
    demo_orders = [o for o in orders_resp if o["status"] == "paid" and o["order_no"].startswith("DEMO-")]
    target_a = demo_orders[0]
    target_b = demo_orders[1]
    target_c = demo_orders[2]

    # 1. asyncio.gather 并发对同一订单提交不同幂等键退款：严格 1 成功、1 返回 REFUND_IN_FLIGHT_EXISTS
    async def _concurrent_refunds() -> tuple[int, int]:
        engine = create_database_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async def _submit_one(key: str) -> str:
                async with sessions.begin() as session:
                    try:
                        await submit_refund(
                            session,
                            organization_id=ops["shops"][0]["organization_id"],
                            shop_id=shop_id,
                            shop_name="轻食旗舰店",
                            actor_id=ops["user"]["id"],
                            business_day=date(2026, 11, 21),
                            payload=RefundCreateRequest(
                                order_id=target_a["id"],
                                amount=Decimal("50.00"),
                                reason="并发申请退款验证",
                            ),
                            idempotency_key=key,
                        )
                        return "ok"
                    except AppError as exc:
                        return exc.code

            r1, r2 = await asyncio.gather(_submit_one("conc-rfd-1"), _submit_one("conc-rfd-2"))
            results = [r1, r2]
            return results.count("ok"), results.count("REFUND_IN_FLIGHT_EXISTS")
        finally:
            await engine.dispose()

    ok_count, conflict_count = asyncio.run(_concurrent_refunds())
    assert ok_count == 1
    assert conflict_count == 1

    # 2. 批准 target_a 退款后模拟提交事务后、ACK 前崩溃重投：业务状态只执行 1 次
    refunds_list = stage4_client.get(f"/api/shops/{shop_id}/refunds", headers=ops_headers).json()
    target_a_refund = next(r for r in refunds_list if r["order_id"] == target_a["id"])
    stage4_client.post(
        f"/api/shops/{shop_id}/approvals/{target_a_refund['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "崩溃重投验证"},
    )
    redelivered = False

    async def _crash_once_before_ack(msg) -> None:
        nonlocal redelivered
        if not redelivered:
            redelivered = True
            await msg.nack(requeue=True)
        else:
            await msg.ack()

    crash_outcomes, _ = asyncio.run(
        _drain_refund_queue_and_consume(
            database_url,
            _isolated_stage4_topology(),
            after_commit_hook=_crash_once_before_ack,
        )
    )
    assert crash_outcomes == ["after_commit_hook", "after_commit_hook"]
    order_a_after = stage4_client.get(
        f"/api/shops/{shop_id}/orders/{target_a['id']}", headers=ops_headers
    ).json()
    assert Decimal(order_a_after["refunded_amount"]) == Decimal("50.00")

    # 3. 3 次瞬态异常进入退款 DLQ，恢复依赖后人工重试最终成功
    sub_b = stage4_client.post(
        f"/api/shops/{shop_id}/refunds",
        headers={**ops_headers, "Idempotency-Key": "stage4-dlq-b"},
        json={"order_id": target_b["id"], "amount": "60.00", "reason": "死信人工重试测试"},
    ).json()
    approve_b = stage4_client.post(
        f"/api/shops/{shop_id}/approvals/{sub_b['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "准予退款"},
    ).json()
    job_b_id = approve_b["job_id"]

    def _always_transient(_job: Job) -> None:
        raise TransientJobError("模拟沙箱退款网关超时")

    dlq_outcomes, dlq_ids = asyncio.run(
        _drain_refund_queue_and_consume(
            database_url,
            _isolated_stage4_topology(),
            fault_injector=_always_transient,
            backoff_base_seconds=0,
        )
    )
    assert dlq_outcomes == ["retrying", "retrying", "dead_lettered"]
    assert len(dlq_ids) == 1

    retry_resp = stage4_client.post(
        f"/api/shops/{shop_id}/jobs/{job_b_id}/retry",
        headers=ops_headers,
    )
    assert retry_resp.status_code == 200
    assert retry_resp.json()["job"]["status"] == "pending"

    recover_outcomes, _ = asyncio.run(
        _drain_refund_queue_and_consume(database_url, _isolated_stage4_topology())
    )
    assert recover_outcomes == ["succeeded"]
    refund_b_after = stage4_client.get(
        f"/api/shops/{shop_id}/refunds/{sub_b['refund_id']}", headers=ops_headers
    ).json()
    assert refund_b_after["status"] == "succeeded"

    # 4. 二次复核失败（批准后余额已被耗尽）进入 failed，且禁止人工直接重试
    sub_c = stage4_client.post(
        f"/api/shops/{shop_id}/refunds",
        headers={**ops_headers, "Idempotency-Key": "stage4-fail-c"},
        json={"order_id": target_c["id"], "amount": "100.00", "reason": "二次复核失败测试"},
    ).json()
    approve_c = stage4_client.post(
        f"/api/shops/{shop_id}/approvals/{sub_c['approval_id']}/approve",
        headers=approver_headers,
        json={"comment": "准予执行"},
    ).json()
    job_c_id = approve_c["job_id"]

    async def _exhaust_order_c() -> None:
        engine = create_database_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as session:
                await session.execute(
                    update(Order).where(Order.id == target_c["id"]).values(status="refunded")
                )
        finally:
            await engine.dispose()

    asyncio.run(_exhaust_order_c())
    fail_outcomes, _ = asyncio.run(
        _drain_refund_queue_and_consume(database_url, _isolated_stage4_topology())
    )
    assert fail_outcomes == ["failed"]

    blocked_retry = stage4_client.post(
        f"/api/shops/{shop_id}/jobs/{job_c_id}/retry",
        headers=ops_headers,
    )
    assert blocked_retry.status_code == 409
    assert blocked_retry.json()["code"] == "JOB_BUSINESS_FAILURE_NOT_RETRYABLE"


def test_inventory_concurrency_optimistic_lock_and_movements(
    stage4_client: TestClient, database_url: str
) -> None:
    ops = _login(stage4_client, "ops.demo")
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    org_id = ops["shops"][0]["organization_id"]
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}

    # 1. 库存概览（含 SKU-005 低库存预警）
    overview = stage4_client.get(f"/api/shops/{shop_id}/inventory", headers=ops_headers)
    assert overview.status_code == 200
    overview_body = overview.json()
    assert overview_body["low_stock_count"] >= 1
    sku5 = next(item for item in overview_body["items"] if item["sku_code"] == "SKU-005")
    assert sku5["available_qty"] == 18
    assert sku5["low_stock"] is True

    # 2. 调整测试库存为可售 10 件，发起 5 个并发预占各 3 件压测：严格 3 成功、2 失败
    async def _set_test_sku_stock_and_race() -> tuple[int, int, int, int, int, int]:
        engine = create_database_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as session:
                sku5_id = (
                    await session.scalars(
                        select(Sku.id).where(Sku.shop_id == shop_id, Sku.sku_code == "SKU-005")
                    )
                ).one()
                await session.execute(
                    update(Inventory)
                    .where(Inventory.sku_id == sku5_id)
                    .values(available_qty=10, reserved_qty=0, version=10)
                )
                before_mv_count = (
                    await session.scalar(
                        select(func.count(InventoryMovement.id)).where(
                            InventoryMovement.sku_id == sku5_id
                        )
                    )
                ) or 0

            async def _reserve_worker(idx: int) -> str:
                async with sessions.begin() as session:
                    try:
                        await apply_inventory_movement(
                            session,
                            organization_id=org_id,
                            shop_id=shop_id,
                            sku_code="SKU-005",
                            actor_id=ops["user"]["id"],
                            payload=InventoryMovementCreateRequest(
                                movement_type="reserve",
                                quantity=3,
                                reference_id=f"ORD-CONC-{idx}",
                                note="并发预占压测",
                            ),
                        )
                        return "ok"
                    except AppError as exc:
                        return exc.code

            results = await asyncio.gather(*(_reserve_worker(i) for i in range(5)))
            async with sessions() as session:
                inv = (
                    await session.scalars(
                        select(Inventory).where(Inventory.sku_id == sku5_id)
                    )
                ).one()
                after_mv_count = (
                    await session.scalar(
                        select(func.count(InventoryMovement.id)).where(
                            InventoryMovement.sku_id == sku5_id
                        )
                    )
                ) or 0
                return (
                    results.count("ok"),
                    results.count("INSUFFICIENT_INVENTORY"),
                    inv.available_qty,
                    inv.reserved_qty,
                    inv.version,
                    after_mv_count - before_mv_count,
                )
        finally:
            await engine.dispose()

    ok_n, fail_n, avail, reserved, ver, mv_delta = asyncio.run(_set_test_sku_stock_and_race())
    assert ok_n == 3
    assert fail_n == 2
    assert avail == 1
    assert reserved == 9
    assert ver == 13
    assert mv_delta == 3

    # 3. 乐观锁版本冲突拦截 + 释放/履约/补货流水与幂等重放
    stale_resp = stage4_client.post(
        f"/api/shops/{shop_id}/inventory/SKU-005/movements",
        headers={**ops_headers, "Idempotency-Key": "inv-stale-1"},
        json={
            "movement_type": "release",
            "quantity": 2,
            "reference_id": "ORD-STALE-01",
            "expected_version": 12,
        },
    )
    assert stale_resp.status_code == 409
    assert stale_resp.json()["code"] == "INVENTORY_VERSION_CONFLICT"

    release_resp = stage4_client.post(
        f"/api/shops/{shop_id}/inventory/SKU-005/movements",
        headers={**ops_headers, "Idempotency-Key": "inv-release-1"},
        json={
            "movement_type": "release",
            "quantity": 2,
            "reference_id": "ORD-REL-01",
            "expected_version": 13,
            "note": "取消预占释放库存",
        },
    )
    assert release_resp.status_code == 200
    assert release_resp.json()["snapshot"]["available_qty"] == 3
    assert release_resp.json()["snapshot"]["reserved_qty"] == 7
    assert release_resp.json()["snapshot"]["version"] == 14

    replay_release = stage4_client.post(
        f"/api/shops/{shop_id}/inventory/SKU-005/movements",
        headers={**ops_headers, "Idempotency-Key": "inv-release-1"},
        json={
            "movement_type": "release",
            "quantity": 2,
            "reference_id": "ORD-REL-01",
            "expected_version": 13,
            "note": "取消预占释放库存",
        },
    )
    assert replay_release.status_code == 200
    assert replay_release.json()["replayed"] is True
    assert replay_release.json()["snapshot"]["version"] == 14

    fulfill_resp = stage4_client.post(
        f"/api/shops/{shop_id}/inventory/SKU-005/movements",
        headers={**ops_headers, "Idempotency-Key": "inv-fulfill-1"},
        json={
            "movement_type": "fulfill",
            "quantity": 4,
            "reference_id": "ORD-FUL-01",
            "expected_version": 14,
        },
    )
    assert fulfill_resp.status_code == 200
    assert fulfill_resp.json()["snapshot"]["available_qty"] == 3
    assert fulfill_resp.json()["snapshot"]["reserved_qty"] == 3
    assert fulfill_resp.json()["snapshot"]["version"] == 15

    restock_resp = stage4_client.post(
        f"/api/shops/{shop_id}/inventory/SKU-005/movements",
        headers={**ops_headers, "Idempotency-Key": "inv-restock-1"},
        json={
            "movement_type": "restock",
            "quantity": 10,
            "reference_id": "STK-RESTOCK-01",
            "expected_version": 15,
        },
    )
    assert restock_resp.status_code == 200
    assert restock_resp.json()["snapshot"]["available_qty"] == 13
    assert restock_resp.json()["snapshot"]["version"] == 16

    movements = stage4_client.get(
        f"/api/shops/{shop_id}/inventory/movements?sku_code=SKU-005",
        headers=ops_headers,
    )
    assert movements.status_code == 200
    assert len(movements.json()) >= 6
