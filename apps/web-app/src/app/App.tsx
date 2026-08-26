import { useEffect, useState } from 'react'
import ChatPage from '../pages/ChatPage'
import EndpointBuilderPage from '../pages/EndpointBuilderPage'
import EndpointDetailPage from '../pages/EndpointDetailPage'
import EndpointListPage from '../pages/EndpointListPage'
import InvocationLogsPage from '../pages/InvocationLogsPage'
import LoginPage from '../pages/LoginPage'
import { SessionProvider, useSession } from './SessionProvider'
import {
  ADMIN_LOGS_ROUTE,
  DEFAULT_APP_ROUTE,
  ENDPOINTS_ROUTE,
  currentAppRoute,
  replaceAppRoute,
  type AppRoute,
} from './routes'

function RouteShell() {
  const { status, user, logout } = useSession()
  const [route, setRoute] = useState<AppRoute | null>(currentAppRoute)

  useEffect(() => {
    const handlePopState = () => setRoute(currentAppRoute())
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  function openDefaultRoute() {
    if (replaceAppRoute(DEFAULT_APP_ROUTE)) {
      setRoute(DEFAULT_APP_ROUTE)
    }
  }

  function openEndpoints() {
    if ((user?.role === 'member' || user?.role === 'admin') && replaceAppRoute(ENDPOINTS_ROUTE)) {
      setRoute(ENDPOINTS_ROUTE)
    }
  }

  function openAdminLogs() {
    if (user?.role === 'admin' && replaceAppRoute(ADMIN_LOGS_ROUTE)) {
      setRoute(ADMIN_LOGS_ROUTE)
    }
  }

  function openEndpointDetail(endpointId: string) {
    const target = { kind: 'endpoint-detail', endpointId } as const
    if ((user?.role === 'member' || user?.role === 'admin') && replaceAppRoute(target)) {
      setRoute(target)
    }
  }

  function openEndpointBuilder() {
    const target = { kind: 'endpoint-new' } as const
    if ((user?.role === 'member' || user?.role === 'admin') && replaceAppRoute(target)) setRoute(target)
  }

  function openEndpointVersionBuilder(endpointId: string) {
    const target = { kind: 'endpoint-version-new', endpointId } as const
    if ((user?.role === 'member' || user?.role === 'admin') && replaceAppRoute(target)) setRoute(target)
  }

  // If login unmounts LoginPage before onAuthenticated, still redirect unknown paths.
  useEffect(() => {
    if (status !== 'authenticated' || (user?.role !== 'member' && user?.role !== 'admin') ||
        route === DEFAULT_APP_ROUTE || route === ENDPOINTS_ROUTE ||
        (route !== null && typeof route === 'object' && ['endpoint-detail', 'endpoint-new', 'endpoint-version-new'].includes(route.kind)) ||
        (route === ADMIN_LOGS_ROUTE && user?.role === 'admin')) {
      return
    }
    if (replaceAppRoute(DEFAULT_APP_ROUTE)) {
      setRoute(DEFAULT_APP_ROUTE)
    }
  }, [status, route, user?.role])

  if (status === 'initializing') {
    return (
      <main className="app-shell">
        <p role="status" aria-live="polite">正在確認登入狀態…</p>
      </main>
    )
  }
  if (status === 'anonymous') {
    return <LoginPage onAuthenticated={openDefaultRoute} />
  }
  if (user?.role !== 'member' && user?.role !== 'admin') {
    return (
      <main className="app-shell">
        <section className="welcome-card" aria-labelledby="role-error-title">
          <h1 id="role-error-title">無法使用此介面</h1>
          <p role="alert">目前帳號沒有可用的介面權限。</p>
          <button type="button" onClick={() => { void logout().catch(() => {}) }}>登出</button>
        </section>
      </main>
    )
  }
  if (route === DEFAULT_APP_ROUTE) {
    return <ChatPage onOpenEndpoints={openEndpoints} onOpenAdminLogs={openAdminLogs} />
  }
  if (route === ENDPOINTS_ROUTE) {
    return <EndpointListPage onClose={openDefaultRoute} onOpenEndpoint={openEndpointDetail} onCreateEndpoint={openEndpointBuilder} onOpenAdminLogs={openAdminLogs} />
  }
  if (route === ADMIN_LOGS_ROUTE && user.role === 'admin') {
    return <InvocationLogsPage onClose={openDefaultRoute} onOpenEndpoints={openEndpoints} />
  }
  if (typeof route === 'object' && route?.kind === 'endpoint-detail') {
    return <EndpointDetailPage endpointId={route.endpointId} onClose={openDefaultRoute} onCreateVersion={openEndpointVersionBuilder} onOpenEndpoints={openEndpoints} onOpenAdminLogs={openAdminLogs} />
  }
  if (typeof route === 'object' && route?.kind === 'endpoint-new') {
    return <EndpointBuilderPage key={`new:${user.id}`} mode="new" onClose={openEndpoints} onOpenChat={openDefaultRoute} onOpenAdminLogs={openAdminLogs} />
  }
  if (typeof route === 'object' && route?.kind === 'endpoint-version-new') {
    return <EndpointBuilderPage key={`version:${route.endpointId}:${user.id}`} mode="version" endpointId={route.endpointId} onClose={openEndpoints} onOpenChat={openDefaultRoute} onOpenAdminLogs={openAdminLogs} />
  }
  return (
    <main className="app-shell">
      <section className="welcome-card" aria-labelledby="route-error-title">
        <h1 id="route-error-title">找不到頁面</h1>
        <button type="button" onClick={openDefaultRoute}>前往對話</button>
      </section>
    </main>
  )
}

export default function App() {
  return (
    <SessionProvider>
      <RouteShell />
    </SessionProvider>
  )
}
