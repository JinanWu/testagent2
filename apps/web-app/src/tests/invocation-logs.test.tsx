import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { execFileSync } from 'node:child_process'
import { ApiFormatError } from '../api/client'
import {
  LOGS_ERROR_MESSAGE,
  LOGS_FORBIDDEN_MESSAGE,
  LOGS_NOT_FOUND_MESSAGE,
  LogsError,
  PYTHON_CASEFOLD_ASCII_ENTRIES,
  getInvocationDetail,
  listInvocations,
  parseInvocationDetail,
  parseInvocationList,
  normalizePythonCasefoldAscii,
} from '../api/logs'
import App from '../App'
import { ADMIN_LOGS_ROUTE, DEFAULT_APP_ROUTE } from '../app/routes'

const RAW_OLD = 'RAW_MARKER_OLD'
const RAW_NEW = 'RAW_MARKER_NEW'
const VALID_CURSOR = `payload.${'a'.repeat(43)}`

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
    metadata: { secret: { $tombstone: {
      redaction_id: `redaction-${invocationId}`, redacted_at: 9,
    } } },
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
      sequence_number: 1,
      event_type: 'completed',
      payload: marker === null ? {} : { state: marker },
      created_at: 10.5,
    }],
    tool_calls: [] as Array<Record<string, unknown>>,
    redactions: [{
      id: `redaction-${invocationId}`,
      target_type: 'metadata',
      target_row_id: invocationId,
      json_path: '/secret',
      original_sha256: 'b'.repeat(64),
      reason: 'privacy',
      actor: { type: 'admin', id: 'admin-1' },
      audit_event_id: `audit-${invocationId}`,
      is_tombstone: true,
      redacted_at: 9,
    }],
    sensitive_hits: [] as Array<Record<string, unknown>>,
  }
}

