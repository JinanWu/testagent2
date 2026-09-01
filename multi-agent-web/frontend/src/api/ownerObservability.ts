import {
  ApiFormatError,
  ApiResponseError,
  apiRequest,
  boundedString,
  exactObject,
  type ApiRoute,
} from './client'

const IDENTIFIER = /^[A-Za-z0-9_-]{1,128}$/
const RESPONSE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const ERROR_CODE = /^[a-z][a-z0-9_.-]{0,127}$/
const DECIMAL = /^(?:0|[1-9][0-9]{0,36})(?:\.[0-9]{1,28})?$/
const DATE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/
const STATUSES = new Set(['pending', 'running', 'succeeded', 'failed', 'rate_limited', 'invalid_api_key'])
const REDACTED = new Set(['error_code', 'schema_path'])

export const OWNER_OBSERVABILITY_ERROR_MESSAGE = '目前無法載入端點觀測資料。'
export const OWNER_OBSERVABILITY_NOT_FOUND_MESSAGE = '找不到這個端點。'

export class OwnerObservabilityError extends Error {
  constructor(readonly status: number) {
    super(status === 404 ? OWNER_OBSERVABILITY_NOT_FOUND_MESSAGE : OWNER_OBSERVABILITY_ERROR_MESSAGE)
    this.name = 'OwnerObservabilityError'
  }
}

export interface OwnerMetrics {
  endpointId: string
  window: { startAt: number; endAt: number; timezone: 'UTC' }
  invocationCount: number
  terminalCount: number
  errorCount: number
  errorRate: number
  latencyMs: { sampleCount: number; average: number | null; p50: number | null; p95: number | null; maximum: number | null }
  usage: { sampleCount: number; inputTokens: number; outputTokens: number; totalTokens: number }
  estimatedCostUsd: string
  costByPricingVersion: Array<{ pricingVersion: string; estimatedCostUsd: string }>
  daily: Array<{ date: string; invocationCount: number; terminalCount: number; errorCount: number; usageTotalTokens: number; estimatedCostUsd: string }>
  topErrors: Array<{ errorCode: string; count: number }>
}

export interface OwnerDiagnosticItem {
  invocationId: string
  requestId: string
  endpointVersionId: string
  status: string
  errorCode: string | null
  schemaPath: string | null
  latencyMs: number | null
  usage: { totalTokens: number } | null
  toolNames: string[]
  createdAt: number
  completedAt: number | null
  redactedFields: Array<'error_code' | 'schema_path'>
}

export interface OwnerDiagnosticsPage { items: OwnerDiagnosticItem[]; nextCursor: string | null }

function fail(): never { throw new ApiFormatError() }
function safeInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) fail()
  return value as number
}
function finite(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) fail()
  return value
}
function nullableFinite(value: unknown): number | null { return value === null ? null : finite(value) }
function text(value: unknown, maximum: number, allowEmpty = false): string {
  if (typeof value !== 'string' || value.length > maximum || (!allowEmpty && value.length === 0)) fail()
  return value
}
function decimal(value: unknown): string {
  const result = text(value, 66)
  if (!DECIMAL.test(result)) fail()
  return result
}

export function parseOwnerMetrics(value: unknown): OwnerMetrics {
  const root = exactObject(value, ['endpoint_id', 'window', 'invocation_count', 'terminal_count', 'error_count',
    'error_rate', 'latency_ms', 'usage', 'estimated_cost_usd', 'cost_by_pricing_version', 'daily', 'top_errors'])
  if (!root || typeof root.endpoint_id !== 'string' || !RESPONSE_IDENTIFIER.test(root.endpoint_id)) fail()
  const window = exactObject(root.window, ['start_at', 'end_at', 'timezone'])
  const latency = exactObject(root.latency_ms, ['sample_count', 'average', 'p50', 'p95', 'maximum'])
  const usage = exactObject(root.usage, ['sample_count', 'input_tokens', 'output_tokens', 'total_tokens'])
  if (!window || !latency || !usage || window.timezone !== 'UTC') fail()
  const startAt = finite(window.start_at); const endAt = finite(window.end_at)
  if (startAt > endAt) fail()
  const invocationCount = safeInteger(root.invocation_count)
  const terminalCount = safeInteger(root.terminal_count)
  const errorCount = safeInteger(root.error_count)
  const errorRate = finite(root.error_rate)
  if (terminalCount > invocationCount || errorCount > terminalCount || errorRate > 1) fail()
  const latencyValues = [nullableFinite(latency.average), nullableFinite(latency.p50), nullableFinite(latency.p95), nullableFinite(latency.maximum)] as const
  const latencySampleCount = safeInteger(latency.sample_count)
  if ((latencySampleCount === 0) !== latencyValues.every((entry) => entry === null)) fail()
  if (!Array.isArray(root.cost_by_pricing_version) || root.cost_by_pricing_version.length > 4096 ||
      !Array.isArray(root.daily) || root.daily.length > 31 || !Array.isArray(root.top_errors) || root.top_errors.length > 10) fail()
  const inputTokens = safeInteger(usage.input_tokens); const outputTokens = safeInteger(usage.output_tokens)
  const totalTokens = safeInteger(usage.total_tokens)
  if (inputTokens + outputTokens !== totalTokens) fail()
  return {
    endpointId: root.endpoint_id,
    window: { startAt, endAt, timezone: 'UTC' },
    invocationCount, terminalCount, errorCount, errorRate,
    latencyMs: { sampleCount: latencySampleCount, average: latencyValues[0], p50: latencyValues[1], p95: latencyValues[2], maximum: latencyValues[3] },
    usage: { sampleCount: safeInteger(usage.sample_count), inputTokens, outputTokens, totalTokens },
    estimatedCostUsd: decimal(root.estimated_cost_usd),
    costByPricingVersion: root.cost_by_pricing_version.map((entry) => {
      const row = exactObject(entry, ['pricing_version', 'estimated_cost_usd'])
      if (!row || typeof row.pricing_version !== 'string' || !RESPONSE_IDENTIFIER.test(row.pricing_version)) fail()
      return { pricingVersion: row.pricing_version, estimatedCostUsd: decimal(row.estimated_cost_usd) }
    }),
    daily: root.daily.map((entry) => {
      const row = exactObject(entry, ['date', 'invocation_count', 'terminal_count', 'error_count', 'usage_total_tokens', 'estimated_cost_usd'])
      if (!row || typeof row.date !== 'string' || !DATE.test(row.date)) fail()
      const dailyInvocation = safeInteger(row.invocation_count); const dailyTerminal = safeInteger(row.terminal_count)
      const dailyError = safeInteger(row.error_count)
      if (dailyTerminal > dailyInvocation || dailyError > dailyTerminal) fail()
      return { date: row.date, invocationCount: dailyInvocation, terminalCount: dailyTerminal, errorCount: dailyError,
        usageTotalTokens: safeInteger(row.usage_total_tokens), estimatedCostUsd: decimal(row.estimated_cost_usd) }
    }),
    topErrors: root.top_errors.map((entry) => {
      const row = exactObject(entry, ['error_code', 'count'])
      if (!row || typeof row.error_code !== 'string' || !ERROR_CODE.test(row.error_code)) fail()
      const count = safeInteger(row.count); if (count < 1) fail()
      return { errorCode: row.error_code, count }
    }),
  }
}

