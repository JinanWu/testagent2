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

  it('拒絕 chat、session 與 skill 回應的多餘或錯誤欄位', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      session_id: 'root', reply: { role: 'assistant', content: 'ok', secret: true },
    }))
    await expect(sendChat('hi', null, 'csrf')).rejects.toThrow()

    fetchMock.mockResolvedValueOnce(jsonResponse({ sessions: [{
      id: 'root', title: '標題', updated_at: 1, message_count: 2, internal: true,
    }] }))
    await expect(listSessions()).rejects.toThrow()

    fetchMock.mockResolvedValueOnce(jsonResponse({
      session: { id: 'root', title: '標題', updated_at: 1 },
      messages: [{ role: 'tool', content: 'secret' }],
    }))
    await expect(getSessionDetail('root')).rejects.toThrow()

    fetchMock.mockResolvedValueOnce(jsonResponse({ skills: [{
      id: 'skill', name: 'Skill', category: 'cat', description: 'desc', path: '/secret',
    }] }))
    await expect(listSkills()).rejects.toThrow()

    fetchMock.mockResolvedValueOnce(jsonResponse({
      id: 'skill', name: 'Skill', category: 'cat', description: 'desc', content: 3,
    }))
    await expect(getSkill('skill')).rejects.toThrow()
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

    fetchMock.mockResolvedValueOnce(jsonResponse(auth)).mockResolvedValueOnce(jsonResponse({
      session_id: 'root', reply: { role: 'assistant', content: '新答案' },
    })).mockResolvedValueOnce(jsonResponse({ sessions: [] }))
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
