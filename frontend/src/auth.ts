export type LoginValues = {
  username: string
  password: string
}

export type ShopAccess = {
  id: number
  organization_id: number
  name: string
  timezone?: string
  roles: string[]
}

export type Session = {
  access_token: string
  user: { id: number; username: string; name: string }
  shops: ShopAccess[]
}

async function postAuth(action: "login" | "refresh", credentials?: LoginValues) {
  return fetch(`/api/auth/${action}`, {
    method: "POST",
    credentials: "same-origin",
    ...(credentials && {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...credentials, username: credentials.username.trim() }),
    }),
  })
}

export class LoginError extends Error {}

export async function login(credentials: LoginValues): Promise<Session> {
  const response = await postAuth("login", credentials)
  if (response.status === 401) throw new LoginError("账号或密码错误。")
  if (response.status === 422) throw new LoginError("请检查账号和密码的格式。")
  if (!response.ok) throw new Error("Login failed")
  return response.json()
}

let refreshing: Promise<Session | null> | undefined

export function refreshSession(): Promise<Session | null> {
  refreshing ??= postAuth("refresh").then(response => {
    if (response.status === 401 || response.status === 403) return null
    if (!response.ok) throw new Error("Session refresh failed")
    return response.json() as Promise<Session>
  }).finally(() => { refreshing = undefined })
  return refreshing
}

export type ShopReadProps = {
  shopId: number
  accessToken: string
  onSessionRenewed: (session: Session) => void
  onSignIn: () => void
}

export async function readShop(
  resource: string,
  { shopId, accessToken, onSessionRenewed }: ShopReadProps,
  signal?: AbortSignal,
  payload?: object,
  options?: { idempotencyKey?: string },
): Promise<Response | undefined> {
  const request = (token: string) => {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      ...(payload && { "Content-Type": "application/json" }),
      ...(options?.idempotencyKey && { "Idempotency-Key": options.idempotencyKey }),
    }
    return fetch(`/api/shops/${shopId}/${resource}`, {
      ...(payload && { method: "POST", body: JSON.stringify(payload) }),
      headers,
      signal,
    })
  }
  const response = await request(accessToken)
  if (signal?.aborted) return
  if (response.status !== 401) return response
  const renewed = await refreshSession()
  if (signal?.aborted) return
  if (!renewed) return response
  onSessionRenewed(renewed)
  // 修复多店铺场景：校验续期后的授权列表是否仍包含当前请求的 shopId
  if (!renewed.shops.some(shop => shop.id === shopId)) return
  return request(renewed.access_token)
}

export async function logout(accessToken: string): Promise<void> {
  const response = await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "same-origin",
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  if (!response.ok) throw new Error("Logout failed")
}
