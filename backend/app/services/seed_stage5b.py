from datetime import datetime
from typing import cast

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.rag.chunker import ChunkDraft, chunk_markdown_document
from app.agent.rag.embeddings import EmbeddingClient, EmbeddingUnavailableError
from app.models import KnowledgeChunk, KnowledgeDocument, Organization, Role, User
from app.services.seed import DEMO_ORGANIZATION, DEMO_USERNAME

STAGE5B_DOCUMENTS = [
    {
        "doc_no": "DOC-001",
        "title": "促销运营规则",
        "version": 3,
        "visibility_scope": "org_operator",
        "allowed_roles": [Role.ADMIN.value, Role.OPERATOR.value],
        "status": "indexed",
        "is_current": True,
        "updated_at": datetime.fromisoformat("2026-11-18T10:00:00+08:00"),
        "last_error": None,
        "raw_content": (
            "# §1.1 促销活动门槛与定价口径\n[第 1 页]\n"
            "单 SKU 发起折扣促销时，按当前在售售价乘以折扣计算活动价，系统保留两位小数。"
            "若活动价低于商品成本价，预览阶段必须显式给出低于成本风险提示，由运营确认后才可送审。\n\n"
            "# §4.3 审批后复核\n[第 6 页]\n"
            "审批通过后，应重新读取当前 SKU 状态、售价、可售库存和已占用活动时段。"
            "如果 SKU 的售价变了或可售库存低于 100 件门槛或存在时段冲突，价格与审批快照不一致时，当前版本不得排期。\n"
            "运营须更新同一活动的方案（递增版本号如 V2）并重新提交审批，旧审批记录会保留以保障全程可追溯。"
        ),
    },
    {
        "doc_no": "DOC-002",
        "title": "促销审批操作规范",
        "version": 2,
        "visibility_scope": "shop_operator",
        "allowed_roles": [Role.ADMIN.value, Role.OPERATOR.value, Role.APPROVER.value],
        "status": "indexed",
        "is_current": True,
        "updated_at": datetime.fromisoformat("2026-11-16T15:30:00+08:00"),
        "last_error": None,
        "raw_content": (
            "# §1.1 审批角色与自审隔离\n[第 1 页]\n"
            "高风险运营操作（促销排期、商品调价、退款）必须经人工审批通过后方可生效。"
            "同一账号禁止自审自己发起的申请，也不允许跨店铺、跨组织越权审批。\n\n"
            "# §2.2 失败后重提\n[第 3 页]\n"
            "当促销因业务复核失败、被审批员驳回或申请人撤回后，不能直接强制执行旧版本。"
            "运营应在原活动上修订方案生成新版本并重新提交审批；若为异步投递临时系统故障进入死信队列，管理员可按原操作幂等重试，不会重复建单。"
        ),
    },
    {
        "doc_no": "DOC-003",
        "title": "订单退款处理规范",
        "version": 2,
        "visibility_scope": "shop_operator",
        "allowed_roles": [Role.ADMIN.value, Role.OPERATOR.value, Role.APPROVER.value],
        "status": "indexed",
        "is_current": True,
        "updated_at": datetime.fromisoformat("2026-11-16T18:00:00+08:00"),
        "last_error": None,
        "raw_content": (
            "# §1 退款资格与余额复核\n[第 2 页]\n"
            "仅已支付订单可发起部分或全额退款，拟退款金额不得超过订单剩余可退余额（实付金额减去已成功退款及在途退款）。\n\n"
            "# §3 重复退款与恢复\n[第 4 页]\n"
            "对同一退款申请应跟进原单，不得对同一笔订单重复发起新的在途退款。"
            "若原申请仍在审批或处理中，应追踪原单状态；如果执行失败，需要确认没有已成功退款，再按原操作恢复，并在执行前二次检查支付状态与可退余额。"
        ),
    },
    {
        "doc_no": "DOC-004",
        "title": "库存预警与补货指引",
        "version": 1,
        "visibility_scope": "org_operator",
        "allowed_roles": [Role.ADMIN.value, Role.OPERATOR.value],
        "status": "indexing",
        "is_current": True,
        "updated_at": datetime.fromisoformat("2026-11-21T09:15:00+08:00"),
        "last_error": None,
        "raw_content": (
            "# §1 低库存预警阈值\n[第 1 页]\n"
            "当 SKU 可售库存低于 20 件时触发低库存预警；所有库存变更须经流水台账与乐观锁版本校验。"
        ),
    },
    {
        "doc_no": "DOC-005",
        "title": "换季商品资料审核规则",
        "version": 1,
        "visibility_scope": "org_operator",
        "allowed_roles": [Role.ADMIN.value, Role.OPERATOR.value],
        "status": "failed",
        "is_current": True,
        "updated_at": datetime.fromisoformat("2026-11-21T11:20:00+08:00"),
        "last_error": "初始同步百炼向量超时，请点击「重建索引」完成向量化。",
        "raw_content": (
            "# §1 换季商品类目与定价审核\n[第 1 页]\n"
            "换季商品重新上架或调整常规售价前，须核对商品成本价、主图规格与上期价格记录，不得低于成本线直接改价。"
        ),
    },
]


