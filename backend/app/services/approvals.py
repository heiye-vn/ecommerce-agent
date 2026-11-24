from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Approval, Promotion, Shop, User
from app.models.org import Role
from app.schemas.approvals import (
    ApprovalDecisionResponse,
    ApprovalDetail,
    ApprovalScope,
    ApprovalStatus,
    ApprovalSummary,
)
from app.services.audit import list_resource_audit_logs, write_audit_log
from app.services.jobs import enqueue_job_with_outbox, run_loaded_job
from app.services.notifications import create_notification
from app.services.promotions import assert_promotion_transition


def _decision_boundary(
    approval: Approval,
    *,
    actor_id: int,
    actor_roles: list[Role],
) -> tuple[bool, str | None]:
    if approval.status != "pending":
        return False, "该审批单已完成处理或已撤回，不可再次裁决。"
    if Role.APPROVER not in actor_roles:
        return False, "当前账号在本店没有审批权限，仅可查看审批快照。"
    if approval.requester_id == actor_id:
        return False, "禁止自审：申请人不能审批自己发起的方案，请由本店其他审批员处理。"
    return True, None


async def list_approvals(
    session: AsyncSession,
    *,
    shop_id: int,
    actor_id: int,
    actor_roles: list[Role],
    scope: ApprovalScope = "all",
) -> list[ApprovalSummary]:
    requester = User.__table__.alias("approval_requester")
    approver = User.__table__.alias("approval_approver")
    query = (
        select(
            Approval,
            requester.c.name.label("requester_name"),
            approver.c.name.label("approver_name"),
        )
        .join(requester, requester.c.id == Approval.requester_id)
        .outerjoin(approver, approver.c.id == Approval.approver_id)
        .where(Approval.shop_id == shop_id)
        .order_by(Approval.created_at.desc(), Approval.id.desc())
    )
    if scope == "pending_for_me":
        if Role.APPROVER not in actor_roles:
            return []
        query = query.where(Approval.status == "pending", Approval.requester_id != actor_id)
    elif scope == "processed_by_me":
        query = query.where(Approval.approver_id == actor_id)
    elif scope == "requested_by_me":
        query = query.where(Approval.requester_id == actor_id)

    results: list[ApprovalSummary] = []
    for approval, requester_name, approver_name in (await session.execute(query)).all():
        snapshot = approval.payload_snapshot or {}
        can_decide, _ = _decision_boundary(approval, actor_id=actor_id, actor_roles=actor_roles)
        results.append(
            ApprovalSummary(
                id=approval.id,
                approval_no=approval.approval_no,
                action_type=approval.action_type,
                resource_type=approval.resource_type,
                resource_id=approval.resource_id,
                resource_no=str(snapshot.get("promotion_no") or approval.resource_id),
                resource_name=str(snapshot.get("promotion_name") or ""),
                resource_version=approval.resource_version,
                sku_code=snapshot.get("sku_code"),
                snapshot_amount=str(snapshot["promotion_price"]) if "promotion_price" in snapshot else None,
                status=cast(ApprovalStatus, approval.status),
                requester_id=approval.requester_id,
                requester_name=requester_name,
                approver_id=approval.approver_id,
                approver_name=approver_name,
                decision_comment=approval.decision_comment,
                decided_at=approval.decided_at,
                created_at=approval.created_at,
                can_decide=can_decide,
            )
        )
    return results


