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
const initialApiKey = `pk_${'A'.repeat(43)}`
function credential(credentialId: string, status: 'active' | 'revoked' = 'active') {
  return {
    credential_id: credentialId, name: 'Production key', purpose: 'Invoke safe endpoint',
    key_prefix: initialApiKey.slice(0, 12), key_last4: initialApiKey.slice(-4), status,
    expires_at: 200, last_used_at: null, created_at: 100,
    revoked_at: status === 'revoked' ? 150 : null,
    ip_allowlist: ['192.0.2.1'], rate_limit_requests: 60,
  }
}
function docs() {
  const errors = [
    ['endpoint_not_found', 404, '找不到 endpoint slug。'], ['invalid_api_key', 401, 'API key 無效。'],
    ['api_key_expired', 401, 'API key 已過期。'], ['endpoint_disabled', 403, 'Endpoint 已停用。'],
    ['endpoint_archived', 410, 'Endpoint 已封存。'], ['input_schema_invalid', 422, 'Input 不符合 schema。'],
    ['model_output_schema_invalid', 502, '模型輸出不符合 response schema。'], ['rate_limit_exceeded', 429, '呼叫頻率超過限制。'],
    ['model_timeout', 504, '模型供應商逾時。'], ['tool_execution_failed', 502, '工具執行失敗。'],
    ['tool_timeout', 504, '工具執行逾時。'], ['endpoint_misconfigured', 500, 'Endpoint 設定錯誤。'],
    ['internal_error', 500, '伺服器內部錯誤。'],
  ].map(([code, status, message]) => ({ code, status, message }))
  return {
    endpoint: { id: 'endpoint-1', slug: 'safe-api', version: 2, status: 'active' },
    invoke_url: '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke',
    authentication: { scheme: 'bearer', header: 'Authorization' },
    request_schema: {
      type: 'object', additionalProperties: false, required: ['input'],
      properties: {
        input: { type: 'object' },
        session_id: {
          anyOf: [{ type: 'string', maxLength: 128 }, { type: 'null' }],
          'x-utf8-max-bytes': 128,
          description: 'Optional Published session identifier；上限 128 UTF-8 bytes。',
        },
        metadata: { anyOf: [{ type: 'object' }, { type: 'null' }] },
      },
    },
    response_schema: { type: 'object' }, rate_limit: { requests: 60, window_seconds: 60 },
    examples: {
      curl: "curl -X POST '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke' -H 'Authorization: Bearer ${API_KEY}' -H 'Content-Type: application/json' --data '{\"input\":{},\"session_id\":\"${SESSION_ID}\",\"metadata\":{\"endpoint_id\":\"${ENDPOINT_ID}\"}}'",
      python: "import json\nimport urllib.request\nurl = '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke'\npayload = {'input': {}, 'session_id': '${SESSION_ID}', 'metadata': {'endpoint_id': '${ENDPOINT_ID}'}}\nrequest = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Authorization': 'Bearer ${API_KEY}', 'Content-Type': 'application/json'}, method='POST')\nwith urllib.request.urlopen(request) as response:\n    print(response.read().decode('utf-8'))",
    },
    errors,
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

  it('loads the authoritative safe detail, renders four explicit tabs, and opens version Builder', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(detail('endpoint-1')))
    await act(async () => { renderer = create(<App />) })
    await flush()

    const rendered = text(renderer!)
    expect(rendered).toContain('safe-api')
    expect(rendered).toContain('active')
    expect(rendered).toContain('版本 2')
    expect(rendered).toContain('Overview')
    expect(rendered).toContain('Credentials')
    expect(rendered).toContain('Docs')
    expect(rendered).toContain('Diagnostics')
    expect(rendered).not.toContain('端點觀測')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/published-endpoints/endpoint-1',
      expect.objectContaining({ method: 'GET', credentials: 'include', signal: expect.any(AbortSignal) }))

    await act(async () => { button(renderer!, '建立新版本').props.onClick() })
    expect(pathname).toBe('/endpoints/endpoint-1/versions/new')
    expect(text(renderer!)).toContain('建立新版本')
  })

  it('creates a one-time credential, blocks mutations until clear, then confirms/reloads revoke and renders live docs', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(detail('endpoint-1')))
      .mockResolvedValueOnce(jsonResponse({ items: [credential('credential-1')] }))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse({ ...credential('credential-2'), initial_api_key: initialApiKey }, 201))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ items: [credential('credential-1', 'revoked'), credential('credential-2')] }))
      .mockResolvedValueOnce(jsonResponse(docs()))
    await act(async () => { renderer = create(<App />) })
    await flush()

    await act(async () => button(renderer!, 'Credentials').props.onClick())
    await flush()
    expect(text(renderer!)).toContain('Production key')

    const change = async (id: string, value: string) => act(async () => {
      renderer!.root.findByProps({ id }).props.onChange({ currentTarget: { value } })
    })
    await change('credential-name', 'Second key')
    await change('credential-purpose', 'Automation')
    await change('credential-expires-at', '300')
    await change('credential-ip-allowlist', '198.51.100.2')
    await change('credential-rate-limit', '30')
    await act(async () => {
      renderer!.root.findByProps({ 'aria-label': '建立 credential' }).props.onSubmit({ preventDefault: vi.fn() })
      await flush()
    })
    await flush()

    expect(text(renderer!)).toContain(initialApiKey)
    const createCall = fetchMock.mock.calls.find(([route, init]) =>
      route === '/api/published-endpoints/endpoint-1/credentials' && init?.method === 'POST')!
    expect(JSON.parse(String(createCall[1]?.body))).toEqual({
      name: 'Second key', purpose: 'Automation', expires_at: 300,
      ip_allowlist: ['198.51.100.2'], rate_limit_requests: 30,
    })
    expect(button(renderer!, '撤銷').props.disabled).toBe(true)
    expect(button(renderer!, '建立 credential').props.disabled).toBe(true)

    await act(async () => button(renderer!, '已保存並清除').props.onClick())
    expect(text(renderer!)).not.toContain(initialApiKey)
    await act(async () => button(renderer!, '撤銷').props.onClick())
    expect(text(renderer!)).toContain('確認撤銷')
    expect(text(renderer!)).toContain('Production key')
    await act(async () => { button(renderer!, '確認撤銷').props.onClick(); await flush() })
    await flush()
    expect(text(renderer!)).toContain('revoked')
    expect(fetchMock).toHaveBeenCalledWith('/api/published-endpoints/endpoint-1/credentials/credential-1/revoke',
      expect.objectContaining({ method: 'POST', headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-safe-value' }) }))

    await act(async () => button(renderer!, 'Docs').props.onClick())
    await flush()
    expect(text(renderer!)).toContain('${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke')
    expect(text(renderer!)).toContain('${API_KEY}')
    expect(text(renderer!)).not.toContain(initialApiKey)
  })

  it('aborts create on tab switch and suppresses a late one-time key completion that ignores abort', async () => {
    const pendingCreate = deferred<Response>()
    let createSignal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(detail('endpoint-1')))
      .mockResolvedValueOnce(jsonResponse({ items: [credential('credential-1')] }))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockImplementationOnce((_route, init) => {
        createSignal = init!.signal as AbortSignal
        return pendingCreate.promise
      })
    await act(async () => { renderer = create(<App />) })
    await flush()
    await act(async () => button(renderer!, 'Credentials').props.onClick())
    await flush()
    const change = async (id: string, value: string) => act(async () => {
      renderer!.root.findByProps({ id }).props.onChange({ currentTarget: { value } })
    })
    await change('credential-name', 'Late key')
    await change('credential-purpose', 'Must be erased')
    await change('credential-expires-at', '300')
    await change('credential-ip-allowlist', '')
    await change('credential-rate-limit', '30')
    await act(async () => {
      renderer!.root.findByProps({ 'aria-label': '建立 credential' }).props.onSubmit({ preventDefault: vi.fn() })
      await flush()
    })
    expect(button(renderer!, '建立 credential').props.disabled).toBe(true)
    expect(button(renderer!, '撤銷').props.disabled).toBe(true)
    await act(async () => button(renderer!, 'Overview').props.onClick())
    expect(createSignal.aborted).toBe(true)
    await act(async () => {
      pendingCreate.resolve(jsonResponse({ ...credential('credential-late'), initial_api_key: initialApiKey }, 201))
      await pendingCreate.promise
      await flush()
    })
    expect(text(renderer!)).not.toContain(initialApiKey)
    expect(text(renderer!)).not.toContain('一次性 API key')
  })

  it('revoke pending時Confirm與Cancel可靠disabled，完成後才發布readback', async () => {
    const revokePreflight = deferred<Response>()
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(detail('endpoint-1')))
      .mockResolvedValueOnce(jsonResponse({ items: [credential('credential-1')] }))
      .mockReturnValueOnce(revokePreflight.promise)
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ items: [credential('credential-1', 'revoked')] }))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await act(async () => button(renderer!, 'Credentials').props.onClick())
    await flush()
    await act(async () => button(renderer!, '撤銷').props.onClick())
    await act(async () => { void button(renderer!, '確認撤銷').props.onClick() })
    expect(button(renderer!, '確認撤銷').props.disabled).toBe(true)
    expect(button(renderer!, '取消').props.disabled).toBe(true)

    await act(async () => {
      revokePreflight.resolve(jsonResponse(session()))
      await revokePreflight.promise
      await flush()
    })
    await flush()
    expect(text(renderer!)).toContain('revoked')
    expect(text(renderer!)).not.toContain('確認撤銷')
  })

  it('revoke 204後readback失敗會fail closed，不保留舊active summary', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(detail('endpoint-1')))
      .mockResolvedValueOnce(jsonResponse({ items: [credential('credential-1')] }))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ detail: 'PRIVATE_READBACK_FAILURE' }, 503))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await act(async () => button(renderer!, 'Credentials').props.onClick())
    await flush()
    await act(async () => button(renderer!, '撤銷').props.onClick())
    await act(async () => { button(renderer!, '確認撤銷').props.onClick(); await flush() })
    await flush()
    const rendered = text(renderer!)
    expect(rendered).toContain('目前無法載入 credentials')
    expect(rendered).not.toContain('Production key')
    expect(rendered).not.toContain('PRIVATE_READBACK_FAILURE')
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
