import { useRef, useState, type FormEvent } from 'react'
import { useSession } from '../app/SessionProvider'

interface LocalMessage {
  id: number
  text: string
}

export default function ChatPage() {
  const { user, logout } = useSession()
  const [draft, setDraft] = useState('')
  const draftRef = useRef('')
  const [messages, setMessages] = useState<LocalMessage[]>([])
  const nextId = useRef(1)

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = draftRef.current.trim()
    if (!text) {
      return
    }
    draftRef.current = ''
    setMessages((current) => [...current, { id: nextId.current++, text }])
    setDraft('')
  }

  return (
    <main className="app-shell">
      <section className="welcome-card" aria-labelledby="chat-title">
        <p className="eyebrow">TestAgent2</p>
        <h1 id="chat-title">開始對話</h1>
        <p>已登入為 {user?.username}。系統會自動選擇適合的執行方式。</p>
        <button type="button" onClick={() => { void logout().catch(() => undefined) }}>
          登出
        </button>
        <section role="log" aria-live="polite" aria-label="對話內容">
          {messages.length === 0 ? (
            <p>尚無訊息。</p>
          ) : (
            messages.map((message) => <p key={message.id}>{message.text}</p>)
          )}
        </section>
        <form onSubmit={handleSubmit} aria-label="傳送訊息">
          <label htmlFor="chat-message">訊息</label>
          <textarea
            id="chat-message"
            name="message"
            rows={4}
            value={draft}
            onChange={(event) => {
              draftRef.current = event.currentTarget.value
              setDraft(event.currentTarget.value)
            }}
          />
          <button type="submit" disabled={!draft.trim()}>傳送</button>
        </form>
      </section>
    </main>
  )
}
