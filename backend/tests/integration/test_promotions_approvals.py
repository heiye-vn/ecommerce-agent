import asyncio
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from app.core.db import create_database_engine
from app.core.security import PASSWORD_HASHER
from app.main import app
from app.models import Approval, AuditLog, Membership, Promotion, Role, Shop, Sku, User
from app.services.approvals import decide_approval
from app.services.seed import seed_demo_operator
from app.services.seed_catalog import seed_catalog
from app.services.seed_stage2 import APPROVER_USERNAME, SECOND_DEMO_SHOP, seed_stage2_sandbox

PASSWORD = "stage2-test-password-123"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def stage2_client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("SANDBOX_BUSINESS_DATE", "2026-11-21")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        asyncio.run(_seed_stage2_world(database_url))
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        command.downgrade(config, "base")


async def _seed_stage2_world(database_url: str) -> None:
    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            await seed_demo_operator(session, PASSWORD)
        async with sessions.begin() as session:
            await seed_catalog(session)
        async with sessions.begin() as session:
            await seed_stage2_sandbox(session, PASSWORD)
            # 新增一位同时具备 operator + approver 角色的账号，用于验证“禁止自审”
            shop = (await session.scalars(select(Shop).where(Shop.name == "轻食旗舰店"))).one()
            dual_user = User(
                name="双角色成员",
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


def test_promotion_idempotency_approval_boundaries_and_adjacent_schedule(stage2_client: TestClient) -> None:
    ops = _login(stage2_client, "ops.demo")
    approver = _login(stage2_client, APPROVER_USERNAME)
    dual = _login(stage2_client, "dual.demo")

    demo_shop = next(s for s in ops["shops"] if s["name"] == "轻食旗舰店")
    second_shop = next(s for s in ops["shops"] if s["name"] == SECOND_DEMO_SHOP)
    shop_id = demo_shop["id"]

    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}
    dual_headers = {"Authorization": f"Bearer {dual['access_token']}"}

    # 1. 提交促销并验证幂等重放与同键异参冲突
    payload = {
        "name": "冬季暖饮专场",
        "sku_code": "SKU-001",
        "discount": "8",
        "start_date": "2026-11-22",
        "duration_days": 3,
    }
    first = stage2_client.post(
        f"/api/shops/{shop_id}/promotions",
        json=payload,
        headers={**ops_headers, "Idempotency-Key": "idem-promo-001"},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["promotion_no"] == "PRM-20261121-01"
    assert body["approval_no"] == "APR-20261121-01"
    assert body["promotion_price"] == "159.20"
    assert body["replayed"] is False

    replay = stage2_client.post(
        f"/api/shops/{shop_id}/promotions",
        json=payload,
        headers={**ops_headers, "Idempotency-Key": "idem-promo-001"},
    )
    assert replay.status_code == 200
    assert replay.json()["approval_no"] == body["approval_no"]
    assert replay.json()["replayed"] is True

    conflict = stage2_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={**payload, "discount": "7.5"},
        headers={**ops_headers, "Idempotency-Key": "idem-promo-001"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    # 2. 验证审批边界：无审批角色、跨店审批、禁止自审
    assert (
        stage2_client.post(
            f"/api/shops/{shop_id}/approvals/{body['approval_id']}/approve",
            json={"comment": "运营尝试自批"},
            headers=ops_headers,
        ).json()["code"]
        == "SHOP_ROLE_FORBIDDEN"
    )
    cross_shop_approve = stage2_client.post(
        f"/api/shops/{second_shop['id']}/approvals/{body['approval_id']}/approve",
        json={"comment": "跨店审批"},
        headers=approver_headers,
    )
    assert cross_shop_approve.status_code == 403
    assert cross_shop_approve.json()["code"] == "CROSS_SHOP_APPROVAL_DENIED"

    # 双角色成员发起的申请自己不能审批（禁止自审）
    dual_submit = stage2_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={**payload, "name": "双角色自审测试", "start_date": "2026-12-10"},
        headers=dual_headers,
    ).json()
    self_approve = stage2_client.post(
        f"/api/shops/{shop_id}/approvals/{dual_submit['approval_id']}/approve",
        json={"comment": "自审"},
        headers=dual_headers,
    )
    assert self_approve.status_code == 403
    assert self_approve.json()["code"] == "SELF_APPROVAL_FORBIDDEN"

    # 3. 独立审批员批准 → 自动复核并排期成功
    approved = stage2_client.post(
        f"/api/shops/{shop_id}/approvals/{body['approval_id']}/approve",
        json={"comment": "毛利与库存达标，批准排期"},
        headers=approver_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["execution_status"] == "scheduled"
    assert approved.json()["approval"]["status"] == "approved"
    assert approved.json()["approval"]["resource_status"] == "scheduled"

    # 4. 禁止重复裁决
    re_decide = stage2_client.post(
        f"/api/shops/{shop_id}/approvals/{body['approval_id']}/reject",
        json={"comment": "重复裁决"},
        headers=approver_headers,
    )
    assert re_decide.status_code == 409
    assert re_decide.json()["code"] == "APPROVAL_ALREADY_DECIDED"

    # 5. 验证时段冲突拦截与首尾相接允许排期
    overlapping = stage2_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={**payload, "name": "重叠活动", "start_date": "2026-11-24", "duration_days": 2},
        headers=ops_headers,
    )
    assert overlapping.status_code == 409
    assert overlapping.json()["code"] == "PROMOTION_SCHEDULE_CONFLICT"

    adjacent = stage2_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={**payload, "name": "首尾相接活动", "start_date": "2026-11-25", "duration_days": 3},
        headers=ops_headers,
    )
    assert adjacent.status_code == 200
    adjacent_approve = stage2_client.post(
        f"/api/shops/{shop_id}/approvals/{adjacent.json()['approval_id']}/approve",
        json={"comment": "首尾相接通过"},
        headers=approver_headers,
    )
    assert adjacent_approve.status_code == 200
    assert adjacent_approve.json()["execution_status"] == "scheduled"

    # 6. 验证详情与不可变审计日志连续性
    detail = stage2_client.get(
        f"/api/shops/{shop_id}/promotions/{body['promotion_id']}", headers=ops_headers
    ).json()
    actions = [entry["action"] for entry in detail["audit_logs"]]
    assert actions == ["promotion.submitted", "approval.approved", "promotion.scheduled"]


