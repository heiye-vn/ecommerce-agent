import { Alert, Button, Select, Spin, Table } from "antd"
import type { TableColumnsType } from "antd"
import type * as echarts from "echarts"
import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"

import { ApiError } from "../../api/client"
import {
  useAnalyticsOverviewQuery,
  useDailyReportQuery,
  useInventoryTurnoverQuery,
  type SkuTurnoverItem,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import EChartBox from "../../components/EChartBox"
import styles from "./index.module.css"

type AnalyticsPageProps = ShopReadProps & {
  shopName: string
}

type AnalyticsTab = "overview" | "report" | "turnover"

const yuan = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const yuanWhole = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 })

function formatMoney(raw: string, compact = false) {
  const num = Number(raw)
  return `¥${(compact ? yuanWhole : yuan).format(num)}`
}

function ErrorBox({
  error,
  onRetry,
  onSignIn,
}: {
  error: unknown
  onRetry: () => void
  onSignIn: () => void
}) {
  const status = error instanceof ApiError ? error.status : undefined
  if (status === 401) {
    return <Alert showIcon type="warning" message="登录状态已失效" action={<Button onClick={onSignIn}>重新登录</Button>} />
  }
  if (status === 403) {
    return <Alert showIcon type="warning" message="没有权限查看当前店铺的经营分析" />
  }
  return <Alert showIcon type="error" message="经营分析数据加载失败" action={<Button onClick={onRetry}>重试</Button>} />
}

