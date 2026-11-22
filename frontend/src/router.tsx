import { Spin } from "antd"
import { useEffect, useRef, useState } from "react"
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom"

import App from "./App"
import { login, logout, refreshSession, type LoginValues, type Session } from "./auth"
import AgentPage from "./pages/agent"
import CatalogPage from "./pages/catalog"
import LoginPage from "./pages/login"
import OrdersPage from "./pages/orders"

function AuthRoutes() {
  const location = useLocation()
  const [session, setSession] = useState<Session | null>()
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
    setSession(await login(values))
  }

  async function handleLogout() {
    if (!session) return
    await logout(session.access_token)
    setSession(null)
  }

  if (session === undefined) return <Spin fullscreen tip="正在检查登录状态" />
  const shop = session?.shops[0]
  const loginRedirect = <Navigate to="/login" state={{ from: `${location.pathname}${location.search}${location.hash}` }} replace />
  const shopRead = session && shop ? {
    shopId: shop.id, accessToken: session.access_token,
    onSessionRenewed: setSession, onSignIn: () => setSession(null),
  } : null
  const orders = shop && shopRead ? <App shopName={shop.name} onLogout={handleLogout}><OrdersPage {...shopRead} /></App> : loginRedirect

  return (
    <Routes>
      <Route
        path="/login"
        element={shop
          ? <Navigate to={location.state?.from ?? "/"} replace />
          : <LoginPage onLogin={handleLogin} />}
      />
      <Route
        path="/catalog"
        element={shop && shopRead
          ? <App shopName={shop.name} onLogout={handleLogout}>
              <CatalogPage key={shop.id} {...shopRead} />
            </App>
          : loginRedirect}
      />
      <Route path="/orders" element={orders} />
      <Route path="/orders/:orderId" element={orders} />
      <Route path="/" element={shop && shopRead
        ? <App shopName={shop.name} onLogout={handleLogout}><AgentPage key={shop.id} shopName={shop.name} {...shopRead} /></App>
        : loginRedirect} />
      <Route
        path="*"
        element={shop ? <App shopName={shop.name} onLogout={handleLogout} /> : loginRedirect}
      />
    </Routes>
  )
}

export default function AppRouter() {
  return (
    <BrowserRouter>
      <AuthRoutes />
    </BrowserRouter>
  )
}
