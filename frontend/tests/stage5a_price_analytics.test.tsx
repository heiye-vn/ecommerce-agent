import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import AppRouter from "../src/router"

const operatorSession = {
  access_token: "stage5a-ops-token",
  user: { id: 1, username: "ops.demo", name: "沙箱运营" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["operator"] }],
}

const approverSession = {
  access_token: "stage5a-approver-token",
  user: { id: 2, username: "approver.demo", name: "沙箱审批员" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["approver"] }],
}

const catalogList = [
  {
    sku_code: "SKU-001",
    product_name: "轻食谷物礼盒",
    category: "食品",
    status: "active",
    current_price: "199.00",
    available_qty: 300,
    reserved_qty: 0,
  },
]

const priceHistories = [
  {
    id: 1,
    sku_id: 1,
    sku_code: "SKU-001",
    product_name: "轻食谷物礼盒",
    old_price: "209.00",
    new_price: "199.00",
    changed_at: "2026-11-08T02:30:00Z",
    actor_id: 1,
    actor_name: "沙箱运营",
    approval_no: "APR-20261108-04",
    price_change_request_id: null,
    reason: "常规价格调整",
  },
]

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

describe("阶段 5A 调价审批闭环、经营分析与活动复盘", () => {
  it("supports pricing tab submission and surfaces drift failure detail on catalog page", async () => {
    const failedSummary = {
      id: 77,
      request_no: "PRC-20261121-01",
      sku_id: 1,
      sku_code: "SKU-001",
      product_name: "轻食谷物礼盒",
      old_price: "199.00",
      new_price: "189.00",
      cost_price: "119.00",
      reason: "调整冬季常规售价",
      status: "failed",
      requester_id: 1,
      requester_name: "沙箱运营",
      failure_reason: "审批快照的原价为 ¥199.00，执行时读到 ¥195.00。本次未修改售价。",
      executed_at: null,
      approval_id: 95,
      approval_no: "APR-20261121-09",
      approval_status: "approved",
      created_at: "2026-11-21T02:45:00Z",
      updated_at: "2026-11-21T02:50:00Z",
    }
    const failedDetail = {
      ...failedSummary,
      current_price: "195.00",
      sku_status: "active",
      job_id: 303,
      job_no: "JOB-20261121-08",
      job_status: "failed",
      audit_logs: [
        {
          id: 1,
          action: "price_change.execution_failed",
          actor_id: 2,
          actor_name: "沙箱审批员",
          before_json: null,
          after_json: { status: "failed" },
          metadata_json: null,
          created_at: "2026-11-21T02:50:00Z",
        },
      ],
    }

    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json(catalogList))
      .mockResolvedValueOnce(Response.json(priceHistories))
      .mockResolvedValueOnce(Response.json([]))
      .mockResolvedValueOnce(Response.json({
        request_id: 77,
        request_no: "PRC-20261121-01",
        sku_code: "SKU-001",
        old_price: "199.00",
        new_price: "189.00",
        status: "pending_approval",
        approval_id: 95,
        approval_no: "APR-20261121-09",
        approval_status: "pending",
        replayed: false,
        message: "已提交调价审批（审批单 APR-20261121-09）",
      }))
      .mockResolvedValueOnce(Response.json(priceHistories))
      .mockResolvedValueOnce(Response.json([failedSummary]))
      .mockResolvedValueOnce(Response.json(failedDetail))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/catalog")
    render(<AppRouter />)

    fireEvent.click(await screen.findByRole("tab", { name: "调价记录" }))
    expect(await screen.findByText("APR-20261108-04")).toBeInTheDocument()

    const priceInput = screen.getByPlaceholderText("例如 189.00")
    fireEvent.change(priceInput, { target: { value: "189" } })
    fireEvent.change(screen.getByPlaceholderText("例如：调整冬季常规售价"), {
      target: { value: "调整冬季常规售价" },
    })
    fireEvent.click(screen.getByRole("button", { name: "提交调价审批" }))

    expect(await screen.findByText("PRC-20261121-01")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("link", { name: "查看原因" }))

    expect(await screen.findByRole("heading", { name: "调价复核失败" })).toBeInTheDocument()
    expect(await screen.findByText(/审批快照的原价为 ¥199.00/)).toBeInTheDocument()
    expect(screen.getByText("¥195.00")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "按最新售价重新发起" })).toBeInTheDocument()
  })

  it("reviews price change approval snapshot and dispatches job to queue", async () => {
    const detail = {
      id: 95,
      approval_no: "APR-20261121-09",
      action_type: "price_change",
      resource_type: "price_change",
      resource_id: 77,
      resource_no: "PRC-20261121-01",
      resource_name: "SKU-001 · 轻食谷物礼盒",
      resource_version: 1,
      sku_code: "SKU-001",
      snapshot_amount: "189.00",
      status: "pending",
      requester_id: 1,
      requester_name: "沙箱运营",
      approver_id: null,
      approver_name: null,
      decision_comment: null,
      decided_at: null,
      created_at: "2026-11-21T02:45:00Z",
      can_decide: true,
      shop_id: 19,
      shop_name: "轻食旗舰店",
      payload_snapshot: {
        request_no: "PRC-20261121-01",
        sku_code: "SKU-001",
        product_name: "轻食谷物礼盒",
        old_price: "199.00",
        new_price: "189.00",
        cost_price: "119.00",
        change_pct: "-5.03",
        reason: "调整冬季常规售价",
      },
      resource_status: "pending_approval",
      resource_failure_reason: null,
      resource_conflict_promotion_no: null,
      cannot_decide_reason: null,
      audit_logs: [],
    }
    const decided = { ...detail, status: "approved", can_decide: false, approver_name: "沙箱审批员" }

    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(approverSession))
      .mockResolvedValueOnce(Response.json(detail))
      .mockResolvedValueOnce(Response.json({
        approval: decided,
        execution_status: "queued",
        execution_message: "审批已通过，调价执行任务 JOB-20261121-08 已入队等待后台复核并生效。",
        job_id: 303,
        job_no: "JOB-20261121-08",
      }))
      .mockResolvedValueOnce(Response.json(decided))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/approvals/95")
    render(<AppRouter />)

    expect(await screen.findByRole("heading", { name: "审阅调价申请" })).toBeInTheDocument()
    expect(await screen.findByText("调价参数快照")).toBeInTheDocument()
    expect(screen.getByText("-5.03%")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "批准调价申请" }))
    expect(await screen.findByText(/调价执行任务 JOB-20261121-08 已入队/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "查看执行任务" })).toHaveAttribute("href", "/tasks/303")
  })

  it("renders analytics overview, daily report, and inventory turnover tabs cleanly", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json({
        shop_id: 19,
        shop_name: "轻食旗舰店",
        timezone: "Asia/Shanghai",
        days: 7,
        start_date: "2026-11-14",
        end_date: "2026-11-21",
        prev_start_date: "2026-11-07",
        prev_end_date: "2026-11-14",
        paid_gmv: "112080.00",
        prev_paid_gmv: "91050.00",
        gmv_change_pct: "23.1",
        paid_orders: 842,
        prev_paid_orders: 710,
        avg_order_value: "133.11",
        prev_avg_order_value: "128.24",
        refunded_orders: 16,
        refund_rate: "1.90",
        prev_refund_rate: "1.28",
        trend: [
          { date_label: "11/14", full_date: "2026-11-14", current_gmv: "12000.00", previous_gmv: "10500.00", current_orders: 90, previous_orders: 80 },
        ],
        sku_ranking: [
          { sku_code: "SKU-001", product_name: "轻食谷物礼盒", units_sold: 120, paid_gmv: "23880.00" },
        ],
        methodology: "当前区间按支付口径统计；客单价 = 支付销售额 ÷ 支付订单数。",
      }))
      .mockResolvedValueOnce(Response.json({
        shop_id: 19,
        shop_name: "轻食旗舰店",
        metrics: {
          report_date: "2026-11-20",
          prev_date: "2026-11-19",
          timezone: "Asia/Shanghai",
          paid_gmv: "19680.00",
          prev_paid_gmv: "20400.00",
          gmv_delta: "-720.00",
          gmv_change_pct: "-3.5",
          paid_orders: 148,
          avg_order_value: "132.97",
          refunded_orders: 3,
          refunded_amount: "237.00",
          refund_rate: "2.03",
        },
        headline: "销售较前日回落 ¥720.00，低库存 SKU 需优先核查",
        facts: ["SKU-001 近 7 个完整自然日累计售出 120 件。"],
        analysis_boundary: "当前报表仅反映本店订单与库存事实，无法直接证明因果关系。",
        recommendations: ["优先核查 SKU-005 库存流水。"],
        methodology: "时间区间 11/20 至 11/21。",
      }))
      .mockResolvedValueOnce(Response.json({
        shop_id: 19,
        shop_name: "轻食旗舰店",
        timezone: "Asia/Shanghai",
        days: 7,
        start_date: "2026-11-14",
        end_date: "2026-11-21",
        total_units_sold: 406,
        total_available_qty: 1128,
        overall_turnover_rate: "0.36",
        low_stock_count: 1,
        low_stock_threshold: 30,
        items: [
          {
            sku_code: "SKU-005",
            product_name: "硅胶杯盖",
            category: "配件",
            units_sold: 42,
            available_qty: 18,
            reserved_qty: 2,
            turnover_rate: "2.33",
            low_stock: true,
            low_stock_threshold: 30,
          },
        ],
        anomalies: [
          {
            kind: "low_stock",
            title: "SKU-005 可售 18 件，低于预警线 30 件",
            detail: "近 7 日售出 42 件。",
            badge: "低库存",
            severity: "warning",
          },
        ],
        methodology: "周转率 = 当期支付件数 ÷ 当前可售库存（按件数示例口径，非财务成本周转率）。",
      }))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/analytics")
    render(<AppRouter />)

    expect(await screen.findByText("客单价")).toBeInTheDocument()
    expect(screen.getByText("¥133.11")).toBeInTheDocument()
    expect(screen.getByText("120 件")).toBeInTheDocument()
    expect(screen.getByRole("img", { name: /销售额趋势图/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole("tab", { name: "分析报告" }))
    expect(await screen.findByText(/销售较前日回落/)).toBeInTheDocument()
    expect(screen.getByText(/无法直接证明因果关系/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("tab", { name: "库存周转与异常" }))
    expect(await screen.findByText("0.36 次")).toBeInTheDocument()
    expect(screen.getByText(/非财务成本周转率/)).toBeInTheDocument()
  })

  it("displays promotion calendar and compares baseline vs promo period in review tab", async () => {
    const promo = {
      id: 55,
      promotion_no: "PRM-20261110-02",
      name: "咖啡器具周",
      version: 1,
      status: "completed",
      sku_code: "SKU-018",
      product_name: "便携咖啡手冲套装",
      original_price: "299.00",
      cost_price: "150.00",
      discount: "9",
      promotion_price: "269.10",
      below_cost: false,
      starts_at: "2026-11-12T00:00:00+08:00",
      ends_at: "2026-11-17T00:00:00+08:00",
      duration_days: 5,
      minimum_qty: 100,
      timezone: "Asia/Shanghai",
      created_by: 1,
      creator_name: "沙箱运营",
      latest_approval_id: null,
      latest_approval_no: null,
      latest_approval_status: null,
      failure_reason: null,
      conflict_promotion_no: null,
      created_at: "2026-11-10T01:00:00Z",
      updated_at: "2026-11-17T00:00:00Z",
    }

    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json([promo]))
      .mockResolvedValueOnce(Response.json(catalogList))
      .mockResolvedValueOnce(Response.json({
        promotion_id: 55,
        promotion_no: "PRM-20261110-02",
        promotion_name: "咖啡器具周",
        status: "completed",
        sku_codes: ["SKU-018"],
        timezone: "Asia/Shanghai",
        starts_at: "2026-11-12T00:00:00+08:00",
        ends_at: "2026-11-17T00:00:00+08:00",
        promo_start_date: "2026-11-12",
        promo_end_date: "2026-11-17",
        baseline_start_date: "2026-11-07",
        baseline_end_date: "2026-11-12",
        duration_days: 5,
        promo_units: 98,
        promo_gmv: "26362.00",
        baseline_units: 46,
        baseline_gmv: "13754.00",
        units_change_pct: "113.0",
        daily_comparison: [
          { day_label: "D1", promo_date: "11/12", baseline_date: "11/07", promo_units: 15, baseline_units: 8, promo_gmv: "4035.00", baseline_gmv: "2392.00" },
        ],
        methodology: "数据事实：活动期 5 个自然日对照基线期 5 个自然日。",
        review_note: "分析推测：无法仅凭前后对比单独归因于折扣。",
      }))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/promotions")
    render(<AppRouter />)

    expect(await screen.findByText("咖啡器具周")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("tab", { name: "促销日历" }))
    expect(await screen.findByText("2026 年 11 月")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("tab", { name: "活动复盘" }))
    expect(await screen.findByText("98 件")).toBeInTheDocument()
    expect(screen.getByText(/分析推测/)).toBeInTheDocument()
    expect(screen.getByRole("img", { name: /按日销量对比/ })).toBeInTheDocument()
  })

  it("previews price change plan in Agent workspace and confirms approval submission", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json({
        kind: "price_change_preview",
        message: "已读取 SKU-001 当前售价 ¥199.00，拟调整为 ¥189.00。",
        session_id: 88,
        task_id: 109,
        tool_calls: 1,
        sales: null,
        price_change: {
          sku_id: 1,
          sku_code: "SKU-001",
          product_name: "轻食谷物礼盒",
          sku_status: "active",
          current_price: "199.00",
          cost_price: "119.00",
          proposed_price: "189.00",
          change_amount: "-10.00",
          change_pct: "-5.03",
          reason: "调整冬季常规售价",
          last_old_price: "209.00",
          last_new_price: "199.00",
          last_changed_at: "2026-11-08T02:30:00Z",
          eligible: true,
          block_reason: null,
          plan_key: "prc-plan-xyz123",
        },
      }))
      .mockResolvedValueOnce(Response.json({
        request_id: 77,
        request_no: "PRC-20261121-01",
        sku_code: "SKU-001",
        old_price: "199.00",
        new_price: "189.00",
        status: "pending_approval",
        approval_id: 95,
        approval_no: "APR-20261121-09",
        approval_status: "pending",
        replayed: false,
        message: "已提交调价审批。",
      }))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/")
    render(<AppRouter />)

    fireEvent.change(await screen.findByRole("textbox", { name: "输入查数问题" }), {
      target: { value: "把 SKU-001 常规售价调到 189 元，原因调整冬季常规售价" },
    })
    fireEvent.click(screen.getByRole("button", { name: "发送" }))

    expect(await screen.findByRole("heading", { level: 3, name: "SKU-001 调价方案" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "确认并提交调价审批" }))

    expect(await screen.findByText("APR-20261121-09")).toBeInTheDocument()
    expect(screen.getByText("PRC-20261121-01")).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/shops/19/price-changes",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "agent-session-88-price-prc-plan-xyz123" }),
      }),
    )
  })
})
