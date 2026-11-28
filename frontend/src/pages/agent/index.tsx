import { ArrowUpOutlined, CheckOutlined, FileTextOutlined, ThunderboltOutlined } from "@ant-design/icons"
import { useQueryClient } from "@tanstack/react-query"
import { Alert, App as AntApp, Button, Input, Spin } from "antd"
import { useEffect, useRef, useState, type FormEvent } from "react"
import { Link, useSearchParams } from "react-router-dom"

import { ApiError, fetchShopJson } from "../../api/client"
import {
  cancelPriceChangeRequest,
  cancelPromotionRequest,
  cancelRefundRequest,
  submitPriceChangeRequest,
  submitPromotionRequest,
  submitRefundRequest,
  type KnowledgeCitationItem,
  type PriceChangeSubmitResponse,
  type PromotionSubmitResponse,
  type RefundSubmitResponse,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import styles from "./index.module.css"

type Sales = {
  sku_code: string
  units_sold: number
  paid_orders: number
  start_date: string
  end_date: string
  timezone: string
}

type Promotion = {
  sku_code: string
  original_price: string
  cost_price?: string | null
  promotion_price: string
  below_cost?: boolean
  risk_warnings?: string[]
  discount: string
  duration_days: number
  available_qty: number | null
  minimum_qty: number
  starts_at: string | null
  ends_at: string | null
  timezone?: string
  conflict_check: "clear" | "conflict" | "unavailable"
  conflicting_promotions?: string[]
  plan_key?: string | null
}

type OrderDiagnosticItem = {
  order_id: number
  order_no: string
  status: string
  fulfillment_status: string
  exception_type: string | null
  exception_note: string | null
  paid_amount: string
  refunded_amount: string
  refundable_amount: string
  can_refund: boolean
  refund_block_reason: string | null
  sku_codes: string[]
}

type OrderQueryResult = {
  mode: "detail" | "exception_list"
  items: OrderDiagnosticItem[]
}

type RefundPreview = {
  order_id: number
  order_no: string
  order_status: string
  paid_amount: string
  refunded_amount: string
  in_flight_amount: string
  refundable_amount: string
  proposed_amount: string
  reason: string
  projected_order_status: string
  eligible: boolean
  block_reason: string | null
  plan_key: string | null
}

type PriceChangePreview = {
  sku_id: number
  sku_code: string
  product_name: string
  sku_status: string
  current_price: string
  cost_price: string
  proposed_price: string
  change_amount: string
  change_pct: string
  reason: string
  last_old_price?: string | null
  last_new_price?: string | null
  last_changed_at?: string | null
  eligible: boolean
  block_reason: string | null
  plan_key: string | null
}

type KnowledgeQaResult = {
  citations: KnowledgeCitationItem[]
  vector_candidates: number
  lexical_candidates: number
  vector_degraded: boolean
  rerank_degraded: boolean
  embedding_model?: string | null
  rerank_model?: string | null
  isolation_notice: string
}

type Answer = {
  kind:
    | "sales"
    | "clarify"
    | "unsupported"
    | "not_found"
    | "promotion_clarify"
    | "promotion_preview"
    | "promotion_blocked"
    | "promotion_submitted"
    | "price_change_clarify"
    | "price_change_preview"
    | "price_change_blocked"
    | "price_change_submitted"
    | "order_info"
    | "refund_clarify"
    | "refund_preview"
    | "refund_blocked"
    | "refund_submitted"
    | "knowledge_qa"
  message: string
  session_id?: number | null
  task_id?: number | null
  tool_calls: number
  sales: Sales | null
  promotion?: Promotion | null
  submission?: PromotionSubmitResponse | null
  price_change?: PriceChangePreview | null
  price_change_submission?: PriceChangeSubmitResponse | null
  order_result?: OrderQueryResult | null
  refund?: RefundPreview | null
  refund_submission?: RefundSubmitResponse | null
  knowledge?: KnowledgeQaResult | null
}

type Failure = { kind: "expired" | "denied" | "unavailable"; message?: string }
type Turn = { question: string; answer: Answer | null }
const example = "查一下 SKU-001 最近 7 天销量"
const promotionExample = "帮我看看 SKU-001 最近 7 天销量，如果库存充足，就创建一个 8 折、持续 3 天的促销。"
const priceChangeExample = "把 SKU-001 常规售价调到 189 元，原因调整冬季常规售价"
const orderDiagExample = "帮我看下有哪些异常订单需要处理"
const refundExample = "给订单 ORD-20261111-01 退 89 元，原因部分商品售后"
const knowledgeExample = "审批通过之后，如果 SKU 的售价变了，系统还能按原方案排期吗？"
const basis = "近 7 个完整自然日；按已支付订单明细数量汇总，不扣除后续退款。"

function period(sales: Sales) {
  return `${sales.start_date} 00:00（含）至 ${sales.end_date} 00:00（不含）`
}

function localTime(value: string) {
  return value.slice(0, 16).replace("T", " ")
}

export default function AgentPage({ shopName, ...props }: ShopReadProps & { shopName: string }) {
  const { message: toast } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const [message, setMessage] = useState(() => searchParams.get("q") ?? "")
  const [turn, setTurn] = useState<Turn | null>(null)
  const [promotionStart, setPromotionStart] = useState<Turn | null>(null)
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [sessionShopId, setSessionShopId] = useState<number | null>(null)
  const [awaitingFollowUp, setAwaitingFollowUp] = useState<"promotion" | "refund" | "price_change" | null>(null)
  const [submission, setSubmission] = useState<PromotionSubmitResponse | null>(null)
  const [refundSubmission, setRefundSubmission] = useState<RefundSubmitResponse | null>(null)
  const [priceSubmission, setPriceSubmission] = useState<PriceChangeSubmitResponse | null>(null)
  const [selectedCitationIdx, setSelectedCitationIdx] = useState(0)
  const [submittingPlan, setSubmittingPlan] = useState(false)
  const [cancellingPlan, setCancellingPlan] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [failure, setFailure] = useState<Failure | null>(null)
  const [pending, setPending] = useState(false)
  const active = useRef<AbortController | null>(null)

  useEffect(() => () => active.current?.abort(), [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const question = message.trim()
    if (!question || active.current) return
    const controller = new AbortController()
    active.current = controller
    setPending(true)
    setFailure(null)
    setActionError(null)
    if (awaitingFollowUp !== "promotion") setPromotionStart(null)
    setTurn({ question, answer: null })
    try {
      const answer = await fetchShopJson<Answer>("agent/query", props, {
        signal: controller.signal,
        payload: {
          message: question,
          ...(sessionId !== null && { session_id: sessionId }),
        },
      })
      if (controller.signal.aborted) return
      const completed = { question, answer }
      setTurn(completed)
      setSelectedCitationIdx(0)
      if (answer.session_id) {
        setSessionId(answer.session_id)
        if (sessionShopId === null) setSessionShopId(props.shopId)
      }
      if (answer.submission) {
        setSubmission(answer.submission)
        setRefundSubmission(null)
        setPriceSubmission(null)
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "promotions"] })
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      } else if (answer.refund_submission) {
        setRefundSubmission(answer.refund_submission)
        setSubmission(null)
        setPriceSubmission(null)
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "refunds"] })
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "orders"] })
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      } else if (answer.price_change_submission) {
        setPriceSubmission(answer.price_change_submission)
        setSubmission(null)
        setRefundSubmission(null)
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "price-changes"] })
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "catalog"] })
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      } else if (answer.kind !== "unsupported") {
        setSubmission(null)
        setRefundSubmission(null)
        setPriceSubmission(null)
      }
      if (!answer.promotion && !answer.submission) {
        setPromotionStart(null)
      } else if (awaitingFollowUp !== "promotion" && answer.kind === "promotion_clarify") {
        setPromotionStart(completed)
      }
      if (answer.kind === "promotion_clarify") {
        setAwaitingFollowUp("promotion")
      } else if (answer.kind === "refund_clarify") {
        setAwaitingFollowUp("refund")
      } else if (answer.kind === "price_change_clarify") {
        setAwaitingFollowUp("price_change")
      } else {
        setAwaitingFollowUp(null)
      }
      setMessage("")
    } catch (error) {
      if (controller.signal.aborted) return
      if (error instanceof ApiError) {
        setFailure(
          error.status === 401
            ? { kind: "expired" }
            : error.status === 403
              ? { kind: "denied", message: error.message }
              : { kind: "unavailable", message: error.message },
        )
      } else {
        setFailure({ kind: "unavailable" })
      }
    } finally {
      active.current = null
      if (!controller.signal.aborted) setPending(false)
    }
  }

  async function handleConfirmSubmit() {
    if (!promotion || !promotion.starts_at) return
    setActionError(null)
    setSubmittingPlan(true)
    try {
      const prefix = sessionId !== null ? `agent-session-${sessionId}` : "agent-plan"
      const key = promotion.plan_key ? `${prefix}-plan-${promotion.plan_key}` : undefined
      const created = await submitPromotionRequest(
        props,
        {
          sku_code: promotion.sku_code,
          discount: promotion.discount,
          start_date: promotion.starts_at.slice(0, 10),
          duration_days: promotion.duration_days,
        },
        key,
      )
      setSubmission(created)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "promotions"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      toast?.success?.(
        created.replayed
          ? `已命中幂等保护，返回原审批单 ${created.approval_no}`
          : `已提交人工审批（审批单 ${created.approval_no}）`,
      )
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "提交审批失败，请稍后重试。")
    } finally {
      setSubmittingPlan(false)
    }
  }

  async function handleConfirmRefundSubmit() {
    if (!refundPreview || !refundPreview.eligible) return
    setActionError(null)
    setSubmittingPlan(true)
    try {
      const prefix = sessionId !== null ? `agent-session-${sessionId}` : "agent-refund"
      const key = refundPreview.plan_key ? `${prefix}-refund-${refundPreview.plan_key}` : undefined
      const created = await submitRefundRequest(
        props,
        {
          order_id: refundPreview.order_id,
          amount: refundPreview.proposed_amount,
          reason: refundPreview.reason,
        },
        key,
      )
      setRefundSubmission(created)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "refunds"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "orders"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      toast?.success?.(
        created.replayed
          ? `已命中幂等保护，返回原退款审批单 ${created.approval_no}`
          : `已提交退款审批（审批单 ${created.approval_no}）`,
      )
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "提交退款审批失败，请稍后重试。")
    } finally {
      setSubmittingPlan(false)
    }
  }

  async function handleWithdrawSubmission() {
    if (!submission) return
    setActionError(null)
    setCancellingPlan(true)
    try {
      const updated = await cancelPromotionRequest(props, submission.promotion_id)
      setSubmission({ ...submission, status: updated.status, approval_status: "cancelled" })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "promotions"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      toast?.success?.("已撤回该待审批申请")
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "撤回失败，请重试。")
    } finally {
      setCancellingPlan(false)
    }
  }

  async function handleWithdrawRefundSubmission() {
    if (!refundSubmission) return
    setActionError(null)
    setCancellingPlan(true)
    try {
      const updated = await cancelRefundRequest(props, refundSubmission.refund_id)
      setRefundSubmission({ ...refundSubmission, status: updated.status, approval_status: "cancelled" })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "refunds"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "orders"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      toast?.success?.("已撤回该退款审批申请")
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "撤回失败，请重试。")
    } finally {
      setCancellingPlan(false)
    }
  }

  async function handleConfirmPriceChangeSubmit() {
    if (!pricePreview || !pricePreview.eligible) return
    setActionError(null)
    setSubmittingPlan(true)
    try {
      const prefix = sessionId !== null ? `agent-session-${sessionId}` : "agent-price"
      const key = pricePreview.plan_key ? `${prefix}-price-${pricePreview.plan_key}` : undefined
      const created = await submitPriceChangeRequest(
        props,
        {
          sku_code: pricePreview.sku_code,
          new_price: pricePreview.proposed_price,
          reason: pricePreview.reason,
        },
        key,
      )
      setPriceSubmission(created)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "price-changes"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "catalog"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      toast?.success?.(
        created.replayed
          ? `已命中幂等保护，返回原调价审批单 ${created.approval_no}`
          : `已提交调价审批（审批单 ${created.approval_no}）`,
      )
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "提交调价审批失败，请稍后重试。")
    } finally {
      setSubmittingPlan(false)
    }
  }

  async function handleWithdrawPriceChangeSubmission() {
    if (!priceSubmission) return
    setActionError(null)
    setCancellingPlan(true)
    try {
      const updated = await cancelPriceChangeRequest(props, priceSubmission.request_id)
      setPriceSubmission({ ...priceSubmission, status: updated.status, approval_status: "cancelled" })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "price-changes"] })
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      toast?.success?.("已撤回该调价审批申请")
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "撤回失败，请重试。")
    } finally {
      setCancellingPlan(false)
    }
  }

  function reset() {
    setTurn(null)
    setPromotionStart(null)
    setSessionId(null)
    setSessionShopId(null)
    setAwaitingFollowUp(null)
    setSubmission(null)
    setRefundSubmission(null)
    setPriceSubmission(null)
    setSelectedCitationIdx(0)
    setActionError(null)
    setMessage("")
    setFailure(null)
  }

  const answer = turn?.answer
  const sales = answer ? answer.sales : awaitingFollowUp === "promotion" ? promotionStart?.answer?.sales : null
  const promotion = answer ? answer.promotion : awaitingFollowUp === "promotion" ? promotionStart?.answer?.promotion : null
  const refundPreview = answer?.refund ?? null
  const pricePreview = answer?.price_change ?? null
  const orderResult = answer?.order_result ?? null
  const knowledgeResult = answer?.knowledge ?? null
  const citations = knowledgeResult?.citations ?? []
  const selectedCitation = citations[selectedCitationIdx] ?? citations[0] ?? null
  const awaitingDate = answer?.kind === "promotion_clarify"
  const awaitingRefundDetails = answer?.kind === "refund_clarify"
  const awaitingPriceDetails = answer?.kind === "price_change_clarify"
  const showEarlierTurn = promotionStart && promotionStart !== turn && (awaitingFollowUp === "promotion" || answer?.promotion)
  const crossShopActive = sessionId !== null && sessionShopId !== null && sessionShopId !== props.shopId
  const riskWarnings = promotion?.risk_warnings ?? []
  const canConfirmPreview = Boolean(
    answer?.kind === "promotion_preview"
    && promotion?.starts_at
    && promotion.ends_at
    && promotion.conflict_check === "clear"
    && !submission
    && !crossShopActive,
  )
  const canConfirmRefund = Boolean(
    answer?.kind === "refund_preview"
    && refundPreview?.eligible
    && !refundSubmission
    && !crossShopActive,
  )
  const canConfirmPrice = Boolean(
    answer?.kind === "price_change_preview"
    && pricePreview?.eligible
    && !priceSubmission
    && !crossShopActive,
  )

  const progressStage = submission || refundSubmission || priceSubmission
    ? (submission?.status === "cancelled" || refundSubmission?.status === "cancelled" || priceSubmission?.status === "cancelled") ? 2 : 3
    : answer?.kind === "promotion_preview" || answer?.kind === "refund_preview" || answer?.kind === "price_change_preview"
      ? 2
      : sales || promotion || refundPreview || pricePreview
        ? 1
        : 0

  const headerTitle = submission
    ? `${submission.sku_code} 促销审批`
    : refundSubmission
      ? `${refundSubmission.order_no} 退款审批`
      : priceSubmission
        ? `${priceSubmission.sku_code} 调价审批`
        : pricePreview
          ? `${pricePreview.sku_code} 调价方案`
          : refundPreview
            ? `${refundPreview.order_no} 退款方案`
            : orderResult
              ? "订单售后与异常排查"
              : knowledgeResult && turn
                ? (turn.question.length > 22 ? `${turn.question.slice(0, 22)}…` : turn.question)
                : promotion
                  ? `${promotion.sku_code} 促销方案`
                  : sales
                    ? `查询 ${sales.sku_code} 近 7 天销量`
                    : "查数、促销、调价、售后与规则问答"

  const headerBadge = submission || refundSubmission || priceSubmission
    ? "待人工审批"
    : promotion || refundPreview || pricePreview
      ? "方案生成"
      : knowledgeResult
        ? "知识库只读问答"
        : "只读查询"

  return <main className={styles.page}>
    <nav className={styles.breadcrumb} aria-label="面包屑"><span>运营工作台</span><span aria-hidden="true">/</span><strong aria-current="page">Agent 对话</strong></nav>
    <h1>Agent 对话</h1>
    <div className={styles.pageActions}>
      <p className={styles.summary}>用自然语言查询业务数据、检索运营规则，或生成促销、调价与退款方案提交人工审批。</p>
      {turn && <Button onClick={reset} disabled={pending}>新建会话</Button>}
    </div>

    <div className={styles.grid}>
      <section className={styles.conversation} aria-labelledby="conversation-heading">
        <header className={styles.panelHeader}>
          <div><h2 id="conversation-heading">{headerTitle}</h2>
            <p>当前店铺 · {shopName}</p></div>
          <span className={styles.pill}>{headerBadge}</span>
        </header>
        <div className={styles.thread}>
          {crossShopActive && (
            <Alert
              type="warning"
              showIcon
              message="当前已切换至其他店铺"
              description="当前对话关联原店铺会话，继续发送或确认将被跨店拦截；如需在新店铺查询，请新建会话。"
              action={<Button size="small" onClick={reset}>新建当前店铺会话</Button>}
            />
          )}
          {!turn && <div className={styles.welcome}>
            <p>输入商品 SKU、订单号、运营规则问题或售后需求，或从下面的示例开始。</p>
            <div className={styles.examples}>
              <Button onClick={() => setMessage(example)}>{example}</Button>
              <Button onClick={() => setMessage(promotionExample)}>试用促销方案预览</Button>
              <Button onClick={() => setMessage(priceChangeExample)}>试用商品调价方案</Button>
              <Button onClick={() => setMessage(orderDiagExample)}>查询异常订单</Button>
              <Button onClick={() => setMessage(refundExample)}>试用订单退款方案</Button>
              <Button onClick={() => setMessage(knowledgeExample)}>试用规则问答（售价变更复核）</Button>
            </div>
          </div>}
          {showEarlierTurn && <>
            <p className={styles.bubble}>{promotionStart.question}</p>
            <p className={styles.earlierReply}>{promotionStart.answer?.message}</p>
          </>}
          {turn && <p className={styles.bubble}>{turn.question}</p>}
          {pending && <div className={styles.pending} role="status"><Spin size="small" /> 正在查询当前店铺…</div>}
          {failure?.kind === "expired" && <Alert type="warning" showIcon message="登录状态已失效"
            action={<Button onClick={props.onSignIn}>重新登录</Button>} />}
          {failure?.kind === "denied" && <Alert type="warning" showIcon message="没有权限查询当前店铺"
            description={failure.message || "如需访问，请联系组织管理员。"} />}
          {failure?.kind === "unavailable" && <Alert type="error" showIcon message="暂时无法完成查询" description="问题已保留，可再次发送。" />}
          {actionError && <Alert type="error" showIcon message={actionError} />}
          {answer && <div className={styles.reply} role="status">
            <span className={styles.agentIcon} aria-hidden="true"><ThunderboltOutlined /></span>
            <div><span>运营 Agent</span><p>{answer.message}</p></div>
          </div>}
          {awaitingDate && <Alert type="warning" showIcon message="还缺开始日期"
            description="可输入“11 月 22 日开始”或完整日期，例如 2026-11-22。" />}
          {awaitingRefundDetails && <Alert type="warning" showIcon message="还缺退款金额或原因"
            description="可补充“退 50 元，原因部分商品破损”。" />}
          {awaitingPriceDetails && <Alert type="warning" showIcon message="还缺目标售价或调价原因"
            description="可补充“调到 189 元，原因调整冬季常规售价”。" />}
          {answer?.kind === "promotion_blocked" && <Alert type="warning" showIcon message="商品、库存或排期冲突校验未通过" />}
          {answer?.kind === "refund_blocked" && <Alert type="warning" showIcon message="退款资格或可退余额校验未通过" />}
          {answer?.kind === "price_change_blocked" && <Alert type="warning" showIcon message="商品调价规则校验未通过" />}
          {(promotion?.below_cost || riskWarnings.length > 0) && (
            <Alert
              type="warning"
              showIcon
              message="毛利与价格风险预警"
              description={riskWarnings.join(" ") || "促销价低于商品成本价，存在负毛利风险。"}
            />
          )}
          {answer?.kind === "promotion_preview" && promotion?.starts_at && promotion.ends_at && !submission &&
            <section className={styles.result} aria-labelledby="promotion-heading">
              <div className={styles.resultHeader}>
                <h3 id="promotion-heading">促销执行方案</h3>
                <span className={styles.pill}>{promotion.conflict_check === "clear" ? "待确认送审" : "仅预览"}</span>
              </div>
              <div className={styles.previewBody}>
                <div className={styles.previewBand}>
                  <div><span>后端核算促销价</span><strong>¥{promotion.promotion_price}</strong>
                    <p>原价 ¥{promotion.original_price} · {promotion.discount} 折</p></div>
                  <div><span>活动时段 · {promotion.timezone ?? "Asia/Shanghai"}</span>
                    <p>{localTime(promotion.starts_at)}（含）至 {localTime(promotion.ends_at)}（不含）</p>
                    <p>持续 {promotion.duration_days} 天</p></div>
                </div>
                <p className={styles.previewNote}>
                  {promotion.conflict_check === "clear"
                    ? "排期冲突检查通过（同店同 SKU 0 个重叠活动）。可点击下方按钮或直接回复“确认刚才的方案”提交审批。"
                    : promotion.conflict_check === "conflict"
                      ? `存在排期冲突（重叠活动：${(promotion.conflicting_promotions ?? []).join("、")}）。`
                      : "当前方案仅供预览。"}
                </p>
                {canConfirmPreview && (
                  <div className={styles.submitActions}>
                    <Button type="primary" loading={submittingPlan} onClick={() => { void handleConfirmSubmit() }}>
                      确认并提交审批
                    </Button>
                  </div>
                )}
              </div>
            </section>}

          {submission && (
            <section className={styles.result} aria-labelledby="submission-heading">
              <div className={styles.resultHeader}>
                <h3 id="submission-heading">{submission.status === "cancelled" ? "审批申请已撤回" : "已提交人工审批"}</h3>
                <span className={styles.pill}>{submission.status === "cancelled" ? "已撤回" : "待审批"}</span>
              </div>
              <dl className={styles.submissionGrid}>
                <div><dt>审批单</dt><dd>{submission.approval_no}</dd></div>
                <div><dt>活动编号</dt><dd>{submission.promotion_no}</dd></div>
                <div><dt>方案版本</dt><dd>V{submission.version}</dd></div>
                <div><dt>活动价</dt><dd>¥{submission.promotion_price}</dd></div>
                <div><dt>适用 SKU</dt><dd>{submission.sku_code}</dd></div>
                <div><dt>活动时段</dt><dd>{localTime(submission.starts_at)} → {localTime(submission.ends_at)}</dd></div>
              </dl>
              <p className={styles.idempotencyHint}>
                {submission.replayed
                  ? "已命中幂等保护，直接返回原审批单，未重复创建活动或审批记录。"
                  : "同一版本重复确认会返回原审批单，不会重复建单。尚未批准或排期。"}
              </p>
              <div className={styles.previewBody} style={{ paddingTop: 0 }}>
                <div className={styles.submitActions}>
                  {submission.status === "pending_approval" && (
                    <Button loading={cancellingPlan} onClick={() => { void handleWithdrawSubmission() }}>
                      撤回申请
                    </Button>
                  )}
                  <Link className={styles.inlineLink} to={`/promotions/${submission.promotion_id}`}>查看活动详情</Link>
                  <Link className={styles.inlineLink} to={`/approvals/${submission.approval_id}`}>查看审批快照</Link>
                </div>
              </div>
            </section>
          )}

          {answer?.kind === "refund_preview" && refundPreview && !refundSubmission && (
            <section className={styles.result} aria-labelledby="refund-preview-heading">
              <div className={styles.resultHeader}>
                <h3 id="refund-preview-heading">订单退款方案 · {refundPreview.order_no}</h3>
                <span className={styles.pill}>{refundPreview.eligible ? "待确认送审" : "暂不可退"}</span>
              </div>
              <div className={styles.previewBody}>
                <div className={styles.previewBand}>
                  <div>
                    <span>拟申请退款额</span>
                    <strong>¥{refundPreview.proposed_amount}</strong>
                    <p>订单实付 ¥{refundPreview.paid_amount} · 可退上限 ¥{refundPreview.refundable_amount}</p>
                  </div>
                  <div>
                    <span>退款原因与预期结果</span>
                    <p>{refundPreview.reason}</p>
                    <p>预计订单状态 → {refundPreview.projected_order_status}</p>
                  </div>
                </div>
                <p className={styles.previewNote}>
                  确认仅提交退款审批。人工批准后仍会锁定订单行二次复核可退余额与重复退款防护，执行仅发生在本地沙箱。
                </p>
                {canConfirmRefund && (
                  <div className={styles.submitActions}>
                    <Button type="primary" loading={submittingPlan} onClick={() => { void handleConfirmRefundSubmit() }}>
                      确认并提交退款审批
                    </Button>
                  </div>
                )}
              </div>
            </section>
          )}

          {refundSubmission && (
            <section className={styles.result} aria-labelledby="refund-submission-heading">
              <div className={styles.resultHeader}>
                <h3 id="refund-submission-heading">{refundSubmission.status === "cancelled" ? "退款审批已撤回" : "已提交退款人工审批"}</h3>
                <span className={styles.pill}>{refundSubmission.status === "cancelled" ? "已撤回" : "待审批"}</span>
              </div>
              <dl className={styles.submissionGrid}>
                <div><dt>审批单</dt><dd>{refundSubmission.approval_no}</dd></div>
                <div><dt>退款单号</dt><dd>{refundSubmission.refund_no}</dd></div>
                <div><dt>关联订单</dt><dd>{refundSubmission.order_no}</dd></div>
                <div><dt>申请退款额</dt><dd>¥{refundSubmission.amount}</dd></div>
              </dl>
              <p className={styles.idempotencyHint}>
                {refundSubmission.replayed
                  ? "已命中幂等保护，直接返回原退款审批单，未重复创建申请。"
                  : "同一退款方案重复确认会返回原审批单，不会产生重复在途退款。"}
              </p>
              <div className={styles.previewBody} style={{ paddingTop: 0 }}>
                <div className={styles.submitActions}>
                  {refundSubmission.status === "pending_approval" && (
                    <Button loading={cancellingPlan} onClick={() => { void handleWithdrawRefundSubmission() }}>
                      撤回申请
                    </Button>
                  )}
                  <Link className={styles.inlineLink} to={`/orders/refunds/${refundSubmission.refund_id}`}>查看退款详情</Link>
                  <Link className={styles.inlineLink} to={`/approvals/${refundSubmission.approval_id}`}>查看审批快照</Link>
                </div>
              </div>
            </section>
          )}

          {answer?.kind === "price_change_preview" && pricePreview && !priceSubmission && (
            <section className={styles.result} aria-labelledby="price-preview-heading">
              <div className={styles.resultHeader}>
                <h3 id="price-preview-heading">{pricePreview.sku_code} 调价方案</h3>
                <span className={styles.pill}>{pricePreview.eligible ? "待运营确认" : "不可提交"}</span>
              </div>
              <div className={styles.previewBody}>
                <div className={styles.previewBand}>
                  <div>
                    <span>当前售价 → 拟调整售价</span>
                    <strong>¥{pricePreview.proposed_price}</strong>
                    <p>
                      原价 ¥{pricePreview.current_price} · 变动 {Number(pricePreview.change_amount) >= 0 ? "+" : ""}
                      ¥{pricePreview.change_amount}（{Number(pricePreview.change_pct) >= 0 ? "+" : ""}
                      {pricePreview.change_pct}%）
                    </p>
                  </div>
                  <div>
                    <span>调价原因与风控基线</span>
                    <p>{pricePreview.reason}</p>
                    <p>商品成本 ¥{pricePreview.cost_price} · 状态在售</p>
                  </div>
                </div>
                <p className={styles.previewNote}>
                  确认仅提交调价审批。批准后业务服务会重新核验 SKU 与当前售价，不会在对话内直接改价。
                </p>
                {canConfirmPrice && (
                  <div className={styles.submitActions}>
                    <Button type="primary" loading={submittingPlan} onClick={() => { void handleConfirmPriceChangeSubmit() }}>
                      确认并提交调价审批
                    </Button>
                  </div>
                )}
              </div>
            </section>
          )}

          {priceSubmission && (
            <section className={styles.result} aria-labelledby="price-submission-heading">
              <div className={styles.resultHeader}>
                <h3 id="price-submission-heading">{priceSubmission.status === "cancelled" ? "调价审批已撤回" : "已提交调价人工审批"}</h3>
                <span className={styles.pill}>{priceSubmission.status === "cancelled" ? "已撤回" : "待审批"}</span>
              </div>
              <dl className={styles.submissionGrid}>
                <div><dt>审批单</dt><dd>{priceSubmission.approval_no}</dd></div>
                <div><dt>调价单号</dt><dd>{priceSubmission.request_no}</dd></div>
                <div><dt>适用 SKU</dt><dd>{priceSubmission.sku_code}</dd></div>
                <div><dt>调价区间</dt><dd>¥{priceSubmission.old_price} → ¥{priceSubmission.new_price}</dd></div>
              </dl>
              <p className={styles.idempotencyHint}>
                {priceSubmission.replayed
                  ? "已命中幂等保护，直接返回原调价审批单，未重复创建申请。"
                  : "同一调价方案重复确认会返回原审批单，批准前商品售价保持不变。"}
              </p>
              <div className={styles.previewBody} style={{ paddingTop: 0 }}>
                <div className={styles.submitActions}>
                  {priceSubmission.status === "pending_approval" && (
                    <Button loading={cancellingPlan} onClick={() => { void handleWithdrawPriceChangeSubmission() }}>
                      撤回申请
                    </Button>
                  )}
                  <Link className={styles.inlineLink} to={`/catalog/price-changes/${priceSubmission.request_id}`}>查看调价详情</Link>
                  <Link className={styles.inlineLink} to={`/approvals/${priceSubmission.approval_id}`}>查看审批快照</Link>
                </div>
              </div>
            </section>
          )}

          {answer?.kind === "order_info" && orderResult && (
            <>
              <section className={styles.result} aria-labelledby="order-diag-heading">
                <div className={styles.resultHeader}>
                  <h3 id="order-diag-heading">
                    {orderResult.mode === "detail" ? "订单售后诊断" : "异常订单与处理建议"}
                  </h3>
                  <span className={styles.pill}>{orderResult.items.length} 笔订单</span>
                </div>
                <div className={styles.previewBody}>
                  {orderResult.items.length === 0 ? (
                    <p className={styles.previewNote}>当前店铺暂未发现待处理异常订单。</p>
                  ) : (
                    <ul className={styles.diagList}>
                      {orderResult.items.map(item => (
                        <li key={item.order_id} className={styles.diagItem}>
                          <div className={styles.diagTop}>
                            <strong>{item.order_no}</strong>
                            <span>实付 ¥{item.paid_amount} · 可退 ¥{item.refundable_amount}</span>
                          </div>
                          <p>{item.exception_note ?? (item.can_refund ? "满足退款资格，可直接评估发起退款。" : item.refund_block_reason)}</p>
                          <div className={styles.submitActions} style={{ marginTop: 6 }}>
                            <Link className={styles.inlineLink} to={`/orders/${item.order_id}`}>查看订单详情</Link>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </section>
              <p className={styles.readOnly}><CheckOutlined aria-hidden="true" /> 仅只读查询与诊断；未修改库存、订单履约状态或发起退款。</p>
            </>
          )}

          {answer?.kind === "sales" && sales && <>
            <div className={styles.result}>
              <div className={styles.resultHeader}><h3>查询结果</h3><span className={styles.pill}>支付件数</span></div>
              <div className={styles.resultBody}>
                <strong>{sales.units_sold} 件</strong>
                <p>{period(sales)} · {sales.timezone}</p>
              </div>
            </div>
            <p className={styles.readOnly}><CheckOutlined aria-hidden="true" /> 仅查询销量；未创建促销、审批单或执行任务。</p>
          </>}

          {answer?.kind === "knowledge_qa" && knowledgeResult && (
            <>
              <section className={styles.result} aria-labelledby="citations-heading">
                <div className={styles.resultHeader}>
                  <h3 id="citations-heading">引用来源</h3>
                  <span className={styles.pill}>{citations.length} 条引用</span>
                </div>
                <div className={styles.previewBody}>
                  {citations.length === 0 ? (
                    <p className={styles.previewNote}>当前知识库中未命中当前角色权限可见的已索引规则条款。</p>
                  ) : (
                    <ul className={styles.citationList}>
                      {citations.map((item, idx) => (
                        <li key={item.chunk_id}>
                          <button
                            type="button"
                            className={`${styles.citationBtn} ${idx === selectedCitationIdx ? styles.citationBtnActive : ""}`}
                            onClick={() => setSelectedCitationIdx(idx)}
                          >
                            <span className={styles.citationBadge} aria-hidden="true"><FileTextOutlined /></span>
                            <div>
                              <div className={styles.citationTitle}>《{item.title}》</div>
                              <div className={styles.citationSub}>
                                {item.doc_no} · V{item.document_version} · {item.section_title} · {item.page_label}
                              </div>
                            </div>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </section>
              <p className={styles.readOnly}>
                <CheckOutlined aria-hidden="true" /> {knowledgeResult.isolation_notice}
              </p>
            </>
          )}
        </div>
        <form className={styles.composer} onSubmit={submit}>
          <Input
            aria-label="输入查数问题"
            placeholder={
              awaitingFollowUp === "promotion"
                ? "输入活动开始日期…"
                : awaitingFollowUp === "refund"
                  ? "补充退款金额与原因…"
                  : awaitingFollowUp === "price_change"
                    ? "补充目标售价与调价原因…"
                    : canConfirmPreview || canConfirmRefund || canConfirmPrice
                      ? "输入“确认刚才的方案”提交审批，或继续提问…"
                      : "询查销量、促销、调价、退款，或规则制度…"
            }
            maxLength={500}
            value={message}
            disabled={pending}
            onChange={event => setMessage(event.target.value)}
          />
          <Button type="primary" htmlType="submit" icon={<ArrowUpOutlined aria-hidden="true" />} loading={pending}
            disabled={!message.trim()} aria-label="发送">发送</Button>
        </form>
      </section>

      <div className={styles.sideColumn}>
        {knowledgeResult ? (
          <aside className={styles.facts} aria-labelledby="knowledge-citation-preview">
            <header className={styles.panelHeader}>
              <h2 id="knowledge-citation-preview">引用预览</h2>
              <span className={styles.pill}>规则来源</span>
            </header>
            {selectedCitation ? (
              <dl>
                <div>
                  <dt>{selectedCitation.title} · {selectedCitation.section_title}</dt>
                  <dd><p className={styles.previewQuote}>{selectedCitation.content}</p></dd>
                </div>
                <div>
                  <dt>文档版本</dt>
                  <dd>V{selectedCitation.document_version}（{selectedCitation.doc_no}）</dd>
                </div>
                <div>
                  <dt>引用位置</dt>
                  <dd>{selectedCitation.page_label} · {selectedCitation.section_title}</dd>
                </div>
                <div>
                  <dt>文档权限</dt>
                  <dd>{selectedCitation.visibility_label}</dd>
                </div>
                <div>
                  <dt>混合检索口径</dt>
                  <dd>
                    向量 {knowledgeResult.vector_candidates} / 词法 {knowledgeResult.lexical_candidates}
                    {knowledgeResult.rerank_degraded ? " · Rerank 已降级 RRF" : ` · ${knowledgeResult.rerank_model ?? "RRF"}`}
                  </dd>
                </div>
              </dl>
            ) : (
              <div className={styles.previewBody}>
                <p className={styles.previewNote}>暂无可用引用片段。</p>
              </div>
            )}
          </aside>
        ) : (
        <>
        {(promotion || submission || refundPreview || refundSubmission || pricePreview || priceSubmission) && (
          <section className={styles.progressCard} aria-labelledby="progress-heading">
            <header className={styles.panelHeader}>
              <h2 id="progress-heading">任务进度</h2>
              {answer?.task_id && <span className={styles.pill}>T-{answer.task_id}</span>}
            </header>
            <ol className={styles.progressSteps}>
              {(refundPreview || refundSubmission
                ? [
                    "核验订单与可退余额",
                    "生成退款申请方案",
                    "运营确认并提交审批",
                    "等待人工审批",
                    "复核余额并沙箱执行",
                  ]
                : pricePreview || priceSubmission
                  ? [
                      "读取当前售价与成本",
                      "生成商品调价方案",
                      "运营确认并提交审批",
                      "等待人工审批",
                      "复核原价并更新售价",
                    ]
                  : [
                      "查询业务数据",
                      "生成结构化方案",
                      "运营确认并提交",
                      "等待人工审批",
                      "业务复核与排期",
                    ]
              ).map((label, idx) => {
                const done = idx < progressStage
                const current = idx === progressStage
                return (
                  <li
                    key={label}
                    className={`${styles.progressStep} ${done ? styles.stepDone : current ? styles.stepCurrent : ""}`}
                  >
                    <span className={styles.progressDot}>{done ? "✓" : idx + 1}</span>
                    <span>{label}</span>
                  </li>
                )
              })}
            </ol>
          </section>
        )}

        <aside className={styles.facts} aria-labelledby="facts-heading">
          <header className={styles.panelHeader}><h2 id="facts-heading">数据库事实</h2><span className={styles.pill}>只读</span></header>
          <dl>
            <div>
              <dt>统计对象</dt>
              <dd>
                {shopName}
                {(promotion || sales || submission || refundPreview || refundSubmission || pricePreview || priceSubmission)
                  && ` · ${submission?.sku_code ?? promotion?.sku_code ?? priceSubmission?.sku_code ?? pricePreview?.sku_code ?? sales?.sku_code ?? refundSubmission?.order_no ?? refundPreview?.order_no}`}
              </dd>
            </div>
            {pricePreview || priceSubmission ? (
              <>
                {pricePreview && (
                  <>
                    <div><dt>当前状态</dt><dd>在售 · ¥{pricePreview.current_price}（成本 ¥{pricePreview.cost_price}）</dd></div>
                    {pricePreview.last_old_price && pricePreview.last_new_price && (
                      <div><dt>上次调价</dt><dd>¥{pricePreview.last_old_price} → ¥{pricePreview.last_new_price}</dd></div>
                    )}
                  </>
                )}
                <div><dt>查询工具</dt><dd>{answer?.tool_calls ? `调价预览工具 · 已调用 ${answer.tool_calls} 次` : "尚未完成查询"}</dd></div>
              </>
            ) : refundPreview || refundSubmission ? (
              <>
                {refundPreview && (
                  <>
                    <div><dt>订单实付</dt><dd>¥{refundPreview.paid_amount}</dd></div>
                    <div><dt>已成功退款 / 在途</dt><dd>¥{refundPreview.refunded_amount} / ¥{refundPreview.in_flight_amount}</dd></div>
                    <div><dt>当前可退上限</dt><dd>¥{refundPreview.refundable_amount}</dd></div>
                  </>
                )}
                <div><dt>查询工具</dt><dd>{answer?.tool_calls ? `退款校验工具 · 已调用 ${answer.tool_calls} 次` : "尚未完成查询"}</dd></div>
              </>
            ) : orderResult ? (
              <>
                <div><dt>命中订单数</dt><dd>{orderResult.items.length} 笔</dd></div>
                <div><dt>查询工具</dt><dd>{answer?.tool_calls ? `订单只读诊断工具 · 仅调用 ${answer.tool_calls} 次` : "尚未完成查询"}</dd></div>
              </>
            ) : (
              <>
                <div><dt>统计区间</dt><dd>{sales ? period(sales) : "查询成功后显示"}</dd>
                  {sales && <dd className={styles.demoDate}>沙箱业务日：{sales.end_date}（固定）</dd>}</div>
                <div><dt>统计口径</dt><dd>{basis}</dd></div>
                {promotion ? <>
                  <div><dt>近 7 日销量</dt><dd>{sales ? `${sales.units_sold} 件` : "暂无统计"}</dd></div>
                  <div><dt>库存快照</dt><dd>{promotion.available_qty === null ? "未记录" : `可售 ${promotion.available_qty} 件`} · 门槛 {promotion.minimum_qty} 件</dd></div>
                  <div><dt>当前售价</dt><dd>¥{promotion.original_price}{promotion.cost_price ? `（成本 ¥${promotion.cost_price}）` : ""}</dd></div>
                  <div><dt>查询工具</dt><dd>{answer?.tool_calls ? `受控业务工具 · 已调用 ${answer.tool_calls} 次` : "尚未完成查询"}</dd></div>
                </> : <div><dt>查询工具</dt><dd>{answer?.tool_calls ? `${submission ? "审批提交工具" : "销量查询"} · 仅调用 ${answer.tool_calls} 次` : "尚未完成查询"}</dd></div>}
              </>
            )}
          </dl>
        </aside>
        </>
        )}
      </div>
    </div>
  </main>
}
