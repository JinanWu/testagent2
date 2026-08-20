import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { listCredentials, type CredentialCreateReceipt, type CredentialSummary } from '../../api/endpoints'
import { useSession } from '../../app/SessionProvider'
import {
  createCredentialOperation,
  createRevokeCredentialOperation,
  type CredentialCreateInput,
  type ProtectedStateOwner,
} from '../../app/sessionAuthority'

export const CREDENTIALS_ERROR_MESSAGE = '目前無法載入 credentials，請稍後再試。'
export const CREDENTIAL_MUTATION_ERROR_MESSAGE = 'Credential 操作失敗，請稍後再試。'
export const CREDENTIAL_INPUT_ERROR_MESSAGE = '請確認 credential 欄位格式與範圍。'

type LoadState = 'loading' | 'ready' | 'error'

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function safeSummary(receipt: CredentialCreateReceipt): CredentialSummary {
  return {
    credentialId: receipt.credentialId,
    name: receipt.name,
    purpose: receipt.purpose,
    keyPrefix: receipt.keyPrefix,
    keyLast4: receipt.keyLast4,
    status: receipt.status,
    expiresAt: receipt.expiresAt,
    lastUsedAt: receipt.lastUsedAt,
    createdAt: receipt.createdAt,
    revokedAt: receipt.revokedAt,
    ipAllowlist: [...receipt.ipAllowlist],
    rateLimitRequests: receipt.rateLimitRequests,
  }
}

