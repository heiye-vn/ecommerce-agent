import { SearchOutlined } from "@ant-design/icons"
import { Alert, Button, Input, Select, Spin, Table } from "antd"
import type { TableColumnsType } from "antd"
import { useState } from "react"
import { Link, useParams } from "react-router-dom"

import { ApiError } from "../../api/client"
import {
  useOrderDetailQuery,
  useOrdersQuery,
  type OrderLine,
  type OrderSummary,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import styles from "./index.module.css"

type FeedbackKind = "loading" | "expired" | "denied" | "missing" | "error"

const pageSize = 6
const labels = new Map([
  ["paid", "已支付"],
  ["pending", "待支付"],
  ["partially_refunded", "部分退款"],
  ["refunded", "已退款"],
  ["cancelled", "已取消"],
])
const dateTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
})
const yuan = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const money = (amount: string) => `¥${yuan.format(Number(amount))}`
const paymentTime = (value: string | null) => value ? dateTime.format(new Date(value)) : "—"

function Status({ status }: { status: string }) {
  return <span className={status === "paid" ? styles.paid : styles.status}>{labels.get(status) ?? "其他状态"}</span>
}

function errorToKind(error: unknown): Exclude<FeedbackKind, "loading"> {
  if (error instanceof ApiError) {
    if (error.status === 401) return "expired"
    if (error.status === 403) return "denied"
    if (error.status === 404) return "missing"
  }
  return "error"
}

function Feedback({ kind, retry, onSignIn }: {
  kind: FeedbackKind; retry: () => void; onSignIn: () => void
}) {
  if (kind === "loading") return <span role="status"><Spin size="small" /> 正在加载订单</span>
  if (kind === "denied") return <Alert showIcon type="warning" message="没有权限查看当前店铺的订单" description="如需访问，请联系组织管理员。" />
  if (kind === "missing") return <Alert showIcon type="warning" message="当前店铺没有这笔订单" />
  if (kind === "expired") return <Alert showIcon type="warning" message="登录状态已失效" action={<Button onClick={onSignIn}>重新登录</Button>} />
  return <Alert showIcon type="error" message="订单数据加载失败" action={<Button aria-label="重试" onClick={retry}>重试</Button>} />
}

function Breadcrumb({ detail }: { detail?: boolean }) {
  return <nav className={styles.breadcrumb} aria-label="面包屑">
    <Link to="/">运营工作台</Link><span aria-hidden="true">/</span>
    {detail ? <><Link to="/orders">订单售后</Link><span aria-hidden="true">/</span><strong aria-current="page">订单详情</strong></>
      : <strong aria-current="page">订单售后</strong>}
  </nav>
}

const orderColumns: TableColumnsType<OrderSummary> = [
  { title: "订单编号 / 商品", width: "35%", dataIndex: "order_no", rowScope: "row", render: (number: string, order) => (
    <div className={styles.orderLabel}>
      <strong title={number}>{number}</strong>
      <span title={order.sku_codes.join("、")}>{order.sku_codes.length ? `${order.sku_codes.join("、")} · ${order.quantity} 件` : "未记录商品明细"}</span>
    </div>
  ) },
  { title: "支付时间", width: "23%", dataIndex: "paid_at", className: styles.number, render: paymentTime },
  { title: "已付金额", width: "15%", dataIndex: "paid_amount", align: "right", className: styles.number, render: money },
  { title: "订单状态", width: "15%", dataIndex: "status", render: (status: string) => <Status status={status} /> },
  { title: "操作", width: "12%", align: "right", render: (_, order) => <Link className={styles.action} to={`/orders/${order.id}`}>查看详情</Link> },
]

function OrderList(props: ShopReadProps) {
  const query = useOrdersQuery(props)
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("")
  const [paidDay, setPaidDay] = useState("")
  const [page, setPage] = useState(1)
  const ready = query.isSuccess
  const orders = ready ? query.data : []
  const term = search.trim().toLowerCase()
  const results = orders.filter(order => (
    (order.order_no.toLowerCase().includes(term) || order.sku_codes.some(code => code.toLowerCase().includes(term)))
    && (!status || order.status === status) && (!paidDay || paymentTime(order.paid_at).slice(0, 10) === paidDay)
  ))
  const lastPage = Math.max(1, Math.ceil(results.length / pageSize))
  const currentPage = Math.min(page, lastPage)

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
    <p className={styles.summary}>查看当前店铺的订单、支付信息和商品明细。</p>
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
  </main>
}

const lineColumns: TableColumnsType<OrderLine> = [
  { title: "商品 / 规格", dataIndex: "product_name", width: "35%", rowScope: "row", ellipsis: true },
  { title: "SKU", dataIndex: "sku_code", width: "20%", ellipsis: true },
  { title: "数量", dataIndex: "quantity", width: "11%", align: "right", className: styles.number },
  { title: "成交单价", dataIndex: "unit_price", width: "17%", align: "right", className: styles.number, render: money },
  { title: "实付", dataIndex: "paid_amount", width: "17%", align: "right", className: styles.number, render: money },
]

function OrderDetail({ orderId, ...props }: ShopReadProps & { orderId: string }) {
  const valid = /^[1-9]\d{0,9}$/.test(orderId) && Number(orderId) <= 2_147_483_647
  const query = useOrderDetailQuery(props, orderId, valid)
  const order = query.isSuccess ? query.data : undefined
  const feedbackKind: FeedbackKind = !valid
    ? "missing"
    : query.isPending || query.isRefetching
      ? "loading"
      : errorToKind(query.error)

  return <main className={styles.page}>
    <Breadcrumb detail />
    <header className={styles.detailHeader}>
      <div><h1>订单详情</h1><p className={styles.summary}>{order?.order_no ?? "查看订单支付信息与商品明细。"}</p></div>
      <Link className={styles.back} to="/orders">返回订单列表</Link>
    </header>
    {!valid || !order ? <div className={styles.feedback}>
      <Feedback kind={feedbackKind} retry={() => { void query.refetch() }} onSignIn={props.onSignIn} />
    </div> : <>
      <section className={styles.panel} aria-labelledby="info-heading">
        <h2 id="info-heading" className={styles.panelTitle}>订单信息</h2>
        <dl className={styles.info}>
          <div><dt>订单编号</dt><dd title={order.order_no}>{order.order_no}</dd></div>
          <div><dt>订单状态</dt><dd><Status status={order.status} /></dd></div>
          <div><dt>支付时间（上海时间）</dt><dd>{paymentTime(order.paid_at)}</dd></div>
          <div><dt>创建时间（上海时间）</dt><dd>{paymentTime(order.created_at)}</dd></div>
          <div><dt>实付金额</dt><dd>{money(order.paid_amount)}</dd></div>
          <div><dt>商品数量</dt><dd>{order.quantity} 件</dd></div>
        </dl>
      </section>
      <section className={styles.panel} aria-labelledby="lines-heading">
        <h2 id="lines-heading" className={styles.panelTitle}>商品明细</h2>
        <div className={styles.lines} tabIndex={0} role="region" aria-label="订单商品明细表">
          <Table<OrderLine> className={styles.table} rowKey="id" columns={lineColumns} tableLayout="fixed" pagination={false}
            dataSource={order.items} locale={{ emptyText: "这笔订单暂无商品明细" }} />
        </div>
      </section>
    </>}
  </main>
}

export default function OrdersPage(props: ShopReadProps) {
  const { orderId } = useParams()
  return orderId === undefined ? <OrderList key={props.shopId} {...props} />
    : <OrderDetail key={`${props.shopId}/${orderId}`} orderId={orderId} {...props} />
}
