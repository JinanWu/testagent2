import {
  API_ROUTES, ApiFormatError, apiRequest, boundedString, byteLength, exactObject,
} from './client'

export const CHAT_ERROR_MESSAGE = '目前無法傳送訊息，請稍後再試。'
export const CHAT_MESSAGE_MAX_BYTES = 16_384
/* 與後端 每則訊息圖片上限 一致。 */
export const CHAT_IMAGE_MAX_COUNT = 4

export interface ChatReply {
  sessionId: string
  reply: { role: 'assistant'; content: string }
}

export async function sendChat(
  message: string,
  sessionId: string | null,
  csrfToken: string,
  signal?: AbortSignal,
  images: readonly string[] = [],
): Promise<ChatReply> {
  const trimmed = message.trim()
  if (!boundedString(trimmed, CHAT_MESSAGE_MAX_BYTES) || byteLength(trimmed) > CHAT_MESSAGE_MAX_BYTES ||
      (sessionId !== null && !boundedString(sessionId, 128)) || !boundedString(csrfToken, 512) ||
      images.length > CHAT_IMAGE_MAX_COUNT ||
      images.some((參照) => !boundedString(參照, 512) || !參照.startsWith('gs://'))) {
    throw new ApiFormatError()
  }
  const body = {
    message: trimmed,
    ...(sessionId === null ? {} : { session_id: sessionId }),
    ...(images.length === 0 ? {} : { images: [...images] }),
  }
  const value = await apiRequest(API_ROUTES.chat, {
    method: 'POST', body: JSON.stringify(body), csrfToken, signal, expectedStatus: 200,
  })
  const outer = exactObject(value, ['session_id', 'reply'])
  const reply = outer && exactObject(outer.reply, ['role', 'content'])
  if (!outer || !reply || !boundedString(outer.session_id, 128) ||
      reply.role !== 'assistant' || typeof reply.content !== 'string' ||
      reply.content.length > 65_536 || byteLength(reply.content) > 65_536) {
    throw new ApiFormatError()
  }
  return { sessionId: outer.session_id, reply: { role: 'assistant', content: reply.content } }
}
