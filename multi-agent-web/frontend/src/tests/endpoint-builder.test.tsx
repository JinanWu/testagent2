import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { createPublishEndpointOperation } from '../app/sessionAuthority'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}
function session() {
  return { user: { id: 'owner-1', username: 'owner', role: 'member' }, csrf_token: 'csrf-safe-value' }
}
const skills = { skills: [
  { id: 'zeta', name: 'Zeta', category: 'ops', description: 'Zeta summary' },
  { id: 'alpha', name: 'Alpha', category: 'core', description: 'Alpha summary' },
] }
const preview = {
  endpoint_name: 'Safe API', suggested_slug: 'safe-api', behavior_summary: 'Safe behavior',
  selected_skills: ['alpha'], recommended_tools: ['skills_list'], tool_capabilities: { skills_list: 'read skills' },
  system_prompt: 'SERVER_SYSTEM', input_schema: { type: 'object', properties: { input: { type: 'string' } } },
  response_schema: { type: 'object', properties: { answer: { type: 'string' } } },
  human_docs: 'SERVER_DOCS', rate_limit: { endpoint_per_minute: 60, credential_per_minute: 10 },
  warnings: ['SERVER_WARNING'],
}
const draft = { draft_id: 'draft-1', expires_at: 1000, preview }
const initialApiKey = `pk_${'A'.repeat(43)}`
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
function input(renderer: ReactTestRenderer, id: string) { return renderer.root.findByProps({ id }) }
/*
 * 「查看」按鈕的可見文字不再包含技能名稱（名稱太長會撐爆卡片），
 * 完整名稱改放 aria-label，因此以 aria-label 取節點。斷言本身不變。
 */
function 查看按鈕(renderer: ReactTestRenderer, name: string) {
  return renderer.root.findByProps({ 'aria-label': `查看 ${name}` })
}
function skillCard(renderer: ReactTestRenderer, name: string) {
  return renderer.root.findAllByType('li').find((node) =>
    typeof node.props.className === 'string' &&
    node.props.className.includes('cursor-pointer') &&
    node.findAllByType('label').some((label) => label.children.flat().join('').includes(name)),
  )!
}

async function prepareDraft(renderer: ReactTestRenderer, fetchMock: ReturnType<typeof vi.fn>) {
  await act(async () => { input(renderer, 'skill-alpha').props.onChange({ target: { checked: true } }) })
  await act(async () => { input(renderer, 'endpoint-requirement').props.onChange({ target: { value: 'Build a safe API' } }) })
  await act(async () => { button(renderer, '建立 Draft').props.onClick(); await flush() })
  await flush()
  expect(fetchMock.mock.calls.some((call) => call[0] === '/api/published-endpoints/draft')).toBe(true)
}

