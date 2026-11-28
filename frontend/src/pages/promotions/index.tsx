import { PlusOutlined, SearchOutlined } from "@ant-design/icons"
import { useQueryClient } from "@tanstack/react-query"
import { Alert, App as AntApp, Button, Input, Modal, Select, Spin, Table } from "antd"
import type { TableColumnsType } from "antd"
import { useMemo, useState, type FormEvent } from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom"
import type * as echarts from "echarts"

import { ApiError } from "../../api/client"
import {
  cancelPromotionRequest,
  revisePromotionRequest,
  submitPromotionRequest,
  useCatalogQuery,
  usePromotionDetailQuery,
  usePromotionReviewQuery,
  usePromotionsQuery,
  type PromotionStatus,
  type PromotionSummary,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import EChartBox from "../../components/EChartBox"
import styles from "./index.module.css"

type PageProps = ShopReadProps & {
  shopName: string
  shopRoles: string[]
  userId: number
}

const pageSize = 6

const statusLabels: Record<PromotionStatus, string> = {
  pending_approval: "待审批",
  approved: "已批准",
  scheduled: "已排期",
  active: "生效中",
  completed: "已结束",
  rejected: "已驳回",
  cancelled: "已撤回",
  failed: "复核失败",
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

function formatShortWindow(startsAt: string, endsAt: string) {
  return `${startsAt.slice(5, 16).replace("T", " ")} → ${endsAt.slice(5, 16).replace("T", " ")}`
}

function StatusPill({ status }: { status: PromotionStatus }) {
  const tone =
    status === "scheduled" || status === "active"
      ? styles.pillScheduled
      : status === "pending_approval" || status === "approved"
        ? styles.pillPending
        : status === "failed" || status === "rejected"
          ? styles.pillFailed
          : ""
  return <span className={`${styles.pill} ${tone}`}>{statusLabels[status] ?? status}</span>
}

function Feedback({
  error,
  loading,
  missingText,
  onRetry,
  onSignIn,
}: {
  error: unknown
  loading: boolean
  missingText?: string
  onRetry: () => void
  onSignIn: () => void
}) {
  if (loading) return <span role="status"><Spin size="small" /> 正在加载促销数据</span>
  const status = error instanceof ApiError ? error.status : undefined
  if (status === 401) {
    return <Alert showIcon type="warning" message="登录状态已失效" action={<Button onClick={onSignIn}>重新登录</Button>} />
  }
  if (status === 403) {
    return <Alert showIcon type="warning" message="没有权限查看当前店铺的促销活动" description="如需访问，请联系组织管理员。" />
  }
  if (status === 404) {
    return <Alert showIcon type="warning" message={missingText || "当前店铺没有找到该促销活动"} />
  }
  return <Alert showIcon type="error" message="促销数据加载失败" action={<Button onClick={onRetry}>重试</Button>} />
}

function buildCalendarDays(year: number, monthIndex: number) {
  const firstDay = new Date(Date.UTC(year, monthIndex, 1))
  const startOffset = (firstDay.getUTCDay() + 6) % 7
  const gridStart = new Date(Date.UTC(year, monthIndex, 1 - startOffset))
  const days: { isoDate: string; dayNum: number; inMonth: boolean }[] = []
  for (let i = 0; i < 42; i += 1) {
    const d = new Date(gridStart.getTime() + i * 86_400_000)
    const isoDate = d.toISOString().slice(0, 10)
    days.push({
      isoDate,
      dayNum: d.getUTCDate(),
      inMonth: d.getUTCMonth() === monthIndex,
    })
  }
  return days
}

function PromotionCalendarTab({
  rows,
  onSelectReview,
}: {
  rows: PromotionSummary[]
  onSelectReview: (promotionId: number) => void
}) {
  const [monthCursor, setMonthCursor] = useState({ year: 2026, month: 10 })
  const [selectedDate, setSelectedDate] = useState("2026-11-21")
  const [skuFilter, setSkuFilter] = useState("")

  const visibleRows = rows.filter(r => (!skuFilter || r.sku_code === skuFilter))
  const calendarDays = useMemo(
    () => buildCalendarDays(monthCursor.year, monthCursor.month),
    [monthCursor.month, monthCursor.year],
  )

  function eventsForDate(isoDate: string) {
    return visibleRows.filter(item => {
      const s = item.starts_at.slice(0, 10)
      const e = item.ends_at.slice(0, 10)
      return isoDate >= s && isoDate < e
    })
  }

  function shiftMonth(delta: number) {
    const next = new Date(Date.UTC(monthCursor.year, monthCursor.month + delta, 1))
    setMonthCursor({ year: next.getUTCFullYear(), month: next.getUTCMonth() })
  }

  const selectedEvents = eventsForDate(selectedDate)
  const monthLabel = `${monthCursor.year} 年 ${monthCursor.month + 1} 月`
  const skuOptions = [...new Set(rows.map(r => r.sku_code))]

  return (
    <section aria-labelledby="calendar-heading">
      <div className={styles.calendarGridWrap}>
        <div className={styles.panel}>
          <div className={styles.calendarHeaderRow}>
            <strong id="calendar-heading">{monthLabel}</strong>
            <div className={styles.headerActions}>
              <Select
                className={styles.filter}
                aria-label="筛选日历 SKU"
                value={skuFilter}
                virtual={false}
                options={[{ value: "", label: "全部 SKU" }, ...skuOptions.map(code => ({ value: code, label: code }))]}
                onChange={setSkuFilter}
              />
              <Button size="small" onClick={() => shiftMonth(-1)}>上月</Button>
              <Button size="small" onClick={() => { setMonthCursor({ year: 2026, month: 10 }); setSelectedDate("2026-11-21") }}>回到基准月</Button>
              <Button size="small" onClick={() => shiftMonth(1)}>下月</Button>
            </div>
          </div>
          <div className={styles.calendarWeekRow}>
            {["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map(w => <span key={w}>{w}</span>)}
          </div>
          <div className={styles.calendarDays}>
            {calendarDays.map(cell => {
              const items = eventsForDate(cell.isoDate)
              return (
                <button
                  key={cell.isoDate}
                  type="button"
                  className={`${styles.calendarCell} ${!cell.inMonth ? styles.calendarCellOutside : ""} ${selectedDate === cell.isoDate ? styles.calendarCellSelected : ""}`}
                  onClick={() => setSelectedDate(cell.isoDate)}
                >
                  <span className={styles.calendarDayNum}>{cell.dayNum}</span>
                  {items.slice(0, 2).map(ev => (
                    <span
                      key={ev.id}
                      className={`${styles.calendarEventTag} ${ev.status === "completed" ? styles.calendarEventMuted : ""}`}
                    >
                      {ev.name}
                    </span>
                  ))}
                </button>
              )
            })}
          </div>
        </div>

        <aside className={styles.leftStack}>
          <section className={styles.card} aria-labelledby="day-events-heading">
            <div className={styles.cardHeader}>
              <h2 id="day-events-heading">{selectedDate} 排期活动</h2>
              <span className={styles.pill}>{selectedEvents.length} 场</span>
            </div>
            {selectedEvents.length === 0 ? (
              <p className={styles.summary}>当日暂无覆盖的促销时段（结束日期 00:00 不占用当天）。</p>
            ) : (
              <div className={styles.approvalList}>
                {selectedEvents.map(ev => (
                  <div key={ev.id} className={styles.approvalRow}>
                    <div>
                      <strong>{ev.name}</strong>
                      <div style={{ color: "#71717a", fontSize: 12 }}>
                        {ev.sku_code} · {formatShortWindow(ev.starts_at, ev.ends_at)}
                      </div>
                    </div>
                    <div className={styles.headerActions}>
                      <StatusPill status={ev.status} />
                      <Button size="small" onClick={() => onSelectReview(ev.id)}>复盘</Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
          <section className={styles.card}>
            <div className={styles.cardHeader}>
              <h2>时段口径</h2>
            </div>
            <p className={styles.summary}>
              日历按店铺当地时区（Asia/Shanghai）展示。活动结束时间（00:00）不占用下一自然日。
            </p>
          </section>
        </aside>
      </div>
    </section>
  )
}

function PromotionReviewTab({
  rows,
  selectedPromoId,
  onChangePromoId,
  ...props
}: PageProps & {
  rows: PromotionSummary[]
  selectedPromoId: number | null
  onChangePromoId: (id: number) => void
}) {
  const defaultId = selectedPromoId
    ?? rows.find(r => r.status === "completed")?.id
    ?? rows.find(r => r.status === "active")?.id
    ?? rows[0]?.id
    ?? null
  const reviewQuery = usePromotionReviewQuery(props, defaultId, Boolean(defaultId))
  const review = reviewQuery.isSuccess ? reviewQuery.data : undefined

  const chartOption = useMemo<echarts.EChartsCoreOption>(() => {
    if (!review) return {}
    return {
      grid: { left: 42, right: 16, top: 20, bottom: 28 },
      tooltip: { trigger: "axis" },
      legend: { bottom: 0, left: 0, textStyle: { color: "#52525b", fontSize: 12 } },
      xAxis: {
        type: "category",
        data: review.daily_comparison.map(pt => pt.day_label),
        axisLine: { lineStyle: { color: "#e4e4e7" } },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: "#f4f4f5" } },
      },
      series: [
        {
          name: "活动期",
          type: "line",
          smooth: false,
          symbol: "circle",
          symbolSize: 7,
          data: review.daily_comparison.map(pt => pt.promo_units),
          lineStyle: { color: "#166534", width: 2.5 },
          itemStyle: { color: "#166534" },
          areaStyle: { color: "rgba(193, 251, 212, 0.4)" },
        },
        {
          name: "基线期",
          type: "line",
          smooth: false,
          symbol: "none",
          data: review.daily_comparison.map(pt => pt.baseline_units),
          lineStyle: { color: "#86efac", width: 2, type: "dashed" },
          itemStyle: { color: "#86efac" },
        },
      ],
    }
  }, [review])

  if (rows.length === 0) {
    return <Alert type="info" showIcon message="当前店铺暂无可复盘的促销活动。" />
  }

  return (
    <section aria-labelledby="review-tab-heading">
      <div className={styles.headerBar} style={{ marginBottom: 14 }}>
        <div>
          <h2 id="review-tab-heading" style={{ margin: 0, fontSize: 18 }}>活动效果对比与复盘</h2>
          <p className={styles.summary}>对比活动自然日与活动前等长基线期的真实支付件数与销售额。</p>
        </div>
        <Select<number>
          className={styles.search}
          aria-label="选择复盘活动"
          value={defaultId ?? undefined}
          virtual={false}
          options={rows.map(r => ({ value: r.id, label: `${r.name} · ${r.promotion_no}` }))}
          onChange={onChangePromoId}
        />
      </div>

      {reviewQuery.isPending ? (
        <div className={styles.feedback}><Spin size="small" /> 正在统计活动复盘数据…</div>
      ) : reviewQuery.isError || !review ? (
        <Feedback error={reviewQuery.error} loading={false} onRetry={() => { void reviewQuery.refetch() }} onSignIn={props.onSignIn} />
      ) : (
        <>
          <section className={styles.metricsStrip} aria-label="活动复盘核心指标">
            <div className={`${styles.metricCard} ${styles.metricHighlight}`}>
              <span>活动期间销量</span>
              <strong>{review.promo_units} 件</strong>
              <p>
                支付件数{review.units_change_pct !== null ? ` · 较基线 ${Number(review.units_change_pct) >= 0 ? "+" : ""}${review.units_change_pct}%` : ""}
              </p>
            </div>
            <div className={styles.metricCard}>
              <span>活动销售额</span>
              <strong>¥{Number(review.promo_gmv).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}</strong>
              <p>活动 SKU 支付额</p>
            </div>
            <div className={styles.metricCard}>
              <span>基线销量</span>
              <strong>{review.baseline_units} 件</strong>
              <p>活动前 {review.duration_days} 个完整自然日</p>
            </div>
            <div className={styles.metricCard}>
              <span>观测窗口</span>
              <strong>{review.duration_days} 天</strong>
              <p>{review.promo_start_date.slice(5)} → {review.promo_end_date.slice(5)}</p>
            </div>
          </section>

          <div className={styles.detailGrid}>
            <section className={styles.card} aria-labelledby="review-chart-heading">
              <div className={styles.cardHeader}>
                <h2 id="review-chart-heading">活动销量与前期基线</h2>
                <span className={styles.pill}>件 · 按日对齐</span>
              </div>
              <EChartBox
                option={chartOption}
                height={250}
                ariaLabel={`${review.promotion_name} 活动期与基线期按日销量对比`}
              />
            </section>

            <aside className={styles.leftStack}>
              <section className={styles.card} aria-labelledby="review-fact-heading">
                <div className={styles.cardHeader}>
                  <h2 id="review-fact-heading">数据口径</h2>
                  <span className={`${styles.pill} ${styles.pillScheduled}`}>数据事实</span>
                </div>
                <dl className={styles.factMetaList}>
                  <div><dt>活动编号</dt><dd>{review.promotion_no}（{review.sku_codes.join("、") || "—"}）</dd></div>
                  <div><dt>活动期</dt><dd>{review.promo_start_date} 至 {review.promo_end_date}</dd></div>
                  <div><dt>基线期</dt><dd>{review.baseline_start_date} 至 {review.baseline_end_date}</dd></div>
                  <div><dt>口径说明</dt><dd>{review.methodology}</dd></div>
                </dl>
              </section>

              <section className={styles.card} aria-labelledby="review-note-heading">
                <div className={styles.cardHeader}>
                  <h2 id="review-note-heading">复盘建议</h2>
                </div>
                <div className={styles.speculateBox}>{review.review_note}</div>
              </section>
            </aside>
          </div>
        </>
      )}
    </section>
  )
}

function PromotionList(props: PageProps) {
  const { message } = AntApp.useApp()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const tabParam = searchParams.get("tab")
  const initialTab = tabParam === "calendar" ? "calendar" : tabParam === "review" ? "review" : "list"
  const [activeTab, setActiveTab] = useState<"list" | "calendar" | "review">(initialTab)
  const [reviewPromoId, setReviewPromoId] = useState<number | null>(null)
  const queryClient = useQueryClient()
  const query = usePromotionsQuery(props)
  const catalogQuery = useCatalogQuery(props)

  const [search, setSearch] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const [page, setPage] = useState(1)
  const [modalOpen, setModalOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [draftName, setDraftName] = useState("")
  const [draftSku, setDraftSku] = useState("SKU-001")
  const [draftDiscount, setDraftDiscount] = useState("8")
  const [draftStartDate, setDraftStartDate] = useState("2026-11-22")
  const [draftDuration, setDraftDuration] = useState("3")

  const canOperate = props.shopRoles.includes("operator") || props.shopRoles.includes("admin")
  const ready = query.isSuccess
  const rows = ready ? query.data : []
  const term = search.trim().toLowerCase()
  const filtered = rows.filter(row => (
    (row.name.toLowerCase().includes(term)
      || row.promotion_no.toLowerCase().includes(term)
      || row.sku_code.toLowerCase().includes(term))
    && (!statusFilter || row.status === statusFilter)
  ))
  const lastPage = Math.max(1, Math.ceil(filtered.length / pageSize))
  const currentPage = Math.min(page, lastPage)

  const columns: TableColumnsType<PromotionSummary> = [
    {
      title: "活动名称",
      width: "26%",
      dataIndex: "name",
      render: (name: string, row) => (
        <div className={styles.nameCell}>
          <strong title={name}>{name}</strong>
          <span>{row.promotion_no} · V{row.version}</span>
        </div>
      ),
    },
    { title: "适用 SKU", width: "13%", dataIndex: "sku_code", className: styles.number },
    { title: "原价", width: "10%", dataIndex: "original_price", align: "right", className: styles.number, render: (v: string) => `¥${v}` },
    { title: "活动价", width: "10%", dataIndex: "promotion_price", align: "right", className: styles.number, render: (v: string) => `¥${v}` },
    {
      title: "活动时段 · 店铺时间",
      width: "21%",
      className: styles.number,
      render: (_, row) => formatShortWindow(row.starts_at, row.ends_at),
    },
    { title: "状态", width: "10%", dataIndex: "status", render: (status: PromotionStatus) => <StatusPill status={status} /> },
    {
      title: "操作",
      width: "10%",
      align: "right",
      render: (_, row) => <Link className={styles.actionLink} to={`/promotions/${row.id}`}>查看详情</Link>,
    },
  ]

  async function handleCreateSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    setSubmitting(true)
    try {
      const idempotencyKey = `ui-promo-${props.shopId}-${draftSku.trim().toUpperCase()}-${draftDiscount.trim()}-${draftStartDate}-${draftDuration}-${Date.now()}`
      const created = await submitPromotionRequest(
        props,
        {
          ...(draftName.trim() && { name: draftName.trim() }),
          sku_code: draftSku.trim().toUpperCase(),
          discount: draftDiscount.trim(),
          start_date: draftStartDate,
          duration_days: Number(draftDuration),
        },
        idempotencyKey,
      )
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "promotions"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      setModalOpen(false)
      message?.success?.(`已提交人工审批（审批单 ${created.approval_no}）`)
      navigate(`/promotions/${created.promotion_id}`)
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : "提交促销草案失败，请检查参数后重试。")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span><strong aria-current="page">促销管理</strong>
      </nav>
      <div className={styles.headerBar}>
        <div>
          <h1>促销管理</h1>
          <p className={styles.summary}>{props.shopName} · 查找和管理当前店铺促销活动与审批版本。</p>
        </div>
        {canOperate && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setFormError(null); setModalOpen(true) }}>
            新建促销草案
          </Button>
        )}
      </div>

      <div className={styles.viewTabs} role="tablist" aria-label="促销管理子视图">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "list"}
          className={`${styles.viewTab} ${activeTab === "list" ? styles.viewTabActive : ""}`}
          onClick={() => setActiveTab("list")}
        >
          活动列表
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "calendar"}
          className={`${styles.viewTab} ${activeTab === "calendar" ? styles.viewTabActive : ""}`}
          onClick={() => setActiveTab("calendar")}
        >
          促销日历
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "review"}
          className={`${styles.viewTab} ${activeTab === "review" ? styles.viewTabActive : ""}`}
          onClick={() => setActiveTab("review")}
        >
          活动复盘
        </button>
      </div>

      {activeTab === "calendar" ? (
        <PromotionCalendarTab
          rows={rows}
          onSelectReview={(id) => {
            setReviewPromoId(id)
            setActiveTab("review")
          }}
        />
      ) : activeTab === "review" ? (
        <PromotionReviewTab
          {...props}
          rows={rows}
          selectedPromoId={reviewPromoId}
          onChangePromoId={setReviewPromoId}
        />
      ) : (
        <section aria-labelledby="promotions-heading">
        <div className={styles.panel}>
          <div className={styles.toolbar}>
            <Input
              className={styles.search}
              type="search"
              prefix={<SearchOutlined aria-hidden="true" />}
              aria-label="搜索活动名称、编号或 SKU"
              placeholder="搜索活动名称、编号或 SKU"
              value={search}
              disabled={!ready}
              onChange={event => { setSearch(event.target.value); setPage(1) }}
            />
            <Select
              className={styles.filter}
              aria-label="活动状态"
              value={statusFilter}
              disabled={!ready}
              virtual={false}
              options={[
                { value: "", label: "全部活动状态" },
                ...Object.entries(statusLabels).map(([value, label]) => ({ value, label })),
              ]}
              onChange={value => { setStatusFilter(value); setPage(1) }}
            />
            {ready && <span className={styles.countText}>{rows.length} 个活动</span>}
          </div>

          <Table<PromotionSummary>
            className={styles.table}
            rowKey="id"
            columns={columns}
            tableLayout="fixed"
            pagination={false}
            dataSource={filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize)}
            locale={{
              emptyText: ready
                ? rows.length
                  ? <div className={styles.empty}>没有符合条件的促销活动 <Button onClick={() => { setSearch(""); setStatusFilter(""); setPage(1) }}>清除筛选</Button></div>
                  : "当前店铺暂无促销活动，可在 Agent 对话或点击右上角新建草案。"
                : <Feedback error={query.error} loading={query.isPending || query.isRefetching} onRetry={() => { void query.refetch() }} onSignIn={props.onSignIn} />,
            }}
          />

          {ready && (
            <footer className={styles.footer}>
              <span aria-live="polite">共 {filtered.length} 条记录</span>
              <nav className={styles.pager} aria-label="促销活动分页">
                <Button size="small" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</Button>
                <span aria-current="page">{currentPage}</span>
                <Button size="small" disabled={currentPage >= lastPage} onClick={() => setPage(currentPage + 1)}>下一页</Button>
              </nav>
            </footer>
          )}
        </div>
      </section>
      )}

      <Modal
        title="新建促销草案并提交审批"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <form onSubmit={handleCreateSubmit} className={styles.reviseForm}>
          {formError && <div className={styles.fullSpan}><Alert type="error" showIcon message={formError} /></div>}
          <label className={`${styles.fieldLabel} ${styles.fullSpan}`}>
            活动名称（可选，留空自动生成）
            <Input value={draftName} onChange={e => setDraftName(e.target.value)} placeholder="例如：冬季暖饮专场" maxLength={160} />
          </label>
          <label className={styles.fieldLabel}>
            适用 SKU
            <Select
              aria-label="适用 SKU"
              value={draftSku}
              onChange={setDraftSku}
              virtual={false}
              options={(catalogQuery.data ?? [{ sku_code: "SKU-001", product_name: "轻食谷物礼盒" }]).map(item => ({
                value: item.sku_code,
                label: `${item.sku_code} · ${item.product_name}`,
              }))}
            />
          </label>
          <label className={styles.fieldLabel}>
            折扣（折，0–10 之间）
            <Input value={draftDiscount} onChange={e => setDraftDiscount(e.target.value)} placeholder="8" />
          </label>
          <label className={styles.fieldLabel}>
            开始日期（店铺 00:00 生效）
            <Input type="date" value={draftStartDate} onChange={e => setDraftStartDate(e.target.value)} />
          </label>
          <label className={styles.fieldLabel}>
            持续天数（整数天）
            <Input type="number" min={1} max={365} value={draftDuration} onChange={e => setDraftDuration(e.target.value)} />
          </label>
          <div className={`${styles.actionRow} ${styles.fullSpan}`}>
            <Button type="primary" htmlType="submit" loading={submitting}>提交人工审批</Button>
            <Button onClick={() => setModalOpen(false)}>取消</Button>
          </div>
        </form>
      </Modal>
    </main>
  )
}

