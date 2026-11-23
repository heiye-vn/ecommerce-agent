import { InboxOutlined, SearchOutlined } from "@ant-design/icons"
import { Alert, Button, Input, Select, Spin, Table } from "antd"
import type { TableColumnsType } from "antd"
import { useState } from "react"
import { Link } from "react-router-dom"

import { ApiError } from "../../api/client"
import { useCatalogQuery, type CatalogSku } from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import styles from "./index.module.css"

const pageSize = 6
const statusLabels = { active: "在售", inactive: "已下架", other: "其他状态" }
const inventoryLabels = { available: "有库存", empty: "缺货", missing: "未记录" }
const yuan = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function inventoryStatus(quantity: number | null) {
  return quantity === null ? "missing" : quantity === 0 ? "empty" : "available"
}

function productStatus(status: string) {
  return status === "active" || status === "inactive" ? status : "other"
}

function quantityCell(quantity: number | null) {
  return quantity === null ? <span aria-label="未记录">—</span> : `${quantity} 件`
}

const columns: TableColumnsType<CatalogSku> = [
  {
    title: "商品 / 规格", width: "28%", dataIndex: "product_name", rowScope: "row",
    render: (name: string, sku) => (
      <div className={styles.product}>
        <span className={styles.productIcon} aria-hidden="true"><InboxOutlined /></span>
        <div className={styles.productText}>
          <strong title={name}>{name}</strong>
          <span title={sku.category}>{sku.category}</span>
        </div>
      </div>
    ),
  },
  { title: "SKU 编码", width: "15%", dataIndex: "sku_code", ellipsis: true, className: styles.code },
  {
    title: "商品状态", width: "12%", dataIndex: "status",
    render: (status: string) => <span className={status === "active" ? styles.activePill : styles.pill}>
      {statusLabels[productStatus(status)]}
    </span>,
  },
  {
    title: "当前售价", width: "12%", dataIndex: "current_price", align: "right", className: styles.number,
    render: (price: string) => `¥${yuan.format(Number(price))}`,
  },
  {
    title: "可售库存", width: "11%", dataIndex: "available_qty", align: "right", className: styles.number,
    render: quantityCell,
  },
  {
    title: "预占库存", width: "11%", dataIndex: "reserved_qty", align: "right", className: styles.number,
    render: quantityCell,
  },
  {
    title: "库存提示", width: "11%", dataIndex: "available_qty", key: "inventoryStatus",
    render: (quantity: number | null) => {
      const status = inventoryStatus(quantity)
      return <span className={status === "available" ? styles.inventoryPill : styles.pill}>{inventoryLabels[status]}</span>
    },
  },
]

export default function CatalogPage(props: ShopReadProps) {
  const { onSignIn } = props
  const query = useCatalogQuery(props)
  const [search, setSearch] = useState("")
  const [category, setCategory] = useState<string>()
  const [status, setStatus] = useState<string>()
  const [inventory, setInventory] = useState<string>()
  const [page, setPage] = useState(1)

  const isReady = query.isSuccess
  const rows = isReady ? query.data : []
  const categories = [...new Set(rows.map(row => row.category))]
  const normalizedSearch = search.trim().toLowerCase()
  const filtered = rows.filter(row => (
    (row.product_name.toLowerCase().includes(normalizedSearch) || row.sku_code.toLowerCase().includes(normalizedSearch))
    && (!category || row.category === category)
    && (!status || productStatus(row.status) === status)
    && (!inventory || inventoryStatus(row.available_qty) === inventory)
  ))
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const visiblePage = Math.min(page, pages)

  function clearFilters() {
    setSearch("")
    setCategory(undefined)
    setStatus(undefined)
    setInventory(undefined)
    setPage(1)
  }

  function retry() {
    void query.refetch()
  }

  const errorStatus = query.error instanceof ApiError ? query.error.status : undefined
  const emptyContent = query.isPending || query.isRefetching
    ? <span role="status"><Spin size="small" /> 正在加载商品</span>
    : query.isError
      ? errorStatus === 403
        ? <Alert type="warning" showIcon message="没有权限查看当前店铺的商品" description="如需访问，请联系组织管理员。" />
        : errorStatus === 401
          ? <Alert type="warning" showIcon message="登录状态已失效" action={<Button onClick={onSignIn}>重新登录</Button>} />
          : <Alert type="error" showIcon message="商品数据加载失败" action={<Button aria-label="重试" onClick={retry}>重试</Button>} />
      : rows.length === 0
        ? <span>当前店铺暂无商品</span>
        : <div className={styles.noResults}>
            <span>没有符合条件的商品</span>
            <Button onClick={clearFilters}>清除筛选</Button>
          </div>

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span><span aria-current="page">商品运营</span>
      </nav>
      <h1>商品运营</h1>
      <p className={styles.summary}>查看当前店铺的商品、规格、库存及价格。</p>

      <section aria-labelledby="catalog-heading">
        <div className={styles.sectionBar}><h2 id="catalog-heading">商品 / SKU</h2></div>
        <div className={styles.panel}>
          <div className={styles.toolbar}>
            <Input
              className={styles.search} type="search" prefix={<SearchOutlined aria-hidden="true" />}
              aria-label="搜索商品或 SKU" placeholder="搜索商品或 SKU" value={search} disabled={!isReady}
              onChange={event => { setSearch(event.target.value); setPage(1) }}
            />
            <Select
              aria-label="商品分类" value={category ?? ""} disabled={!isReady} virtual={false}
              options={[{ value: "", label: "全部分类" }, ...categories.map(value => ({ value, label: value }))]}
              onChange={value => { setCategory(value || undefined); setPage(1) }}
            />
            <Select
              aria-label="商品状态" value={status ?? ""} disabled={!isReady} virtual={false}
              options={[{ value: "", label: "全部商品状态" }, ...Object.entries(statusLabels).map(([value, label]) => ({ value, label }))]}
              onChange={value => { setStatus(value || undefined); setPage(1) }}
            />
            <Select
              aria-label="库存状态" value={inventory ?? ""} disabled={!isReady} virtual={false}
              options={[{ value: "", label: "全部库存状态" }, ...Object.entries(inventoryLabels).map(([value, label]) => ({ value, label }))]}
              onChange={value => { setInventory(value || undefined); setPage(1) }}
            />
            {isReady && <span className={styles.shopCount}>当前店铺 · {rows.length} 个 SKU</span>}
          </div>
          <Table<CatalogSku>
            className={styles.table} rowKey="sku_code" columns={columns} pagination={false} tableLayout="fixed"
            dataSource={filtered.slice((visiblePage - 1) * pageSize, visiblePage * pageSize)}
            locale={{ emptyText: emptyContent }}
          />
          {isReady && <footer className={styles.footer}>
            <span aria-live="polite">共 {filtered.length} 条记录</span>
            <nav className={styles.pager} aria-label="商品列表分页">
              <Button size="small" disabled={visiblePage === 1} onClick={() => setPage(visiblePage - 1)}>上一页</Button>
              <span className={styles.pageNumber} aria-current="page">{visiblePage}</span>
              <Button size="small" disabled={visiblePage === pages} onClick={() => setPage(visiblePage + 1)}>下一页</Button>
            </nav>
          </footer>}
        </div>
      </section>
    </main>
  )
}
