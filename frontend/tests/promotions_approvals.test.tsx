import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import AppRouter from "../src/router"

const operatorSession = {
  access_token: "ops-token",
  user: { id: 1, username: "ops.demo", name: "沙箱运营" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["operator"] }],
}

const approverSession = {
  access_token: "approver-token",
  user: { id: 2, username: "approver.demo", name: "沙箱审批员" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["approver"] }],
}

const promotionSummary = {
  id: 10,
  promotion_no: "PRM-20261121-01",
  name: "冬季暖饮专场",
  version: 1,
  status: "rejected",
  sku_code: "SKU-001",
  product_name: "轻食谷物礼盒",
  original_price: "199.00",
  cost_price: "119.00",
  discount: "8",
  promotion_price: "159.20",
  below_cost: false,
  starts_at: "2026-11-22T00:00:00+08:00",
  ends_at: "2026-11-25T00:00:00+08:00",
  duration_days: 3,
  minimum_qty: 100,
  timezone: "Asia/Shanghai",
  created_by: 1,
  creator_name: "沙箱运营",
  latest_approval_id: 21,
  latest_approval_no: "APR-20261121-01",
  latest_approval_status: "rejected",
  failure_reason: null,
  conflict_promotion_no: null,
  created_at: "2026-11-21T02:24:00Z",
  updated_at: "2026-11-21T02:28:00Z",
}

const promotionDetail = {
  ...promotionSummary,
  item: {
    sku_id: 1,
    sku_code: "SKU-001",
    product_name: "轻食谷物礼盒",
    original_price: "199.00",
    cost_price: "119.00",
    discount: "8",
    discount_rate: "0.8000",
    promotion_price: "159.20",
    below_cost: false,
  },
  approvals: [
    {
      id: 21,
      approval_no: "APR-20261121-01",
      resource_version: 1,
      status: "rejected",
      requester_id: 1,
      requester_name: "沙箱运营",
      approver_id: 2,
      approver_name: "沙箱审批员",
      decision_comment: "档期延后",
      decided_at: "2026-11-21T02:28:00Z",
      created_at: "2026-11-21T02:24:00Z",
    },
  ],
  audit_logs: [
    {
      id: 1,
      action: "promotion.submitted",
      actor_id: 1,
      actor_name: "沙箱运营",
      before_json: null,
      after_json: { version: 1, status: "pending_approval" },
      metadata_json: null,
      created_at: "2026-11-21T02:24:00Z",
    },
  ],
}

const approvalDetail = {
  id: 21,
  approval_no: "APR-20261121-01",
  action_type: "promotion_publish",
  resource_type: "promotion",
  resource_id: 10,
  resource_no: "PRM-20261121-01",
  resource_name: "冬季暖饮专场",
  resource_version: 1,
  sku_code: "SKU-001",
  snapshot_amount: "159.20",
  status: "pending",
  requester_id: 1,
  requester_name: "沙箱运营",
  approver_id: null,
  approver_name: null,
  decision_comment: null,
  decided_at: null,
  created_at: "2026-11-21T02:24:00Z",
  can_decide: true,
  shop_id: 19,
  shop_name: "轻食旗舰店",
  payload_snapshot: {
    promotion_no: "PRM-20261121-01",
    promotion_name: "冬季暖饮专场",
    sku_code: "SKU-001",
    product_name: "轻食谷物礼盒",
    original_price: "199.00",
    cost_price: "119.00",
    discount: "8",
    promotion_price: "159.20",
    starts_at: "2026-11-22T00:00:00+08:00",
    ends_at: "2026-11-25T00:00:00+08:00",
    duration_days: 3,
    minimum_qty: 100,
    available_qty_snapshot: 300,
    timezone: "Asia/Shanghai",
    risk_warnings: [],
    sales_7d: { units_sold: 120, start_date: "2026-11-14", end_date: "2026-11-21" },
  },
  resource_status: "pending_approval",
  resource_failure_reason: null,
  resource_conflict_promotion_no: null,
  cannot_decide_reason: null,
  audit_logs: [],
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

describe("促销管理与审批中心", () => {
  it("loads promotion detail and submits a V2 revision for a rejected promotion", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json(promotionDetail))
      .mockResolvedValueOnce(Response.json({
        promotion_id: 10, promotion_no: "PRM-20261121-01", name: "冬季暖饮专场", version: 2,
        status: "pending_approval", approval_id: 22, approval_no: "APR-20261121-02", approval_status: "pending",
        sku_code: "SKU-001", original_price: "199.00", cost_price: "119.00", promotion_price: "159.20",
        below_cost: false, risk_warnings: [], starts_at: "2026-11-26T00:00:00+08:00",
        ends_at: "2026-11-29T00:00:00+08:00", timezone: "Asia/Shanghai", replayed: false,
      }))
      .mockResolvedValueOnce(Response.json({ ...promotionDetail, version: 2, status: "pending_approval" }))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/promotions/10")
    render(<AppRouter />)

    expect(await screen.findByRole("heading", { name: "冬季暖饮专场" })).toBeInTheDocument()
    expect(screen.getByText("¥159.20")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "修订为 V2 并重新送审" }))
    fireEvent.change(screen.getByLabelText("新开始日期"), { target: { value: "2026-11-26" } })
    fireEvent.click(screen.getByRole("button", { name: "提交 V2 审批" }))

    expect(await screen.findByText("版本 V2")).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/shops/19/promotions/10/revise",
      expect.objectContaining({ method: "POST" }),
    )
  })

  it("lets an authorized approver approve a snapshot and blocks self-approval in UI", async () => {
    const approvedDetail = {
      ...approvalDetail,
      status: "approved",
      can_decide: false,
      approver_id: 2,
      approver_name: "沙箱审批员",
      decided_at: "2026-11-21T02:30:00Z",
      resource_status: "scheduled",
      cannot_decide_reason: "该审批单已完成处理或已撤回，不可再次裁决。",
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(approverSession))
      .mockResolvedValueOnce(Response.json(approvalDetail))
      .mockResolvedValueOnce(Response.json({
        approval: approvedDetail,
        execution_status: "scheduled",
        execution_message: "业务复核通过，活动 PRM-20261121-01 已成功排期。",
      }))
      .mockResolvedValueOnce(Response.json(approvedDetail))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/approvals/21")
    render(<AppRouter />)

    expect(await screen.findByText("审批参数快照")).toBeInTheDocument()
    expect(screen.getByText("有权审批")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "批准此版本" }))

    expect(await screen.findByText(/业务复核通过，活动 PRM-20261121-01 已成功排期/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/shops/19/approvals/21/approve",
      expect.objectContaining({ method: "POST" }),
    )
  })
})