function detailWithSensitiveHits(invocationId = 'invocation-1') {
  const body = detail(invocationId, null)
  body.tool_calls = [
    {
      id: 'tool-1', run_event_id: `event-${invocationId}`, sequence_number: 1,
      tool_name: 'safe_one', arguments: {}, outcome: 'success', result: {}, error: null,
      latency_ms: null, retry_of_tool_call_id: null, created_at: 10.6,
    },
    {
      id: 'tool-2', run_event_id: `event-${invocationId}`, sequence_number: 2,
      tool_name: 'safe_two', arguments: {}, outcome: 'success', result: {}, error: null,
      latency_ms: null, retry_of_tool_call_id: null, created_at: 10.7,
    },
  ]
  body.sensitive_hits = [
    { id: 'hit-input', target: 'input', tool_call_id: null, detector_type: 'email_detector',
      json_path: '/contact', start: 1, end: 4, detected_at: 20 },
    { id: 'hit-metadata', target: 'metadata', tool_call_id: null, detector_type: 'phone_detector',
      json_path: '', start: 0, end: 3, detected_at: 21 },
    { id: 'hit-response', target: 'response_data', tool_call_id: null, detector_type: 'card_detector',
      json_path: '/answer', start: 2, end: 8, detected_at: 22 },
    { id: 'hit-arguments', target: 'tool_arguments', tool_call_id: 'tool-1', detector_type: 'email_detector',
      json_path: '/category', start: 0, end: 2, detected_at: 23 },
    { id: 'hit-result', target: 'tool_result', tool_call_id: 'tool-2', detector_type: 'email_detector',
      json_path: '/result/content', start: 3, end: 9, detected_at: 24 },
  ]
  return body
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
    const rawList = listPage([listItem('invocation-1')], VALID_CURSOR)
    const parsedList = parseInvocationList(rawList)
    rawList.items[0] = listItem('invocation-changed')
    expect(parsedList.items[0].invocationId).toBe('invocation-1')
    expect(parsedList.nextCursor).toBe(VALID_CURSOR)

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

  it('在JSON.parse前拒絕超出safe integer的wire literal且不靜默改值', async () => {
    const wire = JSON.stringify(detail('invocation-1')).replace(
      '"metadata_size_bytes":2', '"metadata_size_bytes":9007199254740993',
    )
    fetchMock.mockResolvedValueOnce(new Response(wire, {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
    await expect(getInvocationDetail('endpoint-1', 'invocation-1')).rejects.toBeInstanceOf(LogsError)
  })

  it('以Unicode code point驗證文字、接受空pricing_version並拒絕反向時間filter', async () => {
    const emoji256 = '😀'.repeat(256)
    expect(parseInvocationDetail({
      ...detail('invocation-1'), pricing_version: '',
      run_events: [{ ...detail('invocation-1').run_events[0], event_type: emoji256 }],
    }).pricingVersion).toBe('')
    expect(() => parseInvocationDetail({
      ...detail('invocation-1'),
      run_events: [{ ...detail('invocation-1').run_events[0], event_type: `${emoji256}😀` }],
    })).toThrow(ApiFormatError)
    await expect(listInvocations('endpoint-1', { fromAt: 20, toAt: 10 })).rejects.toBeInstanceOf(LogsError)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('拒絕canonical遮蔽契約外的path與reason', () => {
    const invalid = [
      { json_path: '$.secret' },
      { json_path: '/bad~2escape' },
      { json_path: '/x'.repeat(17) },
      { json_path: `/${'x'.repeat(257)}` },
      { json_path: `/${'~0'.repeat(200)}` },
      { reason: ' '.repeat(3) },
      { reason: '\u001c\u001f' },
      { reason: '\u0085' },
      { reason: '\ufeff' },
      { reason: 'x'.repeat(257) },
      { reason: 'Bearer credential' },
      { reason: `hash ${'a'.repeat(64)}` },
      { reason: `中${'a'.repeat(64)}` },
    ]
    for (const override of invalid) {
      const body = detail('invocation-1')
      body.redactions[0] = { ...body.redactions[0], ...override }
      expect(() => parseInvocationDetail(body)).toThrow()
    }
  })

  it('遞迴要求canonical tombstone與redactions雙向exact binding', () => {
    const missingLedger = detail('invocation-1')
    missingLedger.redactions = []
    expect(() => parseInvocationDetail(missingLedger)).toThrow(ApiFormatError)

    const missingTombstone = detail('invocation-1')
    missingTombstone.metadata = { secret: null } as unknown as typeof missingTombstone.metadata
    expect(() => parseInvocationDetail(missingTombstone)).toThrow(ApiFormatError)

    for (const override of [
      { id: 'redaction-other' }, { redacted_at: 10 }, { json_path: '/other' },
    ]) {
      const mismatch = detail('invocation-1')
      mismatch.redactions[0] = { ...mismatch.redactions[0], ...override }
      expect(() => parseInvocationDetail(mismatch)).toThrow(ApiFormatError)
    }

    const escaped = detail('invocation-1')
    escaped.metadata = { 'a/b~c': escaped.metadata.secret } as unknown as typeof escaped.metadata
    escaped.redactions[0] = { ...escaped.redactions[0], json_path: '/a~1b~0c' }
    const inputRedaction = {
      id: 'redaction-input', target_type: 'invocation_input', target_row_id: 'invocation-1',
      json_path: '', original_sha256: 'c'.repeat(64), reason: 'privacy',
      actor: { type: 'admin', id: 'admin-1' }, audit_event_id: 'audit-input',
      is_tombstone: true, redacted_at: 10,
    }
    escaped.input = {
      $tombstone: { redaction_id: 'redaction-input', redacted_at: 10 },
    } as unknown as typeof escaped.input
    escaped.redactions.push(inputRedaction)
    expect(parseInvocationDetail(escaped).redactions).toHaveLength(2)
  })

  it('延伸單一exact sensitive_hits並接受五種target與bounded RFC6901位置', () => {
    const parsed = parseInvocationDetail(detailWithSensitiveHits())
    expect(parsed.sensitiveHits).toEqual([
      { id: 'hit-input', target: 'input', toolCallId: null, detectorType: 'email_detector',
        jsonPath: '/contact', start: 1, end: 4, detectedAt: 20 },
      { id: 'hit-metadata', target: 'metadata', toolCallId: null, detectorType: 'phone_detector',
        jsonPath: '', start: 0, end: 3, detectedAt: 21 },
      { id: 'hit-response', target: 'response_data', toolCallId: null, detectorType: 'card_detector',
        jsonPath: '/answer', start: 2, end: 8, detectedAt: 22 },
      { id: 'hit-arguments', target: 'tool_arguments', toolCallId: 'tool-1', detectorType: 'email_detector',
        jsonPath: '/category', start: 0, end: 2, detectedAt: 23 },
      { id: 'hit-result', target: 'tool_result', toolCallId: 'tool-2', detectorType: 'email_detector',
        jsonPath: '/result/content', start: 3, end: 9, detectedAt: 24 },
    ])
  })

  it('sensitive_hits拒絕extra/raw-ish欄位、錯誤關聯、無界值與非deterministic順序', () => {
    const invalidBodies: ReturnType<typeof detailWithSensitiveHits>[] = []
    for (const field of ['extra', 'raw', 'value', 'snippet', 'hash', 'audit_event_id']) {
      const body = detailWithSensitiveHits()
      body.sensitive_hits[0] = { ...body.sensitive_hits[0], [field]: RAW_NEW }
      invalidBodies.push(body)
    }
    for (const override of [
      { target: 'credential' }, { target: 'input', tool_call_id: 'tool-1' },
      { target: 'tool_result', tool_call_id: null }, { tool_call_id: 'tool-missing' },
      { detector_type: 'RawDetector' }, { detector_type: `a${'b'.repeat(128)}` },
      { json_path: '$.contact' }, { json_path: '/bad~2escape' },
      { json_path: `/${'中'.repeat(2731)}` }, { start: -1 }, { start: 4, end: 4 },
      { end: 2**53 }, { detected_at: -1 }, { detected_at: 253_402_300_800 },
    ]) {
      const body = detailWithSensitiveHits()
      body.sensitive_hits[0] = { ...body.sensitive_hits[0], ...override }
      invalidBodies.push(body)
    }
    const duplicateId = detailWithSensitiveHits()
    duplicateId.sensitive_hits[1].id = duplicateId.sensitive_hits[0].id
    invalidBodies.push(duplicateId)
    const reordered = detailWithSensitiveHits()
    reordered.sensitive_hits = [reordered.sensitive_hits[1], reordered.sensitive_hits[0],
      ...reordered.sensitive_hits.slice(2)]
    invalidBodies.push(reordered)
    const toolSequenceReordered = detailWithSensitiveHits()
    toolSequenceReordered.sensitive_hits = [
      ...toolSequenceReordered.sensitive_hits.slice(0, 3),
      { ...toolSequenceReordered.sensitive_hits[3], tool_call_id: 'tool-2' },
      { ...toolSequenceReordered.sensitive_hits[4], target: 'tool_arguments', tool_call_id: 'tool-1' },
    ]
    invalidBodies.push(toolSequenceReordered)
    const tooMany = detailWithSensitiveHits()
    tooMany.sensitive_hits = Array.from({ length: 1025 }, (_, index) => ({
      ...tooMany.sensitive_hits[0], id: `hit-${index}`, json_path: `/p${String(index).padStart(4, '0')}`,
    }))
    invalidBodies.push(tooMany)
    for (const body of invalidBodies) expect(() => parseInvocationDetail(body)).toThrow(ApiFormatError)
  })

  it('拒絕storage不可能產生的child語意與raw unsafe integer', () => {
    const base = detail('invocation-1')
    const event = base.run_events[0]
    for (const run_events of [
      [{ ...event, sequence_number: 0 }],
      [{ ...event, payload: [] }],
      [{ ...event, payload: { number: 2**53 } }],
    ]) expect(() => parseInvocationDetail({ ...base, run_events })).toThrow(ApiFormatError)
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

  it.each([
    { ...detail('invocation-1'), input: { note: 'paßword' } },
    { ...detail('invocation-1'), input: { note: 'paſſword' } },
    { ...detail('invocation-1'), input: { cooKie: 'marker' } },
    { ...detail('invocation-1'), input: { authorizatioŉ: 'marker' } },
    { ...detail('invocation-1'), input: { autẖorization: 'marker' } },
    { ...detail('invocation-1'), input: { auẗhorization: 'marker' } },
    { ...detail('invocation-1'), input: { note: 'passẘord=marker' } },
    { ...detail('invocation-1'), input: { apikeẙ: 'marker' } },
    { ...detail('invocation-1'), input: { mẚsterkey: 'marker' } },
  ])('frontend與Python casefold semantic secret判定一致', (body) => {
    expect(() => parseInvocationDetail(body)).toThrow(ApiFormatError)
  })

  it('casefold小表由全Unicode code point機械證明完整且含已知差異', () => {
    const python = process.env.A21_BROWSER_PYTHON ?? process.env.PYTHON ?? 'python3'
    const environment = { ...process.env }
    for (const name of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTHONUSERBASE']) delete environment[name]
    environment.PYTHONNOUSERSITE = '1'
    const script = [
      'import json,sys',
      'rows=[]',
      'for cp in range(128,sys.maxunicode+1):',
      " c=chr(cp); folded=''.join(x for x in c.casefold() if ('a'<=x<='z') or ('0'<=x<='9'))",
      ' if folded: rows.append([cp,folded])',
      'print(json.dumps(rows,separators=(\",\",\":\")))',
    ].join('\n')
    const generated = JSON.parse(execFileSync(python, ['-c', script], {
      encoding: 'utf8', env: environment,
    })) as Array<[number, string]>
    expect(generated).toEqual(PYTHON_CASEFOLD_ASCII_ENTRIES.map((entry) => [...entry]))
    const differences = generated.filter(([codePoint, folded]) =>
      String.fromCodePoint(codePoint).toLowerCase().replace(/[^a-z0-9]/g, '') !== folded)
    expect(differences).toEqual(expect.arrayContaining([
      [0x0149, 'n'], [0x01f0, 'j'], [0x1e96, 'h'], [0x1e97, 't'],
      [0x1e98, 'w'], [0x1e99, 'y'], [0x1e9a, 'a'],
    ]))
    for (const [codePoint, folded] of generated) {
      expect(normalizePythonCasefoldAscii(String.fromCodePoint(codePoint))).toBe(folded)
    }
    expect(generated).toEqual(expect.arrayContaining([
      [0x00df, 'ss'], [0x0130, 'i'], [0x0149, 'n'], [0x017f, 's'], [0x01f0, 'j'],
      [0x1e96, 'h'], [0x1e97, 't'], [0x1e98, 'w'], [0x1e99, 'y'], [0x1e9a, 'a'],
      [0x212a, 'k'],
    ]))
  })

  it('frontend鏡射backend semantic value matrix並保留合法raw', () => {
    const invalid = [
      'note Authorization', 'Cookie', 'signing-private_key', 'client_secret=TOPSECRET',
      'api_key=TOPSECRET', 'API KEY TOPSECRET', 'access_token=TOPSECRET',
      'refresh_token=TOPSECRET', 'master_key=TOPSECRET', 'credential_hash=TOPSECRET',
      'credential_ciphertext=TOPSECRET', 'password=TOPSECRET', 'secret_key=TOPSECRET',
      'provider-secret TOPSECRET',
    ]
    for (const value of invalid) {
      expect(() => parseInvocationDetail({ ...detail('invocation-1'), input: { note: value } }))
        .toThrow(ApiFormatError)
    }
    const valid = detail('invocation-1')
    valid.input = { route: '/api/v1/items', text: 'bearer market analysis' } as unknown as typeof valid.input
    expect(parseInvocationDetail(valid).input).toEqual(valid.input)
  })

  it('只發exact same-origin credentialed list/detail GET', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(listPage([listItem('invocation-1')], VALID_CURSOR)))
      .mockResolvedValueOnce(jsonResponse(detail('invocation-1')))

    await expect(listInvocations('endpoint-1', {
      status: 'failed', errorCode: 'timeout', limit: 50,
    })).resolves.toMatchObject({ nextCursor: VALID_CURSOR })
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
      .mockResolvedValueOnce(jsonResponse(listPage([listItem('invocation-1', false)])))
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
    await act(async () => button(renderer!, 'invocation-1 — failed').props.onClick())
    const source = text(renderer!)
    expect(source).toContain('部分內容已依政策遮蔽')
    expect(source).toContain('已遮蔽')
    expect(source).toContain('遮蔽紀錄')
    expect(source).toContain('/secret')
    expect(source).toContain('privacy')
    expect(source).not.toContain('$tombstone')
    expect(source).not.toContain('redaction_id')
    expect(source).not.toContain('redacted_at')
    expect(source).not.toContain('已刪除或無資料')
    expect(source).not.toMatch(/export|download|copy all|copy-all|share link|raw search|匯出|下載|複製全部|分享連結|全文搜尋/i)
  })

  it('Admin detail只顯示敏感命中安全位置且空值有固定顯示', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session('admin')))
      .mockResolvedValueOnce(jsonResponse(listPage([listItem('invocation-1')])))
      .mockResolvedValueOnce(jsonResponse(detailWithSensitiveHits()))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await submitList(renderer!)
    await act(async () => button(renderer!, 'invocation-1 — failed').props.onClick())
    const source = text(renderer!)
    expect(source).toContain('敏感資料命中')
    for (const value of ['輸入', 'Metadata', '回應資料', '工具參數', '工具結果',
      'tool-1', '/contact', '根節點', '1–4', 'email_detector', '1970-01-01T00:00:20.000Z']) {
      expect(source).toContain(value)
    }
    expect(source).toContain('工具呼叫識別碼')
    expect(source).toContain('無資料')
    expect(source).not.toContain(RAW_NEW)
    expect(source).not.toMatch(/"(?:raw|snippet|hash|audit[_ -]?id)"|稽核識別碼|複製|下載|匯出/i)
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