async def get_approval_detail(
    session: AsyncSession,
    *,
    shop_id: int,
    approval_id: int,
    actor_id: int,
    actor_roles: list[Role],
) -> ApprovalDetail | None:
    requester = User.__table__.alias("detail_requester")
    approver = User.__table__.alias("detail_approver")
    row = (
        await session.execute(
            select(
                Approval,
                Shop.name.label("shop_name"),
                requester.c.name.label("requester_name"),
                approver.c.name.label("approver_name"),
            )
            .join(Shop, Shop.id == Approval.shop_id)
            .join(requester, requester.c.id == Approval.requester_id)
            .outerjoin(approver, approver.c.id == Approval.approver_id)
            .where(Approval.id == approval_id)
        )
    ).one_or_none()
    if row is None:
        return None
    approval, shop_name, requester_name, approver_name = row
    if approval.shop_id != shop_id:
        raise AppError(
            403,
            "CROSS_SHOP_APPROVAL_DENIED",
            "无权跨店铺查看或处理其他店铺的审批单。",
            detail="Cross-shop approval access denied",
        )

    resource_status: str | None = None
    resource_failure_reason: str | None = None
    resource_conflict_no: str | None = None
    if approval.resource_type == "promotion":
        promotion = await session.scalar(
            select(Promotion).where(Promotion.shop_id == shop_id, Promotion.id == approval.resource_id)
        )
        if promotion is not None:
            resource_status = promotion.status
            resource_failure_reason = promotion.failure_reason
            resource_conflict_no = promotion.conflict_promotion_no

    audit_logs = await list_resource_audit_logs(
        session,
        shop_id=shop_id,
        resource_type=approval.resource_type,
        resource_id=approval.resource_id,
    )
    snapshot = approval.payload_snapshot or {}
    can_decide, cannot_reason = _decision_boundary(approval, actor_id=actor_id, actor_roles=actor_roles)

    return ApprovalDetail(
        id=approval.id,
        approval_no=approval.approval_no,
        action_type=approval.action_type,
        resource_type=approval.resource_type,
        resource_id=approval.resource_id,
        resource_no=str(snapshot.get("promotion_no") or approval.resource_id),
        resource_name=str(snapshot.get("promotion_name") or ""),
        resource_version=approval.resource_version,
        sku_code=snapshot.get("sku_code"),
        snapshot_amount=str(snapshot["promotion_price"]) if "promotion_price" in snapshot else None,
        status=cast(ApprovalStatus, approval.status),
        requester_id=approval.requester_id,
        requester_name=requester_name,
        approver_id=approval.approver_id,
        approver_name=approver_name,
        decision_comment=approval.decision_comment,
        decided_at=approval.decided_at,
        created_at=approval.created_at,
        can_decide=can_decide,
        shop_id=approval.shop_id,
        shop_name=shop_name,
        payload_snapshot=snapshot,
        resource_status=resource_status,
        resource_failure_reason=resource_failure_reason,
        resource_conflict_promotion_no=resource_conflict_no,
        cannot_decide_reason=cannot_reason,
        audit_logs=audit_logs,
    )


