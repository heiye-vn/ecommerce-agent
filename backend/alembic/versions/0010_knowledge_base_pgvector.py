"""Add knowledge_documents and knowledge_chunks with pgvector support."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0010_knowledge_base_pgvector"
down_revision = "0009_price_changes_and_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=True),
        sa.Column("doc_no", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("visibility_scope", sa.String(32), server_default="org_operator", nullable=False),
        sa.Column("allowed_roles_json", sa.JSON(), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            name="fk_knowledge_docs_shop_organization",
        ),
        sa.UniqueConstraint("organization_id", "doc_no", "version", name="uq_knowledge_docs_org_no_ver"),
        sa.CheckConstraint(
            "visibility_scope IN ('org_all', 'org_operator', 'shop_operator', 'approver_only', 'admin_only')",
            name="ck_knowledge_docs_scope",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'indexing', 'indexed', 'failed')",
            name="ck_knowledge_docs_status",
        ),
        sa.CheckConstraint("version >= 1 AND chunk_count >= 0", name="ck_knowledge_docs_version"),
    )
    op.create_index(
        "ix_knowledge_docs_org_status",
        "knowledge_documents",
        ["organization_id", "status", "is_current"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.String(200), nullable=False),
        sa.Column("page_label", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("lexical_text", sa.Text(), nullable=False),
        sa.Column("tsv_content", TSVECTOR(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunks_doc_idx"),
        sa.CheckConstraint("chunk_index >= 0 AND document_version >= 1", name="ck_knowledge_chunks_index"),
    )
    op.create_index("ix_knowledge_chunks_org_shop", "knowledge_chunks", ["organization_id", "shop_id"])
    op.create_index("ix_knowledge_chunks_tsv", "knowledge_chunks", ["tsv_content"], postgresql_using="gin")
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_tsv", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_org_shop", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.drop_index("ix_knowledge_docs_org_status", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.execute("DROP EXTENSION IF EXISTS vector")
