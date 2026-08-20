import { useCallback, useEffect, useRef, useState } from 'react'
import type { DraftReceipt } from '../api/endpoints'
import { useSession } from '../app/SessionProvider'
import {
  createEndpointDraftOperation,
  createEndpointVersionOperation,
  createPublishEndpointOperation,
  type EndpointConfigurationConfirmation,
  type ProtectedStateOwner,
} from '../app/sessionAuthority'
import RateLimitConfirmation from '../features/endpoints/RateLimitConfirmation'
import SchemaEditor from '../features/endpoints/SchemaEditor'
import SkillBrowser from '../features/endpoints/SkillBrowser'

const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/
export const BUILDER_ERROR_MESSAGE = '目前無法完成要求，請稍後再試。'

export interface EndpointBuilderPageProps {
  mode: 'new' | 'version'
  endpointId?: string
  onClose(): void
}

type Success =
  | { kind: 'new'; endpointId: string; initialApiKey: string }
  | { kind: 'version'; endpointId: string; versionNumber: number }

function confirmation(preview: DraftReceipt['preview']): EndpointConfigurationConfirmation {
  return {
    system_prompt: preview.systemPrompt,
    input_schema: preview.inputSchema,
    response_schema: preview.responseSchema,
    human_docs: preview.humanDocs,
    rate_limit: {
      endpoint_per_minute: preview.rateLimit.endpointPerMinute,
      credential_per_minute: preview.rateLimit.credentialPerMinute,
    },
  }
}

