from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import tools
from app.agent.graph import query_graph
from app.schemas.agent import AgentAnswer
from app.services.seed_catalog import DEMO_DATE


async def answer_query(session: AsyncSession, shop_id: int, message: str) -> AgentAnswer:
    async def query_sales(sku_code: str):
        return await tools.query_sales(session, shop_id, sku_code, DEMO_DATE.date())

    outcome = await query_graph.ainvoke({"message": message}, context={"query_sales": query_sales})
    return outcome["answer"]
