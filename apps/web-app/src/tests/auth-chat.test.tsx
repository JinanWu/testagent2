import { StrictMode, useEffect } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  API_ROUTES,
  ApiFormatError,
  apiRequest,
  type ApiRoute,
} from '../api/client'
import {
  AUTH_ERROR_MESSAGE,
  CREDENTIALS_ERROR_MESSAGE,
  AuthError,
  getSession,
  login,
  logout as requestLogout,
} from '../api/auth'
import {
  SessionProvider,
  useSession,
  type SessionContextValue,
} from '../app/SessionProvider'
import App from '../App'
import {
  DEFAULT_APP_ROUTE,
  isAppRoute,
  replaceAppRoute,
} from '../app/routes'
import LoginPage from '../pages/LoginPage'

const sessionBody = {
  user: { id: 'user-1', username: 'alice', role: 'member' },
  csrf_token: 'csrf-secret',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function noContentResponse(): Response {
  return new Response(null, { status: 204 })
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
  })
}

describe('safe auth API', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('localStorage', new Proxy({}, { get: () => { throw new Error('storage used') } }))
    vi.stubGlobal('sessionStorage', new Proxy({}, { get: () => { throw new Error('storage used') } }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('rejects injected routes and oversized bodies before fetch', async () => {
    await expect(apiRequest('https://evil.example/api/auth/session' as ApiRoute)).rejects.toBeInstanceOf(ApiFormatError)
    await expect(apiRequest(API_ROUTES.login, { body: '密'.repeat(1025) })).rejects.toBeInstanceOf(ApiFormatError)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('restores a session with one exact same-origin credentialed call', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(sessionBody))

    await expect(getSession()).resolves.toEqual({
      user: sessionBody.user,
      csrfToken: 'csrf-secret',
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/session', {
      method: 'GET',
      credentials: 'include',
      headers: { Accept: 'application/json' },
    })
  })

  it('treats 401 as anonymous and other details as fixed errors', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'private' }, 401))
    await expect(getSession()).resolves.toBeNull()

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'database path' }, 503))
    await expect(getSession()).rejects.toMatchObject({ message: AUTH_ERROR_MESSAGE })
  })

  it.each([
    { ...sessionBody, extra: true },
    { ...sessionBody, user: { ...sessionBody.user, extra: true } },
    { ...sessionBody, csrf_token: '' },
  ])('rejects malformed or extra session JSON', async (body) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(body))
    await expect(getSession()).rejects.toBeInstanceOf(AuthError)
  })

  it('sends an exact bounded login body and uses a generic 401 error', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(sessionBody))
    await expect(login('alice', 'correct horse')).resolves.toMatchObject({ user: sessionBody.user })
    expect(fetchMock).toHaveBeenLastCalledWith('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'alice', password: 'correct horse' }),
    })

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'alice disabled' }, 401))
    await expect(login('alice', 'wrong')).rejects.toMatchObject({
      message: CREDENTIALS_ERROR_MESSAGE,
    })
  })

  it('requires 204 for logout without exposing server details', async () => {
    fetchMock.mockResolvedValueOnce(noContentResponse())
    await expect(requestLogout('csrf-secret')).resolves.toBeUndefined()
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'private' }))
    await expect(requestLogout('csrf-secret')).rejects.toMatchObject({ message: AUTH_ERROR_MESSAGE })
  })

  it('initializes once under StrictMode and fails closed on logout errors', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(sessionBody))
    let session: SessionContextValue | undefined
    function Capture() {
      const value = useSession()
      useEffect(() => { session = value }, [value])
      return null
    }

    let renderer: ReactTestRenderer
    await act(async () => {
      renderer = create(<StrictMode><SessionProvider><Capture /></SessionProvider></StrictMode>)
    })
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(session?.status).toBe('authenticated')

    fetchMock.mockResolvedValueOnce(jsonResponse(sessionBody))
    fetchMock.mockRejectedValueOnce(new Error('private network detail'))
    let logoutFailed = false
    await act(async () => {
      try {
        await session?.logout()
      } catch {
        logoutFailed = true
      }
    })
    expect(logoutFailed).toBe(true)
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/auth/session', expect.objectContaining({
      method: 'GET',
      credentials: 'include',
      signal: expect.any(AbortSignal),
    }))
    expect(fetchMock).toHaveBeenLastCalledWith('/api/auth/logout', expect.objectContaining({
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'application/json', 'X-CSRF-Token': 'csrf-secret' },
      signal: expect.any(AbortSignal),
    }))
    expect(session?.status).toBe('anonymous')
    await act(async () => { renderer!.unmount() })
  })

  it('renders accessible login controls and blocks duplicate submits', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, 401))
    let resolveLogin!: (response: Response) => void
    const pending = new Promise<Response>((resolve) => { resolveLogin = resolve })
    fetchMock.mockReturnValueOnce(pending)
    const authenticated = vi.fn()
    let renderer: ReactTestRenderer
    await act(async () => {
      renderer = create(<SessionProvider><LoginPage onAuthenticated={authenticated} /></SessionProvider>)
    })
    await flush()

    const root = renderer!.root
    const inputs = root.findAllByType('input')
    expect(inputs.map((input) => input.props.autoComplete)).toEqual(['username', 'current-password'])
    await act(async () => {
      inputs[0].props.onChange({ currentTarget: { value: 'alice' } })
      inputs[1].props.onChange({ currentTarget: { value: 'password' } })
    })
    const form = root.findByType('form')
    const event = { preventDefault: vi.fn() }
    await act(async () => {
      void form.props.onSubmit(event)
      void form.props.onSubmit(event)
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(root.findByType('button').props.disabled).toBe(true)

    await act(async () => { resolveLogin(jsonResponse(sessionBody)); await pending })
    expect(authenticated).toHaveBeenCalledOnce()
    expect(root.findByType('button').props.disabled).toBe(false)
    await act(async () => { renderer!.unmount() })
  })

  it('shows only an accessible loading state while session initialization is pending', async () => {
    let resolveSession!: (response: Response) => void
    fetchMock.mockReturnValueOnce(new Promise((resolve) => { resolveSession = resolve }))
    const replaceState = vi.fn()
    vi.stubGlobal('window', {
      location: { pathname: '/' },
      history: { replaceState },
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })

    let renderer: ReactTestRenderer
    await act(async () => {
      renderer = create(<StrictMode><App /></StrictMode>)
    })
    expect(renderer!.root.findByProps({ role: 'status' }).children.join('')).toContain('確認登入')
    expect(renderer!.root.findAllByType('textarea')).toHaveLength(0)
    expect(renderer!.root.findAllByProps({ id: 'login-title' })).toHaveLength(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await act(async () => { resolveSession(jsonResponse({}, 401)); await flush() })
    await act(async () => { renderer!.unmount() })
  })

  it('renders login anonymously and replaces the route with the default chat after login', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse(sessionBody))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    const replaceState = vi.fn()
    vi.stubGlobal('window', {
      location: { pathname: '/untrusted' },
      history: { replaceState },
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })

    let renderer: ReactTestRenderer
    await act(async () => { renderer = create(<App />) })
    await flush()
    const inputs = renderer!.root.findAllByType('input')
    await act(async () => {
      inputs[0].props.onChange({ currentTarget: { value: 'alice' } })
      inputs[1].props.onChange({ currentTarget: { value: 'password' } })
    })
    await act(async () => {
      await renderer!.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() })
    })

    expect(replaceState).toHaveBeenCalledWith(null, '', DEFAULT_APP_ROUTE)
    expect(renderer!.root.findByProps({ id: 'chat-title' }).children.join('')).toContain('對話')
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/sessions?limit=20', {
      method: 'GET',
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: expect.any(AbortSignal),
    })
    const source = JSON.stringify(renderer!.toJSON())
    expect(source).not.toMatch(/skill|技能選擇|端點建立|selectedSkill|skillId/i)
    await act(async () => { renderer!.unmount() })
  })

  it('loads sessions and renders the server chat reply for an authenticated root', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(sessionBody))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
      .mockResolvedValueOnce(jsonResponse(sessionBody))
      .mockResolvedValueOnce(jsonResponse({
        session_id: 'root-1',
        reply: { role: 'assistant', content: '您好，我能幫忙。' },
      }))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
      .mockResolvedValueOnce(jsonResponse({
        user: { id: 'user-1', username: 'alice', role: 'member' },
        csrf_token: 'csrf-after-chat',
      }))
      .mockResolvedValueOnce(noContentResponse())
    vi.stubGlobal('window', {
      location: { pathname: '/' },
      history: { replaceState: vi.fn() },
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })
    let renderer: ReactTestRenderer
    await act(async () => { renderer = create(<App />) })
    await flush()

    const source = JSON.stringify(renderer!.toJSON())
    expect(source).not.toContain('更多功能即將推出')
    const textarea = renderer!.root.findByType('textarea')
    const send = renderer!.root.findByProps({ type: 'submit' })
    expect(send.props.disabled).toBe(true)
    await act(async () => {
      textarea.props.onChange({ currentTarget: { value: '  你好  ' } })
    })
    await act(async () => {
      await renderer!.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() })
    })
    const conversation = renderer!.root.findByProps({ role: 'log' })
    expect(conversation.findAllByType('p').filter((item) => item.children.includes('你好'))).toHaveLength(1)
    expect(conversation.findAllByType('p').filter((item) => item.children.includes('您好，我能幫忙。'))).toHaveLength(1)
    expect(renderer!.root.findByType('textarea').props.value).toBe('')
    expect(fetchMock).toHaveBeenCalledTimes(5)
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/sessions?limit=20', {
      method: 'GET',
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: expect.any(AbortSignal),
    })
    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/chat', {
      method: 'POST',
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf-secret',
      },
      body: JSON.stringify({ message: '你好' }),
      signal: expect.any(AbortSignal),
    })
    expect(fetchMock).toHaveBeenNthCalledWith(5, '/api/sessions?limit=20', {
      method: 'GET',
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: expect.any(AbortSignal),
    })
    await act(async () => {
      renderer!.root.findAllByType('button').find((button) => button.children.includes('登出'))?.props.onClick()
      await flush()
    })
    expect(renderer!.root.findByProps({ id: 'login-title' })).toBeDefined()
    expect(fetchMock).toHaveBeenCalledTimes(7)
    expect(fetchMock).toHaveBeenNthCalledWith(6, '/api/auth/session', expect.objectContaining({
      method: 'GET',
      credentials: 'include',
      signal: expect.any(AbortSignal),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(7, '/api/auth/logout', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-after-chat' }),
    }))
    await act(async () => { renderer!.unmount() })
  })

  it('returns to login when chat discovers an expired session', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(sessionBody))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
      .mockResolvedValueOnce(jsonResponse({}, 401))
    vi.stubGlobal('window', {
      location: { pathname: '/' },
      history: { replaceState: vi.fn() },
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })
    let renderer: ReactTestRenderer
    await act(async () => { renderer = create(<App />) })
    await flush()
    await act(async () => {
      renderer!.root.findByType('textarea').props.onChange({ currentTarget: { value: '你好' } })
    })
    await act(async () => {
      await renderer!.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() })
    })
    expect(renderer!.root.findByProps({ id: 'login-title' })).toBeDefined()
    expect(renderer!.root.findAllByType('textarea')).toHaveLength(0)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    await act(async () => { renderer!.unmount() })
  })

  it('freezes exact route targets and rejects arbitrary replacements', () => {
    const replaceState = vi.fn()
    vi.stubGlobal('window', { history: { replaceState } })
    expect(isAppRoute('/')).toBe(true)
    expect(isAppRoute('/chat')).toBe(false)
    expect(isAppRoute('https://evil.example/')).toBe(false)
    expect(replaceAppRoute('/chat')).toBe(false)
    expect(replaceState).not.toHaveBeenCalled()
  })
})
