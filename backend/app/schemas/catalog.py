from decimal import Decimal

from pydantic import BaseModel


class CatalogSku(BaseModel):
    sku_code: str
    product_name: str
    category: str
    status: str
    current_price: Decimal
    available_qty: int | None
    reserved_qty: int | None
