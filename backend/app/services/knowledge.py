from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.rag.chunker import ChunkDraft, chunk_markdown_document
from app.agent.rag.embeddings import (
    DashScopeEmbeddingClient,
    EmbeddingClient,
    EmbeddingUnavailableError,
)
from app.agent.rag.rerank import DashScopeRerankClient, RerankClient
from app.agent.rag.retriever import (
    DEFAULT_SCOPE_ROLES,
    hybrid_retrieve,
    resolve_visibility_label,
    user_can_access_document,
)
from app.core.config import AppSettings
from app.core.errors import AppError
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.models.org import Role
from app.schemas.knowledge import (
    KnowledgeChunkPreview,
    KnowledgeCitationItem,
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentDetail,
    KnowledgeDocumentSummary,
    KnowledgeSearchResponse,
    KnowledgeStatus,
    VisibilityScope,
)
from app.services.audit import write_audit_log


def build_embedding_client(settings: AppSettings) -> DashScopeEmbeddingClient:
    return DashScopeEmbeddingClient(
        base_url=settings.dashscope_base_url,
        api_key=settings.dashscope_api_key,
        model_name=settings.dashscope_embedding_model,
        dimensions=settings.dashscope_embedding_dimensions,
    )


def build_rerank_client(settings: AppSettings) -> DashScopeRerankClient:
    return DashScopeRerankClient(
        rerank_url=settings.dashscope_rerank_url,
        api_key=settings.dashscope_api_key,
        model_name=settings.dashscope_rerank_model,
    )


def _resolve_allowed_roles(
    scope: VisibilityScope, explicit_roles: Sequence[str] | None
) -> list[str]:
    if explicit_roles:
        roles = {Role.ADMIN.value}
        for r in explicit_roles:
            if r in {Role.ADMIN.value, Role.OPERATOR.value, Role.APPROVER.value}:
                roles.add(r)
        return sorted(roles)
    return list(DEFAULT_SCOPE_ROLES[scope])


async def next_knowledge_doc_no(session: AsyncSession, organization_id: int) -> str:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(726600, :organization_id)"),
        {"organization_id": organization_id},
    )
    existing_nos = list(
        (
            await session.scalars(
                select(KnowledgeDocument.doc_no)
                .where(KnowledgeDocument.organization_id == organization_id)
                .distinct()
            )
        ).all()
    )
    max_seq = 0
    for code in existing_nos:
        if code.startswith("DOC-"):
            tail = code[4:]
            if tail.isdigit():
                max_seq = max(max_seq, int(tail))
    return f"DOC-{max_seq + 1:03d}"


def _build_excerpt(raw: str | None) -> str | None:
    if not raw:
        return None
    normalized = raw.strip().replace("\n", " ")
    if len(normalized) <= 140:
        return normalized
    return f"{normalized[:137]}..."


def _to_summary(
    doc: KnowledgeDocument, first_chunk: KnowledgeChunk | None = None
) -> KnowledgeDocumentSummary:
    allowed_roles = list(doc.allowed_roles_json or [])
    return KnowledgeDocumentSummary(
        id=doc.id,
        doc_no=doc.doc_no,
        title=doc.title,
        version=doc.version,
        is_current=doc.is_current,
        shop_id=doc.shop_id,
        visibility_scope=cast(VisibilityScope, doc.visibility_scope),
        visibility_label=resolve_visibility_label(doc.visibility_scope, allowed_roles),
        allowed_roles=allowed_roles,
        status=cast(KnowledgeStatus, doc.status),
        embedding_model=doc.embedding_model,
        embedding_dim=doc.embedding_dim,
        chunk_count=doc.chunk_count,
        last_error=doc.last_error,
        preview_section=first_chunk.section_title if first_chunk else None,
        preview_page_label=first_chunk.page_label if first_chunk else None,
        preview_excerpt=_build_excerpt(first_chunk.content if first_chunk else doc.raw_content),
        indexed_at=doc.indexed_at,
        updated_at=doc.updated_at,
    )


