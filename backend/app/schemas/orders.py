from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class OrderSummary(BaseModel):
    id: int
    order_no: str
    status: str
    paid_amount: Decimal
    paid_at: datetime | None
    created_at: datetime
    sku_codes: list[str]
    quantity: int


class OrderLine(BaseModel):
    id: int
    sku_code: str
    product_name: str
    quantity: int
    unit_price: Decimal
    paid_amount: Decimal


class OrderDetail(OrderSummary):
    items: list[OrderLine]


class SkuSales(BaseModel):
    sku_code: str
    units_sold: int
    paid_orders: int
    start_date: date
    end_date: date
    timezone: str
