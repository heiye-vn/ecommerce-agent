import { SearchOutlined } from "@ant-design/icons"
import { useQueryClient } from "@tanstack/react-query"
import { Alert, App as AntApp, Button, Input, Spin, Table } from "antd"
import type { TableColumnsType } from "antd"
import { useState } from "react"
import { Link, useParams } from "react-router-dom"

import { ApiError } from "../../api/client"
import {
  approveApprovalRequest,
  rejectApprovalRequest,
  useApprovalDetailQuery,
  useApprovalsQuery,
  type ApprovalScope,
  type ApprovalStatus,
  type ApprovalSummary,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import styles from "./index.module.css"

type PageProps = ShopReadProps & {
  shopRoles: string[]
  userName: string
}

const statusLabels: Record<ApprovalStatus, string> = {
  pending: "待审批",
  approved: "已批准",
  rejected: "已驳回",
  cancelled: "已撤回",
}

const actionLabels: Record<string, string> = {
  promotion_publish: "促销发布",
  refund_create: "订单退款",
  refund_execute: "订单退款",
  price_change: "商品调价",
}

const dateTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
})

function StatusPill({ status }: { status: ApprovalStatus }) {
  const tone =
    status === "approved"
      ? styles.pillMint
      : status === "pending"
        ? styles.pillPending
        : status === "rejected"
          ? styles.pillDanger
          : ""
  return <span className={`${styles.pill} ${tone}`}>{statusLabels[status] ?? status}</span>
}

function Feedback({
  error,
  loading,
  onRetry,
  onSignIn,
}: {
  error: unknown
  loading: boolean
  onRetry: () => void
  onSignIn: () => void
}) {
  if (loading) return <span role="status"><Spin size="small" /> 正在加载审批数据</span>
  const status = error instanceof ApiError ? error.status : undefined
  if (status === 401) {
    return <Alert showIcon type="warning" message="登录状态已失效" action={<Button onClick={onSignIn}>重新登录</Button>} />
  }
  if (status === 403) {
    return <Alert showIcon type="warning" message="无权查看该审批单" description={error instanceof ApiError ? error.message : "仅限当前授权店铺访问。"} />
  }
  if (status === 404) {
    return <Alert showIcon type="warning" message="当前店铺没有找到该审批单" />
  }
  return <Alert showIcon type="error" message="审批数据加载失败" action={<Button onClick={onRetry}>重试</Button>} />
}

