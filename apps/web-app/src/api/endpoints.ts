import {
  ApiFormatError,
  apiRequest,
  byteLength,
  exactObject,
  type ApiRoute,
} from './client'

const OWNER_ENDPOINT_ID = /^[A-Za-z0-9_-]{1,128}$/
const MANAGEMENT_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const CURSOR = /^[A-Za-z0-9_-]{1,512}$/
const SKILL_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/
const DOCS_SLUG = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/
const STATUSES = new Set(['active', 'disabled', 'archived'])
const CREDENTIAL_STATUSES = new Set(['active', 'inactive', 'expired', 'revoked'])
const INITIAL_API_KEY = /^pk_[A-Za-z0-9_-]{43}$/
const MAX_REQUEST_BYTES = 32 * 1024

export interface RequestOptions {
  signal?: AbortSignal
  onCsrfSuccessor?: (token: string) => void
}
export interface MutationOptions extends RequestOptions {
  onCsrfSuccessor?: (token: string) => void
}
export interface OwnerEndpointItem {
  endpointId: string; slug: string; status: 'active' | 'disabled' | 'archived'
  currentVersionId: string | null; currentVersionNumber: number | null; updatedAt: number
}
export interface OwnerEndpointPage { items: OwnerEndpointItem[]; nextCursor: string | null }
export interface OwnerEndpointDetail extends OwnerEndpointItem { ownerUserId: string; createdAt: number }
export interface DraftReceipt {
  draftId: string; expiresAt: number
  preview: {
    endpointName: string; suggestedSlug: string; behaviorSummary: string; selectedSkills: string[]
    recommendedTools: string[]; toolCapabilities: Record<string, string>; systemPrompt: string
    inputSchema: JsonValue | null; responseSchema: JsonObject; humanDocs: string
    rateLimit: { endpointPerMinute: number; credentialPerMinute: number }; warnings: string[]
  }
}
export interface PublishReceipt {
  endpointId: string; versionId: string; versionNumber: 1; status: 'active'; initialApiKey: string
}
export interface VersionReceipt {
  endpointId: string; versionId: string; versionNumber: number; currentVersionId: string; schemaChanged: boolean
}
export interface CredentialSummary {
  credentialId: string; name: string; purpose: string; keyPrefix: string; keyLast4: string
  status: 'active' | 'inactive' | 'expired' | 'revoked'; expiresAt: number; lastUsedAt: number | null
  createdAt: number; revokedAt: number | null; ipAllowlist: string[]; rateLimitRequests: number
}
export interface CredentialCreateReceipt extends CredentialSummary { initialApiKey: string }
export interface CredentialPage { items: CredentialSummary[] }
export type JsonValue = null | boolean | number | string | JsonValue[] | JsonObject
export interface JsonObject { [key: string]: JsonValue }
export interface EndpointDocs {
  endpoint: { id: string; slug: string; version: number; status: 'active' | 'disabled' | 'archived' }
  invokeUrl: string; authentication: { scheme: 'bearer'; header: 'Authorization' }
  requestSchema: JsonObject; responseSchema: JsonObject
  rateLimit: { requests: number; windowSeconds: number }
  examples: { curl: string; python: string }
  errors: Array<{ code: string; status: number; message: string }>
}

function fail(): never { throw new ApiFormatError() }
function plainArray(value: unknown, maximum: number): unknown[] {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype || value.length > maximum) fail()
  const keys = Reflect.ownKeys(value)
  if (keys.length !== value.length + 1 || !keys.includes('length')) fail()
  for (let index = 0; index < value.length; index += 1) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(index))
    if (!descriptor || !('value' in descriptor) || !descriptor.enumerable || !descriptor.configurable || !descriptor.writable) fail()
  }
  return value
}
function text(value: unknown, maximum: number, allowEmpty = false): string {
  if (typeof value !== 'string' || (!allowEmpty && value.length === 0) || value.length > maximum ||
      byteLength(value) > maximum * 4 || Array.from(value).some((character) => character.charCodeAt(0) < 32 && character !== '\n' && character !== '\t')) fail()
  return value
}
function identifier(value: unknown, pattern = MANAGEMENT_ID): string {
  if (typeof value !== 'string' || !pattern.test(value)) fail()
  return value
}
function safeInteger(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) fail()
  return value as number
}
function finite(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) fail()
  return value
}
function nullableFinite(value: unknown): number | null { return value === null ? null : finite(value) }
function status(value: unknown): 'active' | 'disabled' | 'archived' {
  if (typeof value !== 'string' || !STATUSES.has(value)) fail()
  return value as 'active' | 'disabled' | 'archived'
}
function exactStringArray(value: unknown, maximum: number, itemMaximum: number): string[] {
  return plainArray(value, maximum).map((item) => text(item, itemMaximum))
}

