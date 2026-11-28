from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Approval, Job, PriceChangeRequest, PriceHistory, Product, Sku, User
from app.models.org import Role
from app.schemas.price_changes import (
    PriceChangeCreateRequest,
    PriceChangeDetail,
    PriceChangeStatus,
    PriceChangeSubmitResponse,
    PriceChangeSummary,
    PriceHistoryItem,
)
from app.services.audit import list_resource_audit_logs, write_audit_log
from app.services.idempotency import claim_idempotency, complete_idempotency
from app.services.notifications import notify_shop_roles
from app.services.promotions import next_approval_no

IN_FLIGHT_PRICE_CHANGE_STATUSES = ("pending_approval", "approved", "processing")

ALLOWED_PRICE_CHANGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending_approval": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"processing", "failed"}),
    "processing": frozenset({"succeeded", "failed"}),
    "succeeded": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}


def assert_price_change_transition(current_status: str, next_status: str) -> None:
    allowed = ALLOWED_PRICE_CHANGE_TRANSITIONS.get(current_status, frozenset())
    if next_status not in allowed:
        raise AppError(
            409,
            "INVALID_PRICE_CHANGE_STATE",
            f"当前调价申请状态（{current_status}）不允许流转至 {next_status}。",
            detail=f"Invalid price change transition: {current_status} -> {next_status}",
        )


