from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query

from app.api.auth import AuthorizedShop, Database, OperatorShop, reject_cross_origin
from app.schemas.inventory import (
    InventoryMovementCreateRequest,
    InventoryMovementItem,
    InventoryMovementMutationResponse,
    InventoryMovementType,
    InventoryOverviewResponse,
)
from app.services import inventory as inventory_service

router = APIRouter(prefix="/api/shops/{shop_id}/inventory", tags=["inventory"])
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


@router.get("")
async def get_inventory(shop: AuthorizedShop, session: Database) -> InventoryOverviewResponse:
    return await inventory_service.get_inventory_overview(session, shop.id)


@router.get("/movements")
async def get_inventory_movements(
    shop: AuthorizedShop,
    session: Database,
    sku_code: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    movement_type: Annotated[InventoryMovementType | None, Query()] = None,
) -> list[InventoryMovementItem]:
    return await inventory_service.list_inventory_movements(
        session,
        shop.id,
        sku_code=sku_code,
        movement_type=movement_type,
    )


@router.post("/{sku_code}/movements", dependencies=[Depends(reject_cross_origin)])
async def create_inventory_movement(
    payload: InventoryMovementCreateRequest,
    shop: OperatorShop,
    session: Database,
    sku_code: Annotated[str, Path(min_length=1, max_length=64)],
    idempotency_key: IdempotencyHeader = None,
) -> InventoryMovementMutationResponse:
    return await inventory_service.apply_inventory_movement(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        sku_code=sku_code,
        actor_id=shop.user_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
