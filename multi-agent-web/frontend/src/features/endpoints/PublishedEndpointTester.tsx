import { useEffect, useMemo, useRef, useState } from 'react'
import type { JsonValue } from '../../api/endpoints'
import { invokePublishedEndpoint, type PublishedInvokeResult } from '../../api/publishedInvoke'
import { 等寬輸入樣式 } from '../../ui/元件'
import 圖示 from '../../ui/圖示'

interface Props {
  slug: string
  apiKey: string
  inputSchema: JsonValue | null
}

function schemaRecord(schema: JsonValue | null): Record<string, JsonValue> | null {
  return schema !== null && !Array.isArray(schema) && typeof schema === 'object'
    ? schema as Record<string, JsonValue>
    : null
}

function sampleFromSchema(schema: JsonValue | null, depth = 0): JsonValue {
  const record = schemaRecord(schema)
  if (!record || depth > 4) return '請輸入測試內容'
  if (record.default !== undefined) return record.default
  if (record.example !== undefined) return record.example
  switch (record.type) {
    case 'string': return '請輸入測試內容'
    case 'integer': case 'number': return 0
    case 'boolean': return true
    case 'array': return []
    case 'object': {
      const properties = schemaRecord(record.properties)
      if (!properties) return {}
      const result: Record<string, JsonValue> = {}
      for (const [key, child] of Object.entries(properties).slice(0, 12)) {
        result[key] = sampleFromSchema(child, depth + 1)
      }
      return result
    }
    default: return '請輸入測試內容'
  }
}

function formatResult(result: PublishedInvokeResult): string {
  return JSON.stringify(result.body, null, 2)
}

export default function PublishedEndpointTester({ slug, apiKey, inputSchema }: Props) {
  const initial = useMemo(() => JSON.stringify(sampleFromSchema(inputSchema), null, 2), [inputSchema])
  const [input, setInput] = useState(initial)
  const [result, setResult] = useState<PublishedInvokeResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  async function run() {
    if (running) return
    let parsed: JsonValue
    try {
      parsed = JSON.parse(input) as JsonValue
    } catch {
      setError('輸入必須是有效的 JSON。')
      return
    }
    const next = new AbortController()
    controller.current = next
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      setResult(await invokePublishedEndpoint(slug, apiKey, parsed, next.signal))
    } catch (cause) {
      if (!next.signal.aborted) setError(cause instanceof Error ? cause.message : '測試呼叫失敗。')
    } finally {
      if (controller.current === next) controller.current = null
      if (!next.signal.aborted) setRunning(false)
    }
  }

  return (
    <section aria-labelledby="endpoint-test-title" className="w-full overflow-hidden rounded-2xl border border-outline-variant bg-surface-container-low text-left shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-md border-b border-outline-variant px-lg py-md">
        <div>
          <div className="flex items-center gap-sm">
            <span aria-hidden={true} className="text-primary"><圖示 名稱="發布" 大小={20} /></span>
            <h3 id="endpoint-test-title" className="font-body-lg text-body-lg font-bold text-on-surface">離開前，試跑一次</h3>
            <span className="rounded-full bg-primary-container px-sm py-0.5 font-body-sm text-body-sm font-semibold text-on-primary-container">單次測試</span>
          </div>
          <p className="mt-xs font-body-md text-body-md text-on-surface-variant">直接呼叫剛發布的端點，API key 不會被保存。</p>
        </div>
        <code className="rounded-lg bg-surface-container-highest px-sm py-xs font-mono text-body-sm text-on-surface-variant">POST /v1/endpoints/{slug}/invoke</code>
      </div>

      <div className="grid gap-0 lg:grid-cols-2">
        <div className="border-b border-outline-variant p-lg lg:border-b-0 lg:border-r">
          <label htmlFor="published-test-input" className="mb-sm block font-body-md text-body-md font-semibold text-on-surface">測試輸入 <span className="font-normal text-on-surface-variant">JSON</span></label>
          <textarea id="published-test-input" value={input} rows={9} spellCheck={false}
            onChange={(event) => { setInput(event.target.value); setError(null) }}
            className={`${等寬輸入樣式} resize-y bg-surface font-mono text-body-sm leading-6`} />
          <div className="mt-md flex items-center justify-between gap-md">
            <p className="font-body-sm text-body-sm text-on-surface-variant">根據 input schema 預先產生</p>
            <button type="button" aria-label="送出測試" disabled={running} onClick={() => { void run() }}
              className="inline-flex items-center gap-sm rounded-xl bg-on-surface px-lg py-sm font-body-md text-body-md font-semibold text-surface transition-transform hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50">
              <圖示 名稱="傳送" 大小={17} />
              {running ? '呼叫中…' : '送出測試'}
            </button>
          </div>
          {error && <p role="alert" className="mt-md rounded-lg bg-error-container px-md py-sm font-body-md text-body-md text-on-error-container">{error}</p>}
        </div>

        <div className="flex min-h-[17rem] flex-col bg-surface p-lg">
          <div className="mb-sm flex min-h-6 flex-wrap items-center gap-sm">
            <span className="font-body-md text-body-md font-semibold text-on-surface">回應</span>
            {result && (
              <>
                <span className={`rounded-full px-sm py-0.5 font-mono text-body-sm font-bold ${result.ok ? 'bg-success/10 text-success' : 'bg-error-container text-on-error-container'}`}>{result.status} {result.ok ? 'OK' : 'ERROR'}</span>
                <span className="font-mono text-body-sm text-on-surface-variant">{result.elapsedMs} ms</span>
              </>
            )}
          </div>
          {result ? (
            <>
              <pre aria-label="API 測試回應" className="程式碼區塊 min-h-[12rem] flex-1 overflow-auto whitespace-pre-wrap break-words p-md">{formatResult(result)}</pre>
              {result.requestId && <p className="mt-sm font-mono text-body-sm text-on-surface-variant">request ID · {result.requestId}</p>}
            </>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-outline-variant text-center text-on-surface-variant">
              <span aria-hidden={true} className="mb-sm opacity-60"><圖示 名稱="端點" 大小={28} /></span>
              <p className="font-body-md text-body-md font-semibold">尚未送出測試</p>
              <p className="mt-xs font-body-sm text-body-sm">回應狀態與 JSON 會顯示在這裡。</p>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
