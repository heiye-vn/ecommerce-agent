import { fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import AppRouter from "../src/router"

const session = {
  access_token: "catalog-test-token",
  user: { id: 1, username: "ops.demo", name: "测试运营" },
  shops: [{ id: 19, organization_id: 1, name: "测试店铺", roles: ["operator"] }],
}

const rows = [
  { sku_code: "SKU-001", product_name: "轻食谷物礼盒", category: "食品", status: "active",
    current_price: "199.00", available_qty: 300, reserved_qty: 0 },
  { sku_code: "SKU-002", product_name: "无库存快照的杯子", category: "日用", status: "inactive",
    current_price: "89.00", available_qty: null, reserved_qty: null },
]

function openCatalog(response: Response | Promise<Response>) {
  const request = vi.fn()
    .mockResolvedValueOnce(Response.json(session))
    .mockResolvedValueOnce(response)
  vi.stubGlobal("fetch", request)
  window.history.replaceState(null, "", "/catalog")
  render(<AppRouter />)
  return request
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

describe("商品运营", () => {
  it("loads and filters catalog data for the authorized shop", async () => {
    const request = openCatalog(Response.json(rows))

    await screen.findByText("SKU-001")
    const table = within(screen.getByRole("region", { name: "商品 / SKU" })).getByRole("table")
    const sku = within(table).getByRole("row", { name: /SKU-001/ })
    expect(within(sku).getByText("¥199.00")).toBeInTheDocument()
    expect(within(sku).getByText("300 件")).toBeInTheDocument()
    expect(within(table).getByText("未记录")).toBeInTheDocument()
    expect(request).toHaveBeenNthCalledWith(2, "/api/shops/19/catalog", expect.objectContaining({
      headers: { Authorization: "Bearer catalog-test-token" },
    }))

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索商品或 SKU" }), { target: { value: "sku-002" } })
    expect(within(table).queryByText("SKU-001")).not.toBeInTheDocument()
    expect(within(table).getByText("SKU-002")).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "商品状态" }))
    fireEvent.click(await screen.findByRole("option", { name: "在售" }))
    expect(screen.getByText("没有符合条件的商品")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }))
    expect(within(table).getByText("SKU-001")).toBeInTheDocument()
  })

  it("distinguishes missing permission from an empty catalog", async () => {
    openCatalog(new Response(null, { status: 403 }))

    expect(await screen.findByRole("alert")).toHaveTextContent("没有权限查看当前店铺的商品")
    expect(screen.queryByText("当前店铺暂无商品")).not.toBeInTheDocument()
  })

  it("lets the user retry a failed read without losing the current page", async () => {
    const request = openCatalog(new Response(null, { status: 503 }))
    request.mockResolvedValueOnce(Response.json(rows))

    expect(await screen.findByRole("alert")).toHaveTextContent("商品数据加载失败")
    fireEvent.click(screen.getByRole("button", { name: "重试" }))
    expect(await screen.findByText("SKU-001")).toBeInTheDocument()
    expect(window.location.pathname).toBe("/catalog")
  })

  it("limits the number of visible rows and supports paging", async () => {
    openCatalog(Response.json(Array.from({ length: 7 }, (_, index) => ({
      ...rows[0], sku_code: `DEMO-${index + 1}`,
    }))))

    expect(await screen.findByText("DEMO-1")).toBeInTheDocument()
    expect(screen.queryByText("DEMO-7")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "下一页" }))
    expect(screen.getByText("DEMO-7")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled()
  })

  it("points a revoked session back to login", async () => {
    const request = openCatalog(new Response(null, { status: 401 }))
    request.mockResolvedValueOnce(new Response(null, { status: 401 }))

    expect(await screen.findByRole("alert")).toHaveTextContent("登录状态已失效")
    fireEvent.click(screen.getByRole("button", { name: "重新登录" }))
    expect(await screen.findByRole("heading", { name: "登录工作台" })).toBeInTheDocument()
  })

  it("refreshes an expired access token before asking the operator to sign in", async () => {
    const request = openCatalog(new Response(null, { status: 401 }))
    request.mockResolvedValueOnce(Response.json({ ...session, access_token: "renewed-token" }))
      .mockResolvedValueOnce(Response.json(rows))

    expect(await screen.findByText("SKU-001")).toBeInTheDocument()
    expect(request).toHaveBeenNthCalledWith(3, "/api/auth/refresh", expect.objectContaining({ method: "POST" }))
    expect(request).toHaveBeenNthCalledWith(4, "/api/shops/19/catalog", expect.objectContaining({
      headers: { Authorization: "Bearer renewed-token" },
    }))
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("does not loop when a renewed access token is rejected", async () => {
    const request = openCatalog(new Response(null, { status: 401 }))
    request.mockResolvedValueOnce(Response.json({ ...session, access_token: "renewed-token" }))
      .mockResolvedValueOnce(new Response(null, { status: 401 }))

    expect(await screen.findByRole("alert")).toHaveTextContent("登录状态已失效")
    expect(request).toHaveBeenCalledTimes(4)
  })
})
