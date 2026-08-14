import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiFormatError } from '../api/client'
import {
  LOGS_ERROR_MESSAGE,
  LOGS_FORBIDDEN_MESSAGE,
  LOGS_NOT_FOUND_MESSAGE,
  LogsError,
  getInvocationDetail,
  listInvocations,
  parseInvocationDetail,
  parseInvocationList,
} from '../api/logs'
import App from '../App'
import { ADMIN_LOGS_ROUTE, DEFAULT_APP_ROUTE } from '../app/routes'

const RAW_OLD = 'RAW_MARKER_OLD'
const RAW_NEW = 'RAW_MARKER_NEW'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function session(role: 'admin' | 'member' = 'admin') {
  return {
    user: { id: `${role}-1`, username: role, role },
    csrf_token: 'csrf-safe-value',
  }
}

function listItem(invocationId: string, hasRedaction = false) {
  return {
    invocation_id: invocationId,
    endpoint_id: 'endpoint-1',
    endpoint_version_id: 'version-1',
    request_id: `request-${invocationId}`,
    status: 'failed',
    error_code: 'timeout',
    latency_ms: 12.5,
    created_at: 10,
    completed_at: 11,
    has_redactions: hasRedaction,
  }
}

function listPage(items: unknown[], nextCursor: string | null = null) {
  return { items, next_cursor: nextCursor }
}

function detail(invocationId: string, marker: string | null = RAW_NEW) {
  return {
    invocation: { id: invocationId, request_id: `request-${invocationId}`, session_id: null },
    endpoint_id: 'endpoint-1',
    endpoint_version_id: 'version-1',
    credential_id: null,
    message_id: null,
    status: 'failed',
    input: marker === null ? null : { prompt: marker },
    metadata: {},
    output: marker === null ? null : { answer: marker },
    error: { code: 'timeout' },
    usage: null,
    metadata_size_bytes: 2,
    metadata_sha256: 'a'.repeat(64),
    latency_ms: 12.5,
    pricing_version: null,
    created_at: 10,
    completed_at: 11,
    run_events: [{
      id: `event-${invocationId}`,
      sequence_number: 0,
      event_type: 'completed',
      payload: marker === null ? null : { state: marker },
      created_at: 10.5,
    }],
    tool_calls: [],
    redactions: [{
      id: `redaction-${invocationId}`,
      target_type: 'metadata',
      target_row_id: invocationId,
      json_path: '$.secret',
      reason: 'privacy',
      is_tombstone: true,
      redacted_at: 9,
    }],
  }
}

async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve() })
}

type Deferred<T> = { promise: Promise<T>; resolve(value: T): void }
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function text(renderer: ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON())
}

function button(renderer: ReactTestRenderer, label: string) {
  return renderer.root.findAllByType('button').find((item) => item.children.join('') === label)!
}

function input(renderer: ReactTestRenderer, id: string) {
  return renderer.root.findByProps({ id })
}

async function submitList(renderer: ReactTestRenderer, endpoint = 'endpoint-1'): Promise<void> {
  await act(async () => input(renderer, 'logs-endpoint').props.onChange({ currentTarget: { value: endpoint } }))
  await act(async () => renderer.root.findByProps({ 'aria-label': '篩選呼叫紀錄' }).props.onSubmit({
    preventDefault: vi.fn(),
  }))
}

