import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import AppRouter from "../src/router"

const message = "SKU-001 最近 7 天卖了多少件？"
const session = {
  access_token: "agent-test-token",
  user: { id: 1, username: "ops.demo", name: "沙箱运营" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", roles: ["operator"] }],
}

function openAgent(...responses: Response[]) {
  const request = vi.fn().mockResolvedValueOnce(Response.json(session))
  for (const response of responses) request.mockResolvedValueOnce(response)
  vi.stubGlobal("fetch", request)
  window.history.replaceState(null, "", "/")
  render(<AppRouter />)
  return request
}

async function sendQuestion() {
  fireEvent.change(await screen.findByRole("textbox", { name: "输入查数问题" }), { target: { value: message } })
  fireEvent.click(screen.getByRole("button", { name: "发送" }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

it("renders the authorized shop query result and its database evidence", async () => {
  const request = openAgent(Response.json({
    kind: "sales", message: "SKU-001 共售出 120 件。", tool_calls: 1,
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
  openAgent(new Response(null, { status: 503 }))
  await sendQuestion()

  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法完成查询")
  expect(screen.getByRole("textbox", { name: "输入查数问题" })).toHaveValue(message)
})
