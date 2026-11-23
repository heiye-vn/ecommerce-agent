from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Path, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import BusinessClock
from app.core.config import AppSettings
from app.core.db import create_database_engine, create_session_factory
from app.core.errors import AppError
from app.core.security import AuthSettings
from app.models.org import Role
from app.schemas.auth import LoginRequest, LoginResponse, ShopAccess, ShopContext
from app.services import auth

router = APIRouter(prefix="/api", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
COOKIE = "commerce_refresh"
COOKIE_PATH = "/api/auth"


def get_app_settings(request: Request) -> AppSettings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = AppSettings.load()
        request.app.state.settings = settings
    return settings


def get_auth_settings(settings: Annotated[AppSettings, Depends(get_app_settings)]) -> AuthSettings:
    return settings.require_auth()


def get_business_clock(
    request: Request,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> BusinessClock:
    clock = getattr(request.app.state, "clock", None)
    if clock is None:
        clock = BusinessClock(settings.sandbox_business_date)
        request.app.state.clock = clock
    return clock


def get_session_factory(
    request: Request,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> async_sessionmaker[AsyncSession]:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        engine = getattr(request.app.state, "database_engine", None)
        if engine is None:
            engine = create_database_engine(settings.require_database_url())
            request.app.state.database_engine = engine
        factory = create_session_factory(engine)
        request.app.state.session_factory = factory
    return factory


async def database_session(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    async with session_factory.begin() as session:
        yield session


AppConfig = Annotated[AppSettings, Depends(get_app_settings)]
Clock = Annotated[BusinessClock, Depends(get_business_clock)]
SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
Database = Annotated[AsyncSession, Depends(database_session, scope="function")]
Settings = Annotated[AuthSettings, Depends(get_auth_settings)]
RefreshCookie = Annotated[str | None, Cookie(alias=COOKIE, max_length=128)]
Bearer = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def reject_cross_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if (origin is not None and origin != expected_origin) or request.headers.get("sec-fetch-site") == "cross-site":
        raise AppError(
            403,
            "CROSS_ORIGIN_DENIED",
            "拒绝跨站认证请求。",
            detail="Cross-origin authentication request denied",
        )


def set_refresh_cookie(
    response: Response, settings: AuthSettings, token: str, expires_at: datetime
) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        COOKIE, token, expires=expires_at, path=COOKIE_PATH,
        httponly=True, secure=settings.secure_cookie, samesite="strict",
    )


@router.post("/auth/login", dependencies=[Depends(reject_cross_origin)])
async def login(
    credentials: LoginRequest, response: Response, session: Database, settings: Settings
) -> LoginResponse:
    result = await auth.login(session, settings, credentials)
    if result is None:
        raise AppError(
            401,
            "AUTH_INVALID_CREDENTIALS",
            "账号或密码错误。",
            detail="Invalid login credentials",
        )
    payload, token, expires_at = result
    set_refresh_cookie(response, settings, token, expires_at)
    return payload


@router.post("/auth/refresh", dependencies=[Depends(reject_cross_origin)])
async def refresh(
    response: Response, session: Database, settings: Settings, refresh_token: RefreshCookie = None
) -> LoginResponse:
    result = await auth.refresh(session, settings, refresh_token) if refresh_token else None
    if result is None:
        raise AppError(
            401,
            "AUTH_SESSION_EXPIRED",
            "登录状态已失效，请重新登录。",
            detail="Session expired",
        )
    payload, token, expires_at = result
    set_refresh_cookie(response, settings, token, expires_at)
    return payload


@router.post("/auth/logout", status_code=204, dependencies=[Depends(reject_cross_origin)])
async def logout(
    response: Response, session: Database, settings: Settings, credentials: Bearer,
    refresh_token: RefreshCookie = None,
) -> None:
    await auth.logout(session, settings, refresh_token, credentials.credentials if credentials else None)
    response.delete_cookie(
        COOKIE, path=COOKIE_PATH, httponly=True, secure=settings.secure_cookie, samesite="strict"
    )


async def require_shop_context(
    shop_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
    response: Response,
    session_factory: SessionFactory,
    settings: Settings,
    credentials: Bearer,
) -> ShopContext:
    # 使用独立短只读会话完成鉴权，鉴权结束后立刻归还数据库连接
    async with session_factory() as session:
        user_id = (
            await auth.authenticated_user(session, settings, credentials.credentials)
            if credentials
            else None
        )
        if user_id is None:
            raise AppError(
                401,
                "AUTH_REQUIRED",
                "请先登录。",
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        shops = await auth.accessible_shops(session, user_id, shop_id)
        if not shops:
            raise AppError(
                403,
                "SHOP_ACCESS_DENIED",
                "无权访问当前店铺。",
                detail="Shop access denied",
            )
    response.headers["Cache-Control"] = "no-store"
    return ShopContext(user_id=user_id, shop=shops[0])


def require_shop_roles(*allowed_roles: Role) -> Callable[..., Coroutine[Any, Any, ShopContext]]:
    allowed = frozenset(allowed_roles)

    async def dependency(context: Annotated[ShopContext, Depends(require_shop_context)]) -> ShopContext:
        if not allowed.intersection(context.roles):
            raise AppError(
                403,
                "SHOP_ROLE_FORBIDDEN",
                "当前店铺角色无权执行此操作。",
                detail="Required shop role missing",
            )
        return context

    return dependency


AuthorizedShop = Annotated[ShopContext, Depends(require_shop_context, scope="function")]
OperatorShop = Annotated[
    ShopContext,
    Depends(require_shop_roles(Role.OPERATOR, Role.ADMIN), scope="function"),
]
ApproverShop = Annotated[
    ShopContext,
    Depends(require_shop_roles(Role.APPROVER), scope="function"),
]


@router.get("/shops/{shop_id}")
async def get_shop(shop: AuthorizedShop) -> ShopAccess:
    return shop.shop
