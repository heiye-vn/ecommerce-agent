from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Approval, Job, Order, Refund, User
from app.models.org import Role
from app.schemas.refunds import (
    RefundCreateRequest,
    RefundDetail,
    RefundEligibility,
    RefundStatus,
    RefundSubmitResponse,
    RefundSummary,
)
from app.services.audit import list_resource_audit_logs, write_audit_log
from app.services.idempotency import claim_idempotency, complete_idempotency
from app.services.notifications import notify_shop_roles
from app.services.orders import (
    REFUNDABLE_ORDER_STATUSES,
    compute_order_refund_eligibility,
)
from app.services.promotions import next_approval_no

ALLOWED_REFUND_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending_approval": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"processing", "failed"}),
    "processing": frozenset({"succeeded", "failed"}),
    "succeeded": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}


def assert_refund_transition(current_status: str, next_status: str) -> None:
    allowed = ALLOWED_REFUND_TRANSITIONS.get(current_status, frozenset())
    if next_status not in allowed:
        raise AppError(
            409,
            "INVALID_REFUND_STATE",
            f"当前退款单状态（{current_status}）不允许流转至 {next_status}。",
            detail=f"Invalid refund transition: {current_status} -> {next_status}",
        )


async def next_refund_no(session: AsyncSession, shop_id: int, business_day: date) -> str:
    await session.execute(text("SELECT pg_advisory_xact_lock(726400, :shop_id)"), {"shop_id": shop_id})
    prefix = f"RFD-{business_day:%Y%m%d}-"
    existing_count = await session.scalar(
        select(func.count(Refund.id)).where(
            Refund.shop_id == shop_id,
            Refund.refund_no.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{(existing_count or 0) + 1:02d}"


async def evaluate_order_refund_eligibility(
    session: AsyncSession,
    *,
    shop_id: int,
    order_id: int,
) -> RefundEligibility | None:
    order = await session.scalar(select(Order).where(Order.shop_id == shop_id, Order.id == order_id))
    if order is None:
        return None
    refunds = list(
        (
            await session.scalars(
                select(Refund).where(Refund.shop_id == shop_id, Refund.order_id == order_id)
            )
        ).all()
    )
    computed = compute_order_refund_eligibility(order, refunds)
    return RefundEligibility(
        order_id=order.id,
        order_no=order.order_no,
        order_status=order.status,
        paid_amount=order.paid_amount,
        refunded_amount=computed.refunded_amount,
        in_flight_amount=computed.in_flight_amount,
        refundable_amount=computed.refundable_amount,
        eligible=computed.eligible,
        block_reason=computed.block_reason,
        in_flight_refund_id=computed.in_flight_refund.id if computed.in_flight_refund else None,
        in_flight_refund_no=computed.in_flight_refund.refund_no if computed.in_flight_refund else None,
    )


async def submit_refund(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    shop_name: str,
    actor_id: int,
    business_day: date,
    payload: RefundCreateRequest,
    idempotency_key: str | None = None,
) -> RefundSubmitResponse:
    normalized_amount = payload.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    normalized_reason = payload.reason.strip()
    canonical_request = {
        "order_id": payload.order_id,
        "amount": str(normalized_amount),
        "reason": normalized_reason,
    }

    claim = None
    if idempotency_key:
        claim = await claim_idempotency(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            operation_type="refund_create",
            idempotency_key=idempotency_key,
            request_payload=canonical_request,
        )
        if claim.replayed_response is not None:
            replayed = RefundSubmitResponse.model_validate(claim.replayed_response)
            replayed.replayed = True
            return replayed

    # 锁定订单行，确保同订单并发发起多笔不同幂等键退款申请时严格串行判定在途唯一性与剩余额度
    order = await session.scalar(
        select(Order).where(Order.shop_id == shop_id, Order.id == payload.order_id).with_for_update()
    )
    if order is None:
        raise AppError(404, "ORDER_NOT_FOUND", "当前店铺没有找到该订单。", detail="Order not found")

    existing_refunds = list(
        (
            await session.scalars(
                select(Refund)
                .where(Refund.shop_id == shop_id, Refund.order_id == order.id)
                .with_for_update()
            )
        ).all()
    )
    eligibility = compute_order_refund_eligibility(order, existing_refunds)

    if order.status not in REFUNDABLE_ORDER_STATUSES:
        raise AppError(
            409,
            "ORDER_NOT_REFUNDABLE",
            eligibility.block_reason or "当前订单状态不允许发起退款。",
            detail=f"Order status {order.status} is not refundable",
        )
    if eligibility.in_flight_refund is not None:
        raise AppError(
            409,
            "REFUND_IN_FLIGHT_EXISTS",
            eligibility.block_reason or "该订单已有待处理退款申请，不可重复发起。",
            detail=f"Order already has in-flight refund {eligibility.in_flight_refund.refund_no}",
        )
    if normalized_amount > eligibility.refundable_amount:
        raise AppError(
            422,
            "REFUND_AMOUNT_EXCEEDS_REMAINING",
            f"申请退款金额 ¥{normalized_amount:.2f} 超过当前可退余额 ¥{eligibility.refundable_amount:.2f}。",
            detail=(
                f"Refund amount {normalized_amount} exceeds refundable balance {eligibility.refundable_amount}"
            ),
        )

    refund_no = await next_refund_no(session, shop_id, business_day)
    approval_no = await next_approval_no(session, shop_id, business_day)

    refund = Refund(
        organization_id=organization_id,
        shop_id=shop_id,
        order_id=order.id,
        refund_no=refund_no,
        amount=normalized_amount,
        reason=normalized_reason,
        status="pending_approval",
        requester_id=actor_id,
    )
    session.add(refund)
    await session.flush()

    snapshot = {
        "refund_id": refund.id,
        "refund_no": refund_no,
        "order_id": order.id,
        "order_no": order.order_no,
        "order_status": order.status,
        "order_paid_amount": str(order.paid_amount),
        "refunded_amount": str(eligibility.refunded_amount),
        "refundable_amount": str(eligibility.refundable_amount),
        "amount": str(normalized_amount),
        "reason": normalized_reason,
        "shop_name": shop_name,
        "sandbox_channel": "本地退款沙箱",
    }
    approval = Approval(
        organization_id=organization_id,
        shop_id=shop_id,
        approval_no=approval_no,
        action_type="refund_create",
        resource_type="refund",
        resource_id=refund.id,
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
        action="refund.submitted",
        resource_type="refund",
        resource_id=refund.id,
        before_json=None,
        after_json={
            "refund_no": refund_no,
            "order_no": order.order_no,
            "amount": str(normalized_amount),
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
        title=f"待审批退款申请：{refund_no}",
        content=(
            f"订单 {order.order_no} 申请退款 ¥{normalized_amount:.2f}"
            f"（原因：{normalized_reason}），请前往审批中心处理。"
        ),
        resource_type="approval",
        resource_id=approval.id,
        exclude_user_id=actor_id,
    )

    response = RefundSubmitResponse(
        refund_id=refund.id,
        refund_no=refund.refund_no,
        order_id=order.id,
        order_no=order.order_no,
        amount=normalized_amount,
        status="pending_approval",
        approval_id=approval.id,
        approval_no=approval.approval_no,
        approval_status="pending",
        replayed=False,
        message=f"已提交退款审批（审批单 {approval.approval_no}），批准后将由后台沙箱复核并执行退款。",
    )
    if claim is not None:
        complete_idempotency(
            claim.record,
            resource_type="refund",
            resource_id=refund.id,
            response_payload=response.model_dump(mode="json"),
        )
    return response


async def cancel_refund(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    refund_id: int,
    actor_id: int,
    actor_roles: list[Role],
) -> RefundDetail:
    refund = await session.scalar(
        select(Refund).where(Refund.shop_id == shop_id, Refund.id == refund_id).with_for_update()
    )
    if refund is None:
        raise AppError(404, "REFUND_NOT_FOUND", "当前店铺没有找到该退款单。", detail="Refund not found")
    if refund.requester_id != actor_id and Role.ADMIN not in actor_roles:
        raise AppError(
            403,
            "REFUND_CANCEL_FORBIDDEN",
            "仅原申请人或管理员可撤回待审批退款单。",
            detail="Only requester or admin can cancel pending refund",
        )
    assert_refund_transition(refund.status, "cancelled")

    before_status = refund.status
    refund.status = "cancelled"
    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.shop_id == shop_id,
            Approval.resource_type == "refund",
            Approval.resource_id == refund.id,
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
        action="refund.cancelled",
        resource_type="refund",
        resource_id=refund.id,
        before_json={"status": before_status},
        after_json={"status": "cancelled"},
        metadata_json={"approval_id": approval.id if approval else None},
    )
    detail = await get_refund_detail(session, shop_id=shop_id, refund_id=refund.id)
    assert detail is not None
    return detail


async def execute_approved_refund(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    refund_id: int,
    actor_id: int,
    business_now: datetime,
) -> tuple[str, str]:
    refund = await session.scalar(
        select(Refund).where(Refund.shop_id == shop_id, Refund.id == refund_id).with_for_update()
    )
    if refund is None:
        raise AppError(404, "REFUND_NOT_FOUND", "未找到待执行的退款单。", detail="Refund not found")

    if refund.status == "succeeded":
        return "succeeded", f"退款单 {refund.refund_no} 已执行成功（幂等跳过）。"
    if refund.status == "failed":
        return "failed", refund.failure_reason or "退款复核失败，未重复扣退。"
    if refund.status not in ("approved", "processing"):
        raise AppError(
            409,
            "INVALID_REFUND_STATE",
            f"退款单当前状态为 {refund.status}，无法进入沙箱执行。",
            detail=f"Refund {refund.id} status {refund.status} cannot execute",
        )

    order = await session.scalar(
        select(Order).where(Order.shop_id == shop_id, Order.id == refund.order_id).with_for_update()
    )
    if order is None:
        reason = "关联订单不存在，无法执行退款。"
        refund.status = "failed"
        refund.failure_reason = reason
        await write_audit_log(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="refund.execution_failed",
            resource_type="refund",
            resource_id=refund.id,
            before_json={"status": "approved"},
            after_json={"status": "failed", "failure_reason": reason},
        )
        return "failed", reason

    other_succeeded = sum(
        (
            item.amount
            for item in (
                await session.scalars(
                    select(Refund).where(
                        Refund.shop_id == shop_id,
                        Refund.order_id == order.id,
                        Refund.status == "succeeded",
                        Refund.id != refund.id,
                    )
                )
            ).all()
        ),
        start=Decimal("0.00"),
    )
    remaining = max(Decimal("0.00"), order.paid_amount - other_succeeded)

    failure_reason: str | None = None
    if order.status not in REFUNDABLE_ORDER_STATUSES:
        failure_reason = f"订单当前状态为 {order.status}，已不满足退款资格。"
    elif refund.amount > remaining:
        failure_reason = (
            f"执行前实时复核失败：拟退款 ¥{refund.amount:.2f} 超过当前剩余可退余额 ¥{remaining:.2f}。"
        )

    if failure_reason is not None:
        refund.status = "failed"
        refund.failure_reason = failure_reason
        await write_audit_log(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="refund.execution_failed",
            resource_type="refund",
            resource_id=refund.id,
            before_json={"status": "approved"},
            after_json={"status": "failed", "failure_reason": failure_reason},
            metadata_json={"order_no": order.order_no, "remaining_amount": str(remaining)},
        )
        return "failed", failure_reason

    if refund.status == "approved":
        assert_refund_transition(refund.status, "processing")
        refund.status = "processing"
        await session.flush()

    assert_refund_transition(refund.status, "succeeded")
    prev_order_status = order.status
    new_total_refunded = other_succeeded + refund.amount
    next_order_status = "refunded" if new_total_refunded >= order.paid_amount else "partially_refunded"

    refund.status = "succeeded"
    refund.failure_reason = None
    refund.processed_at = datetime.now(UTC)
    order.status = next_order_status

    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="refund.succeeded",
        resource_type="refund",
        resource_id=refund.id,
        before_json={"refund_status": "processing", "order_status": prev_order_status},
        after_json={
            "refund_status": "succeeded",
            "order_status": next_order_status,
            "refunded_total": str(new_total_refunded),
            "executed_at": business_now.isoformat(),
        },
        metadata_json={"order_id": order.id, "order_no": order.order_no, "amount": str(refund.amount)},
    )
    return (
        "succeeded",
        (
            f"退款单 {refund.refund_no} 已通过沙箱执行完成，退款 ¥{refund.amount:.2f}，"
            f"订单状态更新为 {next_order_status}。"
        ),
    )


async def _latest_refund_approvals(
    session: AsyncSession,
    *,
    shop_id: int,
    refund_ids: list[int],
) -> dict[int, Approval]:
    if not refund_ids:
        return {}
    rows = (
        await session.scalars(
            select(Approval)
            .where(
                Approval.shop_id == shop_id,
                Approval.resource_type == "refund",
                Approval.resource_id.in_(refund_ids),
            )
            .order_by(Approval.id.desc())
        )
    ).all()
    mapping: dict[int, Approval] = {}
    for row in rows:
        mapping.setdefault(row.resource_id, row)
    return mapping


async def list_refunds(
    session: AsyncSession,
    *,
    shop_id: int,
    status: str | None = None,
) -> list[RefundSummary]:
    query = (
        select(Refund, Order.order_no, Order.status, Order.paid_amount, User.name)
        .join(Order, (Order.shop_id == Refund.shop_id) & (Order.id == Refund.order_id))
        .join(User, User.id == Refund.requester_id)
        .where(Refund.shop_id == shop_id)
        .order_by(Refund.created_at.desc(), Refund.id.desc())
    )
    if status:
        query = query.where(Refund.status == status)
    rows = (await session.execute(query)).all()
    approvals_map = await _latest_refund_approvals(
        session, shop_id=shop_id, refund_ids=[refund.id for refund, *_ in rows]
    )
    summaries: list[RefundSummary] = []
    for refund, order_no, order_status, order_paid_amount, requester_name in rows:
        approval = approvals_map.get(refund.id)
        summaries.append(
            RefundSummary(
                id=refund.id,
                refund_no=refund.refund_no,
                order_id=refund.order_id,
                order_no=order_no,
                order_status=order_status,
                order_paid_amount=order_paid_amount,
                amount=refund.amount,
                reason=refund.reason,
                status=cast(RefundStatus, refund.status),
                requester_id=refund.requester_id,
                requester_name=requester_name,
                failure_reason=refund.failure_reason,
                processed_at=refund.processed_at,
                approval_id=approval.id if approval else None,
                approval_no=approval.approval_no if approval else None,
                approval_status=approval.status if approval else None,
                created_at=refund.created_at,
                updated_at=refund.updated_at,
            )
        )
    return summaries


async def get_refund_detail(
    session: AsyncSession,
    *,
    shop_id: int,
    refund_id: int,
) -> RefundDetail | None:
    row = (
        await session.execute(
            select(Refund, Order, User.name)
            .join(Order, (Order.shop_id == Refund.shop_id) & (Order.id == Refund.order_id))
            .join(User, User.id == Refund.requester_id)
            .where(Refund.shop_id == shop_id, Refund.id == refund_id)
        )
    ).one_or_none()
    if row is None:
        return None
    refund, order, requester_name = row

    order_refunds = list(
        (
            await session.scalars(
                select(Refund).where(Refund.shop_id == shop_id, Refund.order_id == order.id)
            )
        ).all()
    )
    eligibility = compute_order_refund_eligibility(order, order_refunds)

    approval = (
        await session.scalars(
            select(Approval)
            .where(
                Approval.shop_id == shop_id,
                Approval.resource_type == "refund",
                Approval.resource_id == refund.id,
            )
            .order_by(Approval.id.desc())
        )
    ).first()
    job = (
        await session.scalars(
            select(Job)
            .where(
                Job.shop_id == shop_id,
                Job.resource_type == "refund",
                Job.resource_id == refund.id,
            )
            .order_by(Job.id.desc())
        )
    ).first()
    audit_logs = await list_resource_audit_logs(
        session,
        shop_id=shop_id,
        resource_type="refund",
        resource_id=refund.id,
    )
    return RefundDetail(
        id=refund.id,
        refund_no=refund.refund_no,
        order_id=refund.order_id,
        order_no=order.order_no,
        order_status=order.status,
        order_paid_amount=order.paid_amount,
        amount=refund.amount,
        reason=refund.reason,
        status=cast(RefundStatus, refund.status),
        requester_id=refund.requester_id,
        requester_name=requester_name,
        failure_reason=refund.failure_reason,
        processed_at=refund.processed_at,
        approval_id=approval.id if approval else None,
        approval_no=approval.approval_no if approval else None,
        approval_status=approval.status if approval else None,
        created_at=refund.created_at,
        updated_at=refund.updated_at,
        refunded_amount=eligibility.refunded_amount,
        refundable_amount=eligibility.refundable_amount,
        job_id=job.id if job else None,
        job_no=job.job_no if job else None,
        job_status=job.status if job else None,
        audit_logs=audit_logs,
    )
