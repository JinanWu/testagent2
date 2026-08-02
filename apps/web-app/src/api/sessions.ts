import { ApiFormatError, apiRequest, boundedString, byteLength, encodedRoute, exactObject, type ApiRoute } from './client'

export interface SessionSummary {
  id: string
  title: string
  updatedAt: number
  messageCount: number
}
export interface TranscriptMessage { role: 'user' | 'assistant'; content: string }
export interface SessionDetail {
  session: Omit<SessionSummary, 'messageCount'>
  messages: TranscriptMessage[]
}

function text(value: unknown, maximum: number, allowEmpty = true): value is string {
  return typeof value === 'string' && value.length <= maximum && (allowEmpty || value.length > 0)
}
function timestamp(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}
function parseSummary(value: unknown): SessionSummary {
  const item = exactObject(value, ['id', 'title', 'updated_at', 'message_count'])
  if (!item || !boundedString(item.id, 128) || !text(item.title, 4096) || !timestamp(item.updated_at) ||
      !Number.isSafeInteger(item.message_count) || (item.message_count as number) < 0) throw new ApiFormatError()
  return { id: item.id, title: item.title, updatedAt: item.updated_at, messageCount: item.message_count as number }
}

export function buildSessionListRoute(limit = 20): ApiRoute {
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new ApiFormatError()
  return `/api/sessions?limit=${limit}`
}
export function buildSessionDetailRoute(id: string): ApiRoute {
  return encodedRoute('/api/sessions/', id)
}
export async function listSessions(limit = 20, signal?: AbortSignal): Promise<SessionSummary[]> {
  const outer = exactObject(await apiRequest(buildSessionListRoute(limit), { signal, expectedStatus: 200 }), ['sessions'])
  if (!outer || !Array.isArray(outer.sessions) || outer.sessions.length > 50) throw new ApiFormatError()
  return outer.sessions.map(parseSummary)
}
export async function getSessionDetail(id: string, signal?: AbortSignal): Promise<SessionDetail> {
  const outer = exactObject(await apiRequest(buildSessionDetailRoute(id), { signal, expectedStatus: 200 }), ['session', 'messages'])
  const session = outer && exactObject(outer.session, ['id', 'title', 'updated_at'])
  if (!outer || !session || !boundedString(session.id, 128) || !text(session.title, 4096) ||
      !timestamp(session.updated_at) || !Array.isArray(outer.messages) || outer.messages.length > 10_000) {
    throw new ApiFormatError()
  }
  const messages = outer.messages.map((value): TranscriptMessage => {
    const item = exactObject(value, ['role', 'content'])
    if (!item || (item.role !== 'user' && item.role !== 'assistant') ||
        !text(item.content, 65_536) || byteLength(item.content) > 65_536) {
      throw new ApiFormatError()
    }
    return { role: item.role, content: item.content }
  })
  return { session: { id: session.id, title: session.title, updatedAt: session.updated_at }, messages }
}
