import logging
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.rag.chunker import build_lexical_tsquery_expression
from app.agent.rag.embeddings import EmbeddingClient, EmbeddingUnavailableError
from app.agent.rag.rerank import RerankClient
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.models.org import Role

logger = logging.getLogger(__name__)

RRF_K = 60
VISIBILITY_SCOPE_LABELS: dict[str, str] = {
    "org_all": "全组织（运营/审批/管理）",
    "org_operator": "组织运营",
    "shop_operator": "店铺运营",
    "approver_only": "审批员与管理员",
    "admin_only": "仅管理员",
}
DEFAULT_SCOPE_ROLES: dict[str, list[str]] = {
    "org_all": [Role.ADMIN.value, Role.OPERATOR.value, Role.APPROVER.value],
    "org_operator": [Role.ADMIN.value, Role.OPERATOR.value],
    "shop_operator": [Role.ADMIN.value, Role.OPERATOR.value],
    "approver_only": [Role.ADMIN.value, Role.APPROVER.value],
    "admin_only": [Role.ADMIN.value],
}


def resolve_visibility_label(scope: str, allowed_roles: list[str] | None = None) -> str:
    if scope == "org_all":
        return VISIBILITY_SCOPE_LABELS["org_all"]
    if allowed_roles:
        role_names: list[str] = []
        if Role.OPERATOR.value in allowed_roles and Role.APPROVER.value in allowed_roles:
            return "运营、审批员" if scope == "org_operator" else "店铺运营、审批员"
        for role_val, label in (
            (Role.OPERATOR.value, "运营"),
            (Role.APPROVER.value, "审批员"),
        ):
            if role_val in allowed_roles:
                role_names.append(label)
        if role_names:
            prefix = "店铺" if scope == "shop_operator" else "组织" if scope == "org_operator" else ""
            if prefix and role_names == ["运营"]:
                return f"{prefix}运营"
            return "、".join(role_names)
    return VISIBILITY_SCOPE_LABELS.get(scope, scope)


def user_can_access_document(allowed_roles: list[str], actor_roles: list[Role]) -> bool:
    role_values = {role.value if isinstance(role, Role) else str(role) for role in actor_roles}
    if Role.ADMIN.value in role_values:
        return True
    return bool(role_values.intersection(allowed_roles))


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: int
    doc_no: str
    title: str
    document_version: int
    chunk_id: int
    chunk_index: int
    section_title: str
    page_label: str
    visibility_scope: str
    visibility_label: str
    excerpt: str
    content: str
    vector_rank: int | None
    lexical_rank: int | None
    rrf_score: float
    rerank_score: float | None


@dataclass(frozen=True)
class HybridSearchOutcome:
    items: list[RetrievedChunk]
    vector_candidates: int
    lexical_candidates: int
    vector_degraded: bool
    rerank_degraded: bool
    embedding_model: str
    rerank_model: str | None


