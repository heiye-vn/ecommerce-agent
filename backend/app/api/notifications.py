from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.auth import AuthorizedShop, Database, reject_cross_origin
from app.schemas.notifications import NotificationItem, NotificationListResponse, ReadAllResponse
from app.services import notifications as notification_service

router = APIRouter(prefix="/api/shops/{shop_id}/notifications", tags=["notifications"])


@router.get("")
async def get_notifications(
    shop: AuthorizedShop,
    session: Database,
    unread_only: Annotated[bool, Query()] = False,
) -> NotificationListResponse:
    return await notification_service.list_notifications(
        session,
        shop_id=shop.id,
        user_id=shop.user_id,
        unread_only=unread_only,
    )


@router.post("/{notification_id}/read", dependencies=[Depends(reject_cross_origin)])
async def mark_notification_as_read(
    shop: AuthorizedShop,
    session: Database,
    notification_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> NotificationItem:
    return await notification_service.mark_notification_read(
        session,
        shop_id=shop.id,
        user_id=shop.user_id,
        notification_id=notification_id,
    )


@router.post("/read-all", dependencies=[Depends(reject_cross_origin)])
async def mark_all_notifications_as_read(
    shop: AuthorizedShop,
    session: Database,
) -> ReadAllResponse:
    updated_count = await notification_service.mark_all_notifications_read(
        session,
        shop_id=shop.id,
        user_id=shop.user_id,
    )
    return ReadAllResponse(updated_count=updated_count)
