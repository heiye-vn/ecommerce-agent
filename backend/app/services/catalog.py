from sqlalchemy import and_, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Inventory, Product, Sku
from app.schemas.catalog import CatalogSku


async def list_skus(session: AsyncSession, shop_id: int) -> list[CatalogSku]:
    query = (
        select(
            Sku.sku_code, Product.name.label("product_name"), Product.category,
            case((Product.status != "active", Product.status), else_=Sku.status).label("status"),
            Sku.current_price, Inventory.available_qty, Inventory.reserved_qty,
        )
        .join(Product, and_(Sku.shop_id == Product.shop_id, Sku.product_id == Product.id))
        .outerjoin(Inventory, Inventory.sku_id == Sku.id)
        .where(Sku.shop_id == shop_id)
        .order_by(Sku.sku_code)
    )
    return [CatalogSku.model_validate(row) for row in (await session.execute(query)).mappings()]
