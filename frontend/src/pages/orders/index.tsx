import { SearchOutlined } from "@ant-design/icons"
import { useQueryClient } from "@tanstack/react-query"
import { Alert, App as AntApp, Button, Form, Input, InputNumber, Modal, Select, Spin, Table } from "antd"
import type { TableColumnsType } from "antd"
import { useState } from "react"
import { Link, useParams } from "react-router-dom"

import { ApiError } from "../../api/client"
import {
  cancelRefundRequest,
  submitRefundRequest,
  useOrderDetailQuery,
  useOrdersQuery,
  useRefundDetailQuery,
  useRefundsQuery,
  type OrderLine,
  type OrderSummary,
  type RefundStatus,
  type RefundSummary,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import styles from "./index.module.css"

type OrdersPageProps = ShopReadProps & {
  shopName?: string
  shopRoles?: string[]
  userId?: number
}

type FeedbackKind = "loading" | "expired" | "denied" | "missing" | "error"
type ListTab = "orders" | "exceptions" | "refunds"

const pageSize = 6
const labels = new Map([
  ["paid", "已支付"],
  ["pending", "待支付"],
  ["partially_refunded", "部分退款"],
  ["refunded", "已退款"],
  ["cancelled", "已取消"],
])
const fulfillmentLabels = new Map([
  ["pending_shipment", "待发货"],
  ["shipped", "已发货"],
  ["delivered", "已签收"],
  ["cancelled", "已取消"],
])
const exceptionLabels = new Map([
  ["buyer_cancelled", "买家取消"],
  ["inventory_review", "库存需复核"],
  ["logistics_sync", "物流状态待同步"],
])
const refundStatusLabels: Record<RefundStatus, string> = {
  pending_approval: "待审批",
  approved: "已批准待执行",
  processing: "执行中",
  succeeded: "已退款",
  rejected: "已驳回",
  cancelled: "已撤回",
  failed: "复核失败",
}
const refundAuditLabels: Record<string, string> = {
  "refund.submitted": "已提交退款审批",
  "refund.cancelled": "申请人撤回退款",
  "approval.approved": "人工审批通过",
  "approval.rejected": "人工审批驳回",
  "refund.succeeded": "沙箱执行退款成功",
  "refund.execution_failed": "执行前实时复核失败",
  "job.dead_lettered": "后台任务进入死信队列",
  "job.manual_retry": "人工重试投递",
}

const dateTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
})
const shortDateTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
})
const yuan = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const money = (amount: string) => `¥${yuan.format(Number(amount))}`
const paymentTime = (value: string | null) => value ? dateTime.format(new Date(value)) : "—"

function Status({ status }: { status: string }) {
  return <span className={status === "paid" ? styles.paid : styles.status}>{labels.get(status) ?? "其他状态"}</span>
}

function RefundStatusPill({ status }: { status: string }) {
  const tone =
    status === "succeeded"
      ? styles.paid
      : status === "pending_approval" || status === "approved" || status === "processing"
        ? styles.pillWarn
        : status === "rejected" || status === "failed"
          ? styles.pillDanger
          : styles.status
  return <span className={tone}>{refundStatusLabels[status as RefundStatus] ?? status}</span>
}

function errorToKind(error: unknown): Exclude<FeedbackKind, "loading"> {
  if (error instanceof ApiError) {
    if (error.status === 401) return "expired"
    if (error.status === 403) return "denied"
    if (error.status === 404) return "missing"
  }
  return "error"
}

function Feedback({
  kind,
  missingText = "当前店铺没有这笔订单",
  retry,
  onSignIn,
}: {
  kind: FeedbackKind
  missingText?: string
  retry: () => void
  onSignIn: () => void
}) {
  if (kind === "loading") return <span role="status"><Spin size="small" /> 正在加载订单</span>
  if (kind === "denied") return <Alert showIcon type="warning" message="没有权限查看当前店铺的订单" description="如需访问，请联系组织管理员。" />
  if (kind === "missing") return <Alert showIcon type="warning" message={missingText} />
  if (kind === "expired") return <Alert showIcon type="warning" message="登录状态已失效" action={<Button onClick={onSignIn}>重新登录</Button>} />
  return <Alert showIcon type="error" message="订单数据加载失败" action={<Button aria-label="重试" onClick={retry}>重试</Button>} />
}

