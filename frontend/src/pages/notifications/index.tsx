import { BellOutlined, DollarOutlined, SafetyCertificateOutlined, ScheduleOutlined, TagOutlined } from "@ant-design/icons"
import { useQueryClient } from "@tanstack/react-query"
import { Alert, App as AntApp, Button, Spin } from "antd"
import { useState } from "react"
import { Link } from "react-router-dom"

import { ApiError } from "../../api/client"
import {
  markAllNotificationsReadRequest,
  markNotificationReadRequest,
  useNotificationsQuery,
  type NotificationCategory,
  type NotificationItem,
} from "../../api/queries"
import type { ShopReadProps } from "../../auth"
import styles from "./index.module.css"

type PageProps = ShopReadProps & {
  shopName: string
}

const dateTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
})

function categoryIcon(category: NotificationCategory) {
  if (category === "approval") return <SafetyCertificateOutlined />
  if (category === "job") return <ScheduleOutlined />
  if (category === "promotion" || category === "price_change") return <TagOutlined />
  if (category === "refund") return <DollarOutlined />
  return <BellOutlined />
}

function targetLink(item: NotificationItem): { to: string; label: string } | null {
  if (!item.resource_id) return null
  if (item.resource_type === "approval") return { to: `/approvals/${item.resource_id}`, label: "审阅审批" }
  if (item.resource_type === "promotion") return { to: `/promotions/${item.resource_id}`, label: "查看活动" }
  if (item.resource_type === "refund") return { to: `/orders/refunds/${item.resource_id}`, label: "查看退款" }
  if (item.resource_type === "price_change") return { to: `/catalog/price-changes/${item.resource_id}`, label: "查看调价" }
  if (item.resource_type === "job") return { to: `/tasks/${item.resource_id}`, label: "处理任务" }
  return null
}

export default function NotificationsPage(props: PageProps) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [markingAll, setMarkingAll] = useState(false)
  const [markingId, setMarkingId] = useState<number | null>(null)
  const query = useNotificationsQuery(props, unreadOnly)

  const ready = query.isSuccess
  const items = ready ? query.data.items : []
  const unreadCount = ready ? query.data.unread_count : 0

  async function handleMarkRead(id: number) {
    setMarkingId(id)
    try {
      await markNotificationReadRequest(props, id)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "notifications"] })
    } catch (error) {
      message?.error?.(error instanceof ApiError ? error.message : "操作失败，请稍后重试。")
    } finally {
      setMarkingId(null)
    }
  }

  async function handleReadAll() {
    setMarkingAll(true)
    try {
      await markAllNotificationsReadRequest(props)
      await queryClient.invalidateQueries({ queryKey: ["shop", props.shopId, "notifications"] })
      message?.success?.("已将全部通知标为已读")
    } catch (error) {
      message?.error?.(error instanceof ApiError ? error.message : "操作失败，请稍后重试。")
    } finally {
      setMarkingAll(false)
    }
  }

  const errorStatus = query.error instanceof ApiError ? query.error.status : undefined

  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/">运营工作台</Link><span aria-hidden="true">/</span>
        <strong aria-current="page">通知中心</strong>
      </nav>
      <div className={styles.headerBar}>
        <div>
          <h1>通知中心</h1>
          <p className={styles.summary}>{props.shopName} · 集中查看需要处理的业务审批、排期与任务变更。</p>
        </div>
        <Button loading={markingAll} disabled={!ready || unreadCount === 0} onClick={() => { void handleReadAll() }}>
          全部标为已读
        </Button>
      </div>

      <div className={styles.tabs} role="tablist" aria-label="通知筛选">
        <button
          type="button"
          role="tab"
          aria-selected={!unreadOnly}
          className={`${styles.tabButton} ${!unreadOnly ? styles.tabActive : ""}`}
          onClick={() => setUnreadOnly(false)}
        >
          <span>全部通知</span>
          {unreadCount > 0 && <span className={styles.countPill}>{unreadCount} 条未读</span>}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={unreadOnly}
          className={`${styles.tabButton} ${unreadOnly ? styles.tabActive : ""}`}
          onClick={() => setUnreadOnly(true)}
        >
          未读通知
        </button>
      </div>

      <section className={styles.panel} aria-label="通知列表">
        <div className={styles.panelHeader}>消息</div>

        {query.isPending || query.isRefetching ? (
          <div className={styles.feedback}>
            <span role="status"><Spin size="small" /> 正在加载通知</span>
          </div>
        ) : query.isError ? (
          <div className={styles.feedback}>
            {errorStatus === 401 ? (
              <Alert showIcon type="warning" message="登录状态已失效" action={<Button onClick={props.onSignIn}>重新登录</Button>} />
            ) : (
              <Alert showIcon type="error" message="通知加载失败" action={<Button onClick={() => { void query.refetch() }}>重试</Button>} />
            )}
          </div>
        ) : items.length === 0 ? (
          <div className={styles.empty}>{unreadOnly ? "暂无未读通知" : "当前店铺暂无通知"}</div>
        ) : (
          <ul className={styles.list}>
            {items.map(item => {
              const link = targetLink(item)
              return (
                <li key={item.id} className={styles.row}>
                  <div className={styles.rowMain}>
                    <span className={`${styles.iconBox} ${item.is_read ? styles.iconBoxRead : ""}`} aria-hidden="true">
                      {categoryIcon(item.category)}
                    </span>
                    <div className={styles.rowText}>
                      <strong>{item.title}</strong>
                      <p>{item.content}</p>
                    </div>
                  </div>
                  <div className={styles.rowMeta}>
                    <span className={item.is_read ? styles.readBadge : styles.unreadBadge}>
                      {item.is_read ? "已读" : "未读"}
                    </span>
                    <span className={styles.time}>{dateTime.format(new Date(item.created_at))}</span>
                    {!item.is_read && (
                      <Button size="small" loading={markingId === item.id} onClick={() => { void handleMarkRead(item.id) }}>
                        标为已读
                      </Button>
                    )}
                    {link && (
                      <Link className={styles.linkBtn} to={link.to}>
                        {link.label}
                      </Link>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </main>
  )
}
