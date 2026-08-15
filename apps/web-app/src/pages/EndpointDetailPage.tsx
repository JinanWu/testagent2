import { useState } from 'react'
import { useSession } from '../app/SessionProvider'
import OwnerDiagnostics from '../features/logs/OwnerDiagnostics'

export interface EndpointDetailPageProps { endpointId: string; onClose(): void }

export default function EndpointDetailPage({ endpointId, onClose }: EndpointDetailPageProps) {
  const { logout } = useSession()
  const [logoutError, setLogoutError] = useState(false)
  return (
    <main className="app-shell">
      <section className="welcome-card endpoint-detail" aria-labelledby="endpoint-title">
        <nav aria-label="端點詳情導覽">
          <button type="button" onClick={onClose}>返回對話</button>
          <button type="button" onClick={() => {
            onClose()
            void logout().catch(() => { setLogoutError(true) })
          }}>登出</button>
        </nav>
        {logoutError && <p role="alert">登出要求未完成，請稍後重試。</p>}
        <h1 id="endpoint-title">端點詳情</h1>
        <p><code>{endpointId}</code></p>
        <OwnerDiagnostics endpointId={endpointId} />
      </section>
    </main>
  )
}