function Breadcrumb({ detail, label }: { detail?: boolean; label?: string }) {
  return <nav className={styles.breadcrumb} aria-label="面包屑">
    <Link to="/">运营工作台</Link><span aria-hidden="true">/</span>
    {detail
      ? <><Link to="/orders">订单售后</Link><span aria-hidden="true">/</span><strong aria-current="page">{label ?? "订单详情"}</strong></>
      : <strong aria-current="page">订单售后</strong>}
  </nav>
}

const orderColumns: TableColumnsType<OrderSummary> = [
  {
    title: "订单编号 / 商品", width: "35%", dataIndex: "order_no", rowScope: "row",
    render: (number: string, order) => (
      <div className={styles.orderLabel}>
        <strong title={number}>{number}</strong>
        <span title={order.sku_codes.join("、")}>{order.sku_codes.length ? `${order.sku_codes.join("、")} · ${order.quantity} 件` : "未记录商品明细"}</span>
      </div>
    ),
  },
  { title: "支付时间", width: "23%", dataIndex: "paid_at", className: styles.number, render: paymentTime },
  { title: "已付金额", width: "15%", dataIndex: "paid_amount", align: "right", className: styles.number, render: money },
  { title: "订单状态", width: "15%", dataIndex: "status", render: (status: string) => <Status status={status} /> },
  { title: "操作", width: "12%", align: "right", render: (_, order) => <Link className={styles.action} to={`/orders/${order.id}`}>查看详情</Link> },
]

const exceptionColumns: TableColumnsType<OrderSummary> = [
  {
    title: "订单 / 商品", width: "32%", dataIndex: "order_no", rowScope: "row",
    render: (number: string, order) => (
      <div className={styles.orderLabel}>
        <strong title={number}>{number}</strong>
        <span>{order.sku_codes.join("、")} · {order.quantity} 件</span>
      </div>
    ),
  },
  {
    title: "履约状态", width: "18%", dataIndex: "fulfillment_status",
    render: (val: string | undefined) => <span className={styles.status}>{fulfillmentLabels.get(val ?? "pending_shipment") ?? val}</span>,
  },
  {
    title: "异常原因", width: "30%", dataIndex: "exception_type",
    render: (val: string | null | undefined) => <span className={styles.pillWarn}>{exceptionLabels.get(val ?? "") ?? val ?? "待复核"}</span>,
  },
  {
    title: "操作", width: "20%", align: "right",
    render: (_, order) => <Link className={styles.action} to={`/orders/${order.id}`}>查看订单</Link>,
  },
]

const refundColumns: TableColumnsType<RefundSummary> = [
  {
    title: "退款单号 / 原因", width: "26%", dataIndex: "refund_no", rowScope: "row",
    render: (no: string, row) => (
      <div className={styles.orderLabel}>
        <strong title={no}>{no}</strong>
        <span title={row.reason}>{row.reason}</span>
      </div>
    ),
  },
  {
    title: "关联订单", width: "18%", dataIndex: "order_no",
    render: (orderNo: string, row) => <Link className={styles.action} to={`/orders/${row.order_id}`}>{orderNo}</Link>,
  },
  { title: "申请退款额", width: "13%", dataIndex: "amount", align: "right", className: styles.number, render: money },
  { title: "申请人", width: "12%", dataIndex: "requester_name" },
  {
    title: "申请时间", width: "13%", dataIndex: "created_at", className: styles.number,
    render: (val: string) => shortDateTime.format(new Date(val)),
  },
  {
    title: "状态", width: "10%", dataIndex: "status",
    render: (status: string) => <RefundStatusPill status={status} />,
  },
  {
    title: "操作", width: "8%", align: "right",
    render: (_, row) => <Link className={styles.action} to={`/orders/refunds/${row.id}`}>详情</Link>,
  },
]