async def hybrid_retrieve(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    actor_roles: list[Role],
    query: str,
    embedding_client: EmbeddingClient | None,
    rerank_client: RerankClient | None,
    embedding_model: str,
    embedding_dim: int,
    top_n: int = 4,
    rrf_limit: int = 8,
    branch_limit: int = 12,
) -> HybridSearchOutcome:
    """
    严格先按租户、店铺、当前有效已索引版本及角色过滤候选文档，
    再执行 pgvector 向量召回 + PostgreSQL 词法召回 + RRF 融合 + 百炼 Rerank 精排。
    """
    docs = (
        await session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.organization_id == organization_id,
                or_(KnowledgeDocument.shop_id.is_(None), KnowledgeDocument.shop_id == shop_id),
                KnowledgeDocument.status == "indexed",
                KnowledgeDocument.is_current.is_(True),
            )
        )
    ).all()

    authorized_docs = {
        doc.id: doc for doc in docs if user_can_access_document(list(doc.allowed_roles_json or []), actor_roles)
    }
    if not authorized_docs:
        return HybridSearchOutcome(
            items=[],
            vector_candidates=0,
            lexical_candidates=0,
            vector_degraded=False,
            rerank_degraded=False,
            embedding_model=embedding_model,
            rerank_model=None,
        )

    authorized_ids = list(authorized_docs.keys())
    vector_doc_ids = [
        doc_id
        for doc_id, doc in authorized_docs.items()
        if doc.embedding_model == embedding_model and doc.embedding_dim == embedding_dim
    ]

    vector_rows: list[KnowledgeChunk] = []
    vector_degraded = False
    if embedding_client is not None and vector_doc_ids:
        try:
            query_vec = await embedding_client.embed_query(query)
            if len(query_vec) == embedding_dim:
                distance_expr = KnowledgeChunk.embedding.cosine_distance(query_vec)
                vector_rows = list(
                    (
                        await session.scalars(
                            select(KnowledgeChunk)
                            .where(
                                KnowledgeChunk.document_id.in_(vector_doc_ids),
                                KnowledgeChunk.embedding.is_not(None),
                            )
                            .order_by(distance_expr.asc(), KnowledgeChunk.id.asc())
                            .limit(branch_limit)
                        )
                    ).all()
                )
            else:
                vector_degraded = True
        except EmbeddingUnavailableError as exc:
            vector_degraded = True
            logger.warning("Knowledge query embedding degraded to lexical search: %s", exc)
    elif vector_doc_ids:
        vector_degraded = True

    lexical_rows: list[KnowledgeChunk] = []
    tsquery_expr = build_lexical_tsquery_expression(query)
    if tsquery_expr:
        ts_query = func.to_tsquery("simple", tsquery_expr)
        rank_expr = func.ts_rank_cd(KnowledgeChunk.tsv_content, ts_query)
        lexical_rows = list(
            (
                await session.scalars(
                    select(KnowledgeChunk)
                    .where(
                        KnowledgeChunk.document_id.in_(authorized_ids),
                        KnowledgeChunk.tsv_content.op("@@")(ts_query),
                    )
                    .order_by(rank_expr.desc(), KnowledgeChunk.id.asc())
                    .limit(branch_limit)
                )
            ).all()
        )

    chunk_by_id: dict[int, KnowledgeChunk] = {}
    vec_rank_map: dict[int, int] = {}
    lex_rank_map: dict[int, int] = {}
    rrf_scores: dict[int, float] = {}

    for rank, chunk in enumerate(vector_rows, start=1):
        chunk_by_id[chunk.id] = chunk
        vec_rank_map[chunk.id] = rank
        rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + (1.0 / (RRF_K + rank))

    for rank, chunk in enumerate(lexical_rows, start=1):
        chunk_by_id[chunk.id] = chunk
        lex_rank_map[chunk.id] = rank
        rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + (1.0 / (RRF_K + rank))

    if not chunk_by_id:
        return HybridSearchOutcome(
            items=[],
            vector_candidates=len(vector_rows),
            lexical_candidates=len(lexical_rows),
            vector_degraded=vector_degraded,
            rerank_degraded=False,
            embedding_model=embedding_model,
            rerank_model=None,
        )

    ordered_ids = sorted(
        rrf_scores.keys(),
        key=lambda cid: (-rrf_scores[cid], cid),
    )[:rrf_limit]
    rrf_chunks = [chunk_by_id[cid] for cid in ordered_ids]

    rerank_degraded = False
    rerank_model_used: str | None = None
    rerank_scores: dict[int, float | None] = {cid: None for cid in ordered_ids}

    if rerank_client is not None:
        doc_texts = [
            f"{authorized_docs[c.document_id].title} | {c.section_title} ({c.page_label})\n{c.content}"
            for c in rrf_chunks
        ]
        outcome = await rerank_client.rerank(query, doc_texts, top_n=min(top_n, len(rrf_chunks)))
        rerank_degraded = outcome.degraded
        rerank_model_used = outcome.model_name
        if not outcome.degraded and outcome.items:
            final_chunks = []
            for item in outcome.items:
                chosen = rrf_chunks[item.index]
                rerank_scores[chosen.id] = item.score
                final_chunks.append(chosen)
        else:
            final_chunks = rrf_chunks[:top_n]
    else:
        final_chunks = rrf_chunks[:top_n]

    results: list[RetrievedChunk] = []
    for chunk in final_chunks:
        doc = authorized_docs[chunk.document_id]
        excerpt = chunk.content.strip().replace("\n", " ")
        if len(excerpt) > 160:
            excerpt = f"{excerpt[:157]}..."
        results.append(
            RetrievedChunk(
                document_id=doc.id,
                doc_no=doc.doc_no,
                title=doc.title,
                document_version=chunk.document_version,
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                section_title=chunk.section_title,
                page_label=chunk.page_label,
                visibility_scope=doc.visibility_scope,
                visibility_label=resolve_visibility_label(
                    doc.visibility_scope, list(doc.allowed_roles_json or [])
                ),
                excerpt=excerpt,
                content=chunk.content,
                vector_rank=vec_rank_map.get(chunk.id),
                lexical_rank=lex_rank_map.get(chunk.id),
                rrf_score=round(rrf_scores[chunk.id], 6),
                rerank_score=rerank_scores.get(chunk.id),
            )
        )

    return HybridSearchOutcome(
        items=results,
        vector_candidates=len(vector_rows),
        lexical_candidates=len(lexical_rows),
        vector_degraded=vector_degraded,
        rerank_degraded=rerank_degraded,
        embedding_model=embedding_model,
        rerank_model=rerank_model_used,
    )
