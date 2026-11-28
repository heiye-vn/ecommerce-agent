from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

InventoryMovementType = Literal["opening_balance", "reserve", "release", "fulfill", "restock", "adjust"]
InventoryMutationType = Literal["reserve", "release", "fulfill", "restock"]


class InventorySnapshot(BaseModel):
    sku_id: int
    sku_code: str
    product_name: str
    category: str
    status: str
    available_qty: int | None
    reserved_qty: int | None
    total_qty: int | None
    version: int | None
    low_stock: bool
    low_stock_threshold: int


class InventoryOverviewResponse(BaseModel):
    low_stock_threshold: int
    low_stock_count: int
    items: list[InventorySnapshot]


class InventoryMovementItem(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    product_name: str
    movement_type: InventoryMovementType
    quantity: int
    available_delta: int
    reserved_delta: int
    before_available: int | None = None
    after_available: int | None = None
    before_reserved: int | None = None
    after_reserved: int | None = None
    reference_id: str
    note: str | None = None
    actor_id: int | None = None
    actor_name: str | None = None
    created_at: datetime


class InventoryMovementCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    movement_type: InventoryMutationType
    quantity: int = Field(gt=0, le=100_000)
    reference_id: str = Field(min_length=1, max_length=64)
    expected_version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=255)


class InventoryMovementMutationResponse(BaseModel):
    snapshot: InventorySnapshot
    movement: InventoryMovementItem
    replayed: bool = False
    message: str
