import { useCallback, useEffect, useRef, useState } from 'react'
import { AUTH_ERROR_MESSAGE } from '../api/auth'
import { ApiResponseError } from '../api/client'
import { getOwnerEndpoint, type OwnerEndpointDetail } from '../api/endpoints'
import { useSession } from '../app/SessionProvider'
import CredentialManager from '../features/endpoints/CredentialManager'
import EndpointDocs from '../features/endpoints/EndpointDocs'
import OwnerDiagnostics from '../features/logs/OwnerDiagnostics'

export const ENDPOINT_NOT_FOUND_MESSAGE = '找不到端點或無權存取。'
export const ENDPOINT_DETAIL_ERROR_MESSAGE = '目前無法載入端點詳情，請稍後再試。'

export interface EndpointDetailPageProps {
  endpointId: string
  onClose(): void
  onCreateVersion(endpointId: string): void
}

type DetailState =
  | { kind: 'loading' }
  | { kind: 'ready'; detail: OwnerEndpointDetail }
  | { kind: 'not-found' }
  | { kind: 'error' }

type DetailTab = 'overview' | 'credentials' | 'docs' | 'diagnostics'
const DETAIL_TABS: ReadonlyArray<Readonly<{ id: DetailTab; label: string }>> = [
  { id: 'overview', label: 'Overview' },
  { id: 'credentials', label: 'Credentials' },
  { id: 'docs', label: 'Docs' },
  { id: 'diagnostics', label: 'Diagnostics' },
]

export default function EndpointDetailPage({ endpointId, onClose, onCreateVersion }: EndpointDetailPageProps) {
  const { logout, registerProtectedStateOwner, user } = useSession()
  const [state, setState] = useState<DetailState>({ kind: 'loading' })
  const [logoutError, setLogoutError] = useState(false)
  const [logoutPending, setLogoutPending] = useState(false)
  const [requestRevision, setRequestRevision] = useState(0)
  const [activeTab, setActiveTab] = useState<DetailTab>('overview')
  const mounted = useRef(false)
  const epoch = useRef(0)
  const controller = useRef<AbortController | null>(null)

  const invalidate = useCallback((clear: boolean) => {
    epoch.current += 1
    controller.current?.abort()
    controller.current = null
    if (clear && mounted.current) {
      setState({ kind: 'loading' })
      setLogoutError(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    const registration = registerProtectedStateOwner(() => invalidate(true))
    const requestEpoch = ++epoch.current
    const requestController = new AbortController()
    controller.current = requestController
    setState({ kind: 'loading' })
    setActiveTab('overview')
    void getOwnerEndpoint(endpointId, { signal: requestController.signal }).then(
      (detail) => {
        if (mounted.current && !requestController.signal.aborted && epoch.current === requestEpoch) {
          setState({ kind: 'ready', detail })
        }
      },
      (error: unknown) => {
        if (!mounted.current || requestController.signal.aborted || epoch.current !== requestEpoch) return
        setState(error instanceof ApiResponseError && error.status === 404 ? { kind: 'not-found' } : { kind: 'error' })
      },
    ).finally(() => {
      if (controller.current === requestController) controller.current = null
    })
    return () => {
      mounted.current = false
      registration.unregister()
      invalidate(false)
    }
  }, [endpointId, user?.id, requestRevision, invalidate, registerProtectedStateOwner])

  function handleLogout() {
    if (logoutPending) return
    setLogoutPending(true)
    setLogoutError(false)
    void logout().catch(() => {
      if (mounted.current) setLogoutError(true)
    }).finally(() => {
      if (!mounted.current) return
      setLogoutPending(false)
      setRequestRevision((current) => current + 1)
    })
  }

  return (
    <main className="app-shell">
      <section className="welcome-card endpoint-detail" aria-labelledby="endpoint-title">
        <nav aria-label="端點詳情導覽">
          <button type="button" onClick={onClose}>返回對話</button>
          <button type="button" disabled={logoutPending} onClick={handleLogout}>
            {logoutPending ? '登出中…' : '登出'}
          </button>
        </nav>
        {logoutError && <p role="alert">{AUTH_ERROR_MESSAGE}</p>}
        <h1 id="endpoint-title">端點詳情</h1>
        {state.kind === 'loading' && <p role="status" aria-live="polite">正在載入端點詳情…</p>}
        {state.kind === 'not-found' && <p role="alert">{ENDPOINT_NOT_FOUND_MESSAGE}</p>}
        {state.kind === 'error' && <p role="alert">{ENDPOINT_DETAIL_ERROR_MESSAGE}</p>}
        {state.kind === 'ready' && (
          <>
            <div role="tablist" aria-label="端點資料分頁">
              {DETAIL_TABS.map((tab) => <button key={tab.id} type="button" role="tab"
                aria-selected={activeTab === tab.id} onClick={() => setActiveTab(tab.id)}>{tab.label}</button>)}
            </div>
            {activeTab === 'overview' && <section role="tabpanel" aria-label="Overview">
              <dl aria-label="端點基本資料">
                <dt>Slug</dt><dd>{state.detail.slug}</dd>
                <dt>狀態</dt><dd>{state.detail.status}</dd>
                <dt>目前版本</dt><dd>{state.detail.currentVersionNumber === null ? '尚未發布' : `版本 ${state.detail.currentVersionNumber}`}</dd>
              </dl>
              <button type="button" onClick={() => onCreateVersion(state.detail.endpointId)}>建立新版本</button>
            </section>}
            {activeTab === 'credentials' && <div role="tabpanel" aria-label="Credentials">
              <CredentialManager endpointId={state.detail.endpointId} />
            </div>}
            {activeTab === 'docs' && <div role="tabpanel" aria-label="Docs">
              <EndpointDocs endpointId={state.detail.endpointId} />
            </div>}
            {activeTab === 'diagnostics' && <div role="tabpanel" aria-label="Diagnostics">
              <OwnerDiagnostics endpointId={state.detail.endpointId} />
            </div>}
          </>
        )}
      </section>
    </main>
  )
}
