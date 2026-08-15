import { useCallback, useEffect, useRef, useState } from 'react'
import {
  OWNER_OBSERVABILITY_ERROR_MESSAGE,
  OWNER_OBSERVABILITY_NOT_FOUND_MESSAGE,
  OwnerObservabilityError,
  getOwnerDiagnostics,
  getOwnerMetrics,
  type OwnerDiagnosticItem,
  type OwnerMetrics,
} from '../../api/ownerObservability'

export interface OwnerDiagnosticsProps { endpointId: string }

export default function OwnerDiagnostics({ endpointId }: OwnerDiagnosticsProps) {
  const [windowSeconds, setWindowSeconds] = useState(86400)
  const [metrics, setMetrics] = useState<OwnerMetrics | null>(null)
  const [items, setItems] = useState<OwnerDiagnosticItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [paging, setPaging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  const initialControllers = useRef<AbortController[]>([])
  const pageController = useRef<AbortController | null>(null)

  const invalidate = useCallback(() => {
    generation.current += 1
    for (const controller of initialControllers.current) controller.abort()
    initialControllers.current = []
    pageController.current?.abort()
    pageController.current = null
    setMetrics(null); setItems([]); setNextCursor(null); setError(null); setPaging(false)
    return generation.current
  }, [])

  useEffect(() => {
    const current = invalidate()
    const metricsController = new AbortController()
    const diagnosticsController = new AbortController()
    initialControllers.current = [metricsController, diagnosticsController]
    setLoading(true)
    Promise.all([
      getOwnerMetrics(endpointId, windowSeconds, metricsController.signal),
      getOwnerDiagnostics(endpointId, { windowSeconds, limit: 50 }, diagnosticsController.signal),
    ]).then(([metricsValue, page]) => {
      if (generation.current !== current || metricsController.signal.aborted || diagnosticsController.signal.aborted) return
      setMetrics(metricsValue); setItems(page.items); setNextCursor(page.nextCursor)
    }).catch((caught: unknown) => {
      if (generation.current !== current || metricsController.signal.aborted || diagnosticsController.signal.aborted) return
      setError(caught instanceof OwnerObservabilityError && caught.status === 404
        ? OWNER_OBSERVABILITY_NOT_FOUND_MESSAGE : OWNER_OBSERVABILITY_ERROR_MESSAGE)
    }).finally(() => {
      if (generation.current === current) { initialControllers.current = []; setLoading(false) }
    })
    return () => { invalidate() }
  }, [endpointId, windowSeconds, invalidate])

  async function loadMore() {
    if (nextCursor === null || paging || loading) return
    const current = generation.current
    const controller = new AbortController()
    pageController.current?.abort(); pageController.current = controller
    setPaging(true); setError(null)
    try {
      const page = await getOwnerDiagnostics(endpointId, { windowSeconds, limit: 50, cursor: nextCursor }, controller.signal)
      if (generation.current !== current || controller.signal.aborted || pageController.current !== controller) return
      setItems((existing) => {
        const identifiers = new Set(existing.map((item) => item.invocationId))
        if (page.items.some((item) => identifiers.has(item.invocationId))) {
          setError(OWNER_OBSERVABILITY_ERROR_MESSAGE); setNextCursor(null); return []
        }
        return [...existing, ...page.items]
      })
      setNextCursor(page.nextCursor)
    } catch {
      if (generation.current === current && !controller.signal.aborted) setError(OWNER_OBSERVABILITY_ERROR_MESSAGE)
    } finally {
      if (generation.current === current && pageController.current === controller) {
        pageController.current = null; setPaging(false)
      }
    }
  }

  return (
    <section className="owner-diagnostics" aria-labelledby="owner-diagnostics-title">
      <header>
        <p className="eyebrow">Owner Observability</p>
        <h2 id="owner-diagnostics-title">端點觀測</h2>
        <label htmlFor="owner-window">觀測區間</label>
        <select id="owner-window" value={windowSeconds} onChange={(event) => setWindowSeconds(Number(event.currentTarget.value))}>
          <option value={86400}>最近24小時</option>
          <option value={604800}>最近7天</option>
          <option value={2592000}>最近30天</option>
        </select>
      </header>
      {loading && <p role="status">正在載入觀測資料…</p>}
      {error && <p role="alert">{error}</p>}
      {metrics && <div className="owner-metrics" aria-label="端點指標">
        <p>呼叫數<strong>{metrics.invocationCount}</strong></p>
        <p>錯誤率<strong>{(metrics.errorRate * 100).toFixed(1)}%</strong></p>
        <p>Token總數<strong>{metrics.usage.totalTokens}</strong></p>
        <p>預估成本<strong>US$ {metrics.estimatedCostUsd}</strong></p>
        <h3>每日趨勢</h3>
        <ul>{metrics.daily.map((day) => <li key={day.date}>{day.date}：{day.invocationCount} 次／{day.errorCount} 錯誤</li>)}</ul>
        <h3>常見錯誤</h3>
        {metrics.topErrors.length === 0 ? <p>沒有安全錯誤摘要。</p> :
          <ol>{metrics.topErrors.map((entry) => <li key={entry.errorCode}>{entry.errorCode}：{entry.count}</li>)}</ol>}
      </div>}
      {!loading && !error && items.length === 0 && <p>目前沒有安全診斷紀錄。</p>}
      <ul aria-label="安全診斷紀錄">{items.map((item) => <li key={item.invocationId}>
        <strong>{item.invocationId}</strong> — {item.status}
        {item.errorCode && !item.redactedFields.includes('error_code') ? `／${item.errorCode}` : ''}
        {item.redactedFields.length > 0 ? '（部分欄位已遮蔽）' : ''}
      </li>)}</ul>
      {nextCursor && <button type="button" disabled={paging} onClick={() => { void loadMore() }}>
        {paging ? '載入中…' : '載入更多'}
      </button>}
    </section>
  )
}
