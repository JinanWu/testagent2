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
import { 卡片, 空狀態, 狀態色調, 狀態標籤, 資料列, 載入中, 輸入樣式, 錯誤訊息 } from '../../ui/元件'
import { 狀態文字 } from '../../ui/格式'

export interface OwnerDiagnosticsProps { endpointId: string }

/** 與 ownerObservability 的 STATUSES 同一組；順序照生命週期排。 */
const 狀態選項 = ['pending', 'running', 'succeeded', 'failed', 'rate_limited', 'invalid_api_key'] as const

function latency(value: number | null): string { return value === null ? '無樣本' : `${value} ms` }

/** 指標卡：設計稿的四格總覽。 */
function 指標卡({ 名稱, 數值, 註記, 強調 = false }: {
  名稱: string; 數值: string; 註記?: string; 強調?: boolean
}) {
  return (
    <div className="flex flex-col justify-between rounded-lg border border-outline-variant bg-surface-container-lowest p-md shadow-[0_4px_6px_-1px_rgba(15,23,42,0.1)]">
      <p className="mb-sm font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
        {名稱}
      </p>
      <p className={[
        'font-display-lg text-display-lg',
        強調 ? 'text-error' : 'text-on-surface',
      ].join(' ')}
      >
        {數值}
      </p>
      {註記 && <p className="mt-xs font-label-sm text-label-sm text-on-surface-variant">{註記}</p>}
    </div>
  )
}

/**
 * 純 CSS 長條圖：專案未引入圖表函式庫，且 CSP 禁止外部資源。
 * 給了 點選 就把整列變成按鈕（用來把錯誤碼接到底下的診斷紀錄篩選）；沒給就是純展示。
 */
