import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from app.agent import graph
from app.agent.rag.rerank import RerankOutcome, RerankResultItem
from app.core.db import create_database_engine
from app.main import app
from app.models import Approval, AuditLog, InventoryMovement, PriceChangeRequest, Promotion, Refund
from app.schemas.agent import QueryIntent
from app.services import knowledge as knowledge_service
from app.services.seed import seed_demo_operator
from app.services.seed_catalog import seed_catalog
from app.services.seed_stage2 import SECOND_DEMO_SHOP, seed_stage2_sandbox
from app.services.seed_stage5b import seed_stage5b_sandbox

PASSWORD = "stage5b-agent-password-123"


def _topic_vector(dim: int) -> list[float]:
    vec = [0.0] * 1024
    vec[dim] = 1.0
    return vec


class _AgentEmbeddingStub:
    @property
    def model_name(self) -> str:
        return "qwen3.7-text-embedding-flash"

    @property
    def dimensions(self) -> int:
        return 1024

    async def embed_texts(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            if "退款" in text:
                vectors.append(_topic_vector(1))
            elif "售价" in text or "排期" in text or "复核" in text:
                vectors.append(_topic_vector(0))
            else:
                vectors.append(_topic_vector(2))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]


class _AgentRerankStub:
    async def rerank(self, query: str, documents: list[str] | tuple[str, ...], *, top_n: int) -> RerankOutcome:
        ranked: list[RerankResultItem] = []
        for idx, doc in enumerate(documents):
            score = 0.2
            if "§4.3" in doc and ("售价" in query or "排期" in query):
                score = 0.99
            elif "§3 重复退款" in doc and "退款" in query:
                score = 0.97
            ranked.append(RerankResultItem(index=idx, score=score))
        ranked.sort(key=lambda item: (-item.score, item.index))
        return RerankOutcome(items=ranked[:top_n], model_name="qwen3.7-text-rerank", degraded=False)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def agent_client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("SANDBOX_BUSINESS_DATE", "2026-11-21")
    monkeypatch.setattr(knowledge_service, "build_embedding_client", lambda _s: _AgentEmbeddingStub())
    monkeypatch.setattr(knowledge_service, "build_rerank_client", lambda _s: _AgentRerankStub())

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        asyncio.run(_seed_world(database_url))
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        command.downgrade(config, "base")


async def _seed_world(database_url: str) -> None:
    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            await seed_demo_operator(session, PASSWORD)
        async with sessions.begin() as session:
            await seed_catalog(session)
        async with sessions.begin() as session:
            await seed_stage2_sandbox(session, PASSWORD)
        await seed_stage5b_sandbox(sessions, embedding_client=_AgentEmbeddingStub())
    finally:
        await engine.dispose()


async def _business_write_counts(database_url: str) -> dict[str, int]:
    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            return {
                "promotions": (await session.scalar(select(func.count(Promotion.id)))) or 0,
                "refunds": (await session.scalar(select(func.count(Refund.id)))) or 0,
                "price_changes": (await session.scalar(select(func.count(PriceChangeRequest.id)))) or 0,
                "approvals": (await session.scalar(select(func.count(Approval.id)))) or 0,
                "movements": (await session.scalar(select(func.count(InventoryMovement.id)))) or 0,
                "audits": (await session.scalar(select(func.count(AuditLog.id)))) or 0,
            }
    finally:
        await engine.dispose()


def test_agent_knowledge_qa_returns_citations_without_business_writes(
    agent_client: TestClient,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stub_classify(_message: str) -> QueryIntent:
        return QueryIntent(kind="knowledge_qa")

    monkeypatch.setattr(graph, "classify", stub_classify)

    login_res = agent_client.post("/api/auth/login", json={"username": "ops.demo", "password": PASSWORD})
    assert login_res.status_code == 200
    session_data = login_res.json()
    shop = next(s for s in session_data["shops"] if s["name"] != SECOND_DEMO_SHOP)
    headers = {"Authorization": f"Bearer {session_data['access_token']}"}

    before_counts = asyncio.run(_business_write_counts(database_url))

    # 1. 促销规则问答：精确引用 DOC-001 V3 §4.3 第 6 页
    promo_qa = agent_client.post(
        f"/api/shops/{shop['id']}/agent/query",
        headers=headers,
        json={"message": "审批通过之后，如果 SKU 的售价变了，系统还能按原方案排期吗？"},
    )
    assert promo_qa.status_code == 200
    promo_ans = promo_qa.json()
    assert promo_ans["kind"] == "knowledge_qa"
    assert promo_ans["tool_calls"] == 1
    assert "DOC-001" in {c["doc_no"] for c in promo_ans["knowledge"]["citations"]}
    top_promo_cit = promo_ans["knowledge"]["citations"][0]
    assert top_promo_cit["document_version"] == 3
    assert "§4.3" in top_promo_cit["section_title"]
    assert top_promo_cit["page_label"] == "第 6 页"
    assert "未查询商品实时价格" in promo_ans["knowledge"]["isolation_notice"]

    # 2. 退款规则问答：精确引用 DOC-003 V2 §3 第 4 页
    refund_qa = agent_client.post(
        f"/api/shops/{shop['id']}/agent/query",
        headers=headers,
        json={"message": "客户说第一次没收到，订单已有一笔待处理退款，可以重新发起吗？"},
    )
    assert refund_qa.status_code == 200
    refund_ans = refund_qa.json()
    assert refund_ans["kind"] == "knowledge_qa"
    assert any(
        c["doc_no"] == "DOC-003" and "§3" in c["section_title"] and c["page_label"] == "第 4 页"
        for c in refund_ans["knowledge"]["citations"]
    )

    # 3. indexing 文档（DOC-004）与 failed 文档（DOC-005）绝不进入 Agent 引用
    unindexed_qa = agent_client.post(
        f"/api/shops/{shop['id']}/agent/query",
        headers=headers,
        json={"message": "换季商品类目与定价审核规则以及低库存预警阈值是多少？"},
    )
    assert unindexed_qa.status_code == 200
    cited_nos = {c["doc_no"] for c in unindexed_qa.json()["knowledge"]["citations"]}
    assert "DOC-004" not in cited_nos
    assert "DOC-005" not in cited_nos

    after_counts = asyncio.run(_business_write_counts(database_url))
    assert after_counts == before_counts