def _to_detail(doc: KnowledgeDocument, chunks: list[KnowledgeChunk]) -> KnowledgeDocumentDetail:
    summary = _to_summary(doc, chunks[0] if chunks else None)
    return KnowledgeDocumentDetail(
        **summary.model_dump(),
        raw_content=doc.raw_content,
        chunks=[
            KnowledgeChunkPreview(
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                section_title=chunk.section_title,
                page_label=chunk.page_label,
                content=chunk.content,
                has_embedding=chunk.embedding is not None,
            )
            for chunk in chunks
        ],
    )


async def list_documents(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    actor_roles: list[Role],
    keyword: str | None = None,
    status: KnowledgeStatus | None = None,
) -> list[KnowledgeDocumentSummary]:
    query = (
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.organization_id == organization_id,
            or_(KnowledgeDocument.shop_id.is_(None), KnowledgeDocument.shop_id == shop_id),
        )
        .order_by(KnowledgeDocument.doc_no.asc(), KnowledgeDocument.version.desc(), KnowledgeDocument.id.desc())
    )
    if status is not None:
        query = query.where(KnowledgeDocument.status == status)
    docs = list((await session.scalars(query)).all())

    # 同一 doc_no 保留最新版本及仍生效的当前版本（如新版本失败时不遮蔽旧可用版本）
    visible_docs: list[KnowledgeDocument] = []
    seen_latest: set[str] = set()
    kw = (keyword or "").strip().lower()
    for doc in docs:
        if not user_can_access_document(list(doc.allowed_roles_json or []), actor_roles):
            continue
        is_latest_for_no = doc.doc_no not in seen_latest
        if is_latest_for_no:
            seen_latest.add(doc.doc_no)
        elif not doc.is_current:
            continue
        if kw and kw not in doc.title.lower() and kw not in doc.doc_no.lower():
            continue
        visible_docs.append(doc)

    if not visible_docs:
        return []

    doc_ids = [doc.id for doc in visible_docs]
    first_chunks_rows = (
        await session.scalars(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.document_id.in_(doc_ids),
                KnowledgeChunk.chunk_index == 0,
            )
        )
    ).all()
    chunk_by_doc = {chunk.document_id: chunk for chunk in first_chunks_rows}
    return [_to_summary(doc, chunk_by_doc.get(doc.id)) for doc in visible_docs]


async def get_document_detail(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    document_id: int,
    actor_roles: list[Role],
) -> KnowledgeDocumentDetail:
    doc = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.organization_id == organization_id,
            or_(KnowledgeDocument.shop_id.is_(None), KnowledgeDocument.shop_id == shop_id),
        )
    )
    if doc is None:
        raise AppError(
            404,
            "KNOWLEDGE_DOCUMENT_NOT_FOUND",
            "当前店铺无此规则文档或未关联该知识库。",
            detail="Knowledge document not found",
        )
    if not user_can_access_document(list(doc.allowed_roles_json or []), actor_roles):
        raise AppError(
            403,
            "KNOWLEDGE_DOCUMENT_FORBIDDEN",
            "当前角色无权查看该知识库文档。",
            detail="Knowledge document role forbidden",
        )
    chunks = list(
        (
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == doc.id)
                .order_by(KnowledgeChunk.chunk_index.asc())
            )
        ).all()
    )
    return _to_detail(doc, chunks)


async def _embed_drafts(
    drafts: list[ChunkDraft],
    embedding_client: EmbeddingClient | None,
) -> tuple[list[list[float]] | None, str | None, int | None, str | None]:
    if not drafts:
        return [], None, None, "文档内容为空，无法生成知识分片。"
    if embedding_client is None:
        return None, None, None, "未配置百炼向量模型客户端。"
    texts = [f"{draft.section_title}\n{draft.content}" for draft in drafts]
    try:
        vectors = await embedding_client.embed_texts(texts)
        if len(vectors) != len(drafts):
            return (
                None,
                embedding_client.model_name,
                embedding_client.dimensions,
                "向量返回条目数与分片数不一致",
            )
        return vectors, embedding_client.model_name, embedding_client.dimensions, None
    except EmbeddingUnavailableError as exc:
        return None, embedding_client.model_name, embedding_client.dimensions, str(exc)


