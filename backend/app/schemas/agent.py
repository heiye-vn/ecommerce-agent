import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.orders import SkuSales
from app.schemas.promotions import PromotionSubmitResponse

SKU_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])SKU-[A-Za-z0-9][A-Za-z0-9_-]{0,59}(?![A-Za-z0-9_-])",
    re.IGNORECASE | re.ASCII,
)


class PromotionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_id: int = Field(gt=0, le=2_147_483_647)
    sku_code: str = Field(pattern=r"^SKU-[A-Z0-9][A-Z0-9_-]*$", max_length=64)
    discount: Decimal = Field(gt=0, lt=10, max_digits=8, decimal_places=6)
    duration_days: int = Field(ge=1, le=3_652_058)


class PendingPromotionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_key: str = Field(min_length=8, max_length=64)
    shop_id: int = Field(gt=0, le=2_147_483_647)
    sku_code: str = Field(pattern=r"^SKU-[A-Z0-9][A-Z0-9_-]*$", max_length=64)
    discount: Decimal = Field(gt=0, lt=10, max_digits=8, decimal_places=4)
    start_date: date
    duration_days: int = Field(ge=1, le=365)
    name: str | None = Field(default=None, max_length=160)


class PromotionPreview(BaseModel):
    sku_code: str
    original_price: Decimal
    cost_price: Decimal | None = None
    promotion_price: Decimal
    below_cost: bool = False
    risk_warnings: list[str] = Field(default_factory=list)
    discount: Decimal
    duration_days: int
    available_qty: int | None
    minimum_qty: int
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone: str = "Asia/Shanghai"
    conflict_check: Literal["clear", "conflict", "unavailable"] = "clear"
    conflicting_promotions: list[str] = Field(default_factory=list)
    plan_key: str | None = None


class AgentQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message: str = Field(min_length=1, max_length=500)
    session_id: int | None = Field(default=None, gt=0, le=2_147_483_647)


class QueryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["sales", "promotion", "confirm_plan", "unsupported"]
    sku_code: str | None = Field(default=None, max_length=64)
    discount: Decimal | None = None
    duration_days: int | None = None
    start_date: str | None = Field(default=None, max_length=32)
    ambiguous_terms: bool = False


class AgentAnswer(BaseModel):
    kind: Literal[
        "sales",
        "clarify",
        "unsupported",
        "not_found",
        "promotion_clarify",
        "promotion_preview",
        "promotion_blocked",
        "promotion_submitted",
    ]
    message: str
    session_id: int | None = None
    task_id: int | None = None
    tool_calls: int = Field(default=0, ge=0)
    sales: SkuSales | None = None
    promotion: PromotionPreview | None = None
    promotion_context: PromotionContext | None = None
    pending_plan: PendingPromotionPlan | None = None
    submission: PromotionSubmitResponse | None = None
