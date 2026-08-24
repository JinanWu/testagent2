import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiFormatError, ApiResponseError, apiRequest, parseSafeJson, type ApiRoute } from '../api/client'
import {
  createCredential,
  createDraft,
  createEndpointVersion,
  getOwnerEndpoint,
  getOwnerEndpointDocs,
  listCredentials,
  listOwnerEndpoints,
  parseCredentialCreateReceipt,
  parseCredentialList,
  parseDraftReceipt,
  parseEndpointDocs,
  parseOwnerEndpointDetail,
  parseOwnerEndpointList,
  parsePublishReceipt,
  parseVersionReceipt,
  publishEndpoint,
  revokeCredential,
} from '../api/endpoints'

const encoder = new TextEncoder()
const initialApiKey = `pk_${'A'.repeat(43)}`
const csrfTwo = 'a'.repeat(32)
const csrfThree = 'b'.repeat(32)
const csrfFour = 'c'.repeat(32)
const csrfNext = 'd'.repeat(32)
const csrfLast = 'e'.repeat(32)
const csrfError = 'f'.repeat(32)
const csrfRestored = 'g'.repeat(32)
const endpointItem = {
  endpoint_id: 'endpoint-1', slug: 'safe-api', status: 'active',
  current_version_id: 'version-1', current_version_number: 1, updated_at: 20,
}
const endpointDetail = {
  endpoint_id: 'endpoint-1', owner_user_id: 'owner-1', slug: 'safe-api', status: 'active',
  current_version_id: 'version-1', current_version_number: 1, created_at: 10, updated_at: 20,
}
const credential = {
  credential_id: 'credential-1', name: 'production', purpose: 'client calls', key_prefix: initialApiKey.slice(0, 12),
  key_last4: initialApiKey.slice(-4), status: 'active', expires_at: 200, last_used_at: null, created_at: 100,
  revoked_at: null, ip_allowlist: ['192.0.2.0/24'], rate_limit_requests: 60,
}
const draftReceipt = {
  draft_id: 'draft-1', expires_at: 200,
  preview: {
    endpoint_name: 'Safe API', suggested_slug: 'safe-api', behavior_summary: 'summary',
    selected_skills: ['alpha'], recommended_tools: [], tool_capabilities: {}, system_prompt: 'prompt',
    input_schema: null, response_schema: { type: 'object' }, human_docs: 'docs',
    rate_limit: { endpoint_per_minute: 60, credential_per_minute: 30 }, warnings: [],
  },
}
const publishReceipt = {
  endpoint_id: 'endpoint-1', version_id: 'version-1', version_number: 1,
  status: 'active', initial_api_key: initialApiKey,
}
const versionReceipt = {
  endpoint_id: 'endpoint-1', version_id: 'version-2', version_number: 2,
  current_version_id: 'version-2', schema_changed: false,
}
const docsErrors = [
  ['endpoint_not_found', 404, '找不到 endpoint slug。'],
  ['invalid_api_key', 401, 'API key 無效。'],
  ['api_key_expired', 401, 'API key 已過期。'],
  ['endpoint_disabled', 403, 'Endpoint 已停用。'],
  ['endpoint_archived', 410, 'Endpoint 已封存。'],
  ['input_schema_invalid', 422, 'Input 不符合 schema。'],
  ['model_output_schema_invalid', 502, '模型輸出不符合 response schema。'],
  ['rate_limit_exceeded', 429, '呼叫頻率超過限制。'],
  ['model_timeout', 504, '模型供應商逾時。'],
  ['tool_execution_failed', 502, '工具執行失敗。'],
  ['tool_timeout', 504, '工具執行逾時。'],
  ['endpoint_misconfigured', 500, 'Endpoint 設定錯誤。'],
  ['internal_error', 500, '伺服器內部錯誤。'],
].map(([code, status, message]) => ({ code, status, message }))
const docs = {
  endpoint: { id: 'endpoint-1', slug: 'safe-api', version: 1, status: 'active' },
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
  response_schema: { type: 'object' },
  rate_limit: { requests: 60, window_seconds: 60 },
  examples: {
    curl: "curl -X POST '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke' -H 'Authorization: Bearer ${API_KEY}' -H 'Content-Type: application/json' --data '{\"input\":{},\"session_id\":\"${SESSION_ID}\",\"metadata\":{\"endpoint_id\":\"${ENDPOINT_ID}\"}}'",
    python: "import json\nimport urllib.request\nurl = '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke'\npayload = {'input': {}, 'session_id': '${SESSION_ID}', 'metadata': {'endpoint_id': '${ENDPOINT_ID}'}}\nrequest = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Authorization': 'Bearer ${API_KEY}', 'Content-Type': 'application/json'}, method='POST')\nwith urllib.request.urlopen(request) as response:\n    print(response.read().decode('utf-8'))",
  },
  errors: docsErrors,
}

