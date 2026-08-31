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
import { listSessions, type SessionSummary } from '../api/sessions'
import {
  consumeProtectedOperation,
  dispatchProtectedOperation,
  type AuthorizedRequest,
  type ProtectedStateOwner,
} from './sessionAuthority'

type SessionState =
  | { status: 'initializing' }
  | { status: 'anonymous' }
  | { status: 'authenticated'; user: AuthUser }

// 與登入狀態無關的操作，三個 status 變體共用。
interface 工作階段操作 {
  login(username: string, password: string): Promise<void>
  logout(): Promise<void>
  registerProtectedStateOwner(erase: () => void): {
    owner: ProtectedStateOwner
    unregister(): void
  }
  runAuthorized<T>(request: Readonly<AuthorizedRequest<T>>): Promise<T>
  recentSessions: readonly SessionSummary[]
  refreshRecentSessions(signal?: AbortSignal): Promise<void>
}

// 寫成 top-level union（而非 `工作階段操作 & (…|…)`）是刻意的：只有這個形狀
// 能讓消費端 `const { status, user } = useSession()` 解構後仍收窄 —— 判斷過
// `status === 'authenticated'` 之後 user 即為 AuthUser，不需另外補 null 守衛。
export type SessionContextValue =
  | (工作階段操作 & { status: 'initializing'; user: null })
  | (工作階段操作 & { status: 'anonymous'; user: null })
  | (工作階段操作 & { status: 'authenticated'; user: AuthUser })

const SessionContext = createContext<SessionContextValue | null>(null)

function abortError(): DOMException {
  return new DOMException('驗證操作已取消', 'AbortError')
}

