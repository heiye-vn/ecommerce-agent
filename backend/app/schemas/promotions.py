from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PromotionStatus = Literal[
    "pending_approval",
    "approved",
    "scheduled",
    "active",
    "completed",
    "rejected",
    "cancelled",
    "failed",
]


class PromotionCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    sku_code: str = Field(min_length=1, max_length=64)
    discount: Decimal = Field(gt=0, lt=10, max_digits=8, decimal_places=4)
    start_date: date
    duration_days: int = Field(ge=1, le=365)


class PromotionReviseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    discount: Decimal = Field(gt=0, lt=10, max_digits=8, decimal_places=4)
    start_date: date
    duration_days: int = Field(ge=1, le=365)
    reason: str | None = Field(default=None, max_length=500)


class PromotionItemInfo(BaseModel):
    sku_id: int
    sku_code: str
    product_name: str
    original_price: Decimal
    cost_price: Decimal
    discount: Decimal
    discount_rate: Decimal
    promotion_price: Decimal
    below_cost: bool


class AuditLogEntry(BaseModel):
    id: int
    action: str
    actor_id: int
    actor_name: str
    before_json: dict[str, Any] | None = None
    after_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime


class PromotionApprovalBrief(BaseModel):
    id: int
    approval_no: str
    resource_version: int
    status: str
    requester_id: int
    requester_name: str
    approver_id: int | None = None
    approver_name: str | None = None
    decision_comment: str | None = None
    decided_at: datetime | None = None
    created_at: datetime


class PromotionSummary(BaseModel):
    id: int
    promotion_no: str
    name: str
    version: int
    status: PromotionStatus
    sku_code: str
    product_name: str
    original_price: Decimal
    cost_price: Decimal
    discount: Decimal
    promotion_price: Decimal
    below_cost: bool
    starts_at: datetime
    ends_at: datetime
    duration_days: int
    minimum_qty: int
    timezone: str
    created_by: int
    creator_name: str
    latest_approval_id: int | None = None
    latest_approval_no: str | None = None
    latest_approval_status: str | None = None
    failure_reason: str | None = None
    conflict_promotion_no: str | None = None
    created_at: datetime
    updated_at: datetime


class PromotionDetail(PromotionSummary):
    item: PromotionItemInfo
    approvals: list[PromotionApprovalBrief]
    audit_logs: list[AuditLogEntry]


class PromotionSubmitResponse(BaseModel):
    promotion_id: int
    promotion_no: str
    name: str
    version: int
    status: PromotionStatus
    approval_id: int
    approval_no: str
    approval_status: str
    sku_code: str
    original_price: Decimal
    cost_price: Decimal
    promotion_price: Decimal
    below_cost: bool
    risk_warnings: list[str] = Field(default_factory=list)
    starts_at: datetime
    ends_at: datetime
    timezone: str
    replayed: bool = False
