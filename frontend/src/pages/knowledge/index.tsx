import { FileTextOutlined, InfoCircleOutlined, PlusOutlined, SearchOutlined } from "@ant-design/icons"
import { useQueryClient } from "@tanstack/react-query"
import { Alert, App as AntApp, Button, Checkbox, Input, Modal, Select, Spin, Table } from "antd"
import type { TableColumnsType } from "antd"
import { useMemo, useState, type FormEvent } from "react"
import { Link } from "react-router-dom"

import { ApiError } from "../../api/client"
import {
  createKnowledgeDocumentRequest,
  reindexKnowledgeDocumentRequest,
  useKnowledgeDocumentDetailQuery,
  useKnowledgeDocumentsQuery,
  type KnowledgeDocumentSummary,
  type KnowledgeStatus,
  type VisibilityScope,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import styles from "./index.module.css"

type KnowledgePageProps = ShopReadProps & {
  shopName: string
  shopRoles: string[]
}

const statusMeta: Record<KnowledgeStatus, { label: string; className: string }> = {
  indexed: { label: "已索引", className: styles.pillIndexed },
  indexing: { label: "索引中", className: styles.pillIndexing },
  failed: { label: "索引失败", className: styles.pillFailed },
  pending: { label: "待索引", className: styles.pillPending },
}

const scopeOptions: Array<{ value: VisibilityScope; label: string }> = [
  { value: "org_operator", label: "组织运营" },
  { value: "shop_operator", label: "当前店铺运营" },
  { value: "org_all", label: "全组织（运营 / 审批 / 管理）" },
  { value: "approver_only", label: "仅审批员与管理员" },
  { value: "admin_only", label: "仅管理员" },
]

export default function KnowledgePage({ shopRoles, ...props }: KnowledgePageProps) {
  const { message: toast } = AntApp.useApp()
  const queryClient = useQueryClient()
  const canManageKnowledge = shopRoles.includes("operator") || shopRoles.includes("admin")

  const [keyword, setKeyword] = useState("")
  const [statusFilter, setStatusFilter] = useState<string>("all")
  const [selectedDocId, setSelectedDocId] = useState<number | null>(null)
  const [selectedChunkIdx, setSelectedChunkIdx] = useState(0)
  const [reindexingId, setReindexingId] = useState<number | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const [docNo, setDocNo] = useState("")
  const [title, setTitle] = useState("")
  const [scope, setScope] = useState<VisibilityScope>("org_operator")
  const [shopOnly, setShopOnly] = useState(false)
  const [rawContent, setRawContent] = useState("")

  const listQuery = useKnowledgeDocumentsQuery(props, "", "all")
  const docs = useMemo(() => listQuery.data ?? [], [listQuery.data])

  const filteredDocs = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    return docs.filter((item) => {
      if (statusFilter !== "all" && item.status !== statusFilter) return false
      if (q && !item.title.toLowerCase().includes(q) && !item.doc_no.toLowerCase().includes(q)) {
        return false
      }
      return true
    })
  }, [docs, keyword, statusFilter])

  const activeDoc = useMemo(() => {
    if (selectedDocId !== null) {
      const matched = docs.find(item => item.id === selectedDocId)
      if (matched) return matched
    }
    return filteredDocs[0] ?? docs[0] ?? null
  }, [docs, filteredDocs, selectedDocId])

  const detailQuery = useKnowledgeDocumentDetailQuery(props, activeDoc?.id ?? null, activeDoc !== null)
  const detail = detailQuery.isSuccess && detailQuery.data?.id === activeDoc?.id ? detailQuery.data : null
  const chunks = detail?.chunks ?? []
  const previewChunk = chunks[selectedChunkIdx] ?? chunks[0] ?? null

  async function handleReindex(doc: KnowledgeDocumentSummary) {
    setReindexingId(doc.id)
    try {
      const updated = await reindexKnowledgeDocumentRequest(props, doc.id)
      setSelectedDocId(updated.id)
      setSelectedChunkIdx(0)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "knowledge"] })
      if (updated.status === "indexed") {
        toast?.success?.(`${updated.doc_no} 已完成重建索引`)
      } else {
        toast?.warning?.(updated.last_error || "索引重建未能完成，请稍后重试")
      }
    } catch (error) {
      toast?.error?.(error instanceof ApiError ? error.message : "重建索引失败，请稍后重试。")
    } finally {
      setReindexingId(null)
    }
  }

  async function handleUploadSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (title.trim().length < 2) {
      setFormError("请输入至少 2 个字的规则文档标题。")
      return
    }
    if (rawContent.trim().length < 10) {
      setFormError("请输入至少 10 个字的规则正文内容。")
      return
    }
    setFormError(null)
    setSubmitting(true)
    try {
      const created = await createKnowledgeDocumentRequest(props, {
        ...(docNo.trim() ? { doc_no: docNo.trim().toUpperCase() } : {}),
        title: title.trim(),
        visibility_scope: scope,
        shop_only: shopOnly,
        raw_content: rawContent.trim(),
      })
      setModalOpen(false)
      setDocNo("")
      setTitle("")
      setRawContent("")
      setShopOnly(false)
      setSelectedDocId(created.id)
      setSelectedChunkIdx(0)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "knowledge"] })
      toast?.success?.(
        created.status === "indexed"
          ? `已发布 ${created.doc_no} V${created.version} 并完成向量化索引`
          : `已保存 ${created.doc_no} V${created.version}（向量化失败，可稍后重建索引）`,
      )
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : "上传规则文档失败，请稍后重试。")
    } finally {
      setSubmitting(false)
    }
  }

  const columns: TableColumnsType<KnowledgeDocumentSummary> = [
    {
      title: "文档 / 版本",
      key: "doc",
      render: (_, record) => (
        <div className={styles.docCell}>
          <span className={styles.docIconBadge} aria-hidden="true"><FileTextOutlined /></span>
          <div>
            <div className={styles.docTitle}>{record.title}</div>
            <div className={styles.docMeta}>{record.doc_no} · V{record.version}</div>
          </div>
        </div>
      ),
    },
    {
      title: "可见范围",
      dataIndex: "visibility_label",
      key: "visibility_label",
    },
    {
      title: "索引状态",
      key: "status",
      render: (_, record) => {
        const meta = statusMeta[record.status]
        return <span className={`${styles.pill} ${meta.className}`}>{meta.label}</span>
      },
    },
    {
      title: "更新日期",
      key: "updated_at",
      render: (_, record) => record.updated_at.slice(0, 10),
    },
    {
      title: "操作",
      key: "action",
      align: "right",
      render: (_, record) => {
        const isActive = activeDoc?.id === record.id
        if (record.status === "failed") {
          return (
            <button
              type="button"
              className={`${styles.actionPill} ${isActive ? styles.actionPillActive : ""}`}
              disabled={!canManageKnowledge || reindexingId === record.id}
              onClick={() => {
                if (canManageKnowledge) {
                  void handleReindex(record)
                } else {
                  setSelectedDocId(record.id)
                  setSelectedChunkIdx(0)
                }
              }}
            >
              {reindexingId === record.id ? "重建中…" : "重建索引"}
            </button>
          )
        }
        if (record.status === "indexing") {
          return (
            <button
              type="button"
              className={`${styles.actionPill} ${isActive ? styles.actionPillActive : ""}`}
              onClick={() => {
                setSelectedDocId(record.id)
                setSelectedChunkIdx(0)
              }}
            >
              索引进度
            </button>
          )
        }
        return (
          <button
            type="button"
            className={`${styles.actionPill} ${isActive ? styles.actionPillActive : ""}`}
            onClick={() => {
              setSelectedDocId(record.id)
              setSelectedChunkIdx(0)
            }}
          >
            预览引用
          </button>
        )
      },
    },
  ]

  const agentPrompt = activeDoc
    ? encodeURIComponent(
        activeDoc.doc_no === "DOC-003"
          ? "客户说第一次没收到，订单已有一笔待处理退款，可以重新发起吗？"
          : "审批通过之后，如果 SKU 的售价变了，系统还能按原方案排期吗？",
      )
    : ""

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <span>运营工作台</span>
        <span aria-hidden="true">/</span>
        <span>系统管理</span>
        <span aria-hidden="true">/</span>
        <strong aria-current="page">知识库管理</strong>
      </nav>

      <div className={styles.headerBar}>
        <div>
          <h1>知识库管理</h1>
          <p className={styles.summary}>管理运营规则文档、版本、可见范围及索引状态。</p>
        </div>
        {canManageKnowledge && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            上传规则文档
          </Button>
        )}
      </div>

      {listQuery.isError && (
        <Alert
          type={listQuery.error instanceof ApiError && listQuery.error.status === 401 ? "warning" : "error"}
          showIcon
          style={{ marginBottom: 16 }}
          message={
            listQuery.error instanceof ApiError && listQuery.error.status === 401
              ? "登录状态已失效"
              : "知识库文档加载失败"
          }
          action={
            listQuery.error instanceof ApiError && listQuery.error.status === 401 ? (
              <Button onClick={props.onSignIn}>重新登录</Button>
            ) : (
              <Button onClick={() => { void listQuery.refetch() }}>重试</Button>
            )
          }
        />
      )}

      <div className={styles.layoutGrid}>
        <section className={styles.panel} aria-label="知识库文档列表">
          <div className={styles.toolbar}>
            <Input
              className={styles.searchInput}
              aria-label="搜索文档名称"
              placeholder="搜索文档名称"
              prefix={<SearchOutlined />}
              allowClear
              value={keyword}
              onChange={event => setKeyword(event.target.value)}
            />
            <Select
              className={styles.statusSelect}
              aria-label="索引状态筛选"
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { value: "all", label: "全部索引状态" },
                { value: "indexed", label: "已索引" },
                { value: "indexing", label: "索引中" },
                { value: "failed", label: "索引失败" },
              ]}
            />
            <span className={styles.tableCount}>共 {filteredDocs.length} 条记录</span>
          </div>

          <div className={styles.table}>
            <Table<KnowledgeDocumentSummary>
              rowKey="id"
              columns={columns}
              dataSource={filteredDocs}
              loading={listQuery.isLoading}
              pagination={false}
              locale={{ emptyText: "当前筛选条件下暂无规则文档" }}
            />
          </div>

          <div className={styles.footerNote}>共 {filteredDocs.length} 条记录 · 仅已索引且当前版本规则参与混合检索</div>
        </section>

        <div className={styles.sideColumn}>
          <section className={styles.sideCard} aria-labelledby="knowledge-preview-heading">
            <header className={styles.sideHeader}>
              <h2 id="knowledge-preview-heading">引用预览</h2>
              <span className={`${styles.pill} ${styles.pillIndexed}`}>
                {activeDoc?.is_current ? "当前版本" : activeDoc ? `V${activeDoc.version}` : "待选择"}
              </span>
            </header>
            <div className={styles.sideBody}>
              {activeDoc ? (
                <>
                  <div className={styles.previewDocHead}>
                    <span className={styles.docIconBadge} aria-hidden="true"><FileTextOutlined /></span>
                    <div>
                      <div className={styles.docTitle}>{activeDoc.title}</div>
                      <div className={styles.docMeta}>
                        {activeDoc.doc_no} · V{activeDoc.version} · {statusMeta[activeDoc.status].label}
                      </div>
                    </div>
                  </div>

                  {detailQuery.isLoading && <Spin size="small" />}

                  <dl className={styles.metaList}>
                    <div>
                      <dt>引用位置</dt>
                      <dd>
                        {previewChunk
                          ? `${previewChunk.page_label} · ${previewChunk.section_title}`
                          : activeDoc.preview_page_label
                            ? `${activeDoc.preview_page_label} · ${activeDoc.preview_section ?? ""}`
                            : "尚未完成切片"}
                      </dd>
                    </div>
                    <div>
                      <dt>可见范围</dt>
                      <dd>{activeDoc.visibility_label}</dd>
                    </div>
                    <div>
                      <dt>索引状态</dt>
                      <dd>
                        {activeDoc.status === "indexed"
                          ? activeDoc.embedding_model
                            ? `关键词与向量索引可用（${activeDoc.embedding_model} · ${activeDoc.embedding_dim ?? 1024} 维）`
                            : "关键词词法索引可用（待补充百炼向量）"
                          : statusMeta[activeDoc.status].label}
                      </dd>
                    </div>
                  </dl>

                  {chunks.length > 1 && (
                    <div className={styles.chunkSwitch} role="group" aria-label="文档分片切换">
                      {chunks.map((chunk, idx) => (
                        <button
                          key={chunk.chunk_id}
                          type="button"
                          className={`${styles.chunkChip} ${idx === selectedChunkIdx ? styles.chunkChipActive : ""}`}
                          onClick={() => setSelectedChunkIdx(idx)}
                        >
                          {chunk.page_label}
                        </button>
                      ))}
                    </div>
                  )}

                  {(previewChunk?.content ?? activeDoc.preview_excerpt) && (
                    <div className={styles.excerptBox}>
                      {previewChunk?.content ?? activeDoc.preview_excerpt}
                    </div>
                  )}

                  {activeDoc.last_error && (
                    <div className={styles.errorBox}>{activeDoc.last_error}</div>
                  )}

                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <Link className={styles.actionPill} to={agentPrompt ? `/?q=${agentPrompt}` : "/"}>
                      查看 Agent 回答
                    </Link>
                    {canManageKnowledge && activeDoc.status !== "indexing" && (
                      <Button
                        size="small"
                        loading={reindexingId === activeDoc.id}
                        onClick={() => { void handleReindex(activeDoc) }}
                      >
                        重建索引
                      </Button>
                    )}
                  </div>
                </>
              ) : (
                <p className={styles.summary}>请从左侧文档列表选择一条规则查看可追溯引用片段。</p>
              )}
            </div>
          </section>

          <section className={styles.sideCard} aria-labelledby="knowledge-tip-heading">
            <header className={styles.sideHeader}>
              <h2 id="knowledge-tip-heading">索引提示</h2>
            </header>
            <div className={styles.sideBody}>
              <div className={styles.tipRow}>
                <InfoCircleOutlined style={{ marginTop: 2, flexShrink: 0 }} aria-hidden="true" />
                <span>正在索引和索引失败的版本不得成为当前问答的引用来源。</span>
              </div>
            </div>
          </section>
        </div>
      </div>

      <Modal
        title="上传规则文档"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
        destroyOnHidden
        width={620}
      >
        <form onSubmit={handleUploadSubmit} className={styles.formGrid}>
          {formError && (
            <div className={styles.fullSpan}>
              <Alert type="error" showIcon message={formError} />
            </div>
          )}
          <label className={styles.fieldLabel}>
            文档标题
            <Input
              aria-label="文档标题"
              value={title}
              onChange={event => setTitle(event.target.value)}
              placeholder="例如：促销运营规则"
              maxLength={200}
            />
          </label>
          <label className={styles.fieldLabel}>
            文档编号（可选，留空自动生成）
            <Input
              aria-label="文档编号"
              value={docNo}
              onChange={event => setDocNo(event.target.value)}
              placeholder="如填写 DOC-001 将发布递增版本"
              maxLength={64}
            />
          </label>
          <label className={styles.fieldLabel}>
            可见范围
            <Select<VisibilityScope>
              aria-label="可见范围"
              value={scope}
              onChange={setScope}
              options={scopeOptions}
            />
          </label>
          <div className={styles.fieldLabel} style={{ justifyContent: "flex-end" }}>
            <Checkbox checked={shopOnly} onChange={event => setShopOnly(event.target.checked)}>
              仅限当前授权店铺可见
            </Checkbox>
          </div>
          <label className={`${styles.fieldLabel} ${styles.fullSpan}`}>
            规则正文（支持 Markdown 标题与 [第 N 页] 标记）
            <Input.TextArea
              aria-label="规则正文"
              rows={7}
              value={rawContent}
              onChange={event => setRawContent(event.target.value)}
              placeholder={"# §1.1 规则章节标题\n[第 1 页]\n输入清晰的运营、促销、调价或售后规则条款…"}
            />
          </label>
          <div className={`${styles.fullSpan} ${styles.modalActions}`}>
            <Button onClick={() => setModalOpen(false)}>取消</Button>
            <Button type="primary" htmlType="submit" loading={submitting}>
              保存并建立索引
            </Button>
          </div>
        </form>
      </Modal>
    </main>
  )
}
