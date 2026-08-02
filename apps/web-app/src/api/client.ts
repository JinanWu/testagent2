const API_ROUTES = {
  session: '/api/auth/session',
  login: '/api/auth/login',
  logout: '/api/auth/logout',
  chat: '/api/chat',
  sessions: '/api/sessions',
  skills: '/api/skills',
} as const

export type ApiRoute = string & { readonly __apiRoute?: never }
export { API_ROUTES }

const MAX_RESPONSE_BYTES = 4096
const MAX_LARGE_RESPONSE_BYTES = 1024 * 1024
const MAX_STREAM_READS = 4096
const MAX_REQUEST_BYTES = 1024
const MAX_CHAT_REQUEST_BYTES = 100 * 1024
const JSON_CONTENT_TYPE = 'application/json'
const ROUTES = new Set<string>(Object.values(API_ROUTES))

function isAllowedRoute(route: string): boolean {
  return ROUTES.has(route) ||
    /^\/api\/sessions\?limit=(?:[1-9]|[1-4]\d|50)$/.test(route) ||
    /^\/api\/(?:sessions|skills)\/(?:[A-Za-z0-9_.!~*'()-]|%[0-9A-F]{2}){1,384}$/.test(route)
}

function responseLimit(route: string): number {
  return route === API_ROUTES.chat || route.startsWith('/api/sessions') || route.startsWith('/api/skills')
    ? MAX_LARGE_RESPONSE_BYTES : MAX_RESPONSE_BYTES
}

export function byteLength(value: string): number {
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

async function readBoundedResponse(response: Response, limit: number, signal?: AbortSignal): Promise<string> {
  let bytes: Uint8Array | null = null
  let text: string | null = null
  const body = response.body
  try {
    throwIfAborted(signal)
    if (body === null) {
      const length = contentLength(response)
      if (length === null || length > limit) {
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
    bytes = new Uint8Array(limit)
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
        if (!(result.value instanceof Uint8Array) || count + result.value.byteLength > limit) {
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
  const requestLimit = route === API_ROUTES.chat ? MAX_CHAT_REQUEST_BYTES : MAX_REQUEST_BYTES
  if (!isAllowedRoute(route) || (options.body !== undefined && byteLength(options.body) > requestLimit)) {
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
  const text = await readBoundedResponse(response, responseLimit(route), options.signal)
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

export function exactObject(value: unknown, keys: readonly string[]): Record<string, unknown> | null {
  try {
    if (typeof value !== 'object' || value === null || Object.getPrototypeOf(value) !== Object.prototype) return null
    const descriptors = Object.getOwnPropertyDescriptors(value)
    if (Reflect.ownKeys(descriptors).length !== keys.length || !keys.every((key) => key in descriptors)) return null
    const result: Record<string, unknown> = {}
    for (const key of keys) {
      const descriptor = descriptors[key]
      if (!descriptor || !('value' in descriptor) || !descriptor.enumerable ||
          !descriptor.configurable || !descriptor.writable) return null
      result[key] = descriptor.value
    }
    return result
  } catch {
    return null
  }
}

export function boundedString(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum
}

export function encodedRoute(prefix: '/api/sessions/' | '/api/skills/', id: string): ApiRoute {
  if (!boundedString(id, 128)) throw new ApiFormatError()
  const encoded = encodeURIComponent(id)
  if (encoded.length > 384) throw new ApiFormatError()
  return `${prefix}${encoded}`
}
