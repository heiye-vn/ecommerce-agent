import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import AppRouter from "../src/router"

const message = "SKU-001 最近 7 天卖了多少件？"
const session = {
  access_token: "agent-test-token",
  user: { id: 1, username: "ops.demo", name: "沙箱运营" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["operator"] }],
}
const multiShopSession = {
  ...session,
  shops: [
    { id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["operator"] },
    { id: 27, organization_id: 1, name: "茶饮专营店", timezone: "Asia/Shanghai", roles: ["operator"] },
  ],
}

function openAgent(currentSession: typeof session, ...responses: Response[]) {
  const request = vi.fn().mockResolvedValueOnce(Response.json(currentSession))
  for (const response of responses) request.mockResolvedValueOnce(response)
  vi.stubGlobal("fetch", request)
  window.history.replaceState(null, "", "/")
  render(<AppRouter />)
  return request
}

async function sendQuestion(question = message) {
  fireEvent.change(await screen.findByRole("textbox", { name: "输入查数问题" }), { target: { value: question } })
  fireEvent.click(screen.getByRole("button", { name: "发送" }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

it("renders the authorized shop query result and its database evidence", async () => {
  const request = openAgent(session, Response.json({
    kind: "sales", message: "SKU-001 共售出 120 件。", session_id: 101, task_id: 201, tool_calls: 1,
    sales: { sku_code: "SKU-001", units_sold: 120, paid_orders: 7, start_date: "2026-11-14", end_date: "2026-11-21", timezone: "Asia/Shanghai" },
  }))
  await sendQuestion()

  expect(await screen.findByText("120 件", { exact: true })).toBeInTheDocument()
  expect(screen.getByRole("complementary", { name: "数据库事实" })).toHaveTextContent("2026-11-14 00:00（含）至 2026-11-21 00:00（不含）")
  expect(screen.getByText("销量查询 · 仅调用 1 次")).toBeInTheDocument()
  expect(request).toHaveBeenNthCalledWith(2, "/api/shops/19/agent/query", expect.objectContaining({
    method: "POST", body: JSON.stringify({ message }),
    headers: { Authorization: "Bearer agent-test-token", "Content-Type": "application/json" },
  }))
})

it("keeps the question available when the model request fails", async () => {
  openAgent(session, new Response(null, { status: 503 }))
  await sendQuestion()

  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法完成查询")
  expect(screen.getByRole("textbox", { name: "输入查数问题" })).toHaveValue(message)
})

it("uses server session_id across follow-up turns and displays below-cost risk warnings", async () => {
  const promotion = {
    sku_code: "SKU-001", original_price: "199.00", cost_price: "119.00", promotion_price: "99.50",
    below_cost: true, risk_warnings: ["促销价 ¥99.50 低于商品成本价 ¥119.00，存在负毛利风险，请谨慎确认。"],
    discount: "5", duration_days: 3, available_qty: 300, minimum_qty: 100,
    starts_at: null, ends_at: null, timezone: "Asia/Shanghai", conflict_check: "clear", plan_key: "plan-key-001",
  }
  const submission = {
    promotion_id: 12, promotion_no: "PRM-20261121-01", name: "轻食谷物礼盒 5 折促销", version: 1,
    status: "pending_approval", approval_id: 34, approval_no: "APR-20261121-01", approval_status: "pending",
    sku_code: "SKU-001", original_price: "199.00", cost_price: "119.00", promotion_price: "99.50",
    below_cost: true, risk_warnings: [], starts_at: "2026-11-22T00:00:00+08:00",
    ends_at: "2026-11-25T00:00:00+08:00", timezone: "Asia/Shanghai", replayed: false,
  }
  const request = openAgent(
    session,
    Response.json({
      kind: "promotion_clarify", message: "请提供开始日期。", session_id: 55, task_id: 88, tool_calls: 1, promotion,
    }),
    Response.json({
      kind: "promotion_preview", message: "促销方案已生成。", session_id: 55, task_id: 89, tool_calls: 1,
      promotion: { ...promotion, starts_at: "2026-11-22T00:00:00+08:00", ends_at: "2026-11-25T00:00:00+08:00" },
    }),
    Response.json(submission),
    Response.json({
      kind: "sales", message: "SKU-002 共售出 5 件。", session_id: 55, task_id: 90, tool_calls: 1,
      sales: { sku_code: "SKU-002", units_sold: 5, paid_orders: 1, start_date: "2026-11-14", end_date: "2026-11-21", timezone: "Asia/Shanghai" },
    }),
  )

  await sendQuestion("给 SKU-001 做一个 5 折、持续 3 天的促销")
  expect(await screen.findByText("还缺开始日期")).toBeInTheDocument()
  expect(screen.getByText("毛利与价格风险预警")).toBeInTheDocument()

  await sendQuestion("11 月 22 日开始")
  expect(await screen.findByText("¥99.50", { exact: true })).toBeInTheDocument()
  expect(screen.getByText(/2026-11-25 00:00（不含）/)).toBeInTheDocument()
  expect(screen.getByText(/成本 ¥119.00/)).toBeInTheDocument()
  expect(request).toHaveBeenNthCalledWith(3, "/api/shops/19/agent/query", expect.objectContaining({
    body: JSON.stringify({ message: "11 月 22 日开始", session_id: 55 }),
  }))

  fireEvent.click(screen.getByRole("button", { name: "确认并提交审批" }))
  expect(await screen.findByText("APR-20261121-01")).toBeInTheDocument()
  expect(screen.getByText("PRM-20261121-01")).toBeInTheDocument()
  expect(request).toHaveBeenNthCalledWith(4, "/api/shops/19/promotions", expect.objectContaining({
    method: "POST",
    headers: expect.objectContaining({ "Idempotency-Key": "agent-session-55-plan-plan-key-001" }),
  }))

  await sendQuestion("查一下 SKU-002 最近 7 天销量")
  expect(await screen.findByText("5 件", { exact: true })).toBeInTheDocument()
  expect(screen.getByRole("complementary", { name: "数据库事实" })).toHaveTextContent("SKU-002")
  expect(screen.getByRole("complementary", { name: "数据库事实" })).not.toHaveTextContent("300 件")
})

it("switches active shop from the top banner and warns on cross-shop session continuation", async () => {
  const request = openAgent(
    multiShopSession,
    Response.json({
      kind: "sales", message: "SKU-001 共售出 120 件。", session_id: 77, task_id: 91, tool_calls: 1,
      sales: { sku_code: "SKU-001", units_sold: 120, paid_orders: 7, start_date: "2026-11-14", end_date: "2026-11-21", timezone: "Asia/Shanghai" },
    }),
    Response.json({
      kind: "unsupported", message: "该会话属于另一家店铺，请切回原店铺继续操作，或在当前店铺新建会话。",
      session_id: 77, tool_calls: 0, sales: null,
    }),
  )

  await sendQuestion()
  expect(await screen.findByText("120 件", { exact: true })).toBeInTheDocument()

  fireEvent.mouseDown(screen.getByRole("combobox", { name: "切换店铺" }))
  fireEvent.click(await screen.findByRole("option", { name: "茶饮专营店" }))
  expect(await screen.findByText("当前已切换至其他店铺")).toBeInTheDocument()

  await sendQuestion("继续刚才的方案")
  expect(await screen.findByText(/该会话属于另一家店铺/)).toBeInTheDocument()
  expect(request).toHaveBeenNthCalledWith(3, "/api/shops/27/agent/query", expect.objectContaining({
    body: JSON.stringify({ message: "继续刚才的方案", session_id: 77 }),
  }))
})
