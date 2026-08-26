import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  LOGS_ERROR_MESSAGE,
  LOGS_NOT_FOUND_MESSAGE,
  LogsError,
  getInvocationDetail,
  listInvocations,
  type AdminInvocationDetail as InvocationDetailData,
  type InvocationListFilters,
  type InvocationListItem,
  type RedactionRequest,
} from '../api/logs'
import { useSession } from '../app/SessionProvider'
import { createRedactionOperation, type ProtectedStateOwner } from '../app/sessionAuthority'
import AdminInvocationDetail from '../features/logs/AdminInvocationDetail'
import { 空狀態, 欄位, 載入中, 輸入樣式, 等寬輸入樣式, 成功訊息, 錯誤訊息 } from '../ui/元件'
import { 狀態文字 } from '../ui/格式'
import 圖示 from '../ui/圖示'
import 應用框架 from '../ui/應用框架'

const FORBIDDEN_MESSAGE = '只有管理者可查看完整呼叫紀錄。'

/*
 * 清單列的時刻只顯示到秒（日期在同一次查詢裡多半相同，年份是雜訊）。
 * 詳情頁的絕對時間仍走 features/logs 那邊的 ISO 呈現，兩者用途不同。
 */
const 時刻格式 = new Intl.DateTimeFormat('zh-TW', {
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
})

function 格式化時刻(秒: number | null): string {
  if (typeof 秒 !== 'number' || !Number.isFinite(秒)) return '—'
  const 日期 = new Date(秒 * 1000)
  return Number.isFinite(日期.getTime()) ? 時刻格式.format(日期) : '—'
}

function 格式化延遲(毫秒: number | null): string {
  return 毫秒 === null ? '—' : `${毫秒} ms`
}

/*
 * 篩選欄位收 datetime-local，送出前才換算成 epoch 秒。
 * API 契約（from_at / to_at 為數字）完全沒動，人類可讀只是呈現層的事。
 */
function 本地時間轉epoch秒(值: string): number | null {
  if (值.trim() === '') return null
  const 毫秒 = new Date(值).getTime()
  return Number.isFinite(毫秒) ? Math.floor(毫秒 / 1000) : null
}

const 時間範圍選項 = [
  { 值: 'custom', 標籤: '自訂範圍', 秒: null },
  { 值: '24h', 標籤: '最近 24 小時', 秒: 86_400 },
  { 值: '7d', 標籤: '最近 7 天', 秒: 604_800 },
  { 值: '30d', 標籤: '最近 30 天', 秒: 2_592_000 },
] as const

/* 狀態燈：清單要能一眼掃出失敗那幾列，不用逐列讀字。 */
const 狀態燈樣式: Record<string, string> = {
  succeeded: 'bg-secondary',
  running: 'bg-secondary/60',
  pending: 'bg-tertiary',
  rate_limited: 'bg-tertiary',
  failed: 'bg-error',
  invalid_api_key: 'bg-error',
}

export interface InvocationLogsPageProps {
  onClose(): void
  onOpenEndpoints?(): void
}

