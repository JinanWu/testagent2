import {
  ApiFormatError,
  boundedString,
  exactObject,
} from './client'

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const ERROR_CODE = /^[a-z][a-z0-9_.-]{0,127}$/
const CURSOR = /^[A-Za-z0-9_.-]{1,4096}$/
const SHA256 = /^[a-f0-9]{64}$/
const STATUS = new Set(['pending', 'running', 'succeeded', 'failed', 'rate_limited', 'invalid_api_key'])
const MAX_DETAIL_BYTES = 1024 * 1024
const MAX_DETAIL_NODES = 4096
const MAX_DETAIL_DEPTH = 128
const MAX_CHILD_ROWS = 4096
const MAX_LIST_RESPONSE_BYTES = 256 * 1024
const MAX_DETAIL_RESPONSE_BYTES = 7 * 1024 * 1024
const MAX_EMPTY_STREAM_READS = 4096
const FORBIDDEN_KEYS = new Set([
  'authorization', 'proxyauthorization', 'cookie', 'setcookie', 'apikey', 'credentialsecret',
  'credentialciphertext', 'credentialhash', 'masterkey', 'providersecret', 'clientsecret',
  'secretkey', 'privatekey', 'password', 'accesstoken', 'refreshtoken',
])
const FORBIDDEN_SUFFIXES = [
  'authorization', 'cookie', 'apikey', 'credentialsecret', 'credentialciphertext',
  'credentialhash', 'providersecret', 'privatekey', 'secretkey', 'masterkey',
  'clientsecret', 'accesstoken', 'refreshtoken',
]
const PATH_KEYS = new Set(['path', 'filepath', 'filesystempath', 'absolutepath'])
const ABSOLUTE_PATH = /(?:^|[\s:="'])(?:~[/\\]|\/(?:Users|home|etc|var|tmp|private|opt|usr|root|proc|sys|dev|srv)\/|[A-Za-z]:[\\/]|\\\\)/i
const API_KEY = /(?:^|[^A-Za-z0-9_-])pk_[A-Za-z0-9_-]{43}(?:$|[^A-Za-z0-9_-])/
const CREDENTIAL_SHAPE = /(?:^|[^A-Za-z0-9_-])(?:[A-Za-z0-9]{2,16}[_-][A-Za-z0-9_-]{32,}|[A-Za-z0-9]{2,4}[_-][A-Za-z0-9]{2,16}[_-][A-Za-z0-9_-]{16,})(?:$|[^A-Za-z0-9_-])/

export const LOGS_ERROR_MESSAGE = '目前無法載入完整呼叫紀錄，請稍後再試。'
export const LOGS_NOT_FOUND_MESSAGE = '找不到呼叫紀錄。'
export const LOGS_FORBIDDEN_MESSAGE = '只有管理者可查看完整呼叫紀錄。'

export interface InvocationListItem {
  invocationId: string
  endpointId: string
  endpointVersionId: string
  requestId: string
  status: string
  errorCode: string | null
  latencyMs: number | null
  createdAt: number
  completedAt: number | null
  hasRedaction: boolean
}

export interface InvocationListPage {
  items: InvocationListItem[]
  nextCursor: string | null
}

export interface InvocationIdentity {
  id: string
  requestId: string
  sessionId: string | null
}

export interface InvocationRunEvent {
  id: string
  sequenceNumber: number
  eventType: string
  payload: JsonValue
  createdAt: number
}

export interface InvocationToolCall {
  id: string
  runEventId: string | null
  sequenceNumber: number
  toolName: string
  arguments: JsonValue
  outcome: string
  result: JsonValue
  error: JsonValue
  latencyMs: number | null
  retryOfToolCallId: string | null
  createdAt: number
}

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }

export interface AdminInvocationDetail {
  invocation: InvocationIdentity
  endpointId: string
  endpointVersionId: string
  credentialId: string | null
  messageId: string | null
  status: string
  input: JsonValue
  metadata: { [key: string]: JsonValue } | null
  output: JsonValue
  error: JsonValue
  usage: JsonValue
  metadataSizeBytes: number | null
  metadataSha256: string | null
  latencyMs: number | null
  pricingVersion: string | null
  createdAt: number
  completedAt: number | null
  runEvents: InvocationRunEvent[]
  toolCalls: InvocationToolCall[]
}

export interface InvocationListFilters {
  fromAt?: number
  toAt?: number
  status?: string
  errorCode?: string
  limit?: number
  cursor?: string
}

export class LogsError extends Error {
  constructor(readonly status: number) {
    super(status === 404 ? LOGS_NOT_FOUND_MESSAGE : status === 403 ? LOGS_FORBIDDEN_MESSAGE : LOGS_ERROR_MESSAGE)
    this.name = 'LogsError'
  }
}

function normalized(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, '')
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && IDENTIFIER.test(value)
}

function finiteNonNegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function nullableFinite(value: unknown): value is number | null {
  return value === null || finiteNonNegative(value)
}

function nullableIdentifier(value: unknown): value is string | null {
  return value === null || identifier(value)
}

function cloneSafeJson(value: unknown, scanSecrets = true): JsonValue {
  const work: Array<{ value: unknown; depth: number }> = [{ value, depth: 1 }]
  let nodes = 0
  while (work.length > 0) {
    const current = work.pop()!
    nodes += 1
    if (nodes > MAX_DETAIL_NODES || current.depth > MAX_DETAIL_DEPTH) throw new ApiFormatError()
    if (current.value === null || typeof current.value === 'boolean') continue
    if (typeof current.value === 'number') {
      if (!Number.isFinite(current.value)) throw new ApiFormatError()
      continue
    }
    if (typeof current.value === 'string') {
      const marker = normalized(current.value)
      if (scanSecrets && (API_KEY.test(current.value) || CREDENTIAL_SHAPE.test(current.value) || ABSOLUTE_PATH.test(current.value) ||
          [...FORBIDDEN_KEYS].some((item) => marker.includes(item)))) throw new ApiFormatError()
      continue
    }
    if (Array.isArray(current.value)) {
      for (const child of current.value) work.push({ value: child, depth: current.depth + 1 })
      continue
    }
    const object = exactObject(current.value, Object.keys(current.value as object))
    if (object === null) throw new ApiFormatError()
    for (const [key, child] of Object.entries(object)) {
      const marker = normalized(key)
      if (scanSecrets && (FORBIDDEN_KEYS.has(marker) ||
          FORBIDDEN_SUFFIXES.some((suffix) => marker.endsWith(suffix)) ||
          (PATH_KEYS.has(marker) && typeof child === 'string' && ABSOLUTE_PATH.test(child)))) {
        throw new ApiFormatError()
      }
      work.push({ value: child, depth: current.depth + 1 })
    }
  }
  let encoded: string
  try {
    encoded = JSON.stringify(value)
  } catch {
    throw new ApiFormatError()
  }
  if (new TextEncoder().encode(encoded).byteLength > MAX_DETAIL_BYTES) throw new ApiFormatError()
  return JSON.parse(encoded) as JsonValue
}

export function parseInvocationList(value: unknown): InvocationListPage {
  const page = exactObject(value, ['items', 'next_cursor'])
  if (page === null || !Array.isArray(page.items) || page.items.length > 100 ||
      !(page.next_cursor === null || (typeof page.next_cursor === 'string' && CURSOR.test(page.next_cursor)))) {
    throw new ApiFormatError()
  }
  const items = page.items.map((raw): InvocationListItem => {
    const item = exactObject(raw, [
      'invocation_id', 'endpoint_id', 'endpoint_version_id', 'request_id', 'status', 'error_code',
      'latency_ms', 'created_at', 'completed_at', 'has_redaction',
    ])
    if (item === null || !identifier(item.invocation_id) || !identifier(item.endpoint_id) ||
        !identifier(item.endpoint_version_id) || !identifier(item.request_id) ||
        typeof item.status !== 'string' || !STATUS.has(item.status) ||
        !(item.error_code === null || (typeof item.error_code === 'string' && ERROR_CODE.test(item.error_code))) ||
        !nullableFinite(item.latency_ms) || !finiteNonNegative(item.created_at) ||
        !nullableFinite(item.completed_at) ||
        (item.completed_at !== null && item.completed_at < item.created_at) ||
        typeof item.has_redaction !== 'boolean') throw new ApiFormatError()
    return {
      invocationId: item.invocation_id,
      endpointId: item.endpoint_id,
      endpointVersionId: item.endpoint_version_id,
      requestId: item.request_id,
      status: item.status,
      errorCode: item.error_code,
      latencyMs: item.latency_ms,
      createdAt: item.created_at,
      completedAt: item.completed_at,
      hasRedaction: item.has_redaction,
    }
  })
  return { items, nextCursor: page.next_cursor }
}

