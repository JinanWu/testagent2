import { useCallback, useEffect, useRef, useState } from 'react'
import { AUTH_ERROR_MESSAGE } from '../api/auth'
import { listOwnerEndpoints, type OwnerEndpointItem } from '../api/endpoints'
import { useSession } from '../app/SessionProvider'

export const ENDPOINT_LIST_ERROR_MESSAGE = '目前無法載入端點，請稍後再試。'

export interface EndpointListPageProps {
  onClose(): void
  onOpenEndpoint(endpointId: string): void
  onCreateEndpoint(): void
}

function mergeEndpointItems(
  current: readonly OwnerEndpointItem[], incoming: readonly OwnerEndpointItem[],
): OwnerEndpointItem[] {
  const merged = [...current]
  const positions = new Map(current.map((item, index) => [item.endpointId, index]))
  for (const item of incoming) {
    const position = positions.get(item.endpointId)
    if (position === undefined) {
      positions.set(item.endpointId, merged.length)
      merged.push(item)
    } else {
      merged[position] = item
    }
  }
  return merged
}

export default function EndpointListPage({ onClose, onOpenEndpoint, onCreateEndpoint }: EndpointListPageProps) {
  const { logout, registerProtectedStateOwner, user } = useSession()
  const [items, setItems] = useState<OwnerEndpointItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  const controllers = useRef(new Set<AbortController>())
  const mounted = useRef(false)
  const loadingMoreRef = useRef(false)

  const invalidate = useCallback((clearState: boolean) => {
    generation.current += 1
    for (const controller of controllers.current) controller.abort()
    controllers.current.clear()
    loadingMoreRef.current = false
    if (clearState && mounted.current) {
      setItems([])
      setNextCursor(null)
      setLoading(false)
      setLoadingMore(false)
      setError(null)
    }
    return generation.current
  }, [])

  const loadFirstPage = useCallback(async () => {
    const requestGeneration = invalidate(false)
    const controller = new AbortController()
    controllers.current.add(controller)
    setItems([])
    setNextCursor(null)
    setError(null)
    setLoading(true)
    try {
      const result = await listOwnerEndpoints(
        { scope: 'owner', limit: 20 },
        { signal: controller.signal },
      )
      if (!mounted.current || controller.signal.aborted || generation.current !== requestGeneration) return
      setItems(mergeEndpointItems([], result.items))
      setNextCursor(result.nextCursor)
    } catch {
      if (mounted.current && !controller.signal.aborted && generation.current === requestGeneration) {
        setError(ENDPOINT_LIST_ERROR_MESSAGE)
      }
    } finally {
      controllers.current.delete(controller)
      if (mounted.current && generation.current === requestGeneration) setLoading(false)
    }
  }, [invalidate])

  useEffect(() => {
    mounted.current = true
    const registration = registerProtectedStateOwner(() => { invalidate(true) })
    void loadFirstPage()
    return () => {
      mounted.current = false
      registration.unregister()
      invalidate(false)
    }
  }, [user?.id, invalidate, loadFirstPage, registerProtectedStateOwner])

  async function loadMore() {
    if (nextCursor === null || loadingMoreRef.current) return
    loadingMoreRef.current = true
    const requestGeneration = generation.current
    const cursor = nextCursor
    const controller = new AbortController()
    controllers.current.add(controller)
    setError(null)
    setLoadingMore(true)
    try {
      const result = await listOwnerEndpoints(
        { scope: 'owner', limit: 20, cursor },
        { signal: controller.signal },
      )
      if (!mounted.current || controller.signal.aborted || generation.current !== requestGeneration) return
      setItems((current) => mergeEndpointItems(current, result.items))
      setNextCursor(result.nextCursor)
    } catch {
      if (mounted.current && !controller.signal.aborted && generation.current === requestGeneration) {
        setError(ENDPOINT_LIST_ERROR_MESSAGE)
      }
    } finally {
      controllers.current.delete(controller)
      if (mounted.current && generation.current === requestGeneration) {
        loadingMoreRef.current = false
        setLoadingMore(false)
      }
    }
  }

  return (
    <main className="app-shell">
      <section className="welcome-card endpoint-detail" aria-labelledby="endpoint-list-title">
        <nav aria-label="端點管理導覽">
          <button type="button" onClick={onClose}>返回對話</button>
          <button type="button" onClick={() => {
            void logout().catch(() => { if (mounted.current) setError(AUTH_ERROR_MESSAGE) })
          }}>登出</button>
        </nav>
        <p className="eyebrow">TestAgent2</p>
        <h1 id="endpoint-list-title">端點管理</h1>
        <button type="button" onClick={onCreateEndpoint}>建立端點</button>
        {loading ? <p role="status" aria-live="polite">正在載入端點…</p> : error ? <p role="alert">{error}</p> : items.length === 0 ? (
          <p>目前沒有端點。</p>
        ) : (
          <ul aria-label="端點清單">
            {items.map((item) => (
              <li key={item.endpointId}>
                <button type="button" onClick={() => onOpenEndpoint(item.endpointId)}>{item.slug}</button>
                <span> {item.status}</span>
              </li>
            ))}
          </ul>
        )}
        {error && items.length > 0 && <p role="alert">{error}</p>}
        {nextCursor !== null && (
          <button type="button" disabled={loadingMore} onClick={() => { void loadMore() }}>
            {loadingMore ? '載入中…' : '載入更多'}
          </button>
        )}
      </section>
    </main>
  )
}
