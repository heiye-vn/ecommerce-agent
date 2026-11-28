import { InboxOutlined, SearchOutlined } from "@ant-design/icons"
import { useQueryClient } from "@tanstack/react-query"
import { Alert, App as AntApp, Button, Form, Input, InputNumber, Modal, Select, Spin, Table } from "antd"
import type { TableColumnsType } from "antd"
import { useState } from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom"

import { ApiError } from "../../api/client"
import {
  applyInventoryMovementRequest,
  cancelPriceChangeRequest,
  submitPriceChangeRequest,
  useCatalogQuery,
  useInventoryMovementsQuery,
  useInventoryOverviewQuery,
  usePriceChangeDetailQuery,
  usePriceChangesQuery,
  usePriceHistoriesQuery,
  type CatalogSku,
  type InventoryMovementItem,
  type InventoryMovementType,
  type InventoryMutationType,
  type PriceChangeStatus,
  type PriceChangeSummary,
  type PriceHistoryItem,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import styles from "./index.module.css"

type CatalogPageProps = ShopReadProps & {
  shopRoles?: string[]
  userId?: number
}

const pageSize = 6
const statusLabels = { active: "在售", inactive: "已下架", other: "其他状态" }
const inventoryLabels = { available: "有库存", empty: "缺货", missing: "未记录" }
const movementLabels: Record<InventoryMovementType, string> = {
  opening_balance: "期初建账",
  reserve: "预占",
  release: "释放",
  fulfill: "履约扣减",
  restock: "入库",
  adjust: "调整",
}
const movementToneMap: Record<InventoryMovementType, string> = {
  opening_balance: "pill",
  reserve: "pillPending",
  release: "activePill",
  fulfill: "pill",
  restock: "inventoryPill",
  adjust: "pill",
}

const priceChangeStatusLabels: Record<PriceChangeStatus, string> = {
  pending_approval: "待审批",
  approved: "已批准待执行",
  processing: "执行中",
  succeeded: "执行成功",
  rejected: "已驳回",
  cancelled: "已撤回",
  failed: "复核失败",
}

const priceAuditLabels: Record<string, string> = {
  "price_change.submitted": "提交调价申请",
  "price_change.cancelled": "申请人撤回调价",
  "approval.approved": "人工审批通过",
  "approval.rejected": "人工审批驳回",
  "price_change.succeeded": "复核通过并更新售价",
  "price_change.execution_failed": "当前售价与审批快照不一致",
  "job.dead_lettered": "后台任务进入死信队列",
  "job.manual_retry": "人工按原操作重新投递",
}

const yuan = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const dateTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
})

function inventoryStatus(quantity: number | null) {
  return quantity === null ? "missing" : quantity === 0 ? "empty" : "available"
}

function productStatus(status: string) {
  return status === "active" || status === "inactive" ? status : "other"
}

function quantityCell(quantity: number | null) {
  return quantity === null ? <span aria-label="未记录">—</span> : `${quantity} 件`
}

