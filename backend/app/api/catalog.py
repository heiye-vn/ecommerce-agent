from fastapi import APIRouter

from app.api.auth import AuthorizedShop, Database
from app.schemas.catalog import CatalogSku
from app.services.catalog import list_skus


router = APIRouter(prefix="/api/shops", tags=["catalog"])


@router.get("/{shop_id}/catalog")
async def get_catalog(shop: AuthorizedShop, session: Database) -> list[CatalogSku]:
    return await list_skus(session, shop.id)
