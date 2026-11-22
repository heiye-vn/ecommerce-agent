from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.orders import SkuSales


class AgentQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message: str = Field(min_length=1, max_length=500)


class QueryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["sales", "unsupported"]
    sku_code: str | None = Field(default=None, max_length=64)


class AgentAnswer(BaseModel):
    kind: Literal["sales", "clarify", "unsupported", "not_found"]
    message: str
    tool_calls: Literal[0, 1] = 0
    sales: SkuSales | None = None
