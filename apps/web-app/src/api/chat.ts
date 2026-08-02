import {
  API_ROUTES, ApiFormatError, apiRequest, boundedString, byteLength, exactObject,
} from './client'

export const CHAT_ERROR_MESSAGE = '目前無法傳送訊息，請稍後再試。'

export interface ChatReply {
  sessionId: string
  reply: { role: 'assistant'; content: string }
}

export async function sendChat(
  message: string,
  sessionId: string | null,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<ChatReply> {
  const trimmed = message.trim()
  if (!boundedString(trimmed, 16_384) || byteLength(trimmed) > 16_384 ||
      (sessionId !== null && !boundedString(sessionId, 128)) || !boundedString(csrfToken, 512)) {
    throw new ApiFormatError()
  }
  const body = sessionId === null ? { message: trimmed } : { message: trimmed, session_id: sessionId }
  const value = await apiRequest(API_ROUTES.chat, {
    method: 'POST', body: JSON.stringify(body), csrfToken, signal, expectedStatus: 200,
  })
  const outer = exactObject(value, ['session_id', 'reply'])
  const reply = outer && exactObject(outer.reply, ['role', 'content'])
  if (!outer || !reply || !boundedString(outer.session_id, 128) ||
      reply.role !== 'assistant' || typeof reply.content !== 'string' || reply.content.length > 65_536) {
    throw new ApiFormatError()
  }
  return { sessionId: outer.session_id, reply: { role: 'assistant', content: reply.content } }
}