async def seed_stage5b_sandbox(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    embedding_client: EmbeddingClient | None = None,
) -> dict[str, bool]:
    """幂等初始化阶段 5B 知识库 5 份沙箱规则文档（DOC-001 ~ DOC-005）。"""
    # 1. 短只读会话确认组织与已存在的文档编号，避免事务内等待外部 Embedding HTTP
    async with session_factory() as read_session:
        org = await read_session.scalar(
            select(Organization).where(Organization.name == DEMO_ORGANIZATION, Organization.status == "active")
        )
        if org is None:
            raise ValueError("Demo organization not found; run python -m scripts.seed first")
        demo_user = await read_session.scalar(select(User).where(User.username == DEMO_USERNAME))
        existing_nos = set(
            (
                await read_session.scalars(
                    select(KnowledgeDocument.doc_no).where(KnowledgeDocument.organization_id == org.id)
                )
            ).all()
        )
        org_id = org.id
        actor_id = demo_user.id if demo_user is not None else None

    result: dict[str, bool] = {str(doc["doc_no"]): False for doc in STAGE5B_DOCUMENTS}
    missing_docs = [doc for doc in STAGE5B_DOCUMENTS if doc["doc_no"] not in existing_nos]
    if not missing_docs:
        return result

    # 2. 在事务外预先切片并为 status == 'indexed' 文档请求百炼 1024 维向量
    prepared: list[
        tuple[dict[str, object], list[ChunkDraft], list[list[float]] | None, str | None, int | None]
    ] = []
    for doc_spec in missing_docs:
        raw_content = cast(str, doc_spec["raw_content"])
        title = cast(str, doc_spec["title"])
        status = cast(str, doc_spec["status"])
        drafts = chunk_markdown_document(raw_content, default_title=title)
        vectors: list[list[float]] | None = None
        embed_model: str | None = None
        embed_dim: int | None = None
        if status == "indexed" and embedding_client is not None:
            try:
                vectors = await embedding_client.embed_texts(
                    [f"{d.section_title}\n{d.content}" for d in drafts]
                )
                embed_model = embedding_client.model_name
                embed_dim = embedding_client.dimensions
            except EmbeddingUnavailableError as exc:
                print(
                    f"[seed_stage5b] {doc_spec['doc_no']} 未写入真实向量（保留词法索引，后续可重建索引）：{exc}"
                )
        prepared.append((doc_spec, drafts, vectors, embed_model, embed_dim))

    # 3. 事务内带咨询锁幂等落库
    async with session_factory.begin() as write_session:
        await write_session.execute(text("SELECT pg_advisory_xact_lock(726601)"))
        current_existing = set(
            (
                await write_session.scalars(
                    select(KnowledgeDocument.doc_no).where(KnowledgeDocument.organization_id == org_id)
                )
            ).all()
        )
        for doc_spec, drafts, vectors, embed_model, embed_dim in prepared:
            doc_no = cast(str, doc_spec["doc_no"])
            if doc_no in current_existing:
                continue
            status = cast(str, doc_spec["status"])
            updated_at = cast(datetime, doc_spec["updated_at"])
            doc = KnowledgeDocument(
                organization_id=org_id,
                shop_id=None,
                doc_no=doc_no,
                title=cast(str, doc_spec["title"]),
                version=cast(int, doc_spec["version"]),
                is_current=cast(bool, doc_spec["is_current"]),
                visibility_scope=cast(str, doc_spec["visibility_scope"]),
                allowed_roles_json=cast(list[str], doc_spec["allowed_roles"]),
                raw_content=cast(str, doc_spec["raw_content"]),
                status=status,
                embedding_model=embed_model,
                embedding_dim=embed_dim,
                chunk_count=len(drafts),
                last_error=cast(str | None, doc_spec["last_error"]),
                created_by=actor_id,
                indexed_at=updated_at if status == "indexed" else None,
                updated_at=updated_at,
                created_at=updated_at,
            )
            write_session.add(doc)
            await write_session.flush()

            write_session.add_all(
                [
                    KnowledgeChunk(
                        organization_id=org_id,
                        shop_id=None,
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
                    for idx, draft in enumerate(drafts)
                ]
            )
            result[doc_no] = True

    return result
