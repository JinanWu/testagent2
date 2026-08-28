import { useCallback, useEffect, useRef, useState } from 'react'
import type { DraftReceipt } from '../api/endpoints'
import { useSession } from '../app/SessionProvider'
import {
  createEndpointDraftOperation,
  createEndpointVersionOperation,
  createPublishEndpointOperation,
  type EndpointConfigurationConfirmation,
  type EndpointVersionConfiguration,
  type ProtectedStateOwner,
} from '../app/sessionAuthority'
import RateLimitConfirmation from '../features/endpoints/RateLimitConfirmation'
import SchemaEditor from '../features/endpoints/SchemaEditor'
import SkillBrowser from '../features/endpoints/SkillBrowser'
import { 卡片, 欄位, 複製按鈕, 輸入樣式, 等寬輸入樣式, 錯誤訊息 } from '../ui/元件'
import 圖示 from '../ui/圖示'
import 應用框架 from '../ui/應用框架'

const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/
export const BUILDER_ERROR_MESSAGE = '目前無法完成要求，請稍後再試。'

export interface EndpointBuilderPageProps {
  mode: 'new' | 'version'
  endpointId?: string
  onClose(): void
  onOpenChat?(): void
  onOpenAdminLogs?(): void
  onSelectConversation?(sessionId: string): void
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

function versionConfiguration(requirement: string, preview: DraftReceipt['preview']): EndpointVersionConfiguration {
  return {
    original_requirement_text: requirement,
    system_prompt: preview.systemPrompt,
    model_config_snapshot: { model: 'published-default', temperature: 0 },
    retry_policy: { max_attempts: 1 },
    input_schema: preview.inputSchema,
    response_schema: preview.responseSchema,
  }
}

export default function EndpointBuilderPage({ mode, endpointId, onClose, onOpenChat, onOpenAdminLogs, onSelectConversation }: EndpointBuilderPageProps) {
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
  /*
   * Builder 是精靈流程：每個階段各自一個畫面，不再往下疊。
   * 注意所有階段都保持掛載、只用 hidden 切換顯示——draft 產生後階段 A 的欄位
   * 必須仍在 DOM 裡且為 disabled（那是「已鎖定」的契約，測試會驗）。
   */
  const [檢視步驟, set檢視步驟] = useState(1)
  const mounted = useRef(false)
  const epoch = useRef(0)
  const draftOwner = useRef<number | null>(null)
  const submitOwner = useRef<number | null>(null)
  const draftController = useRef<AbortController | null>(null)
  const submitController = useRef<AbortController | null>(null)
  const protectedOwner = useRef<ProtectedStateOwner | null>(null)

  function 切換步驟(步驟: number) {
    setError(null)
    set檢視步驟(步驟)
  }

  function 更新需求(value: string) {
    setError(null)
    setRequirement(value)
  }

  function 更新技能(selected: string[]) {
    setError(null)
    setSelectedSkills(selected)
  }

  function 更新回應模式(value: 'text' | 'structured') {
    setError(null)
    setResponseMode(value)
  }

  function 更新Slug(value: string) {
    setError(null)
    setSlug(value)
  }

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
      set檢視步驟(1)
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
    if (trimmed.length === 0) {
      setError('請輸入需求。')
      return
    }
    if (selectedSkills.length === 0) {
      setError('請至少選擇 1 個 Skill。')
      return
    }
    if (selectedSkills.length > 32) {
      setError('最多只能選擇 32 個 Skills。')
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
      setRequirement(trimmed)
      setDraft(receipt)
      setSlug(receipt.preview.suggestedSlug)
      切換步驟(3)
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
      if (mode === 'new') {
        const exactConfirmation = confirmation(draft.preview)
        const receipt = await runAuthorized({
          owner,
          operation: createPublishEndpointOperation(draft.draftId, slug, exactConfirmation),
          signal: controller.signal,
        })
        if (!mounted.current || controller.signal.aborted || epoch.current !== requestEpoch || submitOwner.current !== requestEpoch) return
        setSuccess({ kind: 'new', endpointId: receipt.endpointId, initialApiKey: receipt.initialApiKey })
      } else {
        const exactConfiguration = versionConfiguration(requirement.trim(), draft.preview)
        const receipt = await runAuthorized({
          owner,
          operation: createEndpointVersionOperation(endpointId!, exactConfiguration),
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
  /* 已走到哪一步：決定 stepper 上哪幾步可以點回去看 */
  const 可達步驟 = success !== null ? 5 : draft !== null ? 4 : requirement.trim().length > 0 ? 2 : 1
  const 目前步驟 = success !== null ? 5 : Math.min(檢視步驟, 可達步驟)

  const 步驟清單 = [
    { 序: 1, 名: '需求定義' },
    { 序: 2, 名: '技能選擇' },
    { 序: 3, 名: '草案預覽' },
    { 序: 4, 名: '確認發布' },
  ] as const

  const 次要按鈕 =
    'rounded-xl border border-outline-variant px-4 py-2 font-body-md text-body-md text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface disabled:cursor-not-allowed disabled:opacity-50'
  const 主要按鈕 =
    'rounded-xl bg-primary-container px-6 py-2 font-body-md text-body-md font-semibold text-on-primary-container transition-colors hover:bg-primary-container/90 disabled:cursor-not-allowed disabled:opacity-50'

  return (
    <應用框架
      目前分頁="端點"
      標題={mode === 'new' ? '建立端點' : '建立新版本'}
      副標題={mode === 'version' ? `端點 ${endpointId ?? ''}` : undefined}
      /* 建立流程各階段都使用整頁工作區，不顯示上方標題列與分隔線。 */
      分隔線={false}
      標題可見={false}
      on開啟對話={onOpenChat}
      on選取對話={onSelectConversation}
      on開啟端點={onClose}
      on開啟稽核={user?.role === 'admin' ? onOpenAdminLogs : undefined}
      on登出={() => { void logout().catch(() => { if (mounted.current) setError(BUILDER_ERROR_MESSAGE) }) }}
    >
      <section aria-labelledby="builder-title" className="mx-auto flex w-full max-w-[56rem] flex-col gap-xl py-md">
        <h1 id="builder-title" className="sr-only">
          {mode === 'new' ? '建立端點' : '建立新版本'}
        </h1>

        {/* Stepper：走到哪、還剩幾步，一眼看完 */}
        {success === null && (
          <ol aria-label="建立流程" className="flex items-start justify-between gap-sm px-sm">
            {步驟清單.map((步驟, 索引) => {
              const 已完成 = 目前步驟 > 步驟.序
              const 進行中 = 目前步驟 === 步驟.序
              const 可點 = 步驟.序 <= 可達步驟 && !busy
              return (
                /* 第一步不佔彈性空間，其餘各自吃掉等分的剩餘寬度，圓圈才會等距 */
                <li
                  key={步驟.序}
                  className={['flex items-start gap-sm', 索引 === 0 ? 'shrink-0' : 'min-w-0 flex-1'].join(' ')}
                >
                  {索引 > 0 && (
                    <span
                      aria-hidden={true}
                      className={[
                        'mt-[22px] h-0.5 flex-1',
                        目前步驟 > 索引 ? 'bg-primary-container' : 'bg-surface-container-highest',
                      ].join(' ')}
                    />
                  )}
                  <button
                    type="button"
                    disabled={!可點}
                    aria-current={進行中 ? 'step' : undefined}
                    onClick={() => 切換步驟(步驟.序)}
                    className="flex shrink-0 flex-col items-center gap-sm disabled:cursor-default"
                  >
                    <span
                      aria-hidden={true}
                      className={[
                        'flex size-11 items-center justify-center rounded-full font-body-lg text-body-lg font-bold',
                        進行中 || 已完成
                          ? 'bg-primary-container text-on-primary-container'
                          : 'bg-surface-container-highest text-on-surface-variant',
                      ].join(' ')}
                    >
                      {步驟.序}
                    </span>
                    <span
                      className={[
                        'font-body-md text-body-md',
                        進行中 ? 'font-bold text-on-surface' : 'text-on-surface-variant',
                      ].join(' ')}
                    >
                      {步驟.名}
                    </span>
                  </button>
                </li>
              )
            })}
          </ol>
        )}

        {error && <錯誤訊息>{error}</錯誤訊息>}

        {intentLocked && success === null && (
          <p className="flex items-center gap-sm font-body-md text-body-md text-on-surface-variant">
            <span aria-hidden={true}><圖示 名稱="鎖定" 大小={16} /></span>
            Draft 已產生，需求與 Skills 已鎖定；要改請重新開始。
          </p>
        )}

        {/* ── 步驟 1：需求定義 ───────────────────────────── */}
        <div hidden={目前步驟 !== 1}>
          <卡片 標題="描述這支 API 要做什麼" 說明="規劃引擎會依這段描述草擬 system prompt 與輸入輸出結構。">
            <div className="flex flex-col gap-xl">
              <欄位 標籤={<span className="font-body-md text-body-md font-semibold normal-case tracking-normal">需求</span>} htmlFor="endpoint-requirement">
                <textarea id="endpoint-requirement" value={requirement} disabled={intentLocked} rows={6}
                  placeholder="例如：分析使用者的資源配置需求，並依門檻決定要路由到哪個子系統。"
                  className={`${輸入樣式} resize-y`}
                  onChange={(event) => 更新需求(event.target.value)} />
              </欄位>

              <欄位 標籤="Response mode" htmlFor="endpoint-response-mode" className="max-w-[20rem]">
                <select id="endpoint-response-mode" value={responseMode} disabled={intentLocked}
                  className={輸入樣式}
                  onChange={(event) => 更新回應模式(event.target.value as 'text' | 'structured')}>
                  <option value="text">text</option>
                  <option value="structured">structured</option>
                </select>
              </欄位>

              <div className="flex justify-end border-t border-outline-variant pt-lg">
                <button type="button" disabled={requirement.trim().length === 0}
                  onClick={() => 切換步驟(2)} className={主要按鈕}>
                  下一步：選擇 Skills
                </button>
              </div>
            </div>
          </卡片>
        </div>

        {/* ── 步驟 2：技能選擇 ───────────────────────────── */}
        <div hidden={目前步驟 !== 2}>
          <卡片 標題="選擇這支 API 可以使用的 Skills" 說明="至少 1 個、最多 32 個。">
            <div className="flex flex-col gap-xl">
              <SkillBrowser selected={selectedSkills} disabled={intentLocked} onSelectedChange={更新技能} />

              <div className="flex items-center justify-between gap-md border-t border-outline-variant pt-lg">
                <button type="button" disabled={busy} onClick={() => 切換步驟(1)} className={次要按鈕}>
                  上一步
                </button>
                {/* 子節點維持純字串：既有測試以 children.join('') 取得此按鈕。 */}
                <button type="button" disabled={intentLocked} onClick={() => { void createDraft() }} className={主要按鈕}>
                  {drafting ? '建立 Draft 中…' : '建立 Draft'}
                </button>
              </div>
            </div>
          </卡片>
        </div>

        {/* ── 步驟 3：草案預覽 ───────────────────────────── */}
        <div hidden={目前步驟 !== 3}>
          {draft && (
            <卡片 標題={draft.preview.endpointName} 說明={draft.preview.behaviorSummary}>
              <div className="flex flex-col gap-xl">
                <SchemaEditor preview={draft.preview} />

                {draft.preview.warnings.length > 0 && (
                  <div className="flex items-start gap-sm rounded-lg border border-error/30 bg-error-container px-md py-sm text-on-error-container">
                    <span aria-hidden={true} className="mt-0.5 shrink-0"><圖示 名稱="警告" 大小={18} /></span>
                    <div>
                      <p className="font-body-md text-body-md font-semibold">伺服器警告</p>
                      <ul aria-label="Server warnings" className="list-inside list-disc font-body-md text-body-md">
                        {draft.preview.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
                      </ul>
                    </div>
                  </div>
                )}

                <div className="flex items-center justify-between gap-md border-t border-outline-variant pt-lg">
                  <button type="button" disabled={busy} onClick={() => 切換步驟(2)} className={次要按鈕}>
                    上一步
                  </button>
                  <button type="button" disabled={busy} onClick={() => 切換步驟(4)} className={主要按鈕}>
                    下一步：確認發布
                  </button>
                </div>
              </div>
            </卡片>
          )}
        </div>

        {/* ── 步驟 4：確認發布 ───────────────────────────── */}
        <div hidden={目前步驟 !== 4}>
          {draft && (
            <卡片
              標題={mode === 'new' ? '確認發布設定' : '確認建立新版本'}
              說明={mode === 'new' ? '決定這支 API 對外的呼叫路徑。' : undefined}
            >
              <div className="flex flex-col gap-xl">
                {mode === 'new' && (
                  <欄位 標籤="Slug" htmlFor="endpoint-slug" 提示="小寫英數字與連字號，最長 63 字元" className="max-w-[28rem]">
                    <input id="endpoint-slug" value={slug} disabled={busy} className={等寬輸入樣式}
                      onChange={(event) => 更新Slug(event.target.value)} />
                  </欄位>
                )}

                <RateLimitConfirmation rateLimit={draft.preview.rateLimit} />

                <div className="flex items-center justify-between gap-md border-t border-outline-variant pt-lg">
                  <button type="button" disabled={busy} onClick={() => 切換步驟(3)} className={次要按鈕}>
                    上一步
                  </button>
                  {/* 子節點維持純字串：既有測試以 children.join('') 取得此按鈕。 */}
                  <button type="button" disabled={busy} onClick={() => { void submit() }} className={主要按鈕}>
                    {submitting ? '送出中…' : mode === 'new' ? '發布端點' : '建立版本'}
                  </button>
                </div>
              </div>
            </卡片>
          )}
        </div>

        {/* ── 步驟 5：完成 ───────────────────────────────── */}
        {/*
          發布成功：整個畫面只剩這一件事，所以置中、留白拉滿。
          頭部用成功語氣（發布真的成功了），只有 key 那一塊用危險語氣——
          原本整張卡片都是紅的，看起來像失敗。
        */}
        {success?.kind === 'new' && (
          <section
            role="status"
            aria-live="polite"
            className="flex min-h-[60vh] flex-col items-center justify-center gap-xl py-2xl text-center"
          >
            <div className="flex flex-col items-center gap-md">
              <span
                aria-hidden={true}
                className="flex size-16 items-center justify-center rounded-full bg-success/10 text-success"
              >
                <圖示 名稱="成功" 大小={32} />
              </span>
              <h2 className="font-headline-md text-headline-md text-on-surface">端點發布完成</h2>
              <p className="max-w-[32rem] font-body-lg text-body-lg text-on-surface-variant">
                您的端點已建立並啟用。請立即保存下方的一次性 API key。
              </p>
            </div>

            <div className="w-full max-w-[42rem] overflow-hidden rounded-xl border-2 border-error/50 text-left">
              <div className="flex items-start gap-sm bg-error-container px-lg py-md text-on-error-container">
                <span aria-hidden={true} className="mt-0.5 shrink-0">
                  <圖示 名稱="警告" 大小={20} />
                </span>
                <div>
                  <p className="font-body-md text-body-md font-semibold">這把 key 只會顯示這一次</p>
                  <p className="font-body-md text-body-md">
                    請立即安全保存此一次性 API key；離開此頁後無法再次顯示，也無法補發。
                  </p>
                </div>
              </div>
              <div className="p-lg">
                <div className="relative">
                  <div className="absolute right-2 top-2 z-10">
                    <複製按鈕 內容={success.initialApiKey} 標籤="API key" 深底={true} />
                  </div>
                  <pre className="程式碼區塊 select-all py-lg pr-28">{success.initialApiKey}</pre>
                </div>
              </div>
            </div>

            {/*
              設計需求 §7：一次性 key 需要「我已保存」的確認動作。
              按下後把 key 從畫面與狀態抹掉，再離開 Builder。
            */}
            <button
              type="button"
              onClick={() => { setSuccess(null); onClose() }}
              className={主要按鈕}
            >
              我已保存，前往端點清單
            </button>
          </section>
        )}

        {success?.kind === 'version' && (
          <section
            role="status"
            aria-live="polite"
            className="flex min-h-[60vh] flex-col items-center justify-center gap-lg py-2xl text-center"
          >
            <span
              aria-hidden={true}
              className="flex size-16 items-center justify-center rounded-full bg-success/10 text-success"
            >
              <圖示 名稱="成功" 大小={32} />
            </span>
            <h2 className="font-headline-md text-headline-md text-on-surface">
              版本建立完成：版本 {success.versionNumber}
            </h2>
            <p className="max-w-[32rem] font-body-lg text-body-lg text-on-surface-variant">
              新版本已成為這個端點的目前版本，既有 API key 不受影響。
            </p>
            <button type="button" onClick={() => { setSuccess(null); onClose() }} className={主要按鈕}>
              回到端點清單
            </button>
          </section>
        )}

      </section>
    </應用框架>
  )
}
