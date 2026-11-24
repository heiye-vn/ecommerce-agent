from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.promotions import AuditLogEntry

JobStatus = Literal["pending", "running", "succeeded", "retrying", "failed", "dead_lettered"]
JobType = Literal["promotion_schedule", "promotion_activate", "promotion_complete"]
JobTab = Literal["all", "needs_attention", "completed"]


class JobSummary(BaseModel):
    id: int
    job_no: str
    job_type: JobType
    status: JobStatus
    resource_type: str
    resource_id: int
    resource_no: str
    resource_name: str
    resource_version: int
    attempts: int
    max_attempts: int
    available_at: datetime
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class JobDetail(JobSummary):
    idempotency_key: str
    payload: dict[str, Any]
    result_json: dict[str, Any] | None = None
    resource_status: str | None = None
    can_retry: bool = False
    retry_hint: str | None = None
    audit_logs: list[AuditLogEntry]


class JobRetryResponse(BaseModel):
    job: JobDetail
    message: str
