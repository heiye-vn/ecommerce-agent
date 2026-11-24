from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.auth import AppConfig, ApproverShop, AuthorizedShop, Clock, Database, reject_cross_origin
from app.core.errors import AppError
from app.schemas.approvals import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalDetail,
    ApprovalScope,
    ApprovalSummary,
)
from app.services import approvals as approval_service

router = APIRouter(prefix="/api/shops/{shop_id}/approvals", tags=["approvals"])


@router.get("")
async def get_approvals(
    shop: AuthorizedShop,
    session: Database,
    scope: Annotated[ApprovalScope, Query()] = "all",
) -> list[ApprovalSummary]:
    return await approval_service.list_approvals(
        session,
        shop_id=shop.id,
        actor_id=shop.user_id,
        actor_roles=shop.roles,
        scope=scope,
    )


@router.get("/{approval_id}")
async def get_approval(
    shop: AuthorizedShop,
    session: Database,
    approval_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> ApprovalDetail:
    detail = await approval_service.get_approval_detail(
        session,
        shop_id=shop.id,
        approval_id=approval_id,
        actor_id=shop.user_id,
        actor_roles=shop.roles,
    )
    if detail is None:
        raise AppError(404, "APPROVAL_NOT_FOUND", "当前店铺没有找到该审批单。", detail="Approval not found")
    return detail


@router.post("/{approval_id}/approve", dependencies=[Depends(reject_cross_origin)])
async def approve_approval(
    shop: ApproverShop,
    session: Database,
    clock: Clock,
    settings: AppConfig,
    approval_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
    payload: ApprovalDecisionRequest | None = None,
) -> ApprovalDecisionResponse:
    return await approval_service.decide_approval(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        approval_id=approval_id,
        actor_id=shop.user_id,
        actor_roles=shop.roles,
        decision="approve",
        comment=payload.comment if payload else None,
        business_now=clock.now(shop.timezone),
        execute_schedule=settings.async_execution_mode == "inline",
    )


@router.post("/{approval_id}/reject", dependencies=[Depends(reject_cross_origin)])
async def reject_approval(
    payload: ApprovalDecisionRequest,
    shop: ApproverShop,
    session: Database,
    clock: Clock,
    approval_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> ApprovalDecisionResponse:
    return await approval_service.decide_approval(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        approval_id=approval_id,
        actor_id=shop.user_id,
        actor_roles=shop.roles,
        decision="reject",
        comment=payload.comment,
        business_now=clock.now(shop.timezone),
    )
