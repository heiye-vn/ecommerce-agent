from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.promotions import AuditLogEntry

ApprovalStatus = Literal["pending", "approved", "rejected", "cancelled"]
ApprovalScope = Literal["pending_for_me", "processed_by_me", "requested_by_me", "all"]


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    comment: str | None = Field(default=None, max_length=500)


class ApprovalSummary(BaseModel):
    id: int
    approval_no: str
    action_type: str
    resource_type: str
    resource_id: int
    resource_no: str
    resource_name: str
    resource_version: int
    sku_code: str | None = None
    snapshot_amount: str | None = None
    status: ApprovalStatus
    requester_id: int
    requester_name: str
    approver_id: int | None = None
    approver_name: str | None = None
    decision_comment: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    can_decide: bool = False


class ApprovalDetail(ApprovalSummary):
    shop_id: int
    shop_name: str
    payload_snapshot: dict[str, Any]
    resource_status: str | None = None
    resource_failure_reason: str | None = None
    resource_conflict_promotion_no: str | None = None
    cannot_decide_reason: str | None = None
    audit_logs: list[AuditLogEntry]


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalDetail
    execution_status: str | None = None
    execution_message: str