export default function CredentialManager({ endpointId }: { endpointId: string }) {
  const { registerProtectedStateOwner, runAuthorized } = useSession()
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [items, setItems] = useState<CredentialSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [initialApiKey, setInitialApiKey] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<CredentialSummary | null>(null)
  const [name, setName] = useState('')
  const [purpose, setPurpose] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [ipAllowlist, setIpAllowlist] = useState('')
  const [rateLimitRequests, setRateLimitRequests] = useState('')
  const [mutationPending, setMutationPending] = useState(false)
  const mounted = useRef(false)
  const generation = useRef(0)
  const listController = useRef<AbortController | null>(null)
  const mutationController = useRef<AbortController | null>(null)
  const mutationOwner = useRef<number | null>(null)
  const authorityOwner = useRef<ProtectedStateOwner | null>(null)
  const keyRef = useRef<string | null>(null)

  const eraseSecret = useCallback(() => {
    keyRef.current = null
    if (mounted.current) {
      setInitialApiKey(null)
      setConfirming(null)
    }
  }, [])

  const invalidate = useCallback(() => {
    generation.current += 1
    listController.current?.abort()
    listController.current = null
    mutationController.current?.abort()
    mutationController.current = null
    mutationOwner.current = null
    if (mounted.current) setMutationPending(false)
    eraseSecret()
  }, [eraseSecret])

  useEffect(() => {
    mounted.current = true
    const registration = registerProtectedStateOwner(() => {
      invalidate()
      if (mounted.current) {
        setItems([])
        setLoadState('loading')
        setError(null)
      }
    })
    authorityOwner.current = registration.owner
    return () => {
      mounted.current = false
      authorityOwner.current = null
      registration.unregister()
      invalidate()
    }
  }, [invalidate, registerProtectedStateOwner])

  useEffect(() => {
    const current = ++generation.current
    const controller = new AbortController()
    listController.current?.abort()
    listController.current = controller
    setLoadState('loading')
    setItems([])
    setError(null)
    void listCredentials(endpointId, { signal: controller.signal }).then(
      (page) => {
        if (mounted.current && generation.current === current && !controller.signal.aborted) {
          setItems(page.items)
          setLoadState('ready')
        }
      },
      (caught: unknown) => {
        if (mounted.current && generation.current === current && !controller.signal.aborted && !isAbort(caught)) {
          setLoadState('error')
          setError(CREDENTIALS_ERROR_MESSAGE)
        }
      },
    ).finally(() => {
      if (listController.current === controller) listController.current = null
    })
    return () => {
      generation.current += 1
      controller.abort()
      if (listController.current === controller) listController.current = null
    }
  }, [endpointId])

  function parseInput(): CredentialCreateInput | null {
    const trimmedName = name.trim()
    const trimmedPurpose = purpose.trim()
    const expiry = Number(expiresAt)
    const rate = Number(rateLimitRequests)
    const addresses = ipAllowlist.split(',').map((value) => value.trim()).filter(Boolean)
    if (trimmedName !== name || trimmedPurpose !== purpose || trimmedName.length < 1 || trimmedName.length > 256 ||
        trimmedPurpose.length < 1 || trimmedPurpose.length > 2048 || !Number.isFinite(expiry) || expiry <= 0 ||
        addresses.length > 256 || addresses.some((value) => value.length > 128 || value.includes('%')) ||
        !Number.isSafeInteger(rate) || rate < 1 || rate > 10_000) return null
    return { name, purpose, expiresAt: expiry, ipAllowlist: addresses, rateLimitRequests: rate }
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault()
    if (mutationOwner.current !== null || keyRef.current !== null) return
    const input = parseInput()
    const owner = authorityOwner.current
    if (!input || !owner) {
      setError(CREDENTIAL_INPUT_ERROR_MESSAGE)
      return
    }
    const requestGeneration = generation.current
    const operationEpoch = requestGeneration + 1
    mutationOwner.current = operationEpoch
    setMutationPending(true)
    const controller = new AbortController()
    mutationController.current = controller
    setError(null)
    try {
      const receipt = await runAuthorized({
        owner,
        operation: createCredentialOperation(endpointId, input),
        signal: controller.signal,
      })
      if (!mounted.current || generation.current !== requestGeneration || controller.signal.aborted ||
          mutationOwner.current !== operationEpoch) return
      keyRef.current = receipt.initialApiKey
      setInitialApiKey(receipt.initialApiKey)
      const summary = safeSummary(receipt)
      setItems((current) => [...current.filter((item) => item.credentialId !== summary.credentialId), summary])
      setName(''); setPurpose(''); setExpiresAt(''); setIpAllowlist(''); setRateLimitRequests('')
    } catch (caught) {
      if (mounted.current && generation.current === requestGeneration && !controller.signal.aborted && !isAbort(caught)) {
        setError(CREDENTIAL_MUTATION_ERROR_MESSAGE)
      }
    } finally {
      if (mutationOwner.current === operationEpoch) mutationOwner.current = null
      if (mutationController.current === controller) mutationController.current = null
      if (mounted.current && generation.current === requestGeneration) setMutationPending(false)
    }
  }

  async function handleRevoke() {
    const target = confirming
    const owner = authorityOwner.current
    if (!target || !owner || mutationOwner.current !== null || keyRef.current !== null) return
    const requestGeneration = generation.current
    const operationEpoch = requestGeneration + 1
    mutationOwner.current = operationEpoch
    setMutationPending(true)
    const controller = new AbortController()
    mutationController.current = controller
    setError(null)
    let revokeAcknowledged = false
    try {
      await runAuthorized({
        owner,
        operation: createRevokeCredentialOperation(endpointId, target.credentialId),
        signal: controller.signal,
      })
      revokeAcknowledged = true
      const page = await listCredentials(endpointId, { signal: controller.signal })
      if (!mounted.current || generation.current !== requestGeneration || controller.signal.aborted ||
          mutationOwner.current !== operationEpoch) return
      setItems(page.items)
      setConfirming(null)
    } catch (caught) {
      if (mounted.current && generation.current === requestGeneration && !controller.signal.aborted && !isAbort(caught)) {
        if (revokeAcknowledged) {
          setItems([])
          setLoadState('error')
          setConfirming(null)
          setError(CREDENTIALS_ERROR_MESSAGE)
        } else {
          setError(CREDENTIAL_MUTATION_ERROR_MESSAGE)
        }
      }
    } finally {
      if (mutationOwner.current === operationEpoch) mutationOwner.current = null
      if (mutationController.current === controller) mutationController.current = null
      if (mounted.current && generation.current === requestGeneration) setMutationPending(false)
    }
  }

  const mutationDisabled = mutationPending || initialApiKey !== null
  return <section aria-labelledby="credentials-title">
    <h2 id="credentials-title">Credentials</h2>
    {loadState === 'loading' && <p role="status">正在載入 credentials…</p>}
    {error && <p role="alert">{error}</p>}
    {loadState === 'ready' && items.length === 0 && <p>目前沒有 credential。</p>}
    <ul aria-label="Credential safe summaries">{items.map((item) => <li key={item.credentialId}>
      <h3>{item.name}</h3>
      <dl>
        <dt>用途</dt><dd>{item.purpose}</dd>
        <dt>Key</dt><dd>{item.keyPrefix}…{item.keyLast4}</dd>
        <dt>狀態</dt><dd>{item.status}</dd>
        <dt>到期</dt><dd>{item.expiresAt}</dd>
        <dt>最後使用</dt><dd>{item.lastUsedAt ?? '尚未使用'}</dd>
        <dt>IP allowlist</dt><dd>{item.ipAllowlist.length ? item.ipAllowlist.join('、') : '不限'}</dd>
        <dt>Rate limit</dt><dd>{item.rateLimitRequests}</dd>
      </dl>
      <button type="button" disabled={mutationDisabled || item.status === 'revoked'} onClick={() => setConfirming(item)}>撤銷</button>
    </li>)}</ul>

    {initialApiKey && <section role="status" aria-label="一次性 API key">
      <h3>API key 只顯示這一次，離開後無法復原</h3>
      <pre>{initialApiKey}</pre>
      <button type="button" onClick={eraseSecret}>已保存並清除</button>
    </section>}

    {confirming && !initialApiKey && <section role="alertdialog" aria-labelledby="revoke-title">
      <h3 id="revoke-title">確認撤銷 {confirming.name}</h3>
      <p>撤銷後此 credential 將無法再使用。</p>
      <button type="button" disabled={mutationPending} onClick={() => { void handleRevoke() }}>確認撤銷</button>
      <button type="button" disabled={mutationPending} onClick={() => setConfirming(null)}>取消</button>
    </section>}

    <form aria-label="建立 credential" onSubmit={(event) => { void handleCreate(event) }}>
      <h3>建立 credential</h3>
      <label htmlFor="credential-name">名稱</label>
      <input id="credential-name" maxLength={256} value={name} onChange={(event) => setName(event.currentTarget.value)} />
      <label htmlFor="credential-purpose">用途</label>
      <textarea id="credential-purpose" maxLength={2048} value={purpose} onChange={(event) => setPurpose(event.currentTarget.value)} />
      <label htmlFor="credential-expires-at">到期時間戳</label>
      <input id="credential-expires-at" type="number" min="0" value={expiresAt} onChange={(event) => setExpiresAt(event.currentTarget.value)} />
      <label htmlFor="credential-ip-allowlist">IP allowlist（逗號分隔）</label>
      <input id="credential-ip-allowlist" value={ipAllowlist} onChange={(event) => setIpAllowlist(event.currentTarget.value)} />
      <label htmlFor="credential-rate-limit">Rate limit requests</label>
      <input id="credential-rate-limit" type="number" min="1" max="10000" value={rateLimitRequests} onChange={(event) => setRateLimitRequests(event.currentTarget.value)} />
      <button type="submit" disabled={mutationDisabled || loadState === 'loading'}>建立 credential</button>
    </form>
  </section>
}
