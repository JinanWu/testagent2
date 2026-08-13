import { useEffect, useState } from 'react'
import ChatPage from '../pages/ChatPage'
import InvocationLogsPage from '../pages/InvocationLogsPage'
import LoginPage from '../pages/LoginPage'
import { SessionProvider, useSession } from './SessionProvider'
import {
  ADMIN_LOGS_ROUTE,
  DEFAULT_APP_ROUTE,
  currentAppRoute,
  replaceAppRoute,
  type AppRoute,
} from './routes'

function RouteShell() {
  const { status, user } = useSession()
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

  function openAdminLogs() {
    if (user?.role === 'admin' && replaceAppRoute(ADMIN_LOGS_ROUTE)) {
      setRoute(ADMIN_LOGS_ROUTE)
    }
  }

  // If login unmounts LoginPage before onAuthenticated, still redirect unknown paths.
  useEffect(() => {
    if (status !== 'authenticated' || route === DEFAULT_APP_ROUTE ||
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
  if (route === DEFAULT_APP_ROUTE) {
    return <ChatPage onOpenAdminLogs={openAdminLogs} />
  }
  if (route === ADMIN_LOGS_ROUTE && user?.role === 'admin') {
    return <InvocationLogsPage onClose={openDefaultRoute} />
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
