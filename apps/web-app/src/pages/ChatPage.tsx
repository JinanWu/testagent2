import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { AUTH_ERROR_MESSAGE } from '../api/auth'
import { CHAT_ERROR_MESSAGE } from '../api/chat'
import { getSessionDetail, listSessions, type SessionSummary, type TranscriptMessage } from '../api/sessions'
import { useSession } from '../app/SessionProvider'
import { createSendChatOperation, type ProtectedStateOwner } from '../app/sessionAuthority'
import { 錯誤訊息 } from '../ui/元件'
import 圖示 from '../ui/圖示'
import 應用框架 from '../ui/應用框架'

/*
 * 輸入框的兩段高度（行高 26px + 上下 p-sm 各 8px）：
 * 塞得下就收合成 1 行，塞不下就一次跳到 5 行的固定高度並停住，
 * 不隨字數連續變化；再多的字在框內捲動。
 */
const 輸入框收合高度 = 42
const 輸入框展開高度 = 146

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
  const 輸入框Ref = useRef<HTMLTextAreaElement | null>(null)
  const [輸入區高度, set輸入區高度] = useState(0)
  const [捲軸寬度, set捲軸寬度] = useState(0)
  const 輸入區觀察器 = useRef<ResizeObserver | null>(null)
  const 捲動區觀察器 = useRef<ResizeObserver | null>(null)
  const [已複製索引, set已複製索引] = useState<number | null>(null)
  const 複製計時器 = useRef<number | null>(null)
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
      className="導覽項目 導覽項目-新增 w-full rounded-xl bg-primary-container px-2 py-2 font-body-md text-body-md font-semibold text-on-primary-container transition-colors hover:bg-primary-container/90"
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
        <ul aria-label="工作階段" className="flex min-h-0 flex-1 flex-col gap-xs overflow-y-auto px-md">
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
                    'block w-full truncate rounded-xl px-2 py-1.5 text-left font-body-md text-body-md transition-colors',
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
  /*
   * 先壓回收合高度才量得到真實 scrollHeight；塞不下就整段換成展開高度。
   * 只有這兩個值，所以不會出現「300 字高一點、500 字更高」的連續變化。
   * react-test-renderer 沒有真實節點，ref 為 null 時直接略過。
   */
  useEffect(() => {
    const 節點 = 輸入框Ref.current
    if (節點 === null) return
    節點.style.height = `${輸入框收合高度}px`
    if (節點.scrollHeight > 輸入框收合高度) {
      節點.style.height = `${輸入框展開高度}px`
    }
  }, [draft])


  /*
   * 複製泡泡文字。複製的是 message.content 本身——也就是泡泡上顯示的同一份字串，
   * 不含系統提示、工具呼叫或壓縮摘要（transcript 契約只允許 user/assistant）。
   * navigator.clipboard 在非安全來源不存在，try 會接住 TypeError，安靜略過。
   */
  const 複製訊息 = useCallback(async (索引: number, 內容: string) => {
    try {
      await navigator.clipboard.writeText(內容)
    } catch {
      return
    }
    set已複製索引(索引)
    if (複製計時器.current !== null) window.clearTimeout(複製計時器.current)
    複製計時器.current = window.setTimeout(() => { set已複製索引(null) }, 1500)
  }, [])

  /* 卸載時清掉「已複製」的還原計時器，避免對已卸載元件 setState。 */
  useEffect(() => () => {
    if (複製計時器.current !== null) window.clearTimeout(複製計時器.current)
  }, [])


  /*
   * 這兩個節點只在「非空白對話」分支渲染，所以一律用 callback ref 掛觀察器，
   * 不能用 useEffect(..., [])：首次掛載時若還是空白對話，ref 為 null 會直接
   * return，而 [] 依賴讓它永不重跑，觀察器就再也接不上——底部內距與捲軸補償
   * 都會永遠停在 0（表現為最後一則訊息被輸入區蓋住、左右泡泡對不齊）。
   */
  const 掛載輸入區 = useCallback((節點: HTMLDivElement | null) => {
    輸入區觀察器.current?.disconnect()
    輸入區觀察器.current = null
    if (節點 === null || typeof ResizeObserver === 'undefined') return
    const 觀察器 = new ResizeObserver(() => { set輸入區高度(節點.offsetHeight) })
    觀察器.observe(節點)
    輸入區觀察器.current = 觀察器
    set輸入區高度(節點.offsetHeight)
  }, [])

  /* 捲動區被捲軸吃掉的寬度；疊層式捲軸量到 0，補償自動失效。 */
  const 掛載捲動區 = useCallback((節點: HTMLDivElement | null) => {
    捲動區觀察器.current?.disconnect()
    捲動區觀察器.current = null
    if (節點 === null || typeof ResizeObserver === 'undefined') return
    const 更新 = () => { set捲軸寬度(節點.offsetWidth - 節點.clientWidth) }
    const 觀察器 = new ResizeObserver(更新)
    觀察器.observe(節點)
    捲動區觀察器.current = 觀察器
    更新()
  }, [])

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
            ref={輸入框Ref}
            rows={1}
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
            className="w-full resize-none overflow-y-auto border-none bg-transparent p-sm font-body-md text-body-md text-on-surface outline-none placeholder:text-placeholder"
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
                /* 只留圖示：沿用登出鈕的慣例，文字以 font-size:0 收掉但節點與無障礙名稱都在 */
                '導覽項目-僅圖示',
                /*
                  size-10 寫死 40x40 正方：靠內距推算會被文字節點繼承來的 line-height
                  把高度撐開而變成長方形；leading-none 再把那個行高歸零。
                  另不可加任何 text-* 字級類：utilities layer 會蓋掉
                  導覽項目-僅圖示 的 font-size:0，文字就藏不住。
                */
                'size-10 shrink-0 rounded-xl bg-secondary leading-none text-on-secondary transition-colors hover:bg-secondary/90 disabled:cursor-not-allowed disabled:opacity-50',
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
      /*
       * 對話頁一律不畫標題列與分隔線：整個工作區就是對話本身。
       * header 轉為 sr-only，h1 仍留在 DOM 當畫面的無障礙名稱。
       */
      分隔線={false}
      標題可見={false}
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
              描述您的需求即可，ColaX 為您處理。
            </p>
          </div>

          <div className="w-full max-w-[48rem]">{輸入區}</div>
        </div>
      ) : (
        <div className="relative flex min-h-0 flex-1 flex-col">
          <div
            ref={掛載捲動區}
            role="log"
            aria-live="polite"
            aria-label="對話內容"
            className="min-h-0 flex-1 overflow-y-auto p-lg"
            /* 疊在上方的輸入區會蓋住底部，補等高內距讓最後一則訊息捲得出來 */
            style={{ paddingBottom: 輸入區高度 }}
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
                    {/*
                      泡泡與複製鈕疊成一直欄：複製鈕落在泡泡「外面的下方」。
                      寬度上限移到這層，items-start/end 讓泡泡仍依內容縮放而非撐滿。
                    */}
                    <div
                      className={[
                        'flex min-w-0 max-w-[min(80%,42rem)] flex-col gap-xs',
                        是使用者 ? 'items-end' : 'items-start',
                      ].join(' ')}
                    >
                      <div
                        className={[
                          'min-w-0 max-w-full rounded-2xl border bg-surface-container-lowest p-md',
                          是使用者
                            ? 'rounded-tr-lg border-primary/25'
                            : 'rounded-tl-lg border-outline-variant',
                        ].join(' ')}
                      >
                        <p className="whitespace-pre-wrap break-words font-body-lg text-body-lg text-on-surface">
                          {message.content}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => { void 複製訊息(index, message.content) }}
                        aria-label={已複製索引 === index ? '已複製' : '複製訊息'}
                        className="rounded-md p-1 text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface"
                      >
                        <圖示 名稱={已複製索引 === index ? '成功' : '複製'} 大小={15} />
                      </button>
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

          <div
            ref={掛載輸入區}
            /* pt 決定霧玻璃層的總高＝漸層鋪開的距離；太短會讓整段過渡擠在一行字裡 */
            className="absolute inset-x-0 bottom-0 px-lg pb-lg pt-[15rem]"
            /* 右內距補上捲軸寬度，內容盒才與捲動區一致（見上方 useEffect） */
            style={{ paddingRight: `calc(var(--spacing-lg) + ${捲軸寬度}px)` }}
          >
            {/*
              霧玻璃層：blur 與底色都用遮罩由上往下淡入，訊息捲近輸入區時是漸漸糊掉，
              而不是撞上一條清楚的邊界——這層取代了原本的 border-t 分隔線。
              另外拆成獨立一層而非直接套在容器上，避免遮罩把輸入框本身也啃掉。
            */}
            <div
              aria-hidden={true}
              className="對話霧玻璃 pointer-events-none absolute inset-0 backdrop-blur-[30px]"
            />
            {/*
              寬度對齊兩方泡泡而非整列：訊息列 max-w-4xl(56rem) 扣掉兩側
              頭像 size-8(2rem) 與 gap-md(1rem)，剩 50rem。
            */}
            <div className="relative mx-auto w-full max-w-[50rem]">{輸入區}</div>
          </div>
        </div>
      )}
    </應用框架>
  )
}
