import { StrictMode } from "react"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import LoginPage from "../src/pages/login"
import AppRouter from "../src/router"

describe("LoginPage", () => {
  it("matches the login prototype fields and content", () => {
    render(<LoginPage onLogin={vi.fn()} />)

    expect(screen.getByRole("heading", { name: "登录工作台" })).toBeInTheDocument()
    expect(screen.getByText("从运营想法，到可追溯的执行。")).toBeInTheDocument()
    expect(screen.getByLabelText("账号")).toHaveAttribute("name", "username")
    expect(screen.getByLabelText("密码")).toHaveAttribute("type", "password")
    expect(screen.getByRole("button", { name: /登录工作台/ })).toBeInTheDocument()
    expect(screen.getByText("本地业务沙箱")).toBeInTheDocument()
  })

  it("shows validation errors for empty credentials", async () => {
    render(<LoginPage onLogin={vi.fn()} />)

    fireEvent.change(screen.getByLabelText("账号"), { target: { value: "" } })
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "" } })
    fireEvent.click(screen.getByRole("button", { name: /登录工作台/ }))

    await waitFor(() => {
      expect(screen.getByText("请输入账号")).toBeInTheDocument()
      expect(screen.getByText("请输入密码")).toBeInTheDocument()
    })
  })
})

describe("AppRouter", () => {
  const session = {
    access_token: "test-token",
    user: { id: 1, username: "ops.demo", name: "测试运营" },
    shops: [{ id: 1, organization_id: 1, name: "测试授权店铺", roles: ["operator"] }],
  }

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, "", "/")
  })

  it("redirects an unauthenticated workspace visit to login", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })))
    window.history.replaceState(null, "", "/promotions")
    render(<AppRouter />)

    expect(await screen.findByRole("heading", { name: "登录工作台" })).toBeInTheDocument()
    expect(window.location.pathname).toBe("/login")
    expect(screen.getByLabelText("密码")).toHaveValue("")
  })

  it("shows a credential error without opening the workspace", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })))
    window.history.replaceState(null, "", "/login")
    render(<AppRouter />)

    fireEvent.change(await screen.findByLabelText("账号"), { target: { value: "ops.demo" } })
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } })
    fireEvent.click(screen.getByRole("button", { name: /登录工作台/ }))

    expect(await screen.findByText("账号或密码错误。")).toBeInTheDocument()
    expect(window.location.pathname).toBe("/login")
  })

  it("logs in, returns to the requested route, and logs out", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(Response.json(session))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/analytics")
    render(<AppRouter />)

    fireEvent.change(await screen.findByLabelText("密码"), { target: { value: "test-password" } })
    fireEvent.click(screen.getByRole("button", { name: /登录工作台/ }))

    expect(await screen.findByText("测试授权店铺")).toBeInTheDocument()
    expect(window.location.pathname).toBe("/analytics")
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/auth/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ username: "ops.demo", password: "test-password" }),
    }))
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)

    fireEvent.click(screen.getByRole("button", { name: "退出登录" }))

    expect(await screen.findByRole("heading", { name: "登录工作台" })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/auth/logout", expect.objectContaining({
      method: "POST", headers: { Authorization: "Bearer test-token" },
    }))
    expect(window.location.pathname).toBe("/login")
  })

  it("restores a session with one refresh request under StrictMode", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(session))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/login")
    render(<StrictMode><AppRouter /></StrictMode>)

    expect(await within(await screen.findByRole("banner", { name: "CommerceAgent" })).findByText("测试授权店铺")).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/refresh", expect.objectContaining({ method: "POST" }))
    expect(window.location.pathname).toBe("/")
  })
})
