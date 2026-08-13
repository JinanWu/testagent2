import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  LOGS_ERROR_MESSAGE,
  LOGS_NOT_FOUND_MESSAGE,
  LogsError,
  getInvocationDetail,
  listInvocations,
  type AdminInvocationDetail as InvocationDetailData,
  type InvocationListFilters,
  type InvocationListItem,
} from '../api/logs'
import { useSession } from '../app/SessionProvider'
import AdminInvocationDetail from '../features/logs/AdminInvocationDetail'

const FORBIDDEN_MESSAGE = '只有管理者可查看完整呼叫紀錄。'

export interface InvocationLogsPageProps {
  onClose(): void
}

export default function InvocationLogsPage({ onClose }: InvocationLogsPageProps) {
  const { user, logout } = useSession()
  const [endpointId, setEndpointId] = useState('')
  const [fromAt, setFromAt] = useState('')
  const [toAt, setToAt] = useState('')
  const [status, setStatus] = useState('')
  const [errorCode, setErrorCode] = useState('')
  const [items, setItems] = useState<InvocationListItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [activeFilters, setActiveFilters] = useState<InvocationListFilters>({ limit: 50 })
  const [detail, setDetail] = useState<InvocationDetailData | null>(null)
  const [selected, setSelected] = useState<InvocationListItem | null>(null)
  const [pending, setPending] = useState(false)
  const [detailPending, setDetailPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const epoch = useRef(0)
  const controllers = useRef(new Set<AbortController>())

  const clearSensitiveState = useCallback(() => {
    setDetail(null)
    setSelected(null)
    setDetailPending(false)
  }, [])

  const invalidate = useCallback((clearList = false) => {
    epoch.current += 1
    for (const controller of controllers.current) controller.abort()
    controllers.current.clear()
    clearSensitiveState()
    setPending(false)
    if (clearList) {
      setItems([])
      setNextCursor(null)
    }
    return epoch.current
  }, [clearSensitiveState])

  useEffect(() => () => { invalidate(true) }, [invalidate])

  useEffect(() => {
    if (user?.role !== 'admin') invalidate(true)
  }, [user?.id, user?.role, invalidate])

  const loadList = useCallback(async (
    targetEndpoint: string,
    filters: InvocationListFilters,
    append: boolean,
  ) => {
    const requestEpoch = invalidate(!append)
    const controller = new AbortController()
    controllers.current.add(controller)
    setPending(true)
    setError(null)
    try {
      const page = await listInvocations(targetEndpoint, filters, controller.signal)
      if (epoch.current !== requestEpoch || controller.signal.aborted) return
      setItems((current) => append ? [...current, ...page.items] : page.items)
      setNextCursor(page.nextCursor)
      setActiveFilters({ ...filters, cursor: undefined })
    } catch {
      if (epoch.current === requestEpoch && !controller.signal.aborted) {
        setItems([])
        setNextCursor(null)
        setError(LOGS_ERROR_MESSAGE)
      }
    } finally {
      controllers.current.delete(controller)
      if (epoch.current === requestEpoch) setPending(false)
    }
  }, [invalidate])

  async function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const filters: InvocationListFilters = {
      limit: 50,
      ...(fromAt ? { fromAt: Number(fromAt) } : {}),
      ...(toAt ? { toAt: Number(toAt) } : {}),
      ...(status ? { status } : {}),
      ...(errorCode.trim() ? { errorCode: errorCode.trim() } : {}),
    }
    await loadList(endpointId.trim(), filters, false)
  }

  async function openDetail(item: InvocationListItem) {
    const requestEpoch = invalidate()
    const controller = new AbortController()
    controllers.current.add(controller)
    setSelected(item)
    setDetailPending(true)
    setError(null)
    try {
      const value = await getInvocationDetail(item.endpointId, item.invocationId, controller.signal)
      if (epoch.current !== requestEpoch || controller.signal.aborted) return
      setDetail(value)
    } catch (caught) {
      if (epoch.current === requestEpoch && !controller.signal.aborted) {
        clearSensitiveState()
        setError(caught instanceof LogsError && caught.status === 404 ? LOGS_NOT_FOUND_MESSAGE : LOGS_ERROR_MESSAGE)
      }
    } finally {
      controllers.current.delete(controller)
      if (epoch.current === requestEpoch) setDetailPending(false)
    }
  }

  if (user?.role !== 'admin') {
    return <main className="app-shell"><p role="alert">{FORBIDDEN_MESSAGE}</p></main>
  }

  return (
    <main className="app-shell">
      <section className="welcome-card logs-card" aria-labelledby="logs-title">
        <p className="eyebrow">Admin Audit</p>
        <h1 id="logs-title">完整呼叫紀錄</h1>
        <nav aria-label="管理員紀錄導覽">
          <button type="button" onClick={() => { invalidate(true); onClose() }}>返回對話</button>
          <button type="button" onClick={() => {
            invalidate(true)
            void logout().catch(() => { setError(LOGS_ERROR_MESSAGE) })
          }}>登出</button>
        </nav>
        <form aria-label="篩選呼叫紀錄" onSubmit={submitFilters}>
          <label htmlFor="logs-endpoint">端點識別碼</label>
          <input id="logs-endpoint" value={endpointId} required maxLength={128}
            onChange={(event) => setEndpointId(event.currentTarget.value)} />
          <label htmlFor="logs-status">狀態</label>
          <select id="logs-status" value={status} onChange={(event) => setStatus(event.currentTarget.value)}>
            <option value="">全部</option>
            <option value="pending">等待中</option>
            <option value="running">執行中</option>
            <option value="succeeded">成功</option>
            <option value="failed">失敗</option>
            <option value="rate_limited">流量限制</option>
            <option value="invalid_api_key">API Key 無效</option>
          </select>
          <label htmlFor="logs-from-at">起始時間（Unix epoch）</label>
          <input id="logs-from-at" type="number" min="0" step="any" value={fromAt}
            onChange={(event) => setFromAt(event.currentTarget.value)} />
          <label htmlFor="logs-to-at">結束時間（Unix epoch）</label>
          <input id="logs-to-at" type="number" min="0" step="any" value={toAt}
            onChange={(event) => setToAt(event.currentTarget.value)} />
          <label htmlFor="logs-error-code">錯誤碼</label>
          <input id="logs-error-code" value={errorCode} maxLength={128}
            onChange={(event) => setErrorCode(event.currentTarget.value)} />
          <button type="submit" disabled={pending || !endpointId.trim()}>{pending ? '載入中…' : '查詢'}</button>
        </form>
        {error && <p role="alert">{error}</p>}
        {!pending && items.length === 0 && !error && <p>尚無呼叫紀錄。</p>}
        <ul aria-label="呼叫紀錄">
          {items.map((item) => (
            <li key={item.invocationId}>
              <button type="button" aria-pressed={selected?.invocationId === item.invocationId}
                onClick={() => { void openDetail(item) }}>
                {item.invocationId} — {item.status}{item.hasRedaction ? '（已遮蔽）' : ''}
              </button>
            </li>
          ))}
        </ul>
        {nextCursor && <button type="button" disabled={pending} onClick={() => {
          void loadList(endpointId.trim(), { ...activeFilters, cursor: nextCursor }, true)
        }}>載入下一頁</button>}
        {detailPending && <p role="status">正在載入詳情…</p>}
        {detail && selected && <AdminInvocationDetail detail={detail} hasRedaction={selected.hasRedaction} />}
      </section>
    </main>
  )
}
