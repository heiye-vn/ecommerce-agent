import { readShop, type ShopReadProps } from "../auth"

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

async function parseApiError(response: Response): Promise<ApiError> {
  let code = `HTTP_${response.status}`
  let message = "请求失败，请稍后重试。"
  try {
    const body = (await response.json()) as { code?: string; message?: string; detail?: string }
    if (body.code) code = body.code
    if (body.message) message = body.message
    else if (typeof body.detail === "string") message = body.detail
  } catch {
    // 非 JSON 响应沿用默认兜底信息
  }
  return new ApiError(response.status, code, message)
}

export async function fetchShopJson<Data>(
  resource: string,
  auth: ShopReadProps,
  options?: { signal?: AbortSignal; payload?: object; idempotencyKey?: string },
): Promise<Data> {
  const response = await readShop(
    resource,
    auth,
    options?.signal,
    options?.payload,
    options?.idempotencyKey ? { idempotencyKey: options.idempotencyKey } : undefined,
  )
  if (!response) {
    throw new ApiError(403, "SHOP_ACCESS_DENIED", "当前店铺授权已变更。")
  }
  if (!response.ok) {
    throw await parseApiError(response)
  }
  return (await response.json()) as Data
}
