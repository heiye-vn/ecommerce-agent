from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.promotions import AuditLogEntry

PriceChangeStatus = Literal[
    "pending_approval",
    "approved",
    "processing",
    "succeeded",
    "rejected",
    "cancelled",
    "failed",
]


class PriceHistoryItem(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    product_name: str
    old_price: Decimal
    new_price: Decimal
    changed_at: datetime
    actor_id: int | None = None
    actor_name: str | None = None
    approval_no: str | None = None
    price_change_request_id: int | None = None
    reason: str | None = None


class PriceChangeCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    sku_code: str = Field(min_length=1, max_length=64)
    new_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=2, max_length=500)


class PriceChangeSummary(BaseModel):
    id: int
    request_no: str
    sku_id: int
    sku_code: str
    product_name: str
    old_price: Decimal
    new_price: Decimal
    cost_price: Decimal
    reason: str
    status: PriceChangeStatus
    requester_id: int
    requester_name: str
    failure_reason: str | None = None
    executed_at: datetime | None = None
    approval_id: int | None = None
    approval_no: str | None = None
    approval_status: str | None = None
    created_at: datetime
    updated_at: datetime


class PriceChangeDetail(PriceChangeSummary):
    current_price: Decimal
    sku_status: str
    job_id: int | None = None
    job_no: str | None = None
    job_status: str | None = None
    audit_logs: list[AuditLogEntry]


class PriceChangeSubmitResponse(BaseModel):
    request_id: int
    request_no: str
    sku_code: str
    old_price: Decimal
    new_price: Decimal
    status: PriceChangeStatus
    approval_id: int
    approval_no: str
    approval_status: str
    replayed: bool = False
    message: str
