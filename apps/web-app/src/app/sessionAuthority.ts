import { sendChat, type ChatReply } from '../api/chat'
import {
  createCredential,
  createDraft,
  createEndpointVersion,
  publishEndpoint,
  revokeCredential,
  type CredentialCreateReceipt,
  type DraftReceipt,
  type PublishReceipt,
  type VersionReceipt,
} from '../api/endpoints'
import { redactInvocation, type RedactionReceipt, type RedactionRequest } from '../api/logs'

const protectedOperationBrand: unique symbol = Symbol('protected-operation')
const protectedStateOwnerBrand: unique symbol = Symbol('protected-state-owner')

export interface ProtectedOperation<T> { readonly [protectedOperationBrand]: T }
export interface ProtectedStateOwner { readonly [protectedStateOwnerBrand]: true }
export interface AuthorizedRequest<T> {
  readonly owner: ProtectedStateOwner
  readonly operation: ProtectedOperation<T>
  readonly signal?: AbortSignal
}

type SendChatManifest = Readonly<{ kind: 'send-chat'; message: string; sessionId: string | null }>
type RedactionManifest = Readonly<{
  kind: 'redact-invocation'; endpointId: string; invocationId: string
  request: Readonly<RedactionRequest>; idempotencyKey: string
}>
export interface EndpointDraftInput {
  readonly originalRequirementText: string
  readonly selectedSkills: readonly string[]
  readonly responseMode: 'text' | 'structured'
}
export interface EndpointConfigurationConfirmation extends Readonly<Record<string, unknown>> {
  readonly system_prompt: unknown
  readonly input_schema: unknown
  readonly response_schema: unknown
  readonly human_docs: unknown
  readonly rate_limit: unknown
}
export interface EndpointVersionConfiguration extends Readonly<Record<string, unknown>> {
  readonly original_requirement_text: unknown
  readonly system_prompt: unknown
  readonly model_config_snapshot: unknown
  readonly retry_policy: unknown
  readonly input_schema: unknown
  readonly response_schema: unknown
}
type CreateDraftManifest = Readonly<{ kind: 'create-endpoint-draft'; input: EndpointDraftInput }>
type PublishEndpointManifest = Readonly<{
  kind: 'publish-endpoint'; draftId: string; slug: string; configuration: EndpointConfigurationConfirmation
}>
type CreateVersionManifest = Readonly<{
  kind: 'create-endpoint-version'; endpointId: string; configuration: EndpointVersionConfiguration
}>
export interface CredentialCreateInput {
  readonly name: string
  readonly purpose: string
  readonly expiresAt: number
  readonly ipAllowlist: readonly string[]
  readonly rateLimitRequests: number
}
type CreateCredentialManifest = Readonly<{
  kind: 'create-credential'; endpointId: string; input: CredentialCreateInput
}>
type RevokeCredentialManifest = Readonly<{
  kind: 'revoke-credential'; endpointId: string; credentialId: string
}>
type ProtectedManifest = SendChatManifest | RedactionManifest | CreateDraftManifest | PublishEndpointManifest |
  CreateVersionManifest | CreateCredentialManifest | RevokeCredentialManifest

const manifests = new WeakMap<object, ProtectedManifest>()
const consumed = new WeakSet<object>()
function abortError(): DOMException { return new DOMException('授權操作已取消', 'AbortError') }
function boundedText(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum
}
function token<T>(manifest: ProtectedManifest): ProtectedOperation<T> {
  const value = Object.freeze(Object.create(null)) as ProtectedOperation<T>
  manifests.set(value as object, Object.freeze(manifest))
  return value
}
function detachedJson(value: unknown): unknown {
  let nodes = 0
  const encoder = new TextEncoder()
  const clone = (current: unknown, depth: number): unknown => {
    nodes += 1
    if (nodes > 4096 || depth > 32) throw abortError()
    if (current === null || typeof current === 'boolean') return current
    if (typeof current === 'number') {
      if (!Number.isFinite(current) || (Number.isInteger(current) && !Number.isSafeInteger(current))) throw abortError()
      return current
    }
    if (typeof current === 'string') {
      if (encoder.encode(current).byteLength > 65_536) throw abortError()
      return current
    }
    if (Array.isArray(current)) {
      if (Object.getPrototypeOf(current) !== Array.prototype || current.length > 4096) throw abortError()
      const keys = Reflect.ownKeys(current)
      if (keys.length !== current.length + 1 || !keys.includes('length')) throw abortError()
      return current.map((item, index) => {
        const descriptor = Object.getOwnPropertyDescriptor(current, String(index))
        if (!descriptor || !('value' in descriptor) || !descriptor.enumerable ||
            !descriptor.configurable || !descriptor.writable) throw abortError()
        return clone(item, depth + 1)
      })
    }
    if (typeof current !== 'object' || Object.getPrototypeOf(current) !== Object.prototype) throw abortError()
    const keys = Reflect.ownKeys(current)
    if (keys.length > 256 || keys.some((key) => typeof key !== 'string' || encoder.encode(key).byteLength > 256)) {
      throw abortError()
    }
    const result: Record<string, unknown> = {}
    for (const key of keys as string[]) {
      const descriptor = Object.getOwnPropertyDescriptor(current, key)
      if (!descriptor || !('value' in descriptor) || !descriptor.enumerable ||
          !descriptor.configurable || !descriptor.writable) throw abortError()
      Object.defineProperty(result, key, {
        value: clone(descriptor.value, depth + 1), enumerable: true, configurable: true, writable: true,
      })
    }
    return result
  }
  return clone(value, 0)
}
function exactConfiguration(value: EndpointConfigurationConfirmation): EndpointConfigurationConfirmation {
  const expected = ['system_prompt', 'input_schema', 'response_schema', 'human_docs', 'rate_limit']
  if (value === null || typeof value !== 'object' || Array.isArray(value) ||
      Object.getPrototypeOf(value) !== Object.prototype || Reflect.ownKeys(value).length !== expected.length ||
      !expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))) {
    throw abortError()
  }
  return detachedJson(value) as EndpointConfigurationConfirmation
}
function exactVersionConfiguration(value: EndpointVersionConfiguration): EndpointVersionConfiguration {
  const expected = [
    'original_requirement_text', 'system_prompt', 'model_config_snapshot',
    'retry_policy', 'input_schema', 'response_schema',
  ]
  if (value === null || typeof value !== 'object' || Array.isArray(value) ||
      Object.getPrototypeOf(value) !== Object.prototype || Reflect.ownKeys(value).length !== expected.length ||
      !expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))) {
    throw abortError()
  }
  return detachedJson(value) as EndpointVersionConfiguration
}