function cloneJson(value: unknown, maximumDepth = 24, maximumNodes = 2000, maximumStringBytes = 16_384): JsonValue {
  let nodes = 0
  const visit = (current: unknown, depth: number): JsonValue => {
    nodes += 1
    if (nodes > maximumNodes || depth > maximumDepth) fail()
    if (current === null || typeof current === 'boolean') return current
    if (typeof current === 'number') {
      if (!Number.isFinite(current) || (Number.isInteger(current) && !Number.isSafeInteger(current))) fail()
      return current
    }
    if (typeof current === 'string') {
      if (byteLength(current) > maximumStringBytes) fail()
      return current
    }
    if (Array.isArray(current)) return plainArray(current, maximumNodes).map((item) => visit(item, depth + 1))
    const descriptors = exactObject(current, Object.keys(current as object))
    if (!descriptors || Object.keys(descriptors).length > 256) fail()
    const result: JsonObject = {}
    for (const [key, item] of Object.entries(descriptors)) {
      if (byteLength(key) > 256) fail()
      Object.defineProperty(result, key, {
        value: visit(item, depth + 1), enumerable: true, configurable: true, writable: true,
      })
    }
    return result
  }
  return visit(value, 0)
}
function jsonObject(value: unknown, depth = 24, nodes = 2000, stringBytes = 16_384): JsonObject {
  const result = cloneJson(value, depth, nodes, stringBytes)
  if (result === null || Array.isArray(result) || typeof result !== 'object') fail()
  return result
}
function encodeBody(value: unknown): string {
  const body = JSON.stringify(value)
  if (typeof body !== 'string' || byteLength(body) > MAX_REQUEST_BYTES) fail()
  return body
}
function csrf(value: unknown): string { return text(value, 4096) }
function managementId(value: unknown): string { return identifier(value) }
function ownerId(value: unknown): string { return identifier(value, OWNER_ENDPOINT_ID) }
function route(value: string): ApiRoute { return value as ApiRoute }
function initialApiKey(value: unknown): string {
  if (typeof value !== 'string' || !INITIAL_API_KEY.test(value)) fail()
  try {
    const token = value.slice(3)
    const decoded = atob(token.replace(/-/g, '+').replace(/_/g, '/') + '=')
    const canonical = btoa(decoded).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    if (decoded.length !== 32 || canonical !== token) fail()
  } catch (error) {
    if (error instanceof ApiFormatError) throw error
    fail()
  }
  return value
}

function parseVersionPair(id: unknown, number: unknown): { id: string | null; number: number | null } {
  if (id === null && number === null) return { id: null, number: null }
  if (id === null || number === null) fail()
  return { id: identifier(id), number: safeInteger(number, 1, 2_147_483_647) }
}
function parseEndpointItem(value: unknown): OwnerEndpointItem {
  const row = exactObject(value, ['endpoint_id', 'slug', 'status', 'current_version_id', 'current_version_number', 'updated_at'])
  if (!row) fail()
  const current = parseVersionPair(row.current_version_id, row.current_version_number)
  return {
    endpointId: managementId(row.endpoint_id), slug: text(row.slug, 256), status: status(row.status),
    currentVersionId: current.id, currentVersionNumber: current.number, updatedAt: finite(row.updated_at),
  }
}
export function parseOwnerEndpointList(value: unknown): OwnerEndpointPage {
  const root = exactObject(value, ['items', 'next_cursor'])
  if (!root) fail()
  const items = plainArray(root.items, 100).map(parseEndpointItem)
  const nextCursor = root.next_cursor === null ? null : identifier(root.next_cursor, CURSOR)
  return { items, nextCursor }
}
export function parseOwnerEndpointDetail(value: unknown): OwnerEndpointDetail {
  const row = exactObject(value, ['endpoint_id', 'owner_user_id', 'slug', 'status', 'current_version_id',
    'current_version_number', 'created_at', 'updated_at'])
  if (!row) fail()
  const item = parseEndpointItem({
    endpoint_id: row.endpoint_id, slug: row.slug, status: row.status, current_version_id: row.current_version_id,
    current_version_number: row.current_version_number, updated_at: row.updated_at,
  })
  return { ...item, ownerUserId: managementId(row.owner_user_id), createdAt: finite(row.created_at) }
}

