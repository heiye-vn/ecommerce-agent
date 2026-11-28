from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class OrderRefundBrief(BaseModel):
    id: int
    refund_no: str
    amount: Decimal
    reason: str
    status: str
    failure_reason: str | None = None
    created_at: datetime


class OrderSummary(BaseModel):
    id: int
    order_no: str
    status: str
    fulfillment_status: str = "pending_shipment"
    exception_type: str | None = None
    exception_note: str | None = None
    paid_amount: Decimal
    refunded_amount: Decimal = Decimal("0.00")
    in_flight_refund_amount: Decimal = Decimal("0.00")
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
    refundable_amount: Decimal = Decimal("0.00")
    can_refund: bool = False
    refund_block_reason: str | None = None
    refunds: list[OrderRefundBrief] = Field(default_factory=list)


class SkuSales(BaseModel):
    sku_code: str
    units_sold: int
    paid_orders: int
    start_date: date
    end_date: date
    timezone: str
