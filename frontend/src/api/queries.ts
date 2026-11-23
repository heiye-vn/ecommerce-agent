import { QueryClient, useQuery } from "@tanstack/react-query"

import type { ShopReadProps } from "../auth"
import { fetchShopJson } from "./client"

export type CatalogSku = {
  sku_code: string
  product_name: string
  category: string
  status: string
  current_price: string
  available_qty: number | null
  reserved_qty: number | null
}

export type OrderSummary = {
  id: number
  order_no: string
  status: string
  paid_amount: string
  paid_at: string | null
  created_at: string
  sku_codes: string[]
  quantity: number
}

export type OrderLine = {
  id: number
  sku_code: string
  product_name: string
  quantity: number
  unit_price: string
  paid_amount: string
}

export type OrderDetail = OrderSummary & { items: OrderLine[] }

export type PromotionStatus =
  | "pending_approval"
  | "approved"
  | "scheduled"
  | "active"
  | "completed"
  | "rejected"
  | "cancelled"
  | "failed"

export type AuditLogEntry = {
  id: number
  action: string
  actor_id: number
  actor_name: string
  before_json: Record<string, unknown> | null
  after_json: Record<string, unknown> | null
  metadata_json: Record<string, unknown> | null
  created_at: string
}

export type PromotionApprovalBrief = {
  id: number
  approval_no: string
  resource_version: number
  status: string
  requester_id: number
  requester_name: string
  approver_id: number | null
  approver_name: string | null
  decision_comment: string | null
  decided_at: string | null
  created_at: string
}

export type PromotionSummary = {
  id: number
  promotion_no: string
  name: string
  version: number
  status: PromotionStatus
  sku_code: string
  product_name: string
  original_price: string
  cost_price: string
  discount: string
  promotion_price: string
  below_cost: boolean
  starts_at: string
  ends_at: string
  duration_days: number
  minimum_qty: number
  timezone: string
  created_by: number
  creator_name: string
  latest_approval_id: number | null
  latest_approval_no: string | null
  latest_approval_status: string | null
  failure_reason: string | null
  conflict_promotion_no: string | null
  created_at: string
  updated_at: string
}

export type PromotionDetail = PromotionSummary & {
  item: {
    sku_id: number
    sku_code: string
    product_name: string
    original_price: string
    cost_price: string
    discount: string
    discount_rate: string
    promotion_price: string
    below_cost: boolean
  }
  approvals: PromotionApprovalBrief[]
  audit_logs: AuditLogEntry[]
}

export type PromotionSubmitResponse = {
  promotion_id: number
  promotion_no: string
  name: string
  version: number
  status: PromotionStatus
  approval_id: number
  approval_no: string
  approval_status: string
  sku_code: string
  original_price: string
  cost_price: string
  promotion_price: string
  below_cost: boolean
  risk_warnings: string[]
  starts_at: string
  ends_at: string
  timezone: string
  replayed: boolean
}

export type ApprovalStatus = "pending" | "approved" | "rejected" | "cancelled"
export type ApprovalScope = "pending_for_me" | "processed_by_me" | "requested_by_me" | "all"

export type ApprovalSummary = {
  id: number
  approval_no: string
  action_type: string
  resource_type: string
  resource_id: number
  resource_no: string
  resource_name: string
  resource_version: number
  sku_code: string | null
  snapshot_amount: string | null
  status: ApprovalStatus
  requester_id: number
  requester_name: string
  approver_id: number | null
  approver_name: string | null
  decision_comment: string | null
  decided_at: string | null
  created_at: string
  can_decide: boolean
}

export type ApprovalDetail = ApprovalSummary & {
  shop_id: number
  shop_name: string
  payload_snapshot: Record<string, unknown>
  resource_status: string | null
  resource_failure_reason: string | null
  resource_conflict_promotion_no: string | null
  cannot_decide_reason: string | null
  audit_logs: AuditLogEntry[]
}

export type ApprovalDecisionResponse = {
  approval: ApprovalDetail
  execution_status: string | null
  execution_message: string
}

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        staleTime: 0,
      },
    },
  })
}

export function useCatalogQuery(auth: ShopReadProps) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "catalog"],
    queryFn: ({ signal }) => fetchShopJson<CatalogSku[]>("catalog", auth, { signal }),
  })
}

export function useOrdersQuery(auth: ShopReadProps) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "orders"],
    queryFn: ({ signal }) => fetchShopJson<OrderSummary[]>("orders", auth, { signal }),
  })
}

export function useOrderDetailQuery(auth: ShopReadProps, orderId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "orders", orderId],
    queryFn: ({ signal }) => fetchShopJson<OrderDetail>(`orders/${orderId}`, auth, { signal }),
    enabled,
  })
}

export function usePromotionsQuery(auth: ShopReadProps) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "promotions"],
    queryFn: ({ signal }) => fetchShopJson<PromotionSummary[]>("promotions", auth, { signal }),
  })
}

export function usePromotionDetailQuery(auth: ShopReadProps, promotionId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "promotions", promotionId],
    queryFn: ({ signal }) => fetchShopJson<PromotionDetail>(`promotions/${promotionId}`, auth, { signal }),
    enabled,
  })
}

export function useApprovalsQuery(auth: ShopReadProps, scope: ApprovalScope) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "approvals", scope],
    queryFn: ({ signal }) => fetchShopJson<ApprovalSummary[]>(`approvals?scope=${scope}`, auth, { signal }),
  })
}

export function useApprovalDetailQuery(auth: ShopReadProps, approvalId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "approvals", "detail", approvalId],
    queryFn: ({ signal }) => fetchShopJson<ApprovalDetail>(`approvals/${approvalId}`, auth, { signal }),
    enabled,
  })
}

export function submitPromotionRequest(
  auth: ShopReadProps,
  payload: { name?: string; sku_code: string; discount: string; start_date: string; duration_days: number },
  idempotencyKey?: string,
) {
  return fetchShopJson<PromotionSubmitResponse>("promotions", auth, { payload, idempotencyKey })
}

export function cancelPromotionRequest(auth: ShopReadProps, promotionId: number) {
  return fetchShopJson<PromotionDetail>(`promotions/${promotionId}/cancel`, auth, { payload: {} })
}

export function revisePromotionRequest(
  auth: ShopReadProps,
  promotionId: number,
  payload: { name?: string; discount: string; start_date: string; duration_days: number; reason?: string },
  idempotencyKey?: string,
) {
  return fetchShopJson<PromotionSubmitResponse>(`promotions/${promotionId}/revise`, auth, {
    payload,
    idempotencyKey,
  })
}

export function approveApprovalRequest(auth: ShopReadProps, approvalId: number, comment?: string) {
  return fetchShopJson<ApprovalDecisionResponse>(`approvals/${approvalId}/approve`, auth, {
    payload: comment ? { comment } : {},
  })
}

export function rejectApprovalRequest(auth: ShopReadProps, approvalId: number, comment: string) {
  return fetchShopJson<ApprovalDecisionResponse>(`approvals/${approvalId}/reject`, auth, {
    payload: { comment },
  })
}
