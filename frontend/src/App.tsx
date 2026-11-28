import { App as AntApp, Button, Select, Space, Typography } from "antd"
import {
  AppstoreOutlined,
  BellOutlined,
  BookOutlined,
  FileSearchOutlined,
  LineChartOutlined,
  SafetyCertificateOutlined,
  ScheduleOutlined,
  ShoppingCartOutlined,
  TagOutlined,
} from "@ant-design/icons"
import { useState, type ReactNode } from "react"
import { NavLink } from "react-router-dom"

import type { ShopAccess } from "./auth"
import styles from "./App.module.css"

const navigationItems = [
  { key: "agent", label: "Agent 对话", to: "/", group: "工作台" },
  { key: "catalog", label: "商品运营", to: "/catalog", group: "业务" },
  { key: "promotions", label: "促销管理", to: "/promotions", group: "业务" },
  { key: "orders", label: "订单售后", to: "/orders", group: "业务" },
  { key: "analytics", label: "经营分析", to: "/analytics", group: "业务" },
  { key: "approvals", label: "审批中心", to: "/approvals", group: "系统管理" },
  { key: "tasks", label: "任务中心", to: "/tasks", group: "系统管理" },
  { key: "knowledge", label: "知识库管理", to: "/knowledge", group: "系统管理" },
]

type AppProps = {
  shopName: string
  userName?: string
  shops?: ShopAccess[]
  activeShopId?: number
  unreadCount?: number
  onSelectShop?: (shopId: number) => void
  onLogout: () => Promise<void>
  children?: ReactNode
}

export default function App({
  shopName,
  userName,
  shops,
  activeShopId,
  unreadCount = 0,
  onSelectShop,
  onLogout,
  children,
}: AppProps) {
  const { message } = AntApp.useApp()
  const [signingOut, setSigningOut] = useState(false)

  async function handleLogout() {
    setSigningOut(true)
    try {
      await onLogout()
    } catch {
      message?.error?.("暂时无法退出登录，请重试。")
    } finally {
      setSigningOut(false)
    }
  }

  const canSwitchShop = Boolean(shops && shops.length > 1 && onSelectShop)

  function renderIcon(key: string) {
    if (key === "agent") return <AppstoreOutlined />
    if (key === "catalog") return <ShoppingCartOutlined />
    if (key === "promotions") return <TagOutlined />
    if (key === "orders") return <FileSearchOutlined />
    if (key === "approvals") return <SafetyCertificateOutlined />
    if (key === "tasks") return <ScheduleOutlined />
    if (key === "knowledge") return <BookOutlined />
    return <LineChartOutlined />
  }

  return (
    <div className={styles.shell}>
      <header className={styles.banner} aria-label="CommerceAgent">
        <h1 className={styles.brand}>CommerceAgent</h1>
        <div className={styles.shopSwitcher}>
          <span>当前店铺</span>
          {canSwitchShop && shops && onSelectShop ? (
            <Select<number>
              className={styles.shopSelect}
              aria-label="切换店铺"
              value={activeShopId ?? shops[0]?.id}
              onChange={onSelectShop}
              virtual={false}
              options={shops.map(shop => ({ value: shop.id, label: shop.name }))}
            />
          ) : (
            <span className={styles.shop}>{shopName}</span>
          )}
          <NavLink
            to="/notifications"
            aria-label="通知中心"
            className={({ isActive }) =>
              `${styles.notifButton} ${isActive ? styles.notifButtonActive : ""}`
            }
          >
            <BellOutlined aria-hidden="true" />
            {unreadCount > 0 && <span className={styles.unreadPill}>{unreadCount}</span>}
          </NavLink>
          {userName && <span className={styles.userBadge}>{userName}</span>}
          <Button onClick={handleLogout} loading={signingOut}>退出登录</Button>
        </div>
      </header>
      <div className={styles.layout}>
        <nav className={styles.navigation} aria-label="主导航">
          {navigationItems.map((item, index) => {
            const showGroup = index === 0 || navigationItems[index - 1]?.group !== item.group
            return (
              <div key={item.key} className={styles.navItemWrap}>
                {showGroup && <span className={styles.navGroupLabel}>{item.group}</span>}
                <NavLink
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    `${styles.navigationLink} ${isActive ? styles.active : ""}`
                  }
                >
                  {renderIcon(item.key)}
                  <span>{item.label}</span>
                </NavLink>
              </div>
            )
          })}
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
