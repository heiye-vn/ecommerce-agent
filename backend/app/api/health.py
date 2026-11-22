from fastapi import APIRouter, Depends

from app.services.health import get_health


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def read_health(
    health: dict[str, object] = Depends(get_health),
) -> dict[str, object]:
    return health
