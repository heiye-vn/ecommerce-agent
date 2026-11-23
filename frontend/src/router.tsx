import { QueryClientProvider } from "@tanstack/react-query"
import { Spin } from "antd"
import { useEffect, useRef, useState } from "react"
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom"

import App from "./App"
import { createQueryClient } from "./api/queries"
import { login, logout, refreshSession, type LoginValues, type Session, type ShopAccess } from "./auth"
import AgentPage from "./pages/agent"
import ApprovalsPage from "./pages/approvals"
import CatalogPage from "./pages/catalog"
import LoginPage from "./pages/login"
import OrdersPage from "./pages/orders"
import PromotionsPage from "./pages/promotions"

function WorkspaceLayout({
  shop,
  shops,
  userName,
  onSelectShop,
  onLogout,
}: {
  shop: ShopAccess
  shops: ShopAccess[]
  userName: string
  onSelectShop: (shopId: number) => void
  onLogout: () => Promise<void>
}) {
  return (
    <App
      shopName={shop.name}
      userName={userName}
      shops={shops}
      activeShopId={shop.id}
      onSelectShop={onSelectShop}
      onLogout={onLogout}
    >
      <Outlet />
    </App>
  )
}

function AuthRoutes() {
  const location = useLocation()
  const [session, setSession] = useState<Session | null>()
  const [activeShopId, setActiveShopId] = useState<number | null>(null)
  const startup = useRef<Promise<Session | null> | null>(null)

  useEffect(() => {
    let active = true
    startup.current ??= refreshSession().catch(() => null)
    void startup.current.then((restored) => {
      if (active) setSession(restored)
    })
    return () => { active = false }
  }, [])

  async function handleLogin(values: LoginValues) {
    const nextSession = await login(values)
    setSession(nextSession)
    setActiveShopId(nextSession.shops[0]?.id ?? null)
  }

  async function handleLogout() {
    if (!session) return
    await logout(session.access_token)
    setSession(null)
    setActiveShopId(null)
  }

  if (session === undefined) return <Spin fullscreen tip="正在检查登录状态" />
  const shops = session?.shops ?? []
  const shop = shops.find(item => item.id === activeShopId) ?? shops[0]
  const loginRedirect = <Navigate to="/login" state={{ from: `${location.pathname}${location.search}${location.hash}` }} replace />
  const shopRead = session && shop ? {
    shopId: shop.id,
    accessToken: session.access_token,
    onSessionRenewed: setSession,
    onSignIn: () => {
      setSession(null)
      setActiveShopId(null)
    },
  } : null

  return (
    <Routes>
      <Route
        path="/login"
        element={shop
          ? <Navigate to={location.state?.from ?? "/"} replace />
          : <LoginPage onLogin={handleLogin} />}
      />
      <Route
        element={shop && shopRead && session
          ? <WorkspaceLayout shop={shop} shops={shops} userName={session.user.name} onSelectShop={setActiveShopId} onLogout={handleLogout} />
          : loginRedirect}
      >
        <Route
          path="/"
          element={shop && shopRead
            ? <AgentPage shopName={shop.name} {...shopRead} />
            : null}
        />
        <Route
          path="/catalog"
          element={shop && shopRead
            ? <CatalogPage key={shop.id} {...shopRead} />
            : null}
        />
        <Route
          path="/promotions"
          element={shop && shopRead
            ? <PromotionsPage shopName={shop.name} shopRoles={shop.roles} userId={session?.user.id ?? 0} {...shopRead} />
            : null}
        />
        <Route
          path="/promotions/:promotionId"
          element={shop && shopRead
            ? <PromotionsPage shopName={shop.name} shopRoles={shop.roles} userId={session?.user.id ?? 0} {...shopRead} />
            : null}
        />
        <Route
          path="/orders"
          element={shop && shopRead ? <OrdersPage {...shopRead} /> : null}
        />
        <Route
          path="/orders/:orderId"
          element={shop && shopRead ? <OrdersPage {...shopRead} /> : null}
        />
        <Route
          path="/approvals"
          element={shop && shopRead && session
            ? <ApprovalsPage shopRoles={shop.roles} userName={session.user.name} {...shopRead} />
            : null}
        />
        <Route
          path="/approvals/:approvalId"
          element={shop && shopRead && session
            ? <ApprovalsPage shopRoles={shop.roles} userName={session.user.name} {...shopRead} />
            : null}
        />
      </Route>
      <Route
        path="*"
        element={shop && session
          ? <App shopName={shop.name} userName={session.user.name} shops={shops} activeShopId={shop.id} onSelectShop={setActiveShopId} onLogout={handleLogout} />
          : loginRedirect}
      />
    </Routes>
  )
}

export default function AppRouter() {
  const [queryClient] = useState(() => createQueryClient())
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
