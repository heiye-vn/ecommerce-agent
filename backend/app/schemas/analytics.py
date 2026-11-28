from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class AnalyticsTrendPoint(BaseModel):
    date_label: str
    full_date: date
    current_gmv: Decimal
    previous_gmv: Decimal
    current_orders: int
    previous_orders: int


class AnalyticsSkuRankItem(BaseModel):
    sku_code: str
    product_name: str
    units_sold: int
    paid_gmv: Decimal


class AnalyticsOverviewResponse(BaseModel):
    shop_id: int
    shop_name: str
    timezone: str
    days: int
    start_date: date
    end_date: date
    prev_start_date: date
    prev_end_date: date
    paid_gmv: Decimal
    prev_paid_gmv: Decimal
    gmv_change_pct: Decimal | None = None
    paid_orders: int
    prev_paid_orders: int
    avg_order_value: Decimal
    prev_avg_order_value: Decimal
    refunded_orders: int
    refund_rate: Decimal
    prev_refund_rate: Decimal
    trend: list[AnalyticsTrendPoint]
    sku_ranking: list[AnalyticsSkuRankItem]
    methodology: str


class DailyReportMetricItem(BaseModel):
    report_date: date
    prev_date: date
    timezone: str
    paid_gmv: Decimal
    prev_paid_gmv: Decimal
    gmv_delta: Decimal
    gmv_change_pct: Decimal | None = None
    paid_orders: int
    avg_order_value: Decimal
    refunded_orders: int
    refunded_amount: Decimal
    refund_rate: Decimal


class DailyReportResponse(BaseModel):
    shop_id: int
    shop_name: str
    metrics: DailyReportMetricItem
    headline: str
    facts: list[str]
    analysis_boundary: str
    recommendations: list[str]
    methodology: str


class SkuTurnoverItem(BaseModel):
    sku_code: str
    product_name: str
    category: str
    units_sold: int
    available_qty: int
    reserved_qty: int
    turnover_rate: Decimal
    low_stock: bool
    low_stock_threshold: int


class AnalyticsAnomalyItem(BaseModel):
    kind: Literal["low_stock", "refund_rate"]
    title: str
    detail: str
    badge: str
    severity: Literal["warning", "info"]


class InventoryTurnoverResponse(BaseModel):
    shop_id: int
    shop_name: str
    timezone: str
    days: int
    start_date: date
    end_date: date
    total_units_sold: int
    total_available_qty: int
    overall_turnover_rate: Decimal
    low_stock_count: int
    low_stock_threshold: int
    items: list[SkuTurnoverItem]
    anomalies: list[AnalyticsAnomalyItem]
    methodology: str


class PromotionReviewDayPoint(BaseModel):
    day_label: str
    promo_date: str
    baseline_date: str
    promo_units: int
    baseline_units: int
    promo_gmv: Decimal
    baseline_gmv: Decimal


class PromotionReviewResponse(BaseModel):
    promotion_id: int
    promotion_no: str
    promotion_name: str
    status: str
    sku_codes: list[str]
    timezone: str
    starts_at: datetime
    ends_at: datetime
    promo_start_date: date
    promo_end_date: date
    baseline_start_date: date
    baseline_end_date: date
    duration_days: int
    promo_units: int
    promo_gmv: Decimal
    baseline_units: int
    baseline_gmv: Decimal
    units_change_pct: Decimal | None = None
    daily_comparison: list[PromotionReviewDayPoint]
    methodology: str
    review_note: str
