import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { API_ROUTES, ApiFormatError, apiRequest } from '../api/client'
import {
  AUTH_ERROR_MESSAGE,
  AuthError,
  getSession,
  login,
  logout,
  parseAuthSession,
} from '../api/auth'

const sessionBody = {
  user: { id: 'user-1', username: 'alice', role: 'member' },
  csrf_token: 'csrf-secret',
}
const encoder = new TextEncoder()

function responseFromBytes(bytes: Uint8Array, contentType = 'application/json'): Response {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes)
      controller.close()
    },
  }), { status: 200, headers: { 'content-type': contentType } })
}

function pendingJsonResponse(value: unknown, rejectCancel = false) {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  let cancels = 0
  let pulls = 0
  let markBlocked!: () => void
  const blocked = new Promise<void>((resolve) => { markBlocked = resolve })
  const stream = new ReadableStream<Uint8Array>({
    start(next) {
      controller = next
      next.enqueue(encoder.encode(JSON.stringify(value)))
    },
    pull() {
      pulls += 1
      markBlocked()
    },
    cancel() {
      cancels += 1
      if (rejectCancel) return Promise.reject(new Error('hostile cancel rejection'))
    },
  }, { highWaterMark: 0 })
  return {
    response: new Response(stream, {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
    stream,
    blocked,
    close: () => controller.close(),
    cancels: () => cancels,
    pulls: () => pulls,
  }
}

describe('bounded auth transport', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('cancels an oversized split stream immediately without another read', async () => {
    let pulls = 0
    let cancelled = false
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulls += 1
        if (pulls === 1) controller.enqueue(new Uint8Array(3000))
        else if (pulls === 2) controller.enqueue(new Uint8Array(1097))
        else controller.enqueue(encoder.encode('{}'))
      },
      cancel() {
        cancelled = true
      },
    }, { highWaterMark: 0 })
    fetchMock.mockResolvedValueOnce(new Response(stream, {
      status: 200,
      headers: { 'content-type': 'application/json', 'content-length': '2' },
    }))

    await expect(apiRequest(API_ROUTES.session, { expectedStatus: 200 })).rejects.toBeInstanceOf(ApiFormatError)
    expect({ pulls, cancelled }).toEqual({ pulls: 2, cancelled: true })
  })

  it('passes the exact signal and cancels an active response reader once on abort', async () => {
    const controller = new AbortController()
    const cancel = vi.fn()
    const stream = new ReadableStream<Uint8Array>({
      pull() { return new Promise<void>(() => {}) },
      cancel,
    }, { highWaterMark: 0 })
    fetchMock.mockResolvedValueOnce(new Response(stream, {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))

    const request = apiRequest(API_ROUTES.session, {
      signal: controller.signal,
      expectedStatus: 200,
    })
    await Promise.resolve()
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal)
    controller.abort()

    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
    expect(cancel).toHaveBeenCalledOnce()
    expect(stream.locked).toBe(false)
  })

  it('never accepts complete valid JSON bytes when abort supplies the pending EOF', async () => {
    const pending = pendingJsonResponse(sessionBody)
    const controller = new AbortController()
    fetchMock.mockResolvedValueOnce(pending.response)

    const request = apiRequest(API_ROUTES.session, {
      signal: controller.signal,
      expectedStatus: 200,
    })
    await pending.blocked
    expect(pending.pulls()).toBe(1)
    controller.abort()

    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
    expect(pending.cancels()).toBe(1)
    expect(pending.stream.locked).toBe(false)

    const control = pendingJsonResponse(sessionBody)
    fetchMock.mockResolvedValueOnce(control.response)
    const successfulRequest = apiRequest(API_ROUTES.session, { expectedStatus: 200 })
    await control.blocked
    expect(control.pulls()).toBe(1)
    control.close()
    await expect(successfulRequest).resolves.toEqual(sessionBody)
    expect(control.cancels()).toBe(0)
    expect(control.stream.locked).toBe(false)
  })

  it('consumes a rejected cancel promise while still terminating with AbortError', async () => {
    const pending = pendingJsonResponse(sessionBody, true)
    const controller = new AbortController()
    const unhandled: unknown[] = []
    const processEvents = (globalThis as unknown as { process: {
      on(event: 'unhandledRejection', listener: (reason: unknown) => void): void
      off(event: 'unhandledRejection', listener: (reason: unknown) => void): void
    } }).process
    const observe = (reason: unknown) => { unhandled.push(reason) }
    processEvents.on('unhandledRejection', observe)
    fetchMock.mockResolvedValueOnce(pending.response)

    try {
      const request = apiRequest(API_ROUTES.session, {
        signal: controller.signal,
        expectedStatus: 200,
      })
      await pending.blocked
      expect(pending.pulls()).toBe(1)
      controller.abort()
      await expect(request).rejects.toMatchObject({ name: 'AbortError' })
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(unhandled).toEqual([])
      expect(pending.cancels()).toBe(1)
      expect(pending.stream.locked).toBe(false)
    } finally {
      processEvents.off('unhandledRejection', observe)
    }
  })

  it('forwards the exact internal signal through every auth request', async () => {
    const signal = new AbortController().signal
    fetchMock
      .mockResolvedValueOnce(responseFromBytes(encoder.encode(JSON.stringify(sessionBody))))
      .mockResolvedValueOnce(responseFromBytes(encoder.encode(JSON.stringify(sessionBody))))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await getSession(signal)
    await login('alice', 'secret', signal)
    await logout('csrf-secret', signal)

    expect(fetchMock.mock.calls).toHaveLength(3)
    for (const call of fetchMock.mock.calls) {
      expect(call[1]?.signal).toBe(signal)
    }
  })

  it('accepts the exact 4096-byte streamed boundary', async () => {
    const body = `{"ok":true}${' '.repeat(4085)}`
    expect(encoder.encode(body)).toHaveLength(4096)
    const bytes = encoder.encode(body)
    fetchMock.mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.subarray(0, 2048))
        controller.enqueue(bytes.subarray(2048))
        controller.close()
      },
    }), { status: 200, headers: { 'content-type': 'application/json' } }))

    await expect(apiRequest(API_ROUTES.session, { expectedStatus: 200 })).resolves.toEqual({ ok: true })
  })

  it.each([
    ['malformed UTF-8', responseFromBytes(new Uint8Array([0xc3, 0x28]))],
    ['malformed JSON', responseFromBytes(encoder.encode('{"x":'))],
    ['non-exact content type', responseFromBytes(encoder.encode('{}'), 'application/json; charset=utf-8')],
  ])('normalizes %s to the fixed format error', async (_name, response) => {
    fetchMock.mockResolvedValueOnce(response)
    await expect(apiRequest(API_ROUTES.session, { expectedStatus: 200 })).rejects.toMatchObject({
      name: 'ApiFormatError',
      message: '伺服器回應格式無效',
    })
  })

  it('only permits body-null fallback with an exact bounded content length', async () => {
    const unknownText = vi.fn(async () => '{}')
    fetchMock.mockResolvedValueOnce({
      ok: true, status: 200, body: null,
      headers: new Headers({ 'content-type': 'application/json' }),
      text: unknownText,
    } as unknown as Response)
    await expect(apiRequest(API_ROUTES.session, { expectedStatus: 200 })).rejects.toBeInstanceOf(ApiFormatError)
    expect(unknownText).not.toHaveBeenCalled()

    fetchMock.mockResolvedValueOnce({
      ok: true, status: 200, body: null,
      headers: new Headers({ 'content-type': 'application/json', 'content-length': '2' }),
      text: async () => '{}',
    } as unknown as Response)
    await expect(apiRequest(API_ROUTES.session, { expectedStatus: 200 })).resolves.toEqual({})

    fetchMock.mockResolvedValueOnce({
      ok: true, status: 200, body: null,
      headers: new Headers({ 'content-type': 'application/json', 'content-length': '1' }),
      text: async () => '{}',
    } as unknown as Response)
    await expect(apiRequest(API_ROUTES.session, { expectedStatus: 200 })).rejects.toBeInstanceOf(ApiFormatError)
  })

  it.each([201, 202, 206])('requires exact 200 session and login status, not %s', async (status) => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(sessionBody), {
      status, headers: { 'content-type': 'application/json' },
    }))
    await expect(getSession()).rejects.toMatchObject({ message: AUTH_ERROR_MESSAGE })
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(sessionBody), {
      status, headers: { 'content-type': 'application/json' },
    }))
    await expect(login('alice', 'secret')).rejects.toMatchObject({ message: AUTH_ERROR_MESSAGE })
  })

  it.each([200, 201, 202, 206])('requires exact 204 logout status, not %s', async (status) => {
    fetchMock.mockResolvedValueOnce(new Response(status === 200 ? '{}' : null, {
      status, headers: status === 200 ? { 'content-type': 'application/json' } : undefined,
    }))
    await expect(logout('csrf-secret')).rejects.toMatchObject({ message: AUTH_ERROR_MESSAGE })
  })
})