function RefundsListTab(props: OrdersPageProps) {
  const [search, setSearch] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const query = useRefundsQuery(props, true, statusFilter)
  const ready = query.isSuccess
  const rows = ready ? query.data : []
  const term = search.trim().toLowerCase()
  const filtered = rows.filter(row => (
    row.refund_no.toLowerCase().includes(term)
    || row.order_no.toLowerCase().includes(term)
    || row.reason.toLowerCase().includes(term)
  ))

  return (
    <section aria-labelledby="refunds-list-heading">
      <div className={styles.sectionBar}><h2 id="refunds-list-heading">退款记录</h2></div>
      <div className={styles.panel}>
        <div className={styles.toolbar}>
          <Input
            className={styles.search}
            type="search"
            prefix={<SearchOutlined aria-hidden="true" />}
            aria-label="搜索退款单或订单号"
            placeholder="搜索退款单或订单号"
            value={search}
            disabled={!ready}
            onChange={e => setSearch(e.target.value)}
          />
          <Select
            className={styles.filter}
            aria-label="退款状态"
            value={statusFilter}
            virtual={false}
            options={[
              { value: "", label: "全部退款状态" },
              ...Object.entries(refundStatusLabels).map(([value, label]) => ({ value, label })),
            ]}
            onChange={setStatusFilter}
          />
          <span className={styles.timezone}>本地退款沙箱</span>
        </div>
        <Table<RefundSummary>
          className={styles.table}
          rowKey="id"
          columns={refundColumns}
          tableLayout="fixed"
          pagination={false}
          dataSource={filtered}
          locale={{
            emptyText: query.isPending
              ? <span role="status"><Spin size="small" /> 正在加载退款记录</span>
              : query.isError
                ? <Alert type="error" showIcon message="退款记录加载失败" action={<Button onClick={() => { void query.refetch() }}>重试</Button>} />
                : "当前条件下暂无退款记录",
          }}
        />
        {ready && (
          <footer className={styles.footer}>
            <span aria-live="polite">共 {filtered.length} 条记录</span>
          </footer>
        )}
      </div>
    </section>
  )
}