export function parseOwnerDiagnostics(value: unknown): OwnerDiagnosticsPage {
  const root = exactObject(value, ['items', 'next_cursor'])
  if (!root || !Array.isArray(root.items) || root.items.length > 100 ||
      !(root.next_cursor === null || boundedString(root.next_cursor, 1024))) fail()
  const seen = new Set<string>()
  const items = root.items.map((entry) => {
    const row = exactObject(entry, ['invocation_id', 'request_id', 'endpoint_version_id', 'status', 'error_code',
      'schema_path', 'latency_ms', 'usage', 'tool_names', 'created_at', 'completed_at', 'redacted_fields'])
    if (!row) fail()
    const invocationId = text(row.invocation_id, 128)
    if (seen.has(invocationId)) fail(); seen.add(invocationId)
    if (typeof row.status !== 'string' || !STATUSES.has(row.status) || !Array.isArray(row.tool_names) ||
        row.tool_names.length > 4096 || !Array.isArray(row.redacted_fields) || row.redacted_fields.length > 2) fail()
    const usage = row.usage === null ? null : exactObject(row.usage, ['total_tokens'])
    if (row.usage !== null && !usage) fail()
    const redactedFields = row.redacted_fields.map((field) => {
      if (typeof field !== 'string' || !REDACTED.has(field)) fail()
      return field as 'error_code' | 'schema_path'
    })
    return {
      invocationId,
      requestId: text(row.request_id, 128), endpointVersionId: text(row.endpoint_version_id, 128), status: row.status,
      errorCode: row.error_code === null ? null : text(row.error_code, 512, true),
      schemaPath: row.schema_path === null ? null : text(row.schema_path, 512, true),
      latencyMs: nullableFinite(row.latency_ms), usage: usage ? { totalTokens: safeInteger(usage.total_tokens) } : null,
      toolNames: row.tool_names.map((name) => text(name, 128)), createdAt: finite(row.created_at),
      completedAt: nullableFinite(row.completed_at), redactedFields,
    }
  })
  return { items, nextCursor: root.next_cursor as string | null }
}

function endpointId(value: string): string {
  if (!IDENTIFIER.test(value)) fail()
  return value
}
function positiveInteger(value: number, maximum: number): string {
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) fail()
  return String(value)
}
async function request(route: string, signal: AbortSignal | undefined): Promise<unknown> {
  try { return await apiRequest(route as ApiRoute, { method: 'GET', signal }) }
  catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new OwnerObservabilityError(error instanceof ApiResponseError ? error.status : 0)
  }
}

export async function getOwnerMetrics(id: string, windowSeconds: number, signal?: AbortSignal): Promise<OwnerMetrics> {
  const route = `/api/published-endpoints/${endpointId(id)}/metrics?window_seconds=${positiveInteger(windowSeconds, 2592000)}`
  return parseOwnerMetrics(await request(route, signal))
}

export async function getOwnerDiagnostics(id: string, options: { windowSeconds: number; limit: number; cursor?: string }, signal?: AbortSignal): Promise<OwnerDiagnosticsPage> {
  const window = positiveInteger(options.windowSeconds, 2592000); const limit = positiveInteger(options.limit, 100)
  let route = `/api/published-endpoints/${endpointId(id)}/diagnostics?window_seconds=${window}&limit=${limit}`
  if (options.cursor !== undefined) {
    if (!boundedString(options.cursor, 1024)) fail()
    route += `&cursor=${encodeURIComponent(options.cursor)}`
  }
  return parseOwnerDiagnostics(await request(route, signal))
}