function jsonResponse(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(value), {
    status, headers: { 'content-type': 'application/json', ...headers },
  })
}

function hostileObject(value: Record<string, unknown>): Record<string, unknown> {
  return Object.assign(Object.create({ hostile: true }), value) as Record<string, unknown>
}

function deepClone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }

describe('strict management DTO parsers', () => {
  it('deeply reconstructs every successful DTO', () => {
    expect(parseOwnerEndpointList({ items: [endpointItem], next_cursor: null })).toEqual({
      items: [{ endpointId: 'endpoint-1', slug: 'safe-api', status: 'active', currentVersionId: 'version-1', currentVersionNumber: 1, updatedAt: 20 }],
      nextCursor: null,
    })
    expect(parseOwnerEndpointDetail(endpointDetail)).toEqual({
      endpointId: 'endpoint-1', ownerUserId: 'owner-1', slug: 'safe-api', status: 'active',
      currentVersionId: 'version-1', currentVersionNumber: 1, createdAt: 10, updatedAt: 20,
    })
    expect(parseDraftReceipt(draftReceipt)).toMatchObject({ draftId: 'draft-1', preview: { endpointName: 'Safe API' } })
    expect(parsePublishReceipt(publishReceipt)).toEqual({ endpointId: 'endpoint-1', versionId: 'version-1', versionNumber: 1, status: 'active', initialApiKey: publishReceipt.initial_api_key })
    expect(parseVersionReceipt(versionReceipt, 'endpoint-1')).toEqual({ endpointId: 'endpoint-1', versionId: 'version-2', versionNumber: 2, currentVersionId: 'version-2', schemaChanged: false })
    expect(parseCredentialList({ items: [credential] })).toMatchObject({ items: [{ credentialId: 'credential-1', rateLimitRequests: 60 }] })
    expect(parseCredentialCreateReceipt({ ...credential, initial_api_key: publishReceipt.initial_api_key })).toMatchObject({ credentialId: 'credential-1', initialApiKey: publishReceipt.initial_api_key })
    expect(parseEndpointDocs(docs)).toMatchObject({ endpoint: { id: 'endpoint-1', version: 1 }, rateLimit: { requests: 60 } })
  })

  it.each([
    ['owner list outer', parseOwnerEndpointList, { items: [endpointItem], next_cursor: null, extra: true }],
    ['owner list item', parseOwnerEndpointList, { items: [{ ...endpointItem, extra: true }], next_cursor: null }],
    ['owner detail', parseOwnerEndpointDetail, { ...endpointDetail, extra: true }],
    ['draft preview nested', parseDraftReceipt, { ...draftReceipt, preview: { ...draftReceipt.preview, rate_limit: { ...draftReceipt.preview.rate_limit, extra: 1 } } }],
    ['publish', parsePublishReceipt, { ...publishReceipt, extra: true }],
    ['version', (value: unknown) => parseVersionReceipt(value, 'endpoint-1'), { ...versionReceipt, extra: true }],
    ['credential list item', parseCredentialList, { items: [{ ...credential, extra: true }] }],
    ['credential create', parseCredentialCreateReceipt, { ...credential, initial_api_key: publishReceipt.initial_api_key, extra: true }],
    ['docs nested', parseEndpointDocs, { ...docs, endpoint: { ...docs.endpoint, extra: true } }],
  ])('rejects extra keys at %s', (_name, parser, value) => {
    expect(() => parser(value)).toThrow(ApiFormatError)
  })

  it.each([
    ['custom prototype', hostileObject({ items: [endpointItem], next_cursor: null })],
    ['custom nested prototype', { items: [hostileObject(endpointItem)], next_cursor: null }],
    ['symbol key', { items: [endpointItem], next_cursor: null, [Symbol('extra')]: true }],
  ])('rejects %s without trusting inherited state', (_name, value) => {
    expect(() => parseOwnerEndpointList(value)).toThrow(ApiFormatError)
  })

  it('rejects accessors without invoking them', () => {
    let reads = 0
    const value = Object.defineProperties({}, {
      items: { enumerable: true, configurable: true, get() { reads += 1; return [endpointItem] } },
      next_cursor: { enumerable: true, configurable: true, writable: true, value: null },
    })
    expect(() => parseOwnerEndpointList(value)).toThrow(ApiFormatError)
    expect(reads).toBe(0)
  })

  it.each([
    ['unsafe endpoint version', { items: [{ ...endpointItem, current_version_number: Number.MAX_SAFE_INTEGER + 1 }], next_cursor: null }],
    ['non-finite time', { ...endpointDetail, updated_at: Number.POSITIVE_INFINITY }],
    ['mismatched nullable version pair', { ...endpointDetail, current_version_id: null }],
    ['wrong receipt endpoint', versionReceipt],
    ['non-canonical current version', { ...versionReceipt, current_version_id: 'version-3' }],
  ])('rejects invalid scalar relation: %s', (name, value) => {
    const parser = name.includes('endpoint version') ? parseOwnerEndpointList
      : name === 'non-finite time' || name.includes('nullable') ? parseOwnerEndpointDetail
        : (entry: unknown) => parseVersionReceipt(entry, name === 'wrong receipt endpoint' ? 'other-endpoint' : 'endpoint-1')
    expect(() => parser(value)).toThrow(ApiFormatError)
  })

  it('rejects oversized collections and deep JSON trees', () => {
    expect(() => parseOwnerEndpointList({ items: Array.from({ length: 101 }, () => endpointItem), next_cursor: null })).toThrow(ApiFormatError)
    expect(() => parseCredentialList({ items: Array.from({ length: 10_001 }, () => credential) })).toThrow(ApiFormatError)
    let nested: Record<string, unknown> = { type: 'string' }
    for (let index = 0; index < 34; index += 1) nested = { nested }
    expect(() => parseEndpointDocs({ ...docs, response_schema: nested })).toThrow(ApiFormatError)
  })

  it('returns detached trees that cannot mutate the source', () => {
    const source = deepClone(docs)
    const parsed = parseEndpointDocs(source)
    ;(source.response_schema as Record<string, unknown>).changed = true
    expect(parsed.responseSchema).toEqual({ type: 'object' })
  })

  it('拒絕非canonical一次性API key與不一致的prefix/last4摘要', () => {
    const alternateCanonicalKey = 'pk_AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE'
    expect(parsePublishReceipt({ ...publishReceipt, initial_api_key: alternateCanonicalKey }).initialApiKey)
      .toBe(alternateCanonicalKey)
    expect(() => parsePublishReceipt({ ...publishReceipt, initial_api_key: 'not-a-platform-key' }))
      .toThrow(ApiFormatError)
    expect(() => parsePublishReceipt({ ...publishReceipt, initial_api_key: `${initialApiKey.slice(0, -1)}B` }))
      .toThrow(ApiFormatError)
    expect(() => parseCredentialCreateReceipt({
      ...credential, initial_api_key: publishReceipt.initial_api_key, key_prefix: 'pk_mismatch',
    })).toThrow(ApiFormatError)
    expect(() => parseCredentialCreateReceipt({
      ...credential, initial_api_key: publishReceipt.initial_api_key, key_last4: 'WXYZ',
    })).toThrow(ApiFormatError)
  })

  it('拒絕wire JSON重複物件鍵，避免JSON.parse先覆蓋後形成假exact形狀', () => {
    expect(() => parseSafeJson('{"items":[],"items":[{"endpoint_id":"shadow"}],"next_cursor":null}'))
      .toThrow(ApiFormatError)
  })

  it('保留schema中的合法__proto__自有property且不改變輸出prototype', () => {
    const withPrototypeProperty = deepClone(docs)
    ;(withPrototypeProperty as { response_schema: unknown }).response_schema = JSON.parse(
      '{"type":"object","properties":{"__proto__":{"type":"string"}}}',
    ) as Record<string, unknown>
    const parsed = parseEndpointDocs(withPrototypeProperty)
    const properties = parsed.responseSchema.properties as Record<string, unknown>
    expect(Object.getPrototypeOf(properties)).toBe(Object.prototype)
    expect(Object.prototype.hasOwnProperty.call(properties, '__proto__')).toBe(true)
    expect(properties.__proto__).toEqual({ type: 'string' })
  })
})