describe('strict auth session parser', () => {
  it('rejects accessors without invoking them', () => {
    let calls = 0
    const hostile = Object.defineProperties({}, {
      user: { enumerable: true, configurable: true, get() { calls += 1; return sessionBody.user } },
      csrf_token: { enumerable: true, configurable: true, writable: true, value: 'csrf-secret' },
    })
    expect(() => parseAuthSession(hostile)).toThrow(AuthError)
    expect(calls).toBe(0)
  })

  it('rejects custom prototypes, extra and symbol keys', () => {
    expect(() => parseAuthSession(Object.assign(Object.create(null), sessionBody))).toThrow(AuthError)
    expect(() => parseAuthSession(Object.assign(Object.create({}), sessionBody))).toThrow(AuthError)
    expect(() => parseAuthSession({ ...sessionBody, extra: true })).toThrow(AuthError)
    expect(() => parseAuthSession({ ...sessionBody, [Symbol('extra')]: true })).toThrow(AuthError)
    expect(() => parseAuthSession({ ...sessionBody, user: { ...sessionBody.user, [Symbol('extra')]: true } })).toThrow(AuthError)
  })

  it('does not access cookies or browser storage', () => {
    let browserAccesses = 0
    const trap = { get() { browserAccesses += 1 }, set() { browserAccesses += 1; return true } }
    vi.stubGlobal('document', new Proxy({}, trap))
    vi.stubGlobal('localStorage', new Proxy({}, trap))
    vi.stubGlobal('sessionStorage', new Proxy({}, trap))
    expect(parseAuthSession(sessionBody)).toEqual({ user: sessionBody.user, csrfToken: 'csrf-secret' })
    expect(browserAccesses).toBe(0)
    vi.unstubAllGlobals()
  })
})
