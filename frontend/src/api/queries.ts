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

export type OrderRefundBrief = {
  id: number
  refund_no: string
  amount: string
  reason: string
  status: string
  failure_reason?: string | null
  created_at: string
}

export type OrderSummary = {
  id: number
  order_no: string
  status: string
  fulfillment_status?: string
  exception_type?: string | null
  exception_note?: string | null
  paid_amount: string
  refunded_amount?: string
  in_flight_refund_amount?: string
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

export type OrderDetail = OrderSummary & {
  items: OrderLine[]
  refundable_amount?: string
  can_refund?: boolean
  refund_block_reason?: string | null
  refunds?: OrderRefundBrief[]
}

export type RefundStatus =
  | "pending_approval"
  | "approved"
  | "processing"
  | "succeeded"
  | "rejected"
  | "cancelled"
  | "failed"

export type RefundSummary = {
  id: number
  refund_no: string
  order_id: number
  order_no: string
  order_status: string
  order_paid_amount: string
  amount: string
  reason: string
  status: RefundStatus
  requester_id: number
  requester_name: string
  failure_reason: string | null
  processed_at: string | null
  approval_id: number | null
  approval_no: string | null
  approval_status: string | null
  created_at: string
  updated_at: string
}

export type RefundDetail = RefundSummary & {
  refunded_amount: string
  refundable_amount: string
  job_id: number | null
  job_no: string | null
  job_status: string | null
  audit_logs: AuditLogEntry[]
}

export type RefundSubmitResponse = {
  refund_id: number
  refund_no: string
  order_id: number
  order_no: string
  amount: string
  status: RefundStatus
  approval_id: number
  approval_no: string
  approval_status: string
  replayed: boolean
  message: string
}

export type InventoryMovementType =
  | "opening_balance"
  | "reserve"
  | "release"
  | "fulfill"
  | "restock"
  | "adjust"

export type InventoryMutationType = "reserve" | "release" | "fulfill" | "restock"

export type InventorySnapshot = {
  sku_id: number
  sku_code: string
  product_name: string
  category: string
  status: string
  available_qty: number | null
  reserved_qty: number | null
  total_qty: number | null
  version: number | null
  low_stock: boolean
  low_stock_threshold: number
}

export type InventoryOverviewResponse = {
  low_stock_threshold: number
  low_stock_count: number
  items: InventorySnapshot[]
}

export type InventoryMovementItem = {
  id: number
  sku_id: number
  sku_code: string
  product_name: string
  movement_type: InventoryMovementType
  quantity: number
  available_delta: number
  reserved_delta: number
  before_available: number | null
  after_available: number | null
  before_reserved: number | null
  after_reserved: number | null
  reference_id: string
  note: string | null
  actor_id: number | null
  actor_name: string | null
  created_at: string
}

export type InventoryMovementMutationResponse = {
  snapshot: InventorySnapshot
  movement: InventoryMovementItem
  replayed: boolean
  message: string
}

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
  job_id?: number | null
  job_no?: string | null
}

export type JobStatus = "pending" | "running" | "succeeded" | "retrying" | "failed" | "dead_lettered"
export type JobType =
  | "promotion_schedule"
  | "promotion_activate"
  | "promotion_complete"
  | "refund_execute"
  | "price_change_execute"
export type JobTab = "all" | "needs_attention" | "completed"