describe('A22-03 draft-driven endpoint Builder', () => {
  const fetchMock = vi.fn<typeof fetch>()
  let renderer: ReactTestRenderer | undefined
  let pathname = '/endpoints/new'
  let popstate: (() => void) | undefined
  const replaceState = vi.fn((_state: unknown, _title: string, path: string) => { pathname = path })

  beforeEach(() => {
    pathname = '/endpoints/new'
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

  it('拒絕custom prototype與inherited toJSON改寫sealed五鍵confirmation', () => {
    const hostile = Object.assign(Object.create({
      toJSON: () => ({ attacker_controlled: true }),
    }), {
      system_prompt: 'safe', input_schema: null, response_schema: {}, human_docs: 'docs', rate_limit: {},
    })
    expect(() => createPublishEndpointOperation(
      'draft-1', 'safe-api', hostile,
    )).toThrow(expect.objectContaining({ name: 'AbortError' }))
  })

  it('uses authoritative sorted skills and loads authoritative skill detail', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(skills))
      .mockResolvedValueOnce(jsonResponse({ ...skills.skills[1], content: 'AUTHORITATIVE_SKILL_CONTENT' }))
    await act(async () => { renderer = create(<App />) })
    await flush()
    const labels = renderer!.root.findAllByType('label').map((node) => node.children.flat().join(''))
    expect(labels.findIndex((label) => label.includes('Alpha'))).toBeLessThan(labels.findIndex((label) => label.includes('Zeta')))
    await act(async () => { 查看按鈕(renderer!, 'Alpha').props.onClick(); await flush() })
    expect(text(renderer!)).toContain('AUTHORITATIVE_SKILL_CONTENT')
    expect(fetchMock).toHaveBeenLastCalledWith('/api/skills/alpha', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  it('creates a draft through fresh session authority and renders readonly server preview', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(skills))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(draft, 201))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await prepareDraft(renderer!, fetchMock)

    const draftCall = fetchMock.mock.calls.find(([route]) => route === '/api/published-endpoints/draft')!
    expect(JSON.parse(String(draftCall[1]?.body))).toEqual({
      original_requirement_text: 'Build a safe API', selected_skills: ['alpha'], response_mode: 'text',
    })
    expect(new Headers(draftCall[1]?.headers).get('X-CSRF-Token')).toBe('csrf-safe-value')
    const rendered = text(renderer!)
    for (const value of ['SERVER_SYSTEM', 'SERVER_DOCS', 'SERVER_WARNING', 'endpoint_per_minute', 'credential_per_minute']) {
      expect(rendered).toContain(value)
    }
    expect(renderer!.root.findAllByProps({ 'data-preview-field': true }).every((node) => node.props.contentEditable !== true)).toBe(true)
    expect(input(renderer!, 'endpoint-requirement').props.disabled).toBe(true)
    expect(input(renderer!, 'endpoint-response-mode').props.disabled).toBe(true)
    expect(input(renderer!, 'skill-alpha').props.disabled).toBe(true)
    expect(button(renderer!, '建立 Draft').props.disabled).toBe(true)
  })

  it('selects a skill when clicking the card body outside the checkbox', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(skills))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(draft, 201))
    await act(async () => { renderer = create(<App />) })
    await flush()

    await act(async () => { input(renderer!, 'endpoint-requirement').props.onChange({ target: { value: 'Build a safe API' } }) })
    await act(async () => {
      skillCard(renderer!, 'Alpha').props.onClick({ target: { closest: () => null } })
    })
    await act(async () => { button(renderer!, '建立 Draft').props.onClick(); await flush() })
    await flush()

    const draftCall = fetchMock.mock.calls.find(([route]) => route === '/api/published-endpoints/draft')!
    expect(JSON.parse(String(draftCall[1]?.body)).selected_skills).toEqual(['alpha'])
  })

  it('clears stale validation errors after navigating back or editing', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(skills))
    await act(async () => { renderer = create(<App />) })
    await flush()

    await act(async () => { input(renderer!, 'endpoint-requirement').props.onChange({ target: { value: 'Build a safe API' } }) })
    await act(async () => { button(renderer!, '下一步：選擇 Skills').props.onClick() })
    await act(async () => { button(renderer!, '建立 Draft').props.onClick(); await flush() })
    expect(text(renderer!)).toContain('請至少選擇 1 個 Skill。')

    await act(async () => { button(renderer!, '上一步').props.onClick() })
    expect(text(renderer!)).not.toContain('請至少選擇 1 個 Skill。')
    await act(async () => { button(renderer!, '下一步：選擇 Skills').props.onClick() })
    expect(text(renderer!)).not.toContain('請至少選擇 1 個 Skill。')

    await act(async () => { button(renderer!, '建立 Draft').props.onClick(); await flush() })
    expect(text(renderer!)).toContain('請至少選擇 1 個 Skill。')
    await act(async () => { input(renderer!, 'skill-alpha').props.onChange({ target: { checked: true } }) })
    expect(text(renderer!)).not.toContain('請至少選擇 1 個 Skill。')
  })

  it('需求內容過長時禁止進入下一步並顯示固定提示', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(skills))
    await act(async () => { renderer = create(<App />) })
    await flush()

    await act(async () => { input(renderer!, 'endpoint-requirement').props.onChange({ target: { value: '密'.repeat(6000) } }) })

    expect(text(renderer!)).toContain('內容太長，請縮短後再送出')
    expect(button(renderer!, '下一步：選擇 Skills').props.disabled).toBe(true)
    const before = fetchMock.mock.calls.length
    expect(fetchMock).toHaveBeenCalledTimes(before)
  })

  it('publishes exact detached five-key confirmation and shows the initial key only in current success state', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(skills))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(draft, 201))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse({
        endpoint_id: 'endpoint-1', version_id: 'version-1', version_number: 1, status: 'active', initial_api_key: initialApiKey,
      }, 201))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await prepareDraft(renderer!, fetchMock)
    await act(async () => { button(renderer!, '發布端點').props.onClick(); await flush() })
    await flush()

    const publishCall = fetchMock.mock.calls.find(([route]) => route === '/api/published-endpoints')!
    const body = JSON.parse(String(publishCall[1]?.body))
    expect(Object.keys(body.configuration_confirmation)).toEqual([
      'system_prompt', 'input_schema', 'response_schema', 'human_docs', 'rate_limit',
    ])
    expect(body.configuration_confirmation).toEqual({
      system_prompt: 'SERVER_SYSTEM', input_schema: preview.input_schema, response_schema: preview.response_schema,
      human_docs: 'SERVER_DOCS', rate_limit: { endpoint_per_minute: 60, credential_per_minute: 10 },
    })
    expect(text(renderer!)).toContain(initialApiKey)
    expect(String(window.location.pathname)).not.toContain(initialApiKey)
    const completedCallCount = fetchMock.mock.calls.length
    await act(async () => {
      void button(renderer!, '建立 Draft').props.onClick()
      void button(renderer!, '發布端點').props.onClick()
    })
    expect(fetchMock).toHaveBeenCalledTimes(completedCallCount)
    expect(text(renderer!)).toContain(initialApiKey)
  })

  it('runs one published endpoint smoke test from the one-time key screen', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(skills))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(draft, 201))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse({
        endpoint_id: 'endpoint-1', version_id: 'version-1', version_number: 1, status: 'active', initial_api_key: initialApiKey,
      }, 201))
      .mockResolvedValueOnce(jsonResponse({
        ok: true,
        endpoint: { id: 'endpoint-1', slug: 'safe-api', version: 1 },
        invocation: { id: 'inv-1', request_id: 'req-1', session_id: null },
        data: { answer: '測試成功' }, usage: {}, warnings: [], error: null,
      }))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await prepareDraft(renderer!, fetchMock)
    await act(async () => { button(renderer!, '發布端點').props.onClick(); await flush() })
    await flush()

    expect(text(renderer!)).toContain('離開前，試跑一次')
    await act(async () => { renderer!.root.findByProps({ 'aria-label': '送出測試' }).props.onClick(); await flush() })
    await flush()

    const invokeCall = fetchMock.mock.calls.find(([route]) => route === '/v1/endpoints/safe-api/invoke')!
    expect(JSON.parse(String(invokeCall[1]?.body))).toEqual({ input: { input: '請輸入測試內容' } })
    expect(new Headers(invokeCall[1]?.headers).get('Authorization')).toBe(`Bearer ${initialApiKey}`)
    expect(renderer!.root.findAllByType('span').some((node) => node.children.join('') === '200 OK')).toBe(true)
    expect(text(renderer!)).toContain('測試成功')
    expect(text(renderer!)).toContain('req-1')
  })

  it('guards duplicate draft synchronously and logout erases a published key before I/O completes', async () => {
    const draftPreflight = deferred<Response>()
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(skills))
      .mockReturnValueOnce(draftPreflight.promise)
    await act(async () => { renderer = create(<App />) })
    await flush()
    await act(async () => {
      input(renderer!, 'skill-alpha').props.onChange({ target: { checked: true } })
      input(renderer!, 'endpoint-requirement').props.onChange({ target: { value: 'Build a safe API' } })
    })
    const createButton = button(renderer!, '建立 Draft')
    await act(async () => { void createButton.props.onClick(); void createButton.props.onClick() })
    expect(fetchMock).toHaveBeenCalledTimes(3)
    fetchMock.mockResolvedValueOnce(jsonResponse(draft, 201))
    await act(async () => { draftPreflight.resolve(jsonResponse(session())); await draftPreflight.promise })
    await flush()

    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse({ endpoint_id: 'endpoint-1', version_id: 'version-1', version_number: 1, status: 'active', initial_api_key: initialApiKey }, 201))
    await act(async () => { button(renderer!, '發布端點').props.onClick(); await flush() }); await flush()
    expect(text(renderer!)).toContain(initialApiKey)

    const logoutPreflight = deferred<Response>()
    fetchMock.mockReturnValueOnce(logoutPreflight.promise)
    await act(async () => { void button(renderer!, '登出').props.onClick() })
    expect(text(renderer!)).not.toContain(initialApiKey)
  })

  it('version mode uses route endpoint id, publishes no key, and suppresses late publish after route change', async () => {
    pathname = '/endpoints/endpoint-1/versions/new'
    const lateVersion = deferred<Response>()
    let versionSignal!: AbortSignal
    fetchMock
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(skills))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockResolvedValueOnce(jsonResponse(draft, 201))
      .mockResolvedValueOnce(jsonResponse(session()))
      .mockImplementationOnce((_route, init) => { versionSignal = init!.signal as AbortSignal; return lateVersion.promise })
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => { renderer = create(<App />) })
    await flush()
    await prepareDraft(renderer!, fetchMock)
    await act(async () => { void button(renderer!, '建立版本').props.onClick() })
    const versionCall = fetchMock.mock.calls.find(([route]) => route === '/api/published-endpoints/endpoint-1/versions')!
    expect(JSON.parse(String(versionCall[1]?.body))).toEqual({
      configuration: {
        original_requirement_text: 'Build a safe API',
        system_prompt: 'SERVER_SYSTEM',
        model_config_snapshot: { model: 'published-default', temperature: 0 },
        retry_policy: { max_attempts: 1 },
        input_schema: preview.input_schema,
        response_schema: preview.response_schema,
      },
    })

    pathname = '/'
    await act(async () => { popstate?.() }); await flush()
    expect(versionSignal.aborted).toBe(true)
    await act(async () => {
      lateVersion.resolve(jsonResponse({ endpoint_id: 'endpoint-1', version_id: 'version-2', version_number: 2,
        current_version_id: 'version-2', schema_changed: false }, 201))
      await lateVersion.promise
    })
    expect(text(renderer!)).not.toMatch(/版本建立完成|initialApiKey|pk_/)
  })
})
