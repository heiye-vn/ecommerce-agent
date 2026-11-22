import { App as AntApp, Button, Space, Typography } from "antd"
import {
  AppstoreOutlined,
  FileSearchOutlined,
  LineChartOutlined,
  ShoppingCartOutlined,
  TagOutlined,
} from "@ant-design/icons"
import { useState, type ReactNode } from "react"
import { NavLink } from "react-router-dom"

import styles from "./App.module.css"

const navigationItems = [
  { key: "agent", label: "Agent 对话", to: "/" },
  { key: "catalog", label: "商品运营", to: "/catalog" },
  { key: "promotions", label: "促销管理", to: "/promotions" },
  { key: "orders", label: "订单售后", to: "/orders" },
  { key: "analytics", label: "经营分析", to: "/analytics" },
]

type AppProps = {
  shopName: string
  onLogout: () => Promise<void>
  children?: ReactNode
}

export default function App({ shopName, onLogout, children }: AppProps) {
  const { message } = AntApp.useApp()
  const [signingOut, setSigningOut] = useState(false)

  async function handleLogout() {
    setSigningOut(true)
    try {
      await onLogout()
    } catch {
      void message.error("暂时无法退出登录，请重试。")
    } finally {
      setSigningOut(false)
    }
  }

  return (
    <div className={styles.shell}>
      <header className={styles.banner} aria-label="CommerceAgent">
        <h1 className={styles.brand}>CommerceAgent</h1>
        <div className={styles.shopSwitcher}>
          <span>当前店铺</span>
          <span className={styles.shop}>{shopName}</span>
          <Button onClick={handleLogout} loading={signingOut}>退出登录</Button>
        </div>
      </header>
      <div className={styles.layout}>
        <nav className={styles.navigation} aria-label="主导航">
          {navigationItems.map((item) => (
            <NavLink
              key={item.key}
              to={item.to}
              className={({ isActive }) =>
                `${styles.navigationLink} ${isActive ? styles.active : ""}`
              }
            >
              {item.key === "agent" ? (
                <AppstoreOutlined />
              ) : item.key === "catalog" ? (
                <ShoppingCartOutlined />
              ) : item.key === "promotions" ? (
                <TagOutlined />
              ) : item.key === "orders" ? (
                <FileSearchOutlined />
              ) : (
                <LineChartOutlined />
              )}
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        {children ?? <main className={styles.workspace}>
          <p className={styles.eyebrow}>可控执行型电商运营 Agent</p>
          <Typography.Title className={styles.title} level={1}>
            运营工作台
          </Typography.Title>
          <p className={styles.intro}>
            从自然语言查询业务事实，到生成可审计的执行方案，高风险操作始终经过人工审批和后端复核。
          </p>
          <Space direction="vertical" size="large">
            <Button type="primary">开始新任务</Button>
          </Space>
          <section className={styles.statusGrid} aria-label="环境状态">
            <article className={styles.statusCard}>
              <h2>业务沙箱</h2>
              <p>可在商品运营查看当前店铺的 SKU、售价与库存。</p>
            </article>
            <article className={styles.statusCard}>
              <h2>审批闭环</h2>
              <p>促销、调价与退款将统一进入人工审批流程。</p>
            </article>
            <article className={styles.statusCard}>
              <h2>可追溯执行</h2>
              <p>工具调用、任务状态与审计记录将关联到同一业务资源。</p>
            </article>
          </section>
        </main>}
      </div>
    </div>
  )
}
