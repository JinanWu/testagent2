import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiFormatError } from '../api/client'
import { sendChat } from '../api/chat'
import {
  buildSessionDetailRoute,
  buildSessionListRoute,
  getSessionDetail,
  listSessions,
} from '../api/sessions'
import { buildSkillDetailRoute, getSkill, listSkills } from '../api/skills'

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