async def _replace_document_chunks(
    session: AsyncSession,
    doc: KnowledgeDocument,
    drafts: list[ChunkDraft],
    vectors: list[list[float]] | None,
) -> list[KnowledgeChunk]:
    await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
    chunks: list[KnowledgeChunk] = []
    for idx, draft in enumerate(drafts):
        chunk = KnowledgeChunk(
            organization_id=doc.organization_id,
            shop_id=doc.shop_id,
            document_id=doc.id,
            document_version=doc.version,
            chunk_index=draft.chunk_index,
            section_title=draft.section_title,
            page_label=draft.page_label,
            content=draft.content,
            lexical_text=draft.lexical_text,
            tsv_content=func.to_tsvector("simple", draft.lexical_text),
            embedding=vectors[idx] if vectors is not None else None,
        )
        chunks.append(chunk)
    session.add_all(chunks)
    await session.flush()
    return chunks


async def create_and_index_document(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: int,
    shop_id: int,
    actor_id: int,
    payload: KnowledgeDocumentCreateRequest,
    embedding_client: EmbeddingClient | None,
) -> KnowledgeDocumentDetail:
    allowed_roles = _resolve_allowed_roles(payload.visibility_scope, payload.allowed_roles)
    target_shop_id = shop_id if (payload.shop_only or payload.visibility_scope == "shop_operator") else None
    drafts = chunk_markdown_document(payload.raw_content, default_title=payload.title)

    # 1. 短事务创建文档记录（状态 indexing），释放连接后再请求百炼 Embedding API
    async with session_factory.begin() as session:
        doc_no = (payload.doc_no or "").strip().upper()
        if not doc_no:
            doc_no = await next_knowledge_doc_no(session, organization_id)
        latest_ver = await session.scalar(
            select(func.max(KnowledgeDocument.version)).where(
                KnowledgeDocument.organization_id == organization_id,
                KnowledgeDocument.doc_no == doc_no,
            )
        )
        next_version = (latest_ver or 0) + 1
        has_indexed_sibling = (
            await session.scalar(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.organization_id == organization_id,
                    KnowledgeDocument.doc_no == doc_no,
                    KnowledgeDocument.status == "indexed",
                )
            )
            is not None
        )
        doc = KnowledgeDocument(
            organization_id=organization_id,
            shop_id=target_shop_id,
            doc_no=doc_no,
            title=payload.title.strip(),
            version=next_version,
            is_current=not has_indexed_sibling,
            visibility_scope=payload.visibility_scope,
            allowed_roles_json=allowed_roles,
            raw_content=payload.raw_content.strip(),
            status="indexing",
            chunk_count=len(drafts),
            created_by=actor_id,
        )
        session.add(doc)
        await session.flush()
        doc_id = doc.id

    # 2. 事务外执行百炼向量化，避免持锁阻塞数据库连接
    vectors, model_name, model_dim, embed_error = await _embed_drafts(drafts, embedding_client)

    # 3. 独立短事务落库分片、更新状态与写入审计
    async with session_factory.begin() as session:
        persisted_doc = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
        assert persisted_doc is not None
        now = datetime.now(UTC)
        chunks = await _replace_document_chunks(session, persisted_doc, drafts, vectors)
        persisted_doc.chunk_count = len(chunks)
        persisted_doc.updated_at = now
        if vectors is not None:
            siblings = (
                await session.scalars(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.organization_id == organization_id,
                        KnowledgeDocument.doc_no == persisted_doc.doc_no,
                        KnowledgeDocument.id != persisted_doc.id,
                    )
                )
            ).all()
            for sibling in siblings:
                sibling.is_current = False
            persisted_doc.is_current = True
            persisted_doc.status = "indexed"
            persisted_doc.embedding_model = model_name
            persisted_doc.embedding_dim = model_dim
            persisted_doc.last_error = None
            persisted_doc.indexed_at = now
            audit_action = "knowledge.indexed"
        else:
            persisted_doc.status = "failed"
            persisted_doc.embedding_model = model_name
            persisted_doc.embedding_dim = model_dim
            persisted_doc.last_error = embed_error or "向量化构建失败"
            audit_action = "knowledge.index_failed"

        await write_audit_log(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action=audit_action,
            resource_type="knowledge_document",
            resource_id=persisted_doc.id,
            after_json={
                "doc_no": persisted_doc.doc_no,
                "version": persisted_doc.version,
                "status": persisted_doc.status,
                "chunk_count": persisted_doc.chunk_count,
                "embedding_model": persisted_doc.embedding_model,
                "embedding_dim": persisted_doc.embedding_dim,
            },
        )
        await session.flush()
        return _to_detail(persisted_doc, chunks)


