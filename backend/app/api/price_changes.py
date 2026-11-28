from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query

from app.api.auth import AuthorizedShop, Clock, Database, OperatorShop, reject_cross_origin
from app.core.errors import AppError
from app.schemas.price_changes import (
    PriceChangeCreateRequest,
    PriceChangeDetail,
    PriceChangeStatus,
    PriceChangeSubmitResponse,
    PriceChangeSummary,
    PriceHistoryItem,
)
from app.services import price_changes as price_change_service

router = APIRouter(prefix="/api/shops/{shop_id}/price-changes", tags=["price-changes"])
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


@router.get("")
async def get_price_changes(
    shop: AuthorizedShop,
    session: Database,
    sku_code: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    status: Annotated[PriceChangeStatus | None, Query()] = None,
) -> list[PriceChangeSummary]:
    return await price_change_service.list_price_changes(
        session, shop_id=shop.id, sku_code=sku_code, status=status
    )


@router.get("/histories")
async def get_price_histories(
    shop: AuthorizedShop,
    session: Database,
    sku_code: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> list[PriceHistoryItem]:
    return await price_change_service.list_price_histories(session, shop_id=shop.id, sku_code=sku_code)


@router.post("", dependencies=[Depends(reject_cross_origin)])
async def create_price_change(
    payload: PriceChangeCreateRequest,
    shop: OperatorShop,
    session: Database,
    clock: Clock,
    idempotency_key: IdempotencyHeader = None,
) -> PriceChangeSubmitResponse:
    return await price_change_service.submit_price_change(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        shop_name=shop.name,
        actor_id=shop.user_id,
        business_day=clock.business_date(shop.timezone),
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.get("/{request_id}")
async def get_price_change(
    shop: AuthorizedShop,
    session: Database,
    request_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> PriceChangeDetail:
    detail = await price_change_service.get_price_change_detail(
        session, shop_id=shop.id, request_id=request_id
    )
    if detail is None:
        raise AppError(404, "PRICE_CHANGE_NOT_FOUND", "当前店铺没有找到该调价申请。", detail="Price change not found")
    return detail


@router.post("/{request_id}/cancel", dependencies=[Depends(reject_cross_origin)])
async def cancel_price_change_route(
    shop: OperatorShop,
    session: Database,
    request_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> PriceChangeDetail:
    return await price_change_service.cancel_price_change(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        request_id=request_id,
        actor_id=shop.user_id,
        actor_roles=shop.roles,
    )
