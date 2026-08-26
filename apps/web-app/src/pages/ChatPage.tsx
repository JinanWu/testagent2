import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { AUTH_ERROR_MESSAGE } from '../api/auth'
import { CHAT_ERROR_MESSAGE } from '../api/chat'
import { getSessionDetail, listSessions, type SessionSummary, type TranscriptMessage } from '../api/sessions'
import { useSession } from '../app/SessionProvider'
import { createSendChatOperation, type ProtectedStateOwner } from '../app/sessionAuthority'
import { 錯誤訊息 } from '../ui/元件'
import 圖示 from '../ui/圖示'
import 應用框架 from '../ui/應用框架'

const SESSION_ERROR_MESSAGE = '目前無法載入對話，請稍後再試。'

export default function ChatPage({
  onOpenEndpoints,
  onOpenAdminLogs,
}: {
  onOpenEndpoints?: () => void
  onOpenAdminLogs?: () => void
}) {
  const { user, logout, registerProtectedStateOwner, runAuthorized } = useSession()
  const [draft, setDraft] = useState('')
  const draftRef = useRef('')
  const [messages, setMessages] = useState<TranscriptMessage[]>([])
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const submitOwnerEpochRef = useRef<number | null>(null)
  const [detailPending, setDetailPending] = useState(false)
  const detailPendingRef = useRef(false)
  const epoch = useRef(0)
  const controllers = useRef(new Set<AbortController>())
  const [protectedOwner, setProtectedOwner] = useState<ProtectedStateOwner | null>(null)

  const invalidate = useCallback(() => {
    epoch.current += 1
    submitOwnerEpochRef.current = null
    for (const controller of controllers.current) controller.abort()
    controllers.current.clear()
    detailPendingRef.current = false
    setDetailPending(false)
    setPending(false)
    return epoch.current
  }, [])

  /*
   * 抹除受保護狀態的次數。送出失敗要把草稿還給輸入框，
   * 但登出／工作階段撤銷這種抹除路徑不能還原（草稿本身也是受保護內容），
   * 所以用這個計數器區分「使用者只是按了新增對話」與「狀態被抹除了」。
   */
  const eraseCountRef = useRef(0)
  const isComposingRef = useRef(false)

  const eraseProtectedState = useCallback(() => {
    eraseCountRef.current += 1
    invalidate()
    draftRef.current = ''
    setDraft('')
    setMessages([])
    setSessions([])
    setSessionId(null)
    setError(null)
  }, [invalidate])

  useEffect(() => {
    const registration = registerProtectedStateOwner(eraseProtectedState)
    setProtectedOwner(registration.owner)
    return () => {
      setProtectedOwner(null)
      registration.unregister()
    }
  }, [eraseProtectedState, registerProtectedStateOwner])

  const refreshSessions = useCallback(async (requestEpoch: number) => {
    const controller = new AbortController()
    controllers.current.add(controller)
    try {
      const next = await listSessions(20, controller.signal)
      if (epoch.current === requestEpoch && !controller.signal.aborted) setSessions(next)
    } catch {
      if (epoch.current === requestEpoch && !controller.signal.aborted) setError(SESSION_ERROR_MESSAGE)
    } finally {
      controllers.current.delete(controller)
    }
  }, [])

  useEffect(() => {
    if (protectedOwner === null) return
    const requestEpoch = invalidate()
    void refreshSessions(requestEpoch)
    return () => { invalidate() }
  }, [user?.id, protectedOwner, invalidate, refreshSessions])

  function newConversation() {
    invalidate()
    detailPendingRef.current = false
    setDetailPending(false)
    setSessionId(null)
    setMessages([])
    setError(null)
    setPending(false)
  }

  async function openSession(id: string) {
    const requestEpoch = invalidate()
    const controller = new AbortController()
    controllers.current.add(controller)
    setError(null)
    setPending(false)
    setMessages([])
    detailPendingRef.current = true
    setDetailPending(true)
    try {
      const detail = await getSessionDetail(id, controller.signal)
      if (epoch.current !== requestEpoch || controller.signal.aborted) return
      setSessionId(detail.session.id)
      setMessages(detail.messages)
    } catch {
      if (epoch.current === requestEpoch && !controller.signal.aborted) setError(SESSION_ERROR_MESSAGE)
    } finally {
      controllers.current.delete(controller)
      if (epoch.current === requestEpoch) {
        detailPendingRef.current = false
        setDetailPending(false)
      }
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = draftRef.current.trim()
    const requestEpoch = epoch.current
    if (!text || protectedOwner === null || submitOwnerEpochRef.current !== null || pending || detailPendingRef.current) return
    submitOwnerEpochRef.current = requestEpoch
    const controller = new AbortController()
    controllers.current.add(controller)
    /*
     * 樂觀顯示：本產品沒有 streaming，一次要等完整回覆，可能數十秒。
     * 若等回覆才把使用者訊息掛上去，整段等待期間畫面上看不到自己剛送出什麼，
     * 草稿也還留在輸入框裡，看起來像沒送出去。所以先上畫面、先清輸入框。
     */
    const eraseCountAtSubmit = eraseCountRef.current
    const 樂觀訊息: TranscriptMessage = { role: 'user', content: text }
    setMessages((current) => [...current, 樂觀訊息])
    draftRef.current = ''
    setDraft('')
    setPending(true)
    setError(null)
    try {
      const result = await runAuthorized({
        owner: protectedOwner,
        operation: createSendChatOperation(text, sessionId),
        signal: controller.signal,
      })
      if (epoch.current !== requestEpoch || controller.signal.aborted) return
      setSessionId(result.sessionId)
      setMessages((current) => [...current, result.reply])
      void refreshSessions(requestEpoch)
    } catch {
      /*
       * 草稿還給輸入框，讓使用者可以原地重送。
       * 這件事不受 epoch 影響（送出途中按了新增對話，草稿一樣要留著），
       * 只在狀態被抹除、或使用者已經另外輸入時才不還。
       */
      if (eraseCountRef.current === eraseCountAtSubmit && draftRef.current === '') {
        draftRef.current = text
        setDraft(text)
      }
      if (epoch.current === requestEpoch && !controller.signal.aborted) {
        /* 樂觀訊息以參考位址抽掉，不會誤刪內容剛好相同的歷史訊息 */
        setMessages((current) => current.filter((訊息) => 訊息 !== 樂觀訊息))
        setError(CHAT_ERROR_MESSAGE)
      }
    } finally {
      controllers.current.delete(controller)
      if (epoch.current === requestEpoch && submitOwnerEpochRef.current === requestEpoch) {
        submitOwnerEpochRef.current = null
        setPending(false)
      }
    }
  }

  /*
   * 頁面標題用「當前對話串標題」而不是固定的「對話」：固定字串在每一串對話都長一樣，
   * 等於沒有資訊。未選任何對話（新對話）時退回「未命名對話」。
   */
  const 目前對話 = sessions.find((session) => session.id === sessionId)
  const 頁面標題 = (sessionId !== null && 目前對話?.title) || '未命名對話'

  /* 子節點維持純字串：既有測試以 findByProps({ children: '新增對話' }) 取得此按鈕。 */
  const 新增對話按鈕 = (
    <button
      type="button"
      onClick={newConversation}
      className="導覽項目 導覽項目-新增 w-full rounded-xl bg-primary-container px-4 py-2 font-body-md text-body-md font-semibold text-on-primary-container transition-colors hover:bg-primary-container/90"
    >
      新增對話
    </button>
  )

  const 對話清單 = (
    <div className="mt-lg flex min-h-0 flex-1 flex-col">
      <p className="px-lg pb-xs pt-sm font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
        最近對話
      </p>
      {sessions.length === 0 ? (
        <p className="px-lg py-sm font-body-md text-body-md text-on-surface-variant/70">
          尚無對話紀錄。
        </p>
      ) : (
        <ul aria-label="工作階段" className="flex min-h-0 flex-1 flex-col gap-xs overflow-y-auto px-sm">
          {sessions.map((session) => {
            const isActive = session.id === sessionId
            return (
              <li key={session.id}>
                {/* 子節點維持純字串：測試以對話標題比對 children，並檢查 fontWeight 行內樣式。 */}
                <button
                  type="button"
                  aria-current={isActive ? 'page' : undefined}
                  aria-pressed={isActive}
                  style={isActive ? { fontWeight: 700 } : undefined}
                  onClick={() => { void openSession(session.id) }}
                  className={[
                    'block w-full truncate rounded-xl px-4 py-2 text-left font-body-md text-body-md transition-colors',
                    isActive
                      ? 'bg-surface-container-highest text-on-surface'
                      : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
                  ].join(' ')}
                >
                  {session.title || '未命名對話'}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )

  /*
   * 空白對話（剛按下新增對話）走「整頁式」：不畫分隔線，輸入框直接置中，
   * 一進來就能打字。有訊息之後才切回上方捲動、輸入框釘底的版面。
   */
  const 是空白對話 = messages.length === 0 && !pending && !detailPending

  const 輸入區 = (
    <>
      {/* 送出失敗時草稿會保留，錯誤就放在輸入區上方，讓「看到錯誤」與「重送」在同一處 */}
      {error && (
        <div className="mb-md">
          <錯誤訊息>{error}</錯誤訊息>
        </div>
      )}

      <form onSubmit={handleSubmit} aria-label="傳送訊息">
        <label htmlFor="chat-message" className="sr-only">
          訊息
        </label>
        <div className="rounded-2xl border border-outline-variant bg-surface-container-lowest p-sm shadow-[0_1px_2px_rgba(0,0,0,0.04)] transition-colors focus-within:border-primary/50">
          <textarea
            id="chat-message"
            name="message"
            rows={3}
            /* 整頁式的空白對話一進來就能直接打字 */
            autoFocus={是空白對話}
            value={draft}
            placeholder="請輸入您的指令或需求…"
            onChange={(event) => {
              draftRef.current = event.currentTarget.value
              setDraft(event.currentTarget.value)
            }}
            onCompositionStart={() => {
              isComposingRef.current = true
            }}
            onCompositionEnd={() => {
              isComposingRef.current = false
            }}
            onKeyDown={(event) => {
              // 支援 Enter 傳送，且避開中文輸入法的選字 Enter
              // keyCode === 229 是很多瀏覽器在輸入法組合期間按鍵會觸發的統一碼
              if (
                event.key === 'Enter' &&
                !event.shiftKey &&
                !isComposingRef.current &&
                event.keyCode !== 229
              ) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
            className="w-full resize-none border-none bg-transparent p-sm font-body-md text-body-md text-on-surface outline-none placeholder:text-placeholder"
          />
          <div className="flex justify-end">
            {/*
              子節點維持純字串：既有測試以 findByProps({ type: 'submit' }).props.children
              直接比對「傳送」／「傳送中…」。圖示因此改由偽元素繪製，不進入 DOM。
            */}
            <button
              type="submit"
              disabled={!draft.trim() || pending || detailPending}
              className={[
                '導覽項目',
                pending || detailPending ? '導覽項目-載入中' : '導覽項目-傳送',
                'rounded-xl bg-secondary px-4 py-2 font-body-md text-body-md font-semibold text-on-secondary transition-colors hover:bg-secondary/90 disabled:cursor-not-allowed disabled:opacity-50',
              ].join(' ')}
            >
              {detailPending ? '載入中…' : pending ? '傳送中…' : '傳送'}
            </button>
          </div>
        </div>
      </form>
    </>
  )

  return (
    <應用框架
      目前分頁="對話"
      標題={頁面標題}
      標題Id="chat-title"
      側欄頂部={新增對話按鈕}
      側欄額外={對話清單}
      滿版={true}
      分隔線={!是空白對話}
      標題可見={!是空白對話}
      on開啟端點={onOpenEndpoints}
      on開啟稽核={user?.role === 'admin' ? onOpenAdminLogs : undefined}
      on登出={() => {
        void logout().catch(() => { setError(AUTH_ERROR_MESSAGE) })
      }}
    >
      {是空白對話 ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-xl px-lg pb-xl">
          <div
            role="log"
            aria-live="polite"
            aria-label="對話內容"
            className="flex flex-col items-center text-center"
          >
            <span className="mb-md flex size-16 items-center justify-center rounded-full bg-primary-container/15 text-primary">
              <圖示 名稱="對話" 大小={28} />
            </span>
            <p className="mb-sm font-headline-md text-headline-md text-on-surface">開始新的對話</p>
            <p className="max-w-[28rem] font-body-md text-body-md text-on-surface-variant">
              描述您的需求即可，系統會自動選擇適合的執行方式。
            </p>
          </div>

          <div className="w-full max-w-[48rem]">{輸入區}</div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div
            role="log"
            aria-live="polite"
            aria-label="對話內容"
            className="min-h-0 flex-1 overflow-y-auto p-lg"
          >
            <div className="mx-auto flex w-full max-w-4xl flex-col gap-lg">
              {messages.map((message, index) => {
                const 是使用者 = message.role === 'user'
                return (
                  /* 使用者靠右、助理靠左，泡泡不撐滿寬度，對話才有來回的感覺 */
                  <div
                    key={`${message.role}-${index}`}
                    className={['flex gap-md', 是使用者 ? 'flex-row-reverse' : 'flex-row'].join(' ')}
                  >
                    <span
                      aria-hidden={true}
                      className={[
                        'flex size-8 shrink-0 items-center justify-center rounded-full border',
                        是使用者
                          ? 'border-outline-variant bg-surface-container-highest text-on-surface-variant'
                          : 'border-primary/30 bg-primary-container/15 text-primary',
                      ].join(' ')}
                    >
                      <圖示 名稱={是使用者 ? '帳號' : '標誌'} 大小={16} />
                    </span>
                    <div
                      className={[
                        'min-w-0 max-w-[min(80%,42rem)] rounded-2xl border bg-surface-container-lowest p-md',
                        是使用者
                          ? 'rounded-tr-lg border-primary/25'
                          : 'rounded-tl-lg border-outline-variant',
                      ].join(' ')}
                    >
                      <p className="whitespace-pre-wrap break-words font-body-lg text-body-lg text-on-surface">
                        {message.content}
                      </p>
                    </div>
                  </div>
                )
              })}

              {/* 沒有 streaming：送出後要等完整回覆回來，等待可能長達數十秒 */}
              {(pending || detailPending) && (
                <div className="flex gap-md">
                  <span
                    aria-hidden={true}
                    className="flex size-8 shrink-0 items-center justify-center rounded-full border border-primary/30 bg-primary-container/15 text-primary"
                  >
                    <圖示 名稱="標誌" 大小={16} />
                  </span>
                  <div className="flex items-center gap-sm rounded-2xl rounded-tl-lg border border-outline-variant bg-surface-container-lowest px-md py-3 text-primary">
                    <span className="sr-only">助理正在回覆</span>
                    <span aria-hidden={true} className="flex gap-1">
                      <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:0ms]" />
                      <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:150ms]" />
                      <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:300ms]" />
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="shrink-0 border-t border-outline-variant bg-surface p-lg">
            <div className="mx-auto w-full max-w-4xl">{輸入區}</div>
          </div>
        </div>
      )}
    </應用框架>
  )
}
