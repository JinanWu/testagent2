import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { AUTH_ERROR_MESSAGE, getSession } from '../api/auth'
import { CHAT_ERROR_MESSAGE, sendChat } from '../api/chat'
import { getSessionDetail, listSessions, type SessionSummary, type TranscriptMessage } from '../api/sessions'
import { useSession } from '../app/SessionProvider'

const SESSION_ERROR_MESSAGE = '目前無法載入對話，請稍後再試。'

export default function ChatPage() {
  const { user, logout, replaceSession } = useSession()
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

  const invalidate = useCallback(() => {
    epoch.current += 1
    submitOwnerEpochRef.current = null
    for (const controller of controllers.current) controller.abort()
    controllers.current.clear()
    return epoch.current
  }, [])

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
    const requestEpoch = invalidate()
    void refreshSessions(requestEpoch)
    return () => { invalidate() }
  }, [user?.id, invalidate, refreshSessions])

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
    if (!text || submitOwnerEpochRef.current !== null || pending || detailPendingRef.current) return
    submitOwnerEpochRef.current = requestEpoch
    const controller = new AbortController()
    controllers.current.add(controller)
    setPending(true)
    setError(null)
    try {
      const auth = await getSession(controller.signal)
      if (epoch.current !== requestEpoch || controller.signal.aborted) return
      if (!auth) {
        replaceSession(null)
        return
      }
      replaceSession(auth)
      const result = await sendChat(text, sessionId, auth.csrfToken, controller.signal)
      if (epoch.current !== requestEpoch || controller.signal.aborted) return
      setSessionId(result.sessionId)
      setMessages((current) => [...current, { role: 'user', content: text }, result.reply])
      draftRef.current = ''
      setDraft('')
      void refreshSessions(requestEpoch)
    } catch {
      if (epoch.current === requestEpoch && !controller.signal.aborted) setError(CHAT_ERROR_MESSAGE)
    } finally {
      controllers.current.delete(controller)
      if (epoch.current === requestEpoch && submitOwnerEpochRef.current === requestEpoch) {
        submitOwnerEpochRef.current = null
        setPending(false)
      }
    }
  }

  return (
    <main className="app-shell">
      <section className="welcome-card" aria-labelledby="chat-title">
        <p className="eyebrow">TestAgent2</p>
        <h1 id="chat-title">開始對話</h1>
        <p>已登入為 {user?.username}。系統會自動選擇適合的執行方式。</p>
        <button type="button" onClick={() => {
          invalidate()
          void logout().catch(() => { setError(AUTH_ERROR_MESSAGE) })
        }}>登出</button>
        <nav aria-label="工作階段">
          <button type="button" onClick={newConversation}>新增對話</button>
          {sessions.map((session) => {
            const isActive = session.id === sessionId
            return (
              <button
                type="button"
                key={session.id}
                aria-current={isActive ? 'page' : undefined}
                aria-pressed={isActive}
                style={isActive ? { backgroundColor: '#a45f32', color: '#fff', fontWeight: 700 } : undefined}
                onClick={() => { void openSession(session.id) }}
              >
                {session.title || '未命名對話'}
              </button>
            )
          })}
        </nav>
        {error && <p role="alert">{error}</p>}
        <section role="log" aria-live="polite" aria-label="對話內容">
          {messages.length === 0 ? <p>尚無訊息。</p> : messages.map((message, index) => (
            <p key={`${message.role}-${index}`}><strong>{message.role === 'user' ? '你' : '助理'}：</strong>{message.content}</p>
          ))}
        </section>
        <form onSubmit={handleSubmit} aria-label="傳送訊息">
          <label htmlFor="chat-message">訊息</label>
          <textarea id="chat-message" name="message" rows={4} value={draft} onChange={(event) => {
            draftRef.current = event.currentTarget.value
            setDraft(event.currentTarget.value)
          }} />
          <button type="submit" disabled={!draft.trim() || pending || detailPending}>
            {detailPending ? '載入中…' : pending ? '傳送中…' : '傳送'}
          </button>
        </form>
      </section>
    </main>
  )
}