function OrderList(props: OrdersPageProps) {
  const query = useOrdersQuery(props)
  const [tab, setTab] = useState<ListTab>("orders")
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("")
  const [paidDay, setPaidDay] = useState("")
  const [page, setPage] = useState(1)
  const [exceptionFilter, setExceptionFilter] = useState("")
  const [selectedExceptionId, setSelectedExceptionId] = useState<number | null>(null)

  const ready = query.isSuccess
  const orders = ready ? query.data : []
  const term = search.trim().toLowerCase()
  const results = orders.filter(order => (
    (order.order_no.toLowerCase().includes(term) || order.sku_codes.some(code => code.toLowerCase().includes(term)))
    && (!status || order.status === status) && (!paidDay || paymentTime(order.paid_at).slice(0, 10) === paidDay)
  ))
  const lastPage = Math.max(1, Math.ceil(results.length / pageSize))
  const currentPage = Math.min(page, lastPage)

  const exceptionOrders = orders.filter(order => (
    Boolean(order.exception_type)
    && (!exceptionFilter || order.exception_type === exceptionFilter)
  ))
  const selectedException = exceptionOrders.find(order => order.id === selectedExceptionId) ?? exceptionOrders[0] ?? null

  function clearFilters() {
    setSearch("")
    setStatus("")
    setPaidDay("")
    setPage(1)
  }

  const feedbackKind: FeedbackKind = query.isPending || query.isRefetching ? "loading" : errorToKind(query.error)

  return <main className={styles.page}>
    <Breadcrumb />
    <h1>订单售后</h1>
    <p className={styles.summary}>查看当前店铺的订单、异常复核建议与退款审批执行记录。</p>

    <div className={styles.viewTabs} role="tablist" aria-label="订单售后视图切换">
      <button
        type="button"
        role="tab"
        aria-selected={tab === "orders"}
        className={`${styles.viewTab} ${tab === "orders" ? styles.viewTabActive : ""}`}
        onClick={() => setTab("orders")}
      >
        订单列表
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={tab === "exceptions"}
        className={`${styles.viewTab} ${tab === "exceptions" ? styles.viewTabActive : ""}`}
        onClick={() => setTab("exceptions")}
      >
        异常订单
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={tab === "refunds"}
        className={`${styles.viewTab} ${tab === "refunds" ? styles.viewTabActive : ""}`}
        onClick={() => setTab("refunds")}
      >
        退款记录
      </button>
    </div>

    {tab === "refunds" ? (
      <RefundsListTab {...props} />
    ) : tab === "exceptions" ? (
      <section aria-labelledby="exceptions-heading">
        <div className={styles.sectionBar}><h2 id="exceptions-heading">异常订单（只读排查）</h2></div>
        <div className={styles.splitGrid}>
          <div className={styles.panel}>
            <div className={styles.toolbar}>
              <Select
                className={styles.filter}
                aria-label="异常类型"
                value={exceptionFilter}
                disabled={!ready}
                virtual={false}
                options={[
                  { value: "", label: "全部异常类型" },
                  ...[...exceptionLabels.entries()].map(([value, label]) => ({ value, label })),
                ]}
                onChange={setExceptionFilter}
              />
              {ready && <span className={styles.timezone}>{exceptionOrders.length} 笔待处理</span>}
            </div>
            <Table<OrderSummary>
              className={styles.table}
              rowKey="id"
              columns={exceptionColumns}
              tableLayout="fixed"
              pagination={false}
              dataSource={exceptionOrders}
              onRow={row => ({ onClick: () => setSelectedExceptionId(row.id) })}
              rowClassName={row => (selectedException?.id === row.id ? styles.selectedRow : "")}
              locale={{
                emptyText: ready
                  ? "当前店铺暂无异常订单"
                  : <Feedback kind={feedbackKind} retry={() => { void query.refetch() }} onSignIn={props.onSignIn} />,
              }}
            />
          </div>

          <aside className={styles.sideStack}>
            <section className={styles.sideCard} aria-labelledby="exception-detail-heading">
              <div className={styles.cardHeader}>
                <h3 id="exception-detail-heading">选中订单诊断</h3>
                <span className={styles.pillWarn}>待人工处理</span>
              </div>
              {selectedException ? (
                <>
                  <dl className={styles.compactFacts}>
                    <div><dt>订单号</dt><dd>{selectedException.order_no}</dd></div>
                    <div><dt>SKU</dt><dd>{selectedException.sku_codes.join("、") || "—"}</dd></div>
                    <div><dt>履约状态</dt><dd>{fulfillmentLabels.get(selectedException.fulfillment_status ?? "pending_shipment")}</dd></div>
                    <div><dt>异常类型</dt><dd>{exceptionLabels.get(selectedException.exception_type ?? "") ?? selectedException.exception_type}</dd></div>
                  </dl>
                  <div className={styles.adviceBox}>
                    <strong>处理建议（只读）</strong>
                    <p>{selectedException.exception_note}</p>
                  </div>
                  <div className={styles.cardActions}>
                    <Link className={styles.primaryLink} to={`/orders/${selectedException.id}`}>进入订单详情</Link>
                    {selectedException.sku_codes[0] && (
                      <Link className={styles.back} to={`/catalog?tab=inventory&sku=${selectedException.sku_codes[0]}`}>
                        核对库存流水
                      </Link>
                    )}
                  </div>
                </>
              ) : (
                <p className={styles.summary}>点击左侧任意异常订单查看诊断依据与处理建议。</p>
              )}
            </section>
          </aside>
        </div>
      </section>
    ) : (
      <section aria-labelledby="orders-heading">
        <div className={styles.sectionBar}><h2 id="orders-heading">订单列表</h2></div>
        {ready && <dl className={styles.metrics}>
          <div><dt>订单总数</dt><dd>{orders.length} 单</dd></div>
          <div><dt>已支付订单</dt><dd>{orders.filter(order => order.paid_at !== null).length} 单</dd></div>
        </dl>}
        <div className={styles.panel}>
          <div className={styles.toolbar}>
            <Input className={styles.search} type="search" prefix={<SearchOutlined aria-hidden="true" />}
              aria-label="搜索订单号或 SKU" placeholder="搜索订单号或 SKU" value={search} disabled={!ready}
              onChange={event => { setSearch(event.target.value); setPage(1) }} />
            <Select className={styles.filter} aria-label="订单状态" value={status} disabled={!ready} virtual={false}
              options={[{ value: "", label: "全部订单状态" }, ...[...new Set(orders.map(order => order.status))].map(value => ({ value, label: labels.get(value) ?? value }))]}
              onChange={value => { setStatus(value); setPage(1) }} />
            <label className={styles.dateFilter}>支付日期
              <Input type="date" aria-label="支付日期" value={paidDay} disabled={!ready}
                onChange={event => { setPaidDay(event.target.value); setPage(1) }} />
            </label>
            <span className={styles.timezone}>上海时间（UTC+8）</span>
          </div>
          <Table<OrderSummary> className={styles.table} rowKey="id" columns={orderColumns} tableLayout="fixed" pagination={false}
            dataSource={results.slice((currentPage - 1) * pageSize, currentPage * pageSize)}
            locale={{ emptyText: ready
              ? (orders.length ? <div className={styles.empty}>没有符合条件的订单 <Button onClick={clearFilters}>清除筛选</Button></div> : "当前店铺暂无订单")
              : <Feedback kind={feedbackKind} retry={() => { void query.refetch() }} onSignIn={props.onSignIn} /> }} />
          {ready && <footer className={styles.footer}>
            <span aria-live="polite">共 {results.length} 条记录</span>
            <nav className={styles.pager} aria-label="订单分页">
              <Button size="small" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</Button>
              <span aria-current="page">{currentPage}</span>
              <Button size="small" disabled={currentPage >= lastPage} onClick={() => setPage(currentPage + 1)}>下一页</Button>
            </nav>
          </footer>}
        </div>
      </section>
    )}
  </main>
}