export type JobSummary = {
  id: number
  job_no: string
  job_type: JobType
  status: JobStatus
  resource_type: string
  resource_id: number
  resource_no: string
  resource_name: string
  resource_version: number
  attempts: number
  max_attempts: number
  available_at: string
  last_error: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export type JobDetail = JobSummary & {
  idempotency_key: string
  payload: Record<string, unknown>
  result_json: Record<string, unknown> | null
  resource_status: string | null
  can_retry: boolean
  retry_hint: string | null
  audit_logs: AuditLogEntry[]
}

export type JobRetryResponse = {
  job: JobDetail
  message: string
}

export type NotificationCategory = "approval" | "promotion" | "job" | "refund" | "price_change"

export type PriceChangeStatus =
  | "pending_approval"
  | "approved"
  | "processing"
  | "succeeded"
  | "rejected"
  | "cancelled"
  | "failed"

export type PriceHistoryItem = {
  id: number
  sku_id: number
  sku_code: string
  product_name: string
  old_price: string
  new_price: string
  changed_at: string
  actor_id: number | null
  actor_name: string | null
  approval_no: string | null
  price_change_request_id: number | null
  reason: string | null
}

export type PriceChangeSummary = {
  id: number
  request_no: string
  sku_id: number
  sku_code: string
  product_name: string
  old_price: string
  new_price: string
  cost_price: string
  reason: string
  status: PriceChangeStatus
  requester_id: number
  requester_name: string
  failure_reason: string | null
  executed_at: string | null
  approval_id: number | null
  approval_no: string | null
  approval_status: string | null
  created_at: string
  updated_at: string
}

export type PriceChangeDetail = PriceChangeSummary & {
  current_price: string
  sku_status: string
  job_id: number | null
  job_no: string | null
  job_status: string | null
  audit_logs: AuditLogEntry[]
}

export type PriceChangeSubmitResponse = {
  request_id: number
  request_no: string
  sku_code: string
  old_price: string
  new_price: string
  status: PriceChangeStatus
  approval_id: number
  approval_no: string
  approval_status: string
  replayed: boolean
  message: string
}

export type AnalyticsTrendPoint = {
  date_label: string
  full_date: string
  current_gmv: string
  previous_gmv: string
  current_orders: number
  previous_orders: number
}

export type AnalyticsSkuRankItem = {
  sku_code: string
  product_name: string
  units_sold: number
  paid_gmv: string
}

export type AnalyticsOverviewResponse = {
  shop_id: number
  shop_name: string
  timezone: string
  days: number
  start_date: string
  end_date: string
  prev_start_date: string
  prev_end_date: string
  paid_gmv: string
  prev_paid_gmv: string
  gmv_change_pct: string | null
  paid_orders: number
  prev_paid_orders: number
  avg_order_value: string
  prev_avg_order_value: string
  refunded_orders: number
  refund_rate: string
  prev_refund_rate: string
  trend: AnalyticsTrendPoint[]
  sku_ranking: AnalyticsSkuRankItem[]
  methodology: string
}

export type DailyReportResponse = {
  shop_id: number
  shop_name: string
  metrics: {
    report_date: string
    prev_date: string
    timezone: string
    paid_gmv: string
    prev_paid_gmv: string
    gmv_delta: string
    gmv_change_pct: string | null
    paid_orders: number
    avg_order_value: string
    refunded_orders: number
    refunded_amount: string
    refund_rate: string
  }
  headline: string
  facts: string[]
  analysis_boundary: string
  recommendations: string[]
  methodology: string
}

export type SkuTurnoverItem = {
  sku_code: string
  product_name: string
  category: string
  units_sold: number
  available_qty: number
  reserved_qty: number
  turnover_rate: string
  low_stock: boolean
  low_stock_threshold: number
}

export type AnalyticsAnomalyItem = {
  kind: "low_stock" | "refund_rate"
  title: string
  detail: string
  badge: string
  severity: "warning" | "info"
}

export type InventoryTurnoverResponse = {
  shop_id: number
  shop_name: string
  timezone: string
  days: number
  start_date: string
  end_date: string
  total_units_sold: number
  total_available_qty: number
  overall_turnover_rate: string
  low_stock_count: number
  low_stock_threshold: number
  items: SkuTurnoverItem[]
  anomalies: AnalyticsAnomalyItem[]
  methodology: string
}

export type PromotionReviewDayPoint = {
  day_label: string
  promo_date: string
  baseline_date: string
  promo_units: number
  baseline_units: number
  promo_gmv: string
  baseline_gmv: string
}

export type PromotionReviewResponse = {
  promotion_id: number
  promotion_no: string
  promotion_name: string
  status: string
  sku_codes: string[]
  timezone: string
  starts_at: string
  ends_at: string
  promo_start_date: string
  promo_end_date: string
  baseline_start_date: string
  baseline_end_date: string
  duration_days: number
  promo_units: number
  promo_gmv: string
  baseline_units: number
  baseline_gmv: string
  units_change_pct: string | null
  daily_comparison: PromotionReviewDayPoint[]
  methodology: string
  review_note: string
}

export type NotificationItem = {
  id: number
  category: NotificationCategory
  title: string
  content: string
  resource_type: string | null
  resource_id: number | null
  is_read: boolean
  read_at: string | null
  created_at: string
}

export type NotificationListResponse = {
  items: NotificationItem[]
  unread_count: number
}

export type ReadAllResponse = {
  updated_count: number
}

export type VisibilityScope = "org_all" | "org_operator" | "shop_operator" | "approver_only" | "admin_only"
export type KnowledgeStatus = "pending" | "indexing" | "indexed" | "failed"

export type KnowledgeChunkPreview = {
  chunk_id: number
  chunk_index: number
  section_title: string
  page_label: string
  content: string
  has_embedding: boolean
}

export type KnowledgeDocumentSummary = {
  id: number
  doc_no: string
  title: string
  version: number
  is_current: boolean
  shop_id: number | null
  visibility_scope: VisibilityScope
  visibility_label: string
  allowed_roles: string[]
  status: KnowledgeStatus
  embedding_model: string | null
  embedding_dim: number | null
  chunk_count: number
  last_error: string | null
  preview_section: string | null
  preview_page_label: string | null
  preview_excerpt: string | null
  indexed_at: string | null
  updated_at: string
}

export type KnowledgeDocumentDetail = KnowledgeDocumentSummary & {
  raw_content: string
  chunks: KnowledgeChunkPreview[]
}

export type KnowledgeCitationItem = {
  document_id: number
  doc_no: string
  title: string
  document_version: number
  chunk_id: number
  chunk_index: number
  section_title: string
  page_label: string
  visibility_scope: VisibilityScope
  visibility_label: string
  excerpt: string
  content: string
  vector_rank: number | null
  lexical_rank: number | null
  rrf_score: number
  rerank_score: number | null
}

export type KnowledgeSearchResponse = {
  query: string
  items: KnowledgeCitationItem[]
  vector_candidates: number
  lexical_candidates: number
  vector_degraded: boolean
  rerank_degraded: boolean
  embedding_model: string
  rerank_model: string | null
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

export function useInventoryOverviewQuery(auth: ShopReadProps, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "inventory"],
    queryFn: ({ signal }) => fetchShopJson<InventoryOverviewResponse>("inventory", auth, { signal }),
    enabled,
  })
}

