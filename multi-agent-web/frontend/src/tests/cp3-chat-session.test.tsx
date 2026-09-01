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

  it('接受超過 1 MiB、但仍符合後端契約的 session detail', async () => {
    const messages = Array.from({ length: 17 }, () => ({
      role: 'assistant', content: 'x'.repeat(65_536),
    }))
    const body = { session: sessionMetadata, messages }
    expect(new TextEncoder().encode(JSON.stringify(body)).byteLength).toBeGreaterThan(1024 * 1024)
    fetchMock.mockResolvedValueOnce(jsonResponse(body))

    await expect(getSessionDetail('root')).resolves.toMatchObject({
      messages: expect.arrayContaining([{ role: 'assistant', content: 'x'.repeat(65_536) }]),
    })
  })

  it('接受超過 4096 個非空 chunks 組成的合法 session detail', async () => {
    const bytes = new TextEncoder().encode(JSON.stringify({
      session: sessionMetadata,
      messages: [{ role: 'assistant', content: 'x'.repeat(5_000) }],
    }))
    let offset = 0
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (offset === bytes.byteLength) {
          controller.close()
          return
        }
        controller.enqueue(bytes.subarray(offset, ++offset))
      },
    }, { highWaterMark: 0 })
    fetchMock.mockResolvedValueOnce(new Response(stream, {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))

    await expect(getSessionDetail('root')).resolves.toMatchObject({
      messages: [{ role: 'assistant', content: 'x'.repeat(5_000) }],
    })
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

  function 對話區文字(renderer: ReactTestRenderer): string {
    return renderer.root
      .findByProps({ 'aria-label': '對話內容' })
      .findAllByType('p')
      .map((節點) => 節點.children.filter((子) => typeof 子 === 'string').join(''))
      .join('\n')
  }

  /*
   * 頁面標題 <h1> 現在顯示當前對話串的標題，會與側欄那顆對話按鈕文字相同，
   * 因此要指名 button，不能只用文字比對取節點。斷言本身不變。
   */
  function 對話按鈕(renderer: ReactTestRenderer, 標題: string) {
    return renderer.root
      .findAllByProps({ children: 標題 })
      .filter((節點) => 節點.type === 'button')[0]
  }

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
    await act(async () => 對話按鈕(renderer, '舊對話').props.onClick())
    expect(JSON.stringify(renderer.toJSON())).toContain('舊答案')
    const activeSession = 對話按鈕(renderer, '舊對話')
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
    expect(對話按鈕(renderer, '舊對話').props['aria-pressed']).toBe(false)
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
    await act(async () => 對話按鈕(renderer, '舊對話').props.onClick())

    let resolveDetail!: (response: Response) => void
    fetchMock.mockReturnValueOnce(new Promise((resolve) => { resolveDetail = resolve }))
    await act(async () => { void 對話按鈕(renderer, '新對話').props.onClick() })
    await act(async () => renderer.root.findByType('textarea').props.onChange({ currentTarget: { value: '不可送到舊對話' } }))
    expect(renderer.root.findByProps({ type: 'submit' }).props.disabled).toBe(true)
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }))
    expect(fetchMock).toHaveBeenCalledTimes(4)

    await act(async () => resolveDetail(jsonResponse({
      session: { id: 'next', title: '新對話', updated_at: 2 },
      messages: [{ role: 'assistant', content: '新內容' }],
    })))
    expect(對話區文字(renderer)).toContain('新內容')
    expect(對話區文字(renderer)).not.toContain('不可送到舊對話')
  })

  it('較舊的明細回應不可覆蓋後選工作階段', async () => {
    await openChat([
      { id: 'slow', title: '慢對話', updated_at: 1, message_count: 1 },
      { id: 'fast', title: '快對話', updated_at: 2, message_count: 1 },
    ])
    let resolveSlow!: (response: Response) => void
    fetchMock.mockReturnValueOnce(new Promise((resolve) => { resolveSlow = resolve }))
    await act(async () => { void 對話按鈕(renderer, '慢對話').props.onClick() })
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session: { id: 'fast', title: '快對話', updated_at: 2 },
      messages: [{ role: 'assistant', content: '最新內容' }],
    }))
    await act(async () => 對話按鈕(renderer, '快對話').props.onClick())
    await act(async () => resolveSlow(jsonResponse({
      session: { id: 'slow', title: '慢對話', updated_at: 1 },
      messages: [{ role: 'assistant', content: '過期內容' }],
    })))
    expect(JSON.stringify(renderer.toJSON())).toContain('最新內容')
    expect(JSON.stringify(renderer.toJSON())).not.toContain('過期內容')
  })

  /*
   * 捲動行為的測試要拿到真實節點才跑得到 effect：react-test-renderer 預設把
   * host ref 餵 null，ChatPage 的自動捲動會整段 early-return，等於沒測到。
   * createNodeMock 讓我們替捲動區換上可觀測的假節點。
   */
  function 建立捲動區模擬() {
    return { scrollTop: 0, scrollHeight: 5000, clientHeight: 600, offsetWidth: 800, clientWidth: 800 }
  }

  /* createNodeMock 的 element.props 型別是 unknown，取值前要先窄化 */
  function 建立節點模擬(捲動區模擬: ReturnType<typeof 建立捲動區模擬>) {
    return (element: { type: unknown; props: unknown }) => {
      if (element.type === 'textarea') return { style: {}, scrollHeight: 20 }
      const props = element.props as Record<string, unknown>
      if (props.role === 'log' && props['aria-label'] === '對話內容') return 捲動區模擬
      return { offsetHeight: 120, offsetWidth: 800, clientWidth: 800 }
    }
  }

  async function openChatWithNodes(捲動區模擬: ReturnType<typeof 建立捲動區模擬>) {
    fetchMock.mockResolvedValueOnce(jsonResponse(auth)).mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => {
      renderer = create(<App />, { createNodeMock: 建立節點模擬(捲動區模擬) })
    })
    await act(async () => { await Promise.resolve() })
  }

  function 捲動區(renderer: ReactTestRenderer) {
    return renderer.root.findByProps({ role: 'log', 'aria-label': '對話內容' })
  }

  it('送出訊息後把畫面捲到最新內容', async () => {
    const 捲動區模擬 = 建立捲動區模擬()
    await openChatWithNodes(捲動區模擬)
    fetchMock.mockResolvedValueOnce(jsonResponse(auth)).mockResolvedValueOnce(jsonResponse({
      session_id: 's1', reply: { role: 'assistant', content: '回覆內容' },
    })).mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => renderer.root.findByType('textarea').props.onChange({ currentTarget: { value: '問題' } }))
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }))
    /* 樂觀訊息與打字動畫、以及之後整段插入的回覆，都必須把視窗帶到底部 */
    expect(捲動區模擬.scrollTop).toBe(捲動區模擬.scrollHeight)
    expect(對話區文字(renderer)).toContain('回覆內容')
  })

  it('等待回覆時使用者往上捲，回覆到達不會把畫面拉回底部', async () => {
    const 捲動區模擬 = 建立捲動區模擬()
    await openChatWithNodes(捲動區模擬)
    let resolveChat!: (response: Response) => void
    fetchMock.mockResolvedValueOnce(jsonResponse(auth))
      .mockReturnValueOnce(new Promise((resolve) => { resolveChat = resolve }))
      .mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => renderer.root.findByType('textarea').props.onChange({ currentTarget: { value: '問題' } }))
    let send!: Promise<void>
    await act(async () => { send = renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }) })

    /* 等待期間使用者往回翻歷史：離底部 3000px，遠超過貼底容差 */
    await act(async () => 捲動區(renderer).props.onScroll({
      currentTarget: { scrollHeight: 5000, scrollTop: 1400, clientHeight: 600 },
    }))
    捲動區模擬.scrollTop = 1400

    await act(async () => { resolveChat(jsonResponse({
      session_id: 's1', reply: { role: 'assistant', content: '回覆內容' },
    })); await send })

    expect(對話區文字(renderer)).toContain('回覆內容')
    expect(捲動區模擬.scrollTop).toBe(1400)
  })

  it('開啟既有對話時停在最新一則而非開場白', async () => {
    const 捲動區模擬 = 建立捲動區模擬()
    fetchMock.mockResolvedValueOnce(jsonResponse(auth)).mockResolvedValueOnce(jsonResponse({
      sessions: [{ id: 'root', title: '舊對話', updated_at: 1, message_count: 2 }],
    }))
    await act(async () => {
      renderer = create(<App />, { createNodeMock: 建立節點模擬(捲動區模擬) })
    })
    await act(async () => { await Promise.resolve() })
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session: { id: 'root', title: '舊對話', updated_at: 1 },
      messages: [{ role: 'user', content: '舊問題' }, { role: 'assistant', content: '舊答案' }],
    }))
    await act(async () => 對話按鈕(renderer, '舊對話').props.onClick())
    expect(捲動區模擬.scrollTop).toBe(捲動區模擬.scrollHeight)
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
    expect(對話區文字(renderer)).not.toContain('保留我')
  })

  it('登出失敗後不會讓被中斷的傳送卡住 pending', async () => {
    await openChat()
    let rejectAuth!: (reason?: unknown) => void
    fetchMock.mockImplementationOnce((_input, init) => new Promise<Response>((_resolve, reject) => {
      rejectAuth = reject
      init?.signal?.addEventListener('abort', () => {
        reject(new DOMException('aborted', 'AbortError'))
      }, { once: true })
    }))
    await act(async () => renderer.root.findByType('textarea').props.onChange({
      currentTarget: { value: '傳送中斷後仍可送' },
    }))
    let send!: Promise<void>
    await act(async () => {
      send = renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() })
    })
    expect(renderer.root.findByProps({ type: 'submit' }).props.children).toBe('傳送中…')
    expect(renderer.root.findByProps({ type: 'submit' }).props.disabled).toBe(true)

    fetchMock
      .mockResolvedValueOnce(jsonResponse(auth))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'private' }), { status: 500 }))
    await act(async () => {
      renderer.root.findByProps({ children: '登出' }).props.onClick()
      await send.catch(() => undefined)
      rejectAuth?.(new DOMException('aborted', 'AbortError'))
      await Promise.resolve()
    })

    expect(JSON.stringify(renderer.toJSON())).toContain('驗證服務暫時無法使用')
    expect(renderer.root.findByProps({ id: 'chat-title' })).toBeDefined()
    const submit = renderer.root.findByProps({ type: 'submit' })
    expect(submit.props.children).toBe('傳送')
    expect(submit.props.disabled).toBe(true)
    expect(renderer.root.findByType('textarea').props.value).toBe('')

    await act(async () => renderer.root.findByType('textarea').props.onChange({
      currentTarget: { value: 'fresh request' },
    }))
    expect(renderer.root.findByProps({ type: 'submit' }).props.disabled).toBe(false)

    fetchMock.mockResolvedValueOnce(jsonResponse(auth)).mockResolvedValueOnce(jsonResponse({
      session_id: 'root', reply: { role: 'assistant', content: '恢復後回覆' },
    })).mockResolvedValueOnce(jsonResponse({ sessions: [] }))
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }))
    expect(JSON.stringify(renderer.toJSON())).toContain('恢復後回覆')
  })

  it('輸入內容過長時禁止送出並顯示固定提示', async () => {
    await openChat()
    const before = fetchMock.mock.calls.length
    await act(async () => renderer.root.findByType('textarea').props.onChange({
      currentTarget: { value: '密'.repeat(6000) },
    }))

    expect(JSON.stringify(renderer.toJSON())).toContain('內容太長，請縮短後再送出')
    expect(renderer.root.findByProps({ type: 'submit' }).props.disabled).toBe(true)
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }))
    expect(fetchMock).toHaveBeenCalledTimes(before)
  })
})