async def reindex_document(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: int,
    shop_id: int,
    document_id: int,
    actor_id: int,
    actor_roles: list[Role],
    embedding_client: EmbeddingClient | None,
) -> KnowledgeDocumentDetail:
    async with session_factory.begin() as session:
        doc = await session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.organization_id == organization_id,
                or_(KnowledgeDocument.shop_id.is_(None), KnowledgeDocument.shop_id == shop_id),
            )
        )
        if doc is None:
            raise AppError(
                404,
                "KNOWLEDGE_DOCUMENT_NOT_FOUND",
                "未找到需要重建索引的规则文档。",
                detail="Knowledge document not found",
            )
        if not user_can_access_document(list(doc.allowed_roles_json or []), actor_roles):
            raise AppError(
                403,
                "KNOWLEDGE_DOCUMENT_FORBIDDEN",
                "当前角色无权重建该规则文档索引。",
                detail="Knowledge document role forbidden",
            )
        prev_status = doc.status
        doc.status = "indexing"
        doc.last_error = None
        doc.updated_at = datetime.now(UTC)
        raw_content = doc.raw_content
        title = doc.title

    drafts = chunk_markdown_document(raw_content, default_title=title)
    vectors, model_name, model_dim, embed_error = await _embed_drafts(drafts, embedding_client)

    async with session_factory.begin() as session:
        doc = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
        assert doc is not None
        now = datetime.now(UTC)
        chunks = await _replace_document_chunks(session, doc, drafts, vectors)
        doc.chunk_count = len(chunks)
        doc.updated_at = now
        if vectors is not None:
            siblings = (
                await session.scalars(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.organization_id == organization_id,
                        KnowledgeDocument.doc_no == doc.doc_no,
                        KnowledgeDocument.id != doc.id,
                    )
                )
            ).all()
            for sibling in siblings:
                sibling.is_current = False
            doc.is_current = True
            doc.status = "indexed"
            doc.embedding_model = model_name
            doc.embedding_dim = model_dim
            doc.last_error = None
            doc.indexed_at = now
            audit_action = "knowledge.reindexed"
        else:
            doc.status = "failed"
            doc.embedding_model = model_name
            doc.embedding_dim = model_dim
            doc.last_error = embed_error or "重建向量索引失败"
            audit_action = "knowledge.reindex_failed"

        await write_audit_log(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            actor_id=actor_id,
            action=audit_action,
            resource_type="knowledge_document",
            resource_id=doc.id,
            before_json={"status": prev_status},
            after_json={
                "status": doc.status,
                "chunk_count": doc.chunk_count,
                "embedding_model": doc.embedding_model,
                "embedding_dim": doc.embedding_dim,
            },
        )
        await session.flush()
        return _to_detail(doc, chunks)


async def search_knowledge_base(
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
) -> KnowledgeSearchResponse:
    outcome = await hybrid_retrieve(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_roles=actor_roles,
        query=query,
        embedding_client=embedding_client,
        rerank_client=rerank_client,
        embedding_model=embedding_model,
        embedding_dim=embedding_dim,
        top_n=top_n,
    )
    return KnowledgeSearchResponse(
        query=query,
        items=[
            KnowledgeCitationItem(
                document_id=item.document_id,
                doc_no=item.doc_no,
                title=item.title,
                document_version=item.document_version,
                chunk_id=item.chunk_id,
                chunk_index=item.chunk_index,
                section_title=item.section_title,
                page_label=item.page_label,
                visibility_scope=cast(VisibilityScope, item.visibility_scope),
                visibility_label=item.visibility_label,
                excerpt=item.excerpt,
                content=item.content,
                vector_rank=item.vector_rank,
                lexical_rank=item.lexical_rank,
                rrf_score=item.rrf_score,
                rerank_score=item.rerank_score,
            )
            for item in outcome.items
        ],
        vector_candidates=outcome.vector_candidates,
        lexical_candidates=outcome.lexical_candidates,
        vector_degraded=outcome.vector_degraded,
        rerank_degraded=outcome.rerank_degraded,
        embedding_model=outcome.embedding_model,
        rerank_model=outcome.rerank_model,
    )