function PromotionDetailView({ promotionId, ...props }: PageProps & { promotionId: string }) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const valid = /^[1-9]\d{0,9}$/.test(promotionId) && Number(promotionId) <= 2_147_483_647
  const query = usePromotionDetailQuery(props, promotionId, valid)
  const detail = query.isSuccess ? query.data : undefined

  const [cancelling, setCancelling] = useState(false)
  const [revising, setRevising] = useState(false)
  const [showRevise, setShowRevise] = useState(false)
  const [reviseDiscount, setReviseDiscount] = useState("")
  const [reviseStartDate, setReviseStartDate] = useState("")
  const [reviseDuration, setReviseDuration] = useState("3")
  const [reviseReason, setReviseReason] = useState("")
  const [actionError, setActionError] = useState<string | null>(null)

  const canOperate = props.shopRoles.includes("operator") || props.shopRoles.includes("admin")
  const canCancel = Boolean(
    detail && detail.status === "pending_approval" && (detail.created_by === props.userId || props.shopRoles.includes("admin")),
  )
  const canRevise = Boolean(
    detail && canOperate && ["rejected", "cancelled", "failed"].includes(detail.status),
  )

  function openReviseForm() {
    if (!detail) return
    setReviseDiscount(detail.discount)
    setReviseStartDate(detail.starts_at.slice(0, 10))
    setReviseDuration(String(detail.duration_days))
    setReviseReason("")
    setActionError(null)
    setShowRevise(true)
  }

  async function handleCancel() {
    if (!detail) return
    setActionError(null)
    setCancelling(true)
    try {
      await cancelPromotionRequest(props, detail.id)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "promotions"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      message?.success?.("已撤回该待审批申请")
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "撤回申请失败，请重试。")
    } finally {
      setCancelling(false)
    }
  }

  async function handleReviseSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!detail) return
    setActionError(null)
    setRevising(true)
    try {
      const idempotencyKey = `ui-revise-${detail.id}-v${detail.version + 1}-${reviseDiscount}-${reviseStartDate}-${reviseDuration}`
      const res = await revisePromotionRequest(
        props,
        detail.id,
        {
          discount: reviseDiscount.trim(),
          start_date: reviseStartDate,
          duration_days: Number(reviseDuration),
          ...(reviseReason.trim() && { reason: reviseReason.trim() }),
        },
        idempotencyKey,
      )
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "promotions"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      setShowRevise(false)
      message?.success?.(`已提交 V${res.version} 修订方案（审批单 ${res.approval_no}）`)
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "修订重新送审失败，请检查参数。")
    } finally {
      setRevising(false)
    }
  }

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span>
        <Link to="/promotions">促销管理</Link><span aria-hidden="true">/</span>
        <strong aria-current="page">{detail?.name ?? "活动详情"}</strong>
      </nav>
      <div className={styles.headerBar}>
        <div>
          <h1>{detail?.name ?? "促销活动详情"}</h1>
          <p className={styles.summary}>
            {detail ? `${detail.promotion_no} · V${detail.version} · ${props.shopName}` : "查看活动规则、审批版本与审计时间线。"}
          </p>
        </div>
        <div className={styles.headerActions}>
          {detail && <StatusPill status={detail.status} />}
          <Link className={styles.backLink} to="/promotions">返回活动列表</Link>
        </div>
      </div>

      {!valid || !detail ? (
        <div className={styles.feedback}>
          <Feedback
            error={!valid ? new ApiError(404, "PROMOTION_NOT_FOUND", "当前店铺没有找到该促销活动") : query.error}
            loading={valid && (query.isPending || query.isRefetching)}
            onRetry={() => { void query.refetch() }}
            onSignIn={props.onSignIn}
          />
        </div>
      ) : (
        <>
          <section className={styles.metricsStrip} aria-label="活动核心指标">
            <div className={`${styles.metricCard} ${styles.metricHighlight}`}>
              <span>活动价</span>
              <strong>¥{detail.promotion_price}</strong>
              <p>原价 ¥{detail.original_price} · {detail.discount} 折（成本 ¥{detail.cost_price}）</p>
            </div>
            <div className={styles.metricCard}>
              <span>适用 SKU</span>
              <strong>{detail.sku_code}</strong>
              <p>{detail.product_name}</p>
            </div>
            <div className={styles.metricCard}>
              <span>活动开始</span>
              <strong>{detail.starts_at.slice(5, 16).replace("T", " ")}</strong>
              <p>{detail.timezone} · 含开始</p>
            </div>
            <div className={styles.metricCard}>
              <span>活动结束</span>
              <strong>{detail.ends_at.slice(5, 16).replace("T", " ")}</strong>
              <p>{detail.timezone} · 不含结束（共 {detail.duration_days} 天）</p>
            </div>
          </section>

          <div className={styles.detailGrid}>
            <div className={styles.leftStack}>
              <section className={styles.card} aria-labelledby="rules-heading">
                <div className={styles.cardHeader}>
                  <h2 id="rules-heading">活动规则与审批快照</h2>
                  <span className={styles.pill}>版本 V{detail.version}</span>
                </div>
                {detail.below_cost && (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ marginBottom: 14 }}
                    message="负毛利风控提示"
                    description={`活动价 ¥${detail.promotion_price} 低于商品成本价 ¥${detail.cost_price}。`}
                  />
                )}
                {detail.failure_reason && (
                  <Alert
                    type="error"
                    showIcon
                    style={{ marginBottom: 14 }}
                    message="业务复核未通过"
                    description={detail.failure_reason}
                  />
                )}
                <dl className={styles.ruleGrid}>
                  <div><dt>折扣</dt><dd>{detail.discount} 折</dd></div>
                  <div><dt>库存门槛</dt><dd>可售库存 ≥ {detail.minimum_qty} 件</dd></div>
                  <div><dt>申请人</dt><dd>{detail.creator_name}</dd></div>
                  <div>
                    <dt>当前审批单</dt>
                    <dd>
                      {detail.latest_approval_id && detail.latest_approval_no
                        ? <Link className={styles.actionLink} to={`/approvals/${detail.latest_approval_id}`}>{detail.latest_approval_no}</Link>
                        : "—"}
                    </dd>
                  </div>
                </dl>

                <div className={styles.approvalList}>
                  {detail.approvals.map(item => (
                    <div key={item.id} className={styles.approvalRow}>
                      <div>
                        <strong>{item.approval_no} · V{item.resource_version}</strong>
                        <span style={{ marginLeft: 8, color: "#71717a" }}>
                          {item.requester_name} 提交{item.approver_name ? ` · ${item.approver_name} 处理` : ""}
                        </span>
                      </div>
                      <Link className={styles.actionLink} to={`/approvals/${item.id}`}>查看审批快照</Link>
                    </div>
                  ))}
                </div>
              </section>

              <section className={styles.card} aria-labelledby="actions-heading">
                <div className={styles.cardHeader}>
                  <h2 id="actions-heading">后续动作</h2>
                </div>
                {actionError && <Alert type="error" showIcon message={actionError} style={{ marginBottom: 12 }} />}
                <div className={styles.actionRow}>
                  {canCancel && (
                    <Button onClick={handleCancel} loading={cancelling}>撤回申请</Button>
                  )}
                  {canRevise && !showRevise && (
                    <Button type="primary" onClick={openReviseForm}>
                      修订为 V{detail.version + 1} 并重新送审
                    </Button>
                  )}
                  {detail.status === "scheduled" && (
                    <div className={styles.infoBanner}>
                      活动已通过实时复核并完成排期，将在开始时间自动生效（执行与审计沿用同一活动编号 {detail.promotion_no}）。
                    </div>
                  )}
                </div>

                {showRevise && (
                  <form onSubmit={handleReviseSubmit} className={styles.reviseForm}>
                    <label className={styles.fieldLabel}>
                      新折扣（折）
                      <Input aria-label="新折扣" value={reviseDiscount} onChange={e => setReviseDiscount(e.target.value)} />
                    </label>
                    <label className={styles.fieldLabel}>
                      新开始日期
                      <Input aria-label="新开始日期" type="date" value={reviseStartDate} onChange={e => setReviseStartDate(e.target.value)} />
                    </label>
                    <label className={styles.fieldLabel}>
                      持续天数
                      <Input aria-label="新持续天数" type="number" min={1} max={365} value={reviseDuration} onChange={e => setReviseDuration(e.target.value)} />
                    </label>
                    <label className={styles.fieldLabel}>
                      修订说明
                      <Input aria-label="修订说明" value={reviseReason} onChange={e => setReviseReason(e.target.value)} placeholder="按审批意见调整档期或折扣" />
                    </label>
                    <div className={`${styles.actionRow} ${styles.fullSpan}`}>
                      <Button type="primary" htmlType="submit" loading={revising}>
                        提交 V{detail.version + 1} 审批
                      </Button>
                      <Button onClick={() => setShowRevise(false)}>取消编辑</Button>
                    </div>
                  </form>
                )}
              </section>
            </div>

            <aside className={styles.card} aria-labelledby="timeline-heading">
              <div className={styles.cardHeader}>
                <h2 id="timeline-heading">活动审计时间线</h2>
                <span className={styles.pill}>{detail.audit_logs.length} 条记录</span>
              </div>
              <ol className={styles.timeline}>
                {detail.audit_logs.map(log => (
                  <li key={log.id} className={styles.timelineItem}>
                    <div>
                      <strong>{auditActionLabels[log.action] ?? log.action}</strong>
                      <p>操作人：{log.actor_name}</p>
                      {typeof log.after_json?.failure_reason === "string" && <p>{log.after_json.failure_reason}</p>}
                      {typeof log.after_json?.comment === "string" && log.after_json.comment && <p>意见：{log.after_json.comment}</p>}
                    </div>
                    <span className={styles.timelineTime}>{dateTime.format(new Date(log.created_at))}</span>
                  </li>
                ))}
              </ol>
            </aside>
          </div>
        </>
      )}
    </main>
  )
}

export default function PromotionsPage(props: PageProps) {
  const { promotionId } = useParams()
  return promotionId === undefined
    ? <PromotionList key={props.shopId} {...props} />
    : <PromotionDetailView key={`${props.shopId}/${promotionId}`} promotionId={promotionId} {...props} />
}