describe('A18 Admin logs production decoder與API boundary', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('重建exact list/detail快照且不受caller後續突變', () => {
    const rawList = listPage([listItem('invocation-1')], 'cursor.next')
    const parsedList = parseInvocationList(rawList)
    rawList.items[0] = listItem('invocation-changed')
    expect(parsedList.items[0].invocationId).toBe('invocation-1')
    expect(parsedList.nextCursor).toBe('cursor.next')

    const rawDetail = detail('invocation-1', RAW_NEW)
    const parsedDetail = parseInvocationDetail(rawDetail)
    rawDetail.input = { prompt: 'MUTATED' }
    expect(JSON.stringify(parsedDetail)).toContain(RAW_NEW)
    expect(JSON.stringify(parsedDetail)).not.toContain('MUTATED')
  })

  it('接受契約內invalid_api_key狀態而不把結構欄位誤判為raw secret', () => {
    const body = { ...detail('invocation-1'), status: 'invalid_api_key' }
    expect(parseInvocationDetail(body).status).toBe('invalid_api_key')
  })

  it('接受canonical nullable metadata size與不以敏感尾碼結尾的合法raw keys', () => {
    const body = {
      ...detail('invocation-1'),
      metadata_size_bytes: null,
      input: { cookie_policy: 'accepted', authorization_state: 'disabled' },
    }
    const parsed = parseInvocationDetail(body)
    expect(parsed.metadataSizeBytes).toBeNull()
    expect(parsed.input).toEqual({ cookie_policy: 'accepted', authorization_state: 'disabled' })
  })

  it.each([
    { ...detail('invocation-1'), extra: true },
    { ...detail('invocation-1'), invocation: { ...detail('invocation-1').invocation, extra: true } },
    { ...detail('invocation-1'), run_events: [{ ...detail('invocation-1').run_events[0], extra: true }] },
    { ...detail('invocation-1'), input: { Authorization: 'marker' } },
    { ...detail('invocation-1'), input: { cookie: 'marker' } },
    { ...detail('invocation-1'), input: { session_cookie: 'marker' } },
    { ...detail('invocation-1'), input: { path: '/Users/example/private.json' } },
  ])('拒絕額外欄位與semantic secret/path', (body) => {
    expect(() => parseInvocationDetail(body)).toThrow(ApiFormatError)
  })

  it('只發exact same-origin credentialed list/detail GET', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(listPage([listItem('invocation-1')], 'cursor.next')))
      .mockResolvedValueOnce(jsonResponse(detail('invocation-1')))

    await expect(listInvocations('endpoint-1', {
      status: 'failed', errorCode: 'timeout', limit: 50,
    })).resolves.toMatchObject({ nextCursor: 'cursor.next' })
    await expect(getInvocationDetail('endpoint-1', 'invocation-1')).resolves.toMatchObject({
      invocation: { id: 'invocation-1' },
    })

    expect(fetchMock).toHaveBeenNthCalledWith(1,
      '/api/admin/endpoints/endpoint-1/invocations?status=failed&error_code=timeout&limit=50', {
        method: 'GET', credentials: 'include', headers: { Accept: 'application/json' },
      })
    expect(fetchMock).toHaveBeenNthCalledWith(2,
      '/api/admin/endpoints/endpoint-1/invocations/invocation-1', {
        method: 'GET', credentials: 'include', headers: { Accept: 'application/json' },
      })
  })

  it('接受契約上限100筆的合法safe metadata頁', async () => {
    const items = Array.from({ length: 100 }, (_, index) => listItem(`invocation-${index}`))
    fetchMock.mockResolvedValueOnce(jsonResponse(listPage(items)))
    await expect(listInvocations('endpoint-1', { limit: 100 })).resolves.toMatchObject({
      items: expect.arrayContaining([expect.objectContaining({ invocationId: 'invocation-99' })]),
    })
  })

  it.each([
    [401, LOGS_ERROR_MESSAGE],
    [403, LOGS_FORBIDDEN_MESSAGE],
    [404, LOGS_NOT_FOUND_MESSAGE],
    [503, LOGS_ERROR_MESSAGE],
    [500, LOGS_ERROR_MESSAGE],
  ])('將HTTP %i清洗成固定UI錯誤', async (status, message) => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: { internal: RAW_OLD } }, status))
    await expect(getInvocationDetail('endpoint-1', 'invocation-1')).rejects.toEqual(
      expect.objectContaining({ name: 'LogsError', status, message }),
    )
  })

  it('非法identifier/filter在fetch前fail closed', async () => {
    await expect(listInvocations('../private', {})).rejects.toBeInstanceOf(LogsError)
    await expect(listInvocations('endpoint-1', { limit: 101 })).rejects.toBeInstanceOf(LogsError)
    await expect(getInvocationDetail('endpoint-1', 'https://evil.example')).rejects.toBeInstanceOf(LogsError)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('A18 Admin logs UI與敏感state lifecycle', () => {
  const fetchMock = vi.fn<typeof fetch>()
  let renderer: ReactTestRenderer | undefined
  let pathname: string = ADMIN_LOGS_ROUTE
  let popstate: (() => void) | undefined
  const replaceState = vi.fn((_state: unknown, _title: string, path: string) => { pathname = path })
  const storage = new Proxy({}, { get: () => { throw new Error('禁止使用storage') } })

  beforeEach(() => {
    pathname = ADMIN_LOGS_ROUTE
    popstate = undefined
    fetchMock.mockReset()
    replaceState.mockClear()
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('localStorage', storage)
    vi.stubGlobal('sessionStorage', storage)
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

  it('non-admin不渲染navigation/route/content且不發Admin request', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('member')))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => { renderer = create(<App />) })
    await flush()

    expect(text(renderer!)).not.toContain('完整呼叫紀錄')
    expect(renderer!.root.findAllByProps({ id: 'logs-title' })).toHaveLength(0)
    expect(fetchMock.mock.calls.filter(([route]) => String(route).startsWith('/api/admin/'))).toHaveLength(0)
    expect(replaceState).toHaveBeenCalledWith(null, '', DEFAULT_APP_ROUTE)
  })

  it('Admin只由固定navigation進入Logs route', async () => {
    pathname = DEFAULT_APP_ROUTE
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await act(async () => button(renderer!, '完整呼叫紀錄').props.onClick())
    expect(pathname).toBe(ADMIN_LOGS_ROUTE)
    expect(renderer!.root.findByProps({ id: 'logs-title' })).toBeDefined()
    expect(fetchMock.mock.calls.filter(([route]) => String(route).startsWith('/api/admin/'))).toHaveLength(0)
  })

  it('Admin list/detail顯示遮蔽與tombstone且沒有禁止控制項', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(listPage([listItem('invocation-1', true)])))
      .mockResolvedValueOnce(jsonResponse(detail('invocation-1', null)))
    await act(async () => { renderer = create(<App />) })
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      input(renderer!, 'logs-from-at').props.onChange({ currentTarget: { value: '1' } })
      input(renderer!, 'logs-to-at').props.onChange({ currentTarget: { value: '2' } })
    })
    await submitList(renderer!)
    expect(fetchMock).toHaveBeenNthCalledWith(2,
      '/api/admin/endpoints/endpoint-1/invocations?from_at=1&to_at=2&limit=50',
      expect.objectContaining({ method: 'GET', credentials: 'include' }))
    await act(async () => button(renderer!, 'invocation-1 — failed（已遮蔽）').props.onClick())
    const source = text(renderer!)
    expect(source).toContain('部分內容已依政策遮蔽')
    expect(source).toContain('已刪除或無資料')
    expect(source).not.toMatch(/export|download|copy all|copy-all|share link|raw search|匯出|下載|複製全部|分享連結|全文搜尋/i)
  })

  it('detail 503先清除前一筆raw再顯示固定錯誤', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(listPage([
        listItem('invocation-old'), listItem('invocation-new'),
      ])))
      .mockResolvedValueOnce(jsonResponse(detail('invocation-old', RAW_OLD)))
      .mockResolvedValueOnce(jsonResponse({ detail: { private: RAW_NEW } }, 503))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await submitList(renderer!)
    await act(async () => button(renderer!, 'invocation-old — failed').props.onClick())
    expect(text(renderer!)).toContain(RAW_OLD)
    await act(async () => button(renderer!, 'invocation-new — failed').props.onClick())
    expect(text(renderer!)).not.toContain(RAW_OLD)
    expect(text(renderer!)).not.toContain(RAW_NEW)
    expect(text(renderer!)).toContain(LOGS_ERROR_MESSAGE)
  })

  it('快速切換時abort舊request且舊completion不能覆寫新detail', async () => {
    const slow = deferred<Response>()
    let oldSignal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(listPage([
        listItem('invocation-old'), listItem('invocation-new'),
      ])))
      .mockImplementationOnce((_route, init) => {
        oldSignal = init!.signal as AbortSignal
        return slow.promise
      })
      .mockResolvedValueOnce(jsonResponse(detail('invocation-new', RAW_NEW)))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await submitList(renderer!)
    await act(async () => { void button(renderer!, 'invocation-old — failed').props.onClick() })
    await act(async () => button(renderer!, 'invocation-new — failed').props.onClick())
    expect(oldSignal.aborted).toBe(true)
    await act(async () => { slow.resolve(jsonResponse(detail('invocation-old', RAW_OLD))); await slow.promise })
    expect(text(renderer!)).toContain(RAW_NEW)
    expect(text(renderer!)).not.toContain(RAW_OLD)
  })

  it('route change立即清raw、abort pending且URL/history不含raw或IDs', async () => {
    const pending = deferred<Response>()
    let signal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(listPage([listItem('invocation-old')])))
      .mockResolvedValueOnce(jsonResponse(detail('invocation-old', RAW_OLD)))
      .mockImplementationOnce((_route, init) => {
        signal = init!.signal as AbortSignal
        return pending.promise
      })
    await act(async () => { renderer = create(<App />) })
    await flush()
    await submitList(renderer!)
    await act(async () => button(renderer!, 'invocation-old — failed').props.onClick())
    expect(text(renderer!)).toContain(RAW_OLD)
    await act(async () => { void button(renderer!, 'invocation-old — failed').props.onClick() })
    await act(async () => button(renderer!, '返回對話').props.onClick())
    expect(signal.aborted).toBe(true)
    expect(text(renderer!)).not.toContain(RAW_OLD)
    expect(pathname).toBe(DEFAULT_APP_ROUTE)
    expect(JSON.stringify(replaceState.mock.calls)).not.toMatch(/RAW_MARKER|invocation-old|endpoint-1/)
    pending.resolve(jsonResponse(detail('invocation-old', RAW_OLD)))
  })

  it('logout點擊當下清raw且不寫storage/console/analytics', async () => {
    const freshSession = deferred<Response>()
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {})
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(listPage([listItem('invocation-old')])))
      .mockResolvedValueOnce(jsonResponse(detail('invocation-old', RAW_OLD)))
      .mockReturnValueOnce(freshSession.promise)
    await act(async () => { renderer = create(<App />) })
    await flush()
    await submitList(renderer!)
    await act(async () => button(renderer!, 'invocation-old — failed').props.onClick())
    expect(text(renderer!)).toContain(RAW_OLD)
    await act(async () => { button(renderer!, '登出').props.onClick() })
    expect(text(renderer!)).not.toContain(RAW_OLD)
    expect(JSON.stringify(logSpy.mock.calls)).not.toMatch(/RAW_MARKER/)
    expect(JSON.stringify(errorSpy.mock.calls)).not.toMatch(/RAW_MARKER/)
    freshSession.resolve(jsonResponse(session('admin')))
  })

  it('browser popstate離開Logs route時unmount並清raw', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(listPage([listItem('invocation-old')])))
      .mockResolvedValueOnce(jsonResponse(detail('invocation-old', RAW_OLD)))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await submitList(renderer!)
    await act(async () => button(renderer!, 'invocation-old — failed').props.onClick())
    pathname = DEFAULT_APP_ROUTE
    await act(async () => { popstate?.() })
    expect(text(renderer!)).not.toContain(RAW_OLD)
    expect(renderer!.root.findAllByProps({ id: 'logs-title' })).toHaveLength(0)
  })

  it('直接unmount會abort pending detail且completion不輸出raw', async () => {
    const pending = deferred<Response>()
    let signal!: AbortSignal
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(listPage([listItem('invocation-old')])))
      .mockImplementationOnce((_route, init) => {
        signal = init!.signal as AbortSignal
        return pending.promise
      })
    await act(async () => { renderer = create(<App />) })
    await flush()
    await submitList(renderer!)
    await act(async () => { void button(renderer!, 'invocation-old — failed').props.onClick() })
    await act(async () => { renderer!.unmount() })
    renderer = undefined
    expect(signal.aborted).toBe(true)
    await act(async () => { pending.resolve(jsonResponse(detail('invocation-old', RAW_OLD))); await pending.promise })
    expect(JSON.stringify(errorSpy.mock.calls)).not.toContain(RAW_OLD)
  })
})