function samePrincipal(left: AuthUser, right: AuthUser): boolean {
  return left.id === right.id && left.username === right.username && left.role === right.role
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: 'initializing' })
  const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([])
  const stateRef = useRef<SessionState>({ status: 'initializing' })
  const initialRequest = useRef<{
    epoch: number
    controller: AbortController
    promise: Promise<AuthSession | null>
  } | null>(null)
  const activeController = useRef<AbortController | null>(null)
  const protectedControllers = useRef(new Map<AbortController, ProtectedStateOwner>())
  const owners = useRef(new Map<ProtectedStateOwner, () => void>())
  const operationEpoch = useRef(0)
  const mounted = useRef(false)
  const mountGeneration = useRef(0)

  const commitState = useCallback((next: SessionState) => {
    stateRef.current = next
    setState(next)
  }, [])

  const eraseProtectedState = useCallback(() => {
    setRecentSessions([])
    const snapshot = [...owners.current.entries()]
    for (const controller of protectedControllers.current.keys()) controller.abort()
    protectedControllers.current.clear()
    for (const [owner, erase] of snapshot) {
      if (!owners.current.has(owner)) continue
      try { erase() } catch { /* one consumer cannot retain another consumer's state */ }
    }
  }, [])

  const refreshRecentSessions = useCallback(async (signal?: AbortSignal) => {
    const current = stateRef.current
    if (current.status !== 'authenticated') {
      setRecentSessions([])
      return
    }
    const sessions = await listSessions(20, signal)
    if (stateRef.current.status === 'authenticated' && stateRef.current.user.id === current.user.id) {
      setRecentSessions(sessions)
    }
  }, [])

  const beginOperation = useCallback(() => {
    eraseProtectedState()
    activeController.current?.abort()
    const controller = new AbortController()
    activeController.current = controller
    return { controller, epoch: ++operationEpoch.current }
  }, [eraseProtectedState])

  const finishOperation = useCallback((controller: AbortController) => {
    if (activeController.current === controller) activeController.current = null
  }, [])

  const assertCurrentOperation = useCallback((controller: AbortController, epoch: number) => {
    if (!mounted.current || controller.signal.aborted ||
        operationEpoch.current !== epoch || activeController.current !== controller) {
      throw abortError()
    }
  }, [])

  const registerProtectedStateOwner = useCallback((erase: () => void) => {
    if (typeof erase !== 'function' || !mounted.current) throw abortError()
    const owner = Object.freeze(Object.create(null)) as ProtectedStateOwner
    owners.current.set(owner, erase)
    let registered = true
    return {
      owner,
      unregister() {
        if (!registered) return
        registered = false
        const callback = owners.current.get(owner)
        owners.current.delete(owner)
        for (const [controller, candidate] of protectedControllers.current) {
          if (candidate === owner) {
            protectedControllers.current.delete(controller)
            controller.abort()
          }
        }
        try { callback?.() } catch { /* unmount erasure is best effort and isolated */ }
      },
    }
  }, [])

  const runAuthorized = useCallback(async <T,>(request: Readonly<AuthorizedRequest<T>>): Promise<T> => {
    const manifest = consumeProtectedOperation(request.operation)
    const erase = owners.current.get(request.owner)
    const current = stateRef.current
    if (!erase || current.status !== 'authenticated' || activeController.current !== null || request.signal?.aborted) {
      throw abortError()
    }
    const controller = new AbortController()
    const cancel = () => controller.abort()
    request.signal?.addEventListener('abort', cancel, { once: true })
    protectedControllers.current.set(controller, request.owner)
    try {
      const fresh = await getSession(controller.signal)
      if (!mounted.current || controller.signal.aborted || stateRef.current !== current) throw abortError()
      if (fresh === null || !samePrincipal(fresh.user, current.user)) {
        eraseProtectedState()
        commitState(fresh === null ? { status: 'anonymous' } : { status: 'authenticated', user: fresh.user })
        throw abortError()
      }
      return await dispatchProtectedOperation<T>(manifest, fresh.csrfToken, controller.signal)
    } catch (error) {
      if (controller.signal.aborted || request.signal?.aborted) throw abortError()
      throw error
    } finally {
      request.signal?.removeEventListener('abort', cancel)
      protectedControllers.current.delete(controller)
    }
  }, [commitState, eraseProtectedState])

  useEffect(() => {
    mounted.current = true
    const generation = ++mountGeneration.current
    let active = true
    if (initialRequest.current === null) {
      const operation = beginOperation()
      initialRequest.current = { ...operation, promise: getSession(operation.controller.signal) }
    }
    const request = initialRequest.current
    request.promise.then(
      (session) => {
        try {
          if (!active) throw abortError()
          assertCurrentOperation(request.controller, request.epoch)
          commitState(session ? { status: 'authenticated', user: session.user } : { status: 'anonymous' })
        } catch (error) {
          if (!(error instanceof DOMException && error.name === 'AbortError')) throw error
        } finally {
          if (active) finishOperation(request.controller)
          if (initialRequest.current === request) initialRequest.current = null
        }
      },
      (error) => {
        try {
          if (!active) throw abortError()
          assertCurrentOperation(request.controller, request.epoch)
          if (!(error instanceof DOMException && error.name === 'AbortError')) commitState({ status: 'anonymous' })
        } catch (currentError) {
          if (!(currentError instanceof DOMException && currentError.name === 'AbortError')) throw currentError
        } finally {
          if (active) finishOperation(request.controller)
          if (initialRequest.current === request) initialRequest.current = null
        }
      },
    )
    return () => {
      active = false
      mounted.current = false
      eraseProtectedState()
      owners.current.clear()
      queueMicrotask(() => {
        if (!mounted.current && mountGeneration.current === generation) {
          activeController.current?.abort()
          activeController.current = null
        }
      })
    }
  }, [assertCurrentOperation, beginOperation, commitState, eraseProtectedState, finishOperation])

  const login = useCallback(async (username: string, password: string) => {
    const previous = stateRef.current
    const { controller, epoch } = beginOperation()
    try {
      const session = await requestLogin(username, password, controller.signal)
      assertCurrentOperation(controller, epoch)
      commitState({ status: 'authenticated', user: session.user })
      assertCurrentOperation(controller, epoch)
    } catch (error) {
      try {
        assertCurrentOperation(controller, epoch)
        commitState(previous.status === 'authenticated' ? previous : { status: 'anonymous' })
        throw error
      } catch (currentError) {
        if (currentError === error) throw error
        throw abortError()
      }
    } finally {
      finishOperation(controller)
    }
  }, [assertCurrentOperation, beginOperation, commitState, finishOperation])

  const logout = useCallback(async () => {
    const previous = stateRef.current
    const { controller, epoch } = beginOperation()
    try {
      if (previous.status === 'authenticated') {
        const fresh = await getSession(controller.signal)
        assertCurrentOperation(controller, epoch)
        if (fresh !== null && samePrincipal(fresh.user, previous.user)) {
          await requestLogout(fresh.csrfToken, controller.signal)
          assertCurrentOperation(controller, epoch)
        } else if (fresh !== null) {
          commitState({ status: 'authenticated', user: fresh.user })
          return
        }
      }
      assertCurrentOperation(controller, epoch)
      commitState({ status: 'anonymous' })
    } catch (error) {
      try {
        assertCurrentOperation(controller, epoch)
        commitState(previous.status === 'authenticated' ? previous : { status: 'anonymous' })
        throw error
      } catch (currentError) {
        if (currentError === error) throw error
        throw abortError()
      }
    } finally {
      finishOperation(controller)
    }
  }, [assertCurrentOperation, beginOperation, commitState, finishOperation])

  const value = useMemo<SessionContextValue>(() => {
    const 操作 = { login, logout, registerProtectedStateOwner, runAuthorized, recentSessions, refreshRecentSessions }
    // 逐個變體明確帶出 status 字面值，才能對上 union 的判別欄位。
    if (state.status === 'authenticated') {
      return { ...操作, status: 'authenticated', user: state.user }
    }
    if (state.status === 'anonymous') {
      return { ...操作, status: 'anonymous', user: null }
    }
    return { ...操作, status: 'initializing', user: null }
  }, [state, login, logout, registerProtectedStateOwner, runAuthorized, recentSessions, refreshRecentSessions])

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession 必須在 SessionProvider 內使用')
  return value
}
