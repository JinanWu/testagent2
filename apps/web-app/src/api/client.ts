const API_ROUTES = {
  session: '/api/auth/session',
  login: '/api/auth/login',
  logout: '/api/auth/logout',
} as const

export type ApiRoute = (typeof API_ROUTES)[keyof typeof API_ROUTES]
export { API_ROUTES }

const MAX_RESPONSE_BYTES = 4096
const MAX_STREAM_READS = MAX_RESPONSE_BYTES + 1
const MAX_REQUEST_BYTES = 1024
const JSON_CONTENT_TYPE = 'application/json'
const ROUTES = new Set<string>(Object.values(API_ROUTES))

function byteLength(value: string): number {
  return new TextEncoder().encode(value).byteLength
}

function contentLength(response: Response): number | null {
  const raw = response.headers.get('content-length')
  if (raw === null || !/^(0|[1-9]\d*)$/.test(raw)) {
    return null
  }
  const value = Number(raw)
  return Number.isSafeInteger(value) ? value : null
}

export class ApiResponseError extends Error {
  constructor(readonly status: number) {
    super('伺服器無法完成要求')
    this.name = 'ApiResponseError'
  }
}

export class ApiFormatError extends Error {
  constructor() {
    super('伺服器回應格式無效')
    this.name = 'ApiFormatError'
  }
}

function abortError(): DOMException {
  return new DOMException('要求已取消', 'AbortError')
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw abortError()
  }
}

async function readBoundedResponse(response: Response, signal?: AbortSignal): Promise<string> {
  let bytes: Uint8Array | null = null
  let text: string | null = null
  const body = response.body
  try {
    throwIfAborted(signal)
    if (body === null) {
      const length = contentLength(response)
      if (length === null || length > MAX_RESPONSE_BYTES) {
        throw new ApiFormatError()
      }
      throwIfAborted(signal)
      text = await response.text()
      throwIfAborted(signal)
      if (byteLength(text) !== length) {
        throw new ApiFormatError()
      }
      throwIfAborted(signal)
      return text
    }

    const reader = body.getReader()
    let cancelPromise: Promise<void> | null = null
    const cancel = () => {
      if (cancelPromise === null) {
        try {
          cancelPromise = reader.cancel().catch(() => undefined)
        } catch {
          cancelPromise = Promise.resolve()
        }
      }
      return cancelPromise
    }
    const cancelOnAbort = () => { void cancel() }
    signal?.addEventListener('abort', cancelOnAbort, { once: true })
    bytes = new Uint8Array(MAX_RESPONSE_BYTES)
    let count = 0
    let reads = 0
    try {
      while (true) {
        throwIfAborted(signal)
        if (++reads > MAX_STREAM_READS) {
          await cancel()
          throwIfAborted(signal)
          throw new ApiFormatError()
        }
        const result = await reader.read()
        throwIfAborted(signal)
        if (result.done) {
          break
        }
        if (!(result.value instanceof Uint8Array) || count + result.value.byteLength > MAX_RESPONSE_BYTES) {
          await cancel()
          throwIfAborted(signal)
          throw new ApiFormatError()
        }
        bytes.set(result.value, count)
        count += result.value.byteLength
      }
      throwIfAborted(signal)
      text = new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, count))
      throwIfAborted(signal)
      return text
    } finally {
      signal?.removeEventListener('abort', cancelOnAbort)
      reader.releaseLock()
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }
    if (error instanceof ApiFormatError) {
      throw error
    }
    throw new ApiFormatError()
  } finally {
    bytes = null
    text = null
  }
}

export interface ApiRequestOptions {
  method?: 'GET' | 'POST'
  body?: string
  csrfToken?: string
  signal?: AbortSignal
  expectedStatus?: number
}

export async function apiRequest(
  route: ApiRoute,
  options: ApiRequestOptions = {},
): Promise<unknown> {
  throwIfAborted(options.signal)
  const method = options.method ?? 'GET'
  if (!ROUTES.has(route) || (options.body !== undefined && byteLength(options.body) > MAX_REQUEST_BYTES)) {
    throw new ApiFormatError()
  }
  const headers: Record<string, string> = { Accept: JSON_CONTENT_TYPE }

  if (options.body !== undefined) {
    headers['Content-Type'] = JSON_CONTENT_TYPE
  }
  if (options.csrfToken !== undefined) {
    headers['X-CSRF-Token'] = options.csrfToken
  }

  let response: Response
  try {
    throwIfAborted(options.signal)
    response = await fetch(route, {
      method,
      credentials: 'include',
      headers,
      ...(options.body === undefined ? {} : { body: options.body }),
      ...(options.signal === undefined ? {} : { signal: options.signal }),
    })
    throwIfAborted(options.signal)
  } catch (error) {
    if (options.signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
      throw abortError()
    }
    throw new ApiResponseError(0)
  }

  throwIfAborted(options.signal)
  const expectedStatus = options.expectedStatus
  if (!response.ok || (expectedStatus !== undefined && response.status !== expectedStatus)) {
    throw new ApiResponseError(response.status)
  }
  throwIfAborted(options.signal)
  if (response.status === 204) {
    throwIfAborted(options.signal)
    return undefined
  }
  if (response.headers.get('content-type') !== JSON_CONTENT_TYPE) {
    throw new ApiFormatError()
  }

  throwIfAborted(options.signal)
  const text = await readBoundedResponse(response, options.signal)
  throwIfAborted(options.signal)
  if (text.length === 0) {
    throw new ApiFormatError()
  }
  let parsed: unknown
  try {
    throwIfAborted(options.signal)
    parsed = JSON.parse(text) as unknown
  } catch {
    throwIfAborted(options.signal)
    throw new ApiFormatError()
  }
  throwIfAborted(options.signal)
  return parsed
}
