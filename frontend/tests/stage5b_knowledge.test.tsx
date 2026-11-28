import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import AppRouter from "../src/router"

const operatorSession = {
  access_token: "stage5b-token",
  user: { id: 1, username: "ops.demo", name: "沙箱运营" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["operator"] }],
}

const sampleDocs = [
  {
    id: 11,
    doc_no: "DOC-001",
    title: "促销运营规则",
    version: 3,
    is_current: true,
    shop_id: null,
    visibility_scope: "org_operator",
    visibility_label: "组织运营",
    allowed_roles: ["admin", "operator"],
    status: "indexed",
    embedding_model: "qwen3.7-text-embedding-flash",
    embedding_dim: 1024,
    chunk_count: 2,
    last_error: null,
    preview_section: "§4.3 审批后复核",
    preview_page_label: "第 6 页",
    preview_excerpt: "价格与审批快照不一致时，当前版本不得排期。",
    indexed_at: "2026-11-18T02:00:00Z",
    updated_at: "2026-11-18T02:00:00Z",
  },
  {
    id: 15,
    doc_no: "DOC-005",
    title: "换季商品资料审核规则",
    version: 1,
    is_current: true,
    shop_id: null,
    visibility_scope: "org_operator",
    visibility_label: "组织运营",
    allowed_roles: ["admin", "operator"],
    status: "failed",
    embedding_model: "qwen3.7-text-embedding-flash",
    embedding_dim: 1024,
    chunk_count: 1,
    last_error: "初始同步百炼向量超时，请点击「重建索引」完成向量化。",
    preview_section: "§1 换季商品类目与定价审核",
    preview_page_label: "第 1 页",
    preview_excerpt: "换季商品重新上架或调整常规售价前，须核对成本与上期价格。",
    indexed_at: null,
    updated_at: "2026-11-21T03:20:00Z",
  },
]

const doc11Detail = {
  ...sampleDocs[0],
  raw_content: "# §4.3 审批后复核\n[第 6 页]\n价格与审批快照不一致时，当前版本不得排期。",
  chunks: [
    {
      chunk_id: 101,
      chunk_index: 0,
      section_title: "§4.3 审批后复核",
      page_label: "第 6 页",
      content: "审批通过后，应重新读取当前 SKU 状态、售价与可售库存。价格与审批快照不一致时，当前版本不得排期。",
      has_embedding: true,
    },
  ],
}

const doc15Detail = {
  ...sampleDocs[1],
  raw_content: "# §1 换季商品类目与定价审核\n[第 1 页]\n不得低于成本线直接改价。",
  chunks: [
    {
      chunk_id: 201,
      chunk_index: 0,
      section_title: "§1 换季商品类目与定价审核",
      page_label: "第 1 页",
      content: "换季商品重新上架或调整常规售价前，须核对商品成本价与历史售价，不得低于成本线直接改价。",
      has_embedding: false,
    },
  ],
}