function signedDelta(delta: number) {
  if (delta > 0) return `+${delta}`
  return String(delta)
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

const movementColumns: TableColumnsType<InventoryMovementItem> = [
  {
    title: "发生时间",
    width: "16%",
    dataIndex: "created_at",
    className: styles.number,
    render: (value: string) => dateTime.format(new Date(value)),
  },
  {
    title: "变动类型",
    width: "14%",
    dataIndex: "movement_type",
    render: (type: InventoryMovementType) => {
      const clsName = movementToneMap[type] ?? "pill"
      return <span className={styles[clsName as keyof typeof styles] ?? styles.pill}>{movementLabels[type] ?? type}</span>
    },
  },
  {
    title: "可售变化",
    width: "12%",
    dataIndex: "available_delta",
    align: "right",
    className: styles.number,
    render: signedDelta,
  },
  {
    title: "预占变化",
    width: "12%",
    dataIndex: "reserved_delta",
    align: "right",
    className: styles.number,
    render: signedDelta,
  },
  {
    title: "快照（可售/预占）",
    width: "18%",
    className: styles.number,
    render: (_, row) => (
      row.after_available !== null && row.after_reserved !== null
        ? `${row.after_available} / ${row.after_reserved}`
        : `${row.quantity} 件`
    ),
  },
  {
    title: "业务引用",
    width: "28%",
    dataIndex: "reference_id",
    ellipsis: true,
    className: styles.code,
  },
]

function InventoryWorkspace({
  shopRoles = ["operator"],
  initialSku,
  ...props
}: CatalogPageProps & { initialSku?: string }) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [selectedSku, setSelectedSku] = useState(initialSku ?? "")
  const [movementType, setMovementType] = useState("")
  const [modalOpen, setModalOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [modalError, setModalError] = useState<string | null>(null)
  const [form] = Form.useForm<{
    movement_type: InventoryMutationType
    quantity: number
    reference_id: string
    note?: string
  }>()

  const overviewQuery = useInventoryOverviewQuery(props, true)
  const items = overviewQuery.isSuccess ? overviewQuery.data.items : []
  const activeSku = selectedSku && items.some(item => item.sku_code === selectedSku)
    ? selectedSku
    : items[0]?.sku_code ?? ""
  const currentSnapshot = items.find(item => item.sku_code === activeSku) ?? null
  const lowStockItems = items.filter(item => item.low_stock)
  const movementsQuery = useInventoryMovementsQuery(props, activeSku, movementType, Boolean(activeSku))
  const canOperate = shopRoles.includes("operator") || shopRoles.includes("admin")

  function openMutationModal() {
    setModalError(null)
    form.setFieldsValue({
      movement_type: "reserve",
      quantity: 1,
      reference_id: `INV-${Date.now().toString().slice(-6)}`,
      note: "",
    })
    setModalOpen(true)
  }

  async function handleSubmitMovement(values: {
    movement_type: InventoryMutationType
    quantity: number
    reference_id: string
    note?: string
  }) {
    if (!currentSnapshot) return
    setModalError(null)
    setSubmitting(true)
    try {
      const idemKey = `inv-${props.shopId}-${currentSnapshot.sku_code}-${Date.now()}`
      const res = await applyInventoryMovementRequest(
        props,
        currentSnapshot.sku_code,
        {
          movement_type: values.movement_type,
          quantity: values.quantity,
          reference_id: values.reference_id,
          expected_version: currentSnapshot.version ?? undefined,
          note: values.note || undefined,
        },
        idemKey,
      )
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "inventory"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "catalog"] })
      setModalOpen(false)
      message?.success?.(res.message)
    } catch (error) {
      setModalError(error instanceof ApiError ? error.message : "库存操作失败，请稍后重试。")
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "inventory"] })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section aria-labelledby="inventory-heading">
      <div className={styles.inventoryToolbarRow}>
        <div className={styles.sectionBar} style={{ flex: 1, marginBottom: 0 }}>
          <h2 id="inventory-heading">库存与流水</h2>
        </div>
        <div className={styles.inventoryActions}>
          <Select
            className={styles.skuSelect}
            aria-label="选择库存 SKU"
            value={activeSku || undefined}
            disabled={!overviewQuery.isSuccess || items.length === 0}
            virtual={false}
            options={items.map(item => ({
              value: item.sku_code,
              label: `${item.sku_code} · ${item.product_name}`,
            }))}
            onChange={setSelectedSku}
          />
          {canOperate && (
            <Button
              type="primary"
              disabled={!currentSnapshot || currentSnapshot.available_qty === null}
              onClick={openMutationModal}
            >
              库存操作
            </Button>
          )}
        </div>
      </div>

      {overviewQuery.isPending ? (
        <div className={styles.feedback}><Spin size="small" /> 正在加载库存快照…</div>
      ) : overviewQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message="库存数据加载失败"
          action={<Button onClick={() => { void overviewQuery.refetch() }}>重试</Button>}
        />
      ) : (
        <>
          <dl className={styles.snapshotBand}>
            <div className={styles.snapshotHighlight}>
              <dt>可售库存</dt>
              <dd>{currentSnapshot?.available_qty ?? "—"} 件</dd>
              <span>
                当前 SKU · {currentSnapshot?.version ? `快照 V${currentSnapshot.version}` : "未建账"}
              </span>
            </div>
            <div>
              <dt>预占库存</dt>
              <dd>{currentSnapshot?.reserved_qty ?? "—"} 件</dd>
              <span>已预占，非可售</span>
            </div>
            <div>
              <dt>当前库存合计</dt>
              <dd>{currentSnapshot?.total_qty ?? "—"} 件</dd>
              <span>可售 + 预占</span>
            </div>
            <div>
              <dt>库存预警</dt>
              <dd>{overviewQuery.data.low_stock_count} 个 SKU</dd>
              <span>当前店铺 · 可售 &lt; {overviewQuery.data.low_stock_threshold} 件</span>
            </div>
          </dl>

          <div className={styles.inventoryGrid}>
            <div className={styles.panel}>
              <div className={styles.toolbar}>
                <strong className={styles.panelHeading}>不可变库存流水</strong>
                <Select
                  className={styles.filterCompact}
                  aria-label="变动类型"
                  value={movementType}
                  virtual={false}
                  options={[
                    { value: "", label: "全部变动类型" },
                    ...Object.entries(movementLabels).map(([value, label]) => ({ value, label })),
                  ]}
                  onChange={setMovementType}
                />
                {movementsQuery.isSuccess && (
                  <span className={styles.shopCount}>共 {movementsQuery.data.length} 条记录</span>
                )}
              </div>
              <Table<InventoryMovementItem>
                className={styles.table}
                rowKey="id"
                columns={movementColumns}
                pagination={false}
                tableLayout="fixed"
                dataSource={movementsQuery.isSuccess ? movementsQuery.data : []}
                locale={{
                  emptyText: movementsQuery.isPending
                    ? <Spin size="small" />
                    : "当前 SKU 暂无库存流水记录",
                }}
              />
            </div>

            <aside className={styles.sideStack}>
              <section className={styles.sideCard} aria-labelledby="alert-heading">
                <h3 id="alert-heading">库存预警</h3>
                {lowStockItems.length === 0 ? (
                  <p className={styles.okNotice}>当前店铺在售 SKU 库存均高于预警线（{overviewQuery.data.low_stock_threshold} 件）。</p>
                ) : (
                  <ul className={styles.alertList}>
                    {lowStockItems.map(item => (
                      <li key={item.sku_code} className={styles.warnNotice}>
                        <strong>{item.sku_code} · {item.product_name}</strong>
                        <p>当前可售 {item.available_qty} 件，低于预警线 {item.low_stock_threshold} 件。</p>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className={styles.sideCard} aria-labelledby="note-heading">
                <h3 id="note-heading">流水与并发说明</h3>
                <p className={styles.mutedNote}>
                  通知仓库并不等于库存预占；唯有通过行级锁与乐观锁版本号（expected_version）落库的实际预占、释放、履约或入库事务，才会更新快照并写入不可变流水。
                </p>
              </section>
            </aside>
          </div>
        </>
      )}

      <Modal
        title={`库存并发操作 · ${currentSnapshot?.sku_code ?? ""}`}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => { form.submit() }}
        okText="提交变更"
        cancelText="取消"
        confirmLoading={submitting}
      >
        {modalError && (
          <Alert type="error" showIcon message={modalError} style={{ marginBottom: 12 }} />
        )}
        <Form form={form} layout="vertical" onFinish={values => { void handleSubmitMovement(values) }}>
          <Form.Item label="操作类型" name="movement_type" rules={[{ required: true }]}>
            <Select
              virtual={false}
              options={[
                { value: "reserve", label: "预占（可售 → 预占）" },
                { value: "release", label: "释放（预占 → 可售）" },
                { value: "fulfill", label: "履约扣减（扣减预占）" },
                { value: "restock", label: "补货入库（增加可售）" },
              ]}
            />
          </Form.Item>
          <Form.Item label="变更数量" name="quantity" rules={[{ required: true, message: "请输入数量" }]}>
            <InputNumber min={1} max={100000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="业务引用单号" name="reference_id" rules={[{ required: true, message: "请输入单号" }]}>
            <Input placeholder="例如 ORD-20261111-01" />
          </Form.Item>
          <Form.Item label="备注说明" name="note">
            <Input placeholder="可选备注" />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  )
}

function PriceStatusPill({ status }: { status: string }) {
  const cls =
    status === "succeeded"
      ? styles.inventoryPill
      : status === "failed" || status === "rejected"
        ? styles.pillDanger
        : status === "pending_approval" || status === "approved" || status === "processing"
          ? styles.pillPending
          : styles.pill
  return <span className={cls}>{priceChangeStatusLabels[status as PriceChangeStatus] ?? status}</span>
}

const historyColumns: TableColumnsType<PriceHistoryItem> = [
  {
    title: "生效时间",
    width: "20%",
    dataIndex: "changed_at",
    className: styles.number,
    render: (value: string) => dateTime.format(new Date(value)),
  },
  {
    title: "原价",
    width: "14%",
    dataIndex: "old_price",
    align: "right",
    className: styles.number,
    render: (v: string) => `¥${yuan.format(Number(v))}`,
  },
  {
    title: "新价",
    width: "14%",
    dataIndex: "new_price",
    align: "right",
    className: styles.number,
    render: (v: string) => `¥${yuan.format(Number(v))}`,
  },
  {
    title: "变更人",
    width: "18%",
    dataIndex: "actor_name",
    render: (name: string | null) => name || "沙箱初始化",
  },
  {
    title: "审批单 / 说明",
    width: "34%",
    render: (_, row) => (
      <span className={styles.code}>
        {row.approval_no ?? row.reason ?? "—"}
      </span>
    ),
  },
]

const priceRequestColumns: TableColumnsType<PriceChangeSummary> = [
  {
    title: "申请单 / SKU",
    width: "26%",
    dataIndex: "request_no",
    render: (no: string, row) => (
      <div className={styles.productText}>
        <strong>{no}</strong>
        <span>{row.sku_code} · ¥{row.old_price} → ¥{row.new_price}</span>
      </div>
    ),
  },
  {
    title: "调价原因",
    width: "32%",
    dataIndex: "reason",
    ellipsis: true,
  },
  {
    title: "状态",
    width: "22%",
    dataIndex: "status",
    render: (status: string) => <PriceStatusPill status={status} />,
  },
  {
    title: "操作",
    width: "20%",
    align: "right",
    render: (_, row) => (
      <Link className={styles.actionLink} to={`/catalog/price-changes/${row.id}`}>
        {row.status === "failed" ? "查看原因" : "查看结果"}
      </Link>
    ),
  },
]

function PricingWorkspace({
  shopRoles = ["operator"],
  catalogRows,
  initialSku,
  ...props
}: CatalogPageProps & { catalogRows: CatalogSku[]; initialSku?: string }) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [selectedSku, setSelectedSku] = useState(initialSku ?? "")
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [form] = Form.useForm<{ new_price: number; reason: string }>()

  const activeSku = selectedSku && catalogRows.some(r => r.sku_code === selectedSku)
    ? selectedSku
    : catalogRows[0]?.sku_code ?? ""
  const currentSkuObj = catalogRows.find(r => r.sku_code === activeSku) ?? null

  const historiesQuery = usePriceHistoriesQuery(props, activeSku, true)
  const requestsQuery = usePriceChangesQuery(props, activeSku, true)
  const canOperate = shopRoles.includes("operator") || shopRoles.includes("admin")

  async function handleSubmitPriceChange(values: { new_price: number; reason: string }) {
    if (!currentSkuObj) return
    setFormError(null)
    setSubmitting(true)
    try {
      const idemKey = `prc-${props.shopId}-${currentSkuObj.sku_code}-${values.new_price.toFixed(2)}-${Date.now()}`
      const res = await submitPriceChangeRequest(
        props,
        {
          sku_code: currentSkuObj.sku_code,
          new_price: values.new_price.toFixed(2),
          reason: values.reason.trim(),
        },
        idemKey,
      )
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "price-changes"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      form.resetFields()
      message?.success?.(res.message)
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : "提交调价审批失败，请稍后重试。")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section aria-labelledby="pricing-heading">
      <div className={styles.inventoryToolbarRow}>
        <div className={styles.sectionBar} style={{ flex: 1, marginBottom: 0 }}>
          <h2 id="pricing-heading">调价记录与申请</h2>
        </div>
        <div className={styles.inventoryActions}>
          <Select
            className={styles.skuSelect}
            aria-label="选择调价 SKU"
            value={activeSku || undefined}
            disabled={catalogRows.length === 0}
            virtual={false}
            options={catalogRows.map(item => ({
              value: item.sku_code,
              label: `${item.sku_code} · ${item.product_name}（¥${item.current_price}）`,
            }))}
            onChange={setSelectedSku}
          />
        </div>
      </div>

      <div className={styles.inventoryGrid}>
        <div className={styles.sideStack}>
          <div className={styles.panel}>
            <div className={styles.toolbar}>
              <strong className={styles.panelHeading}>价格历史</strong>
              {historiesQuery.isSuccess && (
                <span className={styles.shopCount}>共 {historiesQuery.data.length} 条记录</span>
              )}
            </div>
            <Table<PriceHistoryItem>
              className={styles.table}
              rowKey="id"
              columns={historyColumns}
              pagination={false}
              tableLayout="fixed"
              dataSource={historiesQuery.isSuccess ? historiesQuery.data : []}
              locale={{
                emptyText: historiesQuery.isPending ? <Spin size="small" /> : "当前 SKU 暂无价格历史记录",
              }}
            />
          </div>

          <div className={styles.panel}>
            <div className={styles.toolbar}>
              <strong className={styles.panelHeading}>调价审批与执行记录</strong>
              {requestsQuery.isSuccess && (
                <span className={styles.shopCount}>共 {requestsQuery.data.length} 笔申请</span>
              )}
            </div>
            <Table<PriceChangeSummary>
              className={styles.table}
              rowKey="id"
              columns={priceRequestColumns}
              pagination={false}
              tableLayout="fixed"
              dataSource={requestsQuery.isSuccess ? requestsQuery.data : []}
              locale={{
                emptyText: requestsQuery.isPending ? <Spin size="small" /> : "当前 SKU 暂无调价申请记录",
              }}
            />
          </div>
        </div>

        <aside className={styles.sideStack}>
          <section className={styles.sideCard} aria-labelledby="new-price-heading">
            <div className={styles.detailHeader} style={{ marginBottom: 12 }}>
              <h3 id="new-price-heading" style={{ margin: 0 }}>发起调价</h3>
              <span className={styles.pillPending}>需要审批</span>
            </div>
            {currentSkuObj && (
              <p className={styles.pricingFormHint}>
                当前对象：{currentSkuObj.sku_code}（{currentSkuObj.product_name}），当前售价{" "}
                <strong>¥{currentSkuObj.current_price}</strong>。提交后沿用统一审批流程，批准后重新复核 SKU 与售价。
              </p>
            )}
            {formError && <Alert type="error" showIcon message={formError} style={{ marginBottom: 12 }} />}
            {canOperate ? (
              <Form form={form} layout="vertical" onFinish={values => { void handleSubmitPriceChange(values) }}>
                <Form.Item label="拟调整售价（元）" name="new_price" rules={[{ required: true, message: "请输入目标售价" }]}>
                  <InputNumber min={0.01} max={999999} step={1} precision={2} style={{ width: "100%" }} placeholder="例如 189.00" />
                </Form.Item>
                <Form.Item label="调价原因" name="reason" rules={[{ required: true, min: 2, message: "请填写调价原因" }]}>
                  <Input placeholder="例如：调整冬季常规售价" maxLength={500} />
                </Form.Item>
                <Button type="primary" htmlType="submit" block loading={submitting} disabled={!currentSkuObj}>
                  提交调价审批
                </Button>
              </Form>
            ) : (
              <Alert type="info" showIcon message="当前店铺角色仅支持只读查看调价历史。" />
            )}
          </section>
        </aside>
      </div>
    </section>
  )
}

function PriceChangeDetailView({
  requestId,
  shopRoles = ["operator"],
  userId = 0,
  ...props
}: CatalogPageProps & { requestId: string }) {
  const { message } = AntApp.useApp()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const valid = /^[1-9]\d{0,9}$/.test(requestId) && Number(requestId) <= 2_147_483_647
  const query = usePriceChangeDetailQuery(props, requestId, valid)
  const detail = query.isSuccess ? query.data : undefined
  const [cancelling, setCancelling] = useState(false)

  const canCancel = Boolean(
    detail && detail.status === "pending_approval" && (detail.requester_id === userId || shopRoles.includes("admin")),
  )

  async function handleCancel() {
    if (!detail) return
    setCancelling(true)
    try {
      await cancelPriceChangeRequest(props, detail.id)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "price-changes"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      message?.success?.("已撤回该调价审批申请")
    } catch (error) {
      message?.error?.(error instanceof ApiError ? error.message : "撤回调价失败，请重试。")
    } finally {
      setCancelling(false)
    }
  }

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span>
        <Link to="/catalog?tab=pricing">商品运营</Link><span aria-hidden="true">/</span>
        <strong aria-current="page">{detail?.status === "failed" ? "调价复核失败" : "调价执行详情"}</strong>
      </nav>
      <div className={styles.detailHeader}>
        <div>
          <h1>{detail?.status === "failed" ? "调价复核失败" : "调价执行结果"}</h1>
          <p className={styles.summary}>
            {detail ? `${detail.sku_code} · ${detail.product_name} · ${detail.request_no}` : "查看调价审批快照、执行时间线与当前售价核对。"}
          </p>
        </div>
        <div className={styles.headerActions}>
          {detail && <PriceStatusPill status={detail.status} />}
          <Link className={styles.backLink} to="/catalog?tab=pricing">返回调价记录</Link>
        </div>
      </div>

      {!valid || !detail ? (
        <div className={styles.feedback}>
          <Spin size="small" /> 正在加载调价记录…
        </div>
      ) : (
        <>
          {detail.status === "failed" && detail.failure_reason && (
            <Alert
              type="error"
              showIcon
              message="调价复核失败"
              description={detail.failure_reason}
              style={{ marginBottom: 16 }}
            />
          )}
          <div className={styles.inventoryGrid}>
            <section className={styles.sideCard} aria-labelledby="price-timeline-heading">
              <div className={styles.detailHeader} style={{ marginBottom: 12 }}>
                <h2 id="price-timeline-heading" style={{ margin: 0, fontSize: 16 }}>执行时间线</h2>
                <span className={styles.pill}>{detail.request_no}</span>
              </div>
              <ol className={styles.timeline}>
                {detail.audit_logs.map(log => (
                  <li key={log.id} className={styles.timelineItem}>
                    <div>
                      <strong>{priceAuditLabels[log.action] ?? log.action}</strong>
                      <p>操作人：{log.actor_name}</p>
                    </div>
                    <span className={styles.timelineTime}>{dateTime.format(new Date(log.created_at))}</span>
                  </li>
                ))}
              </ol>
            </section>

            <aside className={styles.sideCard} aria-labelledby="price-step-heading">
              <h2 id="price-step-heading" style={{ margin: "0 0 12px", fontSize: 16 }}>价格与下一步</h2>
              <dl className={styles.priceFactList}>
                <div><dt>原审批价</dt><dd>¥{detail.old_price}</dd></div>
                <div><dt>拟调整售价</dt><dd>¥{detail.new_price}</dd></div>
                <div><dt>当前实时售价</dt><dd>¥{detail.current_price}</dd></div>
                <div><dt>价格流水</dt><dd>{detail.status === "succeeded" ? "已追加历史记录" : "未新增本次价格变更"}</dd></div>
              </dl>
              <div className={styles.headerActions} style={{ marginTop: 14, flexWrap: "wrap" }}>
                {detail.status === "failed" && (
                  <Button
                    type="primary"
                    onClick={() => navigate(`/catalog?tab=pricing&sku=${detail.sku_code}`)}
                  >
                    按最新售价重新发起
                  </Button>
                )}
                {canCancel && (
                  <Button loading={cancelling} onClick={() => { void handleCancel() }}>
                    撤回调价申请
                  </Button>
                )}
                {detail.approval_id && (
                  <Link className={styles.backLink} to={`/approvals/${detail.approval_id}`}>查看审批单</Link>
                )}
                {detail.job_id && (
                  <Link className={styles.backLink} to={`/tasks/${detail.job_id}`}>查看执行任务</Link>
                )}
              </div>
            </aside>
          </div>
        </>
      )}
    </main>
  )
}

