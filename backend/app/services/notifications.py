from datetime import UTC, datetime
from typing import cast

from sqlalchemy import and_, distinct, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Membership, Notification, Role, User
from app.schemas.notifications import NotificationCategory, NotificationItem, NotificationListResponse


async def create_notification(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    user_id: int,
    category: NotificationCategory,
    title: str,
    content: str,
    resource_type: str | None = None,
    resource_id: int | None = None,
) -> Notification:
    notification = Notification(
        organization_id=organization_id,
        shop_id=shop_id,
        user_id=user_id,
        category=category,
        title=title,
        content=content,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    session.add(notification)
    await session.flush()
    return notification


async def notify_shop_roles(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    roles: tuple[Role, ...],
    category: NotificationCategory,
    title: str,
    content: str,
    resource_type: str | None = None,
    resource_id: int | None = None,
    exclude_user_id: int | None = None,
) -> list[int]:
    query = (
        select(distinct(Membership.user_id))
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.organization_id == organization_id,
            or_(Membership.shop_id.is_(None), Membership.shop_id == shop_id),
            Membership.role.in_(roles),
            User.status == "active",
        )
    )
    if exclude_user_id is not None:
        query = query.where(Membership.user_id != exclude_user_id)
    recipient_ids = list((await session.scalars(query)).all())
    for recipient_id in recipient_ids:
        await create_notification(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            user_id=recipient_id,
            category=category,
            title=title,
            content=content,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    return recipient_ids


def _to_schema(item: Notification) -> NotificationItem:
    return NotificationItem(
        id=item.id,
        category=cast(NotificationCategory, item.category),
        title=item.title,
        content=item.content,
        resource_type=item.resource_type,
        resource_id=item.resource_id,
        is_read=item.is_read,
        read_at=item.read_at,
        created_at=item.created_at,
    )


async def list_notifications(
    session: AsyncSession,
    *,
    shop_id: int,
    user_id: int,
    unread_only: bool = False,
) -> NotificationListResponse:
    unread_count = await session.scalar(
        select(func.count(Notification.id)).where(
            Notification.shop_id == shop_id,
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
    )
    query = select(Notification).where(
        Notification.shop_id == shop_id,
        Notification.user_id == user_id,
    )
    if unread_only:
        query = query.where(Notification.is_read.is_(False))
    query = query.order_by(Notification.is_read.asc(), Notification.created_at.desc(), Notification.id.desc())
    rows = list((await session.scalars(query)).all())
    return NotificationListResponse(
        items=[_to_schema(row) for row in rows],
        unread_count=int(unread_count or 0),
    )


async def mark_notification_read(
    session: AsyncSession,
    *,
    shop_id: int,
    user_id: int,
    notification_id: int,
) -> NotificationItem:
    item = await session.scalar(
        select(Notification)
        .where(
            and_(
                Notification.id == notification_id,
                Notification.shop_id == shop_id,
                Notification.user_id == user_id,
            )
        )
        .with_for_update()
    )
    if item is None:
        raise AppError(
            404,
            "NOTIFICATION_NOT_FOUND",
            "当前店铺没有找到这条通知。",
            detail="Notification not found",
        )
    if not item.is_read:
        item.is_read = True
        item.read_at = datetime.now(UTC)
        await session.flush()
    return _to_schema(item)


async def mark_all_notifications_read(
    session: AsyncSession,
    *,
    shop_id: int,
    user_id: int,
) -> int:
    now = datetime.now(UTC)
    updated_ids = (
        await session.scalars(
            update(Notification)
            .where(
                Notification.shop_id == shop_id,
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=now)
            .returning(Notification.id)
        )
    ).all()
    return len(updated_ids)