def test_cancel_reject_recheck_failure_and_v2_revision_flow(
    stage2_client: TestClient, database_url: str
) -> None:
    ops = _login(stage2_client, "ops.demo")
    approver = _login(stage2_client, APPROVER_USERNAME)
    shop_id = next(s["id"] for s in ops["shops"] if s["name"] == "轻食旗舰店")
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}

    # 创建 V1 并撤回
    v1 = stage2_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={
            "name": "可修订促销",
            "sku_code": "SKU-001",
            "discount": "8",
            "start_date": "2026-11-22",
            "duration_days": 3,
        },
        headers=ops_headers,
    ).json()
    cancelled = stage2_client.post(
        f"/api/shops/{shop_id}/promotions/{v1['promotion_id']}/cancel", headers=ops_headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # 修订为 V2 重新送审，再由审批员驳回（先测空意见 422）
    v2 = stage2_client.post(
        f"/api/shops/{shop_id}/promotions/{v1['promotion_id']}/revise",
        json={"discount": "8.5", "start_date": "2026-11-23", "duration_days": 3, "reason": "调整折扣"},
        headers=ops_headers,
    ).json()
    assert v2["version"] == 2 and v2["status"] == "pending_approval"

    missing_reason = stage2_client.post(
        f"/api/shops/{shop_id}/approvals/{v2['approval_id']}/reject",
        json={"comment": "   "},
        headers=approver_headers,
    )
    assert missing_reason.status_code == 422
    assert missing_reason.json()["code"] == "REJECTION_REASON_REQUIRED"

    rejected = stage2_client.post(
        f"/api/shops/{shop_id}/approvals/{v2['approval_id']}/reject",
        json={"comment": "档期需延后至月底"},
        headers=approver_headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["execution_status"] == "rejected"

    # 修订为 V3，但在审批通过前 SKU 售价被修改 → 批准后复核失败
    v3 = stage2_client.post(
        f"/api/shops/{shop_id}/promotions/{v1['promotion_id']}/revise",
        json={"discount": "8", "start_date": "2026-11-26", "duration_days": 3, "reason": "延后档期"},
        headers=ops_headers,
    ).json()
    assert v3["version"] == 3

    async def change_price(new_price: Decimal) -> None:
        engine = create_database_engine(database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    update(Sku)
                    .where(Sku.shop_id == shop_id, Sku.sku_code == "SKU-001")
                    .values(current_price=new_price)
                )
        finally:
            await engine.dispose()

    asyncio.run(change_price(Decimal("209.00")))
    failed_schedule = stage2_client.post(
        f"/api/shops/{shop_id}/approvals/{v3['approval_id']}/approve",
        json={"comment": "批准 V3"},
        headers=approver_headers,
    )
    assert failed_schedule.status_code == 200
    assert failed_schedule.json()["execution_status"] == "failed"
    assert "售价已从" in failed_schedule.json()["execution_message"]

    # 基于新价格修订 V4 再审 → 排期成功，旧审批单全部保留
    v4 = stage2_client.post(
        f"/api/shops/{shop_id}/promotions/{v1['promotion_id']}/revise",
        json={"discount": "8", "start_date": "2026-11-26", "duration_days": 3, "reason": "按新售价重算"},
        headers=ops_headers,
    ).json()
    assert v4["version"] == 4 and v4["original_price"] == "209.00" and v4["promotion_price"] == "167.20"

    scheduled = stage2_client.post(
        f"/api/shops/{shop_id}/approvals/{v4['approval_id']}/approve",
        json={"comment": "V4 通过"},
        headers=approver_headers,
    )
    assert scheduled.status_code == 200
    assert scheduled.json()["execution_status"] == "scheduled"

    detail = stage2_client.get(
        f"/api/shops/{shop_id}/promotions/{v1['promotion_id']}", headers=ops_headers
    ).json()
    assert len(detail["approvals"]) == 4
    assert [a["resource_version"] for a in detail["approvals"]] == [4, 3, 2, 1]


def test_concurrent_overlapping_approvals_allow_at_most_one_scheduled(
    stage2_client: TestClient, database_url: str
) -> None:
    """验证第 4.3 节并发约束：两份独立获批且时段重叠的方案并发执行时，至多一份排期成功。"""
    ops = _login(stage2_client, "ops.demo")
    approver = _login(stage2_client, APPROVER_USERNAME)
    demo_shop = next(s for s in ops["shops"] if s["name"] == "轻食旗舰店")
    shop_id = demo_shop["id"]
    org_id = demo_shop["organization_id"]
    approver_id = approver["user"]["id"]
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}

    # 两份时段完全重叠的活动在排期前分别提交审批（均为 pending_approval）
    p1 = stage2_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={
            "name": "并发方案 A",
            "sku_code": "SKU-001",
            "discount": "8",
            "start_date": "2026-12-01",
            "duration_days": 3,
        },
        headers=ops_headers,
    ).json()
    p2 = stage2_client.post(
        f"/api/shops/{shop_id}/promotions",
        json={
            "name": "并发方案 B",
            "sku_code": "SKU-001",
            "discount": "7.5",
            "start_date": "2026-12-02",
            "duration_days": 3,
        },
        headers=ops_headers,
    ).json()

    async def race_approve() -> list[tuple[str | None, str | None, str | None]]:
        engine = create_database_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        business_now = datetime(2026, 11, 21, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        async def run_one(approval_id: int) -> tuple[str | None, str | None, str | None]:
            async with sessions.begin() as session:
                res = await decide_approval(
                    session,
                    organization_id=org_id,
                    shop_id=shop_id,
                    approval_id=approval_id,
                    actor_id=approver_id,
                    actor_roles=[Role.APPROVER],
                    decision="approve",
                    comment="并发批准",
                    business_now=business_now,
                )
                return (
                    res.execution_status,
                    res.approval.resource_no,
                    res.approval.resource_conflict_promotion_no,
                )

        try:
            return list(await asyncio.gather(run_one(p1["approval_id"]), run_one(p2["approval_id"])))
        finally:
            await engine.dispose()

    outcomes = asyncio.run(race_approve())
    statuses = [item[0] for item in outcomes]
    assert sorted(statuses) == ["failed", "scheduled"]
    winner_no = next(item[1] for item in outcomes if item[0] == "scheduled")
    loser_conflict_no = next(item[2] for item in outcomes if item[0] == "failed")
    assert loser_conflict_no == winner_no


def test_stage2_agent_acceptance_matrix(
    stage2_client: TestClient, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """严格对照产品设计第 9 节：阶段 2 Agent 对照验收矩阵。"""
    from app.agent import graph
    from app.schemas.agent import QueryIntent

    ops = _login(stage2_client, "ops.demo")
    approver = _login(stage2_client, APPROVER_USERNAME)
    demo_shop = next(s for s in ops["shops"] if s["name"] == "轻食旗舰店")
    second_shop = next(s for s in ops["shops"] if s["name"] == SECOND_DEMO_SHOP)
    shop_id = demo_shop["id"]
    ops_headers = {"Authorization": f"Bearer {ops['access_token']}"}
    approver_headers = {"Authorization": f"Bearer {approver['access_token']}"}

    async def classify(message: str) -> QueryIntent:
        if "确认" in message:
            return QueryIntent(kind="confirm_plan")
        if "促销" in message:
            return QueryIntent(kind="promotion", sku_code="SKU-001")
        return QueryIntent(kind="sales", sku_code="SKU-001")

    monkeypatch.setattr(graph, "classify", classify)

    async def counts() -> tuple[int, int, int]:
        engine = create_database_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as db:
                p = await db.scalar(select(func.count(Promotion.id)))
                a = await db.scalar(select(func.count(Approval.id)))
                au = await db.scalar(select(func.count(AuditLog.id)))
                return int(p or 0), int(a or 0), int(au or 0)
        finally:
            await engine.dispose()

    # 1. 只读销量查询 → 120 件，无促销/审批写入
    sales_res = stage2_client.post(
        f"/api/shops/{shop_id}/agent/query",
        json={"message": "查一下 SKU-001 最近 7 天销量"},
        headers=ops_headers,
    ).json()
    assert sales_res["kind"] == "sales" and sales_res["sales"]["units_sold"] == 120
    assert asyncio.run(counts()) == (0, 0, 0)

    # 2. 两轮追问生成促销方案 → 两个回合均无促销或审批写入
    turn1 = stage2_client.post(
        f"/api/shops/{shop_id}/agent/query",
        json={"message": "帮我看看 SKU-001 最近 7 天销量，如果库存充足，就创建一个 8 折、持续 3 天的促销。"},
        headers=ops_headers,
    ).json()
    assert turn1["kind"] == "promotion_clarify" and turn1["sales"]["units_sold"] == 120
    turn2 = stage2_client.post(
        f"/api/shops/{shop_id}/agent/query",
        json={"message": "11 月 22 日开始", "session_id": turn1["session_id"]},
        headers=ops_headers,
    ).json()
    assert turn2["kind"] == "promotion_preview"
    assert turn2["promotion"]["promotion_price"] == "159.20"
    assert turn2["promotion"]["conflict_check"] == "clear"
    assert asyncio.run(counts()) == (0, 0, 0)

    # 3. 换店后确认旧方案 → 提示切回原店，两店均无新增审批单
    cross_confirm = stage2_client.post(
        f"/api/shops/{second_shop['id']}/agent/query",
        json={"message": "确认刚才的方案", "session_id": turn1["session_id"]},
        headers=ops_headers,
    ).json()
    assert cross_confirm["kind"] == "unsupported"
    assert "另一家店铺" in cross_confirm["message"]
    assert asyncio.run(counts()) == (0, 0, 0)

    # 4. 回到原店说“确认刚才的方案” → 创建审批单；再次确认返回同一编号
    confirm1 = stage2_client.post(
        f"/api/shops/{shop_id}/agent/query",
        json={"message": "确认刚才的方案", "session_id": turn1["session_id"]},
        headers=ops_headers,
    ).json()
    assert confirm1["kind"] == "promotion_submitted"
    assert confirm1["tool_calls"] == 1
    approval_no = confirm1["submission"]["approval_no"]
    approval_id = confirm1["submission"]["approval_id"]
    assert asyncio.run(counts())[:2] == (1, 1)

    confirm2 = stage2_client.post(
        f"/api/shops/{shop_id}/agent/query",
        json={"message": "确认刚才的方案", "session_id": turn1["session_id"]},
        headers=ops_headers,
    ).json()
    assert confirm2["kind"] == "promotion_submitted"
    assert confirm2["submission"]["approval_no"] == approval_no
    assert confirm2["submission"]["replayed"] is True
    assert asyncio.run(counts())[:2] == (1, 1)

    # 5. 审批员批准排期后，Agent 再次预览重叠时段应实时识别排期冲突
    approved = stage2_client.post(
        f"/api/shops/{shop_id}/approvals/{approval_id}/approve",
        json={"comment": "批准"},
        headers=approver_headers,
    )
    assert approved.status_code == 200 and approved.json()["execution_status"] == "scheduled"

    conflict_preview = stage2_client.post(
        f"/api/shops/{shop_id}/agent/query",
        json={"message": "给 SKU-001 做 8 折、持续 3 天的促销，11 月 23 日开始"},
        headers=ops_headers,
    ).json()
    assert conflict_preview["kind"] == "promotion_blocked"
    assert conflict_preview["promotion"]["conflict_check"] == "conflict"
    assert confirm1["submission"]["promotion_no"] in conflict_preview["promotion"]["conflicting_promotions"]
