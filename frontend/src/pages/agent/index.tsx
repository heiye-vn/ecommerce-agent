import { ArrowUpOutlined, CheckOutlined, ThunderboltOutlined } from "@ant-design/icons"
import { Alert, Button, Input, Spin } from "antd"
import { useEffect, useRef, useState, type FormEvent } from "react"

import { readShop, type ShopReadProps } from "../../auth"
import styles from "./index.module.css"

type Sales = {
  sku_code: string
  units_sold: number
  paid_orders: number
  start_date: string
  end_date: string
  timezone: string
}

type Answer = {
  kind: "sales" | "clarify" | "unsupported" | "not_found"
  message: string
  tool_calls: number
  sales: Sales | null
}

type Failure = "expired" | "denied" | "unavailable"
type Turn = { question: string; answer: Answer | null }
const example = "查一下 SKU-001 最近 7 天销量"
const basis = "近 7 个完整自然日；按已支付订单明细数量汇总，不扣除后续退款。"

function period(sales: Sales) {
  return `${sales.start_date} 00:00（含）至 ${sales.end_date} 00:00（不含）`
}

export default function AgentPage({ shopName, ...props }: ShopReadProps & { shopName: string }) {
  const [message, setMessage] = useState("")
  const [turn, setTurn] = useState<Turn | null>(null)
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
    setTurn({ question, answer: null })
    try {
      const response = await readShop("agent/query", props, controller.signal, { message: question })
      if (!response || controller.signal.aborted) return
      if (!response.ok) {
        setFailure(response.status === 401 ? "expired" : response.status === 403 ? "denied" : "unavailable")
        return
      }
      const answer: Answer = await response.json()
      if (controller.signal.aborted) return
      setTurn({ question, answer })
      setMessage("")
    } catch {
      if (!controller.signal.aborted) setFailure("unavailable")
    } finally {
      active.current = null
      if (!controller.signal.aborted) setPending(false)
    }
  }

  const answer = turn?.answer
  const sales = answer?.sales

  return <main className={styles.page}>
    <nav className={styles.breadcrumb} aria-label="面包屑"><span>运营工作台</span><span aria-hidden="true">/</span><strong aria-current="page">Agent 对话</strong></nav>
    <h1>Agent 对话</h1>
    <p className={styles.summary}>查询仅限当前店铺；本阶段只读，不执行促销或审批。</p>

    <div className={styles.grid}>
      <section className={styles.conversation} aria-labelledby="conversation-heading">
        <header className={styles.panelHeader}>
          <div><h2 id="conversation-heading">{sales ? `查询 ${sales.sku_code} 近 7 天销量` : "销量查询"}</h2>
            <p>当前店铺 · {shopName}</p></div>
          <span className={styles.pill}>只读查询</span>
        </header>
        <div className={styles.thread}>
          {!turn && <div className={styles.welcome}>
            <p>输入商品 SKU，查询最近 7 个完整自然日的支付件数。</p>
            <Button onClick={() => setMessage(example)}>{example}</Button>
          </div>}
          {turn && <p className={styles.bubble}>{turn.question}</p>}
          {pending && <div className={styles.pending} role="status"><Spin size="small" /> 正在查询当前店铺…</div>}
          {failure === "expired" && <Alert type="warning" showIcon message="登录状态已失效"
            action={<Button onClick={props.onSignIn}>重新登录</Button>} />}
          {failure === "denied" && <Alert type="warning" showIcon message="没有权限查询当前店铺" description="如需访问，请联系组织管理员。" />}
          {failure === "unavailable" && <Alert type="error" showIcon message="暂时无法完成查询" description="问题已保留，可再次发送。" />}
          {answer && <div className={styles.reply} role="status">
            <span className={styles.agentIcon} aria-hidden="true"><ThunderboltOutlined /></span>
            <div><span>运营 Agent</span><p>{answer.message}</p></div>
          </div>}
          {sales && <>
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
          <Input aria-label="输入查数问题" placeholder="输入 SKU 和销量问题…" maxLength={500} value={message}
            disabled={pending} onChange={event => setMessage(event.target.value)} />
          <Button type="primary" htmlType="submit" icon={<ArrowUpOutlined aria-hidden="true" />} loading={pending}
            disabled={!message.trim()} aria-label="发送">发送</Button>
        </form>
      </section>

      <aside className={styles.facts} aria-labelledby="facts-heading">
        <header className={styles.panelHeader}><h2 id="facts-heading">数据库事实</h2><span className={styles.pill}>只读</span></header>
        <dl>
          <div><dt>统计对象</dt><dd>{shopName}{sales && ` · ${sales.sku_code}`}</dd></div>
          <div><dt>统计区间</dt><dd>{sales ? period(sales) : "查询成功后显示"}</dd>
            {sales && <dd className={styles.demoDate}>沙箱业务日：{sales.end_date}（固定）</dd>}</div>
          <div><dt>统计口径</dt><dd>{basis}</dd></div>
          <div><dt>查询工具</dt><dd>{answer?.tool_calls ? "销量查询 · 仅调用 1 次" : "尚未完成查询"}</dd></div>
        </dl>
      </aside>
    </div>
  </main>
}