export function parseDraftReceipt(value: unknown): DraftReceipt {
  const root = exactObject(value, ['draft_id', 'expires_at', 'preview'])
  if (!root) fail()
  const preview = exactObject(root.preview, ['endpoint_name', 'suggested_slug', 'behavior_summary', 'selected_skills',
    'recommended_tools', 'tool_capabilities', 'system_prompt', 'input_schema', 'response_schema', 'human_docs', 'rate_limit', 'warnings'])
  if (!preview) fail()
  const rate = exactObject(preview.rate_limit, ['endpoint_per_minute', 'credential_per_minute'])
  const capabilities = exactObject(preview.tool_capabilities, Object.keys(preview.tool_capabilities as object))
  if (!rate || !capabilities || Object.keys(capabilities).length > 256) fail()
  const toolCapabilities: Record<string, string> = {}
  for (const [key, item] of Object.entries(capabilities)) Object.defineProperty(toolCapabilities, text(key, 128), {
    value: text(item, 16_384, true), enumerable: true, configurable: true, writable: true,
  })
  const inputSchema = preview.input_schema === null ? null : cloneJson(preview.input_schema)
  const responseSchema = jsonObject(preview.response_schema)
  return {
    draftId: managementId(root.draft_id), expiresAt: finite(root.expires_at),
    preview: {
      endpointName: text(preview.endpoint_name, 16_384), suggestedSlug: text(preview.suggested_slug, 128),
      behaviorSummary: text(preview.behavior_summary, 16_384), selectedSkills: exactStringArray(preview.selected_skills, 32, 128),
      recommendedTools: exactStringArray(preview.recommended_tools, 256, 128), toolCapabilities,
      systemPrompt: text(preview.system_prompt, 16_384, true), inputSchema, responseSchema,
      humanDocs: text(preview.human_docs, 16_384, true),
      rateLimit: { endpointPerMinute: safeInteger(rate.endpoint_per_minute, 1, 10_000), credentialPerMinute: safeInteger(rate.credential_per_minute, 1, 10_000) },
      warnings: exactStringArray(preview.warnings, 256, 16_384),
    },
  }
}
export function parsePublishReceipt(value: unknown): PublishReceipt {
  const root = exactObject(value, ['endpoint_id', 'version_id', 'version_number', 'status', 'initial_api_key'])
  if (!root || root.version_number !== 1 || root.status !== 'active') fail()
  return {
    endpointId: managementId(root.endpoint_id), versionId: managementId(root.version_id), versionNumber: 1,
    status: 'active', initialApiKey: initialApiKey(root.initial_api_key),
  }
}
export function parseVersionReceipt(value: unknown, expectedEndpointId: string): VersionReceipt {
  const root = exactObject(value, ['endpoint_id', 'version_id', 'version_number', 'current_version_id', 'schema_changed'])
  if (!root) fail()
  const endpointId = managementId(root.endpoint_id); const versionId = managementId(root.version_id)
  const currentVersionId = managementId(root.current_version_id)
  if (endpointId !== managementId(expectedEndpointId) || currentVersionId !== versionId || typeof root.schema_changed !== 'boolean') fail()
  return { endpointId, versionId, versionNumber: safeInteger(root.version_number, 2, 2_147_483_647), currentVersionId, schemaChanged: root.schema_changed }
}

