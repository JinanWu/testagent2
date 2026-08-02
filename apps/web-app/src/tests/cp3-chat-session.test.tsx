import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { ApiFormatError } from '../api/client'
import { sendChat } from '../api/chat'
import {
  buildSessionDetailRoute,
  buildSessionListRoute,
  getSessionDetail,
  listSessions,
} from '../api/sessions'
import { buildSkillDetailRoute, getSkill, listSkills } from '../api/skills'
import App from '../App'

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

interface StrictContract {
  name: string
  request: () => Promise<unknown>
  missing: unknown
  wrong: unknown
  extra: unknown
}

const sessionSummary = { id: 'root', title: '標題', updated_at: 1, message_count: 2 }
const sessionMetadata = { id: 'root', title: '標題', updated_at: 1 }
const transcriptMessage = { role: 'assistant', content: '回覆' }
const skillSummary = { id: 'skill', name: 'Skill', category: 'cat', description: 'desc' }

describe('CP3 安全 API clients', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => vi.unstubAllGlobals())

  it('只建立有界且編碼過的 session、skill 與 limit routes', () => {
    expect(buildSessionListRoute(50)).toBe('/api/sessions?limit=50')
    expect(buildSessionDetailRoute('root/a')).toBe('/api/sessions/root%2Fa')
    expect(buildSkillDetailRoute('safe/skill')).toBe('/api/skills/safe%2Fskill')
    expect(() => buildSessionListRoute(0)).toThrow(ApiFormatError)
    expect(() => buildSessionListRoute(1.5)).toThrow(ApiFormatError)
    expect(() => buildSessionDetailRoute('')).toThrow(ApiFormatError)
    expect(() => buildSkillDetailRoute('x'.repeat(129))).toThrow(ApiFormatError)
  })

  it('以 credential 與 CSRF 傳送精確 chat body', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session_id: 'root-1', reply: { role: 'assistant', content: '回覆' },
    }))
    await expect(sendChat('  你好  ', null, 'csrf')).resolves.toMatchObject({ sessionId: 'root-1' })
    expect(fetchMock).toHaveBeenCalledWith('/api/chat', {
      method: 'POST', credentials: 'include',
      headers: {
        Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf',
      },
      body: JSON.stringify({ message: '你好' }),
    })
  })

  const strictContracts: StrictContract[] = [
    {
      name: 'chat', request: () => sendChat('hi', null, 'csrf'),
      missing: { session_id: 'root' },
      wrong: { session_id: 1, reply: { role: 'assistant', content: 'ok' } },
      extra: { session_id: 'root', reply: { role: 'assistant', content: 'ok' }, internal: true },
    },
    {
      name: 'session list', request: () => listSessions(),
      missing: {}, wrong: { sessions: {} }, extra: { sessions: [], internal: true },
    },
    {
      name: 'session detail', request: () => getSessionDetail('root'),
      missing: { session: { id: 'root', title: '標題', updated_at: 1 } },
      wrong: { session: 'root', messages: [] },
      extra: { session: { id: 'root', title: '標題', updated_at: 1 }, messages: [], internal: true },
    },
    {
      name: 'skill list', request: () => listSkills(),
      missing: {}, wrong: { skills: {} }, extra: { skills: [], internal: true },
    },
    {
      name: 'skill detail', request: () => getSkill('skill'),
      missing: { id: 'skill', name: 'Skill', category: 'cat', description: 'desc' },
      wrong: { id: 'skill', name: 1, category: 'cat', description: 'desc', content: 'body' },
      extra: { id: 'skill', name: 'Skill', category: 'cat', description: 'desc', content: 'body', internal: true },
    },
  ]

  const nestedContracts: StrictContract[] = [
    {
      name: 'chat reply', request: () => sendChat('hi', null, 'csrf'),
      missing: { session_id: 'root', reply: { role: 'assistant' } },
      wrong: { session_id: 'root', reply: { role: 'assistant', content: 1 } },
      extra: { session_id: 'root', reply: { ...transcriptMessage, internal: true } },
    },
    {
      name: 'session summary', request: () => listSessions(),
      missing: { sessions: [{ id: 'root', title: '標題', updated_at: 1 }] },
      wrong: { sessions: [{ ...sessionSummary, message_count: '2' }] },
      extra: { sessions: [{ ...sessionSummary, internal: true }] },
    },
    {
      name: 'session detail metadata', request: () => getSessionDetail('root'),
      missing: { session: { id: 'root', title: '標題' }, messages: [] },
      wrong: { session: { ...sessionMetadata, updated_at: '1' }, messages: [] },
      extra: { session: { ...sessionMetadata, internal: true }, messages: [] },
    },
    {
      name: 'transcript message', request: () => getSessionDetail('root'),
      missing: { session: sessionMetadata, messages: [{ role: 'assistant' }] },
      wrong: { session: sessionMetadata, messages: [{ role: 'assistant', content: 1 }] },
      extra: { session: sessionMetadata, messages: [{ ...transcriptMessage, internal: true }] },
    },
    {
      name: 'skill summary', request: () => listSkills(),
      missing: { skills: [{ id: 'skill', name: 'Skill', category: 'cat' }] },
      wrong: { skills: [{ ...skillSummary, description: 1 }] },
      extra: { skills: [{ ...skillSummary, internal: true }] },
    },
  ]

  it.each(strictContracts)('$name 拒絕 missing key', async ({ request, missing }) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(missing))
    await expect(request()).rejects.toThrow(ApiFormatError)
  })

  it.each(strictContracts)('$name 拒絕 wrong type', async ({ request, wrong }) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(wrong))
    await expect(request()).rejects.toThrow(ApiFormatError)
  })

  it.each(strictContracts)('$name 拒絕 extra key', async ({ request, extra }) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(extra))
    await expect(request()).rejects.toThrow(ApiFormatError)
  })

  it.each(nestedContracts)('$name nested DTO 拒絕 missing key', async ({ request, missing }) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(missing))
    await expect(request()).rejects.toThrow(ApiFormatError)
  })

  it.each(nestedContracts)('$name nested DTO 拒絕 wrong type', async ({ request, wrong }) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(wrong))
    await expect(request()).rejects.toThrow(ApiFormatError)
  })

  it.each(nestedContracts)('$name nested DTO 拒絕 extra key', async ({ request, extra }) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(extra))
    await expect(request()).rejects.toThrow(ApiFormatError)
  })

  it('拒絕超過 1 MiB transport 上限的回應', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session_id: 'root', reply: { role: 'assistant', content: 'x'.repeat(1024 * 1024) },
    }))
    await expect(sendChat('hi', null, 'csrf')).rejects.toThrow(ApiFormatError)
  })

  it.each([
    ['ASCII', 'x'.repeat(65_536)],
    ['multibyte', '界'.repeat(21_845) + 'x'],
  ])('%s 65,536-byte chat 與 session 文字皆通過', async (_name, content) => {
    expect(new TextEncoder().encode(content)).toHaveLength(65_536)
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session_id: 'root', reply: { role: 'assistant', content },
    })).mockResolvedValueOnce(jsonResponse({
      session: sessionMetadata, messages: [{ role: 'user', content }],
    }))
    await expect(sendChat('hi', null, 'csrf')).resolves.toMatchObject({ reply: { content } })
    await expect(getSessionDetail('root')).resolves.toMatchObject({ messages: [{ content }] })
  })

  it.each([
    ['ASCII', 'x'.repeat(65_537)],
    ['multibyte', '界'.repeat(21_845) + 'xx'],
  ])('%s 65,537-byte chat 與 session 文字皆回 generalized format error', async (_name, content) => {
    expect(new TextEncoder().encode(content)).toHaveLength(65_537)
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session_id: 'root', reply: { role: 'assistant', content },
    })).mockResolvedValueOnce(jsonResponse({
      session: sessionMetadata, messages: [{ role: 'assistant', content }],
    }))
    await expect(sendChat('hi', null, 'csrf')).rejects.toBeInstanceOf(ApiFormatError)
    await expect(getSessionDetail('root')).rejects.toBeInstanceOf(ApiFormatError)
  })

  it.each([
    ['chat reply', () => sendChat('hi', null, 'csrf'), {
      session_id: 'root', reply: { role: 'assistant', content: 'x'.repeat(65_537) },
    }],
    ['session message', () => getSessionDetail('root'), {
      session: { id: 'root', title: '標題', updated_at: 1 },
      messages: [{ role: 'assistant', content: 'x'.repeat(65_537) }],
    }],
    ['skill content', () => getSkill('skill'), {
      id: 'skill', name: 'Skill', category: 'cat', description: 'desc', content: '界'.repeat(87_382),
    }],
  ] as const)('%s 拒絕 oversized field', async (_name, request, body) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(body))
    await expect(request()).rejects.toThrow(ApiFormatError)
  })

  it.each([
    ['session list', () => listSessions(), { sessions: Array.from({ length: 51 }) }],
    ['session messages', () => getSessionDetail('root'), {
      session: { id: 'root', title: '標題', updated_at: 1 }, messages: Array.from({ length: 10_001 }),
    }],
    ['skill list', () => listSkills(), { skills: Array.from({ length: 10_001 }) }],
  ] as const)('%s 拒絕 oversized collection', async (_name, request, body) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(body))
    await expect(request()).rejects.toThrow(ApiFormatError)
  })
})

