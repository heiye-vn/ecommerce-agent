import { fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import AppRouter from "../src/router"

const operatorSession = {
  access_token: "stage3-ops-token",
  user: { id: 1, username: "ops.demo", name: "沙箱运营" },
  shops: [{ id: 19, organization_id: 1, name: "轻食旗舰店", timezone: "Asia/Shanghai", roles: ["operator"] }],
}

const deadLetterJob = {
  id: 88,
  job_no: "JOB-20261121-01",
  job_type: "promotion_schedule",
  status: "dead_lettered",
  resource_type: "promotion",
  resource_id: 10,
  resource_no: "PRM-20261121-01",
  resource_name: "冬季暖饮专场",
  resource_version: 1,
  attempts: 3,
  max_attempts: 3,
  available_at: "2026-11-21T02:42:00Z",
  last_error: "下游排期网关超时",
  started_at: "2026-11-21T02:34:00Z",
  finished_at: "2026-11-21T02:42:00Z",
  created_at: "2026-11-21T02:33:00Z",
  updated_at: "2026-11-21T02:42:00Z",
}

const deadLetterDetail = {
  ...deadLetterJob,
  idempotency_key: "promotion-schedule-10-v1",
  payload: { promotion_id: 10, promotion_no: "PRM-20261121-01" },
  result_json: null,
  resource_status: "approved",
  can_retry: true,
  retry_hint: "系统暂时性故障已超出自动重试上限，确认依赖恢复后可按原操作重试，不会重复建单。",
  audit_logs: [
    {
      id: 1,
      action: "job.dead_lettered",
      actor_id: 1,
      actor_name: "沙箱运营",
      before_json: null,
      after_json: { last_error: "下游排期网关超时" },
      metadata_json: null,
      created_at: "2026-11-21T02:42:00Z",
    },
  ],
}

const notificationsPayload = {
  items: [
    {
      id: 301,
      category: "promotion",
      title: "促销活动 PRM-20261121-01 已成功排期",
      content: "业务复核通过，活动 PRM-20261121-01 已成功排期。",
      resource_type: "promotion",
      resource_id: 10,
      is_read: false,
      read_at: null,
      created_at: "2026-11-21T02:30:00Z",
    },
    {
      id: 302,
      category: "job",
      title: "任务 JOB-20261121-02 已转入死信队列",
      content: "连续自动重试 3/3 次均失败。",
      resource_type: "job",
      resource_id: 88,
      is_read: false,
      read_at: null,
      created_at: "2026-11-21T02:42:00Z",
    },
  ],
  unread_count: 2,
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

describe("任务中心与通知中心", () => {
  it("lists jobs, navigates to dead-letter detail, and triggers manual retry", async () => {
    const retriedDetail = {
      ...deadLetterDetail,
      status: "pending",
      attempts: 0,
      last_error: null,
      can_retry: false,
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json([deadLetterJob]))
      .mockResolvedValueOnce(Response.json(deadLetterDetail))
      .mockResolvedValueOnce(Response.json({
        job: retriedDetail,
        message: "已按原操作重新投递任务 JOB-20261121-01，不会重复创建活动或审批单。",
      }))
      .mockResolvedValueOnce(Response.json(retriedDetail))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/tasks")
    render(<AppRouter />)

    expect(await screen.findByText("JOB-20261121-01")).toBeInTheDocument()
    expect(screen.getByText("1 项需人工处理")).toBeInTheDocument()
    expect(screen.getByText("3 / 3")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("link", { name: "处理任务" }))
    expect(await screen.findByRole("heading", { name: "促销执行异常" })).toBeInTheDocument()
    expect(screen.getByText("已达自动重试上限，转入死信队列")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "按原操作重试" }))
    expect(await screen.findByText(/已按原操作重新投递任务 JOB-20261121-01/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/shops/19/jobs/88/retry",
      expect.objectContaining({ method: "POST" }),
    )
  })

  it("displays notifications, marks one read, and marks all read", async () => {
    const afterSingleRead = {
      items: [
        { ...notificationsPayload.items[0], is_read: true, read_at: "2026-11-21T02:45:00Z" },
        notificationsPayload.items[1],
      ],
      unread_count: 1,
    }
    const afterAllRead = {
      items: afterSingleRead.items.map(item => ({ ...item, is_read: true })),
      unread_count: 0,
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(operatorSession))
      .mockResolvedValueOnce(Response.json(notificationsPayload))
      .mockResolvedValueOnce(Response.json(afterSingleRead.items[0]))
      .mockResolvedValueOnce(Response.json(afterSingleRead))
      .mockResolvedValueOnce(Response.json({ updated_count: 1 }))
      .mockResolvedValueOnce(Response.json(afterAllRead))
    vi.stubGlobal("fetch", fetchMock)
    window.history.replaceState(null, "", "/notifications")
    render(<AppRouter />)

    expect(await screen.findByText("促销活动 PRM-20261121-01 已成功排期")).toBeInTheDocument()
    const banner = screen.getByRole("banner", { name: "CommerceAgent" })
    expect(within(banner).getByText("2")).toBeInTheDocument()

    const readButtons = screen.getAllByRole("button", { name: "标为已读" })
    fireEvent.click(readButtons[0]!)
    expect(await within(banner).findByText("1")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "全部标为已读" }))
    expect(await screen.findByRole("button", { name: "全部标为已读" })).toBeDisabled()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/shops/19/notifications/read-all",
      expect.objectContaining({ method: "POST" }),
    )
  })
})