export function parseInvocationDetail(value: unknown): AdminInvocationDetail {
  const detail = exactObject(value, [
    'invocation', 'endpoint_id', 'endpoint_version_id', 'credential_id', 'message_id', 'status',
    'input', 'metadata', 'output', 'error', 'usage', 'metadata_size_bytes', 'metadata_sha256',
    'latency_ms', 'pricing_version', 'created_at', 'completed_at', 'run_events', 'tool_calls',
  ])
  const invocation = detail === null ? null : exactObject(detail.invocation, ['id', 'request_id', 'session_id'])
  if (detail === null || invocation === null || !identifier(invocation.id) || !identifier(invocation.request_id) ||
      !nullableIdentifier(invocation.session_id) || !identifier(detail.endpoint_id) ||
      !identifier(detail.endpoint_version_id) || !nullableIdentifier(detail.credential_id) ||
      !nullableIdentifier(detail.message_id) || typeof detail.status !== 'string' || !STATUS.has(detail.status) ||
      !(detail.metadata === null || (typeof detail.metadata === 'object' && detail.metadata !== null && !Array.isArray(detail.metadata))) ||
      !(detail.metadata_size_bytes === null ||
        (Number.isInteger(detail.metadata_size_bytes) && finiteNonNegative(detail.metadata_size_bytes))) ||
      !(detail.metadata_sha256 === null || (typeof detail.metadata_sha256 === 'string' && SHA256.test(detail.metadata_sha256))) ||
      !nullableFinite(detail.latency_ms) || !(detail.pricing_version === null || boundedString(detail.pricing_version, 256)) ||
      !finiteNonNegative(detail.created_at) || !nullableFinite(detail.completed_at) ||
      !Array.isArray(detail.run_events) || detail.run_events.length > MAX_CHILD_ROWS ||
      !Array.isArray(detail.tool_calls) || detail.tool_calls.length > MAX_CHILD_ROWS) throw new ApiFormatError()

  const safe = cloneSafeJson(value, false) as Record<string, JsonValue>
  for (const raw of [safe.input, safe.metadata, safe.output, safe.error, safe.usage]) cloneSafeJson(raw)
  const safeInvocation = safe.invocation as Record<string, JsonValue>
  const events = (safe.run_events as JsonValue[]).map((raw): InvocationRunEvent => {
    const event = exactObject(raw, ['id', 'sequence_number', 'event_type', 'payload', 'created_at'])
    if (event === null || !identifier(event.id) || !Number.isInteger(event.sequence_number) ||
        !finiteNonNegative(event.sequence_number) || !boundedString(event.event_type, 256) ||
        !finiteNonNegative(event.created_at)) throw new ApiFormatError()
    cloneSafeJson(event.payload)
    return { id: event.id, sequenceNumber: event.sequence_number, eventType: event.event_type,
      payload: event.payload as JsonValue, createdAt: event.created_at }
  })
  const tools = (safe.tool_calls as JsonValue[]).map((raw): InvocationToolCall => {
    const tool = exactObject(raw, [
      'id', 'run_event_id', 'sequence_number', 'tool_name', 'arguments', 'outcome', 'result', 'error',
      'latency_ms', 'retry_of_tool_call_id', 'created_at',
    ])
    if (tool === null || !identifier(tool.id) || !nullableIdentifier(tool.run_event_id) ||
        !Number.isInteger(tool.sequence_number) || !finiteNonNegative(tool.sequence_number) ||
        !boundedString(tool.tool_name, 256) || !boundedString(tool.outcome, 256) ||
        !nullableFinite(tool.latency_ms) || !nullableIdentifier(tool.retry_of_tool_call_id) ||
        !finiteNonNegative(tool.created_at)) throw new ApiFormatError()
    for (const raw of [tool.arguments, tool.result, tool.error]) cloneSafeJson(raw)
    return {
      id: tool.id, runEventId: tool.run_event_id, sequenceNumber: tool.sequence_number,
      toolName: tool.tool_name, arguments: tool.arguments as JsonValue, outcome: tool.outcome,
      result: tool.result as JsonValue, error: tool.error as JsonValue, latencyMs: tool.latency_ms,
      retryOfToolCallId: tool.retry_of_tool_call_id, createdAt: tool.created_at,
    }
  })
  return {
    invocation: { id: safeInvocation.id as string, requestId: safeInvocation.request_id as string,
      sessionId: safeInvocation.session_id as string | null },
    endpointId: safe.endpoint_id as string,
    endpointVersionId: safe.endpoint_version_id as string,
    credentialId: safe.credential_id as string | null,
    messageId: safe.message_id as string | null,
    status: safe.status as string,
    input: safe.input,
    metadata: safe.metadata as { [key: string]: JsonValue } | null,
    output: safe.output,
    error: safe.error,
    usage: safe.usage,
    metadataSizeBytes: safe.metadata_size_bytes as number | null,
    metadataSha256: safe.metadata_sha256 as string | null,
    latencyMs: safe.latency_ms as number | null,
    pricingVersion: safe.pricing_version as string | null,
    createdAt: safe.created_at as number,
    completedAt: safe.completed_at as number | null,
    runEvents: events,
    toolCalls: tools,
  }
}

function encodedId(value: string): string {
  if (!identifier(value)) throw new ApiFormatError()
  return encodeURIComponent(value)
}

