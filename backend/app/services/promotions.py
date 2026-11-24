from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Approval, Inventory, Product, Promotion, PromotionItem, Shop, Sku, User
from app.models.org import Role
from app.schemas.promotions import (
    PromotionApprovalBrief,
    PromotionCreateRequest,
    PromotionDetail,
    PromotionItemInfo,
    PromotionReviseRequest,
    PromotionStatus,
    PromotionSubmitResponse,
    PromotionSummary,
)
from app.services.audit import list_resource_audit_logs, write_audit_log
from app.services.idempotency import claim_idempotency, complete_idempotency
from app.services.notifications import create_notification, notify_shop_roles
from app.services.orders import sku_sales

MINIMUM_PROMOTION_QTY = 100
ACTIVE_CONFLICT_STATUSES = ("scheduled", "active")
REVISABLE_STATUSES = frozenset({"rejected", "cancelled", "failed"})

ALLOWED_PROMOTION_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending_approval": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"scheduled", "failed"}),
    "scheduled": frozenset({"active", "completed", "cancelled"}),
    "active": frozenset({"completed", "cancelled"}),
    "rejected": frozenset({"pending_approval"}),
    "cancelled": frozenset({"pending_approval"}),
    "failed": frozenset({"pending_approval"}),
    "completed": frozenset(),
}


def assert_promotion_transition(current_status: str, next_status: str) -> None:
    allowed = ALLOWED_PROMOTION_TRANSITIONS.get(current_status, frozenset())
    if next_status not in allowed:
        raise AppError(
            409,
            "INVALID_PROMOTION_STATE",
            f"当前活动状态（{current_status}）不允许流转至 {next_status}。",
            detail=f"Invalid promotion transition: {current_status} -> {next_status}",
        )


