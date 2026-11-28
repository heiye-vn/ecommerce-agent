from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query

from app.api.auth import AuthorizedShop, Clock, Database, OperatorShop, reject_cross_origin
from app.core.errors import AppError
from app.schemas.refunds import (
    RefundCreateRequest,
    RefundDetail,
    RefundStatus,
    RefundSubmitResponse,
    RefundSummary,
)
from app.services import refunds as refund_service

router = APIRouter(prefix="/api/shops/{shop_id}/refunds", tags=["refunds"])
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


@router.get("")
async def get_refunds(
    shop: AuthorizedShop,
    session: Database,
    status: Annotated[RefundStatus | None, Query()] = None,
) -> list[RefundSummary]:
    return await refund_service.list_refunds(session, shop_id=shop.id, status=status)


@router.post("", dependencies=[Depends(reject_cross_origin)])
async def create_refund(
    payload: RefundCreateRequest,
    shop: OperatorShop,
    session: Database,
    clock: Clock,
    idempotency_key: IdempotencyHeader = None,
) -> RefundSubmitResponse:
    return await refund_service.submit_refund(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        shop_name=shop.name,
        actor_id=shop.user_id,
        business_day=clock.business_date(shop.timezone),
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.get("/{refund_id}")
async def get_refund(
    shop: AuthorizedShop,
    session: Database,
    refund_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> RefundDetail:
    detail = await refund_service.get_refund_detail(session, shop_id=shop.id, refund_id=refund_id)
    if detail is None:
        raise AppError(404, "REFUND_NOT_FOUND", "当前店铺没有找到该退款单。", detail="Refund not found")
    return detail


@router.post("/{refund_id}/cancel", dependencies=[Depends(reject_cross_origin)])
async def cancel_refund_route(
    shop: OperatorShop,
    session: Database,
    refund_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> RefundDetail:
    return await refund_service.cancel_refund(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        refund_id=refund_id,
        actor_id=shop.user_id,
        actor_roles=shop.roles,
    )
