from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import DEFAULT_DASHSCOPE_EMBEDDING_DIMENSIONS
from app.models.base import Base


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_knowledge_docs_shop_organization",
        ),
        UniqueConstraint("organization_id", "doc_no", "version", name="uq_knowledge_docs_org_no_ver"),
        CheckConstraint(
            "visibility_scope IN ('org_all', 'org_operator', 'shop_operator', 'approver_only', 'admin_only')",
            name="ck_knowledge_docs_scope",
        ),
        CheckConstraint(
            "status IN ('pending', 'indexing', 'indexed', 'failed')",
            name="ck_knowledge_docs_status",
        ),
        CheckConstraint("version >= 1 AND chunk_count >= 0", name="ck_knowledge_docs_version"),
        Index("ix_knowledge_docs_org_status", "organization_id", "status", "is_current"),
    )

    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doc_no: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    visibility_scope: Mapped[str] = mapped_column(String(32), server_default="org_operator")
    allowed_roles_json: Mapped[list[str]] = mapped_column(JSON)
    raw_content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), server_default="pending")
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunks_doc_idx"),
        CheckConstraint("chunk_index >= 0 AND document_version >= 1", name="ck_knowledge_chunks_index"),
        Index("ix_knowledge_chunks_org_shop", "organization_id", "shop_id"),
        Index("ix_knowledge_chunks_tsv", "tsv_content", postgresql_using="gin"),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    organization_id: Mapped[int] = mapped_column(Integer)
    shop_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"))
    document_version: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_title: Mapped[str] = mapped_column(String(200))
    page_label: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    lexical_text: Mapped[str] = mapped_column(Text)
    tsv_content: Mapped[Any] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(DEFAULT_DASHSCOPE_EMBEDDING_DIMENSIONS),
        nullable=True,
    )
