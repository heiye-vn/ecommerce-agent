from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from app.api.auth import AuthorizedShop, Database
from app.schemas.orders import OrderDetail, OrderSummary, SkuSales
from app.services import orders


router = APIRouter(prefix="/api/shops/{shop_id}", tags=["orders"])


@router.get("/orders")
async def get_orders(shop: AuthorizedShop, session: Database) -> list[OrderSummary]:
    return await orders.list_orders(session, shop.id)


@router.get("/orders/{order_id}")
async def get_order(
    shop: AuthorizedShop, session: Database, order_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> OrderDetail:
    detail = await orders.get_order(session, shop.id, order_id)
    if detail is None:
        raise HTTPException(404, "Order not found")
    return detail


@router.get("/sales")
async def get_sales(
    shop: AuthorizedShop, session: Database, sku_code: Annotated[str, Query(min_length=1, max_length=64)],
    as_of: Annotated[date, Query(ge=date(1, 1, 9), description="Local day excluded from the seven-day window")],
) -> SkuSales:
    sales = await orders.sku_sales(session, shop.id, sku_code, as_of)
    if sales is None:
        raise HTTPException(404, "SKU not found")
    return sales