export default function CatalogPage(props: CatalogPageProps) {
  const { requestId } = useParams()
  if (requestId !== undefined) {
    return <PriceChangeDetailView key={`${props.shopId}/prc/${requestId}`} requestId={requestId} {...props} />
  }
  return <CatalogWorkspace key={props.shopId} {...props} />
}

function CatalogWorkspace(props: CatalogPageProps) {
  const { onSignIn } = props
  const [searchParams] = useSearchParams()
  const tabParam = searchParams.get("tab")
  const initialTab: "overview" | "inventory" | "pricing" =
    tabParam === "inventory" ? "inventory" : tabParam === "pricing" ? "pricing" : "overview"
  const initialSku = searchParams.get("sku") ?? undefined
  const [activeTab, setActiveTab] = useState<"overview" | "inventory" | "pricing">(initialTab)
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
      <p className={styles.summary}>查看当前店铺的商品、规格、库存快照与不可变库存流水。</p>

      <div className={styles.viewTabs} role="tablist" aria-label="商品运营子视图">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "overview"}
          className={`${styles.viewTab} ${activeTab === "overview" ? styles.viewTabActive : ""}`}
          onClick={() => setActiveTab("overview")}
        >
          商品概览
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "inventory"}
          className={`${styles.viewTab} ${activeTab === "inventory" ? styles.viewTabActive : ""}`}
          onClick={() => setActiveTab("inventory")}
        >
          库存与流水
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "pricing"}
          className={`${styles.viewTab} ${activeTab === "pricing" ? styles.viewTabActive : ""}`}
          onClick={() => setActiveTab("pricing")}
        >
          调价记录
        </button>
      </div>

      {activeTab === "inventory" ? (
        <InventoryWorkspace {...props} initialSku={initialSku} />
      ) : activeTab === "pricing" ? (
        <PricingWorkspace {...props} catalogRows={rows} initialSku={initialSku} />
      ) : (
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
      )}
    </main>
  )
}
