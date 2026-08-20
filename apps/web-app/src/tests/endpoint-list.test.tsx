import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import {
  ADMIN_LOGS_ROUTE,
  DEFAULT_APP_ROUTE,
  ENDPOINTS_ROUTE,
  parseAppRoute,
  replaceAppRoute,
} from '../app/routes'
import { ENDPOINT_LIST_ERROR_MESSAGE } from '../pages/EndpointListPage'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function noContentResponse(): Response {
  return new Response(null, { status: 204 })
}

function session(role: string = 'member') {
  return {
    user: { id: `${role}-1`, username: role, role },
    csrf_token: 'csrf-safe-value',
  }
}

function endpoint(endpointId: string, slug = endpointId) {
  return {
    endpoint_id: endpointId,
    slug,
    status: 'active',
    current_version_id: 'version-1',
    current_version_number: 1,
    updated_at: 20,
  }
}

function page(items: unknown[], nextCursor: string | null = null) {
  return { items, next_cursor: nextCursor }
}

type Deferred<T> = { promise: Promise<T>; resolve(value: T): void }
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve() })
}

function text(renderer: ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON())
}

function button(renderer: ReactTestRenderer, label: string) {
  return renderer.root.findAllByType('button').find((item) => item.children.join('') === label)!
}

describe('A22 role-aware shell與Owner endpoint list', () => {
  const fetchMock = vi.fn<typeof fetch>()
  let renderer: ReactTestRenderer | undefined
  let pathname = DEFAULT_APP_ROUTE as string
  let popstate: (() => void) | undefined
  const replaceState = vi.fn((_state: unknown, _title: string, path: string) => { pathname = path })

  beforeEach(() => {
    pathname = DEFAULT_APP_ROUTE
    popstate = undefined
    fetchMock.mockReset()
    replaceState.mockClear()
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('window', {
      location: { get pathname() { return pathname } },
      history: { replaceState },
      addEventListener: vi.fn((name: string, callback: () => void) => {
        if (name === 'popstate') popstate = callback
      }),
      removeEventListener: vi.fn(),
    })
  })

  afterEach(async () => {
    if (renderer) await act(async () => { renderer!.unmount() })
    renderer = undefined
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('freezes the exact static endpoint route and safe replacement', () => {
    expect(parseAppRoute(ENDPOINTS_ROUTE)).toBe(ENDPOINTS_ROUTE)
    expect(parseAppRoute('/endpoints/')).toBeNull()
    expect(parseAppRoute('/endpoints/new/extra')).toBeNull()
    expect(replaceAppRoute(ENDPOINTS_ROUTE)).toBe(true)
    expect(replaceState).toHaveBeenCalledWith(null, '', ENDPOINTS_ROUTE)
  })

  it('member sees endpoint management but no Admin logs and opens a live owner-scoped first page', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('member')))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
      .mockResolvedValueOnce(jsonResponse(page([endpoint('endpoint-1', 'safe-api')])))
    await act(async () => { renderer = create(<App />) })
    await flush()

    expect(button(renderer!, '端點管理')).toBeDefined()
    expect(text(renderer!)).not.toContain('完整呼叫紀錄')
    await act(async () => button(renderer!, '端點管理').props.onClick())
    await flush()

    expect(pathname).toBe(ENDPOINTS_ROUTE)
    expect(text(renderer!)).toContain('safe-api')
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/published-endpoints?scope=owner&limit=20', {
      method: 'GET', credentials: 'include', headers: { Accept: 'application/json' },
      signal: expect.any(AbortSignal),
    })
    expect(button(renderer!, '建立端點')).toBeDefined()
    expect(text(renderer!)).not.toMatch(/upload|edit|delete|invoke preview|mock/i)
  })

  it('Admin sees both entries and direct endpoint list still defaults to owner scope', async () => {
    pathname = ENDPOINTS_ROUTE
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(page([endpoint('endpoint-admin', 'admin-owned')])))
    await act(async () => { renderer = create(<App />) })
    await flush()

    expect(text(renderer!)).toContain('admin-owned')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/published-endpoints?scope=owner&limit=20',
      expect.objectContaining({ method: 'GET', credentials: 'include', signal: expect.any(AbortSignal) }))
    expect(fetchMock.mock.calls.map(([route]) => String(route)).join(' ')).not.toContain('scope=all')

    fetchMock.mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => button(renderer!, '返回對話').props.onClick())
    await flush()
    expect(button(renderer!, '端點管理')).toBeDefined()
    expect(button(renderer!, '完整呼叫紀錄')).toBeDefined()
  })

  it('unknown role fails closed without flashing any protected page and offers only fixed denial/logout', async () => {
    pathname = ENDPOINTS_ROUTE
    const pendingSession = deferred<Response>()
    fetchMock.mockReturnValueOnce(pendingSession.promise)
    await act(async () => { renderer = create(<App />) })
    expect(text(renderer!)).toContain('確認登入狀態')
    expect(text(renderer!)).not.toMatch(/開始對話|端點管理|完整呼叫紀錄/)

    await act(async () => {
      pendingSession.resolve(jsonResponse(session('auditor')))
      await pendingSession.promise
    })
    const source = text(renderer!)
    expect(source).toContain('目前帳號沒有可用的介面權限。')
    expect(source).toContain('登出')
    expect(source).not.toMatch(/開始對話|正在載入端點|完整呼叫紀錄|auditor/)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('auditor')))
      .mockResolvedValueOnce(noContentResponse())
    await act(async () => { button(renderer!, '登出').props.onClick(); await flush() })
    await flush()
    expect(text(renderer!)).toContain('login-title')
  })

  it('renders loading then legal empty and maps server details to one fixed error', async () => {
    pathname = ENDPOINTS_ROUTE
    const pending = deferred<Response>()
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockReturnValueOnce(pending.promise)
    await act(async () => { renderer = create(<App />) })
    await flush()
    expect(renderer!.root.findByProps({ role: 'status' }).children.join('')).toContain('載入端點')

    await act(async () => { pending.resolve(jsonResponse(page([]))); await pending.promise })
    expect(text(renderer!)).toContain('目前沒有端點。')

    await act(async () => button(renderer!, '返回對話').props.onClick())
    fetchMock.mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await flush()
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'PRIVATE_MARKER' }, 503))
    await act(async () => button(renderer!, '端點管理').props.onClick())
    await flush()
    expect(text(renderer!)).toContain(ENDPOINT_LIST_ERROR_MESSAGE)
    expect(text(renderer!)).not.toContain('PRIVATE_MARKER')
  })

  it('loads an opaque cursor once per same-render click and opens an existing detail route', async () => {
    pathname = ENDPOINTS_ROUTE
    const secondPage = deferred<Response>()
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(page([endpoint('endpoint-1', 'first')], 'opaque_CURSOR')))
      .mockReturnValueOnce(secondPage.promise)
    await act(async () => { renderer = create(<App />) })
    await flush()

    const loadMore = button(renderer!, '載入更多')
    await act(async () => {
      void loadMore.props.onClick()
      void loadMore.props.onClick()
    })
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/published-endpoints?scope=owner&limit=20&cursor=opaque_CURSOR',
      expect.objectContaining({ method: 'GET', credentials: 'include', signal: expect.any(AbortSignal) }),
    )
    await act(async () => {
      secondPage.resolve(jsonResponse(page([
        endpoint('endpoint-2', 'second'), endpoint('endpoint-1', 'first-updated'),
      ])))
      await secondPage.promise
    })
    const labels = renderer!.root.findAllByType('button').map((item) => item.children.join(''))
    expect(labels).toContain('first-updated')
    expect(labels).not.toContain('first')
    expect(labels).toContain('second')
    expect(renderer!.root.findAllByProps({ 'aria-label': '端點清單' })[0].findAllByType('li')).toHaveLength(2)

    fetchMock.mockImplementation(() => new Promise<Response>(() => {}))
    await act(async () => button(renderer!, 'second').props.onClick())
    expect(pathname).toBe('/endpoints/endpoint-2')
    expect(renderer!.root.findByProps({ id: 'endpoint-title' })).toBeDefined()
  })

  it('unmount aborts the in-flight list and late completion cannot publish state', async () => {
    pathname = ENDPOINTS_ROUTE
    const pending = deferred<Response>()
    let signal!: AbortSignal
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockImplementationOnce((_route, init) => {
        signal = init!.signal as AbortSignal
        return pending.promise
      })
    await act(async () => { renderer = create(<App />) })
    await flush()
    await act(async () => { renderer!.unmount() })
    renderer = undefined
    expect(signal.aborted).toBe(true)
    await act(async () => {
      pending.resolve(jsonResponse(page([endpoint('late', 'LATE_MARKER')]))); await pending.promise
    })
    expect(JSON.stringify(errorSpy.mock.calls)).not.toContain('LATE_MARKER')
  })

  it('popstate route change aborts list and ignores a transport that resolves after abort', async () => {
    pathname = ENDPOINTS_ROUTE
    const pending = deferred<Response>()
    let signal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockImplementationOnce((_route, init) => {
        signal = init!.signal as AbortSignal
        return pending.promise
      })
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => { renderer = create(<App />) })
    await flush()

    pathname = DEFAULT_APP_ROUTE
    await act(async () => { popstate?.() })
    await flush()
    expect(signal.aborted).toBe(true)
    expect(renderer!.root.findByProps({ id: 'chat-title' })).toBeDefined()
    await act(async () => {
      pending.resolve(jsonResponse(page([endpoint('late', 'LATE_ROUTE_MARKER')]))); await pending.promise
    })
    expect(text(renderer!)).not.toContain('LATE_ROUTE_MARKER')
  })

  it('logout synchronously aborts list, clears it, and a late role response stays fail closed', async () => {
    pathname = ENDPOINTS_ROUTE
    const pending = deferred<Response>()
    let signal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockImplementationOnce((_route, init) => {
        signal = init!.signal as AbortSignal
        return pending.promise
      })
      .mockResolvedValueOnce(jsonResponse(session('auditor')))
    await act(async () => { renderer = create(<App />) })
    await flush()

    await act(async () => { button(renderer!, '登出').props.onClick(); await flush() })
    expect(signal.aborted).toBe(true)
    expect(text(renderer!)).toContain('目前帳號沒有可用的介面權限。')
    expect(text(renderer!)).not.toMatch(/正在載入端點|開始對話|完整呼叫紀錄/)
    await act(async () => {
      pending.resolve(jsonResponse(page([endpoint('late', 'LATE_LOGOUT_MARKER')]))); await pending.promise
    })
    expect(text(renderer!)).not.toContain('LATE_LOGOUT_MARKER')
    expect(pathname).toBe(ENDPOINTS_ROUTE)
    expect(parseAppRoute(ADMIN_LOGS_ROUTE)).toBe(ADMIN_LOGS_ROUTE)
  })
})