async def decide_approval(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    approval_id: int,
    actor_id: int,
    actor_roles: list[Role],
    decision: str,
    comment: str | None,
    business_now: datetime,
    execute_schedule: bool = True,
) -> ApprovalDecisionResponse:
    if Role.APPROVER not in actor_roles:
        raise AppError(
            403,
            "SHOP_ROLE_FORBIDDEN",
            "当前店铺角色无权执行审批裁决。",
            detail="Approver role required",
        )

    approval = await session.scalar(
        select(Approval).where(Approval.id == approval_id).with_for_update()
    )
    if approval is None:
        raise AppError(404, "APPROVAL_NOT_FOUND", "未找到对应的审批单。", detail="Approval not found")
    if approval.shop_id != shop_id:
        raise AppError(
            403,
            "CROSS_SHOP_APPROVAL_DENIED",
            "禁止跨店审批：该审批单属于其他店铺。",
            detail="Cross-shop approval denied",
        )
    if approval.status != "pending":
        raise AppError(
            409,
            "APPROVAL_ALREADY_DECIDED",
            "该审批单已处理完成，不能重复裁决。",
            detail=f"Approval already in status {approval.status}",
        )
    if approval.requester_id == actor_id:
        raise AppError(
            403,
            "SELF_APPROVAL_FORBIDDEN",
            "禁止自审：申请人不能审批自己提交的方案。",
            detail="Requester cannot approve own request",
        )

    if decision == "reject" and not (comment and comment.strip()):
        raise AppError(
            422,
            "REJECTION_REASON_REQUIRED",
            "驳回审批时必须填写处理意见。",
            detail="Rejection comment is required",
        )

    now = datetime.now(UTC)
    approval.approver_id = actor_id
    approval.decision_comment = comment.strip() if comment else None
    approval.decided_at = now

    if approval.resource_type != "promotion":
        raise AppError(
            400,
            "UNSUPPORTED_APPROVAL_RESOURCE",
            f"暂不支持审批资源类型 {approval.resource_type}。",
            detail="Unsupported approval resource type",
        )

    promotion = await session.scalar(
        select(Promotion)
        .where(Promotion.shop_id == shop_id, Promotion.id == approval.resource_id)
        .with_for_update()
    )
    if promotion is None:
        raise AppError(404, "PROMOTION_NOT_FOUND", "未找到关联的促销活动。", detail="Promotion not found")
    if promotion.version != approval.resource_version:
        raise AppError(
            409,
            "STALE_PROMOTION_VERSION",
            f"审批单版本（V{approval.resource_version}）与活动当前版本（V{promotion.version}）不一致。",
            detail="Approval version does not match current promotion version",
        )

    before_state = {"approval_status": "pending", "promotion_status": promotion.status, "version": promotion.version}
    if decision == "reject":
        assert_promotion_transition(promotion.status, "rejected")
        approval.status = "rejected"
        promotion.status = "rejected"
        await write_audit_log(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action="approval.rejected",
            resource_type="promotion",
            resource_id=promotion.id,
            before_json=before_state,
            after_json={
                "approval_status": "rejected",
                "promotion_status": "rejected",
                "version": promotion.version,
                "comment": approval.decision_comment,
            },
            metadata_json={"approval_id": approval.id, "approval_no": approval.approval_no},
        )
        await create_notification(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            user_id=approval.requester_id,
            category="approval",
            title=f"审批单 {approval.approval_no} 已驳回",
            content=f"{promotion.name}（{promotion.promotion_no}）被驳回：{approval.decision_comment}。",
            resource_type="promotion",
            resource_id=promotion.id,
        )
        detail = await get_approval_detail(
            session, shop_id=shop_id, approval_id=approval.id, actor_id=actor_id, actor_roles=actor_roles
        )
        assert detail is not None
        return ApprovalDecisionResponse(
            approval=detail,
            execution_status="rejected",
            execution_message="已驳回该促销方案，运营可在活动详情中修订 V2 后重新提交审批。",
        )

    assert_promotion_transition(promotion.status, "approved")
    approval.status = "approved"
    promotion.status = "approved"
    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="approval.approved",
        resource_type="promotion",
        resource_id=promotion.id,
        before_json=before_state,
        after_json={
            "approval_status": "approved",
            "promotion_status": "approved",
            "version": promotion.version,
            "comment": approval.decision_comment,
        },
        metadata_json={"approval_id": approval.id, "approval_no": approval.approval_no},
    )
    await create_notification(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        user_id=approval.requester_id,
        category="approval",
        title=f"审批单 {approval.approval_no} 已批准",
        content=f"{promotion.name}（{promotion.promotion_no} · V{promotion.version}）已获批，即将复核并排期。",
        resource_type="promotion",
        resource_id=promotion.id,
    )

    job_payload = {
        "promotion_id": promotion.id,
        "promotion_no": promotion.promotion_no,
        "promotion_name": promotion.name,
        "approval_id": approval.id,
        "approval_no": approval.approval_no,
        "requester_id": approval.requester_id,
        "actor_id": actor_id,
    }
    job, _ = await enqueue_job_with_outbox(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        job_type="promotion_schedule",
        resource_type="promotion",
        resource_id=promotion.id,
        resource_version=approval.resource_version,
        idempotency_key=f"promotion-schedule-{promotion.id}-v{approval.resource_version}",
        payload=job_payload,
        business_day=business_now.date(),
        emit_outbox=not execute_schedule,
    )

    if execute_schedule:
        execution_status, execution_message = await run_loaded_job(
            session,
            job=job,
            business_now=business_now,
        )
    else:
        execution_status = "queued"
        execution_message = f"审批已通过，排期任务 {job.job_no} 已入队等待后台复核与排期。"

    detail = await get_approval_detail(
        session, shop_id=shop_id, approval_id=approval.id, actor_id=actor_id, actor_roles=actor_roles
    )
    assert detail is not None
    return ApprovalDecisionResponse(
        approval=detail,
        execution_status=execution_status,
        execution_message=execution_message,
        job_id=job.id,
        job_no=job.job_no,
    )