def compute_discount_rate(discount: Decimal) -> Decimal:
    return (discount / Decimal("10")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def compute_promotion_price(original_price: Decimal, discount_rate: Decimal) -> Decimal:
    return (original_price * discount_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def discount_from_rate(discount_rate: Decimal) -> Decimal:
    return (discount_rate * Decimal("10")).normalize()


def compute_activity_window(
    start_date: date,
    duration_days: int,
    business_day: date,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    if start_date <= business_day:
        raise AppError(
            422,
            "INVALID_PROMOTION_WINDOW",
            f"活动开始日期必须晚于当前业务日 {business_day}。",
            detail="Promotion start date must follow current business day",
        )
    shop_tz = ZoneInfo(timezone_name)
    starts_at = datetime.combine(start_date, time.min, shop_tz)
    ends_at = datetime.combine(start_date + timedelta(days=duration_days), time.min, shop_tz)
    return starts_at, ends_at


async def next_promotion_no(session: AsyncSession, shop_id: int, business_day: date) -> str:
    await session.execute(text("SELECT pg_advisory_xact_lock(726100, :shop_id)"), {"shop_id": shop_id})
    prefix = f"PRM-{business_day:%Y%m%d}-"
    existing_count = await session.scalar(
        select(func.count(Promotion.id)).where(
            Promotion.shop_id == shop_id,
            Promotion.promotion_no.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{(existing_count or 0) + 1:02d}"


async def next_approval_no(session: AsyncSession, shop_id: int, business_day: date) -> str:
    await session.execute(text("SELECT pg_advisory_xact_lock(726200, :shop_id)"), {"shop_id": shop_id})
    prefix = f"APR-{business_day:%Y%m%d}-"
    existing_count = await session.scalar(
        select(func.count(Approval.id)).where(
            Approval.shop_id == shop_id,
            Approval.approval_no.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{(existing_count or 0) + 1:02d}"


async def find_conflicting_promotions(
    session: AsyncSession,
    *,
    shop_id: int,
    sku_id: int,
    starts_at: datetime,
    ends_at: datetime,
    exclude_promotion_id: int | None = None,
) -> list[str]:
    """查找同店同 SKU 在指定时段重叠的已排期/生效活动（严格小于/大于，允许首尾相接）。"""
    query = (
        select(Promotion.promotion_no)
        .join(
            PromotionItem,
            and_(
                PromotionItem.shop_id == Promotion.shop_id,
                PromotionItem.promotion_id == Promotion.id,
            ),
        )
        .where(
            Promotion.shop_id == shop_id,
            PromotionItem.sku_id == sku_id,
            Promotion.status.in_(ACTIVE_CONFLICT_STATUSES),
            Promotion.starts_at < ends_at,
            Promotion.ends_at > starts_at,
        )
        .order_by(Promotion.starts_at.asc(), Promotion.id.asc())
    )
    if exclude_promotion_id is not None:
        query = query.where(Promotion.id != exclude_promotion_id)
    return list((await session.scalars(query)).all())


async def _load_sku_facts(session: AsyncSession, shop_id: int, sku_code: str) -> Any:
    query = (
        select(
            Sku.id.label("sku_id"),
            Sku.sku_code,
            Sku.current_price,
            Sku.cost,
            Sku.status.label("sku_status"),
            Product.name.label("product_name"),
            Product.status.label("product_status"),
            Inventory.available_qty,
        )
        .join(Product, and_(Sku.shop_id == Product.shop_id, Sku.product_id == Product.id))
        .outerjoin(Inventory, Inventory.sku_id == Sku.id)
        .where(Sku.shop_id == shop_id, Sku.sku_code == sku_code.upper())
    )
    return (await session.execute(query)).one_or_none()


def _validate_sku_eligibility(
    facts: Any,
    sku_code: str,
    minimum_qty: int = MINIMUM_PROMOTION_QTY,
) -> None:
    if facts is None:
        raise AppError(404, "SKU_NOT_FOUND", f"当前店铺没有找到 {sku_code}。", detail="SKU not found")
    if facts.sku_status != "active" or facts.product_status != "active":
        raise AppError(
            422,
            "SKU_NOT_ACTIVE",
            "该商品或 SKU 当前不在售，无法提交促销。",
            detail="Product or SKU is not active",
        )
    if facts.available_qty is None:
        raise AppError(
            422,
            "INVENTORY_MISSING",
            "当前没有库存记录，无法校验库存门槛。",
            detail="Inventory snapshot missing",
        )
    if facts.available_qty < minimum_qty:
        raise AppError(
            422,
            "INSUFFICIENT_INVENTORY",
            f"可售库存 {facts.available_qty} 件，低于 {minimum_qty} 件门槛。",
            detail=f"Available inventory {facts.available_qty} below threshold {minimum_qty}",
        )


async def submit_promotion(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    shop_name: str,
    timezone_name: str,
    actor_id: int,
    business_day: date,
    payload: PromotionCreateRequest,
    idempotency_key: str | None = None,
) -> PromotionSubmitResponse:
    sku_code = payload.sku_code.upper()
    canonical_request = {
        "name": payload.name,
        "sku_code": sku_code,
        "discount": str(payload.discount),
        "start_date": payload.start_date.isoformat(),
        "duration_days": payload.duration_days,
    }

    claim = None
    if idempotency_key:
        claim = await claim_idempotency(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            operation_type="promotion_submit",
            idempotency_key=idempotency_key,
            request_payload=canonical_request,
        )
        if claim.replayed_response is not None:
            replayed = PromotionSubmitResponse.model_validate(claim.replayed_response)
            replayed.replayed = True
            return replayed

    starts_at, ends_at = compute_activity_window(
        payload.start_date, payload.duration_days, business_day, timezone_name
    )
    facts = await _load_sku_facts(session, shop_id, sku_code)
    _validate_sku_eligibility(facts, sku_code, MINIMUM_PROMOTION_QTY)

    conflicts = await find_conflicting_promotions(
        session,
        shop_id=shop_id,
        sku_id=facts.sku_id,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    if conflicts:
        raise AppError(
            409,
            "PROMOTION_SCHEDULE_CONFLICT",
            f"该 SKU 在所选时段与已有活动 {', '.join(conflicts)} 存在重叠，请调整活动时间。",
            detail=f"Promotion overlaps with {', '.join(conflicts)}",
        )

    discount_rate = compute_discount_rate(payload.discount)
    promotion_price = compute_promotion_price(facts.current_price, discount_rate)
    below_cost = promotion_price < facts.cost
    risk_warnings: list[str] = []
    if below_cost:
        risk_warnings.append(
            f"促销价 ¥{promotion_price} 低于商品成本价 ¥{facts.cost}，存在负毛利风险，请谨慎审批。"
        )

    sales = await sku_sales(session, shop_id, sku_code, business_day, timezone_name=timezone_name)
    promotion_no = await next_promotion_no(session, shop_id, business_day)
    approval_no = await next_approval_no(session, shop_id, business_day)
    activity_name = payload.name or f"{facts.product_name} {payload.discount.normalize()} 折促销"

    promotion = Promotion(
        shop_id=shop_id,
        promotion_no=promotion_no,
        name=activity_name,
        version=1,
        status="pending_approval",
        starts_at=starts_at,
        ends_at=ends_at,
        minimum_qty=MINIMUM_PROMOTION_QTY,
        created_by=actor_id,
    )
    session.add(promotion)
    await session.flush()

    item = PromotionItem(
        shop_id=shop_id,
        promotion_id=promotion.id,
        sku_id=facts.sku_id,
        original_price=facts.current_price,
        cost_price=facts.cost,
        discount_rate=discount_rate,
        promotion_price=promotion_price,
    )
    session.add(item)

    snapshot: dict[str, Any] = {
        "promotion_id": promotion.id,
        "promotion_no": promotion_no,
        "promotion_name": activity_name,
        "version": 1,
        "shop_id": shop_id,
        "shop_name": shop_name,
        "timezone": timezone_name,
        "sku_id": facts.sku_id,
        "sku_code": sku_code,
        "product_name": facts.product_name,
        "original_price": str(facts.current_price),
        "cost_price": str(facts.cost),
        "discount": str(payload.discount.normalize()),
        "discount_rate": str(discount_rate),
        "promotion_price": str(promotion_price),
        "below_cost": below_cost,
        "risk_warnings": risk_warnings,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "duration_days": payload.duration_days,
        "minimum_qty": MINIMUM_PROMOTION_QTY,
        "available_qty_snapshot": facts.available_qty,
        "sales_7d": sales.model_dump(mode="json") if sales else None,
        "conflict_check": "clear",
    }
    approval = Approval(
        organization_id=organization_id,
        shop_id=shop_id,
        approval_no=approval_no,
        action_type="promotion_publish",
        resource_type="promotion",
        resource_id=promotion.id,
        resource_version=1,
        payload_snapshot=snapshot,
        status="pending",
        requester_id=actor_id,
    )
    session.add(approval)
    await session.flush()

    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="promotion.submitted",
        resource_type="promotion",
        resource_id=promotion.id,
        before_json=None,
        after_json={
            "promotion_no": promotion_no,
            "version": 1,
            "status": "pending_approval",
            "approval_no": approval_no,
            "promotion_price": str(promotion_price),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
        },
        metadata_json={"approval_id": approval.id, "idempotency_key": idempotency_key},
    )
    await notify_shop_roles(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        roles=(Role.APPROVER,),
        category="approval",
        title=f"待审批促销：{activity_name}",
        content=f"{promotion_no}（V1 · {sku_code} · 活动价 ¥{promotion_price}）已提交审批，请审阅快照。",
        resource_type="approval",
        resource_id=approval.id,
        exclude_user_id=actor_id,
    )

    response = PromotionSubmitResponse(
        promotion_id=promotion.id,
        promotion_no=promotion_no,
        name=activity_name,
        version=1,
        status="pending_approval",
        approval_id=approval.id,
        approval_no=approval_no,
        approval_status="pending",
        sku_code=sku_code,
        original_price=facts.current_price,
        cost_price=facts.cost,
        promotion_price=promotion_price,
        below_cost=below_cost,
        risk_warnings=risk_warnings,
        starts_at=starts_at,
        ends_at=ends_at,
        timezone=timezone_name,
        replayed=False,
    )
    if claim is not None:
        complete_idempotency(
            claim.record,
            resource_type="promotion",
            resource_id=promotion.id,
            response_payload=response.model_dump(mode="json"),
        )
    return response


async def cancel_promotion(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    promotion_id: int,
    actor_id: int,
    actor_roles: list[Role],
) -> PromotionDetail:
    promotion = await session.scalar(
        select(Promotion)
        .where(Promotion.shop_id == shop_id, Promotion.id == promotion_id)
        .with_for_update()
    )
    if promotion is None:
        raise AppError(404, "PROMOTION_NOT_FOUND", "当前店铺没有找到该促销活动。", detail="Promotion not found")
    if promotion.created_by != actor_id and Role.ADMIN not in actor_roles:
        raise AppError(
            403,
            "PROMOTION_CANCEL_FORBIDDEN",
            "仅原申请人或管理员可撤回该待审批活动。",
            detail="Only requester or admin can cancel promotion",
        )
    if promotion.status != "pending_approval":
        raise AppError(
            409,
            "INVALID_PROMOTION_STATE",
            "仅待审批状态的促销活动支持撤回。",
            detail=f"Cannot cancel promotion in status {promotion.status}",
        )

    assert_promotion_transition(promotion.status, "cancelled")
    before_state = {"status": promotion.status, "version": promotion.version}
    promotion.status = "cancelled"

    pending_approval = await session.scalar(
        select(Approval)
        .where(
            Approval.resource_type == "promotion",
            Approval.resource_id == promotion.id,
            Approval.resource_version == promotion.version,
        )
        .with_for_update()
    )
    if pending_approval is not None and pending_approval.status == "pending":
        pending_approval.status = "cancelled"

    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="promotion.cancelled",
        resource_type="promotion",
        resource_id=promotion.id,
        before_json=before_state,
        after_json={"status": "cancelled", "version": promotion.version},
        metadata_json={"approval_no": pending_approval.approval_no if pending_approval else None},
    )
    detail = await get_promotion_detail(session, shop_id=shop_id, promotion_id=promotion.id)
    assert detail is not None
    return detail


async def revise_promotion(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    shop_name: str,
    timezone_name: str,
    promotion_id: int,
    actor_id: int,
    business_day: date,
    payload: PromotionReviseRequest,
    idempotency_key: str | None = None,
) -> PromotionSubmitResponse:
    canonical_request = {
        "promotion_id": promotion_id,
        "name": payload.name,
        "discount": str(payload.discount),
        "start_date": payload.start_date.isoformat(),
        "duration_days": payload.duration_days,
        "reason": payload.reason,
    }
    claim = None
    if idempotency_key:
        claim = await claim_idempotency(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            operation_type="promotion_revise",
            idempotency_key=idempotency_key,
            request_payload=canonical_request,
        )
        if claim.replayed_response is not None:
            replayed = PromotionSubmitResponse.model_validate(claim.replayed_response)
            replayed.replayed = True
            return replayed

    promotion = await session.scalar(
        select(Promotion)
        .where(Promotion.shop_id == shop_id, Promotion.id == promotion_id)
        .with_for_update()
    )
    if promotion is None:
        raise AppError(404, "PROMOTION_NOT_FOUND", "当前店铺没有找到该促销活动。", detail="Promotion not found")
    if promotion.status not in REVISABLE_STATUSES:
        raise AppError(
            409,
            "INVALID_PROMOTION_STATE",
            "仅已驳回、已撤回或复核失败的活动支持修订并重新送审。",
            detail=f"Cannot revise promotion in status {promotion.status}",
        )

    item = (
        await session.scalars(
            select(PromotionItem)
            .where(PromotionItem.shop_id == shop_id, PromotionItem.promotion_id == promotion.id)
            .with_for_update()
        )
    ).one()
    sku_code = (await session.scalars(select(Sku.sku_code).where(Sku.id == item.sku_id))).one()

    starts_at, ends_at = compute_activity_window(
        payload.start_date, payload.duration_days, business_day, timezone_name
    )
    facts = await _load_sku_facts(session, shop_id, sku_code)
    _validate_sku_eligibility(facts, sku_code, promotion.minimum_qty)

    conflicts = await find_conflicting_promotions(
        session,
        shop_id=shop_id,
        sku_id=facts.sku_id,
        starts_at=starts_at,
        ends_at=ends_at,
        exclude_promotion_id=promotion.id,
    )
    if conflicts:
        raise AppError(
            409,
            "PROMOTION_SCHEDULE_CONFLICT",
            f"新时段与已有活动 {', '.join(conflicts)} 存在重叠，请重新选择日期。",
            detail=f"Revised window overlaps with {', '.join(conflicts)}",
        )

    discount_rate = compute_discount_rate(payload.discount)
    promotion_price = compute_promotion_price(facts.current_price, discount_rate)
    below_cost = promotion_price < facts.cost
    risk_warnings: list[str] = []
    if below_cost:
        risk_warnings.append(
            f"促销价 ¥{promotion_price} 低于商品成本价 ¥{facts.cost}，存在负毛利风险，请谨慎审批。"
        )

    assert_promotion_transition(promotion.status, "pending_approval")
    before_state = {
        "version": promotion.version,
        "status": promotion.status,
        "starts_at": promotion.starts_at.isoformat(),
        "ends_at": promotion.ends_at.isoformat(),
        "promotion_price": str(item.promotion_price),
    }

    next_version = promotion.version + 1
    promotion.version = next_version
    promotion.status = "pending_approval"
    promotion.starts_at = starts_at
    promotion.ends_at = ends_at
    promotion.failure_reason = None
    promotion.conflict_promotion_no = None
    if payload.name:
        promotion.name = payload.name

    item.original_price = facts.current_price
    item.cost_price = facts.cost
    item.discount_rate = discount_rate
    item.promotion_price = promotion_price

    sales = await sku_sales(session, shop_id, sku_code, business_day, timezone_name=timezone_name)
    approval_no = await next_approval_no(session, shop_id, business_day)
    snapshot: dict[str, Any] = {
        "promotion_id": promotion.id,
        "promotion_no": promotion.promotion_no,
        "promotion_name": promotion.name,
        "version": next_version,
        "shop_id": shop_id,
        "shop_name": shop_name,
        "timezone": timezone_name,
        "sku_id": facts.sku_id,
        "sku_code": sku_code,
        "product_name": facts.product_name,
        "original_price": str(facts.current_price),
        "cost_price": str(facts.cost),
        "discount": str(payload.discount.normalize()),
        "discount_rate": str(discount_rate),
        "promotion_price": str(promotion_price),
        "below_cost": below_cost,
        "risk_warnings": risk_warnings,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "duration_days": payload.duration_days,
        "minimum_qty": promotion.minimum_qty,
        "available_qty_snapshot": facts.available_qty,
        "sales_7d": sales.model_dump(mode="json") if sales else None,
        "conflict_check": "clear",
        "revision_reason": payload.reason,
    }
    approval = Approval(
        organization_id=organization_id,
        shop_id=shop_id,
        approval_no=approval_no,
        action_type="promotion_publish",
        resource_type="promotion",
        resource_id=promotion.id,
        resource_version=next_version,
        payload_snapshot=snapshot,
        status="pending",
        requester_id=actor_id,
    )
    session.add(approval)
    await session.flush()

    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="promotion.revised",
        resource_type="promotion",
        resource_id=promotion.id,
        before_json=before_state,
        after_json={
            "version": next_version,
            "status": "pending_approval",
            "approval_no": approval_no,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "promotion_price": str(promotion_price),
        },
        metadata_json={"revision_reason": payload.reason, "approval_id": approval.id},
    )
    await notify_shop_roles(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        roles=(Role.APPROVER,),
        category="approval",
        title=f"待审批修订促销：{promotion.name}（V{next_version}）",
        content=(
            f"{promotion.promotion_no} 已提交修订版本 V{next_version}"
            f"（{sku_code} · 活动价 ¥{promotion_price}），请审阅。"
        ),
        resource_type="approval",
        resource_id=approval.id,
        exclude_user_id=actor_id,
    )

    response = PromotionSubmitResponse(
        promotion_id=promotion.id,
        promotion_no=promotion.promotion_no,
        name=promotion.name,
        version=next_version,
        status="pending_approval",
        approval_id=approval.id,
        approval_no=approval_no,
        approval_status="pending",
        sku_code=sku_code,
        original_price=facts.current_price,
        cost_price=facts.cost,
        promotion_price=promotion_price,
        below_cost=below_cost,
        risk_warnings=risk_warnings,
        starts_at=starts_at,
        ends_at=ends_at,
        timezone=timezone_name,
        replayed=False,
    )
    if claim is not None:
        complete_idempotency(
            claim.record,
            resource_type="promotion",
            resource_id=promotion.id,
            response_payload=response.model_dump(mode="json"),
        )
    return response


async def schedule_approved_promotion(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    promotion_id: int,
    expected_version: int,
    actor_id: int,
    business_now: datetime,
) -> tuple[Literal["scheduled", "failed"], str]:
    """
    在事务内锁定目标 SKU 行后执行实时复核与排期。
    满足第 4.3 节并发约束：两份独立获批且时段重叠的方案并发执行时，至多一份排期成功。
    """
    promotion = await session.scalar(
        select(Promotion)
        .where(Promotion.shop_id == shop_id, Promotion.id == promotion_id)
        .with_for_update()
    )
    if promotion is None:
        raise AppError(404, "PROMOTION_NOT_FOUND", "当前店铺没有找到该促销活动。", detail="Promotion not found")
    if promotion.version != expected_version:
        raise AppError(
            409,
            "STALE_PROMOTION_VERSION",
            f"当前活动版本已更新至 V{promotion.version}，不能执行旧版本 V{expected_version}。",
            detail="Execution version does not match current promotion version",
        )
    if promotion.status == "scheduled":
        return "scheduled", "活动已完成排期。"
    assert_promotion_transition(promotion.status, "scheduled")

    item = (
        await session.scalars(
            select(PromotionItem).where(
                PromotionItem.shop_id == shop_id,
                PromotionItem.promotion_id == promotion.id,
            )
        )
    ).one()

    # 关键并发控制：先锁定目标 SKU，再检查价格、库存、状态与时段冲突，串行化同 SKU 排期竞争
    locked_sku = (
        await session.scalars(
            select(Sku)
            .where(Sku.shop_id == shop_id, Sku.id == item.sku_id)
            .with_for_update()
        )
    ).one()
    product_status = await session.scalar(
        select(Product.status).where(Product.shop_id == shop_id, Product.id == locked_sku.product_id)
    )
    available_qty = await session.scalar(
        select(Inventory.available_qty).where(Inventory.sku_id == locked_sku.id)
    )
    conflicts = await find_conflicting_promotions(
        session,
        shop_id=shop_id,
        sku_id=locked_sku.id,
        starts_at=promotion.starts_at,
        ends_at=promotion.ends_at,
        exclude_promotion_id=promotion.id,
    )

    failure_reason: str | None = None
    conflict_no: str | None = None
    if locked_sku.status != "active" or product_status != "active":
        failure_reason = "复核失败：商品或 SKU 当前已下架，无法排期。"
    elif locked_sku.current_price != item.original_price:
        failure_reason = (
            f"复核失败：SKU 当前售价已从审批时的 ¥{item.original_price} 变更为 ¥{locked_sku.current_price}。"
        )
    elif available_qty is None or available_qty < promotion.minimum_qty:
        qty_text = "未记录" if available_qty is None else f"{available_qty} 件"
        failure_reason = f"复核失败：当前可售库存（{qty_text}）低于 {promotion.minimum_qty} 件门槛。"
    elif promotion.starts_at <= business_now:
        failure_reason = "复核失败：活动开始时间已早于或等于当前业务时间，无法排期。"
    elif conflicts:
        conflict_no = conflicts[0]
        failure_reason = f"复核失败：与已排期/生效活动 {conflict_no} 时段重叠。"

    before_state = {"status": promotion.status, "version": promotion.version}
    if failure_reason is not None:
        assert_promotion_transition(promotion.status, "failed")
        promotion.status = "failed"
        promotion.failure_reason = failure_reason
        promotion.conflict_promotion_no = conflict_no
        await write_audit_log(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="promotion.schedule_failed",
            resource_type="promotion",
            resource_id=promotion.id,
            before_json=before_state,
            after_json={
                "status": "failed",
                "version": promotion.version,
                "failure_reason": failure_reason,
                "conflict_promotion_no": conflict_no,
            },
        )
        return "failed", failure_reason

    promotion.status = "scheduled"
    promotion.failure_reason = None
    promotion.conflict_promotion_no = None
    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="promotion.scheduled",
        resource_type="promotion",
        resource_id=promotion.id,
        before_json=before_state,
        after_json={
            "status": "scheduled",
            "version": promotion.version,
            "starts_at": promotion.starts_at.isoformat(),
            "ends_at": promotion.ends_at.isoformat(),
        },
    )
    return "scheduled", f"业务复核通过，活动 {promotion.promotion_no} 已成功排期。"


async def _mark_missed_window_completed(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    promotion: Promotion,
    actor_id: int,
    business_now: datetime,
) -> tuple[str, str]:
    before_state = {"status": promotion.status, "version": promotion.version}
    reason = "错过生效时段：调度恢复时活动已超过结束时间，系统直接标记为已结束。"
    assert_promotion_transition(promotion.status, "completed")
    promotion.status = "completed"
    promotion.failure_reason = reason
    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="promotion.missed_window",
        resource_type="promotion",
        resource_id=promotion.id,
        before_json=before_state,
        after_json={
            "status": "completed",
            "version": promotion.version,
            "failure_reason": reason,
            "business_now": business_now.isoformat(),
        },
    )
    await create_notification(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        user_id=promotion.created_by,
        category="promotion",
        title=f"活动 {promotion.promotion_no} 已错过生效时段",
        content=f"{promotion.name}（{promotion.promotion_no}）调度恢复时已过结束时间，已直接结束并记录审计。",
        resource_type="promotion",
        resource_id=promotion.id,
    )
    return "completed", reason


async def activate_scheduled_promotion(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    promotion_id: int,
    actor_id: int,
    business_now: datetime,
) -> tuple[str, str]:
    """到点生效调度：scheduled -> active；若恢复时整个时段已过则直接进入 completed。"""
    promotion = await session.scalar(
        select(Promotion)
        .where(Promotion.shop_id == shop_id, Promotion.id == promotion_id)
        .with_for_update()
    )
    if promotion is None:
        raise AppError(404, "PROMOTION_NOT_FOUND", "当前店铺没有找到该促销活动。", detail="Promotion not found")
    if promotion.status in ("active", "completed"):
        return promotion.status, f"活动 {promotion.promotion_no} 已处于 {promotion.status} 状态。"
    if promotion.status != "scheduled":
        raise AppError(
            409,
            "INVALID_PROMOTION_STATE",
            f"当前活动状态（{promotion.status}）不能执行生效流转。",
            detail=f"Cannot activate promotion in status {promotion.status}",
        )
    if business_now >= promotion.ends_at:
        return await _mark_missed_window_completed(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            promotion=promotion,
            actor_id=actor_id,
            business_now=business_now,
        )
    if business_now < promotion.starts_at:
        return "scheduled", f"活动 {promotion.promotion_no} 尚未到达开始时间。"

    before_state = {"status": promotion.status, "version": promotion.version}
    assert_promotion_transition(promotion.status, "active")
    promotion.status = "active"
    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="promotion.activated",
        resource_type="promotion",
        resource_id=promotion.id,
        before_json=before_state,
        after_json={
            "status": "active",
            "version": promotion.version,
            "activated_at": business_now.isoformat(),
        },
    )
    await create_notification(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        user_id=promotion.created_by,
        category="promotion",
        title=f"促销活动 {promotion.promotion_no} 已到点生效",
        content=f"{promotion.name}（{promotion.promotion_no}）已按店铺时区自动转为生效中。",
        resource_type="promotion",
        resource_id=promotion.id,
    )
    return "active", f"活动 {promotion.promotion_no} 已到点生效。"


async def complete_active_promotion(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    promotion_id: int,
    actor_id: int,
    business_now: datetime,
) -> tuple[str, str]:
    """到期结束调度：active -> completed；若 scheduled 错过窗口也直接结束。"""
    promotion = await session.scalar(
        select(Promotion)
        .where(Promotion.shop_id == shop_id, Promotion.id == promotion_id)
        .with_for_update()
    )
    if promotion is None:
        raise AppError(404, "PROMOTION_NOT_FOUND", "当前店铺没有找到该促销活动。", detail="Promotion not found")
    if promotion.status == "completed":
        return "completed", f"活动 {promotion.promotion_no} 已结束。"
    if promotion.status == "scheduled" and business_now >= promotion.ends_at:
        return await _mark_missed_window_completed(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            promotion=promotion,
            actor_id=actor_id,
            business_now=business_now,
        )
    if promotion.status != "active":
        raise AppError(
            409,
            "INVALID_PROMOTION_STATE",
            f"当前活动状态（{promotion.status}）不能执行结束流转。",
            detail=f"Cannot complete promotion in status {promotion.status}",
        )
    if business_now < promotion.ends_at:
        return "active", f"活动 {promotion.promotion_no} 尚未到达结束时间。"

    before_state = {"status": promotion.status, "version": promotion.version}
    assert_promotion_transition(promotion.status, "completed")
    promotion.status = "completed"
    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="promotion.completed",
        resource_type="promotion",
        resource_id=promotion.id,
        before_json=before_state,
        after_json={
            "status": "completed",
            "version": promotion.version,
            "completed_at": business_now.isoformat(),
        },
    )
    await create_notification(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        user_id=promotion.created_by,
        category="promotion",
        title=f"促销活动 {promotion.promotion_no} 已到期结束",
        content=f"{promotion.name}（{promotion.promotion_no}）活动时段已结束，状态已更新为已结束。",
        resource_type="promotion",
        resource_id=promotion.id,
    )
    return "completed", f"活动 {promotion.promotion_no} 已到期结束。"


async def list_promotions(session: AsyncSession, *, shop_id: int) -> list[PromotionSummary]:
    shop_tz = (await session.scalar(select(Shop.timezone).where(Shop.id == shop_id))) or "Asia/Shanghai"
    query = (
        select(
            Promotion,
            PromotionItem,
            Sku.sku_code,
            Product.name.label("product_name"),
            User.name.label("creator_name"),
            Approval.id.label("latest_approval_id"),
            Approval.approval_no.label("latest_approval_no"),
            Approval.status.label("latest_approval_status"),
        )
        .join(
            PromotionItem,
            and_(
                PromotionItem.shop_id == Promotion.shop_id,
                PromotionItem.promotion_id == Promotion.id,
            ),
        )
        .join(Sku, and_(Sku.shop_id == PromotionItem.shop_id, Sku.id == PromotionItem.sku_id))
        .join(Product, and_(Product.shop_id == Sku.shop_id, Product.id == Sku.product_id))
        .join(User, User.id == Promotion.created_by)
        .outerjoin(
            Approval,
            and_(
                Approval.resource_type == "promotion",
                Approval.resource_id == Promotion.id,
                Approval.resource_version == Promotion.version,
            ),
        )
        .where(Promotion.shop_id == shop_id)
        .order_by(Promotion.created_at.desc(), Promotion.id.desc())
    )
    rows = (await session.execute(query)).all()
    summaries: list[PromotionSummary] = []
    for (
        promotion,
        item,
        sku_code,
        product_name,
        creator_name,
        latest_approval_id,
        latest_approval_no,
        latest_approval_status,
    ) in rows:
        duration_days = max(1, (promotion.ends_at - promotion.starts_at).days)
        summaries.append(
            PromotionSummary(
                id=promotion.id,
                promotion_no=promotion.promotion_no,
                name=promotion.name,
                version=promotion.version,
                status=cast(PromotionStatus, promotion.status),
                sku_code=sku_code,
                product_name=product_name,
                original_price=item.original_price,
                cost_price=item.cost_price,
                discount=discount_from_rate(item.discount_rate),
                promotion_price=item.promotion_price,
                below_cost=item.promotion_price < item.cost_price,
                starts_at=promotion.starts_at,
                ends_at=promotion.ends_at,
                duration_days=duration_days,
                minimum_qty=promotion.minimum_qty,
                timezone=shop_tz,
                created_by=promotion.created_by,
                creator_name=creator_name,
                latest_approval_id=latest_approval_id,
                latest_approval_no=latest_approval_no,
                latest_approval_status=latest_approval_status,
                failure_reason=promotion.failure_reason,
                conflict_promotion_no=promotion.conflict_promotion_no,
                created_at=promotion.created_at,
                updated_at=promotion.updated_at,
            )
        )
    return summaries


async def get_promotion_detail(
    session: AsyncSession, *, shop_id: int, promotion_id: int
) -> PromotionDetail | None:
    shop_tz = (await session.scalar(select(Shop.timezone).where(Shop.id == shop_id))) or "Asia/Shanghai"
    row = (
        await session.execute(
            select(
                Promotion,
                PromotionItem,
                Sku.sku_code,
                Product.name.label("product_name"),
                User.name.label("creator_name"),
            )
            .join(
                PromotionItem,
                and_(
                    PromotionItem.shop_id == Promotion.shop_id,
                    PromotionItem.promotion_id == Promotion.id,
                ),
            )
            .join(Sku, and_(Sku.shop_id == PromotionItem.shop_id, Sku.id == PromotionItem.sku_id))
            .join(Product, and_(Product.shop_id == Sku.shop_id, Product.id == Sku.product_id))
            .join(User, User.id == Promotion.created_by)
            .where(Promotion.shop_id == shop_id, Promotion.id == promotion_id)
        )
    ).one_or_none()
    if row is None:
        return None
    promotion, item, sku_code, product_name, creator_name = row

    requester = User.__table__.alias("requester_user")
    approver = User.__table__.alias("approver_user")
    approval_rows = (
        await session.execute(
            select(
                Approval.id,
                Approval.approval_no,
                Approval.resource_version,
                Approval.status,
                Approval.requester_id,
                requester.c.name.label("requester_name"),
                Approval.approver_id,
                approver.c.name.label("approver_name"),
                Approval.decision_comment,
                Approval.decided_at,
                Approval.created_at,
            )
            .join(requester, requester.c.id == Approval.requester_id)
            .outerjoin(approver, approver.c.id == Approval.approver_id)
            .where(Approval.resource_type == "promotion", Approval.resource_id == promotion.id)
            .order_by(Approval.resource_version.desc(), Approval.id.desc())
        )
    ).mappings()
    approvals = [PromotionApprovalBrief.model_validate(item_row) for item_row in approval_rows]
    latest = approvals[0] if approvals else None
    audit_logs = await list_resource_audit_logs(
        session, shop_id=shop_id, resource_type="promotion", resource_id=promotion.id
    )
    duration_days = max(1, (promotion.ends_at - promotion.starts_at).days)

    return PromotionDetail(
        id=promotion.id,
        promotion_no=promotion.promotion_no,
        name=promotion.name,
        version=promotion.version,
        status=cast(PromotionStatus, promotion.status),
        sku_code=sku_code,
        product_name=product_name,
        original_price=item.original_price,
        cost_price=item.cost_price,
        discount=discount_from_rate(item.discount_rate),
        promotion_price=item.promotion_price,
        below_cost=item.promotion_price < item.cost_price,
        starts_at=promotion.starts_at,
        ends_at=promotion.ends_at,
        duration_days=duration_days,
        minimum_qty=promotion.minimum_qty,
        timezone=shop_tz,
        created_by=promotion.created_by,
        creator_name=creator_name,
        latest_approval_id=latest.id if latest else None,
        latest_approval_no=latest.approval_no if latest else None,
        latest_approval_status=latest.status if latest else None,
        failure_reason=promotion.failure_reason,
        conflict_promotion_no=promotion.conflict_promotion_no,
        created_at=promotion.created_at,
        updated_at=promotion.updated_at,
        item=PromotionItemInfo(
            sku_id=item.sku_id,
            sku_code=sku_code,
            product_name=product_name,
            original_price=item.original_price,
            cost_price=item.cost_price,
            discount=discount_from_rate(item.discount_rate),
            discount_rate=item.discount_rate,
            promotion_price=item.promotion_price,
            below_cost=item.promotion_price < item.cost_price,
        ),
        approvals=approvals,
        audit_logs=audit_logs,
    )