function ApprovalQueue(props: PageProps) {
  const isApprover = props.shopRoles.includes("approver")
  const [scope, setScope] = useState<ApprovalScope>(isApprover ? "pending_for_me" : "requested_by_me")
  const [search, setSearch] = useState("")
  const query = useApprovalsQuery(props, scope)
  const ready = query.isSuccess
  const rows = ready ? query.data : []
  const term = search.trim().toLowerCase()
  const filtered = rows.filter(row => (
    row.approval_no.toLowerCase().includes(term)
    || row.resource_no.toLowerCase().includes(term)
    || row.resource_name.toLowerCase().includes(term)
    || (row.sku_code ?? "").toLowerCase().includes(term)
  ))

  const columns: TableColumnsType<ApprovalSummary> = [
    {
      title: "审批单 / 操作",
      width: "22%",
      dataIndex: "approval_no",
      render: (no: string, row) => (
        <div className={styles.cellStack}>
          <strong>{no}</strong>
          <span>{actionLabels[row.action_type] ?? row.action_type}</span>
        </div>
      ),
    },
    {
      title: "业务对象",
      width: "24%",
      dataIndex: "resource_name",
      render: (name: string, row) => (
        <div className={styles.cellStack}>
          <strong>{name || row.resource_no}</strong>
          <span>{row.sku_code ? `${row.sku_code} · ` : ""}V{row.resource_version} · {row.resource_no}</span>
        </div>
      ),
    },
    { title: "申请人", width: "12%", dataIndex: "requester_name" },
    {
      title: "快照金额",
      width: "12%",
      dataIndex: "snapshot_amount",
      align: "right",
      className: styles.number,
      render: (amount: string | null) => amount ? `¥${amount}` : "—",
    },
    {
      title: "提交时间",
      width: "12%",
      dataIndex: "created_at",
      className: styles.number,
      render: (val: string) => dateTime.format(new Date(val)),
    },
    {
      title: "状态",
      width: "9%",
      dataIndex: "status",
      render: (status: ApprovalStatus) => <StatusPill status={status} />,
    },
    {
      title: "操作",
      width: "13%",
      align: "right",
      render: (_, row) => (
        <Link
          className={row.can_decide ? styles.primaryLinkBtn : styles.outlineLinkBtn}
          to={`/approvals/${row.id}`}
        >
          {row.can_decide ? "审阅快照" : "查看快照"}
        </Link>
      ),
    },
  ]

  const scopeTabs: { key: ApprovalScope; label: string }[] = [
    { key: "pending_for_me", label: "待我审批" },
    { key: "processed_by_me", label: "我已处理" },
    { key: "requested_by_me", label: "我发起的" },
    { key: "all", label: "本店全部" },
  ]

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span><strong aria-current="page">审批中心</strong>
      </nav>
      <div className={styles.headerBar}>
        <div>
          <h1>审批中心</h1>
          <p className={styles.summary}>复核高风险操作的固定参数版本。仅可处理当前店铺且非本人发起的审批单。</p>
        </div>
        {isApprover && scope === "pending_for_me" && ready && (
          <span className={`${styles.pill} ${styles.pillMint}`}>{rows.length} 单待我处理</span>
        )}
      </div>

      <div className={styles.tabs} role="tablist" aria-label="审批视图切换">
        {scopeTabs.map(tab => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={scope === tab.key}
            className={`${styles.tabButton} ${scope === tab.key ? styles.tabActive : ""}`}
            onClick={() => setScope(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <section className={styles.panel} aria-label="审批队列">
        <div className={styles.toolbar}>
          <Input
            className={styles.search}
            type="search"
            prefix={<SearchOutlined aria-hidden="true" />}
            aria-label="搜索审批单、资源编号或 SKU"
            placeholder="搜索审批单、资源编号或 SKU"
            value={search}
            disabled={!ready}
            onChange={e => setSearch(e.target.value)}
          />
          <span className={styles.identityHint}>当前身份：{props.userName}</span>
        </div>

        <Table<ApprovalSummary>
          className={styles.table}
          rowKey="id"
          columns={columns}
          tableLayout="fixed"
          pagination={false}
          dataSource={filtered}
          locale={{
            emptyText: ready
              ? scope === "pending_for_me" && !isApprover
                ? "当前账号在本店无审批权限，可切换至「我发起的」或「本店全部」查看审批进度。"
                : "当前视图下暂无审批记录。"
              : <Feedback error={query.error} loading={query.isPending || query.isRefetching} onRetry={() => { void query.refetch() }} onSignIn={props.onSignIn} />,
          }}
        />
        {ready && (
          <footer className={styles.footer}>
            <span aria-live="polite">共 {filtered.length} 条记录</span>
          </footer>
        )}
      </section>
    </main>
  )
}

function ApprovalDetailView({ approvalId, ...props }: PageProps & { approvalId: string }) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const valid = /^[1-9]\d{0,9}$/.test(approvalId) && Number(approvalId) <= 2_147_483_647
  const query = useApprovalDetailQuery(props, approvalId, valid)
  const detail = query.isSuccess ? query.data : undefined

  const [comment, setComment] = useState("")
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(null)
  const [resultBanner, setResultBanner] = useState<{
    type: "success" | "warning" | "error"
    text: string
    jobId?: number | null
  } | null>(null)

  async function handleDecision(action: "approve" | "reject") {
    if (!detail) return
    setResultBanner(null)
    if (action === "reject" && !comment.trim()) {
      setResultBanner({ type: "warning", text: "驳回审批时必须填写处理意见。" })
      return
    }
    setSubmitting(action)
    try {
      const response =
        action === "approve"
          ? await approveApprovalRequest(props, detail.id, comment.trim() || undefined)
          : await rejectApprovalRequest(props, detail.id, comment.trim())
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "promotions"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "refunds"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "orders"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "jobs"] })
      if (response.execution_status === "queued") {
        setResultBanner({
          type: "success",
          text: response.execution_message || "审批已通过，后台执行任务已入队。",
          jobId: response.job_id,
        })
        message?.success?.("审批已通过，后台执行任务已入队")
      } else if (response.execution_status === "scheduled" || response.execution_status === "succeeded") {
        setResultBanner({ type: "success", text: response.execution_message, jobId: response.job_id })
        message?.success?.("审批通过，业务复核并执行成功")
      } else if (response.execution_status === "failed") {
        setResultBanner({ type: "warning", text: response.execution_message, jobId: response.job_id })
        message?.warning?.("审批已通过，但实时业务复核未通过")
      } else {
        setResultBanner({ type: "success", text: response.execution_message })
        message?.success?.("已驳回该审批单")
      }
    } catch (error) {
      setResultBanner({
        type: "error",
        text: error instanceof ApiError ? error.message : "审批操作失败，请稍后重试。",
      })
    } finally {
      setSubmitting(null)
    }
  }

  const isRefund = detail?.resource_type === "refund"
  const snap = detail?.payload_snapshot ?? {}
  const startsAt = typeof snap.starts_at === "string" ? snap.starts_at : ""
  const endsAt = typeof snap.ends_at === "string" ? snap.ends_at : ""
  const riskWarnings = Array.isArray(snap.risk_warnings) ? (snap.risk_warnings as string[]) : []
  const sales7d = (snap.sales_7d ?? null) as { units_sold?: number; start_date?: string; end_date?: string } | null
  const resourceLink = detail
    ? isRefund
      ? { to: `/orders/refunds/${detail.resource_id}`, label: "查看退款详情" }
      : { to: `/promotions/${detail.resource_id}`, label: "查看活动详情" }
    : null

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span>
        <Link to="/approvals">审批中心</Link><span aria-hidden="true">/</span>
        <strong aria-current="page">{isRefund ? "审阅退款申请" : "审阅促销方案"}</strong>
      </nav>
      <div className={styles.headerBar}>
        <div>
          <h1>{isRefund ? "审阅退款申请" : "审阅促销方案"}</h1>
          <p className={styles.summary}>
            {detail ? `${detail.approval_no} · ${detail.resource_name} · ${detail.resource_no}` : "核对不可变审批参数快照与业务依据。"}
          </p>
        </div>
        <div className={styles.headerActions}>
          {detail && <StatusPill status={detail.status} />}
          <Link className={styles.outlineLinkBtn} to="/approvals">返回审批队列</Link>
        </div>
      </div>

      {!valid || !detail ? (
        <div className={styles.feedback}>
          <Feedback
            error={!valid ? new ApiError(404, "APPROVAL_NOT_FOUND", "当前店铺没有找到该审批单") : query.error}
            loading={valid && (query.isPending || query.isRefetching)}
            onRetry={() => { void query.refetch() }}
            onSignIn={props.onSignIn}
          />
        </div>
      ) : (
        <div className={styles.detailGrid}>
          <div className={styles.stack}>
            <section className={styles.card} aria-labelledby="snapshot-heading">
              <div className={styles.cardHeader}>
                <h2 id="snapshot-heading">{isRefund ? "退款参数快照" : "审批参数快照"}</h2>
                <span className={styles.pill}>不可变快照</span>
              </div>

              {isRefund ? (
                <>
                  <div className={styles.snapshotBand}>
                    <div>
                      <span>申请退款金额</span>
                      <div className={styles.priceRow}>
                        <strong>¥{String(snap.amount ?? detail.snapshot_amount ?? "—")}</strong>
                        <span className={styles.discountTag}>本地沙箱模拟</span>
                      </div>
                    </div>
                    <div>
                      <span>关联订单与实付金额</span>
                      <p className={styles.windowValue}>{String(snap.order_no ?? "—")}</p>
                      <p className={styles.windowNote}>
                        订单实付 ¥{String(snap.order_paid_amount ?? "—")} · 当时已成功退款 ¥{String(snap.refunded_amount ?? "0.00")}
                      </p>
                    </div>
                  </div>
                  <dl className={styles.specGrid}>
                    <div><dt>退款单号</dt><dd>{detail.resource_no}</dd></div>
                    <div><dt>关联订单</dt><dd>{String(snap.order_no ?? "—")}</dd></div>
                    <div><dt>退款原因</dt><dd>{String(snap.reason ?? "—")}</dd></div>
                    <div><dt>店铺</dt><dd>{detail.shop_name}</dd></div>
                    <div><dt>申请人</dt><dd>{detail.requester_name}</dd></div>
                    <div><dt>提交时间</dt><dd>{dateTime.format(new Date(detail.created_at))}</dd></div>
                  </dl>
                </>
              ) : (
                <>
                  <div className={styles.snapshotBand}>
                    <div>
                      <span>申请活动价</span>
                      <div className={styles.priceRow}>
                        <strong>¥{String(snap.promotion_price ?? detail.snapshot_amount ?? "—")}</strong>
                        {snap.original_price ? <span className={styles.strikePrice}>¥{String(snap.original_price)}</span> : null}
                        {snap.discount ? <span className={styles.discountTag}>{String(snap.discount)} 折</span> : null}
                      </div>
                    </div>
                    <div>
                      <span>活动时段 · {String(snap.timezone ?? "Asia/Shanghai")}</span>
                      <p className={styles.windowValue}>
                        {startsAt && endsAt
                          ? `${startsAt.slice(5, 16).replace("T", " ")} → ${endsAt.slice(5, 16).replace("T", " ")}`
                          : "—"}
                      </p>
                      <p className={styles.windowNote}>共 {String(snap.duration_days ?? "—")} 个完整自然日（含开始、不含结束）</p>
                    </div>
                  </div>

                  {riskWarnings.length > 0 && (
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginBottom: 16 }}
                      message="毛利与价格风险预警"
                      description={riskWarnings.join(" ")}
                    />
                  )}

                  <dl className={styles.specGrid}>
                    <div><dt>适用 SKU</dt><dd>{String(snap.sku_code ?? detail.sku_code ?? "—")} · {String(snap.product_name ?? "")}</dd></div>
                    <div><dt>活动版本</dt><dd>V{detail.resource_version}</dd></div>
                    <div><dt>店铺</dt><dd>{detail.shop_name}</dd></div>
                    <div><dt>库存门槛</dt><dd>可售库存 ≥ {String(snap.minimum_qty ?? 100)} 件</dd></div>
                    <div><dt>申请人</dt><dd>{detail.requester_name}</dd></div>
                    <div><dt>提交时间</dt><dd>{dateTime.format(new Date(detail.created_at))}</dd></div>
                  </dl>
                </>
              )}
            </section>

            <section className={styles.card} aria-labelledby="decision-heading">
              <div className={styles.cardHeader}>
                <h2 id="decision-heading">审批意见</h2>
                {resourceLink && (
                  <Link className={styles.outlineLinkBtn} to={resourceLink.to}>
                    {resourceLink.label}
                  </Link>
                )}
              </div>

              {resultBanner && (
                <Alert
                  type={resultBanner.type}
                  showIcon
                  message={resultBanner.text}
                  action={resultBanner.jobId ? <Link className={styles.outlineLinkBtn} to={`/tasks/${resultBanner.jobId}`}>查看执行任务</Link> : undefined}
                  style={{ marginBottom: 12 }}
                />
              )}
              {detail.resource_failure_reason && !resultBanner && (
                <Alert
                  type="warning"
                  showIcon
                  message={isRefund ? "退款执行复核结果" : "排期复核结果"}
                  description={detail.resource_failure_reason}
                  style={{ marginBottom: 12 }}
                />
              )}

              {detail.status === "pending" ? (
                <>
                  <Input.TextArea
                    aria-label="处理备注"
                    rows={3}
                    maxLength={500}
                    placeholder="处理备注（驳回时必填理由）"
                    value={comment}
                    disabled={!detail.can_decide || submitting !== null}
                    onChange={e => setComment(e.target.value)}
                  />
                  <div className={styles.decisionActions}>
                    <Button
                      type="primary"
                      disabled={!detail.can_decide || submitting !== null}
                      loading={submitting === "approve"}
                      onClick={() => { void handleDecision("approve") }}
                    >
                      {isRefund ? "批准退款申请" : "批准此版本"}
                    </Button>
                    <Button
                      disabled={!detail.can_decide || submitting !== null}
                      loading={submitting === "reject"}
                      onClick={() => { void handleDecision("reject") }}
                    >
                      {isRefund ? "驳回并退回" : "驳回并返回"}
                    </Button>
                  </div>
                </>
              ) : (
                <dl className={styles.specGrid}>
                  <div><dt>裁决状态</dt><dd>{statusLabels[detail.status]}</dd></div>
                  <div><dt>处理人</dt><dd>{detail.approver_name ?? "—"}</dd></div>
                  <div><dt>处理时间</dt><dd>{detail.decided_at ? dateTime.format(new Date(detail.decided_at)) : "—"}</dd></div>
                  <div><dt>处理意见</dt><dd>{detail.decision_comment || "无"}</dd></div>
                </dl>
              )}
            </section>
          </div>

          <aside className={styles.stack}>
            <section className={styles.card} aria-labelledby="evidence-heading">
              <div className={styles.cardHeader}>
                <h2 id="evidence-heading">{isRefund ? "资格与余额依据" : "数据依据"}</h2>
                <span className={`${styles.pill} ${styles.pillMint}`}>数据库事实</span>
              </div>
              {isRefund ? (
                <dl className={styles.factList}>
                  <div><dt>订单实付金额</dt><dd>¥{String(snap.order_paid_amount ?? "—")}</dd></div>
                  <div><dt>提交时已成功退款</dt><dd>¥{String(snap.refunded_amount ?? "0.00")}</dd></div>
                  <div><dt>提交时可退上限</dt><dd>¥{String(snap.refundable_amount ?? "—")}</dd></div>
                  <div><dt>当前退款单状态</dt><dd>{detail.resource_status ?? "—"}</dd></div>
                </dl>
              ) : (
                <dl className={styles.factList}>
                  <div>
                    <dt>近 7 天销量</dt>
                    <dd>
                      {sales7d
                        ? `${sales7d.units_sold ?? 0} 件（${sales7d.start_date} 至 ${sales7d.end_date}）`
                        : "未记录"}
                    </dd>
                  </div>
                  <div>
                    <dt>库存快照（提交时）</dt>
                    <dd>可售 {String(snap.available_qty_snapshot ?? "—")} 件</dd>
                  </div>
                  <div>
                    <dt>售价与成本（提交时）</dt>
                    <dd>售价 ¥{String(snap.original_price ?? "—")} · 成本 ¥{String(snap.cost_price ?? "—")}</dd>
                  </div>
                </dl>
              )}
            </section>

            <section className={styles.card} aria-labelledby="boundary-heading">
              <div className={styles.cardHeader}>
                <h2 id="boundary-heading">审批与执行边界</h2>
              </div>
              {detail.can_decide ? (
                <div className={styles.boundaryOk}>
                  <strong>有权审批</strong>
                  <p style={{ margin: "4px 0 0" }}>{props.userName} 拥有本店审批权限，且与申请人不同。</p>
                </div>
              ) : (
                <div className={styles.boundaryWarn}>
                  <strong>当前不可裁决</strong>
                  <p style={{ margin: "4px 0 0" }}>{detail.cannot_decide_reason}</p>
                </div>
              )}
              <div className={styles.boundaryNote}>
                {isRefund
                  ? "批准并不等于资金已退回。后台任务在沙箱执行前会锁定订单行，重新复核支付状态、剩余可退余额与重复退款防护。"
                  : `批准仅授权当前版本（V${detail.resource_version}），后端在排期前会锁定目标 SKU 并重新复核最新售价、可售库存、商品状态与时段冲突。`}
              </div>
            </section>
          </aside>
        </div>
      )}
    </main>
  )
}

export default function ApprovalsPage(props: PageProps) {
  const { approvalId } = useParams()
  return approvalId === undefined
    ? <ApprovalQueue key={props.shopId} {...props} />
    : <ApprovalDetailView key={`${props.shopId}/${approvalId}`} approvalId={approvalId} {...props} />
}
