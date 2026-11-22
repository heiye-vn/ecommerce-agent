import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.orders import SkuSales
from app.services.orders import sku_sales


logger = logging.getLogger(__name__)


async def query_sales(session: AsyncSession, shop_id: int, sku_code: str, as_of: date) -> SkuSales | None:
    result = await sku_sales(session, shop_id, sku_code, as_of)
    logger.info("Read-only Agent tool completed", extra={
        "tool_name": "query_sales", "shop_id": shop_id, "found": result is not None,
    })
    return result
