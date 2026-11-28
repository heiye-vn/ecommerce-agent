from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.promotions import AuditLogEntry

RefundStatus = Literal[
    "pending_approval",
    "approved",
    "processing",
    "succeeded",
    "rejected",
    "cancelled",
    "failed",
]


class RefundEligibility(BaseModel):
    order_id: int
    order_no: str
    order_status: str
    paid_amount: Decimal
    refunded_amount: Decimal
    in_flight_amount: Decimal
    refundable_amount: Decimal
    eligible: bool
    block_reason: str | None = None
    in_flight_refund_id: int | None = None
    in_flight_refund_no: str | None = None


class RefundCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    order_id: int = Field(gt=0, le=2_147_483_647)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=2, max_length=500)


class RefundSummary(BaseModel):
    id: int
    refund_no: str
    order_id: int
    order_no: str
    order_status: str
    order_paid_amount: Decimal
    amount: Decimal
    reason: str
    status: RefundStatus
    requester_id: int
    requester_name: str
    failure_reason: str | None = None
    processed_at: datetime | None = None
    approval_id: int | None = None
    approval_no: str | None = None
    approval_status: str | None = None
    created_at: datetime
    updated_at: datetime


class RefundDetail(RefundSummary):
    refunded_amount: Decimal
    refundable_amount: Decimal
    job_id: int | None = None
    job_no: str | None = None
    job_status: str | None = None
    audit_logs: list[AuditLogEntry]


class RefundSubmitResponse(BaseModel):
    refund_id: int
    refund_no: str
    order_id: int
    order_no: str
    amount: Decimal
    status: RefundStatus
    approval_id: int
    approval_no: str
    approval_status: str
    replayed: bool = False
    message: str