describe('CP3 真實對話工作流程', () => {
  const fetchMock = vi.fn<typeof fetch>()
  const auth = { user: { id: 'u1', username: 'alice', role: 'member' }, csrf_token: 'csrf' }
  let renderer: ReactTestRenderer

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('window', {
      location: { pathname: '/' }, history: { replaceState: vi.fn() },
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    })
    fetchMock.mockReset()
  })
  afterEach(async () => {
    if (renderer) await act(async () => renderer.unmount())
    vi.unstubAllGlobals()
  })

  async function openChat(sessions: unknown[] = []) {
    fetchMock.mockResolvedValueOnce(jsonResponse(auth)).mockResolvedValueOnce(jsonResponse({ sessions }))
    await act(async () => { renderer = create(<App />) })
    await act(async () => { await Promise.resolve() })
  }

  it('載入、恢復、傳送並建立新對話', async () => {
    await openChat([{ id: 'root', title: '舊對話', updated_at: 1, message_count: 2 }])
    expect(renderer.root.findAllByType('select')).toHaveLength(0)
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session: { id: 'root', title: '舊對話', updated_at: 1 },
      messages: [{ role: 'user', content: '舊問題' }, { role: 'assistant', content: '舊答案' }],
    }))
    await act(async () => renderer.root.findByProps({ children: '舊對話' }).props.onClick())
    expect(JSON.stringify(renderer.toJSON())).toContain('舊答案')
    const activeSession = renderer.root.findByProps({ children: '舊對話' })
    expect(activeSession.props['aria-current']).toBe('page')
    expect(activeSession.props['aria-pressed']).toBe(true)
    expect(activeSession.props.style).toMatchObject({ fontWeight: 700 })

    fetchMock.mockResolvedValueOnce(jsonResponse(auth)).mockResolvedValueOnce(jsonResponse({
      session_id: 'root', reply: { role: 'assistant', content: '新答案' },
    })).mockResolvedValueOnce(jsonResponse({ sessions: [
      { ...sessionSummary, title: '舊對話' },
    ] }))
    await act(async () => renderer.root.findByType('textarea').props.onChange({ currentTarget: { value: '新問題' } }))
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }))
    expect(JSON.stringify(renderer.toJSON())).toContain('新答案')
    expect(renderer.root.findByType('textarea').props.value).toBe('')
    expect(fetchMock).toHaveBeenCalledWith('/api/chat', expect.objectContaining({
      headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf' }),
      body: JSON.stringify({ message: '新問題', session_id: 'root' }),
    }))
    await act(async () => renderer.root.findByProps({ children: '新增對話' }).props.onClick())
    expect(JSON.stringify(renderer.toJSON())).not.toContain('新答案')
    expect(renderer.root.findByProps({ children: '舊對話' }).props['aria-pressed']).toBe(false)
  })

  it('同一 render 的重複送出只啟動一組 auth 與 chat requests', async () => {
    await openChat()
    let resolveAuth!: (response: Response) => void
    const pendingAuth = new Promise<Response>((resolve) => { resolveAuth = resolve })
    fetchMock.mockReturnValueOnce(pendingAuth)
      .mockResolvedValueOnce(jsonResponse({
        session_id: 'root', reply: { role: 'assistant', content: '唯一回覆' },
      }))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => renderer.root.findByType('textarea').props.onChange({
      currentTarget: { value: '只送一次' },
    }))
    const submit = renderer.root.findByType('form').props.onSubmit
    const event = { preventDefault: vi.fn() }
    let first!: Promise<void>
    let second!: Promise<void>
    await act(async () => {
      first = submit(event)
      second = submit(event)
      await Promise.resolve()
    })
    expect(fetchMock).toHaveBeenCalledTimes(3)

    await act(async () => {
      resolveAuth(jsonResponse(auth))
      await Promise.all([first, second])
    })
    expect(fetchMock.mock.calls.filter(([route]) => route === '/api/auth/session')).toHaveLength(2)
    expect(fetchMock.mock.calls.filter(([route]) => route === '/api/chat')).toHaveLength(1)
    expect(JSON.stringify(renderer.toJSON())).toContain('唯一回覆')
    expect(renderer.root.findByProps({ type: 'submit' }).props.disabled).toBe(true)
  })

  it('工作階段明細載入期間禁止送出到舊工作階段', async () => {
    await openChat([
      { id: 'old', title: '舊對話', updated_at: 1, message_count: 1 },
      { id: 'next', title: '新對話', updated_at: 2, message_count: 1 },
    ])
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session: { id: 'old', title: '舊對話', updated_at: 1 },
      messages: [{ role: 'assistant', content: '舊內容' }],
    }))
    await act(async () => renderer.root.findByProps({ children: '舊對話' }).props.onClick())

    let resolveDetail!: (response: Response) => void
    fetchMock.mockReturnValueOnce(new Promise((resolve) => { resolveDetail = resolve }))
    await act(async () => { void renderer.root.findByProps({ children: '新對話' }).props.onClick() })
    await act(async () => renderer.root.findByType('textarea').props.onChange({ currentTarget: { value: '不可送到舊對話' } }))
    expect(renderer.root.findByProps({ type: 'submit' }).props.disabled).toBe(true)
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }))
    expect(fetchMock).toHaveBeenCalledTimes(4)

    await act(async () => resolveDetail(jsonResponse({
      session: { id: 'next', title: '新對話', updated_at: 2 },
      messages: [{ role: 'assistant', content: '新內容' }],
    })))
    expect(JSON.stringify(renderer.toJSON())).toContain('新內容')
    expect(JSON.stringify(renderer.toJSON())).not.toContain('你：不可送到舊對話')
  })

  it('較舊的明細回應不可覆蓋後選工作階段', async () => {
    await openChat([
      { id: 'slow', title: '慢對話', updated_at: 1, message_count: 1 },
      { id: 'fast', title: '快對話', updated_at: 2, message_count: 1 },
    ])
    let resolveSlow!: (response: Response) => void
    fetchMock.mockReturnValueOnce(new Promise((resolve) => { resolveSlow = resolve }))
    await act(async () => { void renderer.root.findByProps({ children: '慢對話' }).props.onClick() })
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session: { id: 'fast', title: '快對話', updated_at: 2 },
      messages: [{ role: 'assistant', content: '最新內容' }],
    }))
    await act(async () => renderer.root.findByProps({ children: '快對話' }).props.onClick())
    await act(async () => resolveSlow(jsonResponse({
      session: { id: 'slow', title: '慢對話', updated_at: 1 },
      messages: [{ role: 'assistant', content: '過期內容' }],
    })))
    expect(JSON.stringify(renderer.toJSON())).toContain('最新內容')
    expect(JSON.stringify(renderer.toJSON())).not.toContain('過期內容')
  })

  it('傳送失敗時顯示安全錯誤並保留草稿', async () => {
    await openChat()
    fetchMock.mockResolvedValueOnce(jsonResponse(auth)).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'private traceback' }), { status: 500 }),
    )
    await act(async () => renderer.root.findByType('textarea').props.onChange({ currentTarget: { value: '保留我' } }))
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }))
    expect(renderer.root.findByType('textarea').props.value).toBe('保留我')
    expect(JSON.stringify(renderer.toJSON())).toContain('目前無法傳送訊息')
    expect(JSON.stringify(renderer.toJSON())).not.toContain('private traceback')
  })

  it('失敗時保留草稿並淘汰新增對話前的舊回應', async () => {
    await openChat()
    let resolveChat!: (response: Response) => void
    fetchMock.mockResolvedValueOnce(jsonResponse(auth))
      .mockReturnValueOnce(new Promise((resolve) => { resolveChat = resolve }))
    await act(async () => renderer.root.findByType('textarea').props.onChange({ currentTarget: { value: '保留我' } }))
    let send!: Promise<void>
    await act(async () => { send = renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }) })
    await act(async () => renderer.root.findByProps({ children: '新增對話' }).props.onClick())
    await act(async () => { resolveChat(jsonResponse({ detail: 'private traceback' })); await send })
    expect(JSON.stringify(renderer.toJSON())).not.toContain('private traceback')
    expect(renderer.root.findByType('textarea').props.value).toBe('保留我')
    expect(JSON.stringify(renderer.toJSON())).not.toContain('你：保留我')
  })
})
