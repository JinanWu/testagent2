import { StrictMode } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SessionProvider, useSession, type SessionContextValue } from '../app/SessionProvider'
import { createSendChatOperation } from '../app/sessionAuthority'

type Deferred<T> = {
  promise: Promise<T>
  resolve(value: T): void
  reject(reason: unknown): void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

function sessionResponse(username: string): Response {
  return new Response(JSON.stringify({
    user: { id: `${username}-id`, username, role: 'member' },
    csrf_token: `${username}-csrf`,
  }), { status: 200, headers: { 'content-type': 'application/json' } })
}

function anonymousResponse(): Response {
  return new Response('{}', { status: 401, headers: { 'content-type': 'application/json' } })
}

function pendingSessionPrefix(username: string) {
  let cancels = 0
  let pulls = 0
  let markBlocked!: () => void
  const blocked = new Promise<void>((resolve) => { markBlocked = resolve })
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(JSON.stringify({
        user: { id: `${username}-id`, username, role: 'member' },
        csrf_token: `${username}-csrf`,
      })))
    },
    pull() {
      pulls += 1
      markBlocked()
    },
    cancel() { cancels += 1 },
  }, { highWaterMark: 0 })
  return {
    response: new Response(stream, {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
    stream,
    blocked,
    cancels: () => cancels,
    pulls: () => pulls,
  }
}

function rejectWhenAborted(pending: Deferred<Response>, signal: AbortSignal): void {
  signal.addEventListener('abort', () => {
    pending.reject(new DOMException('aborted', 'AbortError'))
  }, { once: true })
}

describe('SessionProvider operation ordering', () => {
  const fetchMock = vi.fn<typeof fetch>()
  let current: SessionContextValue
  let renderer: ReactTestRenderer

  function Capture() {
    current = useSession()
    return null
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(async () => {
    if (renderer) {
      await act(async () => { renderer.unmount() })
    }
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('keeps a completed login newer than the deferred initial GET', async () => {
    const initial = deferred<Response>()
    const laterLogin = deferred<Response>()
    let initialSignal!: AbortSignal
    fetchMock.mockImplementationOnce((_input, init) => {
      initialSignal = init!.signal as AbortSignal
      rejectWhenAborted(initial, initialSignal)
      return initial.promise
    }).mockReturnValueOnce(laterLogin.promise)
    await act(async () => {
      renderer = create(<SessionProvider><Capture /></SessionProvider>)
    })

    let loginPromise!: Promise<void>
    await act(async () => {
      loginPromise = current.login('newer', 'secret')
    })
    expect(initialSignal.aborted).toBe(true)
    await act(async () => {
      laterLogin.resolve(sessionResponse('newer'))
      await loginPromise
    })
    expect(current.user?.username).toBe('newer')

    expect(current.user?.username).toBe('newer')
  })

  it('prevents an older login from overwriting a newer login', async () => {
    const older = deferred<Response>()
    const newer = deferred<Response>()
    let olderSignal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(anonymousResponse())
      .mockImplementationOnce((_input, init) => {
        olderSignal = init!.signal as AbortSignal
        rejectWhenAborted(older, olderSignal)
        return older.promise
      })
      .mockReturnValueOnce(newer.promise)
    await act(async () => {
      renderer = create(<SessionProvider><Capture /></SessionProvider>)
    })
    expect(current.status).toBe('anonymous')

    let olderPromise!: Promise<void>
    let olderResult!: Promise<unknown>
    let newerPromise!: Promise<void>
    await act(async () => {
      olderPromise = current.login('older', 'secret')
      olderResult = olderPromise.catch((error: unknown) => error)
      newerPromise = current.login('newer', 'secret')
    })
    expect(olderSignal.aborted).toBe(true)
    await act(async () => {
      newer.resolve(sessionResponse('newer'))
      await newerPromise
    })
    await act(async () => {
      await expect(olderResult).resolves.toMatchObject({ name: 'AbortError' })
    })
    expect(current.user?.username).toBe('newer')
  })

  it('rejects a valid-prefix login aborted by a newer provider operation', async () => {
    const older = pendingSessionPrefix('older')
    const authenticated = vi.fn()
    fetchMock
      .mockResolvedValueOnce(anonymousResponse())
      .mockResolvedValueOnce(older.response)
      .mockResolvedValueOnce(sessionResponse('newer'))
    await act(async () => {
      renderer = create(<SessionProvider><Capture /></SessionProvider>)
    })

    let olderPromise!: Promise<void>
    let observedCallback!: Promise<void>
    await act(async () => {
      olderPromise = current.login('older', 'secret')
      observedCallback = olderPromise.then(authenticated, () => undefined)
      await older.blocked
    })
    expect(older.pulls()).toBe(1)
    let newerPromise!: Promise<void>
    await act(async () => {
      newerPromise = current.login('newer', 'secret')
      await newerPromise
    })

    await expect(olderPromise).rejects.toMatchObject({ name: 'AbortError' })
    await observedCallback
    expect(authenticated).not.toHaveBeenCalled()
    expect(current.user?.username).toBe('newer')
    expect(older.cancels()).toBe(1)
    expect(older.stream.locked).toBe(false)
  })

  it('does not let a pending logout clear a later login', async () => {
    const pendingLogout = deferred<Response>()
    const laterLogin = deferred<Response>()
    let logoutSignal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(sessionResponse('first'))
      .mockImplementationOnce((_input, init) => {
        logoutSignal = init!.signal as AbortSignal
        rejectWhenAborted(pendingLogout, logoutSignal)
        return pendingLogout.promise
      })
      .mockReturnValueOnce(laterLogin.promise)
    await act(async () => {
      renderer = create(<SessionProvider><Capture /></SessionProvider>)
    })

    let logoutPromise!: Promise<void>
    let logoutResult!: Promise<unknown>
    await act(async () => {
      logoutPromise = current.logout()
      logoutResult = logoutPromise.catch((error: unknown) => error)
    })
    expect(current.status).toBe('authenticated')
    let loginPromise!: Promise<void>
    await act(async () => {
      loginPromise = current.login('later', 'secret')
      expect(logoutSignal.aborted).toBe(true)
      laterLogin.resolve(sessionResponse('later'))
      await loginPromise
    })
    await act(async () => {
      await expect(logoutResult).resolves.toMatchObject({ name: 'AbortError' })
    })
    expect(current.user?.username).toBe('later')
  })

  it('shares one StrictMode initial GET and ignores its post-unmount completion', async () => {
    const initial = deferred<Response>()
    let initialSignal!: AbortSignal
    fetchMock.mockImplementationOnce((_input, init) => {
      initialSignal = init!.signal as AbortSignal
      rejectWhenAborted(initial, initialSignal)
      return initial.promise
    })
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    await act(async () => {
      renderer = create(<StrictMode><SessionProvider><Capture /></SessionProvider></StrictMode>)
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(initialSignal.aborted).toBe(false)
    errorSpy.mockClear()
    await act(async () => { renderer.unmount() })
    await act(async () => { await Promise.resolve() })
    expect(initialSignal.aborted).toBe(true)
    expect(errorSpy).not.toHaveBeenCalled()
  })

  it('ignores a deferred login completion after unmount', async () => {
    const pendingLogin = deferred<Response>()
    let loginSignal!: AbortSignal
    fetchMock.mockResolvedValueOnce(anonymousResponse()).mockImplementationOnce((_input, init) => {
      loginSignal = init!.signal as AbortSignal
      rejectWhenAborted(pendingLogin, loginSignal)
      return pendingLogin.promise
    })
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    await act(async () => {
      renderer = create(<SessionProvider><Capture /></SessionProvider>)
    })
    let loginPromise!: Promise<void>
    let loginResult!: Promise<unknown>
    await act(async () => {
      loginPromise = current.login('late', 'secret')
      loginResult = loginPromise.catch((error: unknown) => error)
    })
    errorSpy.mockClear()
    await act(async () => { renderer.unmount() })
    await act(async () => {
      await Promise.resolve()
      await expect(loginResult).resolves.toMatchObject({ name: 'AbortError' })
    })
    expect(loginSignal.aborted).toBe(true)
    expect(errorSpy).not.toHaveBeenCalled()
  })

  it('erases mounted protected state before logout I/O and aborts the losing mutation preflight', async () => {
    const mutationPreflight = deferred<Response>()
    let mutationSignal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(sessionResponse('first'))
      .mockImplementationOnce((_input, init) => {
        mutationSignal = init!.signal as AbortSignal
        rejectWhenAborted(mutationPreflight, mutationSignal)
        return mutationPreflight.promise
      })
      .mockResolvedValueOnce(sessionResponse('first'))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    await act(async () => {
      renderer = create(<SessionProvider><Capture /></SessionProvider>)
    })
    const erase = vi.fn()
    const registration = current.registerProtectedStateOwner(erase)
    const mutation = current.runAuthorized({
      owner: registration.owner,
      operation: createSendChatOperation('secret draft', null),
    })
    const mutationResult = mutation.catch((error: unknown) => error)
    await Promise.resolve()

    let logoutPromise!: Promise<void>
    await act(async () => {
      logoutPromise = current.logout()
      expect(erase).toHaveBeenCalledOnce()
      expect(mutationSignal.aborted).toBe(true)
      await logoutPromise
    })
    await expect(mutationResult).resolves.toMatchObject({ name: 'AbortError' })
    expect(fetchMock.mock.calls.some(([route]) => route === '/api/chat')).toBe(false)
    registration.unregister()
  })
})
