import { SearchOutlined } from "@ant-design/icons"
import { useQueryClient } from "@tanstack/react-query"
import { Alert, App as AntApp, Button, Input, Select, Spin, Table } from "antd"
import type { TableColumnsType } from "antd"
import { useState } from "react"
import { Link, useParams } from "react-router-dom"

import { ApiError } from "../../api/client"
import {
  retryJobRequest,
  useJobDetailQuery,
  useJobsQuery,
  type JobStatus,
  type JobSummary,
  type JobTab,
  type JobType,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import styles from "./index.module.css"

type PageProps = ShopReadProps & {
  shopName: string
  shopRoles: string[]
}

const statusLabels: Record<JobStatus, string> = {
  pending: "待投递",
  running: "执行中",
  succeeded: "成功",
  retrying: "退避重试中",
  failed: "复核失败",
  dead_lettered: "待人工处理",
}

const typeLabels: Record<JobType, string> = {
  promotion_schedule: "促销排期",
  promotion_activate: "到点生效",
  promotion_complete: "到期结束",
  refund_execute: "退款执行",
  price_change_execute: "调价执行",
}

const auditActionLabels: Record<string, string> = {
  "promotion.submitted": "已提交审批",
  "promotion.cancelled": "已撤回申请",
  "promotion.revised": "提交修订版本",
  "approval.approved": "人工审批通过",
  "approval.rejected": "人工审批驳回",
  "promotion.scheduled": "业务复核通过并排期",
  "promotion.schedule_failed": "业务复核失败",
  "promotion.activated": "到点自动生效",
  "promotion.completed": "到期自动结束",
  "promotion.missed_window": "错过生效时段已结束",
  "refund.submitted": "已提交退款审批",
  "refund.cancelled": "已撤回退款申请",
  "refund.succeeded": "沙箱退款执行成功",
  "refund.execution_failed": "退款实时复核失败",
  "price_change.submitted": "已提交调价审批",
  "price_change.cancelled": "已撤回调价申请",
  "price_change.succeeded": "复核通过并更新售价",
  "price_change.execution_failed": "调价实时复核失败",
  "job.dead_lettered": "重试超限转入死信队列",
  "job.manual_retry": "人工按原操作重新投递",
}

const dateTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
})

function StatusPill({ status }: { status: JobStatus }) {
  const tone =
    status === "succeeded"
      ? styles.pillMint
      : status === "dead_lettered" || status === "failed"
        ? styles.pillDanger
        : styles.pillPending
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
  if (loading) return <span role="status"><Spin size="small" /> 正在加载任务数据</span>
  const status = error instanceof ApiError ? error.status : undefined
  if (status === 401) {
    return <Alert showIcon type="warning" message="登录状态已失效" action={<Button onClick={onSignIn}>重新登录</Button>} />
  }
  if (status === 403) {
    return <Alert showIcon type="warning" message="没有权限查看当前店铺的任务" description="如需访问，请联系组织管理员。" />
  }
  if (status === 404) {
    return <Alert showIcon type="warning" message="当前店铺没有找到该任务" />
  }
  return <Alert showIcon type="error" message="任务数据加载失败" action={<Button onClick={onRetry}>重试</Button>} />
}

