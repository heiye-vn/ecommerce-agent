import asyncio
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from app.agent.rag.rerank import DashScopeRerankClient, RerankOutcome, RerankResultItem
from app.core.db import create_database_engine
from app.main import app
from app.models import AuditLog, KnowledgeChunk, KnowledgeDocument
from app.services import knowledge as knowledge_service
from app.services.seed import seed_demo_operator
from app.services.seed_catalog import seed_catalog
from app.services.seed_stage2 import APPROVER_USERNAME, SECOND_DEMO_SHOP, seed_stage2_sandbox
from app.services.seed_stage5b import seed_stage5b_sandbox

PASSWORD = "stage5b-rag-password-123"


def _basis_vector(topic_dim: int, secondary_dim: int = 10) -> list[float]:
    vec = [0.0] * 1024
    vec[topic_dim] = 0.99
    vec[secondary_dim] = 0.14106735979665885
    return vec


class DeterministicEmbeddingStub:
    def __init__(self, *, model_name: str = "qwen3.7-text-embedding-flash", fail: bool = False) -> None:
        self._model_name = model_name
        self._fail = fail

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return 1024

    def _vector_for(self, text: str) -> list[float]:
        if "退款" in text or "重复退款" in text:
            return _basis_vector(1, 21)
        if "售价" in text or "排期" in text or "复核" in text or "促销" in text:
            return _basis_vector(0, 20)
        if "换季" in text or "类目" in text:
            return _basis_vector(2, 22)
        if "审批通过后" in text or "驳回" in text or "重提" in text:
            return _basis_vector(3, 23)
        return _basis_vector(4, 24)

    async def embed_texts(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        if self._fail:
            from app.agent.rag.embeddings import EmbeddingUnavailableError

            raise EmbeddingUnavailableError("stub embedding failure")
        return [self._vector_for(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]


class DeterministicRerankStub:
    async def rerank(self, query: str, documents: list[str] | tuple[str, ...], *, top_n: int) -> RerankOutcome:
        scored: list[RerankResultItem] = []
        for idx, doc in enumerate(documents):
            score = 0.2
            if "§4.3" in doc and ("售价" in query or "排期" in query):
                score = 0.98
            elif "§3 重复退款" in doc and "退款" in query:
                score = 0.96
            elif "换季" in doc and "换季" in query:
                score = 0.95
            scored.append(RerankResultItem(index=idx, score=score))
        scored.sort(key=lambda item: (-item.score, item.index))
        return RerankOutcome(items=scored[:top_n], model_name="qwen3.7-text-rerank", degraded=False)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def stage5b_client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-signing-key-with-more-than-32-bytes")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("SANDBOX_BUSINESS_DATE", "2026-11-21")
    monkeypatch.setenv("DASHSCOPE_EMBEDDING_MODEL", "qwen3.7-text-embedding-flash")
    monkeypatch.setenv("DASHSCOPE_EMBEDDING_DIMENSIONS", "1024")
    monkeypatch.setenv("DASHSCOPE_RERANK_MODEL", "qwen3.7-text-rerank")
    monkeypatch.setattr(knowledge_service, "build_embedding_client", lambda _s: DeterministicEmbeddingStub())
    monkeypatch.setattr(knowledge_service, "build_rerank_client", lambda _s: DeterministicRerankStub())

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
        outcome = await seed_stage5b_sandbox(sessions, embedding_client=DeterministicEmbeddingStub())
        assert all(outcome.values())
        second_outcome = await seed_stage5b_sandbox(sessions, embedding_client=DeterministicEmbeddingStub())
        assert not any(second_outcome.values())
    finally:
        await engine.dispose()


def _login(client: TestClient, username: str) -> dict:
    res = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200
    return res.json()


def test_hybrid_search_rerank_fallback_scope_and_reindex_lifecycle(
    stage5b_client: TestClient,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    op_session = _login(stage5b_client, "ops.demo")
    shop_main = next(s for s in op_session["shops"] if s["name"] != SECOND_DEMO_SHOP)
    shop_other = next(s for s in op_session["shops"] if s["name"] == SECOND_DEMO_SHOP)
    headers = {"Authorization": f"Bearer {op_session['access_token']}"}

    # 1. 文档列表与详情：可见 5 份演示文档（含 indexed / indexing / failed）
    list_res = stage5b_client.get(f"/api/shops/{shop_main['id']}/knowledge/documents", headers=headers)
    assert list_res.status_code == 200
    docs = list_res.json()
    doc_by_no = {item["doc_no"]: item for item in docs}
    assert {"DOC-001", "DOC-002", "DOC-003", "DOC-004", "DOC-005"} <= set(doc_by_no)
    assert doc_by_no["DOC-004"]["status"] == "indexing"
    assert doc_by_no["DOC-005"]["status"] == "failed"

    # 2. 混合检索（pgvector + 词法 + RRF + Rerank）：命中 DOC-001 V3 §4.3 第 6 页，绝不召回 indexing / failed
    search_res = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/search",
        headers=headers,
        json={"query": "审批通过后如果 SKU 的售价变了还能按原方案排期吗", "top_n": 3},
    )
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert search_data["embedding_model"] == "qwen3.7-text-embedding-flash"
    assert search_data["rerank_degraded"] is False
    assert search_data["vector_candidates"] >= 1
    assert search_data["lexical_candidates"] >= 1
    top_hit = search_data["items"][0]
    assert top_hit["doc_no"] == "DOC-001"
    assert top_hit["document_version"] == 3
    assert "§4.3" in top_hit["section_title"]
    assert top_hit["page_label"] == "第 6 页"
    assert all(item["doc_no"] not in {"DOC-004", "DOC-005"} for item in search_data["items"])

    # 3. indexing 与 failed 文档中的专属关键词（低库存预警 / 换季商品类目）在恢复索引前绝不召回
    unindexed_search = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/search",
        headers=headers,
        json={"query": "换季商品类目定价审核", "top_n": 3},
    )
    assert unindexed_search.status_code == 200
    assert all(item["doc_no"] != "DOC-005" for item in unindexed_search.json()["items"])

    # 4. 恢复 DOC-005 重建索引后进入可检索集合并写入 AuditLog
    reindex_res = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/documents/{doc_by_no['DOC-005']['id']}/reindex",
        headers=headers,
    )
    assert reindex_res.status_code == 200
    reindexed_doc = reindex_res.json()
    assert reindexed_doc["status"] == "indexed"
    assert reindexed_doc["embedding_model"] == "qwen3.7-text-embedding-flash"
    assert reindexed_doc["embedding_dim"] == 1024
    assert all(chunk["has_embedding"] for chunk in reindexed_doc["chunks"])

    recovered_search = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/search",
        headers=headers,
        json={"query": "换季商品类目审核", "top_n": 3},
    )
    assert any(item["doc_no"] == "DOC-005" for item in recovered_search.json()["items"])

    # 5. Rerank 超时降级测试：依然返回 RRF 排序结果并标记 rerank_degraded=True
    def timeout_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    monkeypatch.setattr(
        knowledge_service,
        "build_rerank_client",
        lambda _s: DashScopeRerankClient(
            rerank_url="https://dashscope.example.invalid/rerank",
            api_key="test-key",
            model_name="qwen3.7-text-rerank",
            transport=httpx.MockTransport(timeout_transport),
        ),
    )
    degraded_search = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/search",
        headers=headers,
        json={"query": "重复退款处理规范", "top_n": 3},
    )
    assert degraded_search.status_code == 200
    degraded_payload = degraded_search.json()
    assert degraded_payload["rerank_degraded"] is True
    assert len(degraded_payload["items"]) >= 1
    monkeypatch.setattr(knowledge_service, "build_rerank_client", lambda _s: DeterministicRerankStub())

    # 6. 同 doc_no 上传新版本（V4 成功）；上传 V5 但向量失败时不覆盖已有 is_current=True 的 V4
    v4_res = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/documents",
        headers=headers,
        json={
            "doc_no": "DOC-001",
            "title": "促销运营规则",
            "visibility_scope": "org_operator",
            "raw_content": "# §4.3 审批后复核\n[第 6 页]\n审批通过后如 SKU 售价变动，V4 版同样禁止直接排期。",
        },
    )
    assert v4_res.status_code == 200
    assert v4_res.json()["version"] == 4
    assert v4_res.json()["is_current"] is True

    monkeypatch.setattr(
        knowledge_service,
        "build_embedding_client",
        lambda _s: DeterministicEmbeddingStub(fail=True),
    )
    v5_fail_res = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/documents",
        headers=headers,
        json={
            "doc_no": "DOC-001",
            "title": "促销运营规则",
            "visibility_scope": "org_operator",
            "raw_content": "# §4.3 审批后复核\n[第 6 页]\n第五版草稿测试失败隔离。",
        },
    )
    assert v5_fail_res.status_code == 200
    assert v5_fail_res.json()["version"] == 5
    assert v5_fail_res.json()["status"] == "failed"
    assert v5_fail_res.json()["is_current"] is False
    monkeypatch.setattr(knowledge_service, "build_embedding_client", lambda _s: DeterministicEmbeddingStub())

    check_v4_search = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/search",
        headers=headers,
        json={"query": "售价变了能否排期", "top_n": 3},
    )
    hit_versions = {item["document_version"] for item in check_v4_search.json()["items"] if item["doc_no"] == "DOC-001"}
    assert hit_versions == {4}

    # 7. 店铺隔离 + 角色权限隔离（仅店铺专属文档在茶饮专营店不可见；审批员看不到 org_operator 文档且无权上传）
    shop_scoped_res = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/documents",
        headers=headers,
        json={
            "title": "轻食店专属冷链打包准则",
            "visibility_scope": "shop_operator",
            "shop_only": True,
            "raw_content": "# §1 轻食冷链标准\n[第 1 页]\n冷链独有专规仅轻食旗舰店可见。",
        },
    )
    assert shop_scoped_res.status_code == 200
    scoped_id = shop_scoped_res.json()["id"]

    other_shop_detail = stage5b_client.get(
        f"/api/shops/{shop_other['id']}/knowledge/documents/{scoped_id}",
        headers=headers,
    )
    assert other_shop_detail.status_code == 404

    approver_session = _login(stage5b_client, APPROVER_USERNAME)
    app_headers = {"Authorization": f"Bearer {approver_session['access_token']}"}
    app_list = stage5b_client.get(f"/api/shops/{shop_main['id']}/knowledge/documents", headers=app_headers)
    assert app_list.status_code == 200
    app_nos = {item["doc_no"] for item in app_list.json()}
    assert "DOC-001" not in app_nos
    assert "DOC-003" in app_nos

    app_upload = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/documents",
        headers=app_headers,
        json={"title": "越权文档", "raw_content": "# 标题\n足够长的文本内容测试角色权限拦截。"},
    )
    assert app_upload.status_code == 403

    # 8. 向量模型空间隔离：若数据库中有旧 embedding_model 标识文档，不混入现行向量检索（并校验审计记录）
    async def _check_isolation_and_audit() -> None:
        engine = create_database_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as session:
                await session.execute(
                    update(KnowledgeDocument)
                    .where(KnowledgeDocument.doc_no == "DOC-005")
                    .values(embedding_model="legacy-embedding-v0")
                )
                count_audit = await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.resource_type == "knowledge_document",
                        AuditLog.action == "knowledge.reindexed",
                    )
                )
                assert (count_audit or 0) >= 1
                chunk_vec_count = await session.scalar(
                    select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.embedding.is_not(None))
                )
                assert (chunk_vec_count or 0) >= 3
        finally:
            await engine.dispose()

    asyncio.run(_check_isolation_and_audit())
    model_isolated = stage5b_client.post(
        f"/api/shops/{shop_main['id']}/knowledge/search",
        headers=headers,
        json={"query": "换季商品类目审核", "top_n": 3},
    )
    doc5_hits = [item for item in model_isolated.json()["items"] if item["doc_no"] == "DOC-005"]
    assert all(item["vector_rank"] is None for item in doc5_hits)