const lineColumns: TableColumnsType<OrderLine> = [
  { title: "商品 / 规格", dataIndex: "product_name", width: "35%", rowScope: "row", ellipsis: true },
  { title: "SKU", dataIndex: "sku_code", width: "20%", ellipsis: true },
  { title: "数量", dataIndex: "quantity", width: "11%", align: "right", className: styles.number },
  { title: "成交单价", dataIndex: "unit_price", width: "17%", align: "right", className: styles.number, render: money },
  { title: "实付", dataIndex: "paid_amount", width: "17%", align: "right", className: styles.number, render: money },
]

function OrderDetail({ orderId, shopRoles = ["operator"], ...props }: OrdersPageProps & { orderId: string }) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const valid = /^[1-9]\d{0,9}$/.test(orderId) && Number(orderId) <= 2_147_483_647
  const query = useOrderDetailQuery(props, orderId, valid)
  const order = query.isSuccess ? query.data : undefined
  const [modalOpen, setModalOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [modalError, setModalError] = useState<string | null>(null)
  const [form] = Form.useForm<{ amount: number; reason: string }>()

  const canOperate = shopRoles.includes("operator") || shopRoles.includes("admin")
  const refundableAmount = Number(order?.refundable_amount ?? order?.paid_amount ?? "0")
  const feedbackKind: FeedbackKind = !valid
    ? "missing"
    : query.isPending || query.isRefetching
      ? "loading"
      : errorToKind(query.error)

  function openRefundModal() {
    setModalError(null)
    form.setFieldsValue({ amount: refundableAmount > 0 ? refundableAmount : 1, reason: "" })
    setModalOpen(true)
  }

  async function handleSubmitRefund(values: { amount: number; reason: string }) {
    if (!order) return
    setModalError(null)
    setSubmitting(true)
    try {
      const idemKey = `order-refund-${order.id}-${Date.now()}`
      const res = await submitRefundRequest(
        props,
        {
          order_id: order.id,
          amount: values.amount.toFixed(2),
          reason: values.reason.trim(),
        },
        idemKey,
      )
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "orders"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "refunds"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      setModalOpen(false)
      message?.success?.(res.message)
    } catch (error) {
      setModalError(error instanceof ApiError ? error.message : "提交退款申请失败，请稍后重试。")
    } finally {
      setSubmitting(false)
    }
  }

  return <main className={styles.page}>
    <Breadcrumb detail />
    <header className={styles.detailHeader}>
      <div><h1>订单详情</h1><p className={styles.summary}>{order?.order_no ?? "查看订单支付信息与商品明细。"}</p></div>
      <Link className={styles.back} to="/orders">返回订单列表</Link>
    </header>
    {!valid || !order ? <div className={styles.feedback}>
      <Feedback kind={feedbackKind} retry={() => { void query.refetch() }} onSignIn={props.onSignIn} />
    </div> : <div className={styles.splitGrid}>
      <div className={styles.stack}>
        <section className={styles.panel} aria-labelledby="info-heading">
          <h2 id="info-heading" className={styles.panelTitle}>订单信息</h2>
          <dl className={styles.info}>
            <div><dt>订单编号</dt><dd title={order.order_no}>{order.order_no}</dd></div>
            <div><dt>订单状态</dt><dd><Status status={order.status} /></dd></div>
            <div><dt>支付时间（上海时间）</dt><dd>{paymentTime(order.paid_at)}</dd></div>
            <div><dt>创建时间（上海时间）</dt><dd>{paymentTime(order.created_at)}</dd></div>
            <div><dt>实付金额</dt><dd>{money(order.paid_amount)}</dd></div>
            <div><dt>商品数量</dt><dd>{order.quantity} 件</dd></div>
            {order.fulfillment_status && (
              <div><dt>履约状态</dt><dd>{fulfillmentLabels.get(order.fulfillment_status) ?? order.fulfillment_status}</dd></div>
            )}
            {order.refunded_amount !== undefined && (
              <div><dt>已成功退款</dt><dd>{money(order.refunded_amount)}</dd></div>
            )}
          </dl>
        </section>
        <section className={styles.panel} aria-labelledby="lines-heading">
          <h2 id="lines-heading" className={styles.panelTitle}>商品明细</h2>
          <div className={styles.lines} tabIndex={0} role="region" aria-label="订单商品明细表">
            <Table<OrderLine> className={styles.table} rowKey="id" columns={lineColumns} tableLayout="fixed" pagination={false}
              dataSource={order.items} locale={{ emptyText: "这笔订单暂无商品明细" }} />
          </div>
        </section>
      </div>

      <aside className={styles.sideStack}>
        <section className={styles.sideCard} aria-labelledby="eligibility-heading">
          <div className={styles.cardHeader}>
            <h3 id="eligibility-heading">退款资格校验</h3>
            <span className={order.can_refund ? styles.paid : styles.pillWarn}>
              {order.can_refund ? "可申请退款" : "暂不可申请"}
            </span>
          </div>
          <dl className={styles.compactFacts}>
            <div><dt>订单实付</dt><dd>{money(order.paid_amount)}</dd></div>
            <div><dt>已成功退款</dt><dd>{money(order.refunded_amount ?? "0.00")}</dd></div>
            <div><dt>在途退款申请</dt><dd>{money(order.in_flight_refund_amount ?? "0.00")}</dd></div>
            <div><dt>当前可退上限</dt><dd>{money(order.refundable_amount ?? "0.00")}</dd></div>
          </dl>
          {order.refund_block_reason && (
            <Alert type="warning" showIcon message={order.refund_block_reason} style={{ marginTop: 12 }} />
          )}
          {canOperate && (
            <div className={styles.cardActions}>
              <Button
                type="primary"
                disabled={!order.can_refund}
                onClick={openRefundModal}
              >
                申请退款
              </Button>
            </div>
          )}
        </section>

        <section className={styles.sideCard} aria-labelledby="related-refunds-heading">
          <div className={styles.cardHeader}>
            <h3 id="related-refunds-heading">关联退款记录</h3>
            <span className={styles.status}>{(order.refunds ?? []).length} 笔</span>
          </div>
          {(order.refunds ?? []).length === 0 ? (
            <p className={styles.summary} style={{ marginBottom: 0 }}>该订单暂无退款申请记录。</p>
          ) : (
            <ul className={styles.refundList}>
              {(order.refunds ?? []).map(item => (
                <li key={item.id} className={styles.refundRow}>
                  <div>
                    <strong>{item.refund_no}</strong>
                    <p>{money(item.amount)} · {item.reason}</p>
                  </div>
                  <div className={styles.refundRowRight}>
                    <RefundStatusPill status={item.status} />
                    <Link className={styles.action} to={`/orders/refunds/${item.id}`}>查看退款详情</Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </aside>
    </div>}

    <Modal
      title={`发起退款申请 · ${order?.order_no ?? ""}`}
      open={modalOpen}
      onCancel={() => setModalOpen(false)}
      onOk={() => { form.submit() }}
      okText="提交退款审批"
      cancelText="取消"
      confirmLoading={submitting}
    >
      {modalError && <Alert type="error" showIcon message={modalError} style={{ marginBottom: 12 }} />}
      <Form form={form} layout="vertical" onFinish={values => { void handleSubmitRefund(values) }}>
        <Form.Item
          label={`申请退款金额（当前最高可退 ¥${refundableAmount.toFixed(2)}）`}
          name="amount"
          rules={[{ required: true, message: "请输入退款金额" }]}
        >
          <InputNumber min={0.01} max={Math.max(0.01, refundableAmount)} step={1} precision={2} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          label="退款原因"
          name="reason"
          rules={[{ required: true, min: 2, message: "请填写至少 2 个字的退款原因" }]}
        >
          <Input.TextArea rows={3} maxLength={500} placeholder="例如：部分商品破损或买家申请退差价" />
        </Form.Item>
      </Form>
    </Modal>
  </main>
}

function RefundDetailView({
  refundId,
  shopRoles = ["operator"],
  userId = 0,
  ...props
}: OrdersPageProps & { refundId: string }) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const valid = /^[1-9]\d{0,9}$/.test(refundId) && Number(refundId) <= 2_147_483_647
  const query = useRefundDetailQuery(props, refundId, valid)
  const detail = query.isSuccess ? query.data : undefined
  const [cancelling, setCancelling] = useState(false)

  const canCancel = Boolean(
    detail
    && detail.status === "pending_approval"
    && (detail.requester_id === userId || shopRoles.includes("admin") || shopRoles.includes("operator")),
  )

  async function handleCancel() {
    if (!detail) return
    setCancelling(true)
    try {
      await cancelRefundRequest(props, detail.id)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "refunds"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "orders"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      message?.success?.("已撤回该退款申请")
    } catch (error) {
      message?.error?.(error instanceof ApiError ? error.message : "撤回退款失败，请稍后重试。")
    } finally {
      setCancelling(false)
    }
  }

  const feedbackKind: FeedbackKind = !valid
    ? "missing"
    : query.isPending || query.isRefetching
      ? "loading"
      : errorToKind(query.error)

  return (
    <main className={styles.page}>
      <Breadcrumb detail label="退款详情" />
      <header className={styles.detailHeader}>
        <div>
          <h1>退款详情</h1>
          <p className={styles.summary}>
            {detail ? `${detail.refund_no} · ${detail.order_no}` : "查看退款资格核对、审批与沙箱执行状态。"}
          </p>
        </div>
        <div className={styles.cardActions} style={{ marginTop: 0 }}>
          {detail && <RefundStatusPill status={detail.status} />}
          {detail && <Link className={styles.back} to={`/orders/${detail.order_id}`}>查看原订单</Link>}
        </div>
      </header>

      {!valid || !detail ? (
        <div className={styles.feedback}>
          <Feedback
            kind={feedbackKind}
            missingText="当前店铺没有找到这笔退款单"
            retry={() => { void query.refetch() }}
            onSignIn={props.onSignIn}
          />
        </div>
      ) : (
        <>
          <dl className={styles.refundBand}>
            <div className={styles.refundBandHighlight}>
              <dt>申请退款额</dt>
              <dd>{money(detail.amount)}</dd>
              <span>当前申请</span>
            </div>
            <div>
              <dt>订单实付</dt>
              <dd>{money(detail.order_paid_amount)}</dd>
              <span>订单支付事实</span>
            </div>
            <div>
              <dt>已成功退款</dt>
              <dd>{money(detail.refunded_amount)}</dd>
              <span>当前订单累计</span>
            </div>
            <div>
              <dt>退款渠道</dt>
              <dd>本地沙箱</dd>
              <span>未接入真实支付</span>
            </div>
          </dl>

          {detail.failure_reason && (
            <Alert type="error" showIcon message="执行前复核失败" description={detail.failure_reason} style={{ marginBottom: 16 }} />
          )}

          <div className={styles.splitGrid}>
            <div className={styles.stack}>
              <section className={styles.panel} aria-labelledby="refund-info-heading">
                <h2 id="refund-info-heading" className={styles.panelTitle}>退款申请与资格</h2>
                <dl className={styles.info}>
                  <div><dt>退款单号</dt><dd>{detail.refund_no}</dd></div>
                  <div><dt>关联订单</dt><dd>{detail.order_no}（{labels.get(detail.order_status) ?? detail.order_status}）</dd></div>
                  <div><dt>申请人</dt><dd>{detail.requester_name}</dd></div>
                  <div><dt>退款原因</dt><dd title={detail.reason}>{detail.reason}</dd></div>
                  <div><dt>当前剩余可退</dt><dd>{money(detail.refundable_amount)}</dd></div>
                  <div><dt>关联审批单</dt><dd>{detail.approval_no ?? "—"}</dd></div>
                </dl>
                <div className={styles.adviceBox} style={{ margin: 18 }}>
                  <strong>执行约束</strong>
                  <p>人工批准后，后端会在事务内锁定订单行并二次实时复核可退余额与重复退款防护，仅在本地沙箱完成退款状态更新。</p>
                </div>
                <div className={styles.cardActions} style={{ padding: "0 18px 18px", marginTop: 0 }}>
                  {canCancel && (
                    <Button loading={cancelling} onClick={() => { void handleCancel() }}>
                      撤回退款申请
                    </Button>
                  )}
                  {detail.approval_id && (
                    <Link className={styles.back} to={`/approvals/${detail.approval_id}`}>查看审批单</Link>
                  )}
                  {detail.job_id && (
                    <Link className={styles.back} to={`/tasks/${detail.job_id}`}>查看执行任务</Link>
                  )}
                </div>
              </section>
            </div>

            <aside className={styles.sideStack}>
              <section className={styles.sideCard} aria-labelledby="refund-timeline-heading">
                <div className={styles.cardHeader}>
                  <h3 id="refund-timeline-heading">处理进度</h3>
                  <span className={styles.status}>{detail.audit_logs.length} 条记录</span>
                </div>
                <ol className={styles.timeline}>
                  {detail.audit_logs.map(log => (
                    <li key={log.id} className={styles.timelineItem}>
                      <div>
                        <strong>{refundAuditLabels[log.action] ?? log.action}</strong>
                        <p>操作人：{log.actor_name}</p>
                      </div>
                      <span className={styles.timelineTime}>{shortDateTime.format(new Date(log.created_at))}</span>
                    </li>
                  ))}
                </ol>
              </section>
            </aside>
          </div>
        </>
      )}
    </main>
  )
}

export default function OrdersPage(props: OrdersPageProps) {
  const { orderId, refundId } = useParams()
  if (refundId !== undefined) {
    return <RefundDetailView key={`${props.shopId}/refund/${refundId}`} refundId={refundId} {...props} />
  }
  return orderId === undefined
    ? <OrderList key={props.shopId} {...props} />
    : <OrderDetail key={`${props.shopId}/${orderId}`} orderId={orderId} {...props} />
}