function OverviewTab({
  days,
  shopName,
  ...props
}: AnalyticsPageProps & { days: number; onGoReport: () => void }) {
  const query = useAnalyticsOverviewQuery(props, days, true)
  const data = query.isSuccess ? query.data : undefined

  const trendOption = useMemo<echarts.EChartsCoreOption>(() => {
    if (!data) return {}
    return {
      grid: { left: 48, right: 16, top: 20, bottom: 28 },
      tooltip: { trigger: "axis" },
      legend: { bottom: 0, left: 0, textStyle: { color: "#52525b", fontSize: 12 } },
      xAxis: {
        type: "category",
        data: data.trend.map(item => item.date_label),
        axisLine: { lineStyle: { color: "#e4e4e7" } },
        axisLabel: { color: "#52525b" },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: "#f4f4f5" } },
        axisLabel: { color: "#71717a" },
      },
      series: [
        {
          name: "当前区间",
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 7,
          data: data.trend.map(item => Number(item.current_gmv)),
          lineStyle: { color: "#166534", width: 2.5 },
          itemStyle: { color: "#166534" },
          areaStyle: { color: "rgba(193, 251, 212, 0.45)" },
        },
        {
          name: "上期同长度区间",
          type: "line",
          smooth: true,
          symbol: "none",
          data: data.trend.map(item => Number(item.previous_gmv)),
          lineStyle: { color: "#86efac", width: 2, type: "dashed" },
          itemStyle: { color: "#86efac" },
        },
      ],
    }
  }, [data])

  if (query.isPending || query.isRefetching) {
    return <div className={styles.feedback}><Spin size="small" /> 正在统计店铺指标看板…</div>
  }
  if (query.isError || !data) {
    return <ErrorBox error={query.error} onRetry={() => { void query.refetch() }} onSignIn={props.onSignIn} />
  }

  const maxUnits = Math.max(1, ...data.sku_ranking.map(item => item.units_sold))
  const changeSign = data.gmv_change_pct !== null && Number(data.gmv_change_pct) >= 0 ? "+" : ""

  return (
    <section aria-labelledby="overview-tab-heading">
      <dl className={styles.metricsBand}>
        <div className={`${styles.metricItem} ${styles.metricHighlight}`}>
          <dt>支付销售额</dt>
          <dd>{formatMoney(data.paid_gmv, true)}</dd>
          <span>
            {data.gmv_change_pct !== null ? `较上期 ${changeSign}${data.gmv_change_pct}%` : `${shopName} · 当期实付`}
          </span>
        </div>
        <div className={styles.metricItem}>
          <dt>支付订单</dt>
          <dd>{data.paid_orders} 单</dd>
          <span>按支付成功订单计数（上期 {data.prev_paid_orders} 单）</span>
        </div>
        <div className={styles.metricItem}>
          <dt>客单价</dt>
          <dd>{formatMoney(data.avg_order_value)}</dd>
          <span>支付销售额 ÷ 支付订单数</span>
        </div>
        <div className={styles.metricItem}>
          <dt>退款率</dt>
          <dd>{Number(data.refund_rate).toFixed(2)}%</dd>
          <span>成功退款订单 {data.refunded_orders} 单 ÷ 支付订单数</span>
        </div>
      </dl>

      <div className={styles.mainGrid}>
        <section className={styles.panel} aria-labelledby="sales-trend-heading">
          <div className={styles.panelHeader}>
            <h2 id="sales-trend-heading">销售额走势</h2>
            <span className={styles.pill}>按支付时间</span>
          </div>
          <EChartBox
            option={trendOption}
            height={250}
            ariaLabel={`${shopName} 近 ${days} 日销售额趋势图`}
          />
        </section>

        <section className={styles.panel} aria-labelledby="sku-rank-heading">
          <div className={styles.panelHeader}>
            <h2 id="sku-rank-heading">SKU 销量排行</h2>
            <span className={styles.pillMint}>支付件数</span>
          </div>
          {data.sku_ranking.length === 0 ? (
            <p className={styles.mutedText}>统计区间内暂无已支付 SKU 销售明细。</p>
          ) : (
            <ul className={styles.rankList}>
              {data.sku_ranking.map((item, idx) => {
                const widthPct = Math.max(8, Math.round((item.units_sold / maxUnits) * 100))
                return (
                  <li key={item.sku_code} className={styles.rankRow}>
                    <span className={styles.rankCode}>{item.sku_code}</span>
                    <div className={styles.rankTrack}>
                      <div
                        className={`${styles.rankFill} ${idx > 0 ? styles.rankFillSecondary : ""}`}
                        style={{ width: `${widthPct}%` }}
                      />
                    </div>
                    <span className={styles.rankQty}>{item.units_sold} 件</span>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </div>

      <section className={styles.methodologyCard} aria-labelledby="overview-method-heading">
        <h2 id="overview-method-heading">数据口径</h2>
        <div className={styles.noticeBox}>{data.methodology}</div>
      </section>
    </section>
  )
}

function DailyReportTab(props: AnalyticsPageProps) {
  const query = useDailyReportQuery(props, true)
  const data = query.isSuccess ? query.data : undefined

  if (query.isPending || query.isRefetching) {
    return <div className={styles.feedback}><Spin size="small" /> 正在生成经营日报…</div>
  }
  if (query.isError || !data) {
    return <ErrorBox error={query.error} onRetry={() => { void query.refetch() }} onSignIn={props.onSignIn} />
  }

  const m = data.metrics
  const pctStr = m.gmv_change_pct !== null
    ? `较 ${m.prev_date.slice(5)} ${Number(m.gmv_change_pct) >= 0 ? "+" : ""}${m.gmv_change_pct}%`
    : "按支付口径统计"

  return (
    <section aria-labelledby="daily-report-heading">
      <dl className={styles.metricsBand}>
        <div className={`${styles.metricItem} ${styles.metricHighlight}`}>
          <dt>支付销售额</dt>
          <dd>{formatMoney(m.paid_gmv, true)}</dd>
          <span>{pctStr}</span>
        </div>
        <div className={styles.metricItem}>
          <dt>支付订单</dt>
          <dd>{m.paid_orders} 单</dd>
          <span>支付成功</span>
        </div>
        <div className={styles.metricItem}>
          <dt>客单价</dt>
          <dd>{formatMoney(m.avg_order_value)}</dd>
          <span>支付销售额 ÷ 支付订单数</span>
        </div>
        <div className={styles.metricItem}>
          <dt>退款率</dt>
          <dd>{Number(m.refund_rate).toFixed(2)}%</dd>
          <span>{m.refunded_orders} 单 / {m.paid_orders} 单</span>
        </div>
      </dl>

      <div className={styles.mainGrid}>
        <section className={styles.panel} aria-labelledby="report-facts-heading">
          <div className={styles.panelHeader}>
            <h2 id="report-facts-heading">经营摘要</h2>
            <span className={styles.pillMint}>数据事实</span>
          </div>
          <p className={styles.headline}>{data.headline}</p>
          <ul className={styles.factsList}>
            {data.facts.map(fact => (
              <li key={fact}>{fact}</li>
            ))}
          </ul>
          <h3 className={styles.subSectionTitle}>行动建议</h3>
          {data.recommendations.map(rec => (
            <p key={rec} className={styles.mutedText}>{rec}</p>
          ))}
          <div style={{ marginTop: 12 }}>
            <Link className={styles.outlineLink} to="/catalog?tab=inventory">查看库存预警</Link>
          </div>
        </section>

        <aside className={styles.sideStack}>
          <section className={styles.panel} aria-labelledby="report-boundary-heading">
            <div className={styles.panelHeader}>
              <h2 id="report-boundary-heading">分析边界</h2>
            </div>
            <div className={styles.noticeBox}>{data.analysis_boundary}</div>
          </section>

          <section className={styles.panel} aria-labelledby="report-method-heading">
            <div className={styles.panelHeader}>
              <h2 id="report-method-heading">查询口径</h2>
              <span className={styles.pillMint}>数据库事实</span>
            </div>
            <dl className={styles.metaRows}>
              <div>
                <dt>时间区间</dt>
                <dd>{m.report_date} 00:00（含）至次日 00:00（不含）</dd>
              </div>
              <div>
                <dt>比较区间</dt>
                <dd>{m.prev_date} 前一完整自然日</dd>
              </div>
              <div>
                <dt>数据范围</dt>
                <dd>{data.shop_name} · {m.timezone}</dd>
              </div>
            </dl>
          </section>
        </aside>
      </div>
    </section>
  )
}

const turnoverColumns: TableColumnsType<SkuTurnoverItem> = [
  {
    title: "SKU / 商品",
    width: "32%",
    dataIndex: "sku_code",
    render: (code: string, row) => (
      <div>
        <strong style={{ display: "block" }}>{code}</strong>
        <span style={{ color: "#71717a", fontSize: 12 }}>{row.product_name} · {row.category}</span>
      </div>
    ),
  },
  { title: "售出件数", width: "16%", dataIndex: "units_sold", align: "right", className: styles.number },
  { title: "当前可售", width: "16%", dataIndex: "available_qty", align: "right", className: styles.number },
  {
    title: "周转率",
    width: "16%",
    dataIndex: "turnover_rate",
    align: "right",
    className: styles.number,
    render: (val: string) => `${Number(val).toFixed(2)}`,
  },
  {
    title: "提示",
    width: "20%",
    render: (_, row) => (
      row.low_stock
        ? <span className={styles.pillWarn}>低库存</span>
        : <span className={styles.pill}>正常</span>
    ),
  },
]

function TurnoverTab({ days, ...props }: AnalyticsPageProps & { days: number }) {
  const query = useInventoryTurnoverQuery(props, days, true)
  const data = query.isSuccess ? query.data : undefined

  if (query.isPending || query.isRefetching) {
    return <div className={styles.feedback}><Spin size="small" /> 正在统计库存周转与异常…</div>
  }
  if (query.isError || !data) {
    return <ErrorBox error={query.error} onRetry={() => { void query.refetch() }} onSignIn={props.onSignIn} />
  }

  return (
    <section aria-labelledby="turnover-heading">
      <dl className={`${styles.metricsBand} ${styles.metricsBandThree}`}>
        <div className={`${styles.metricItem} ${styles.metricHighlight}`}>
          <dt>库存周转率（件数口径）</dt>
          <dd>{Number(data.overall_turnover_rate).toFixed(2)} 次</dd>
          <span>售出 {data.total_units_sold} 件 / 当前可售 {data.total_available_qty} 件</span>
        </div>
        <div className={styles.metricItem}>
          <dt>低库存 SKU</dt>
          <dd>{data.low_stock_count} 个</dd>
          <span>当前可售低于预警线 {data.low_stock_threshold} 件</span>
        </div>
        <div className={styles.metricItem}>
          <dt>待追踪指标</dt>
          <dd>{data.anomalies.length} 项</dd>
          <span>库存预警与退款波动</span>
        </div>
      </dl>

      <div className={styles.mainGrid}>
        <section className={styles.panel} aria-labelledby="turnover-table-heading">
          <div className={styles.panelHeader}>
            <h2 id="turnover-table-heading">SKU 库存周转明细</h2>
            <span className={styles.pill}>{data.items.length} 个在售 SKU</span>
          </div>
          <Table<SkuTurnoverItem>
            className={styles.table}
            rowKey="sku_code"
            columns={turnoverColumns}
            pagination={false}
            tableLayout="fixed"
            dataSource={data.items}
            rowClassName={row => (row.low_stock ? styles.lowStockRow : "")}
          />
        </section>

        <aside className={styles.sideStack}>
          <section className={styles.panel} aria-labelledby="anomalies-heading">
            <div className={styles.panelHeader}>
              <h2 id="anomalies-heading">异常指标</h2>
            </div>
            {data.anomalies.length === 0 ? (
              <p className={styles.mutedText}>当前周期暂无库存预警或退款率异常上升提示。</p>
            ) : (
              <ul className={styles.anomalyList}>
                {data.anomalies.map(item => (
                  <li key={item.title} className={styles.anomalyItem}>
                    <div className={styles.anomalyTop}>
                      <strong>{item.title}</strong>
                      <span className={styles.pillWarn}>{item.badge}</span>
                    </div>
                    <p>{item.detail}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={styles.panel} aria-labelledby="turnover-method-heading">
            <div className={styles.panelHeader}>
              <h2 id="turnover-method-heading">口径与建议</h2>
            </div>
            <div className={styles.noticeBox}>{data.methodology}</div>
          </section>
        </aside>
      </div>
    </section>
  )
}

export default function AnalyticsPage(props: AnalyticsPageProps) {
  const [searchParams] = useSearchParams()
  const paramTab = searchParams.get("tab")
  const initialTab: AnalyticsTab =
    paramTab === "report" || paramTab === "turnover" ? paramTab : "overview"
  const [activeTab, setActiveTab] = useState<AnalyticsTab>(initialTab)
  const [days, setDays] = useState<number>(7)

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span>
        <strong aria-current="page">经营分析</strong>
      </nav>
      <div className={styles.headerBar}>
        <div>
          <h1>{activeTab === "report" ? "经营日报" : activeTab === "turnover" ? "库存周转与异常指标" : "经营分析"}</h1>
          <p className={styles.summary}>
            {props.shopName} · 查看销售、客单价、退款与库存周转表现；指标均由数据库事实聚合。
          </p>
        </div>
        <div className={styles.headerActions}>
          {activeTab !== "report" && (
            <Select<number>
              className={styles.rangeSelect}
              aria-label="统计周期"
              value={days}
              virtual={false}
              options={[
                { value: 7, label: "近 7 个自然日" },
                { value: 14, label: "近 14 个自然日" },
              ]}
              onChange={setDays}
            />
          )}
          <Button
            onClick={() => setActiveTab(activeTab === "report" ? "overview" : "report")}
          >
            {activeTab === "report" ? "返回指标看板" : "查看经营日报"}
          </Button>
        </div>
      </div>

      <div className={styles.viewTabs} role="tablist" aria-label="经营分析子视图">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "overview"}
          className={`${styles.viewTab} ${activeTab === "overview" ? styles.viewTabActive : ""}`}
          onClick={() => setActiveTab("overview")}
        >
          指标看板
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "report"}
          className={`${styles.viewTab} ${activeTab === "report" ? styles.viewTabActive : ""}`}
          onClick={() => setActiveTab("report")}
        >
          分析报告
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "turnover"}
          className={`${styles.viewTab} ${activeTab === "turnover" ? styles.viewTabActive : ""}`}
          onClick={() => setActiveTab("turnover")}
        >
          库存周转与异常
        </button>
      </div>

      {activeTab === "overview" && (
        <OverviewTab {...props} days={days} onGoReport={() => setActiveTab("report")} />
      )}
      {activeTab === "report" && <DailyReportTab {...props} />}
      {activeTab === "turnover" && <TurnoverTab {...props} days={days} />}
    </main>
  )
}
