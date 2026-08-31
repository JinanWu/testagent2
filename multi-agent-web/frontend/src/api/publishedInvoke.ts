import { byteLength } from './client'
import type { JsonValue } from './endpoints'

const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/
const API_KEY = /^pk_[A-Za-z0-9_-]{43}$/
const MAX_REQUEST_BYTES = 32 * 1024
const MAX_RESPONSE_BYTES = 1024 * 1024

export interface PublishedInvokeResult {
  status: number
  ok: boolean
  elapsedMs: number
  body: JsonValue
  requestId: string | null
}

function requestId(body: JsonValue): string | null {
  if (body === null || Array.isArray(body) || typeof body !== 'object') return null
  const root = body as Record<string, JsonValue>
  const envelope = root.envelope
  const source = envelope !== null && !Array.isArray(envelope) && typeof envelope === 'object'
    ? envelope as Record<string, JsonValue>
    : root
  const invocation = source.invocation
  if (invocation === null || Array.isArray(invocation) || typeof invocation !== 'object') return null
  const value = (invocation as Record<string, JsonValue>).request_id
  return typeof value === 'string' && value.length <= 128 ? value : null
}

async function readBoundedJson(response: Response, signal?: AbortSignal): Promise<JsonValue> {
  const declared = response.headers.get('content-length')
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_RESPONSE_BYTES)) {
    throw new Error('回應太大，無法在測試區顯示。')
  }
  const reader = response.body?.getReader()
  if (!reader) {
    const text = await response.text()
    if (byteLength(text) > MAX_RESPONSE_BYTES) throw new Error('回應太大，無法在測試區顯示。')
    return JSON.parse(text) as JsonValue
  }
  const chunks: Uint8Array[] = []
  let total = 0
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException('要求已取消', 'AbortError')
      const { done, value } = await reader.read()
      if (done) break
      total += value.byteLength
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel()
        throw new Error('回應太大，無法在測試區顯示。')
      }
      chunks.push(value)
    }
  } finally {
    reader.releaseLock()
  }
  const bytes = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as JsonValue
}

export async function invokePublishedEndpoint(
  slug: string,
  apiKey: string,
  input: JsonValue,
  signal?: AbortSignal,
): Promise<PublishedInvokeResult> {
  if (!SLUG.test(slug) || !API_KEY.test(apiKey)) throw new Error('測試參數無效。')
  const body = JSON.stringify({ input })
  if (byteLength(body) > MAX_REQUEST_BYTES) throw new Error('測試輸入太大。')
  const started = performance.now()
  const response = await fetch(`/v1/endpoints/${encodeURIComponent(slug)}/invoke`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body,
    signal,
  })
  const parsed = await readBoundedJson(response, signal)
  return {
    status: response.status,
    ok: response.ok,
    elapsedMs: Math.max(0, Math.round(performance.now() - started)),
    body: parsed,
    requestId: requestId(parsed),
  }
}
