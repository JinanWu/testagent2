import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { AUTH_ERROR_MESSAGE } from '../api/auth'
import { ApiResponseError } from '../api/client'
import { getOwnerEndpoint, type OwnerEndpointDetail } from '../api/endpoints'
import { useSession } from '../app/SessionProvider'
import CredentialManager from '../features/endpoints/CredentialManager'
import EndpointDocs from '../features/endpoints/EndpointDocs'
import OwnerDiagnostics from '../features/logs/OwnerDiagnostics'
import { 卡片, 資料列, 載入中, 錯誤訊息 } from '../ui/元件'
import { 格式化時間, 狀態文字 } from '../ui/格式'
import 應用框架 from '../ui/應用框架'

export const ENDPOINT_NOT_FOUND_MESSAGE = '找不到端點或無權存取。'
export const ENDPOINT_DETAIL_ERROR_MESSAGE = '目前無法載入端點詳情，請稍後再試。'

export interface EndpointDetailPageProps {
  endpointId: string
  onClose(): void
  onCreateVersion(endpointId: string): void
  onOpenEndpoints?(): void
  onOpenAdminLogs?(): void
  onSelectConversation?(sessionId: string): void
}

type DetailState =
  | { kind: 'loading' }
  | { kind: 'ready'; detail: OwnerEndpointDetail }
  | { kind: 'not-found' }
  | { kind: 'error' }

type DetailTab = 'overview' | 'credentials' | 'docs' | 'diagnostics'
type TabIndicator = { left: number; width: number }
/* 狀態圓點沿用端點清單的顏色語言 */
const 狀態圓點: Record<'active' | 'disabled' | 'archived', string> = {
  active: 'bg-success', disabled: 'bg-outline', archived: 'bg-error',
}

const DETAIL_TABS: ReadonlyArray<Readonly<{ id: DetailTab; label: string }>> = [
  { id: 'overview', label: '總覽' },
  { id: 'credentials', label: '憑證' },
  { id: 'docs', label: '文件' },
  { id: 'diagnostics', label: '監控' },
]

