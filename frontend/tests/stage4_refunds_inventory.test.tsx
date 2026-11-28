import { fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import AppRouter from "../src/router"

const operatorSession = {
  access_token: "stage4-ops-token",
  user: { id: 1, username: "ops.demo", name: "沙箱运营" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["operator"] }],
}

const approverSession = {
  access_token: "stage4-approver-token",
  user: { id: 2, username: "approver.demo", name: "沙箱审批员" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["approver"] }],
}

const inventoryOverview = {
  low_stock_threshold: 30,
  low_stock_count: 1,
  items: [
    {
      sku_id: 5,
      sku_code: "SKU-005",
      product_name: "硅胶杯盖",
      category: "配件",
      status: "active",
      available_qty: 18,
      reserved_qty: 2,
      total_qty: 20,
      version: 2,
      low_stock: true,
      low_stock_threshold: 30,
    },
  ],
}

const inventoryMovements = [
  {
    id: 11,
    sku_id: 5,
    sku_code: "SKU-005",
    product_name: "硅胶杯盖",
    movement_type: "reserve",
    quantity: 2,
    available_delta: -2,
    reserved_delta: 2,
    before_available: 20,
    after_available: 18,
    before_reserved: 0,
    after_reserved: 2,
    reference_id: "ORD-20261111-01",
    note: "待发货预占",
    actor_id: 1,
    actor_name: "沙箱运营",
    created_at: "2026-11-21T02:00:00Z",
  },
]

const orderDetailReady = {
  id: 31,
  order_no: "ORD-20261111-01",
  status: "paid",
  fulfillment_status: "pending_shipment",
  exception_type: "inventory_review",
  exception_note: "待发货订单关联商品库存记录待复核，请先核对库存预占与流水后再决定是否发货。",
  paid_amount: "398.00",
  refunded_amount: "0.00",
  in_flight_refund_amount: "0.00",
  paid_at: "2026-11-11T02:20:00Z",
  created_at: "2026-11-11T02:20:00Z",
  sku_codes: ["SKU-001"],
  quantity: 2,
  items: [
    { id: 1, sku_code: "SKU-001", product_name: "轻食谷物礼盒", quantity: 2, unit_price: "199.00", paid_amount: "398.00" },
  ],
  refundable_amount: "398.00",
  can_refund: true,
  refund_block_reason: null,
  refunds: [],
}

