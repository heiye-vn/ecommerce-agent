from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Path, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import create_database_engine
from app.core.security import AuthSettings
from app.schemas.auth import LoginRequest, LoginResponse, ShopAccess
from app.services import auth


router = APIRouter(prefix="/api", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
COOKIE = "commerce_refresh"
COOKIE_PATH = "/api/auth"


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    if not hasattr(request.app.state, "database_engine"):
        request.app.state.database_engine = create_database_engine()
    sessions = async_sessionmaker(request.app.state.database_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        yield session


Database = Annotated[AsyncSession, Depends(database_session, scope="function")]
Settings = Annotated[AuthSettings, Depends(AuthSettings.load)]
RefreshCookie = Annotated[str | None, Cookie(alias=COOKIE, max_length=128)]
Bearer = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def reject_cross_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if (origin is not None and origin != expected_origin) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-origin authentication request denied")


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
        raise HTTPException(401, "Invalid login credentials")
    payload, token, expires_at = result
    set_refresh_cookie(response, settings, token, expires_at)
    return payload


@router.post("/auth/refresh", dependencies=[Depends(reject_cross_origin)])
async def refresh(
    response: Response, session: Database, settings: Settings, refresh_token: RefreshCookie = None
) -> LoginResponse:
    result = await auth.refresh(session, settings, refresh_token) if refresh_token else None
    if result is None:
        raise HTTPException(401, "Session expired")
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


async def require_shop_access(
    shop_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
    response: Response, session: Database, settings: Settings, credentials: Bearer,
) -> ShopAccess:
    user_id = await auth.authenticated_user(session, settings, credentials.credentials) if credentials else None
    if user_id is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    shops = await auth.accessible_shops(session, user_id, shop_id)
    if not shops:
        raise HTTPException(403, "Shop access denied")
    response.headers["Cache-Control"] = "no-store"
    return shops[0]


AuthorizedShop = Annotated[ShopAccess, Depends(require_shop_access, scope="function")]


@router.get("/shops/{shop_id}")
async def get_shop(shop: AuthorizedShop) -> ShopAccess:
    return shop
