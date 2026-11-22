from fastapi import APIRouter, Depends, HTTPException

from app.agent.llm_gateway import ModelUnavailable
from app.api.auth import AuthorizedShop, Database, reject_cross_origin
from app.schemas.agent import AgentAnswer, AgentQuery
from app.services.agent import answer_query


router = APIRouter(prefix="/api/shops/{shop_id}/agent", tags=["agent"])


@router.post("/query", dependencies=[Depends(reject_cross_origin)])
async def query_agent(payload: AgentQuery, shop: AuthorizedShop, session: Database) -> AgentAnswer:
    try:
        return await answer_query(session, shop.id, payload.message)
    except ModelUnavailable:
        raise HTTPException(503, "Model temporarily unavailable") from None