export default function InvocationLogsPage({ onClose, onOpenEndpoints }: InvocationLogsPageProps) {
  const { user, logout, registerProtectedStateOwner, runAuthorized } = useSession()
  const [endpointId, setEndpointId] = useState('')
  const [fromAt, setFromAt] = useState('')
  const [toAt, setToAt] = useState('')
  const [timeRange, setTimeRange] = useState<string>('custom')
  const [status, setStatus] = useState('')
  const [errorCode, setErrorCode] = useState('')
  const [items, setItems] = useState<InvocationListItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [activeFilters, setActiveFilters] = useState<InvocationListFilters>({ limit: 50 })
  const [detail, setDetail] = useState<InvocationDetailData | null>(null)
  const [selected, setSelected] = useState<InvocationListItem | null>(null)
  const [pending, setPending] = useState(false)
  const [detailPending, setDetailPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [redactionPending, setRedactionPending] = useState(false)
  const [redactionCompleted, setRedactionCompleted] = useState(false)
  const [queried, setQueried] = useState(false)
  const epoch = useRef(0)
  const controllers = useRef(new Set<AbortController>())
  const protectedOwner = useRef<ProtectedStateOwner | null>(null)
  const redactionLocked = useRef(false)

  const clearSensitiveState = useCallback(() => {
    setDetail(null)
    setSelected(null)
    setDetailPending(false)
  }, [])

  const invalidate = useCallback((clearList = false) => {
    epoch.current += 1
    for (const controller of controllers.current) controller.abort()
    controllers.current.clear()
    clearSensitiveState()
    setPending(false)
    setRedactionPending(false)
    setRedactionCompleted(false)
    redactionLocked.current = false
    if (clearList) {
      setItems([])
      setNextCursor(null)
    }
    return epoch.current
  }, [clearSensitiveState])

  useEffect(() => () => { invalidate(true) }, [invalidate])

  useEffect(() => {
    const registration = registerProtectedStateOwner(() => invalidate(true))
    protectedOwner.current = registration.owner
    return () => {
      protectedOwner.current = null
      registration.unregister()
    }
  }, [invalidate, registerProtectedStateOwner])

  useEffect(() => {
    if (user?.role !== 'admin') invalidate(true)
  }, [user?.id, user?.role, invalidate])

  const loadList = useCallback(async (
    targetEndpoint: string,
    filters: InvocationListFilters,
    append: boolean,
  ) => {
    const requestEpoch = invalidate(!append)
    const controller = new AbortController()
    controllers.current.add(controller)
    setPending(true)
    setError(null)
    try {
      const page = await listInvocations(targetEndpoint, filters, controller.signal)
      if (epoch.current !== requestEpoch || controller.signal.aborted) return
      setItems((current) => append ? [...current, ...page.items] : page.items)
      setNextCursor(page.nextCursor)
      setActiveFilters({ ...filters, cursor: undefined })
    } catch {
      if (epoch.current === requestEpoch && !controller.signal.aborted) {
        setItems([])
        setNextCursor(null)
        setError(LOGS_ERROR_MESSAGE)
      }
    } finally {
      controllers.current.delete(controller)
      if (epoch.current === requestEpoch) setPending(false)
    }
  }, [invalidate])

  async function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const 快捷 = 時間範圍選項.find((選項) => 選項.值 === timeRange)
    const 現在 = Math.floor(Date.now() / 1000)
    const 起始 = 快捷?.秒 == null ? 本地時間轉epoch秒(fromAt) : 現在 - 快捷.秒
    const 結束 = 快捷?.秒 == null ? 本地時間轉epoch秒(toAt) : null
    const filters: InvocationListFilters = {
      limit: 50,
      ...(起始 === null ? {} : { fromAt: 起始 }),
      ...(結束 === null ? {} : { toAt: 結束 }),
      ...(status ? { status } : {}),
      ...(errorCode.trim() ? { errorCode: errorCode.trim() } : {}),
    }
    setQueried(true)
    await loadList(endpointId.trim(), filters, false)
  }

  function resetFilters() {
    setFromAt('')
    setToAt('')
    setTimeRange('custom')
    setStatus('')
    setErrorCode('')
  }

  async function openDetail(item: InvocationListItem) {
    const requestEpoch = invalidate()
    const controller = new AbortController()
    controllers.current.add(controller)
    setSelected(item)
    setDetailPending(true)
    setError(null)
    try {
      const value = await getInvocationDetail(item.endpointId, item.invocationId, controller.signal)
      if (epoch.current !== requestEpoch || controller.signal.aborted) return
      setDetail(value)
    } catch (caught) {
      if (epoch.current === requestEpoch && !controller.signal.aborted) {
        clearSensitiveState()
        setError(caught instanceof LogsError && caught.status === 404 ? LOGS_NOT_FOUND_MESSAGE : LOGS_ERROR_MESSAGE)
      }
    } finally {
      controllers.current.delete(controller)
      if (epoch.current === requestEpoch) setDetailPending(false)
    }
  }

  async function redactSelected(request: RedactionRequest): Promise<void> {
    const owner = protectedOwner.current
    const item = selected
    if (!owner || !item || redactionLocked.current) throw new DOMException('要求已取消', 'AbortError')
    redactionLocked.current = true
    epoch.current += 1
    for (const active of controllers.current) active.abort()
    controllers.current.clear()
    setDetail(null)
    setDetailPending(false)
    setRedactionPending(true)
    setRedactionCompleted(false)
    setError(null)
    const controller = new AbortController()
    controllers.current.add(controller)
    try {
      await runAuthorized({
        owner,
        operation: createRedactionOperation(
          item.endpointId, item.invocationId, request, globalThis.crypto.randomUUID(),
        ),
        signal: controller.signal,
      })
      if (controller.signal.aborted || protectedOwner.current !== owner) return
      controllers.current.delete(controller)
      await openDetail(item)
      if (protectedOwner.current === owner) setRedactionCompleted(true)
    } catch (caught) {
      if (!controller.signal.aborted) setError(LOGS_ERROR_MESSAGE)
      throw caught
    } finally {
      controllers.current.delete(controller)
      redactionLocked.current = false
      setRedactionPending(false)
    }
  }

  if (user?.role !== 'admin') {
    return <main className="app-shell p-xl"><p role="alert">{FORBIDDEN_MESSAGE}</p></main>
  }

  /* 錯誤跟著它的來源走：清單的錯誤留在左欄，詳情／遮蔽的錯誤放右欄。 */
  const 錯誤在詳情欄 = selected !== null

  return (
    <應用框架
      目前分頁="稽核"
      標題="完整呼叫紀錄"
      標題Id="logs-title"
      副標題="用於治理與合規的不可逆執行紀錄。"
      滿版={true}
      on開啟對話={() => { invalidate(true); onClose() }}
      on開啟端點={onOpenEndpoints === undefined ? undefined : () => { invalidate(true); onOpenEndpoints() }}
      on登出={() => {
        invalidate(true)
        void logout().catch(() => { setError(LOGS_ERROR_MESSAGE) })
      }}
    >
      {/* 不掛 .應用主內容：那個 class 會讓外層自己捲，兩欄各自捲動才是這頁要的 */}
      <main className="flex min-h-0 flex-1">

        {/* ── 左欄：篩選 ＋ 清單 ───────────────────────────── */}
        <section
          aria-label="呼叫紀錄清單"
          className="flex w-[21rem] shrink-0 flex-col border-r border-outline-variant bg-surface-container-low xl:w-[24rem]"
        >
          <div className="shrink-0 border-b border-outline-variant p-md">
            <div className="mb-sm flex items-center justify-between gap-sm">
              <h2 className="font-headline-sm text-headline-sm text-on-surface">日誌篩選</h2>
              <button
                type="button"
                onClick={resetFilters}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-body-md text-body-md text-secondary transition-colors hover:bg-secondary/10"
              >
                <span aria-hidden={true}><圖示 名稱="重新載入" 大小={14} /></span>
                重設
              </button>
            </div>

            <form aria-label="篩選呼叫紀錄" onSubmit={submitFilters} className="flex flex-col gap-sm">
              {/*
                端點識別碼必填：API 路徑是 /api/admin/endpoints/{endpoint_id}/invocations，
                沒有跨端點的全域清單，所以空著時「查詢」必須 disabled。
              */}
              <欄位 標籤="端點識別碼" htmlFor="logs-endpoint">
                <input id="logs-endpoint" value={endpointId} required maxLength={128}
                  className={等寬輸入樣式} placeholder="endpoint-id"
                  onChange={(event) => setEndpointId(event.currentTarget.value)} />
              </欄位>

              <div className="grid grid-cols-2 gap-sm">
                <欄位 標籤="狀態" htmlFor="logs-status">
                  <select id="logs-status" value={status} className={輸入樣式}
                    onChange={(event) => setStatus(event.currentTarget.value)}>
                    <option value="">全部</option>
                    <option value="pending">等待中</option>
                    <option value="running">執行中</option>
                    <option value="succeeded">成功</option>
                    <option value="failed">失敗</option>
                    <option value="rate_limited">流量限制</option>
                    <option value="invalid_api_key">API Key 無效</option>
                  </select>
                </欄位>
                <欄位 標籤="錯誤碼" htmlFor="logs-error-code">
                  <input id="logs-error-code" value={errorCode} maxLength={128} className={等寬輸入樣式}
                    onChange={(event) => setErrorCode(event.currentTarget.value)} />
                </欄位>
              </div>

              <欄位 標籤="時間範圍" htmlFor="logs-time-range">
                <select id="logs-time-range" value={timeRange} className={輸入樣式}
                  onChange={(event) => setTimeRange(event.currentTarget.value)}>
                  {時間範圍選項.map((選項) => (
                    <option key={選項.值} value={選項.值}>{選項.標籤}</option>
                  ))}
                </select>
              </欄位>

              {timeRange === 'custom' && (
                <div className="grid grid-cols-2 gap-sm">
                  <欄位 標籤="起始時間" htmlFor="logs-from-at">
                    <input id="logs-from-at" type="datetime-local" step="1" value={fromAt} className={輸入樣式}
                      onChange={(event) => setFromAt(event.currentTarget.value)} />
                  </欄位>
                  <欄位 標籤="結束時間" htmlFor="logs-to-at">
                    <input id="logs-to-at" type="datetime-local" step="1" value={toAt} className={輸入樣式}
                      onChange={(event) => setToAt(event.currentTarget.value)} />
                  </欄位>
                </div>
              )}

              {/* 子節點維持純字串：既有測試以 children.join('') 取得此按鈕。 */}
              <button type="submit" disabled={pending || !endpointId.trim()}
                className="mt-xs w-full rounded bg-secondary px-6 py-2 font-body-md text-body-md font-semibold text-on-secondary transition-colors hover:bg-secondary/90 disabled:cursor-not-allowed disabled:opacity-50">
                {pending ? '載入中…' : '查詢'}
              </button>
            </form>
          </div>

          {!錯誤在詳情欄 && error && <div className="p-md"><錯誤訊息>{error}</錯誤訊息></div>}

          <div className="min-h-0 flex-1 overflow-y-auto">
            {pending && items.length === 0 && <div className="px-md"><載入中>正在載入呼叫紀錄…</載入中></div>}

            {!pending && items.length === 0 && !error && (
              <空狀態>{queried ? '尚無呼叫紀錄。' : '輸入端點識別碼後查詢。'}</空狀態>
            )}

            <ul aria-label="呼叫紀錄" className="flex flex-col gap-xs p-sm">
              {items.map((item) => {
                const 已選 = selected?.invocationId === item.invocationId
                return (
                  <li key={item.invocationId}>
                    {/*
                      data-invocation-id 是這一列的穩定把手：
                      版面會變，識別碼不會，測試以它取節點而不是比對顯示字串。
                    */}
                    <button type="button" aria-pressed={已選} data-invocation-id={item.invocationId}
                      onClick={() => { void openDetail(item) }}
                      className={[
                        'block w-full rounded-lg border p-sm text-left transition-colors',
                        已選
                          ? 'border-secondary bg-secondary-fixed/40'
                          : 'border-transparent hover:bg-surface-container-high',
                      ].join(' ')}>
                      <span className="mb-1 flex items-center justify-between gap-sm">
                        <span className="flex min-w-0 items-center gap-sm">
                          <span aria-hidden={true}
                            className={[
                              'size-2 shrink-0 rounded-full',
                              狀態燈樣式[item.status] ?? 'bg-outline',
                            ].join(' ')} />
                          <span className="truncate font-code-md text-code-md text-on-surface">
                            {item.requestId}
                          </span>
                        </span>
                        <span className="shrink-0 font-code-md text-label-sm text-on-surface-variant">
                          {格式化時刻(item.createdAt)}
                        </span>
                      </span>
                      <span className="flex items-center justify-between gap-sm">
                        {/* 呼叫識別碼一定要留在可存取名稱裡：browser smoke 以 RegExp(invocationId) 取這一列 */}
                        <span className="min-w-0 truncate font-code-md text-label-sm text-on-surface-variant">
                          {item.invocationId}
                        </span>
                        <span className="flex shrink-0 items-center gap-sm font-body-md text-body-md">
                          {item.errorCode !== null && (
                            <span className="rounded bg-error-container px-1 font-code-md text-label-sm text-on-error-container">
                              {item.errorCode}
                            </span>
                          )}
                          {item.hasRedaction && (
                            <span className="rounded border border-error/40 px-1 font-label-sm text-label-sm text-error">
                              已遮蔽
                            </span>
                          )}
                          <span className={item.status === 'failed' || item.status === 'invalid_api_key'
                            ? 'text-error' : 'text-on-surface-variant'}>
                            {狀態文字(item.status)}
                          </span>
                          <span className="font-code-md text-label-sm text-on-surface-variant">
                            {格式化延遲(item.latencyMs)}
                          </span>
                        </span>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>

            {nextCursor && (
              <div className="p-sm pt-0">
                <button type="button" disabled={pending} onClick={() => {
                  void loadList(endpointId.trim(), { ...activeFilters, cursor: nextCursor }, true)
                }}
                  className="w-full rounded border border-outline-variant bg-surface-container-lowest py-1.5 font-body-md text-body-md font-semibold text-secondary transition-colors hover:border-secondary hover:bg-surface-container disabled:cursor-not-allowed disabled:opacity-50">
                  載入下一頁
                </button>
              </div>
            )}
          </div>
        </section>

        {/* ── 右欄：詳情 ───────────────────────────────────── */}
        <section className="min-w-0 flex-1 overflow-y-auto bg-surface">
          <div className="flex flex-col gap-md p-lg">
            {錯誤在詳情欄 && error && <錯誤訊息>{error}</錯誤訊息>}
            {redactionCompleted && <成功訊息>不可逆遮蔽已完成。</成功訊息>}
            {detailPending && <載入中>正在載入詳情…</載入中>}
            {detail && selected && (
              <AdminInvocationDetail detail={detail} hasRedaction={selected.hasRedaction}
                redactionPending={redactionPending} onRedact={redactSelected} />
            )}
            {!detailPending && !detail && !error && (
              <空狀態>從左側選擇一筆呼叫紀錄以查看詳情。</空狀態>
            )}
          </div>
        </section>
      </main>
    </應用框架>
  )
}
