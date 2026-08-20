import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import {
  formatEndpointVersionBuilderRoute,
  parseAppRoute,
} from '../app/routes'
import { ENDPOINT_DETAIL_ERROR_MESSAGE, ENDPOINT_NOT_FOUND_MESSAGE } from '../pages/EndpointDetailPage'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}
function session() {
  return { user: { id: 'owner-1', username: 'owner', role: 'member' }, csrf_token: 'csrf-safe-value' }
}
function detail(endpointId: string, slug = 'safe-api') {
  return {
    endpoint_id: endpointId,
    owner_user_id: 'owner-1',
    slug,
    status: 'active',
    current_version_id: 'version-2',
    current_version_number: 2,
    created_at: 10,
    updated_at: 20,
  }
}
type Deferred<T> = { promise: Promise<T>; resolve(value: T): void }
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
async function flush() { await act(async () => { await Promise.resolve(); await Promise.resolve() }) }
function text(renderer: ReactTestRenderer) { return JSON.stringify(renderer.toJSON()) }
function button(renderer: ReactTestRenderer, label: string) {
  return renderer.root.findAllByType('button').find((node) => node.children.join('') === label)!
}

describe('A22-03 Owner safe endpoint detail', () => {
  const fetchMock = vi.fn<typeof fetch>()
  let renderer: ReactTestRenderer | undefined
  let pathname = '/endpoints/endpoint-1'
  let popstate: (() => void) | undefined
  const replaceState = vi.fn((_state: unknown, _title: string, path: string) => { pathname = path })

  beforeEach(() => {
    pathname = '/endpoints/endpoint-1'
    popstate = undefined
    fetchMock.mockReset()
    replaceState.mockClear()
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('window', {
      location: { get pathname() { return pathname } },
      history: { replaceState },
      addEventListener: vi.fn((name: string, callback: () => void) => { if (name === 'popstate') popstate = callback }),
      removeEventListener: vi.fn(),
    })
  })
  afterEach(async () => {
    if (renderer) await act(async () => { renderer!.unmount() })
    renderer = undefined
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('parses version Builder before endpoint detail and rejects new as an endpoint id', () => {
    expect(parseAppRoute('/endpoints/new')).toEqual({ kind: 'endpoint-new' })
    expect(parseAppRoute('/endpoints/endpoint-1/versions/new')).toEqual({ kind: 'endpoint-version-new', endpointId: 'endpoint-1' })
    expect(formatEndpointVersionBuilderRoute('endpoint-1')).toBe('/endpoints/endpoint-1/versions/new')
    expect(parseAppRoute('/endpoints/new/versions/new')).toBeNull()
  })

  it('loads the authoritative safe detail, renders fixed fields, preserves diagnostics, and opens version Builder', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(detail('endpoint-1')))
      .mockRejectedValue(new Error('diagnostics unavailable'))
    await act(async () => { renderer = create(<App />) })
    await flush()

    const rendered = text(renderer!)
    expect(rendered).toContain('safe-api')
    expect(rendered).toContain('active')
    expect(rendered).toContain('版本 2')
    expect(rendered).toContain('端點觀測')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/published-endpoints/endpoint-1',
      expect.objectContaining({ method: 'GET', credentials: 'include', signal: expect.any(AbortSignal) }))

    await act(async () => { button(renderer!, '建立新版本').props.onClick() })
    expect(pathname).toBe('/endpoints/endpoint-1/versions/new')
    expect(text(renderer!)).toContain('建立新版本')
  })

  it('maps foreign/missing 404 and all other failures to fixed non-disclosing states', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(session())).mockResolvedValueOnce(jsonResponse({ detail: 'FOREIGN_SECRET' }, 404))
    await act(async () => { renderer = create(<App />) })
    await flush()
    expect(text(renderer!)).toContain(ENDPOINT_NOT_FOUND_MESSAGE)
    expect(text(renderer!)).not.toContain('FOREIGN_SECRET')
    expect(text(renderer!)).not.toContain('端點觀測')

    await act(async () => { renderer!.unmount() }); renderer = undefined
    fetchMock.mockReset()
    fetchMock.mockResolvedValueOnce(jsonResponse(session())).mockResolvedValueOnce(jsonResponse({ detail: 'PRIVATE_FAILURE' }, 503))
    await act(async () => { renderer = create(<App />) })
    await flush()
    expect(text(renderer!)).toContain(ENDPOINT_DETAIL_ERROR_MESSAGE)
    expect(text(renderer!)).not.toContain('PRIVATE_FAILURE')
  })

  it('aborts detail on route change and suppresses a transport completion that ignores abort', async () => {
    const pending = deferred<Response>()
    let detailSignal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockImplementationOnce((_route, init) => {
        detailSignal = init!.signal as AbortSignal
        return pending.promise
      })
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => { renderer = create(<App />) })
    await flush()
    expect(text(renderer!)).toContain('正在載入端點詳情')

    pathname = '/'
    await act(async () => { popstate?.() })
    await flush()
    expect(detailSignal.aborted).toBe(true)
    await act(async () => { pending.resolve(jsonResponse(detail('endpoint-1', 'LATE_DETAIL'))); await pending.promise })
    expect(text(renderer!)).not.toContain('LATE_DETAIL')
  })

  it('logout在原detail頁同步abort舊request，失敗後以新revision安全重載', async () => {
    const oldDetail = deferred<Response>()
    let oldSignal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockImplementationOnce((_route, init) => {
        oldSignal = init!.signal as AbortSignal
        return oldDetail.promise
      })
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse({ detail: 'PRIVATE_LOGOUT_FAILURE' }, 500))
      .mockResolvedValueOnce(jsonResponse(detail('endpoint-1', 'reloaded-safe')))
      .mockRejectedValue(new Error('diagnostics unavailable'))
    await act(async () => { renderer = create(<App />) })
    await flush()

    await act(async () => { button(renderer!, '登出').props.onClick(); await flush() })
    await flush()
    expect(oldSignal.aborted).toBe(true)
    expect(pathname).toBe('/endpoints/endpoint-1')
    expect(text(renderer!)).not.toContain('開始對話')
    expect(text(renderer!)).toContain('reloaded-safe')
    expect(text(renderer!)).not.toContain('PRIVATE_LOGOUT_FAILURE')

    await act(async () => {
      oldDetail.resolve(jsonResponse(detail('endpoint-1', 'LATE_LOGOUT_DETAIL')))
      await oldDetail.promise
    })
    expect(text(renderer!)).not.toContain('LATE_LOGOUT_DETAIL')
  })
})
