import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { listCredentials, type CredentialCreateReceipt, type CredentialSummary } from '../../api/endpoints'
import { 卡片, 空狀態, 狀態色調, 狀態標籤, 載入中, 欄位, 複製按鈕, 輸入樣式, 錯誤訊息 } from '../../ui/元件'
import { 格式化時間, 狀態文字 } from '../../ui/格式'
import 圖示 from '../../ui/圖示'
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

/*
 * 憑證改用「固定標籤欄」的定義清單，不再用八欄表格。
 *
 * 八欄在這個 shell 裡永遠塞不下（側欄 260px 之後只剩約 1180px，八欄光最小寬度就要
 * 1064px 再加間距），欄位一擠就對不齊也讀不動。固定 6.5rem 的標籤欄可以保證
 * 每一列的欄位名與內容都在同一條垂直線上，寬度再窄也不會跑掉。
 */
/*
 * 表頭與資料列共用同一份欄位定義，兩邊都套 憑證欄位樣式，欄寬永遠一致。
 * 不設 min-width，也不靠水平捲軸；欄位名稱縮短，欄名與內容都置中對齊。
 */
const 憑證欄位樣式 =
  'grid-cols-[minmax(9rem,1.15fr)_9.5rem_5.5rem_7rem_7rem_6.5rem_minmax(4.5rem,0.55fr)_5rem]'
const 欄位數值樣式 = 'min-w-0 break-words text-center font-code-md text-code-md tabular-nums text-on-surface'

