import { useEffect, useRef, useState, type FormEvent } from 'react'
import { flushSync } from 'react-dom'
import { AUTH_ERROR_MESSAGE, AuthError } from '../api/auth'
import { useSession } from '../app/SessionProvider'
import { 按鈕, 欄位, 輸入樣式 } from '../ui/元件'
import 圖示 from '../ui/圖示'

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
      // Session status may unmount this page during flushSync; still navigate after success.
      if (operationEpoch.current === epoch) {
        if (mounted.current) {
          flushSync(() => setPassword(''))
        }
        if (operationEpoch.current === epoch) {
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
    <main className="app-shell flex min-h-screen items-center justify-center bg-background p-lg">
      <section
        aria-labelledby="login-title"
        className="w-full max-w-[28rem] rounded-lg border border-outline-variant bg-surface-container-lowest p-xl shadow-[0_4px_6px_-1px_rgba(15,23,42,0.1)]"
      >
        <div className="mb-xl text-center">
          <span className="mb-sm inline-flex size-12 items-center justify-center rounded-full bg-primary-container text-primary-fixed">
            <圖示 名稱="標誌" 大小={26} />
          </span>
          <h1 id="login-title" className="font-headline-md text-headline-md text-on-surface">
            ColaX
          </h1>
        </div>

        <form
          onSubmit={handleSubmit}
          aria-describedby={error ? 'login-error' : undefined}
          className="flex flex-col gap-md"
        >
          <欄位 標籤="帳號" htmlFor="username">
            <div className="relative">
              <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-on-surface-variant">
                <圖示 名稱="帳號" 大小={18} />
              </span>
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
                className={`${輸入樣式} pl-10`}
              />
            </div>
          </欄位>

          <欄位 標籤="密碼" htmlFor="password">
            <div className="relative">
              <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-on-surface-variant">
                <圖示 名稱="鎖定" 大小={18} />
              </span>
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
                className={`${輸入樣式} pl-10`}
              />
            </div>
          </欄位>

          {error && (
            <div className="flex items-start gap-sm rounded border border-error/30 bg-error-container px-md py-sm text-on-error-container">
              <span aria-hidden={true} className="mt-0.5 shrink-0">
                <圖示 名稱="錯誤" 大小={16} />
              </span>
              <p id="login-error" role="alert" className="font-body-md text-body-md">
                {error}
              </p>
            </div>
          )}

          <按鈕 樣式="主要" type="submit" disabled={submitting} className="mt-sm w-full py-2">
            {submitting ? '登入中…' : '登入'}
          </按鈕>
        </form>
      </section>
    </main>
  )
}