function parseCredential(value: unknown): CredentialSummary {
  const root = exactObject(value, ['credential_id', 'name', 'purpose', 'key_prefix', 'key_last4', 'status',
    'expires_at', 'last_used_at', 'created_at', 'revoked_at', 'ip_allowlist', 'rate_limit_requests'])
  if (!root || typeof root.status !== 'string' || !CREDENTIAL_STATUSES.has(root.status)) fail()
  const createdAt = finite(root.created_at); const expiresAt = finite(root.expires_at)
  const lastUsedAt = nullableFinite(root.last_used_at); const revokedAt = nullableFinite(root.revoked_at)
  if (expiresAt <= createdAt || (lastUsedAt !== null && lastUsedAt < createdAt) || (revokedAt !== null && revokedAt < createdAt)) fail()
  const ipAllowlist = exactStringArray(root.ip_allowlist, 256, 128)
  if (ipAllowlist.some((entry) => entry.includes('%'))) fail()
  const name = safeCredentialText(root.name, 256); const purpose = safeCredentialText(root.purpose, 2048)
  const keyLast4 = text(root.key_last4, 4); if (keyLast4.length !== 4) fail()
  return {
    credentialId: managementId(root.credential_id), name, purpose,
    keyPrefix: text(root.key_prefix, 32), keyLast4,
    status: root.status as CredentialSummary['status'], expiresAt, lastUsedAt, createdAt, revokedAt,
    ipAllowlist, rateLimitRequests: safeInteger(root.rate_limit_requests, 1, 10_000),
  }
}
function safeCredentialText(value: unknown, maximum: number): string {
  const result = text(value, maximum); const lower = result.toLowerCase()
  if (result.trim() !== result || ['pk_', 'sk_', 'sk-', 'bearer'].some((marker) => lower.includes(marker)) ||
      (result.length === 64 && /^[0-9a-f]{64}$/i.test(result))) fail()
  return result
}
export function parseCredentialList(value: unknown): CredentialPage {
  const root = exactObject(value, ['items'])
  if (!root) fail()
  return { items: plainArray(root.items, 10_000).map(parseCredential) }
}
export function parseCredentialCreateReceipt(value: unknown): CredentialCreateReceipt {
  const root = exactObject(value, ['credential_id', 'name', 'purpose', 'key_prefix', 'key_last4', 'status',
    'expires_at', 'last_used_at', 'created_at', 'revoked_at', 'ip_allowlist', 'rate_limit_requests', 'initial_api_key'])
  if (!root) fail()
  const summary = parseCredential({
    credential_id: root.credential_id, name: root.name, purpose: root.purpose, key_prefix: root.key_prefix,
    key_last4: root.key_last4, status: root.status, expires_at: root.expires_at, last_used_at: root.last_used_at,
    created_at: root.created_at, revoked_at: root.revoked_at, ip_allowlist: root.ip_allowlist,
    rate_limit_requests: root.rate_limit_requests,
  })
  const key = initialApiKey(root.initial_api_key)
  if (summary.keyPrefix !== key.slice(0, 12) || summary.keyLast4 !== key.slice(-4)) fail()
  return { ...summary, initialApiKey: key }
}