function TaskList(props: PageProps) {
  const [tab, setTab] = useState<JobTab>("all")
  const [typeFilter, setTypeFilter] = useState("")
  const [search, setSearch] = useState("")
  const query = useJobsQuery(props, tab)
  const ready = query.isSuccess
  const rows = ready ? query.data : []
  const term = search.trim().toLowerCase()
  const filtered = rows.filter(row => (
    (row.job_no.toLowerCase().includes(term)
      || row.resource_no.toLowerCase().includes(term)
      || row.resource_name.toLowerCase().includes(term))
    && (!typeFilter || row.job_type === typeFilter)
  ))
  const attentionCount = rows.filter(
    row => row.status === "dead_lettered" || row.status === "failed" || row.status === "retrying",
  ).length

  const columns: TableColumnsType<JobSummary> = [
    {
      title: "任务编号 / 类型",
      width: "22%",
      dataIndex: "job_no",
      render: (no: string, row) => (
        <div className={styles.cellStack}>
          <strong>{no}</strong>
          <span>{typeLabels[row.job_type] ?? row.job_type}</span>
        </div>
      ),
    },
    {
      title: "业务对象",
      width: "26%",
      dataIndex: "resource_no",
      render: (no: string, row) => (
        <div className={styles.cellStack}>
          <strong>{row.resource_name || no}</strong>
          <span>{no} · V{row.resource_version}</span>
        </div>
      ),
    },
    {
      title: "状态",
      width: "14%",
      dataIndex: "status",
      render: (status: JobStatus) => <StatusPill status={status} />,
    },
    {
      title: "重试次数",
      width: "12%",
      align: "right",
      className: styles.number,
      render: (_, row) => `${row.attempts} / ${row.max_attempts}`,
    },
    {
      title: "最后更新",
      width: "14%",
      dataIndex: "updated_at",
      className: styles.number,
      render: (val: string) => dateTime.format(new Date(val)),
    },
    {
      title: "操作",
      width: "12%",
      align: "right",
      render: (_, row) => (
        <Link
          className={row.status === "dead_lettered" ? styles.primaryLinkBtn : styles.actionLink}
          to={`/tasks/${row.id}`}
        >
          {row.status === "dead_lettered" ? "处理任务" : "查看详情"}
        </Link>
      ),
    },
  ]

  const tabItems: { key: JobTab; label: string }[] = [
    { key: "all", label: "全部任务" },
    { key: "needs_attention", label: "需人工处理" },
    { key: "completed", label: "已完成" },
  ]

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span>
        <strong aria-current="page">任务中心</strong>
      </nav>
      <div className={styles.headerBar}>
        <div>
          <h1>任务中心</h1>
          <p className={styles.summary}>{props.shopName} · 查看后台异步任务、自动重试状态与死信人工处理。</p>
        </div>
        {ready && attentionCount > 0 && (
          <span className={`${styles.pill} ${styles.pillDanger}`}>{attentionCount} 项需人工处理</span>
        )}
      </div>

      <div className={styles.tabs} role="tablist" aria-label="任务视图切换">
        {tabItems.map(item => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            className={`${styles.tabButton} ${tab === item.key ? styles.tabActive : ""}`}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <section className={styles.panel} aria-label="后台任务列表">
        <div className={styles.toolbar}>
          <Input
            className={styles.search}
            type="search"
            prefix={<SearchOutlined aria-hidden="true" />}
            aria-label="搜索任务号或业务编号"
            placeholder="搜索任务号或业务编号"
            value={search}
            disabled={!ready}
            onChange={e => setSearch(e.target.value)}
          />
          <Select
            className={styles.filter}
            aria-label="任务类型"
            value={typeFilter}
            disabled={!ready}
            virtual={false}
            options={[
              { value: "", label: "全部任务类型" },
              ...Object.entries(typeLabels).map(([value, label]) => ({ value, label })),
            ]}
            onChange={setTypeFilter}
          />
          {ready && <span className={styles.countText}>共 {filtered.length} 个任务</span>}
        </div>

        <Table<JobSummary>
          className={styles.table}
          rowKey="id"
          columns={columns}
          tableLayout="fixed"
          pagination={false}
          dataSource={filtered}
          locale={{
            emptyText: ready
              ? "当前视图下暂无后台任务。"
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

function TaskDetailView({ jobId, ...props }: PageProps & { jobId: string }) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const valid = /^[1-9]\d{0,9}$/.test(jobId) && Number(jobId) <= 2_147_483_647
  const query = useJobDetailQuery(props, jobId, valid)
  const detail = query.isSuccess ? query.data : undefined

  const [retrying, setRetrying] = useState(false)
  const [actionBanner, setActionBanner] = useState<{ type: "success" | "error"; text: string } | null>(null)
  const canOperate = props.shopRoles.includes("operator") || props.shopRoles.includes("admin")

  async function handleRetry() {
    if (!detail) return
    setActionBanner(null)
    setRetrying(true)
    try {
      const response = await retryJobRequest(props, detail.id)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "jobs"] })
      setActionBanner({ type: "success", text: response.message })
      message?.success?.(response.message)
    } catch (error) {
      setActionBanner({
        type: "error",
        text: error instanceof ApiError ? error.message : "重试请求失败，请稍后重试。",
      })
    } finally {
      setRetrying(false)
    }
  }

  const isRefundJob = detail?.resource_type === "refund"
  const isPriceJob = detail?.resource_type === "price_change"
  const resourcePath = detail
    ? isRefundJob
      ? `/orders/refunds/${detail.resource_id}`
      : isPriceJob
        ? `/catalog/price-changes/${detail.resource_id}`
        : `/promotions/${detail.resource_id}`
    : "/tasks"

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span>
        <Link to="/tasks">任务中心</Link><span aria-hidden="true">/</span>
        <strong aria-current="page">{detail?.job_no ?? "任务详情"}</strong>
      </nav>
      <div className={styles.headerBar}>
        <div>
          <h1>
            {detail?.status === "dead_lettered" || detail?.status === "failed"
              ? isRefundJob ? "退款执行异常" : isPriceJob ? "调价执行异常" : "促销执行异常"
              : isRefundJob ? "退款执行任务" : isPriceJob ? "调价执行任务" : "促销执行任务"}
          </h1>
          <p className={styles.summary}>
            {detail
              ? `${detail.job_no} · ${detail.resource_name || detail.resource_no} · ${detail.resource_no} · V${detail.resource_version}`
              : "查看后台执行状态、重试记录与审计时间线。"}
          </p>
        </div>
        <div className={styles.headerActions}>
          {detail && <StatusPill status={detail.status} />}
          <Link className={styles.outlineLinkBtn} to="/tasks">返回任务中心</Link>
        </div>
      </div>

      {!valid || !detail ? (
        <div className={styles.feedback}>
          <Feedback
            error={!valid ? new ApiError(404, "JOB_NOT_FOUND", "当前店铺没有找到该任务") : query.error}
            loading={valid && (query.isPending || query.isRefetching)}
            onRetry={() => { void query.refetch() }}
            onSignIn={props.onSignIn}
          />
        </div>
      ) : (
        <div className={styles.stack}>
          {detail.last_error && (
            <Alert
              type={detail.status === "dead_lettered" ? "error" : "warning"}
              showIcon
              message={detail.status === "dead_lettered" ? "已达自动重试上限，转入死信队列" : "最近一次执行异常"}
              description={detail.last_error}
            />
          )}
          {actionBanner && <Alert type={actionBanner.type} showIcon message={actionBanner.text} />}

          <div className={styles.detailGrid}>
            <div className={styles.stack}>
              <section className={styles.card} aria-labelledby="timeline-heading">
                <div className={styles.cardHeader}>
                  <h2 id="timeline-heading">执行时间线</h2>
                  <span className={styles.pill}>{detail.audit_logs.length} 条记录</span>
                </div>
                <ol className={styles.timeline}>
                  {detail.audit_logs.map(log => (
                    <li key={log.id} className={styles.timelineItem}>
                      <div>
                        <strong>{auditActionLabels[log.action] ?? log.action}</strong>
                        <p>操作人：{log.actor_name}</p>
                        {typeof log.after_json?.failure_reason === "string" && <p>{log.after_json.failure_reason}</p>}
                        {typeof log.after_json?.last_error === "string" && <p>异常：{log.after_json.last_error}</p>}
                      </div>
                      <span className={styles.timelineTime}>{dateTime.format(new Date(log.created_at))}</span>
                    </li>
                  ))}
                </ol>
              </section>

              <section className={styles.card} aria-labelledby="boundary-heading">
                <div className={styles.cardHeader}>
                  <h2 id="boundary-heading">重试与补偿边界</h2>
                </div>
                <div className={styles.hintBox}>
                  {detail.retry_hint ??
                    "Consumer 基于 job_id 幂等消费并在数据库事务提交后手动 ACK；重复投递不会重复修改业务状态。"}
                </div>
              </section>
            </div>

            <aside className={styles.stack}>
              <section className={styles.card} aria-labelledby="result-heading">
                <div className={styles.cardHeader}>
                  <h2 id="result-heading">{detail.can_retry ? "人工处理" : "执行结果"}</h2>
                  <span className={`${styles.pill} ${detail.status === "succeeded" ? styles.pillMint : ""}`}>
                    {typeLabels[detail.job_type] ?? detail.job_type}
                  </span>
                </div>
                <dl className={styles.specList}>
                  <div>
                    <dt>{isRefundJob ? "关联退款单" : isPriceJob ? "关联调价单" : "关联活动"}</dt>
                    <dd>
                      <Link className={styles.actionLink} to={resourcePath}>
                        {detail.resource_no}
                      </Link>
                    </dd>
                  </div>
                  <div><dt>当前版本</dt><dd>V{detail.resource_version}</dd></div>
                  <div><dt>任务编号</dt><dd>{detail.job_no}</dd></div>
                  <div><dt>幂等键</dt><dd>{detail.idempotency_key}</dd></div>
                  <div><dt>重试次数</dt><dd>{detail.attempts} / {detail.max_attempts}</dd></div>
                  <div><dt>关联资源状态</dt><dd>{detail.resource_status ?? "—"}</dd></div>
                </dl>

                <div className={styles.actionBlock}>
                  {detail.can_retry && canOperate && (
                    <Button type="primary" loading={retrying} onClick={() => { void handleRetry() }}>
                      按原操作重试
                    </Button>
                  )}
                  <Link className={styles.outlineLinkBtn} to={resourcePath}>
                    {isRefundJob ? "查看退款详情" : isPriceJob ? "查看调价详情" : "查看促销活动详情"}
                  </Link>
                </div>
              </section>
            </aside>
          </div>
        </div>
      )}
    </main>
  )
}

export default function TasksPage(props: PageProps) {
  const { jobId } = useParams()
  return jobId === undefined
    ? <TaskList key={props.shopId} {...props} />
    : <TaskDetailView key={`${props.shopId}/${jobId}`} jobId={jobId} {...props} />
}
