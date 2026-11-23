from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import (
    REFRESH_LIFETIME,
    AuthSettings,
    issue_access_token,
    read_access_token,
    verify_password,
)
from app.models import AuthSession, Membership, Organization, Shop, User
from app.schemas.auth import LoginRequest, LoginResponse, ShopAccess, UserInfo


def hash_refresh_token(token: str) -> str:
    return sha256(token.encode()).hexdigest()


async def accessible_shops(
    session: AsyncSession, user_id: int, shop_id: int | None = None
) -> list[ShopAccess]:
    query = (
        select(Shop.id, Shop.organization_id, Shop.name, Shop.timezone, Membership.role)
        .join(Organization, Shop.organization_id == Organization.id)
        .join(
            Membership,
            and_(
                Membership.organization_id == Shop.organization_id,
                or_(Membership.shop_id.is_(None), Membership.shop_id == Shop.id),
            ),
        )
        .where(Membership.user_id == user_id, Organization.status == "active", Shop.status == "active")
        .order_by(Shop.id, Membership.role)
    )
    if shop_id is not None:
        query = query.where(Shop.id == shop_id)
    shops: dict[int, ShopAccess] = {}
    for shop_key, organization_id, name, timezone, role in (await session.execute(query)):
        if shop_key not in shops:
            shops[shop_key] = ShopAccess(
                id=shop_key, organization_id=organization_id, name=name, timezone=timezone, roles=[]
            )
        if role not in shops[shop_key].roles:
            shops[shop_key].roles.append(role)
    return list(shops.values())


def login_response(
    settings: AuthSettings, user: User, auth_session: AuthSession, shops: list[ShopAccess]
) -> LoginResponse:
    assert user.username is not None
    return LoginResponse(
        access_token=issue_access_token(settings, user.id, auth_session.id),
        user=UserInfo(id=user.id, username=user.username, name=user.name),
        shops=shops,
    )


async def login(
    session: AsyncSession, settings: AuthSettings, credentials: LoginRequest
) -> tuple[LoginResponse, str, datetime] | None:
    user = await session.scalar(select(User).where(User.username == credentials.username.strip()))
    matches = await run_in_threadpool(
        verify_password, credentials.password, user.password_hash if user else None
    )
    if not matches or user is None or user.status != "active":
        return None
    shops = await accessible_shops(session, user.id)
    if not shops:
        return None
    await session.execute(
        delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at <= func.now())
    )
    token = token_urlsafe(32)
    auth_session = AuthSession(
        user_id=user.id, refresh_hash=hash_refresh_token(token),
        expires_at=datetime.now(UTC) + REFRESH_LIFETIME,
    )
    session.add(auth_session)
    await session.flush()
    return login_response(settings, user, auth_session, shops), token, auth_session.expires_at


async def refresh(
    session: AsyncSession, settings: AuthSettings, token: str
) -> tuple[LoginResponse, str, datetime] | None:
    result = await session.execute(
        select(AuthSession, User)
        .join(User, AuthSession.user_id == User.id)
        .where(
            AuthSession.refresh_hash == hash_refresh_token(token), AuthSession.expires_at > func.now(),
            User.status == "active", User.username.is_not(None),
        )
        .with_for_update(of=AuthSession)
    )
    pair = result.one_or_none()
    if pair is None:
        return None
    auth_session, user = pair
    shops = await accessible_shops(session, user.id)
    if not shops:
        return None
    token = token_urlsafe(32)
    auth_session.refresh_hash = hash_refresh_token(token)
    return login_response(settings, user, auth_session, shops), token, auth_session.expires_at


async def logout(
    session: AsyncSession, settings: AuthSettings, refresh_token: str | None, access_token: str | None
) -> None:
    conditions = []
    if refresh_token:
        conditions.append(AuthSession.refresh_hash == hash_refresh_token(refresh_token))
    claims = read_access_token(settings, access_token) if access_token else None
    if claims:
        user_id, session_id = claims
        conditions.append(and_(AuthSession.id == session_id, AuthSession.user_id == user_id))
    if conditions:
        await session.execute(delete(AuthSession).where(or_(*conditions)))


async def authenticated_user(session: AsyncSession, settings: AuthSettings, token: str) -> int | None:
    claims = read_access_token(settings, token)
    if claims is None:
        return None
    user_id, session_id = claims
    return await session.scalar(
        select(AuthSession.user_id)
        .join(User, AuthSession.user_id == User.id)
        .where(
            AuthSession.id == session_id, AuthSession.user_id == user_id,
            AuthSession.expires_at > func.now(), User.status == "active", User.username.is_not(None),
        )
    )
