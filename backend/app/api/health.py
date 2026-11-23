from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.auth import AppConfig
from app.services.health import check_readiness, get_health

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def read_health(
    health: Annotated[dict[str, str | list[str]], Depends(get_health)],
) -> dict[str, str | list[str]]:
    return health


@router.get("/health/live")
def read_liveness() -> dict[str, str]:
    return {"status": "ok", "service": "commerce-agent-backend"}


@router.get("/health/ready")
async def read_readiness(request: Request, settings: AppConfig) -> JSONResponse:
    engine = getattr(request.app.state, "database_engine", None)
    ready, payload = await check_readiness(
        engine,
        redis_url=settings.redis_url,
        rabbitmq_url=settings.rabbitmq_url,
    )
    return JSONResponse(status_code=200 if ready else 503, content=payload)