export default function EndpointBuilderPage({ mode, endpointId, onClose }: EndpointBuilderPageProps) {
  const { logout, registerProtectedStateOwner, runAuthorized, user } = useSession()
  const [requirement, setRequirement] = useState('')
  const [selectedSkills, setSelectedSkills] = useState<string[]>([])
  const [responseMode, setResponseMode] = useState<'text' | 'structured'>('text')
  const [draft, setDraft] = useState<DraftReceipt | null>(null)
  const [slug, setSlug] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [drafting, setDrafting] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState<Success | null>(null)
  const mounted = useRef(false)
  const epoch = useRef(0)
  const draftOwner = useRef<number | null>(null)
  const submitOwner = useRef<number | null>(null)
  const draftController = useRef<AbortController | null>(null)
  const submitController = useRef<AbortController | null>(null)
  const protectedOwner = useRef<ProtectedStateOwner | null>(null)

  const erase = useCallback((publishState: boolean) => {
    epoch.current += 1
    draftController.current?.abort()
    submitController.current?.abort()
    draftController.current = null
    submitController.current = null
    draftOwner.current = null
    submitOwner.current = null
    if (publishState && mounted.current) {
      setRequirement('')
      setSelectedSkills([])
      setResponseMode('text')
      setDraft(null)
      setSlug('')
      setError(null)
      setDrafting(false)
      setSubmitting(false)
      setSuccess(null)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    const registration = registerProtectedStateOwner(() => erase(true))
    protectedOwner.current = registration.owner
    return () => {
      mounted.current = false
      protectedOwner.current = null
      registration.unregister()
      erase(false)
    }
  }, [mode, endpointId, user?.id, erase, registerProtectedStateOwner])

  async function createDraft() {
    if (draft !== null || success !== null || draftOwner.current !== null || submitOwner.current !== null) return
    const trimmed = requirement.trim()
    if (trimmed.length === 0 || trimmed !== requirement || selectedSkills.length === 0 || selectedSkills.length > 32) {
      setError('請輸入首尾無空白的需求，並選擇 1 至 32 個 Skills。')
      return
    }
    const owner = protectedOwner.current
    if (!owner) return
    const requestEpoch = ++epoch.current
    draftOwner.current = requestEpoch
    const controller = new AbortController()
    draftController.current = controller
    setDrafting(true)
    setError(null)
    setDraft(null)
    setSuccess(null)
    setSlug('')
    try {
      const receipt = await runAuthorized({
        owner,
        operation: createEndpointDraftOperation({
          originalRequirementText: trimmed,
          selectedSkills: [...selectedSkills].sort(),
          responseMode,
        }),
        signal: controller.signal,
      })
      if (!mounted.current || controller.signal.aborted || epoch.current !== requestEpoch || draftOwner.current !== requestEpoch) return
      setDraft(receipt)
      setSlug(receipt.preview.suggestedSlug)
    } catch {
      if (mounted.current && !controller.signal.aborted && epoch.current === requestEpoch && draftOwner.current === requestEpoch) {
        setError(BUILDER_ERROR_MESSAGE)
      }
    } finally {
      if (draftController.current === controller) draftController.current = null
      if (mounted.current && epoch.current === requestEpoch && draftOwner.current === requestEpoch) {
        draftOwner.current = null
        setDrafting(false)
      }
    }
  }

  async function submit() {
    if (success !== null || submitOwner.current !== null || draftOwner.current !== null || draft === null) return
    if (mode === 'new' && !SLUG.test(slug)) {
      setError('Slug 必須為小寫英數字與連字號，且長度不超過 63。')
      return
    }
    if (mode === 'version' && !endpointId) return
    const owner = protectedOwner.current
    if (!owner) return
    const requestEpoch = ++epoch.current
    submitOwner.current = requestEpoch
    const controller = new AbortController()
    submitController.current = controller
    setSubmitting(true)
    setError(null)
    setSuccess(null)
    try {
      const exactConfirmation = confirmation(draft.preview)
      if (mode === 'new') {
        const receipt = await runAuthorized({
          owner,
          operation: createPublishEndpointOperation(draft.draftId, slug, exactConfirmation),
          signal: controller.signal,
        })
        if (!mounted.current || controller.signal.aborted || epoch.current !== requestEpoch || submitOwner.current !== requestEpoch) return
        setSuccess({ kind: 'new', endpointId: receipt.endpointId, initialApiKey: receipt.initialApiKey })
      } else {
        const receipt = await runAuthorized({
          owner,
          operation: createEndpointVersionOperation(endpointId!, exactConfirmation),
          signal: controller.signal,
        })
        if (!mounted.current || controller.signal.aborted || epoch.current !== requestEpoch || submitOwner.current !== requestEpoch) return
        setSuccess({ kind: 'version', endpointId: receipt.endpointId, versionNumber: receipt.versionNumber })
      }
    } catch {
      if (mounted.current && !controller.signal.aborted && epoch.current === requestEpoch && submitOwner.current === requestEpoch) {
        setError(BUILDER_ERROR_MESSAGE)
      }
    } finally {
      if (submitController.current === controller) submitController.current = null
      if (mounted.current && epoch.current === requestEpoch && submitOwner.current === requestEpoch) {
        submitOwner.current = null
        setSubmitting(false)
      }
    }
  }

  const busy = drafting || submitting || success !== null
  const intentLocked = busy || draft !== null
  return (
    <main className="app-shell">
      <section className="welcome-card endpoint-detail" aria-labelledby="builder-title">
        <nav aria-label="Builder 導覽">
          <button type="button" onClick={onClose}>返回端點管理</button>
          <button type="button" onClick={() => { void logout().catch(() => { if (mounted.current) setError(BUILDER_ERROR_MESSAGE) }) }}>登出</button>
        </nav>
        <p className="eyebrow">Draft-driven Builder</p>
        <h1 id="builder-title">{mode === 'new' ? '建立端點' : '建立新版本'}</h1>
        {mode === 'version' && <p>端點：<code>{endpointId}</code></p>}
        <label htmlFor="endpoint-requirement">需求</label>
        <textarea id="endpoint-requirement" value={requirement} disabled={intentLocked}
          onChange={(event) => setRequirement(event.target.value)} />
        <label htmlFor="endpoint-response-mode">Response mode</label>
        <select id="endpoint-response-mode" value={responseMode} disabled={intentLocked}
          onChange={(event) => setResponseMode(event.target.value as 'text' | 'structured')}>
          <option value="text">text</option><option value="structured">structured</option>
        </select>
        <SkillBrowser selected={selectedSkills} disabled={intentLocked} onSelectedChange={setSelectedSkills} />
        <button type="button" disabled={intentLocked} onClick={() => { void createDraft() }}>
          {drafting ? '建立 Draft 中…' : '建立 Draft'}
        </button>
        {error && <p role="alert">{error}</p>}
        {draft && (
          <section aria-label="Draft confirmation">
            <p>{draft.preview.endpointName}</p>
            <p>{draft.preview.behaviorSummary}</p>
            <SchemaEditor preview={draft.preview} />
            <RateLimitConfirmation rateLimit={draft.preview.rateLimit} />
            {draft.preview.warnings.length > 0 && <ul aria-label="Server warnings">
              {draft.preview.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
            </ul>}
            {mode === 'new' && <><label htmlFor="endpoint-slug">Slug</label>
              <input id="endpoint-slug" value={slug} disabled={busy} onChange={(event) => setSlug(event.target.value)} /></>}
            <button type="button" disabled={busy} onClick={() => { void submit() }}>
              {submitting ? '送出中…' : mode === 'new' ? '發布端點' : '建立版本'}
            </button>
          </section>
        )}
        {success?.kind === 'new' && (
          <section role="status" aria-live="polite">
            <h2>端點發布完成</h2>
            <p>請立即安全保存此一次性 API key；離開此頁後無法再次顯示。</p>
            <code>{success.initialApiKey}</code>
          </section>
        )}
        {success?.kind === 'version' && <p role="status">版本建立完成：版本 {success.versionNumber}</p>}
      </section>
    </main>
  )
}