export function useInventoryMovementsQuery(
  auth: ShopReadProps,
  skuCode: string,
  movementType: string,
  enabled: boolean,
) {
  const params = new URLSearchParams()
  if (skuCode) params.set("sku_code", skuCode)
  if (movementType) params.set("movement_type", movementType)
  const suffix = params.toString() ? `inventory/movements?${params.toString()}` : "inventory/movements"
  return useQuery({
    queryKey: ["shop", auth.shopId, "inventory", "movements", skuCode, movementType],
    queryFn: ({ signal }) => fetchShopJson<InventoryMovementItem[]>(suffix, auth, { signal }),
    enabled,
  })
}

export function applyInventoryMovementRequest(
  auth: ShopReadProps,
  skuCode: string,
  payload: {
    movement_type: InventoryMutationType
    quantity: number
    reference_id: string
    expected_version?: number
    note?: string
  },
  idempotencyKey?: string,
) {
  return fetchShopJson<InventoryMovementMutationResponse>(
    `inventory/${encodeURIComponent(skuCode)}/movements`,
    auth,
    { payload, idempotencyKey },
  )
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

export function useRefundsQuery(auth: ShopReadProps, enabled: boolean, status = "") {
  const path = status ? `refunds?status=${encodeURIComponent(status)}` : "refunds"
  return useQuery({
    queryKey: ["shop", auth.shopId, "refunds", status],
    queryFn: ({ signal }) => fetchShopJson<RefundSummary[]>(path, auth, { signal }),
    enabled,
  })
}

export function useRefundDetailQuery(auth: ShopReadProps, refundId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "refunds", "detail", refundId],
    queryFn: ({ signal }) => fetchShopJson<RefundDetail>(`refunds/${refundId}`, auth, { signal }),
    enabled,
  })
}

export function submitRefundRequest(
  auth: ShopReadProps,
  payload: { order_id: number; amount: string; reason: string },
  idempotencyKey?: string,
) {
  return fetchShopJson<RefundSubmitResponse>("refunds", auth, { payload, idempotencyKey })
}

export function cancelRefundRequest(auth: ShopReadProps, refundId: number) {
  return fetchShopJson<RefundDetail>(`refunds/${refundId}/cancel`, auth, { payload: {} })
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

export function useJobsQuery(auth: ShopReadProps, tab: JobTab) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "jobs", tab],
    queryFn: ({ signal }) => fetchShopJson<JobSummary[]>(`jobs?tab=${tab}`, auth, { signal }),
  })
}

export function useJobDetailQuery(auth: ShopReadProps, jobId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "jobs", "detail", jobId],
    queryFn: ({ signal }) => fetchShopJson<JobDetail>(`jobs/${jobId}`, auth, { signal }),
    enabled,
  })
}

export function retryJobRequest(auth: ShopReadProps, jobId: number) {
  return fetchShopJson<JobRetryResponse>(`jobs/${jobId}/retry`, auth, { payload: {} })
}

export function useNotificationsQuery(auth: ShopReadProps, unreadOnly: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "notifications", unreadOnly],
    queryFn: ({ signal }) =>
      fetchShopJson<NotificationListResponse>(`notifications?unread_only=${unreadOnly}`, auth, { signal }),
  })
}

export function markNotificationReadRequest(auth: ShopReadProps, notificationId: number) {
  return fetchShopJson<NotificationItem>(`notifications/${notificationId}/read`, auth, { payload: {} })
}

export function markAllNotificationsReadRequest(auth: ShopReadProps) {
  return fetchShopJson<ReadAllResponse>("notifications/read-all", auth, { payload: {} })
}

