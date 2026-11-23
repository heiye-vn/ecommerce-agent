import { ArrowUpOutlined, CheckOutlined, ThunderboltOutlined } from "@ant-design/icons"
import { useQueryClient } from "@tanstack/react-query"
import { Alert, App as AntApp, Button, Input, Spin } from "antd"
import { useEffect, useRef, useState, type FormEvent } from "react"
import { Link } from "react-router-dom"

import { ApiError, fetchShopJson } from "../../api/client"
import {
  cancelPromotionRequest,
  submitPromotionRequest,
  type PromotionSubmitResponse,
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
  message: string
  session_id?: number | null
  task_id?: number | null
  tool_calls: number
  sales: Sales | null
  promotion?: Promotion | null
  submission?: PromotionSubmitResponse | null
}

type Failure = { kind: "expired" | "denied" | "unavailable"; message?: string }
type Turn = { question: string; answer: Answer | null }
const example = "查一下 SKU-001 最近 7 天销量"
const promotionExample = "帮我看看 SKU-001 最近 7 天销量，如果库存充足，就创建一个 8 折、持续 3 天的促销。"
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
  const [message, setMessage] = useState("")
  const [turn, setTurn] = useState<Turn | null>(null)
  const [promotionStart, setPromotionStart] = useState<Turn | null>(null)
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [sessionShopId, setSessionShopId] = useState<number | null>(null)
  const [awaitingFollowUp, setAwaitingFollowUp] = useState(false)
  const [submission, setSubmission] = useState<PromotionSubmitResponse | null>(null)
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
    if (!awaitingFollowUp) setPromotionStart(null)
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
      if (answer.session_id) {
        setSessionId(answer.session_id)
        if (sessionShopId === null) setSessionShopId(props.shopId)
      }
      if (answer.submission) {
        setSubmission(answer.submission)
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "promotions"] })
        await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "approvals"] })
      } else if (answer.kind !== "unsupported") {
        setSubmission(null)
      }
      if (!answer.promotion && !answer.submission) {
        setPromotionStart(null)
      } else if (!awaitingFollowUp && answer.kind === "promotion_clarify") {
        setPromotionStart(completed)
      }
      setAwaitingFollowUp(answer.kind === "promotion_clarify")
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

  function reset() {
    setTurn(null)
    setPromotionStart(null)
    setSessionId(null)
    setSessionShopId(null)
    setAwaitingFollowUp(false)
    setSubmission(null)
    setActionError(null)
    setMessage("")
    setFailure(null)
  }

  const answer = turn?.answer
  const sales = answer ? answer.sales : awaitingFollowUp ? promotionStart?.answer?.sales : null
  const promotion = answer ? answer.promotion : awaitingFollowUp ? promotionStart?.answer?.promotion : null
  const awaitingDate = answer?.kind === "promotion_clarify"
  const showEarlierTurn = promotionStart && promotionStart !== turn && (awaitingFollowUp || answer?.promotion)
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

  const progressStage = submission
    ? submission.status === "cancelled" ? 2 : 3
    : answer?.kind === "promotion_preview"
      ? 2
      : sales || promotion
        ? 1
        : 0

  return <main className={styles.page}>
    <nav className={styles.breadcrumb} aria-label="面包屑"><span>运营工作台</span><span aria-hidden="true">/</span><strong aria-current="page">Agent 对话</strong></nav>
    <h1>Agent 对话</h1>
    <div className={styles.pageActions}>
      <p className={styles.summary}>查询当前店铺的销量，或根据售价、库存与排期校验生成促销方案并提交审批。</p>
      {turn && <Button onClick={reset} disabled={pending}>新建会话</Button>}
    </div>

    <div className={styles.grid}>
      <section className={styles.conversation} aria-labelledby="conversation-heading">
        <header className={styles.panelHeader}>
          <div><h2 id="conversation-heading">{submission ? `${submission.sku_code} 促销审批` : promotion ? `${promotion.sku_code} 促销方案` : sales ? `查询 ${sales.sku_code} 近 7 天销量` : "销量查询与促销方案"}</h2>
            <p>当前店铺 · {shopName}</p></div>
          <span className={styles.pill}>{submission ? "待人工审批" : promotion ? "方案生成" : "只读查询"}</span>
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
            <p>输入商品 SKU 和需求，或从下面的示例开始。</p>
            <div className={styles.examples}>
              <Button onClick={() => setMessage(example)}>{example}</Button>
              <Button onClick={() => setMessage(promotionExample)}>试用促销方案预览</Button>
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
          {answer?.kind === "promotion_blocked" && <Alert type="warning" showIcon message="商品、库存或排期冲突校验未通过" />}
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
        </div>
        <form className={styles.composer} onSubmit={submit}>
          <Input
            aria-label="输入查数问题"
            placeholder={
              awaitingFollowUp
                ? "输入活动开始日期…"
                : canConfirmPreview
                  ? "输入“确认刚才的方案”提交审批，或继续提问…"
                  : "输入查数或促销需求…"
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
        {(promotion || submission) && (
          <section className={styles.progressCard} aria-labelledby="progress-heading">
            <header className={styles.panelHeader}>
              <h2 id="progress-heading">任务进度</h2>
              {answer?.task_id && <span className={styles.pill}>T-{answer.task_id}</span>}
            </header>
            <ol className={styles.progressSteps}>
              {[
                "查询业务数据",
                "生成结构化方案",
                "运营确认并提交",
                "等待人工审批",
                "业务复核与排期",
              ].map((label, idx) => {
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
            <div><dt>统计对象</dt><dd>{shopName}{(promotion || sales || submission) && ` · ${submission?.sku_code ?? promotion?.sku_code ?? sales?.sku_code}`}</dd></div>
            <div><dt>统计区间</dt><dd>{sales ? period(sales) : "查询成功后显示"}</dd>
              {sales && <dd className={styles.demoDate}>沙箱业务日：{sales.end_date}（固定）</dd>}</div>
            <div><dt>统计口径</dt><dd>{basis}</dd></div>
            {promotion ? <>
              <div><dt>近 7 日销量</dt><dd>{sales ? `${sales.units_sold} 件` : "暂无统计"}</dd></div>
              <div><dt>库存快照</dt><dd>{promotion.available_qty === null ? "未记录" : `可售 ${promotion.available_qty} 件`} · 门槛 {promotion.minimum_qty} 件</dd></div>
              <div><dt>当前售价</dt><dd>¥{promotion.original_price}{promotion.cost_price ? `（成本 ¥${promotion.cost_price}）` : ""}</dd></div>
              <div><dt>查询工具</dt><dd>{answer?.tool_calls ? `受控业务工具 · 已调用 ${answer.tool_calls} 次` : "尚未完成查询"}</dd></div>
            </> : <div><dt>查询工具</dt><dd>{answer?.tool_calls ? `${submission ? "审批提交工具" : "销量查询"} · 仅调用 ${answer.tool_calls} 次` : "尚未完成查询"}</dd></div>}
          </dl>
        </aside>
      </div>
    </div>
  </main>
}
