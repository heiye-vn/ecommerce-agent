import { fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import AppRouter from "../src/router"

const session = {
  access_token: "order-test-token",
  user: { id: 1, username: "ops.demo", name: "沙箱运营" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", roles: ["operator"] }],
}
const order = {
  id: 17, order_no: "DEMO-20261120", status: "paid", paid_amount: "4378.00",
  paid_at: "2026-11-20T04:00:00Z", created_at: "2026-11-20T03:00:00Z",
  sku_codes: ["SKU-001"], quantity: 22,
}
const detail = { ...order, items: [
  { id: 8, sku_code: "SKU-001", product_name: "轻食谷物礼盒", quantity: 22, unit_price: "199.00", paid_amount: "4378.00" },
] }

function openOrders(path: string, ...responses: Response[]) {
  const request = vi.fn().mockResolvedValueOnce(Response.json(session))
  for (const response of responses) request.mockResolvedValueOnce(response)
  vi.stubGlobal("fetch", request)
  window.history.replaceState(null, "", path)
  render(<AppRouter />)
  return request
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

it("searches by SKU and opens a protected order detail", async () => {
  const request = openOrders("/orders", Response.json([order]), Response.json(detail), Response.json([order]))
  const row = await screen.findByRole("row", { name: /DEMO-20261120/ }, { timeout: 3000 })
  expect(within(row).getByText("¥4,378.00")).toBeInTheDocument()
  expect(within(row).getByText("2026-11-20 12:00")).toBeInTheDocument()
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索订单号或 SKU" }), { target: { value: "other" } })
  expect(screen.getByText("没有符合条件的订单")).toBeInTheDocument()
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索订单号或 SKU" }), { target: { value: "sku-001" } })
  fireEvent.click(screen.getByRole("link", { name: "查看详情" }))
  expect(await screen.findByText("轻食谷物礼盒")).toBeInTheDocument()
  expect(screen.getByText("¥199.00")).toBeInTheDocument()
  expect(request).toHaveBeenNthCalledWith(3, "/api/shops/19/orders/17", expect.objectContaining({
    headers: { Authorization: "Bearer order-test-token" },
  }))
  fireEvent.click(screen.getByRole("link", { name: "返回订单列表" }))
  expect(await screen.findByRole("row", { name: /DEMO-20261120/ })).toBeInTheDocument()
})

it("distinguishes an unavailable order from a missing permission", async () => {
  openOrders("/orders/77", new Response(null, { status: 404 }))
  expect(await screen.findByRole("alert")).toHaveTextContent("当前店铺没有这笔订单")
  expect(screen.getByRole("link", { name: "返回订单列表" })).toBeInTheDocument()
})

it("does not display the previous order while navigating to another detail", async () => {
  openOrders("/orders/17", Response.json(detail), new Response(null, { status: 403 }))
  await screen.findByText("轻食谷物礼盒")
  window.history.pushState(null, "", "/orders/18")
  fireEvent.popState(window)
  expect(screen.queryByText("轻食谷物礼盒")).not.toBeInTheDocument()
  expect(await screen.findByRole("alert")).toHaveTextContent("没有权限查看当前店铺的订单")
})