const DOCS_INVOKE_URL = '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke'
const DOCS_CURL = "curl -X POST '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke' -H 'Authorization: Bearer ${API_KEY}' -H 'Content-Type: application/json' --data '{\"input\":{},\"session_id\":\"${SESSION_ID}\",\"metadata\":{\"endpoint_id\":\"${ENDPOINT_ID}\"}}'"
const DOCS_PYTHON = "import json\nimport urllib.request\nurl = '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke'\npayload = {'input': {}, 'session_id': '${SESSION_ID}', 'metadata': {'endpoint_id': '${ENDPOINT_ID}'}}\nrequest = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Authorization': 'Bearer ${API_KEY}', 'Content-Type': 'application/json'}, method='POST')\nwith urllib.request.urlopen(request) as response:\n    print(response.read().decode('utf-8'))"
const DOCS_ERRORS = [
  ['endpoint_not_found', 404, '找不到 endpoint slug。'], ['invalid_api_key', 401, 'API key 無效。'],
  ['api_key_expired', 401, 'API key 已過期。'], ['endpoint_disabled', 403, 'Endpoint 已停用。'],
  ['endpoint_archived', 410, 'Endpoint 已封存。'], ['input_schema_invalid', 422, 'Input 不符合 schema。'],
  ['model_output_schema_invalid', 502, '模型輸出不符合 response schema。'], ['rate_limit_exceeded', 429, '呼叫頻率超過限制。'],
  ['model_timeout', 504, '模型供應商逾時。'], ['tool_execution_failed', 502, '工具執行失敗。'],
  ['tool_timeout', 504, '工具執行逾時。'], ['endpoint_misconfigured', 500, 'Endpoint 設定錯誤。'],
  ['internal_error', 500, '伺服器內部錯誤。'],
] as const
export function parseEndpointDocs(value: unknown): EndpointDocs {
  const root = exactObject(value, ['endpoint', 'invoke_url', 'authentication', 'request_schema', 'response_schema', 'rate_limit', 'examples', 'errors'])
  if (!root || root.invoke_url !== DOCS_INVOKE_URL) fail()
  const endpoint = exactObject(root.endpoint, ['id', 'slug', 'version', 'status'])
  const authentication = exactObject(root.authentication, ['scheme', 'header'])
  const request = exactObject(root.request_schema, ['type', 'additionalProperties', 'required', 'properties'])
  const rate = exactObject(root.rate_limit, ['requests', 'window_seconds'])
  const examples = exactObject(root.examples, ['curl', 'python'])
  if (!endpoint || !authentication || !request || !rate || !examples || authentication.scheme !== 'bearer' ||
      authentication.header !== 'Authorization' || request.type !== 'object' || request.additionalProperties !== false ||
      examples.curl !== DOCS_CURL || examples.python !== DOCS_PYTHON) fail()
  const required = plainArray(request.required, 1)
  if (required.length !== 1 || required[0] !== 'input') fail()
  const properties = exactObject(request.properties, ['input', 'session_id', 'metadata'])
  if (!properties) fail()
  const session = exactObject(properties.session_id, ['anyOf', 'x-utf8-max-bytes', 'description'])
  const metadata = exactObject(properties.metadata, ['anyOf'])
  if (!session || !metadata || session['x-utf8-max-bytes'] !== 128 ||
      session.description !== 'Optional Published session identifier；上限 128 UTF-8 bytes。') fail()
  const expectedSession = [{ type: 'string', maxLength: 128 }, { type: 'null' }]
  const expectedMetadata = [{ type: 'object' }, { type: 'null' }]
  if (JSON.stringify(cloneJson(session.anyOf, 4, 16)) !== JSON.stringify(expectedSession) ||
      JSON.stringify(cloneJson(metadata.anyOf, 4, 16)) !== JSON.stringify(expectedMetadata)) fail()
  const errorRows = plainArray(root.errors, DOCS_ERRORS.length)
  if (errorRows.length !== DOCS_ERRORS.length) fail()
  const errors = errorRows.map((entry, index) => {
    const row = exactObject(entry, ['code', 'status', 'message']); const expected = DOCS_ERRORS[index]
    if (!row || row.code !== expected[0] || row.status !== expected[1] || row.message !== expected[2]) fail()
    return { code: expected[0], status: expected[1], message: expected[2] }
  })
  const requestSchema = jsonObject(root.request_schema, 32, 4096, 65_536)
  const responseSchema = jsonObject(root.response_schema, 32, 4096, 65_536)
  if (byteLength(JSON.stringify(properties.input)) > 65_536 || byteLength(JSON.stringify(responseSchema)) > 65_536) fail()
  return {
    endpoint: { id: managementId(endpoint.id), slug: identifier(endpoint.slug, DOCS_SLUG), version: safeInteger(endpoint.version, 1, 2_147_483_647), status: status(endpoint.status) },
    invokeUrl: DOCS_INVOKE_URL, authentication: { scheme: 'bearer', header: 'Authorization' },
    requestSchema, responseSchema,
    rateLimit: { requests: safeInteger(rate.requests, 1, 10_000), windowSeconds: safeInteger(rate.window_seconds, 1, 86_400) },
    examples: { curl: DOCS_CURL, python: DOCS_PYTHON }, errors,
  }
}

