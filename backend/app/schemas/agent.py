import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.knowledge import KnowledgeCitationItem
from app.schemas.orders import SkuSales
from app.schemas.price_changes import PriceChangeSubmitResponse
from app.schemas.promotions import PromotionSubmitResponse
from app.schemas.refunds import RefundSubmitResponse

SKU_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])SKU-[A-Za-z0-9][A-Za-z0-9_-]{0,59}(?![A-Za-z0-9_-])",
    re.IGNORECASE | re.ASCII,
)
ORDER_NO_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:ORD|DEMO)-[A-Za-z0-9][A-Za-z0-9_-]{0,59}(?![A-Za-z0-9_-])",
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


class RefundContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_id: int = Field(gt=0, le=2_147_483_647)
    order_id: int = Field(gt=0, le=2_147_483_647)
    order_no: str = Field(max_length=64)
    amount: Decimal | None = None
    reason: str | None = Field(default=None, max_length=500)


class PendingRefundPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_key: str = Field(min_length=8, max_length=64)
    shop_id: int = Field(gt=0, le=2_147_483_647)
    order_id: int = Field(gt=0, le=2_147_483_647)
    order_no: str = Field(max_length=64)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=2, max_length=500)


class RefundPreview(BaseModel):
    order_id: int
    order_no: str
    order_status: str
    paid_amount: Decimal
    refunded_amount: Decimal
    in_flight_amount: Decimal
    refundable_amount: Decimal
    proposed_amount: Decimal
    reason: str
    projected_order_status: str
    eligible: bool
    block_reason: str | None = None
    plan_key: str | None = None


class OrderDiagnosticItem(BaseModel):
    order_id: int
    order_no: str
    status: str
    fulfillment_status: str
    exception_type: str | None = None
    exception_note: str | None = None
    paid_amount: Decimal
    refunded_amount: Decimal
    refundable_amount: Decimal
    can_refund: bool
    refund_block_reason: str | None = None
    sku_codes: list[str] = Field(default_factory=list)


class OrderQueryResult(BaseModel):
    mode: Literal["detail", "exception_list"]
    items: list[OrderDiagnosticItem]


class PriceChangeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_id: int = Field(gt=0, le=2_147_483_647)
    sku_id: int = Field(gt=0, le=2_147_483_647)
    sku_code: str = Field(pattern=r"^SKU-[A-Z0-9][A-Z0-9_-]*$", max_length=64)
    target_price: Decimal | None = None
    reason: str | None = Field(default=None, max_length=500)


class PendingPriceChangePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_key: str = Field(min_length=8, max_length=64)
    shop_id: int = Field(gt=0, le=2_147_483_647)
    sku_id: int = Field(gt=0, le=2_147_483_647)
    sku_code: str = Field(pattern=r"^SKU-[A-Z0-9][A-Z0-9_-]*$", max_length=64)
    new_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=2, max_length=500)


class PriceChangePreview(BaseModel):
    sku_id: int
    sku_code: str
    product_name: str
    sku_status: str
    current_price: Decimal
    cost_price: Decimal
    proposed_price: Decimal
    change_amount: Decimal
    change_pct: Decimal
    reason: str
    last_old_price: Decimal | None = None
    last_new_price: Decimal | None = None
    last_changed_at: datetime | None = None
    eligible: bool
    block_reason: str | None = None
    plan_key: str | None = None


class KnowledgeQaResult(BaseModel):
    citations: list[KnowledgeCitationItem] = Field(default_factory=list)
    vector_candidates: int = 0
    lexical_candidates: int = 0
    vector_degraded: bool = False
    rerank_degraded: bool = False
    embedding_model: str | None = None
    rerank_model: str | None = None
    isolation_notice: str = "本回答仅解释运营规则，未查询商品实时价格，未提交审批或变更数据。"


class AgentQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message: str = Field(min_length=1, max_length=500)
    session_id: int | None = Field(default=None, gt=0, le=2_147_483_647)


class QueryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "sales",
        "promotion",
        "price_change",
        "order_query",
        "refund",
        "knowledge_qa",
        "confirm_plan",
        "unsupported",
    ]
    sku_code: str | None = Field(default=None, max_length=64)
    discount: Decimal | None = None
    duration_days: int | None = None
    start_date: str | None = Field(default=None, max_length=32)
    ambiguous_terms: bool = False
    order_no: str | None = Field(default=None, max_length=64)
    refund_amount: Decimal | None = None
    refund_reason: str | None = Field(default=None, max_length=500)
    exception_only: bool = False
    target_price: Decimal | None = None
    price_change_reason: str | None = None


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
        "price_change_clarify",
        "price_change_preview",
        "price_change_blocked",
        "price_change_submitted",
        "order_info",
        "refund_clarify",
        "refund_preview",
        "refund_blocked",
        "refund_submitted",
        "knowledge_qa",
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
    price_change: PriceChangePreview | None = None
    price_change_context: PriceChangeContext | None = None
    pending_price_change_plan: PendingPriceChangePlan | None = None
    price_change_submission: PriceChangeSubmitResponse | None = None
    order_result: OrderQueryResult | None = None
    refund: RefundPreview | None = None
    refund_context: RefundContext | None = None
    pending_refund_plan: PendingRefundPlan | None = None
    refund_submission: RefundSubmitResponse | None = None
    knowledge: KnowledgeQaResult | None = None
