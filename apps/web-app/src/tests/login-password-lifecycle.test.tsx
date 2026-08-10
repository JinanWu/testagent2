import type { ReactNode } from 'react'
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CREDENTIALS_ERROR_MESSAGE } from '../api/auth'
import { SessionProvider, useSession } from '../app/SessionProvider'
import LoginPage from '../pages/LoginPage'

function UnmountLoginWhenAuthenticated({ children }: { children: ReactNode }) {
  const { status } = useSession()
  if (status === 'authenticated') {
    return null
  }
  return children
}

type Deferred<T> = { promise: Promise<T>; resolve(value: T): void; reject(reason: unknown): void }

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const successfulSession = {
  user: { id: 'user-1', username: 'alice', role: 'member' },
  csrf_token: 'csrf-secret',
}

function passwordInput(root: ReactTestInstance): ReactTestInstance {
  return root.findAllByType('input').find((input) => input.props.name === 'password')!
}

async function enterAndSubmit(root: ReactTestInstance, password: string): Promise<void> {
  const inputs = root.findAllByType('input')
  await act(async () => {
    inputs[0].props.onChange({ currentTarget: { value: 'alice' } })
    inputs[1].props.onChange({ currentTarget: { value: password } })
  })
  await act(async () => {
    await root.findByType('form').props.onSubmit({ preventDefault: vi.fn() })
  })
}

describe('LoginPage password lifecycle', () => {
  const fetchMock = vi.fn<typeof fetch>()
  let renderer: ReactTestRenderer

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

  it('clears the controlled password after a failed attempt without leaking it', async () => {
    const marker = 'PASSWORD-MARKER-FAILURE'
    fetchMock
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: marker }, 401))
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    await act(async () => {
      renderer = create(<SessionProvider><LoginPage /></SessionProvider>)
    })
    errorSpy.mockClear()

    await enterAndSubmit(renderer.root, marker)

    expect(passwordInput(renderer.root).props.value).toBe('')
    expect(renderer.root.findByProps({ role: 'alert' }).children).toEqual([CREDENTIALS_ERROR_MESSAGE])
    expect(JSON.stringify(renderer.toJSON())).not.toContain(marker)
    expect(JSON.stringify(errorSpy.mock.calls)).not.toContain(marker)
  })

  it('clears the controlled password after success without requiring unmount', async () => {
    const marker = 'PASSWORD-MARKER-SUCCESS'
    const authenticated = vi.fn()
    fetchMock
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse(successfulSession))
    await act(async () => {
      renderer = create(
        <SessionProvider><LoginPage onAuthenticated={authenticated} /></SessionProvider>,
      )
    })

    await enterAndSubmit(renderer.root, marker)

    expect(authenticated).toHaveBeenCalledOnce()
    expect(passwordInput(renderer.root).props.value).toBe('')
    expect(JSON.stringify(renderer.toJSON())).not.toContain(marker)
  })

  it('clears the password even when the authentication callback throws', async () => {
    const marker = 'PASSWORD-MARKER-CONTROL'
    fetchMock
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse(successfulSession))
    await act(async () => {
      renderer = create(
        <SessionProvider><LoginPage onAuthenticated={() => {
          expect(passwordInput(renderer.root).props.value).toBe('')
          throw new Error('route failed')
        }} /></SessionProvider>,
      )
    })

    await enterAndSubmit(renderer.root, marker)

    expect(passwordInput(renderer.root).props.value).toBe('')
    expect(JSON.stringify(renderer.toJSON())).not.toContain(marker)
  })

  it('still calls onAuthenticated when auth status unmounts the page', async () => {
    const marker = 'PASSWORD-MARKER-UNMOUNT-RACE'
    const authenticated = vi.fn()
    fetchMock
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse(successfulSession))
    await act(async () => {
      renderer = create(
        <SessionProvider>
          <UnmountLoginWhenAuthenticated>
            <LoginPage onAuthenticated={authenticated} />
          </UnmountLoginWhenAuthenticated>
        </SessionProvider>,
      )
    })

    await enterAndSubmit(renderer.root, marker)

    expect(authenticated).toHaveBeenCalledOnce()
  })

  it('does not update state when success synchronously unmounts the page', async () => {
    const pending = deferred<Response>()
    const authenticated = vi.fn(() => {
      expect(passwordInput(renderer.root).props.value).toBe('')
      renderer.unmount()
    })
    fetchMock.mockResolvedValueOnce(jsonResponse({}, 401)).mockReturnValueOnce(pending.promise)
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    await act(async () => {
      renderer = create(
        <SessionProvider><LoginPage onAuthenticated={authenticated} /></SessionProvider>,
      )
    })
    const form = renderer.root.findByType('form')
    const inputs = renderer.root.findAllByType('input')
    let submitPromise!: Promise<void>
    await act(async () => {
      inputs[0].props.onChange({ currentTarget: { value: 'alice' } })
      inputs[1].props.onChange({ currentTarget: { value: 'secret' } })
    })
    await act(async () => {
      submitPromise = form.props.onSubmit({ preventDefault: vi.fn() })
    })
    errorSpy.mockClear()

    await act(async () => {
      pending.resolve(jsonResponse(successfulSession))
      await submitPromise
    })

    expect(authenticated).toHaveBeenCalledOnce()
    expect(errorSpy).not.toHaveBeenCalled()
  })

  it('does not update state or call back after deferred completion following unmount', async () => {
    const pending = deferred<Response>()
    const authenticated = vi.fn()
    let loginSignal!: AbortSignal
    fetchMock.mockResolvedValueOnce(jsonResponse({}, 401)).mockImplementationOnce((_input, init) => {
      loginSignal = init!.signal as AbortSignal
      loginSignal.addEventListener('abort', () => {
        pending.reject(new DOMException('aborted', 'AbortError'))
      }, { once: true })
      return pending.promise
    })
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    await act(async () => {
      renderer = create(
        <SessionProvider><LoginPage onAuthenticated={authenticated} /></SessionProvider>,
      )
    })
    const form = renderer.root.findByType('form')
    const inputs = renderer.root.findAllByType('input')
    let submitPromise!: Promise<void>
    await act(async () => {
      inputs[0].props.onChange({ currentTarget: { value: 'alice' } })
      inputs[1].props.onChange({ currentTarget: { value: 'secret' } })
    })
    await act(async () => {
      submitPromise = form.props.onSubmit({ preventDefault: vi.fn() })
    })
    await act(async () => { renderer.unmount() })
    errorSpy.mockClear()

    await act(async () => {
      await submitPromise
    })

    expect(loginSignal.aborted).toBe(true)
    expect(authenticated).not.toHaveBeenCalled()
    expect(errorSpy).not.toHaveBeenCalled()
  })
})