function listRoute(endpointId: string, filters: InvocationListFilters): string {
  const params = new URLSearchParams()
  if (filters.fromAt !== undefined) {
    if (!finiteNonNegative(filters.fromAt)) throw new ApiFormatError()
    params.set('from_at', String(filters.fromAt))
  }
  if (filters.toAt !== undefined) {
    if (!finiteNonNegative(filters.toAt)) throw new ApiFormatError()
    params.set('to_at', String(filters.toAt))
  }
  if (filters.status !== undefined) {
    if (!STATUS.has(filters.status)) throw new ApiFormatError()
    params.set('status', filters.status)
  }
  if (filters.errorCode !== undefined) {
    if (!ERROR_CODE.test(filters.errorCode)) throw new ApiFormatError()
    params.set('error_code', filters.errorCode)
  }
  if (filters.limit !== undefined) {
    if (!Number.isInteger(filters.limit) || filters.limit < 1 || filters.limit > 100) throw new ApiFormatError()
    params.set('limit', String(filters.limit))
  }
  if (filters.cursor !== undefined) {
    if (!CURSOR.test(filters.cursor)) throw new ApiFormatError()
    params.set('cursor', filters.cursor)
  }
  const query = params.toString()
  return `/api/admin/endpoints/${encodedId(endpointId)}/invocations${query ? `?${query}` : ''}`
}

function abortError(): DOMException {
  return new DOMException('要求已取消', 'AbortError')
}

async function readBoundedJson(response: Response, limit: number, signal?: AbortSignal): Promise<unknown> {
  const body = response.body
  if (body === null) throw new ApiFormatError()
  const reader = body.getReader()
  let bytes = new Uint8Array(Math.min(64 * 1024, limit))
  let count = 0
  let emptyReads = 0
  const cancel = () => reader.cancel().catch(() => undefined)
  const cancelOnAbort = () => { void cancel() }
  signal?.addEventListener('abort', cancelOnAbort, { once: true })
  try {
    while (true) {
      if (signal?.aborted) throw abortError()
      const result = await reader.read()
      if (signal?.aborted) throw abortError()
      if (result.done) break
      if (!(result.value instanceof Uint8Array)) throw new ApiFormatError()
      if (result.value.byteLength === 0) {
        if (++emptyReads > MAX_EMPTY_STREAM_READS) throw new ApiFormatError()
        continue
      }
      const nextCount = count + result.value.byteLength
      if (nextCount > limit) throw new ApiFormatError()
      if (nextCount > bytes.byteLength) {
        let capacity = bytes.byteLength
        while (capacity < nextCount) capacity = Math.min(limit, Math.max(nextCount, capacity * 2))
        const grown = new Uint8Array(capacity)
        grown.set(bytes.subarray(0, count))
        bytes = grown
      }
      bytes.set(result.value, count)
      count = nextCount
    }
    if (count === 0) throw new ApiFormatError()
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, count))
    return JSON.parse(text) as unknown
  } catch (error) {
    await cancel()
    if (signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) throw abortError()
    throw new ApiFormatError()
  } finally {
    signal?.removeEventListener('abort', cancelOnAbort)
    reader.releaseLock()
    bytes = new Uint8Array(0)
  }
}

async function logsRequest(route: string, limit: number, signal?: AbortSignal): Promise<unknown> {
  if (signal?.aborted) throw abortError()
  let response: Response
  try {
    response = await fetch(route, {
      method: 'GET', credentials: 'include', headers: { Accept: 'application/json' },
      ...(signal === undefined ? {} : { signal }),
    })
  } catch (error) {
    if (signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) throw abortError()
    throw new LogsError(0)
  }
  if (signal?.aborted) throw abortError()
  if (!response.ok) {
    void response.body?.cancel().catch(() => undefined)
    throw new LogsError(response.status)
  }
  if (response.status !== 200 || response.headers.get('content-type') !== 'application/json') {
    void response.body?.cancel().catch(() => undefined)
    throw new LogsError(0)
  }
  try {
    return await readBoundedJson(response, limit, signal)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new LogsError(0)
  }
}

function mapError(error: unknown): never {
  if (error instanceof DOMException && error.name === 'AbortError') throw error
  if (error instanceof LogsError) throw error
  throw new LogsError(0)
}

export async function listInvocations(
  endpointId: string,
  filters: InvocationListFilters = {},
  signal?: AbortSignal,
): Promise<InvocationListPage> {
  try {
    return parseInvocationList(await logsRequest(listRoute(endpointId, filters), MAX_LIST_RESPONSE_BYTES, signal))
  } catch (error) {
    return mapError(error)
  }
}

export async function getInvocationDetail(
  endpointId: string,
  invocationId: string,
  signal?: AbortSignal,
): Promise<AdminInvocationDetail> {
  try {
    const route = `/api/admin/endpoints/${encodedId(endpointId)}/invocations/${encodedId(invocationId)}`
    return parseInvocationDetail(await logsRequest(route, MAX_DETAIL_RESPONSE_BYTES, signal))
  } catch (error) {
    return mapError(error)
  }
}