async def next_price_change_no(session: AsyncSession, shop_id: int, business_day: date) -> str:
    await session.execute(text("SELECT pg_advisory_xact_lock(726500, :shop_id)"), {"shop_id": shop_id})
    prefix = f"PRC-{business_day:%Y%m%d}-"
    existing_count = await session.scalar(
        select(func.count(PriceChangeRequest.id)).where(
            PriceChangeRequest.shop_id == shop_id,
            PriceChangeRequest.request_no.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{(existing_count or 0) + 1:02d}"


async def list_price_histories(
    session: AsyncSession,
    *,
    shop_id: int,
    sku_code: str | None = None,
) -> list[PriceHistoryItem]:
    query = (
        select(PriceHistory, Sku.sku_code, Product.name, User.name)
        .join(Sku, and_(Sku.id == PriceHistory.sku_id, Sku.shop_id == shop_id))
        .join(Product, and_(Product.id == Sku.product_id, Product.shop_id == Sku.shop_id))
        .outerjoin(User, User.id == PriceHistory.actor_id)
        .order_by(PriceHistory.changed_at.desc(), PriceHistory.id.desc())
    )
    if sku_code:
        query = query.where(Sku.sku_code == sku_code.strip().upper())

    rows = (await session.execute(query)).all()
    return [
        PriceHistoryItem(
            id=history.id,
            sku_id=history.sku_id,
            sku_code=code,
            product_name=product_name,
            old_price=history.old_price,
            new_price=history.new_price,
            changed_at=history.changed_at,
            actor_id=history.actor_id,
            actor_name=actor_name or "沙箱初始化",
            approval_no=history.approval_no,
            price_change_request_id=history.price_change_request_id,
            reason=history.reason,
        )
        for history, code, product_name, actor_name in rows
    ]


async def submit_price_change(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    shop_name: str,
    actor_id: int,
    business_day: date,
    payload: PriceChangeCreateRequest,
    idempotency_key: str | None = None,
) -> PriceChangeSubmitResponse:
    normalized_sku = payload.sku_code.strip().upper()
    normalized_price = payload.new_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    normalized_reason = payload.reason.strip()
    canonical_request = {
        "sku_code": normalized_sku,
        "new_price": str(normalized_price),
        "reason": normalized_reason,
    }

    claim = None
    if idempotency_key:
        claim = await claim_idempotency(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            operation_type="price_change_create",
            idempotency_key=idempotency_key,
            request_payload=canonical_request,
        )
        if claim.replayed_response is not None:
            replayed = PriceChangeSubmitResponse.model_validate(claim.replayed_response)
            replayed.replayed = True
            return replayed

    # 锁定 SKU 行，串行判定该 SKU 的在途唯一性与成本/状态约束
    row = (
        await session.execute(
            select(Sku, Product.name)
            .join(Product, and_(Product.id == Sku.product_id, Product.shop_id == Sku.shop_id))
            .where(Sku.shop_id == shop_id, Sku.sku_code == normalized_sku)
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise AppError(404, "SKU_NOT_FOUND", f"当前店铺没有找到 {normalized_sku}。", detail="SKU not found")
    sku, product_name = row

    if sku.status != "active":
        raise AppError(
            409,
            "SKU_NOT_ACTIVE",
            f"SKU {sku.sku_code} 当前状态为 {sku.status}（非在售），不可发起调价。",
            detail=f"SKU {sku.sku_code} is not active",
        )
    if normalized_price == sku.current_price:
        raise AppError(
            422,
            "PRICE_UNCHANGED",
            f"拟调整售价 ¥{normalized_price:.2f} 与当前售价一致，无需提交调价。",
            detail="New price equals current price",
        )
    if normalized_price < sku.cost:
        raise AppError(
            422,
            "PRICE_BELOW_COST",
            f"拟调整售价 ¥{normalized_price:.2f} 低于成本价 ¥{sku.cost:.2f}，已被规则拦截。",
            detail=f"New price {normalized_price} is below cost {sku.cost}",
        )

    existing_in_flight = await session.scalar(
        select(PriceChangeRequest)
        .where(
            PriceChangeRequest.shop_id == shop_id,
            PriceChangeRequest.sku_id == sku.id,
            PriceChangeRequest.status.in_(IN_FLIGHT_PRICE_CHANGE_STATUSES),
        )
        .with_for_update()
    )
    if existing_in_flight is not None:
        raise AppError(
            409,
            "PRICE_CHANGE_IN_FLIGHT_EXISTS",
            f"{sku.sku_code} 已有在途调价申请 {existing_in_flight.request_no}，请等待处理完成或撤回后再操作。",
            detail=f"In-flight price change {existing_in_flight.request_no} already exists",
        )

    request_no = await next_price_change_no(session, shop_id, business_day)
    approval_no = await next_approval_no(session, shop_id, business_day)

    req = PriceChangeRequest(
        organization_id=organization_id,
        shop_id=shop_id,
        sku_id=sku.id,
        request_no=request_no,
        old_price=sku.current_price,
        new_price=normalized_price,
        cost_price=sku.cost,
        reason=normalized_reason,
        status="pending_approval",
        requester_id=actor_id,
    )
    session.add(req)
    await session.flush()

    change_amount = (normalized_price - sku.current_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    change_pct = (
        ((normalized_price - sku.current_price) * Decimal("100") / sku.current_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if sku.current_price > Decimal("0.00")
        else Decimal("0.00")
    )
    snapshot = {
        "request_id": req.id,
        "request_no": request_no,
        "sku_code": sku.sku_code,
        "product_name": product_name,
        "old_price": str(sku.current_price),
        "new_price": str(normalized_price),
        "cost_price": str(sku.cost),
        "change_amount": str(change_amount),
        "change_pct": str(change_pct),
        "reason": normalized_reason,
        "shop_name": shop_name,
    }
    approval = Approval(
        organization_id=organization_id,
        shop_id=shop_id,
        approval_no=approval_no,
        action_type="price_change",
        resource_type="price_change",
        resource_id=req.id,
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
        action="price_change.submitted",
        resource_type="price_change",
        resource_id=req.id,
        before_json=None,
        after_json={
            "request_no": request_no,
            "sku_code": sku.sku_code,
            "old_price": str(sku.current_price),
            "new_price": str(normalized_price),
            "status": "pending_approval",
        },
        metadata_json={"approval_id": approval.id, "approval_no": approval_no},
    )

    await notify_shop_roles(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        roles=(Role.APPROVER, Role.ADMIN),
        category="approval",
        title=f"待审批调价申请：{request_no}",
        content=(
            f"{sku.sku_code}（{product_name}）申请由 ¥{sku.current_price:.2f} 调整为 ¥{normalized_price:.2f}"
            f"（原因：{normalized_reason}），请前往审批中心处理。"
        ),
        resource_type="approval",
        resource_id=approval.id,
        exclude_user_id=actor_id,
    )

    response = PriceChangeSubmitResponse(
        request_id=req.id,
        request_no=req.request_no,
        sku_code=sku.sku_code,
        old_price=sku.current_price,
        new_price=normalized_price,
        status="pending_approval",
        approval_id=approval.id,
        approval_no=approval.approval_no,
        approval_status="pending",
        replayed=False,
        message=f"已提交调价审批（审批单 {approval.approval_no}），批准后将二次复核当前售价并落库。",
    )
    if claim is not None:
        complete_idempotency(
            claim.record,
            resource_type="price_change",
            resource_id=req.id,
            response_payload=response.model_dump(mode="json"),
        )
    return response


async def cancel_price_change(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    request_id: int,
    actor_id: int,
    actor_roles: list[Role],
) -> PriceChangeDetail:
    req = await session.scalar(
        select(PriceChangeRequest)
        .where(PriceChangeRequest.shop_id == shop_id, PriceChangeRequest.id == request_id)
        .with_for_update()
    )
    if req is None:
        raise AppError(404, "PRICE_CHANGE_NOT_FOUND", "当前店铺没有找到该调价申请。", detail="Price change not found")
    if req.requester_id != actor_id and Role.ADMIN not in actor_roles:
        raise AppError(
            403,
            "PRICE_CHANGE_CANCEL_FORBIDDEN",
            "仅原申请人或管理员可撤回待审批调价申请。",
            detail="Only requester or admin can cancel pending price change",
        )
    assert_price_change_transition(req.status, "cancelled")

    before_status = req.status
    req.status = "cancelled"
    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.shop_id == shop_id,
            Approval.resource_type == "price_change",
            Approval.resource_id == req.id,
            Approval.status == "pending",
        )
        .with_for_update()
    )
    if approval is not None:
        approval.status = "cancelled"

    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="price_change.cancelled",
        resource_type="price_change",
        resource_id=req.id,
        before_json={"status": before_status},
        after_json={"status": "cancelled"},
        metadata_json={"approval_id": approval.id if approval else None},
    )
    detail = await get_price_change_detail(session, shop_id=shop_id, request_id=req.id)
    assert detail is not None
    return detail


async def execute_approved_price_change(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    request_id: int,
    actor_id: int,
    business_now: datetime,
) -> tuple[str, str]:
    req = await session.scalar(
        select(PriceChangeRequest)
        .where(PriceChangeRequest.shop_id == shop_id, PriceChangeRequest.id == request_id)
        .with_for_update()
    )
    if req is None:
        raise AppError(404, "PRICE_CHANGE_NOT_FOUND", "未找到待执行的调价申请。", detail="Price change not found")

    if req.status == "succeeded":
        return "succeeded", f"调价单 {req.request_no} 已执行成功（幂等跳过）。"
    if req.status == "failed":
        return "failed", req.failure_reason or "调价执行前复核失败，本次未修改售价。"
    if req.status not in ("approved", "processing"):
        raise AppError(
            409,
            "INVALID_PRICE_CHANGE_STATE",
            f"调价单当前状态为 {req.status}，无法进入执行。",
            detail=f"Price change {req.id} status {req.status} cannot execute",
        )

    sku = await session.scalar(
        select(Sku).where(Sku.shop_id == shop_id, Sku.id == req.sku_id).with_for_update()
    )
    failure_reason: str | None = None
    if sku is None:
        failure_reason = "关联商品规格不存在，本次未修改售价。"
    elif sku.status != "active":
        failure_reason = f"商品 {sku.sku_code} 当前状态为 {sku.status}（已下架），本次未修改售价。"
    elif req.new_price < sku.cost:
        failure_reason = (
            f"执行前复核失败：拟调整售价 ¥{req.new_price:.2f} 低于当前成本价 ¥{sku.cost:.2f}，本次未修改售价。"
        )
    elif sku.current_price != req.old_price:
        failure_reason = (
            f"审批快照的原价为 ¥{req.old_price:.2f}，执行时读到 ¥{sku.current_price:.2f}。本次未修改售价。"
        )

    if failure_reason is not None:
        req.status = "failed"
        req.failure_reason = failure_reason
        await write_audit_log(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="price_change.execution_failed",
            resource_type="price_change",
            resource_id=req.id,
            before_json={"status": "approved", "expected_old_price": str(req.old_price)},
            after_json={
                "status": "failed",
                "failure_reason": failure_reason,
                "observed_price": str(sku.current_price) if sku is not None else None,
            },
            metadata_json={"sku_code": sku.sku_code if sku is not None else None},
        )
        return "failed", failure_reason

    assert sku is not None
    if req.status == "approved":
        assert_price_change_transition(req.status, "processing")
        req.status = "processing"
        await session.flush()

    assert_price_change_transition(req.status, "succeeded")
    approval_no = await session.scalar(
        select(Approval.approval_no)
        .where(
            Approval.shop_id == shop_id,
            Approval.resource_type == "price_change",
            Approval.resource_id == req.id,
        )
        .order_by(Approval.id.desc())
    )

    prev_price = sku.current_price
    sku.current_price = req.new_price
    req.status = "succeeded"
    req.failure_reason = None
    req.executed_at = datetime.now(UTC)

    session.add(
        PriceHistory(
            shop_id=shop_id,
            sku_id=sku.id,
            old_price=prev_price,
            new_price=req.new_price,
            changed_at=business_now,
            actor_id=req.requester_id,
            approval_no=approval_no,
            price_change_request_id=req.id,
            reason=req.reason,
        )
    )
    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="price_change.succeeded",
        resource_type="price_change",
        resource_id=req.id,
        before_json={"price_change_status": "processing", "current_price": str(prev_price)},
        after_json={
            "price_change_status": "succeeded",
            "current_price": str(req.new_price),
            "executed_at": business_now.isoformat(),
        },
        metadata_json={"sku_code": sku.sku_code, "approval_no": approval_no},
    )
    return (
        "succeeded",
        f"调价单 {req.request_no} 执行成功，{sku.sku_code} 售价已由 ¥{prev_price:.2f} 变更为 ¥{req.new_price:.2f}。",
    )


async def _latest_price_change_approvals(
    session: AsyncSession,
    *,
    shop_id: int,
    request_ids: list[int],
) -> dict[int, Approval]:
    if not request_ids:
        return {}
    rows = (
        await session.scalars(
            select(Approval)
            .where(
                Approval.shop_id == shop_id,
                Approval.resource_type == "price_change",
                Approval.resource_id.in_(request_ids),
            )
            .order_by(Approval.id.desc())
        )
    ).all()
    mapping: dict[int, Approval] = {}
    for row in rows:
        mapping.setdefault(row.resource_id, row)
    return mapping


async def list_price_changes(
    session: AsyncSession,
    *,
    shop_id: int,
    sku_code: str | None = None,
    status: str | None = None,
) -> list[PriceChangeSummary]:
    query = (
        select(PriceChangeRequest, Sku.sku_code, Product.name, User.name)
        .join(Sku, and_(Sku.shop_id == PriceChangeRequest.shop_id, Sku.id == PriceChangeRequest.sku_id))
        .join(Product, and_(Product.shop_id == Sku.shop_id, Product.id == Sku.product_id))
        .join(User, User.id == PriceChangeRequest.requester_id)
        .where(PriceChangeRequest.shop_id == shop_id)
        .order_by(PriceChangeRequest.created_at.desc(), PriceChangeRequest.id.desc())
    )
    if sku_code:
        query = query.where(Sku.sku_code == sku_code.strip().upper())
    if status:
        query = query.where(PriceChangeRequest.status == status)

    rows = (await session.execute(query)).all()
    approvals_map = await _latest_price_change_approvals(
        session, shop_id=shop_id, request_ids=[req.id for req, *_ in rows]
    )
    summaries: list[PriceChangeSummary] = []
    for req, code, product_name, requester_name in rows:
        approval = approvals_map.get(req.id)
        summaries.append(
            PriceChangeSummary(
                id=req.id,
                request_no=req.request_no,
                sku_id=req.sku_id,
                sku_code=code,
                product_name=product_name,
                old_price=req.old_price,
                new_price=req.new_price,
                cost_price=req.cost_price,
                reason=req.reason,
                status=cast(PriceChangeStatus, req.status),
                requester_id=req.requester_id,
                requester_name=requester_name,
                failure_reason=req.failure_reason,
                executed_at=req.executed_at,
                approval_id=approval.id if approval else None,
                approval_no=approval.approval_no if approval else None,
                approval_status=approval.status if approval else None,
                created_at=req.created_at,
                updated_at=req.updated_at,
            )
        )
    return summaries


async def get_price_change_detail(
    session: AsyncSession,
    *,
    shop_id: int,
    request_id: int,
) -> PriceChangeDetail | None:
    row = (
        await session.execute(
            select(PriceChangeRequest, Sku, Product.name, User.name)
            .join(Sku, and_(Sku.shop_id == PriceChangeRequest.shop_id, Sku.id == PriceChangeRequest.sku_id))
            .join(Product, and_(Product.shop_id == Sku.shop_id, Product.id == Sku.product_id))
            .join(User, User.id == PriceChangeRequest.requester_id)
            .where(PriceChangeRequest.shop_id == shop_id, PriceChangeRequest.id == request_id)
        )
    ).one_or_none()
    if row is None:
        return None
    req, sku, product_name, requester_name = row

    approval = (
        await session.scalars(
            select(Approval)
            .where(
                Approval.shop_id == shop_id,
                Approval.resource_type == "price_change",
                Approval.resource_id == req.id,
            )
            .order_by(Approval.id.desc())
        )
    ).first()
    job = (
        await session.scalars(
            select(Job)
            .where(
                Job.shop_id == shop_id,
                Job.resource_type == "price_change",
                Job.resource_id == req.id,
            )
            .order_by(Job.id.desc())
        )
    ).first()
    audit_logs = await list_resource_audit_logs(
        session,
        shop_id=shop_id,
        resource_type="price_change",
        resource_id=req.id,
    )
    return PriceChangeDetail(
        id=req.id,
        request_no=req.request_no,
        sku_id=req.sku_id,
        sku_code=sku.sku_code,
        product_name=product_name,
        old_price=req.old_price,
        new_price=req.new_price,
        cost_price=req.cost_price,
        reason=req.reason,
        status=cast(PriceChangeStatus, req.status),
        requester_id=req.requester_id,
        requester_name=requester_name,
        failure_reason=req.failure_reason,
        executed_at=req.executed_at,
        approval_id=approval.id if approval else None,
        approval_no=approval.approval_no if approval else None,
        approval_status=approval.status if approval else None,
        created_at=req.created_at,
        updated_at=req.updated_at,
        current_price=sku.current_price,
        sku_status=sku.status,
        job_id=job.id if job else None,
        job_no=job.job_no if job else None,
        job_status=job.status if job else None,
        audit_logs=audit_logs,
    )
