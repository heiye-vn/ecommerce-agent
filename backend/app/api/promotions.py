from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path

from app.api.auth import AuthorizedShop, Clock, Database, OperatorShop, reject_cross_origin
from app.core.errors import AppError
from app.schemas.analytics import PromotionReviewResponse
from app.schemas.promotions import (
    PromotionCreateRequest,
    PromotionDetail,
    PromotionReviseRequest,
    PromotionSubmitResponse,
    PromotionSummary,
)
from app.services import analytics as analytics_service
from app.services import promotions as promotion_service

router = APIRouter(prefix="/api/shops/{shop_id}/promotions", tags=["promotions"])
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


@router.get("")
async def get_promotions(shop: AuthorizedShop, session: Database) -> list[PromotionSummary]:
    return await promotion_service.list_promotions(session, shop_id=shop.id)


@router.post("", dependencies=[Depends(reject_cross_origin)])
async def create_promotion(
    payload: PromotionCreateRequest,
    shop: OperatorShop,
    session: Database,
    clock: Clock,
    idempotency_key: IdempotencyHeader = None,
) -> PromotionSubmitResponse:
    return await promotion_service.submit_promotion(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        shop_name=shop.name,
        timezone_name=shop.timezone,
        actor_id=shop.user_id,
        business_day=clock.business_date(shop.timezone),
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.get("/{promotion_id}/review")
async def get_promotion_review_route(
    shop: AuthorizedShop,
    session: Database,
    promotion_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> PromotionReviewResponse:
    return await analytics_service.get_promotion_review(
        session, shop_id=shop.id, promotion_id=promotion_id
    )


@router.get("/{promotion_id}")
async def get_promotion(
    shop: AuthorizedShop,
    session: Database,
    promotion_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> PromotionDetail:
    detail = await promotion_service.get_promotion_detail(
        session, shop_id=shop.id, promotion_id=promotion_id
    )
    if detail is None:
        raise AppError(404, "PROMOTION_NOT_FOUND", "当前店铺没有找到该促销活动。", detail="Promotion not found")
    return detail


@router.post("/{promotion_id}/cancel", dependencies=[Depends(reject_cross_origin)])
async def cancel_promotion_route(
    shop: OperatorShop,
    session: Database,
    promotion_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> PromotionDetail:
    return await promotion_service.cancel_promotion(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        promotion_id=promotion_id,
        actor_id=shop.user_id,
        actor_roles=shop.roles,
    )


@router.post("/{promotion_id}/revise", dependencies=[Depends(reject_cross_origin)])
async def revise_promotion_route(
    payload: PromotionReviseRequest,
    shop: OperatorShop,
    session: Database,
    clock: Clock,
    promotion_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
    idempotency_key: IdempotencyHeader = None,
) -> PromotionSubmitResponse:
    return await promotion_service.revise_promotion(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        shop_name=shop.name,
        timezone_name=shop.timezone,
        promotion_id=promotion_id,
        actor_id=shop.user_id,
        business_day=clock.business_date(shop.timezone),
        payload=payload,
        idempotency_key=idempotency_key,
    )