export function usePriceHistoriesQuery(auth: ShopReadProps, skuCode: string, enabled: boolean) {
  const path = skuCode ? `price-changes/histories?sku_code=${encodeURIComponent(skuCode)}` : "price-changes/histories"
  return useQuery({
    queryKey: ["shop", auth.shopId, "price-changes", "histories", skuCode],
    queryFn: ({ signal }) => fetchShopJson<PriceHistoryItem[]>(path, auth, { signal }),
    enabled,
  })
}

export function usePriceChangesQuery(auth: ShopReadProps, skuCode: string, enabled: boolean) {
  const path = skuCode ? `price-changes?sku_code=${encodeURIComponent(skuCode)}` : "price-changes"
  return useQuery({
    queryKey: ["shop", auth.shopId, "price-changes", "list", skuCode],
    queryFn: ({ signal }) => fetchShopJson<PriceChangeSummary[]>(path, auth, { signal }),
    enabled,
  })
}

export function usePriceChangeDetailQuery(auth: ShopReadProps, requestId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "price-changes", "detail", requestId],
    queryFn: ({ signal }) => fetchShopJson<PriceChangeDetail>(`price-changes/${requestId}`, auth, { signal }),
    enabled,
  })
}

export function submitPriceChangeRequest(
  auth: ShopReadProps,
  payload: { sku_code: string; new_price: string; reason: string },
  idempotencyKey?: string,
) {
  return fetchShopJson<PriceChangeSubmitResponse>("price-changes", auth, { payload, idempotencyKey })
}

export function cancelPriceChangeRequest(auth: ShopReadProps, requestId: number) {
  return fetchShopJson<PriceChangeDetail>(`price-changes/${requestId}/cancel`, auth, { payload: {} })
}

export function useAnalyticsOverviewQuery(auth: ShopReadProps, days: number, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "analytics", "overview", days],
    queryFn: ({ signal }) => fetchShopJson<AnalyticsOverviewResponse>(`analytics/overview?days=${days}`, auth, { signal }),
    enabled,
  })
}

export function useDailyReportQuery(auth: ShopReadProps, enabled: boolean, reportDate = "") {
  const path = reportDate
    ? `analytics/daily-report?report_date=${encodeURIComponent(reportDate)}`
    : "analytics/daily-report"
  return useQuery({
    queryKey: ["shop", auth.shopId, "analytics", "daily-report", reportDate],
    queryFn: ({ signal }) => fetchShopJson<DailyReportResponse>(path, auth, { signal }),
    enabled,
  })
}

export function useInventoryTurnoverQuery(auth: ShopReadProps, days: number, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "analytics", "inventory-turnover", days],
    queryFn: ({ signal }) =>
      fetchShopJson<InventoryTurnoverResponse>(`analytics/inventory-turnover?days=${days}`, auth, { signal }),
    enabled,
  })
}

export function usePromotionReviewQuery(auth: ShopReadProps, promotionId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "promotions", "review", promotionId],
    queryFn: ({ signal }) => fetchShopJson<PromotionReviewResponse>(`promotions/${promotionId}/review`, auth, { signal }),
    enabled: Boolean(enabled && promotionId),
  })
}

export function useKnowledgeDocumentsQuery(auth: ShopReadProps, keyword = "", status = "all") {
  const params = new URLSearchParams()
  if (keyword.trim()) params.set("keyword", keyword.trim())
  if (status && status !== "all") params.set("status", status)
  const path = params.toString() ? `knowledge/documents?${params.toString()}` : "knowledge/documents"
  return useQuery({
    queryKey: ["shop", auth.shopId, "knowledge", "documents", keyword, status],
    queryFn: ({ signal }) => fetchShopJson<KnowledgeDocumentSummary[]>(path, auth, { signal }),
  })
}

export function useKnowledgeDocumentDetailQuery(
  auth: ShopReadProps,
  documentId: number | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["shop", auth.shopId, "knowledge", "detail", documentId],
    queryFn: ({ signal }) =>
      fetchShopJson<KnowledgeDocumentDetail>(`knowledge/documents/${documentId}`, auth, { signal }),
    enabled: Boolean(enabled && documentId),
  })
}

export function createKnowledgeDocumentRequest(
  auth: ShopReadProps,
  payload: {
    doc_no?: string
    title: string
    visibility_scope: VisibilityScope
    shop_only?: boolean
    raw_content: string
  },
) {
  return fetchShopJson<KnowledgeDocumentDetail>("knowledge/documents", auth, { payload })
}

export function reindexKnowledgeDocumentRequest(auth: ShopReadProps, documentId: number) {
  return fetchShopJson<KnowledgeDocumentDetail>(`knowledge/documents/${documentId}/reindex`, auth, {
    payload: {},
  })
}

export function searchKnowledgeRequest(
  auth: ShopReadProps,
  payload: { query: string; top_n?: number },
) {
  return fetchShopJson<KnowledgeSearchResponse>("knowledge/search", auth, { payload })
}
