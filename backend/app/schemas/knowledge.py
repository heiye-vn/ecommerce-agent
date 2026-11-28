from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VisibilityScope = Literal["org_all", "org_operator", "shop_operator", "approver_only", "admin_only"]
KnowledgeStatus = Literal["pending", "indexing", "indexed", "failed"]


class KnowledgeDocumentCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    doc_no: str | None = Field(default=None, pattern=r"^DOC-[A-Z0-9][A-Z0-9_-]*$", max_length=64)
    title: str = Field(min_length=2, max_length=200)
    visibility_scope: VisibilityScope = "org_operator"
    allowed_roles: list[Literal["admin", "operator", "approver"]] | None = None
    shop_only: bool = False
    raw_content: str = Field(min_length=10, max_length=60000)


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(min_length=2, max_length=500)
    top_n: int = Field(default=4, ge=1, le=8)


class KnowledgeChunkPreview(BaseModel):
    chunk_id: int
    chunk_index: int
    section_title: str
    page_label: str
    content: str
    has_embedding: bool


class KnowledgeDocumentSummary(BaseModel):
    id: int
    doc_no: str
    title: str
    version: int
    is_current: bool
    shop_id: int | None
    visibility_scope: VisibilityScope
    visibility_label: str
    allowed_roles: list[str]
    status: KnowledgeStatus
    embedding_model: str | None
    embedding_dim: int | None
    chunk_count: int
    last_error: str | None
    preview_section: str | None = None
    preview_page_label: str | None = None
    preview_excerpt: str | None = None
    indexed_at: datetime | None
    updated_at: datetime


class KnowledgeDocumentDetail(KnowledgeDocumentSummary):
    raw_content: str
    chunks: list[KnowledgeChunkPreview]


class KnowledgeCitationItem(BaseModel):
    document_id: int
    doc_no: str
    title: str
    document_version: int
    chunk_id: int
    chunk_index: int
    section_title: str
    page_label: str
    visibility_scope: VisibilityScope
    visibility_label: str
    excerpt: str
    content: str
    vector_rank: int | None = None
    lexical_rank: int | None = None
    rrf_score: float
    rerank_score: float | None = None


class KnowledgeSearchResponse(BaseModel):
    query: str
    items: list[KnowledgeCitationItem]
    vector_candidates: int
    lexical_candidates: int
    vector_degraded: bool
    rerank_degraded: bool
    embedding_model: str
    rerank_model: str | None
