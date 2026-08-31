import { useCallback, useEffect, useRef, useState } from 'react'
import { AUTH_ERROR_MESSAGE } from '../api/auth'
import { listOwnerEndpoints, type OwnerEndpointItem } from '../api/endpoints'
import { useSession } from '../app/SessionProvider'
import { 載入中, 錯誤訊息 } from '../ui/元件'
import { 格式化相對時間, 狀態文字 } from '../ui/格式'
import 圖示 from '../ui/圖示'
import 應用框架 from '../ui/應用框架'

export const ENDPOINT_LIST_ERROR_MESSAGE = '目前無法載入端點，請稍後再試。'
type EndpointScope = 'owner' | 'all'

export interface EndpointListPageProps {
  onClose(): void
  onOpenEndpoint(endpointId: string): void
  onCreateEndpoint(): void
  onOpenAdminLogs?(): void
  onSelectConversation?(sessionId: string): void
}

function mergeEndpointItems(
  current: readonly OwnerEndpointItem[], incoming: readonly OwnerEndpointItem[],
): OwnerEndpointItem[] {
  const merged = [...current]
  const positions = new Map(current.map((item, index) => [item.endpointId, index]))
  for (const item of incoming) {
    const position = positions.get(item.endpointId)
    if (position === undefined) {
      positions.set(item.endpointId, merged.length)
      merged.push(item)
    } else {
      merged[position] = item
    }
  }
  return merged
}

/*
 * 三種狀態的視覺配置。狀態文字一定會出現在副標，圓點與底色只是輔助——
 * 不能只靠顏色傳達狀態。
 */
const 狀態外觀: Record<'active' | 'disabled' | 'archived', { 圖磚: string; 圓點: string }> = {
  active: { 圖磚: 'bg-primary-container/10 text-primary', 圓點: 'bg-success' },
  disabled: { 圖磚: 'bg-surface-container-highest text-on-surface-variant', 圓點: 'bg-outline' },
  archived: { 圖磚: 'bg-surface-container text-on-surface-variant/70', 圓點: 'bg-error' },
}

