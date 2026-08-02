import { useEffect, useRef, useState, type FormEvent } from 'react'
import { flushSync } from 'react-dom'
import { AUTH_ERROR_MESSAGE, AuthError } from '../api/auth'
import { useSession } from '../app/SessionProvider'

export interface LoginPageProps {
  onAuthenticated?: () => void
}

export default function LoginPage({ onAuthenticated }: LoginPageProps) {
  const { login } = useSession()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const operationEpoch = useRef(0)
  const mounted = useRef(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submittingRef.current) {
      return
    }
    submittingRef.current = true
    const epoch = ++operationEpoch.current
    setSubmitting(true)
    setError(null)
    try {
      await login(username, password)
      if (mounted.current && operationEpoch.current === epoch) {
        flushSync(() => setPassword(''))
        if (mounted.current && operationEpoch.current === epoch) {
          onAuthenticated?.()
        }
      }
    } catch (reason) {
      if (
        !(reason instanceof DOMException && reason.name === 'AbortError') &&
        mounted.current && operationEpoch.current === epoch
      ) {
        setError(reason instanceof AuthError ? reason.message : AUTH_ERROR_MESSAGE)
      }
    } finally {
      submittingRef.current = false
      if (mounted.current && operationEpoch.current === epoch) {
        setPassword('')
        setSubmitting(false)
      }
    }
  }

  return (
    <main className="app-shell">
      <section className="welcome-card" aria-labelledby="login-title">
        <p className="eyebrow">TestAgent2</p>
        <h1 id="login-title">登入智慧工作空間</h1>
        <form onSubmit={handleSubmit} aria-describedby={error ? 'login-error' : undefined}>
          <div>
            <label htmlFor="username">帳號</label>
            <input
              id="username"
              name="username"
              type="text"
              autoComplete="username"
              required
              maxLength={128}
              value={username}
              onChange={(event) => setUsername(event.currentTarget.value)}
              disabled={submitting}
            />
          </div>
          <div>
            <label htmlFor="password">密碼</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={256}
              value={password}
              onChange={(event) => setPassword(event.currentTarget.value)}
              disabled={submitting}
            />
          </div>
          {error && <p id="login-error" role="alert">{error}</p>}
          <button type="submit" disabled={submitting}>
            {submitting ? '登入中…' : '登入'}
          </button>
        </form>
      </section>
    </main>
  )
}