function 長條圖({ 資料, 點選, 已選 }: {
  資料: ReadonlyArray<{ 標籤: string; 值: number; 次要值?: number }>
  點選?: (標籤: string) => void
  已選?: string | null
}) {
  const 最大 = 資料.reduce((大, 項) => Math.max(大, 項.值), 0)
  if (最大 <= 0) return <空狀態>區間內沒有資料。</空狀態>
  const 版面 = 'grid w-full grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-sm text-left'
  return (
    <ul className="flex flex-col gap-xs">
      {資料.map((項) => {
        const 內容 = (
          <>
            <span className="truncate font-code-md text-code-md text-on-surface-variant">{項.標籤}</span>
            <span className="flex h-4 overflow-hidden rounded bg-surface-container">
              <span
                className="bg-secondary"
                style={{ width: `${(項.值 / 最大) * 100}%` }}
                aria-hidden={true}
              />
              {典型正數(項.次要值) && (
                <span
                  className="bg-error"
                  style={{ width: `${(項.次要值! / 最大) * 100}%` }}
                  aria-hidden={true}
                />
              )}
            </span>
            <span className="font-code-md text-code-md text-on-surface">{項.值}</span>
          </>
        )
        return (
          <li key={項.標籤}>
            {點選 === undefined ? (
              <div className={版面}>{內容}</div>
            ) : (
              <button
                type="button"
                aria-pressed={已選 === 項.標籤}
                onClick={() => 點選(項.標籤)}
                className={[
                  版面,
                  'rounded px-xs py-0.5 transition-colors hover:bg-surface-container',
                  已選 === 項.標籤 ? 'bg-surface-container ring-1 ring-secondary' : '',
                ].join(' ')}
              >
                {內容}
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function 典型正數(值: number | undefined): boolean {
  return typeof 值 === 'number' && Number.isFinite(值) && 值 > 0
}

export default function OwnerDiagnostics({ endpointId }: OwnerDiagnosticsProps) {
  const [windowSeconds, setWindowSeconds] = useState(86400)
  const [metrics, setMetrics] = useState<OwnerMetrics | null>(null)
  const [items, setItems] = useState<OwnerDiagnosticItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [paging, setPaging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /*
   * 診斷紀錄的篩選只在前端做：後端 /diagnostics 只收 window_seconds／limit／cursor，
   * 沒有 status 或 error_code 參數。因此篩選僅涵蓋「已載入」的紀錄，
   * 卡片上必須把這件事寫明，否則使用者會把它當成全區間的結果。
   */
  const [狀態篩選, set狀態篩選] = useState('')
  const [錯誤碼篩選, set錯誤碼篩選] = useState<string | null>(null)
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

  const 已篩選 = 狀態篩選 !== '' || 錯誤碼篩選 !== null
  const 可見紀錄 = items.filter((item) => (
    (狀態篩選 === '' || item.status === 狀態篩選)
    && (錯誤碼篩選 === null || item.errorCode === 錯誤碼篩選)
  ))

  return (
    <section className="owner-diagnostics flex flex-col gap-md" aria-labelledby="owner-diagnostics-title">
      <header className="flex flex-wrap items-center justify-between gap-md">
        <h2 id="owner-diagnostics-title" className="font-headline-md text-headline-md text-on-surface">
          端點觀測
        </h2>
        <div className="flex items-center gap-sm">
          <label htmlFor="owner-window" className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
            觀測區間
          </label>
          <select
            id="owner-window"
            value={windowSeconds}
            onChange={(event) => setWindowSeconds(Number(event.currentTarget.value))}
            className={`${輸入樣式} w-40 py-1.5`}
          >
            <option value={86400}>最近24小時</option>
            <option value={604800}>最近7天</option>
            <option value={2592000}>最近30天</option>
          </select>
        </div>
      </header>

      {loading && <載入中>正在載入觀測資料…</載入中>}
      {error && <錯誤訊息>{error}</錯誤訊息>}

      {metrics && (
        <div className="owner-metrics flex flex-col gap-md" aria-label="端點指標">
          <div className="grid gap-md sm:grid-cols-2 xl:grid-cols-4">
            <指標卡 名稱="呼叫數" 數值={String(metrics.invocationCount)} 註記={`終態數 ${metrics.terminalCount}`} />
            <指標卡 名稱="錯誤數" 數值={String(metrics.errorCount)} 強調={metrics.errorCount > 0} />
            <指標卡
              名稱="錯誤率"
              數值={`${(metrics.errorRate * 100).toFixed(1)}%`}
              強調={metrics.errorRate > 0}
            />
            <指標卡
              名稱="預估成本"
              數值={`US$ ${metrics.estimatedCostUsd}`}
              註記={`總 tokens ${metrics.usage.totalTokens}`}
            />
          </div>

          <div className="grid gap-md lg:grid-cols-2">
            <卡片 標題="延遲">
              <dl>
                <資料列 名稱="樣本數">{metrics.latencyMs.sampleCount}</資料列>
                <資料列 名稱="平均">{latency(metrics.latencyMs.average)}</資料列>
                <資料列 名稱="P50">{latency(metrics.latencyMs.p50)}</資料列>
                <資料列 名稱="P95">{latency(metrics.latencyMs.p95)}</資料列>
                <資料列 名稱="最大">{latency(metrics.latencyMs.maximum)}</資料列>
              </dl>
            </卡片>

            <卡片 標題="Token 用量">
              <dl>
                <資料列 名稱="樣本數">{metrics.usage.sampleCount}</資料列>
                <資料列 名稱="輸入">{metrics.usage.inputTokens}</資料列>
                <資料列 名稱="輸出">{metrics.usage.outputTokens}</資料列>
                <資料列 名稱="總數">{metrics.usage.totalTokens}</資料列>
              </dl>
            </卡片>
          </div>

          <卡片 標題="歷史價格版本">
            <ul className="flex flex-col gap-xs">
              {metrics.costByPricingVersion.map((entry) => (
                <li
                  key={entry.pricingVersion}
                  className="flex items-baseline justify-between gap-sm border-b border-outline-variant/60 py-1 last:border-b-0 font-body-md text-body-md"
                >
                  {entry.pricingVersion}：US$ {entry.estimatedCostUsd}
                </li>
              ))}
            </ul>
          </卡片>

          <卡片 標題={`每日趨勢（${metrics.window.timezone}）`}>
            <長條圖
              資料={metrics.daily.map((day) => ({
                標籤: day.date,
                值: day.invocationCount,
                次要值: day.errorCount,
              }))}
            />
          </卡片>

          {/* 點錯誤碼直接篩選底下的診斷紀錄：看到錯誤後的下一步一定是看那幾筆。 */}
          <卡片 標題="常見錯誤" 說明="點錯誤碼可篩選下方的診斷紀錄">
            {metrics.topErrors.length === 0 ? (
              <空狀態>沒有安全錯誤摘要。</空狀態>
            ) : (
              <長條圖
                資料={metrics.topErrors.map((entry) => ({ 標籤: entry.errorCode, 值: entry.count }))}
                已選={錯誤碼篩選}
                點選={(標籤) => set錯誤碼篩選((前) => (前 === 標籤 ? null : 標籤))}
              />
            )}
          </卡片>
        </div>
      )}

      <卡片
        標題="安全診斷紀錄"
        說明={已篩選
          ? `符合 ${可見紀錄.length} 筆／已載入 ${items.length} 筆；篩選只作用在已載入的紀錄`
          : undefined}
        無內距={true}
        動作={(
          <>
            <select
              aria-label="狀態篩選"
              value={狀態篩選}
              onChange={(event) => set狀態篩選(event.currentTarget.value)}
              className={`${輸入樣式} w-36 py-1.5`}
            >
              <option value="">全部狀態</option>
              {狀態選項.map((值) => (
                <option key={值} value={值}>{狀態文字(值)}</option>
              ))}
            </select>
            {已篩選 && (
              <button
                type="button"
                onClick={() => { set狀態篩選(''); set錯誤碼篩選(null) }}
                className="rounded border border-outline-variant px-3 py-1.5 font-label-sm text-label-sm text-secondary transition-colors hover:border-secondary hover:bg-surface-container"
              >
                清除篩選
              </button>
            )}
          </>
        )}
      >
        {!loading && !error && items.length === 0 && <空狀態>目前沒有安全診斷紀錄。</空狀態>}
        {!loading && !error && items.length > 0 && 可見紀錄.length === 0 && (
          <空狀態>已載入的紀錄沒有符合篩選的項目；可按「載入更多」再撈。</空狀態>
        )}
        {錯誤碼篩選 !== null && (
          <p className="border-b border-outline-variant bg-surface-container-low px-lg py-sm font-code-md text-code-md text-on-surface-variant">
            錯誤碼：{錯誤碼篩選}
          </p>
        )}
        {/*
         * 診斷紀錄會隨呼叫一直累積，固定高度讓它在框內自己捲，頁面就不會被撐長；
         * 「載入更多」留在捲動區外，才不會被捲走而點不到。
         */}
        <div className="max-h-[28rem] overflow-y-auto">
          <ul aria-label="安全診斷紀錄" className="divide-y divide-outline-variant/60">
            {可見紀錄.map((item) => (
              <li key={item.invocationId} className="flex flex-col gap-xs p-md">
                <div className="flex flex-wrap items-center gap-sm">
                  <strong className="font-code-md text-code-md text-secondary">{item.invocationId}</strong>
                  <狀態標籤 色調={狀態色調(item.status)}>{狀態文字(item.status)}</狀態標籤>
                  {item.errorCode && !item.redactedFields.includes('error_code') ? `／${item.errorCode}` : ''}
                  {item.schemaPath && !item.redactedFields.includes('schema_path') ? `／${item.schemaPath}` : ''}
                </div>
                <span className="font-code-md text-code-md text-on-surface-variant">
                  ／request {item.requestId}／version {item.endpointVersionId}／延遲 {latency(item.latencyMs)}
                  ／tokens {item.usage?.totalTokens ?? '無樣本'}／工具 {item.toolNames.length === 0 ? '無' : item.toolNames.join('、')}
                  ／建立 {item.createdAt}／完成 {item.completedAt ?? '未完成'}
                </span>
                {item.redactedFields.length > 0 && (
                  <span className="font-label-sm text-label-sm text-on-surface-variant">
                    {`（遮蔽欄位：${item.redactedFields.join('、')}）`}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
        {nextCursor && (
          <div className="flex justify-center border-t border-outline-variant bg-surface-container-low p-md">
            {/* 子節點維持純字串，與其他分頁按鈕一致 */}
            <button
              type="button"
              disabled={paging}
              onClick={() => { void loadMore() }}
              className="rounded border border-outline-variant bg-surface-container-lowest px-6 py-1.5 font-body-md text-body-md font-semibold text-secondary transition-colors hover:border-secondary hover:bg-surface-container disabled:cursor-not-allowed disabled:opacity-50"
            >
              {paging ? '載入中…' : '載入更多'}
            </button>
          </div>
        )}
      </卡片>
    </section>
  )
}
