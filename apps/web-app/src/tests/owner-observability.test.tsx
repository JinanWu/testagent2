import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiFormatError } from '../api/client'
import {
  getOwnerDiagnostics,
  getOwnerMetrics,
  parseOwnerDiagnostics,
  parseOwnerMetrics,
} from '../api/ownerObservability'
import { formatEndpointDetailRoute, parseAppRoute } from '../app/routes'
import OwnerDiagnostics from '../features/logs/OwnerDiagnostics'

const metrics = () => ({
  endpoint_id: 'endpoint-1',
  window: { start_at: 10, end_at: 20, timezone: 'UTC' },
  invocation_count: 2,
  terminal_count: 2,
  error_count: 1,
  error_rate: 0.5,
  latency_ms: { sample_count: 2, average: 15, p50: 10, p95: 20, maximum: 20 },
  usage: { sample_count: 2, input_tokens: 3, output_tokens: 4, total_tokens: 7 },
  estimated_cost_usd: '0.001',
  cost_by_pricing_version: [{ pricing_version: 'v1', estimated_cost_usd: '0.001' }],
  daily: [{ date: '1970-01-01', invocation_count: 2, terminal_count: 2, error_count: 1,
    usage_total_tokens: 7, estimated_cost_usd: '0.001' }],
  top_errors: [{ error_code: 'timeout', count: 1 }],
})

const item = (id = 'invocation-1') => ({
  invocation_id: id,
  request_id: `request-${id}`,
  endpoint_version_id: 'version-1',
  status: 'failed',
  error_code: 'timeout',
  schema_path: null,
  latency_ms: 12,
  usage: { total_tokens: 7 },
  tool_names: ['skills_list'],
  created_at: 10,
  completed_at: 11,
  redacted_fields: [],
})
const diagnostics = (items: unknown[] = [item()], next_cursor: string | null = null) => ({ items, next_cursor })

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
}

function text(renderer: ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON())
}

describe('A19-03 typed endpoint route', () => {
  it('只接受canonical動態route並可無損format', () => {
    expect(parseAppRoute('/endpoints/endpoint-1')).toEqual({ kind: 'endpoint-detail', endpointId: 'endpoint-1' })
    expect(formatEndpointDetailRoute('endpoint-1')).toBe('/endpoints/endpoint-1')
    for (const invalid of ['/endpoints/', '/endpoints/a/b', '/endpoints/a%2Fb', '/endpoints/../x',
      '/endpoints/é', `/endpoints/${'a'.repeat(129)}`, '/endpoints/endpoint-1/']) {
      expect(parseAppRoute(invalid)).toBeNull()
    }
  })
})

describe('A19-03 owner observability strict client', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock) })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('深度重建合法metrics與diagnostics', () => {
    const raw = metrics()
    const parsed = parseOwnerMetrics(raw)
    raw.daily[0].error_count = 0
    expect(parsed.daily[0].errorCount).toBe(1)
    expect(parseOwnerDiagnostics(diagnostics()).items[0].invocationId).toBe('invocation-1')
  })

  it.each([
    { ...metrics(), raw_error_json: 'SECRET' },
    { ...metrics(), window: { ...metrics().window, extra: true } },
    { ...metrics(), invocation_count: -1 },
    { ...metrics(), error_rate: Number.NaN },
    { ...metrics(), daily: [...metrics().daily, { ...metrics().daily[0], extra: true }] },
    { ...metrics(), top_errors: [{ error_code: 'Bad Code', count: 1 }] },
  ])('拒絕metrics額外欄位、錯型、越界與raw資料', (body) => {
    expect(() => parseOwnerMetrics(body)).toThrow(ApiFormatError)
  })

  it.each([
    { ...diagnostics(), raw: { input: 'SECRET' } },
    diagnostics([{ ...item(), metadata: { secret: true } }]),
    diagnostics([{ ...item(), status: 'unknown' }]),
    diagnostics([{ ...item(), redacted_fields: ['error_json'] }]),
    diagnostics(Array.from({ length: 101 }, (_, index) => item(`invocation-${index}`))),
  ])('拒絕diagnostics raw／extra／越界資料', (body) => {
    expect(() => parseOwnerDiagnostics(body)).toThrow(ApiFormatError)
  })

  it('只送exact same-origin credentialed GET與固定query順序', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(metrics())).mockResolvedValueOnce(jsonResponse(diagnostics()))
    await getOwnerMetrics('endpoint-1', 86400)
    await getOwnerDiagnostics('endpoint-1', { windowSeconds: 86400, limit: 50, cursor: 'opaque.cursor' })
    expect(fetchMock).toHaveBeenNthCalledWith(1,
      '/api/published-endpoints/endpoint-1/metrics?window_seconds=86400', {
        method: 'GET', credentials: 'include', headers: { Accept: 'application/json' },
      })
    expect(fetchMock).toHaveBeenNthCalledWith(2,
      '/api/published-endpoints/endpoint-1/diagnostics?window_seconds=86400&limit=50&cursor=opaque.cursor',
      { method: 'GET', credentials: 'include', headers: { Accept: 'application/json' } })
  })
})

describe('A19-03 OwnerDiagnostics state lifecycle', () => {
  const fetchMock = vi.fn<typeof fetch>()
  let renderer: ReactTestRenderer | undefined
  beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset() })
  afterEach(async () => { if (renderer) await act(async () => renderer!.unmount()); renderer = undefined; vi.unstubAllGlobals() })

  it('呈現metrics與safe diagnostics', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(metrics())).mockResolvedValueOnce(jsonResponse(diagnostics()))
    await act(async () => { renderer = create(<OwnerDiagnostics endpointId="endpoint-1" />) })
    await flush()
    const rendered = text(renderer!)
    for (const expected of ['端點觀測', '終態數', '錯誤數', '平均', '15 ms', 'P50', 'P95',
      'Token 用量', '輸入', '輸出', '歷史價格版本', 'v1', '每日趨勢（', 'UTC',
      ' 終態／', ' tokens／US$ ', 'invocation-1', 'request-invocation-1', 'version-1',
      'skills_list', '建立 ', '完成 ']) {
      expect(rendered).toContain(expected)
    }
    expect(rendered).not.toContain('error_json')
  })

  it('latency零樣本顯示無樣本而非偽裝成0', async () => {
    const zero = { ...metrics(),
      latency_ms: { sample_count: 0, average: null, p50: null, p95: null, maximum: null } }
    fetchMock.mockResolvedValueOnce(jsonResponse(zero)).mockResolvedValueOnce(jsonResponse(diagnostics([])))
    await act(async () => { renderer = create(<OwnerDiagnostics endpointId="endpoint-1" />) })
    await flush()
    expect(text(renderer!)).toContain('無樣本')
  })

  it('卸載會abort所有in-flight觀測request', async () => {
    fetchMock.mockImplementation(() => new Promise<Response>(() => undefined))
    await act(async () => { renderer = create(<OwnerDiagnostics endpointId="endpoint-1" />) })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const signals = fetchMock.mock.calls.map((call) => (call[1] as RequestInit).signal as AbortSignal)
    await act(async () => renderer!.unmount()); renderer = undefined
    expect(signals.every((signal) => signal.aborted)).toBe(true)
  })
})