describe("阶段 5B 知识库管理与 Agent 规则问答引用", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, "", "/")
  })

  it("renders knowledge documents, previews citations, uploads new doc, and triggers reindex", async () => {
    let reindexed = false
    let uploaded = false

    const createdDoc = {
      id: 16,
      doc_no: "DOC-006",
      title: "冬季调价红线手册",
      version: 1,
      is_current: true,
      shop_id: 19,
      visibility_scope: "shop_operator",
      visibility_label: "店铺运营",
      allowed_roles: ["admin", "operator"],
      status: "indexed",
      embedding_model: "qwen3.7-text-embedding-flash",
      embedding_dim: 1024,
      chunk_count: 1,
      last_error: null,
      preview_section: "§1 调价底线",
      preview_page_label: "第 1 页",
      preview_excerpt: "调价目标价不得低于成本价。",
      indexed_at: "2026-11-21T05:00:00Z",
      updated_at: "2026-11-21T05:00:00Z",
      raw_content: "# §1 调价底线\n[第 1 页]\n调价目标价不得低于成本价。",
      chunks: [
        {
          chunk_id: 301,
          chunk_index: 0,
          section_title: "§1 调价底线",
          page_label: "第 1 页",
          content: "调价目标价不得低于成本价。",
          has_embedding: true,
        },
      ],
    }

    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url
      const method = (init?.method ?? "GET").toUpperCase()

      if (url === "/api/auth/refresh") return Promise.resolve(Response.json(operatorSession))
      if (url === "/api/shops/19/knowledge/documents" && method === "GET") {
        const list = sampleDocs.map(doc =>
          doc.id === 15 && reindexed ? { ...doc, status: "indexed", last_error: null } : doc,
        )
        return Promise.resolve(Response.json(uploaded ? [...list, createdDoc] : list))
      }
      if (url === "/api/shops/19/knowledge/documents/11") return Promise.resolve(Response.json(doc11Detail))
      if (url === "/api/shops/19/knowledge/documents/15" && method === "GET") {
        return Promise.resolve(
          Response.json(reindexed ? { ...doc15Detail, status: "indexed", last_error: null } : doc15Detail),
        )
      }
      if (url === "/api/shops/19/knowledge/documents/15/reindex" && method === "POST") {
        reindexed = true
        return Promise.resolve(
          Response.json({
            ...doc15Detail,
            status: "indexed",
            last_error: null,
            chunks: [{ ...doc15Detail.chunks[0], has_embedding: true }],
          }),
        )
      }
      if (url === "/api/shops/19/knowledge/documents" && method === "POST") {
        uploaded = true
        return Promise.resolve(Response.json(createdDoc))
      }
      return Promise.resolve(Response.json(doc11Detail))
    })

    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/knowledge")
    render(<AppRouter />)

    expect(await screen.findByRole("row", { name: /促销运营规则/ })).toBeInTheDocument()
    expect(screen.getByText("当前版本")).toBeInTheDocument()
    expect(await screen.findByText(/第 6 页 · §4.3 审批后复核/)).toBeInTheDocument()
    expect(screen.getByText("正在索引和索引失败的版本不得成为当前问答的引用来源。")).toBeInTheDocument()

    // 点击 DOC-005 失败行中的重建索引按钮
    const doc5Row = screen.getByRole("row", { name: /换季商品资料审核规则/ })
    fireEvent.click(within(doc5Row).getByRole("button", { name: "重建索引" }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/shops/19/knowledge/documents/15/reindex",
        expect.objectContaining({ method: "POST" }),
      )
    })

    // 打开上传规则文档弹窗并提交
    fireEvent.click(screen.getByRole("button", { name: /上传规则文档/ }))
    fireEvent.change(await screen.findByLabelText("文档标题"), { target: { value: "冬季调价红线手册" } })
    fireEvent.change(screen.getByLabelText("规则正文"), {
      target: { value: "# §1 调价底线\n[第 1 页]\n调价目标价不得低于成本价。" },
    })
    fireEvent.click(screen.getByRole("button", { name: "保存并建立索引" }))

    expect(await screen.findByRole("row", { name: /冬季调价红线手册/ })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/shops/19/knowledge/documents",
      expect.objectContaining({ method: "POST" }),
    )
  })

  it("displays Agent knowledge_qa citations, switches preview card, and shows isolation notice", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(
        Response.json({
          kind: "knowledge_qa",
          message: "不能按原方案排期。价格与审批快照不一致时，当前版本不得排期，须更新方案重新送审。",
          session_id: 91,
          task_id: 301,
          tool_calls: 1,
          sales: null,
          knowledge: {
            citations: [
              {
                document_id: 11,
                doc_no: "DOC-001",
                title: "促销运营规则",
                document_version: 3,
                chunk_id: 101,
                chunk_index: 0,
                section_title: "§4.3 审批后复核",
                page_label: "第 6 页",
                visibility_scope: "org_operator",
                visibility_label: "组织运营",
                excerpt: "价格与审批快照不一致时，当前版本不得排期。",
                content: "审批通过后，应重新读取当前 SKU 状态、售价与可售库存。价格与审批快照不一致时，当前版本不得排期。",
                vector_rank: 1,
                lexical_rank: 1,
                rrf_score: 0.032787,
                rerank_score: 0.96,
              },
              {
                document_id: 12,
                doc_no: "DOC-002",
                title: "促销审批操作规范",
                document_version: 2,
                chunk_id: 102,
                chunk_index: 1,
                section_title: "§2.2 失败后重提",
                page_label: "第 3 页",
                visibility_scope: "shop_operator",
                visibility_label: "店铺运营、审批员",
                excerpt: "运营应在原活动上修订方案生成新版本并重新提交审批。",
                content: "失败后不能直接强制执行旧版本，运营应在原活动上修订方案生成 V2 新版本并重新提交审批。",
                vector_rank: 2,
                lexical_rank: 2,
                rrf_score: 0.032258,
                rerank_score: 0.88,
              },
            ],
            vector_candidates: 4,
            lexical_candidates: 4,
            vector_degraded: false,
            rerank_degraded: false,
            embedding_model: "qwen3.7-text-embedding-flash",
            rerank_model: "qwen3.7-text-rerank",
            isolation_notice: "本回答仅解释运营规则，未查询商品实时价格，未提交审批或变更数据。",
          },
        }),
      )

    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/")
    render(<AppRouter />)

    fireEvent.click(await screen.findByRole("button", { name: "试用规则问答（售价变更复核）" }))
    fireEvent.click(screen.getByRole("button", { name: "发送" }))

    expect(await screen.findByText(/不能按原方案排期/)).toBeInTheDocument()
    expect(screen.getByText("知识库只读问答")).toBeInTheDocument()
    expect(screen.getByText("2 条引用")).toBeInTheDocument()
    expect(screen.getByText("本回答仅解释运营规则，未查询商品实时价格，未提交审批或变更数据。")).toBeInTheDocument()

    // 默认右侧引用预览展示第 1 条，点击第 2 条切换到《促销审批操作规范》
    expect(screen.getByText("促销运营规则 · §4.3 审批后复核")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /《促销审批操作规范》/ }))
    expect(screen.getByText("促销审批操作规范 · §2.2 失败后重提")).toBeInTheDocument()
    expect(screen.getByText(/修订方案生成 V2 新版本/)).toBeInTheDocument()
  })
})
