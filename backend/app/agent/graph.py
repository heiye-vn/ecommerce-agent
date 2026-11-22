import re
from collections.abc import Awaitable, Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agent.llm_gateway import SKU_PATTERN, classify
from app.schemas.agent import AgentAnswer, QueryIntent
from app.schemas.orders import SkuSales


class QueryState(TypedDict, total=False):
    message: str
    intent: QueryIntent
    answer: AgentAnswer


class QueryContext(TypedDict):
    query_sales: Callable[[str], Awaitable[SkuSales | None]]


async def recognize(state: QueryState) -> dict:
    return {"intent": await classify(state["message"])}


async def respond(state: QueryState, runtime: Runtime[QueryContext]) -> dict:
    intent = state["intent"]
    if intent.kind == "unsupported":
        return {"answer": AgentAnswer(kind="unsupported", message="当前仅支持本店单个 SKU 最近 7 天销量查询，不会创建促销或审批。")}
    codes = {match.group().upper() for match in SKU_PATTERN.finditer(state["message"])}
    code = intent.sku_code
    if not code or len(codes) != 1 or code.upper() not in codes or not re.fullmatch(SKU_PATTERN, code):
        return {"answer": AgentAnswer(kind="clarify", message="请提供一个明确的 SKU 编码，例如：查一下 SKU-001 最近 7 天销量。")}
    sku_code = code.upper()
    sales = await runtime.context["query_sales"](sku_code)
    if sales is None:
        return {"answer": AgentAnswer(kind="not_found", message=f"当前店铺没有找到 {sku_code}。请核对编码后重试。", tool_calls=1)}
    return {"answer": AgentAnswer(
        kind="sales", message=f"{sales.sku_code} 在当前店铺最近 7 个完整自然日共售出 {sales.units_sold} 件。",
        sales=sales, tool_calls=1,
    )}


builder = StateGraph(QueryState, context_schema=QueryContext)
builder.add_node("recognize", recognize)
builder.add_node("respond", respond)
builder.add_edge(START, "recognize")
builder.add_edge("recognize", "respond")
builder.add_edge("respond", END)
query_graph = builder.compile()
