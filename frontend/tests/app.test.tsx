import { fireEvent, render, screen } from "@testing-library/react"
import { ConfigProvider } from "antd"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App"
import styles from "../src/App.module.css"
import { designTokens } from "../src/theme/designTokens"

function renderApp() {
  return render(
    <ConfigProvider theme={designTokens}>
      <MemoryRouter>
        <App shopName="轻食旗舰店" onLogout={vi.fn()} />
      </MemoryRouter>
    </ConfigProvider>,
  )
}

describe("App", () => {
  it("renders the desktop workspace shell", () => {
    renderApp()

    expect(screen.getByRole("banner", { name: "CommerceAgent" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "运营工作台" })).toBeInTheDocument()
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeInTheDocument()
  })

  it("keeps one nav item background on hover", () => {
    renderApp()

    const navItem = screen.getByRole("link", { name: /促销管理/ })
    fireEvent.mouseEnter(navItem)

    const itemBackground = getComputedStyle(navItem).backgroundColor
    const linkBackground = getComputedStyle(navItem).backgroundColor

    expect([itemBackground, linkBackground]).toEqual(["rgba(0, 0, 0, 0)", "rgba(0, 0, 0, 0)"])
    expect(navItem).not.toHaveAttribute("aria-current")
  })

  it("renders the selected navigation as a black pill", () => {
    renderApp()

    const navItem = screen.getByRole("link", { name: /Agent 对话/ })
    expect(navItem).toHaveAttribute("aria-current", "page")
    expect(navItem.querySelector("svg")).toBeInTheDocument()
    expect(navItem).toHaveClass(styles.active)
    expect(getComputedStyle(navItem).backgroundColor).toBe("rgb(0, 0, 0)")
    expect(getComputedStyle(navItem).borderRadius).toBe("9999px")
  })

  it("applies zero body margin and border-box workspace sizing to prevent viewport scrollbars", async () => {
    await import("../src/global.css")
    renderApp()

    const main = screen.getByRole("main")
    expect(getComputedStyle(document.body).margin).toBe("0px")
    expect(getComputedStyle(main).boxSizing).toBe("border-box")
  })
})
