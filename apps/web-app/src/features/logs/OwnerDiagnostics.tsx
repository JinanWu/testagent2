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

function latency(value: number | null): string { return value === null ? '無樣本' : `${value} ms` }

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
  const pageOwner = useRef<number | null>(null)
  const pageSequence = useRef(0)

  const invalidate = useCallback(() => {
    generation.current += 1
    for (const controller of initialControllers.current) controller.abort()
    initialControllers.current = []
    pageController.current?.abort()
    pageController.current = null
    pageOwner.current = null
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
    if (nextCursor === null || paging || loading || pageOwner.current !== null) return
    const current = generation.current
    const owner = ++pageSequence.current
    pageOwner.current = owner
    const controller = new AbortController()
    pageController.current?.abort(); pageController.current = controller
    setPaging(true); setError(null)
    try {
      const page = await getOwnerDiagnostics(endpointId, { windowSeconds, limit: 50, cursor: nextCursor }, controller.signal)
      if (generation.current !== current || controller.signal.aborted || pageController.current !== controller ||
          pageOwner.current !== owner) return
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
      if (generation.current === current && pageController.current === controller && pageOwner.current === owner) {
        pageController.current = null; pageOwner.current = null; setPaging(false)
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
        <p>終態數<strong>{metrics.terminalCount}</strong></p>
        <p>錯誤數<strong>{metrics.errorCount}</strong></p>
        <p>錯誤率<strong>{(metrics.errorRate * 100).toFixed(1)}%</strong></p>
        <h3>延遲</h3>
        <dl><dt>樣本數</dt><dd>{metrics.latencyMs.sampleCount}</dd>
          <dt>平均</dt><dd>{latency(metrics.latencyMs.average)}</dd>
          <dt>P50</dt><dd>{latency(metrics.latencyMs.p50)}</dd>
          <dt>P95</dt><dd>{latency(metrics.latencyMs.p95)}</dd>
          <dt>最大</dt><dd>{latency(metrics.latencyMs.maximum)}</dd></dl>
        <h3>Token 用量</h3>
        <dl><dt>樣本數</dt><dd>{metrics.usage.sampleCount}</dd>
          <dt>輸入</dt><dd>{metrics.usage.inputTokens}</dd>
          <dt>輸出</dt><dd>{metrics.usage.outputTokens}</dd>
          <dt>總數</dt><dd>{metrics.usage.totalTokens}</dd></dl>
        <p>預估成本<strong>US$ {metrics.estimatedCostUsd}</strong></p>
        <h3>歷史價格版本</h3>
        <ul>{metrics.costByPricingVersion.map((entry) => <li key={entry.pricingVersion}>
          {entry.pricingVersion}：US$ {entry.estimatedCostUsd}
        </li>)}</ul>
        <h3>每日趨勢（{metrics.window.timezone}）</h3>
        <ul>{metrics.daily.map((day) => <li key={day.date}>{day.date}：{day.invocationCount} 次／
          {day.terminalCount} 終態／{day.errorCount} 錯誤／{day.usageTotalTokens} tokens／US$ {day.estimatedCostUsd}
        </li>)}</ul>
        <h3>常見錯誤</h3>
        {metrics.topErrors.length === 0 ? <p>沒有安全錯誤摘要。</p> :
          <ol>{metrics.topErrors.map((entry) => <li key={entry.errorCode}>{entry.errorCode}：{entry.count}</li>)}</ol>}
      </div>}
      {!loading && !error && items.length === 0 && <p>目前沒有安全診斷紀錄。</p>}
      <ul aria-label="安全診斷紀錄">{items.map((item) => <li key={item.invocationId}>
        <strong>{item.invocationId}</strong> — {item.status}
        {item.errorCode && !item.redactedFields.includes('error_code') ? `／${item.errorCode}` : ''}
        {item.schemaPath && !item.redactedFields.includes('schema_path') ? `／${item.schemaPath}` : ''}
        <span>／request {item.requestId}／version {item.endpointVersionId}／延遲 {latency(item.latencyMs)}
          ／tokens {item.usage?.totalTokens ?? '無樣本'}／工具 {item.toolNames.length === 0 ? '無' : item.toolNames.join('、')}
          ／建立 {item.createdAt}／完成 {item.completedAt ?? '未完成'}
        </span>
        {item.redactedFields.length > 0
          ? `（遮蔽欄位：${item.redactedFields.join('、')}）`
          : ''}
      </li>)}</ul>
      {nextCursor && <button type="button" disabled={paging} onClick={() => { void loadMore() }}>
        {paging ? '載入中…' : '載入更多'}
      </button>}
    </section>
  )
}
