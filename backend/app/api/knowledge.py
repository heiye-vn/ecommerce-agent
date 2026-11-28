from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.auth import (
    AppConfig,
    AuthorizedShop,
    Database,
    OperatorShop,
    SessionFactory,
    reject_cross_origin,
)
from app.schemas.knowledge import (
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentDetail,
    KnowledgeDocumentSummary,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeStatus,
)
from app.services import knowledge as knowledge_service

router = APIRouter(prefix="/api/shops/{shop_id}/knowledge", tags=["knowledge"])


@router.get("/documents")
async def list_knowledge_documents(
    shop: AuthorizedShop,
    session: Database,
    keyword: Annotated[str | None, Query(max_length=120)] = None,
    status: Annotated[KnowledgeStatus | None, Query()] = None,
) -> list[KnowledgeDocumentSummary]:
    return await knowledge_service.list_documents(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        actor_roles=shop.roles,
        keyword=keyword,
        status=status,
    )


@router.get("/documents/{document_id}")
async def get_knowledge_document(
    shop: AuthorizedShop,
    session: Database,
    document_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> KnowledgeDocumentDetail:
    return await knowledge_service.get_document_detail(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        document_id=document_id,
        actor_roles=shop.roles,
    )


@router.post("/documents", dependencies=[Depends(reject_cross_origin)])
async def create_knowledge_document(
    payload: KnowledgeDocumentCreateRequest,
    shop: OperatorShop,
    session_factory: SessionFactory,
    settings: AppConfig,
) -> KnowledgeDocumentDetail:
    embedding_client = knowledge_service.build_embedding_client(settings)
    return await knowledge_service.create_and_index_document(
        session_factory,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        actor_id=shop.user_id,
        payload=payload,
        embedding_client=embedding_client,
    )


@router.post("/documents/{document_id}/reindex", dependencies=[Depends(reject_cross_origin)])
async def reindex_knowledge_document(
    shop: OperatorShop,
    session_factory: SessionFactory,
    settings: AppConfig,
    document_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> KnowledgeDocumentDetail:
    embedding_client = knowledge_service.build_embedding_client(settings)
    return await knowledge_service.reindex_document(
        session_factory,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        document_id=document_id,
        actor_id=shop.user_id,
        actor_roles=shop.roles,
        embedding_client=embedding_client,
    )


@router.post("/search", dependencies=[Depends(reject_cross_origin)])
async def search_knowledge_documents(
    payload: KnowledgeSearchRequest,
    shop: AuthorizedShop,
    session: Database,
    settings: AppConfig,
) -> KnowledgeSearchResponse:
    return await knowledge_service.search_knowledge_base(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        actor_roles=shop.roles,
        query=payload.query,
        embedding_client=knowledge_service.build_embedding_client(settings),
        rerank_client=knowledge_service.build_rerank_client(settings),
        embedding_model=settings.dashscope_embedding_model,
        embedding_dim=settings.dashscope_embedding_dimensions,
        top_n=payload.top_n,
    )
