import { sendChat, type ChatReply } from '../api/chat'

const protectedOperationBrand: unique symbol = Symbol('protected-operation')
const protectedStateOwnerBrand: unique symbol = Symbol('protected-state-owner')

export interface ProtectedOperation<T> {
  readonly [protectedOperationBrand]: T
}

export interface ProtectedStateOwner {
  readonly [protectedStateOwnerBrand]: true
}

export interface AuthorizedRequest<T> {
  readonly owner: ProtectedStateOwner
  readonly operation: ProtectedOperation<T>
  readonly signal?: AbortSignal
}

type SendChatManifest = Readonly<{
  kind: 'send-chat'
  message: string
  sessionId: string | null
}>

type ProtectedManifest = SendChatManifest

const manifests = new WeakMap<object, ProtectedManifest>()
const consumed = new WeakSet<object>()

function abortError(): DOMException {
  return new DOMException('授權操作已取消', 'AbortError')
}

function boundedText(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum
}

function token<T>(manifest: ProtectedManifest): ProtectedOperation<T> {
  const value = Object.freeze(Object.create(null)) as ProtectedOperation<T>
  manifests.set(value as object, Object.freeze(manifest))
  return value
}

export function createSendChatOperation(message: string, sessionId: string | null): ProtectedOperation<ChatReply> {
  const trimmed = message.trim()
  if (!boundedText(trimmed, 16_384) ||
      (sessionId !== null && !boundedText(sessionId, 128))) {
    throw abortError()
  }
  return token<ChatReply>({ kind: 'send-chat', message: trimmed, sessionId })
}

export function consumeProtectedOperation<T>(operation: ProtectedOperation<T>): ProtectedManifest {
  const candidate = operation as object
  const manifest = manifests.get(candidate)
  if (!manifest || consumed.has(candidate)) throw abortError()
  consumed.add(candidate)
  manifests.delete(candidate)
  return manifest
}

export async function dispatchProtectedOperation<T>(
  manifest: ProtectedManifest,
  csrfToken: string,
  signal: AbortSignal,
): Promise<T> {
  if (signal.aborted) throw abortError()
  switch (manifest.kind) {
    case 'send-chat':
      return await sendChat(manifest.message, manifest.sessionId, csrfToken, signal) as T
  }
}
