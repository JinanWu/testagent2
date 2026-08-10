import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  getSession,
  login as requestLogin,
  logout as requestLogout,
  type AuthSession,
  type AuthUser,
} from '../api/auth'

type SessionState =
  | { status: 'initializing' }
  | { status: 'anonymous' }
  | { status: 'authenticated'; session: AuthSession }

export interface SessionContextValue {
  status: SessionState['status']
  user: AuthUser | null
  login(username: string, password: string): Promise<void>
  logout(): Promise<void>
  replaceSession(session: AuthSession | null): void
}

const SessionContext = createContext<SessionContextValue | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: 'initializing' })
  const initialRequest = useRef<{
    epoch: number
    controller: AbortController
    promise: Promise<AuthSession | null>
  } | null>(null)
  const activeController = useRef<AbortController | null>(null)
  const operationEpoch = useRef(0)
  const mounted = useRef(false)
  const mountGeneration = useRef(0)

  const beginOperation = useCallback(() => {
    activeController.current?.abort()
    const controller = new AbortController()
    activeController.current = controller
    return { controller, epoch: ++operationEpoch.current }
  }, [])

  const finishOperation = useCallback((controller: AbortController) => {
    if (activeController.current === controller) {
      activeController.current = null
    }
  }, [])

  const assertCurrentOperation = useCallback((controller: AbortController, epoch: number) => {
    if (
      !mounted.current || controller.signal.aborted ||
      operationEpoch.current !== epoch || activeController.current !== controller
    ) {
      throw new DOMException('驗證操作已取消', 'AbortError')
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    const generation = ++mountGeneration.current
    let active = true
    if (initialRequest.current === null) {
      const operation = beginOperation()
      initialRequest.current = {
        ...operation,
        promise: getSession(operation.controller.signal),
      }
    }
    const request = initialRequest.current
    request.promise.then(
      (session) => {
        try {
          if (!active) throw new DOMException('驗證操作已取消', 'AbortError')
          assertCurrentOperation(request.controller, request.epoch)
          setState(session ? { status: 'authenticated', session } : { status: 'anonymous' })
        } catch (error) {
          if (!(error instanceof DOMException && error.name === 'AbortError')) {
            throw error
          }
        } finally {
          if (active) finishOperation(request.controller)
        }
      },
      (error) => {
        try {
          if (!active) throw new DOMException('驗證操作已取消', 'AbortError')
          assertCurrentOperation(request.controller, request.epoch)
          if (!(error instanceof DOMException && error.name === 'AbortError')) {
            setState({ status: 'anonymous' })
          }
        } catch (currentError) {
          if (!(currentError instanceof DOMException && currentError.name === 'AbortError')) {
            throw currentError
          }
        } finally {
          if (active) finishOperation(request.controller)
        }
      },
    )
    return () => {
      active = false
      mounted.current = false
      queueMicrotask(() => {
        if (!mounted.current && mountGeneration.current === generation) {
          activeController.current?.abort()
          activeController.current = null
        }
      })
    }
  }, [assertCurrentOperation, beginOperation, finishOperation])

  const login = useCallback(async (username: string, password: string) => {
    const { controller, epoch } = beginOperation()
    try {
      const session = await requestLogin(username, password, controller.signal)
      assertCurrentOperation(controller, epoch)
      setState({ status: 'authenticated', session })
      assertCurrentOperation(controller, epoch)
    } catch (error) {
      try {
        assertCurrentOperation(controller, epoch)
        throw error
      } catch (currentError) {
        if (currentError === error) throw error
        throw new DOMException('驗證操作已取消', 'AbortError')
      }
    } finally {
      finishOperation(controller)
    }
  }, [assertCurrentOperation, beginOperation, finishOperation])

  const logout = useCallback(async () => {
    const { controller, epoch } = beginOperation()
    const shouldRevoke = state.status === 'authenticated'
    if (mounted.current && operationEpoch.current === epoch) {
      setState({ status: 'anonymous' })
    }
    try {
      if (shouldRevoke) {
        const fresh = await getSession(controller.signal)
        assertCurrentOperation(controller, epoch)
        if (fresh !== null) {
          await requestLogout(fresh.csrfToken, controller.signal)
          assertCurrentOperation(controller, epoch)
        }
      }
      assertCurrentOperation(controller, epoch)
    } catch (error) {
      try {
        assertCurrentOperation(controller, epoch)
        throw error
      } catch (currentError) {
        if (currentError === error) throw error
        throw new DOMException('驗證操作已取消', 'AbortError')
      }
    } finally {
      finishOperation(controller)
    }
  }, [state, assertCurrentOperation, beginOperation, finishOperation])

  const replaceSession = useCallback((session: AuthSession | null) => {
    if (!mounted.current) return
    setState(session === null ? { status: 'anonymous' } : { status: 'authenticated', session })
  }, [])

  const value = useMemo<SessionContextValue>(
    () => ({
      status: state.status,
      user: state.status === 'authenticated' ? state.session.user : null,
      login,
      logout,
      replaceSession,
    }),
    [state, login, logout, replaceSession],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext)
  if (!value) {
    throw new Error('useSession 必須在 SessionProvider 內使用')
  }
  return value
}