async function get(routeValue: string, options: RequestOptions = {}): Promise<unknown> {
  return apiRequest(route(routeValue), {
    method: 'GET', expectedStatus: 200, signal: options.signal, onCsrfSuccessor: options.onCsrfSuccessor,
  })
}
async function mutate(routeValue: string, body: string | undefined, csrfToken: string, options: MutationOptions, expectedStatus: number): Promise<unknown> {
  return apiRequest(route(routeValue), {
    method: 'POST', ...(body === undefined ? {} : { body }), csrfToken: csrf(csrfToken),
    expectedStatus, signal: options.signal, onCsrfSuccessor: options.onCsrfSuccessor,
  })
}
export async function listOwnerEndpoints(options: { scope?: 'owner' | 'all'; limit?: number; cursor?: string } = {}, requestOptions: RequestOptions = {}): Promise<OwnerEndpointPage> {
  const scope = options.scope ?? 'owner'; if (scope !== 'owner' && scope !== 'all') fail()
  const limit = safeInteger(options.limit ?? 20, 1, 100)
  let path = `/api/published-endpoints?scope=${scope}&limit=${limit}`
  if (options.cursor !== undefined) path += `&cursor=${identifier(options.cursor, CURSOR)}`
  return parseOwnerEndpointList(await get(path, requestOptions))
}
export async function getOwnerEndpoint(endpointId: string, options: RequestOptions = {}): Promise<OwnerEndpointDetail> {
  const id = ownerId(endpointId)
  const result = parseOwnerEndpointDetail(await get(`/api/published-endpoints/${id}`, options))
  if (result.endpointId !== id) fail()
  return result
}
export async function createDraft(input: { originalRequirementText: string; selectedSkills: string[]; responseMode: 'text' | 'structured' }, csrfToken: string, options: MutationOptions = {}): Promise<DraftReceipt> {
  const requirement = text(input.originalRequirementText, 16_384)
  if (requirement.trim() !== requirement || byteLength(requirement) > 16_384) fail()
  const selectedSkills = exactStringArray(input.selectedSkills, 32, 128)
  if (selectedSkills.length === 0 || selectedSkills.some((skill) => !SKILL_ID.test(skill)) ||
      selectedSkills.join('\0') !== [...new Set(selectedSkills)].sort().join('\0') || !['text', 'structured'].includes(input.responseMode)) fail()
  const body = encodeBody({ original_requirement_text: requirement, selected_skills: selectedSkills, response_mode: input.responseMode })
  return parseDraftReceipt(await mutate('/api/published-endpoints/draft', body, csrfToken, options, 201))
}
export async function publishEndpoint(input: { draftId: string; slug: string; configurationConfirmation: Record<string, unknown> }, csrfToken: string, options: MutationOptions = {}): Promise<PublishReceipt> {
  const draftId = managementId(input.draftId); if (!SLUG.test(input.slug)) fail()
  const configuration = jsonObject(input.configurationConfirmation)
  const body = encodeBody({ draft_id: draftId, slug: input.slug, configuration_confirmation: configuration })
  return parsePublishReceipt(await mutate('/api/published-endpoints', body, csrfToken, options, 201))
}
export async function createEndpointVersion(endpointId: string, input: { configuration: Record<string, unknown> }, csrfToken: string, options: MutationOptions = {}): Promise<VersionReceipt> {
  const id = managementId(endpointId); const body = encodeBody({ configuration: jsonObject(input.configuration) })
  return parseVersionReceipt(await mutate(`/api/published-endpoints/${id}/versions`, body, csrfToken, options, 201), id)
}
export async function listCredentials(endpointId: string, options: RequestOptions = {}): Promise<CredentialPage> {
  const id = managementId(endpointId)
  return parseCredentialList(await get(`/api/published-endpoints/${id}/credentials`, options))
}
export async function createCredential(endpointId: string, input: { name: string; purpose: string; expiresAt: number; ipAllowlist: string[]; rateLimitRequests: number }, csrfToken: string, options: MutationOptions = {}): Promise<CredentialCreateReceipt> {
  const id = managementId(endpointId); const name = safeCredentialText(input.name, 256); const purpose = safeCredentialText(input.purpose, 2048)
  const body = encodeBody({ name, purpose, expires_at: finite(input.expiresAt), ip_allowlist: exactStringArray(input.ipAllowlist, 256, 128), rate_limit_requests: safeInteger(input.rateLimitRequests, 1, 10_000) })
  return parseCredentialCreateReceipt(await mutate(`/api/published-endpoints/${id}/credentials`, body, csrfToken, options, 201))
}
export async function revokeCredential(endpointId: string, credentialId: string, csrfToken: string, options: MutationOptions = {}): Promise<void> {
  await mutate(`/api/published-endpoints/${managementId(endpointId)}/credentials/${managementId(credentialId)}/revoke`, undefined, csrfToken, options, 204)
}
export async function getOwnerEndpointDocs(endpointId: string, options: RequestOptions = {}): Promise<EndpointDocs> {
  const id = managementId(endpointId)
  const result = parseEndpointDocs(await get(`/api/published-endpoints/${id}/docs`, options))
  if (result.endpoint.id !== id) fail()
  return result
}