describe('management transport and routes', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock) })
  afterEach(() => { vi.unstubAllGlobals() })

  it('uses exact owner list/detail routes and credentials include', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: [endpointItem], next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse(endpointDetail))
    await listOwnerEndpoints({ scope: 'owner', limit: 20, cursor: 'abc_DEF' })
    await getOwnerEndpoint('endpoint-1')
    expect(fetchMock.mock.calls.map(([route, init]) => [route, init?.method, init?.credentials])).toEqual([
      ['/api/published-endpoints?scope=owner&limit=20&cursor=abc_DEF', 'GET', 'include'],
      ['/api/published-endpoints/endpoint-1', 'GET', 'include'],
    ])
  })

  it('GET成功response也交付session restoration successor', async () => {
    const successors: string[] = []
    fetchMock.mockResolvedValueOnce(jsonResponse(
      { items: [endpointItem], next_cursor: null }, 200, { 'X-CSRF-Token': csrfRestored },
    ))
    await listOwnerEndpoints({}, { onCsrfSuccessor: (token: string) => successors.push(token) })
    expect(successors).toEqual([csrfRestored])
  })

  it('successor header存在但格式無效時fail closed而非靜默沿用舊token', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(
      { items: [endpointItem], next_cursor: null }, 200, { 'X-CSRF-Token': 'x'.repeat(4097) },
    ))
    await expect(listOwnerEndpoints()).rejects.toBeInstanceOf(ApiFormatError)
  })

  it('拒絕Fetch合併後的重複successor headers', async () => {
    const headers = new Headers([['content-type', 'application/json'],
      ['X-CSRF-Token', csrfRestored], ['X-CSRF-Token', csrfNext]])
    fetchMock.mockResolvedValueOnce(new Response(
      JSON.stringify({ items: [endpointItem], next_cursor: null }), { status: 200, headers },
    ))
    await expect(listOwnerEndpoints()).rejects.toBeInstanceOf(ApiFormatError)
  })

  it('uses exact draft/publish/version JSON bodies, CSRF and successor callback', async () => {
    const successors: string[] = []
    fetchMock
      .mockResolvedValueOnce(jsonResponse(draftReceipt, 201, { 'X-CSRF-Token': csrfTwo }))
      .mockResolvedValueOnce(jsonResponse(publishReceipt, 201, { 'X-CSRF-Token': csrfThree }))
      .mockResolvedValueOnce(jsonResponse(versionReceipt, 201, { 'X-CSRF-Token': csrfFour }))
    const options = { onCsrfSuccessor: (token: string) => successors.push(token) }
    await createDraft({ originalRequirementText: 'Create API', selectedSkills: ['alpha'], responseMode: 'text' }, 'csrf-1', options)
    await publishEndpoint({ draftId: 'draft-1', slug: 'safe-api', configurationConfirmation: { system_prompt: 'safe' } }, csrfTwo, options)
    await createEndpointVersion('endpoint-1', { configuration: { system_prompt: 'new' } }, csrfThree, options)
    expect(successors).toEqual([csrfTwo, csrfThree, csrfFour])
    expect(fetchMock.mock.calls.map(([route, init]) => [route, init?.method, new Headers(init?.headers).get('X-CSRF-Token'), init?.body])).toEqual([
      ['/api/published-endpoints/draft', 'POST', 'csrf-1', JSON.stringify({ original_requirement_text: 'Create API', selected_skills: ['alpha'], response_mode: 'text' })],
      ['/api/published-endpoints', 'POST', csrfTwo, JSON.stringify({ draft_id: 'draft-1', slug: 'safe-api', configuration_confirmation: { system_prompt: 'safe' } })],
      ['/api/published-endpoints/endpoint-1/versions', 'POST', csrfThree, JSON.stringify({ configuration: { system_prompt: 'new' } })],
    ])
  })

  it('uses exact credential list/create/revoke and owner docs routes', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: [credential] }))
      .mockResolvedValueOnce(jsonResponse({ ...credential, initial_api_key: publishReceipt.initial_api_key }, 201, { 'X-CSRF-Token': csrfNext }))
      .mockResolvedValueOnce(new Response(null, { status: 204, headers: { 'X-CSRF-Token': csrfLast } }))
      .mockResolvedValueOnce(jsonResponse(docs))
    const successors: string[] = []
    await listCredentials('endpoint-1')
    await createCredential('endpoint-1', {
      name: 'production', purpose: 'client calls', expiresAt: 200,
      ipAllowlist: ['192.0.2.0/24'], rateLimitRequests: 60,
    }, 'csrf-current', { onCsrfSuccessor: (token) => successors.push(token) })
    await revokeCredential('endpoint-1', 'credential-1', csrfNext, { onCsrfSuccessor: (token) => successors.push(token) })
    await getOwnerEndpointDocs('endpoint-1')
    expect(successors).toEqual([csrfNext, csrfLast])
    expect(fetchMock.mock.calls.map(([route, init]) => [route, init?.method, init?.body])).toEqual([
      ['/api/published-endpoints/endpoint-1/credentials', 'GET', undefined],
      ['/api/published-endpoints/endpoint-1/credentials', 'POST', JSON.stringify({ name: 'production', purpose: 'client calls', expires_at: 200, ip_allowlist: ['192.0.2.0/24'], rate_limit_requests: 60 })],
      ['/api/published-endpoints/endpoint-1/credentials/credential-1/revoke', 'POST', undefined],
      ['/api/published-endpoints/endpoint-1/docs', 'GET', undefined],
    ])
  })

  it.each([401, 403, 404, 409, 422, 500, 503])('never consumes or leaks error body for status %s, while delivering successor', async (status) => {
    const marker = `PRIVATE_SERVER_BODY_${status}`
    let pulls = 0
    const stream = new ReadableStream<Uint8Array>({ pull() { pulls += 1; throw new Error(marker) } }, { highWaterMark: 0 })
    fetchMock.mockResolvedValueOnce(new Response(stream, { status, headers: { 'content-type': 'application/json', 'X-CSRF-Token': csrfError } }))
    const successors: string[] = []
    let error: unknown
    try {
      await apiRequest('/api/published-endpoints/draft' as ApiRoute, {
        method: 'POST', body: '{}', csrfToken: 'csrf-current', expectedStatus: 201,
        onCsrfSuccessor: (token) => successors.push(token),
      })
    } catch (caught) { error = caught }
    expect(error).toBeInstanceOf(ApiResponseError)
    expect(String(error)).not.toContain(marker)
    expect(successors).toEqual([csrfError])
    expect(pulls).toBe(0)
  })

  it('preserves AbortError before fetch and during active response streaming', async () => {
    const before = new AbortController(); before.abort()
    await expect(listOwnerEndpoints({}, { signal: before.signal })).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).not.toHaveBeenCalled()

    const during = new AbortController()
    let blocked!: () => void
    const reached = new Promise<void>((resolve) => { blocked = resolve })
    fetchMock.mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
      pull() { blocked(); return new Promise<void>(() => {}) },
    }, { highWaterMark: 0 }), { status: 200, headers: { 'content-type': 'application/json' } }))
    const request = listOwnerEndpoints({}, { signal: during.signal })
    await reached; during.abort()
    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('rejects route injection and request/body overflow before fetch', async () => {
    await expect(getOwnerEndpoint('../admin')).rejects.toBeInstanceOf(ApiFormatError)
    await expect(createDraft({ originalRequirementText: 'x'.repeat(16_385), selectedSkills: ['alpha'], responseMode: 'text' }, 'csrf', {})).rejects.toBeInstanceOf(ApiFormatError)
    await expect(publishEndpoint({ draftId: 'draft', slug: 'safe', configurationConfirmation: { huge: 'x'.repeat(40_000) } }, 'csrf', {})).rejects.toBeInstanceOf(ApiFormatError)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('bounds management response streams and cancels on overflow', async () => {
    let cancelled = false
    fetchMock.mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array(1024 * 1024 + 1)) },
      cancel() { cancelled = true },
    }), { status: 200, headers: { 'content-type': 'application/json' } }))
    await expect(listOwnerEndpoints()).rejects.toBeInstanceOf(ApiFormatError)
    expect(cancelled).toBe(true)
  })
})