export function createSendChatOperation(message: string, sessionId: string | null): ProtectedOperation<ChatReply> {
  const trimmed = message.trim()
  if (!boundedText(trimmed, 16_384) || (sessionId !== null && !boundedText(sessionId, 128))) throw abortError()
  return token<ChatReply>({ kind: 'send-chat', message: trimmed, sessionId })
}
export function createRedactionOperation(
  endpointId: string, invocationId: string, request: Readonly<RedactionRequest>, idempotencyKey: string,
): ProtectedOperation<RedactionReceipt> {
  if (!boundedText(endpointId, 128) || !boundedText(invocationId, 128) || !boundedText(idempotencyKey, 128)) throw abortError()
  return token<RedactionReceipt>({
    kind: 'redact-invocation', endpointId, invocationId, idempotencyKey,
    request: Object.freeze({ ...request }),
  })
}
export function createEndpointDraftOperation(input: EndpointDraftInput): ProtectedOperation<DraftReceipt> {
  return token<DraftReceipt>({
    kind: 'create-endpoint-draft',
    input: Object.freeze({
      originalRequirementText: input.originalRequirementText,
      selectedSkills: Object.freeze([...input.selectedSkills]),
      responseMode: input.responseMode,
    }),
  })
}
export function createPublishEndpointOperation(
  draftId: string, slug: string, confirmation: EndpointConfigurationConfirmation,
): ProtectedOperation<PublishReceipt> {
  return token<PublishReceipt>({ kind: 'publish-endpoint', draftId, slug, configuration: exactConfiguration(confirmation) })
}
export function createEndpointVersionOperation(
  endpointId: string, configuration: EndpointVersionConfiguration,
): ProtectedOperation<VersionReceipt> {
  return token<VersionReceipt>({ kind: 'create-endpoint-version', endpointId, configuration: exactVersionConfiguration(configuration) })
}

export function createCredentialOperation(
  endpointId: string, input: CredentialCreateInput,
): ProtectedOperation<CredentialCreateReceipt> {
  const expected = ['name', 'purpose', 'expiresAt', 'ipAllowlist', 'rateLimitRequests']
  if (!boundedText(endpointId, 128) || input === null || typeof input !== 'object' || Array.isArray(input) ||
      Object.getPrototypeOf(input) !== Object.prototype || Reflect.ownKeys(input).length !== expected.length ||
      !expected.every((key) => Object.prototype.hasOwnProperty.call(input, key))) throw abortError()
  return token<CredentialCreateReceipt>({
    kind: 'create-credential', endpointId,
    input: Object.freeze(detachedJson(input) as CredentialCreateInput),
  })
}

export function createRevokeCredentialOperation(
  endpointId: string, credentialId: string,
): ProtectedOperation<void> {
  if (!boundedText(endpointId, 128) || !boundedText(credentialId, 128)) throw abortError()
  return token<void>({ kind: 'revoke-credential', endpointId, credentialId })
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
  manifest: ProtectedManifest, csrfToken: string, signal: AbortSignal,
): Promise<T> {
  if (signal.aborted) throw abortError()
  switch (manifest.kind) {
    case 'send-chat':
      return await sendChat(manifest.message, manifest.sessionId, csrfToken, signal) as T
    case 'redact-invocation':
      return await redactInvocation(
        manifest.endpointId, manifest.invocationId, manifest.request,
        manifest.idempotencyKey, csrfToken, signal,
      ) as T
    case 'create-endpoint-draft':
      return await createDraft({
        originalRequirementText: manifest.input.originalRequirementText,
        selectedSkills: [...manifest.input.selectedSkills],
        responseMode: manifest.input.responseMode,
      }, csrfToken, { signal }) as T
    case 'publish-endpoint':
      return await publishEndpoint({
        draftId: manifest.draftId,
        slug: manifest.slug,
        configurationConfirmation: manifest.configuration,
      }, csrfToken, { signal }) as T
    case 'create-endpoint-version':
      return await createEndpointVersion(
        manifest.endpointId, { configuration: manifest.configuration }, csrfToken, { signal },
      ) as T
    case 'create-credential':
      return await createCredential(manifest.endpointId, {
        name: manifest.input.name,
        purpose: manifest.input.purpose,
        expiresAt: manifest.input.expiresAt,
        ipAllowlist: [...manifest.input.ipAllowlist],
        rateLimitRequests: manifest.input.rateLimitRequests,
      }, csrfToken, { signal }) as T
    case 'revoke-credential':
      return await revokeCredential(manifest.endpointId, manifest.credentialId, csrfToken, { signal }) as T
  }
}