const refundDetailPending = {
  id: 71,
  refund_no: "RFD-20261121-01",
  order_id: 31,
  order_no: "ORD-20261111-01",
  order_status: "paid",
  order_paid_amount: "398.00",
  amount: "89.00",
  reason: "部分商品破损",
  status: "pending_approval",
  requester_id: 1,
  requester_name: "沙箱运营",
  failure_reason: null,
  processed_at: null,
  approval_id: 91,
  approval_no: "APR-20261121-05",
  approval_status: "pending",
  created_at: "2026-11-21T03:00:00Z",
  updated_at: "2026-11-21T03:00:00Z",
  refunded_amount: "0.00",
  refundable_amount: "398.00",
  job_id: null,
  job_no: null,
  job_status: null,
  audit_logs: [
    {
      id: 1,
      action: "refund.submitted",
      actor_id: 1,
      actor_name: "沙箱运营",
      before_json: null,
      after_json: { status: "pending_approval" },
      metadata_json: null,
      created_at: "2026-11-21T03:00:00Z",
    },
  ],
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

describe("阶段 4 退款、库存并发与售后 Agent", () => {
  it("switches to inventory tab, submits optimistic-locked movement, and surfaces version conflict", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json([]))
      .mockResolvedValueOnce(Response.json(inventoryOverview))
      .mockResolvedValueOnce(Response.json(inventoryMovements))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ code: "INVENTORY_VERSION_CONFLICT", message: "库存快照版本冲突：请刷新后重试。" }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(Response.json(inventoryOverview))
      .mockResolvedValueOnce(Response.json(inventoryMovements))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/catalog")
    render(<AppRouter />)

    fireEvent.click(await screen.findByRole("tab", { name: "库存与流水" }))
    expect(await screen.findByText("18 件")).toBeInTheDocument()
    expect(screen.getByText("当前 SKU · 快照 V2")).toBeInTheDocument()
    expect(screen.getByText(/通知仓库并不等于库存预占/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "库存操作" }))
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "提交变更" }))

    expect(await screen.findByText(/库存快照版本冲突/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/shops/19/inventory/SKU-005/movements",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": expect.stringMatching(/^inv-19-SKU-005-/) }),
      }),
    )
  })

  it("displays refund eligibility on order detail, submits refund, and supports withdrawal on refund detail", async () => {
    const orderBlockedAfterSubmit = {
      ...orderDetailReady,
      in_flight_refund_amount: "89.00",
      can_refund: false,
      refund_block_reason: "订单存在在途退款申请 RFD-20261121-01，请勿重复发起。",
      refunds: [
        {
          id: 71,
          refund_no: "RFD-20261121-01",
          amount: "89.00",
          reason: "部分商品破损",
          status: "pending_approval",
          created_at: "2026-11-21T03:00:00Z",
        },
      ],
    }
    const cancelledRefund = { ...refundDetailPending, status: "cancelled", approval_status: "cancelled" }

    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json(orderDetailReady))
      .mockResolvedValueOnce(Response.json({
        refund_id: 71,
        refund_no: "RFD-20261121-01",
        order_id: 31,
        order_no: "ORD-20261111-01",
        amount: "89.00",
        status: "pending_approval",
        approval_id: 91,
        approval_no: "APR-20261121-05",
        approval_status: "pending",
        replayed: false,
        message: "已提交退款审批（审批单 APR-20261121-05）",
      }))
      .mockResolvedValueOnce(Response.json(orderBlockedAfterSubmit))
      .mockResolvedValueOnce(Response.json(refundDetailPending))
      .mockResolvedValueOnce(Response.json(cancelledRefund))
      .mockResolvedValueOnce(Response.json(cancelledRefund))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/orders/31")
    render(<AppRouter />)

    expect(await screen.findByText("退款资格校验")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "申请退款" }))
    const modal = await screen.findByRole("dialog")
    fireEvent.change(within(modal).getByPlaceholderText(/例如：部分商品破损/), { target: { value: "部分商品破损" } })
    fireEvent.click(within(modal).getByRole("button", { name: "提交退款审批" }))

    expect(await screen.findByText("订单存在在途退款申请 RFD-20261121-01，请勿重复发起。")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("link", { name: "查看退款详情" }))

    expect(await screen.findByRole("heading", { name: "退款详情" })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole("button", { name: "撤回退款申请" }))
    expect(await screen.findByText("已撤回")).toBeInTheDocument()
  })

  it("reviews refund approval snapshot and dispatches execution job to queue", async () => {
    const approvalDetail = {
      id: 91,
      approval_no: "APR-20261121-05",
      action_type: "refund_create",
      resource_type: "refund",
      resource_id: 71,
      resource_no: "RFD-20261121-01",
      resource_name: "ORD-20261111-01 · 部分商品破损",
      resource_version: 1,
      sku_code: null,
      snapshot_amount: "89.00",
      status: "pending",
      requester_id: 1,
      requester_name: "沙箱运营",
      approver_id: null,
      approver_name: null,
      decision_comment: null,
      decided_at: null,
      created_at: "2026-11-21T03:00:00Z",
      can_decide: true,
      shop_id: 19,
      shop_name: "轻食旗舰店",
      payload_snapshot: {
        refund_no: "RFD-20261121-01",
        order_no: "ORD-20261111-01",
        order_paid_amount: "398.00",
        refunded_amount: "0.00",
        refundable_amount: "398.00",
        amount: "89.00",
        reason: "部分商品破损",
      },
      resource_status: "pending_approval",
      resource_failure_reason: null,
      resource_conflict_promotion_no: null,
      cannot_decide_reason: null,
      audit_logs: [],
    }
    const decidedDetail = { ...approvalDetail, status: "approved", can_decide: false, approver_name: "沙箱审批员" }

    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(approverSession))
      .mockResolvedValueOnce(Response.json(approvalDetail))
      .mockResolvedValueOnce(Response.json({
        approval: decidedDetail,
        execution_status: "queued",
        execution_message: "审批已通过，退款执行任务 JOB-20261121-02 已入队等待后台复核并执行。",
        job_id: 202,
        job_no: "JOB-20261121-02",
      }))
      .mockResolvedValueOnce(Response.json(decidedDetail))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/approvals/91")
    render(<AppRouter />)

    expect(await screen.findByRole("heading", { name: "审阅退款申请" })).toBeInTheDocument()
    expect(screen.getByText("退款参数快照")).toBeInTheDocument()
    expect(screen.getByText("¥89.00")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "批准退款申请" }))
    expect(await screen.findByText(/退款执行任务 JOB-20261121-02 已入队/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "查看执行任务" })).toHaveAttribute("href", "/tasks/202")
  })

  it("previews refund plan in Agent workspace and confirms approval submission", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json({
        kind: "refund_preview",
        message: "已核对订单 ORD-20261111-01，拟申请退款 ¥89.00。",
        session_id: 66,
        task_id: 99,
        tool_calls: 1,
        sales: null,
        refund: {
          order_id: 31,
          order_no: "ORD-20261111-01",
          order_status: "paid",
          paid_amount: "398.00",
          refunded_amount: "0.00",
          in_flight_amount: "0.00",
          refundable_amount: "398.00",
          proposed_amount: "89.00",
          reason: "部分商品破损",
          projected_order_status: "partially_refunded",
          eligible: true,
          block_reason: null,
          plan_key: "refund-plan-abc123",
        },
      }))
      .mockResolvedValueOnce(Response.json({
        refund_id: 71,
        refund_no: "RFD-20261121-01",
        order_id: 31,
        order_no: "ORD-20261111-01",
        amount: "89.00",
        status: "pending_approval",
        approval_id: 91,
        approval_no: "APR-20261121-05",
        approval_status: "pending",
        replayed: false,
        message: "已提交退款审批。",
      }))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/")
    render(<AppRouter />)

    fireEvent.change(await screen.findByRole("textbox", { name: "输入查数问题" }), {
      target: { value: "给 ORD-20261111-01 退 89 元，原因部分商品破损" },
    })
    fireEvent.click(screen.getByRole("button", { name: "发送" }))

    expect(await screen.findByText("订单退款方案 · ORD-20261111-01")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "确认并提交退款审批" }))

    expect(await screen.findByText("RFD-20261121-01")).toBeInTheDocument()
    expect(screen.getByText("APR-20261121-05")).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/shops/19/refunds",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "agent-session-66-refund-refund-plan-abc123" }),
      }),
    )
  })
})