export default function EndpointListPage({ onClose, onOpenEndpoint, onCreateEndpoint, onOpenAdminLogs, onSelectConversation }: EndpointListPageProps) {
  const { logout, registerProtectedStateOwner, user } = useSession()
  const [items, setItems] = useState<OwnerEndpointItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [scope, setScope] = useState<EndpointScope>('owner')
  const [scopeMenuOpen, setScopeMenuOpen] = useState(false)
  const generation = useRef(0)
  const controllers = useRef(new Set<AbortController>())
  const mounted = useRef(false)
  const loadingMoreRef = useRef(false)

  const invalidate = useCallback((clearState: boolean) => {
    generation.current += 1
    for (const controller of controllers.current) controller.abort()
    controllers.current.clear()
    loadingMoreRef.current = false
    if (clearState && mounted.current) {
      setItems([])
      setNextCursor(null)
      setLoading(false)
      setLoadingMore(false)
      setError(null)
    }
    return generation.current
  }, [])

  const loadFirstPage = useCallback(async () => {
    const requestGeneration = invalidate(false)
    const controller = new AbortController()
    controllers.current.add(controller)
    setItems([])
    setNextCursor(null)
    setError(null)
    setLoading(true)
    try {
      const result = await listOwnerEndpoints(
        { scope, limit: 20 },
        { signal: controller.signal },
      )
      if (!mounted.current || controller.signal.aborted || generation.current !== requestGeneration) return
      setItems(mergeEndpointItems([], result.items))
      setNextCursor(result.nextCursor)
    } catch {
      if (mounted.current && !controller.signal.aborted && generation.current === requestGeneration) {
        setError(ENDPOINT_LIST_ERROR_MESSAGE)
      }
    } finally {
      controllers.current.delete(controller)
      if (mounted.current && generation.current === requestGeneration) setLoading(false)
    }
  }, [scope, invalidate])

  useEffect(() => {
    mounted.current = true
    if (user?.role !== 'admin' && scope !== 'owner') {
      setScope('owner')
      return () => {
        mounted.current = false
        invalidate(false)
      }
    }
    const registration = registerProtectedStateOwner(() => { invalidate(true) })
    void loadFirstPage()
    return () => {
      mounted.current = false
      registration.unregister()
      invalidate(false)
    }
  }, [user?.id, user?.role, scope, invalidate, loadFirstPage, registerProtectedStateOwner])

  async function loadMore() {
    if (nextCursor === null || loadingMoreRef.current) return
    loadingMoreRef.current = true
    const requestGeneration = generation.current
    const cursor = nextCursor
    const controller = new AbortController()
    controllers.current.add(controller)
    setError(null)
    setLoadingMore(true)
    try {
      const result = await listOwnerEndpoints(
        { scope, limit: 20, cursor },
        { signal: controller.signal },
      )
      if (!mounted.current || controller.signal.aborted || generation.current !== requestGeneration) return
      setItems((current) => mergeEndpointItems(current, result.items))
      setNextCursor(result.nextCursor)
    } catch {
      if (mounted.current && !controller.signal.aborted && generation.current === requestGeneration) {
        setError(ENDPOINT_LIST_ERROR_MESSAGE)
      }
    } finally {
      controllers.current.delete(controller)
      if (mounted.current && generation.current === requestGeneration) {
        loadingMoreRef.current = false
        setLoadingMore(false)
      }
    }
  }

  function changeScope(value: string) {
    if ((value !== 'owner' && value !== 'all') || value === scope) return
    invalidate(true)
    setScope(value)
  }

  function chooseScope(value: EndpointScope) {
    changeScope(value)
    setScopeMenuOpen(false)
  }

  return (
    <應用框架
      目前分頁="端點"
      標題="端點管理"
      副標題="管理與觀測您的 Agent 介面。"
      /* 與對話頁一致：不畫標題列文字與分隔線，只留右側動作鈕那一排 */
      分隔線={false}
      標題可見={false}
      on開啟對話={onClose}
      on選取對話={onSelectConversation}
      on開啟稽核={user?.role === 'admin' ? onOpenAdminLogs : undefined}
      on登出={() => {
        void logout().catch(() => { if (mounted.current) setError(AUTH_ERROR_MESSAGE) })
      }}
    >
      <div className="mx-auto flex w-full max-w-[64rem] flex-col gap-xl pb-md pt-xl">
        {error && <錯誤訊息>{error}</錯誤訊息>}

        <section aria-labelledby="endpoint-list-heading" className="flex flex-col gap-lg">
          <div className="flex items-center justify-between gap-md border-b border-outline-variant pb-md">
            <h2 id="endpoint-list-heading" className="font-headline-sm text-headline-sm text-on-surface">
              {scope === 'owner' ? '我的端點' : '所有端點'}
            </h2>
            <div className="flex items-center gap-md">
              <div className="relative w-[7.75rem]">
                <button
                  id="endpoint-scope"
                  type="button"
                  aria-haspopup="menu"
                  aria-expanded={scopeMenuOpen}
                  aria-controls="endpoint-scope-menu"
                  onClick={() => setScopeMenuOpen((open) => !open)}
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') setScopeMenuOpen(false)
                  }}
                  className="flex w-full items-center justify-between gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest px-4 py-2 font-body-md text-body-md font-semibold text-on-surface shadow-sm transition-[border-color,box-shadow,background-color] hover:border-outline hover:bg-surface-container-low focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20"
                >
                  <span>{scope === 'owner' ? '我的端點' : '所有端點'}</span>
                  <圖示
                    名稱="展開"
                    大小={18}
                    className={`shrink-0 text-on-surface-variant transition-transform duration-200 ${scopeMenuOpen ? 'rotate-180' : ''}`}
                  />
                </button>
                {scopeMenuOpen && (
                  <div
                    id="endpoint-scope-menu"
                    role="menu"
                    aria-labelledby="endpoint-scope"
                    className="absolute right-0 z-20 mt-2 w-full overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest p-1 shadow-[0_16px_32px_rgba(28,27,26,0.16)]"
                  >
                    {([
                      ['owner', '我的端點'],
                      ['all', '所有端點'],
                    ] as const).map(([value, label]) => {
                      const selected = scope === value
                      return (
                        <button
                          key={value}
                          type="button"
                          role="menuitemradio"
                          aria-checked={selected}
                          data-scope-value={value}
                          onClick={() => chooseScope(value)}
                          className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left font-body-md text-body-md font-semibold transition-colors ${selected ? 'bg-primary-container/12 text-primary' : 'text-on-surface hover:bg-surface-container'}`}
                        >
                          <span>{label}</span>
                          {selected && <span aria-hidden={true} className="font-headline-sm text-headline-sm leading-none">✓</span>}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
              {/* 子節點維持純字串：既有測試以 children.join('') 取得此按鈕。 */}
              <button
                type="button"
                onClick={onCreateEndpoint}
                className="導覽項目 導覽項目-新增 flex w-[7.75rem] items-center justify-center rounded-xl bg-primary-container px-3 py-2 font-body-md text-body-md font-semibold text-on-primary-container shadow-sm transition-colors hover:bg-primary-container/90"
              >
                建立端點
              </button>
            </div>
          </div>
        </section>

        {loading ? (
          <載入中>正在載入端點…</載入中>
        ) : items.length === 0 && !error ? (
          <div className="flex flex-col items-center gap-md rounded-xl border border-dashed border-outline-variant px-lg py-2xl text-center">
            <span
              aria-hidden={true}
              className="flex size-14 items-center justify-center rounded-full bg-surface-container text-on-surface-variant"
            >
              <圖示 名稱="端點" 大小={26} />
            </span>
            <p className="font-headline-sm text-headline-sm text-on-surface">目前沒有端點。</p>
            <p className="max-w-[26rem] font-body-md text-body-md text-on-surface-variant">
              把一組 Agent 設定發布成對外的 HTTP API，就會出現在這裡。
            </p>
          </div>
        ) : (
          <section aria-label={scope === 'owner' ? '我的端點清單' : '所有端點清單'} className="flex flex-col gap-lg">
            {/*
              兩欄清單呼應插件列表的瀏覽節奏：每筆仍是輕量 row，不做厚重卡片。
              後續端點變多時，左右欄可以一起向下延展，掃描距離比較短。
            */}
            <ul aria-label="端點清單" className="grid gap-x-2xl gap-y-sm lg:grid-cols-2">
              {items.map((item) => {
                const 外觀 = 狀態外觀[item.status]
                return (
                  <li
                    key={item.endpointId}
                    className="group relative rounded-xl border-b border-outline-variant/70 transition-colors hover:bg-surface-container lg:border-b-0"
                  >
                    <div className="pointer-events-none flex items-center gap-md px-sm py-md">
                      <span
                        aria-hidden={true}
                        className={['flex size-11 shrink-0 items-center justify-center rounded-xl border border-outline-variant/70', 外觀.圖磚].join(' ')}
                      >
                        <圖示 名稱="端點" 大小={20} />
                      </span>

                      <div className="min-w-0 flex-1">
                        <p className="truncate font-body-md text-body-md font-semibold text-on-surface">
                          {item.slug}
                        </p>
                        <p className="flex items-center gap-sm truncate font-body-md text-body-md text-on-surface-variant">
                          <span className="flex items-center gap-1.5">
                            <span aria-hidden={true} className={['size-1.5 shrink-0 rounded-full', 外觀.圓點].join(' ')} />
                            {狀態文字(item.status)}
                          </span>
                          <span aria-hidden={true}>·</span>
                          <span className="font-code-md text-code-md">
                            {item.currentVersionNumber === null ? '尚未發布' : `v${item.currentVersionNumber}`}
                          </span>
                          <span aria-hidden={true}>·</span>
                          <span className="truncate">{格式化相對時間(item.updatedAt)}</span>
                        </p>
                      </div>

                      <span
                        aria-hidden={true}
                        className="shrink-0 text-on-surface-variant/50 transition-colors group-hover:text-on-surface-variant"
                      >
                        <圖示 名稱="前往" 大小={18} />
                      </span>
                    </div>

                    {/*
                      子節點維持純字串：既有測試以 button.children.join('') 比對 slug 取得此按鈕。
                      按鈕覆蓋整列（.整列連結 以 font-size: 0 收掉文字），可視內容畫在上面那層。
                    */}
                    <button
                      type="button"
                      onClick={() => onOpenEndpoint(item.endpointId)}
                      className="整列連結"
                    >
                      {item.slug}
                    </button>
                  </li>
                )
              })}
            </ul>
          </section>
        )}

        {nextCursor !== null && (
          <div className="flex justify-center">
            {/* 子節點維持純字串：既有測試以 children.join('') 取得此按鈕。 */}
            <button
              type="button"
              disabled={loadingMore}
              onClick={() => { void loadMore() }}
              className="rounded-xl border border-outline-variant px-6 py-2 font-body-md text-body-md font-semibold text-on-surface transition-colors hover:bg-surface-container-highest disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loadingMore ? '載入中…' : '載入更多'}
            </button>
          </div>
        )}
      </div>
    </應用框架>
  )
}