export default function EndpointDetailPage({ endpointId, onClose, onCreateVersion, onOpenEndpoints, onOpenAdminLogs, onSelectConversation }: EndpointDetailPageProps) {
  const { logout, registerProtectedStateOwner, user } = useSession()
  const [state, setState] = useState<DetailState>({ kind: 'loading' })
  const [logoutError, setLogoutError] = useState(false)
  const [logoutPending, setLogoutPending] = useState(false)
  const [requestRevision, setRequestRevision] = useState(0)
  const [activeTab, setActiveTab] = useState<DetailTab>('overview')
  const [tabIndicator, setTabIndicator] = useState<TabIndicator>({ left: 0, width: 0 })
  const mounted = useRef(false)
  const epoch = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const tabRefs = useRef<Partial<Record<DetailTab, HTMLButtonElement | null>>>({})

  const updateTabIndicator = useCallback(() => {
    const currentTab = tabRefs.current[activeTab]
    if (!currentTab) {
      setTabIndicator({ left: 0, width: 0 })
      return
    }
    setTabIndicator({
      left: currentTab.offsetLeft,
      width: currentTab.offsetWidth,
    })
  }, [activeTab])

  const invalidate = useCallback((clear: boolean) => {
    epoch.current += 1
    controller.current?.abort()
    controller.current = null
    if (clear && mounted.current) {
      setState({ kind: 'loading' })
      setLogoutError(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    const registration = registerProtectedStateOwner(() => invalidate(true))
    const requestEpoch = ++epoch.current
    const requestController = new AbortController()
    controller.current = requestController
    setState({ kind: 'loading' })
    setActiveTab('overview')
    void getOwnerEndpoint(endpointId, { signal: requestController.signal }).then(
      (detail) => {
        if (mounted.current && !requestController.signal.aborted && epoch.current === requestEpoch) {
          setState({ kind: 'ready', detail })
        }
      },
      (error: unknown) => {
        if (!mounted.current || requestController.signal.aborted || epoch.current !== requestEpoch) return
        setState(error instanceof ApiResponseError && error.status === 404 ? { kind: 'not-found' } : { kind: 'error' })
      },
    ).finally(() => {
      if (controller.current === requestController) controller.current = null
    })
    return () => {
      mounted.current = false
      registration.unregister()
      invalidate(false)
    }
  }, [endpointId, user?.id, requestRevision, invalidate, registerProtectedStateOwner])

  useEffect(() => {
    if (state.kind !== 'ready') return
    updateTabIndicator()
    const ResizeObserverCtor = globalThis.ResizeObserver
    if (typeof ResizeObserverCtor === 'undefined') return
    const currentTab = tabRefs.current[activeTab]
    if (!currentTab) return
    const observer = new ResizeObserverCtor(() => updateTabIndicator())
    observer.observe(currentTab)
    return () => observer.disconnect()
  }, [activeTab, state.kind, updateTabIndicator])

  function handleLogout() {
    if (logoutPending) return
    setLogoutPending(true)
    setLogoutError(false)
    void logout().catch(() => {
      if (mounted.current) setLogoutError(true)
    }).finally(() => {
      if (!mounted.current) return
      setLogoutPending(false)
      setRequestRevision((current) => current + 1)
    })
  }

  function renderActivePanel(detail: OwnerEndpointDetail) {
    if (activeTab === 'overview') {
      return (
        <section key="overview" role="tabpanel" aria-label="總覽" className="端點詳情面板 flex flex-col gap-md">
          <卡片
            標題="端點基本資料"
            動作={
              /* 子節點維持純字串：既有測試以 children.join('') 取得此按鈕。 */
              <button
                type="button"
                onClick={() => onCreateVersion(detail.endpointId)}
                className="導覽項目 導覽項目-新增 rounded-xl bg-primary-container px-4 py-2 font-body-md text-body-md font-semibold text-on-primary-container transition-colors hover:bg-primary-container/90"
              >
                建立新版本
              </button>
            }
          >
            <dl aria-label="端點基本資料">
              <資料列 名稱="Slug">
                <code className="font-code-md text-code-md">{detail.slug}</code>
              </資料列>
              <資料列 名稱="狀態">
                {/*
                  圓點＋文字沿用端點清單的狀態語言（不只靠顏色）。
                  後面併呈後端實際回傳的原始碼，除錯時需要，字級與顏色都降一階。
                */}
                <span className="inline-flex items-center gap-sm">
                  <span
                    aria-hidden={true}
                    className={['size-1.5 shrink-0 rounded-full', 狀態圓點[detail.status]].join(' ')}
                  />
                  {狀態文字(detail.status)}
                  <code className="font-code-md text-code-md text-on-surface-variant/70">
                    {detail.status}
                  </code>
                </span>
              </資料列>
              <資料列 名稱="目前版本">
                {detail.currentVersionNumber === null
                  ? '尚未發布'
                  : `版本 ${detail.currentVersionNumber}`}
              </資料列>
              <資料列 名稱="建立時間">{格式化時間(detail.createdAt)}</資料列>
              <資料列 名稱="最後更新">{格式化時間(detail.updatedAt)}</資料列>
            </dl>
          </卡片>
        </section>
      )
    }
    if (activeTab === 'credentials') {
      return (
        <div key="credentials" role="tabpanel" aria-label="憑證" className="端點詳情面板">
          <CredentialManager endpointId={detail.endpointId} />
        </div>
      )
    }
    if (activeTab === 'docs') {
      return (
        <div key="docs" role="tabpanel" aria-label="文件" className="端點詳情面板">
          <EndpointDocs endpointId={detail.endpointId} />
        </div>
      )
    }
    return (
      <div key="diagnostics" role="tabpanel" aria-label="監控" className="端點詳情面板">
        <OwnerDiagnostics endpointId={detail.endpointId} />
      </div>
    )
  }

  return (
    <應用框架
      目前分頁="端點"
      標題={state.kind === 'ready' ? state.detail.slug : '端點詳情'}
      副標題={state.kind === 'ready' ? '端點詳情' : undefined}
      on開啟對話={onClose}
      on選取對話={onSelectConversation}
      on開啟端點={onOpenEndpoints}
      on開啟稽核={user?.role === 'admin' ? onOpenAdminLogs : undefined}
      on登出={handleLogout}
      登出中={logoutPending}
    >
      <div className="mx-auto flex w-full max-w-[68rem] flex-col gap-xl py-md">
        <h2 id="endpoint-title" className="sr-only">
          端點詳情
        </h2>
        {logoutError && <錯誤訊息>{AUTH_ERROR_MESSAGE}</錯誤訊息>}
        {state.kind === 'loading' && <載入中>正在載入端點詳情…</載入中>}
        {state.kind === 'not-found' && <錯誤訊息>{ENDPOINT_NOT_FOUND_MESSAGE}</錯誤訊息>}
        {state.kind === 'error' && <錯誤訊息>{ENDPOINT_DETAIL_ERROR_MESSAGE}</錯誤訊息>}

        {state.kind === 'ready' && (
          <>
            <div
              role="tablist"
              aria-label="端點資料分頁"
              className="端點詳情分頁 relative flex gap-xl border-b border-outline-variant"
            >
              {DETAIL_TABS.map((tab) => {
                const 是目前 = activeTab === tab.id
                return (
                  /* 子節點維持純字串：既有測試以 children.join('') 取得分頁按鈕。 */
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={是目前}
                    /*
                      ::after 的粗體幽靈副本（用來鎖住寬度、避免切換時左右位移）會被
                      算進無障礙名稱，讓 tab 變成「憑證憑證」。用 aria-label 明確指定名稱蓋掉。
                    */
                    aria-label={tab.label}
                    data-label={tab.label}
                    ref={(node) => {
                      tabRefs.current[tab.id] = node
                    }}
                    onClick={() => setActiveTab(tab.id)}
                    className={[
                      '端點詳情分頁按鈕 px-1 pb-3 font-body-md text-body-md font-semibold transition-colors',
                      是目前
                        ? 'text-on-surface'
                        : 'text-on-surface-variant hover:text-on-surface',
                    ].join(' ')}
                  >
                    {tab.label}
                  </button>
                )
              })}
              <span
                aria-hidden={true}
                className="端點詳情分頁指示器"
                style={{
                  '--tab-indicator-left': `${tabIndicator.left}px`,
                  '--tab-indicator-width': `${tabIndicator.width}px`,
                  opacity: tabIndicator.width > 0 ? 1 : 0,
                } as CSSProperties}
              />
            </div>

            {renderActivePanel(state.detail)}
          </>
        )}
      </div>
    </應用框架>
  )
}