/** 時間拆成日期與時分兩行，窄欄位下不會被截斷。 */
function 時間兩行(值: number | null): ReactNode {
  if (值 === null) return '-'
  const 文字 = 格式化時間(值)
  const 空格 = 文字.indexOf(' ')
  if (空格 < 0) return 文字
  return (
    <>
      <span className="block">{文字.slice(0, 空格)}</span>
      <span className="block text-on-surface-variant">{文字.slice(空格 + 1)}</span>
    </>
  )
}

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
  const [showCreate, setShowCreate] = useState(false)
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
    if (mounted.current) {
      setMutationPending(false)
      setShowCreate(false)
    }
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

  useEffect(() => {
    if (!confirming || initialApiKey !== null) return
    const 攔截Escape = (事件: KeyboardEvent) => {
      if (事件.key === 'Escape' && !mutationPending) setConfirming(null)
    }
    window.addEventListener('keydown', 攔截Escape)
    return () => window.removeEventListener('keydown', 攔截Escape)
  }, [confirming, initialApiKey, mutationPending])

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
      setShowCreate(false)
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

  return <section aria-labelledby="credentials-title" className="flex flex-col gap-lg">
    <div className="flex flex-wrap items-start justify-between gap-md">
      <div className="min-w-0">
        <h2 id="credentials-title" className="font-headline-sm text-headline-sm text-on-surface">
          API 存取憑證
        </h2>
        <p className="mt-xs max-w-3xl font-body-md text-body-md text-on-surface-variant">
          管理此端點的驗證金鑰。請妥善保管您的金鑰，並定期輪替以確保安全性。
        </p>
      </div>
      <button
        type="button"
        onClick={() => setShowCreate(true)}
        disabled={mutationDisabled || loadState === 'loading'}
        className="導覽項目 導覽項目-新增 rounded-xl bg-primary-container px-4 py-2 font-body-md text-body-md font-semibold text-on-primary-container transition-colors hover:bg-primary-container/90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        建立新憑證
      </button>
    </div>

    {loadState === 'loading' && <載入中>正在載入 credentials…</載入中>}
    {error && <錯誤訊息>{error}</錯誤訊息>}

    {initialApiKey && <section role="status" aria-label="一次性 API key"
      className="overflow-hidden rounded-lg border-2 border-error/50 bg-error/5">
      <div className="flex items-start gap-sm border-b border-error/20 bg-error-container px-md py-sm text-on-error-container">
        <span aria-hidden={true} className="mt-0.5 shrink-0"><圖示 名稱="警告" 大小={18} /></span>
        <div>
          <h3 className="font-headline-sm text-headline-sm">API key 只顯示這一次，離開後無法復原</h3>
          <p className="font-body-md text-body-md">請立即安全保存；此頁面關閉或切換後將無法再次取得。</p>
        </div>
      </div>
      <div className="p-md">
        <div className="relative">
          <div className="absolute right-2 top-2 z-10"><複製按鈕 內容={initialApiKey} 標籤="API key" 深底={true} /></div>
          <pre className="程式碼區塊 pr-24 select-all">{initialApiKey}</pre>
        </div>
        <button type="button" onClick={eraseSecret}
          className="mt-md w-full rounded border border-outline-variant bg-surface-container-lowest px-4 py-2 font-body-md text-body-md font-semibold text-on-surface transition-colors hover:bg-surface-container">
          已保存並清除
        </button>
      </div>
    </section>}

    {confirming && !initialApiKey && <div role="alertdialog" aria-modal="true" aria-labelledby="revoke-title"
      onClick={() => { if (!mutationPending) setConfirming(null) }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-md backdrop-blur-sm">
      <div onClick={(event) => event.stopPropagation()}
        className="w-full max-w-[28rem] overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest shadow-xl">
        <div className="flex items-start gap-sm border-b border-outline-variant p-lg">
          <span aria-hidden={true} className="mt-0.5 shrink-0 text-on-surface-variant"><圖示 名稱="危險" 大小={18} /></span>
          <div className="min-w-0">
            <h3 id="revoke-title" className="break-words font-headline-sm text-headline-sm text-on-surface">
              確認撤銷 {confirming.name}
            </h3>
            <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
              撤銷後此 credential 將無法再使用，且無法復原。
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-sm p-md">
          <button type="button" disabled={mutationPending} onClick={() => setConfirming(null)}
            className="rounded px-4 py-1.5 font-body-md text-body-md font-semibold text-secondary transition-colors hover:bg-secondary/10 disabled:opacity-50">
            取消
          </button>
          <button type="button" disabled={mutationPending} onClick={() => { void handleRevoke() }}
            className="rounded bg-error px-4 py-1.5 font-body-md text-body-md font-semibold text-on-error transition-colors hover:bg-error/90 disabled:opacity-50">
            確認撤銷
          </button>
        </div>
      </div>
    </div>}

    {showCreate && !initialApiKey && <卡片 標題="建立 credential">
      <form id="credential-create-form" aria-label="建立 credential" onSubmit={(event) => { void handleCreate(event) }}
        className="grid gap-md sm:grid-cols-2">
        <欄位 標籤="名稱" htmlFor="credential-name">
          <input id="credential-name" maxLength={256} value={name} className={輸入樣式}
            placeholder="例如：Production API Key"
            onChange={(event) => setName(event.currentTarget.value)} />
        </欄位>
        <欄位 標籤="到期時間戳" htmlFor="credential-expires-at" 提示="Unix epoch 秒">
          <input id="credential-expires-at" type="number" min="0" value={expiresAt} className={輸入樣式}
            onChange={(event) => setExpiresAt(event.currentTarget.value)} />
        </欄位>
        <欄位 標籤="用途描述" htmlFor="credential-purpose" className="sm:col-span-2">
          <textarea id="credential-purpose" maxLength={2048} value={purpose} rows={3}
            className={`${輸入樣式} resize-y`}
            placeholder="簡述此憑證的用途"
            onChange={(event) => setPurpose(event.currentTarget.value)} />
        </欄位>
        <欄位 標籤="IP 白名單" htmlFor="credential-ip-allowlist" 提示="以逗號分隔，留白代表不限">
          <input id="credential-ip-allowlist" value={ipAllowlist} className={輸入樣式}
            placeholder="例如：192.168.1.1, 10.0.0.0/8"
            onChange={(event) => setIpAllowlist(event.currentTarget.value)} />
        </欄位>
        <欄位 標籤="Rate limit requests" htmlFor="credential-rate-limit" 提示="1 至 10000">
          <input id="credential-rate-limit" type="number" min="1" max="10000" value={rateLimitRequests}
            className={輸入樣式}
            onChange={(event) => setRateLimitRequests(event.currentTarget.value)} />
        </欄位>
        <div className="flex justify-end gap-sm border-t border-outline-variant pt-md sm:col-span-2">
          <button type="button" disabled={mutationPending} onClick={() => setShowCreate(false)}
            className="rounded px-4 py-2 font-body-md text-body-md font-semibold text-secondary transition-colors hover:bg-secondary/10 disabled:opacity-50">
            取消建立
          </button>
          <button type="submit" disabled={mutationDisabled || loadState === 'loading'}
            className="rounded bg-secondary px-4 py-2 font-body-md text-body-md font-semibold text-on-secondary transition-colors hover:bg-secondary/90 disabled:cursor-not-allowed disabled:opacity-50">
            建立 credential
          </button>
        </div>
      </form>
    </卡片>}

    {!showCreate && <section className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest shadow-[0_1px_3px_rgba(15,23,42,0.04)]">
      {loadState === 'ready' && items.length === 0 && <空狀態>目前沒有 credential。</空狀態>}
      {items.length > 0 && <div aria-label="Credential safe summaries" className="overflow-hidden">
        <div className={`grid items-center gap-md border-b border-outline-variant bg-surface-container px-lg py-sm text-center font-body-md text-body-md text-on-surface-variant ${憑證欄位樣式}`}>
          <div>名稱</div>
          <div>金鑰</div>
          <div>狀態</div>
          <div>到期</div>
          <div>最後使用</div>
          <div>速率限制</div>
          <div>IP</div>
          <div>操作</div>
        </div>
        <ul className="divide-y divide-outline-variant/60">
          {items.map((item) => {
            const revoked = item.status === 'revoked'
            return (
              <li key={item.credentialId}
                className={`grid items-center gap-md px-lg py-md transition-colors hover:bg-surface-container-highest/30 ${憑證欄位樣式}`}>
                <div className="min-w-0 text-center">
                  <p className={['break-words font-body-md text-body-md font-semibold text-on-surface', revoked ? 'line-through opacity-70' : ''].join(' ')}>
                    {item.name}
                  </p>
                  <p className="break-words font-body-md text-body-md text-on-surface-variant">{item.purpose}</p>
                </div>

                <div className={欄位數值樣式}>{item.keyPrefix}…{item.keyLast4}</div>

                <div className="text-center">
                  <狀態標籤 色調={狀態色調(item.status)}>{狀態文字(item.status)}</狀態標籤>
                </div>

                {/* 時間拆兩行，7rem 的欄寬就塞得下，不必截斷 */}
                <div className={欄位數值樣式}>{時間兩行(item.expiresAt)}</div>
                <div className={欄位數值樣式}>{時間兩行(item.lastUsedAt)}</div>

                <div className={欄位數值樣式}>{item.rateLimitRequests} req/min</div>

                <div className={欄位數值樣式} title={item.ipAllowlist.join('、')}>
                  {item.ipAllowlist.length ? item.ipAllowlist.join('、') : '不限'}
                </div>

                <div className="flex justify-center">
                  <button type="button" disabled={mutationDisabled || revoked}
                    onClick={() => setConfirming(item)}
                    className="rounded-lg border border-outline-variant px-3 py-1 font-body-md text-body-md font-semibold text-error transition-colors hover:border-error/40 hover:bg-error/10 disabled:cursor-not-allowed disabled:opacity-40">
                    撤銷
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      </div>}
    </section>}
  </section>
}
