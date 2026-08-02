import { API_ROUTES, ApiResponseError, apiRequest } from './client'

const MAX_USERNAME_LENGTH = 128
const MAX_PASSWORD_LENGTH = 256
const MAX_ID_LENGTH = 128
const MAX_ROLE_LENGTH = 64
const MAX_CSRF_LENGTH = 512

export const AUTH_ERROR_MESSAGE = '驗證服務暫時無法使用，請稍後再試。'
export const CREDENTIALS_ERROR_MESSAGE = '帳號或密碼不正確。'

export interface AuthUser {
  id: string
  username: string
  role: string
}

export interface AuthSession {
  user: AuthUser
  csrfToken: string
}

export class AuthError extends Error {
  constructor(message = AUTH_ERROR_MESSAGE) {
    super(message)
    this.name = 'AuthError'
  }
}

function exactDataValues(value: unknown, keys: readonly string[]): Record<string, unknown> | null {
  try {
    if (typeof value !== 'object' || value === null || Object.getPrototypeOf(value) !== Object.prototype) {
      return null
    }
    const descriptors = Object.getOwnPropertyDescriptors(value)
    const ownKeys = Reflect.ownKeys(descriptors)
    if (ownKeys.length !== keys.length || !keys.every((key) => ownKeys.includes(key))) {
      return null
    }
    const values: Record<string, unknown> = {}
    for (const key of keys) {
      const descriptor = descriptors[key]
      if (
        descriptor === undefined || !('value' in descriptor) ||
        descriptor.enumerable !== true || descriptor.configurable !== true || descriptor.writable !== true
      ) {
        return null
      }
      values[key] = descriptor.value
    }
    return values
  } catch {
    return null
  }
}

function isBoundedString(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum
}

export function parseAuthSession(value: unknown): AuthSession {
  const outer = exactDataValues(value, ['user', 'csrf_token'])
  const user = outer === null ? null : exactDataValues(outer.user, ['id', 'username', 'role'])
  if (outer === null || user === null) {
    throw new AuthError()
  }
  if (
    !isBoundedString(user.id, MAX_ID_LENGTH) ||
    !isBoundedString(user.username, MAX_USERNAME_LENGTH) ||
    !isBoundedString(user.role, MAX_ROLE_LENGTH) ||
    !isBoundedString(outer.csrf_token, MAX_CSRF_LENGTH)
  ) {
    throw new AuthError()
  }
  return {
    user: { id: user.id, username: user.username, role: user.role },
    csrfToken: outer.csrf_token,
  }
}

export async function getSession(signal?: AbortSignal): Promise<AuthSession | null> {
  try {
    return parseAuthSession(await apiRequest(API_ROUTES.session, { signal, expectedStatus: 200 }))
  } catch (error) {
    if (error instanceof ApiResponseError && error.status === 401) {
      return null
    }
    throw new AuthError()
  }
}

export async function login(username: string, password: string, signal?: AbortSignal): Promise<AuthSession> {
  if (!isBoundedString(username, MAX_USERNAME_LENGTH) || !isBoundedString(password, MAX_PASSWORD_LENGTH)) {
    throw new AuthError(CREDENTIALS_ERROR_MESSAGE)
  }
  let body: string | null = JSON.stringify({ username, password })
  username = ''
  password = ''
  try {
    return parseAuthSession(
      await apiRequest(API_ROUTES.login, { method: 'POST', body, signal, expectedStatus: 200 }),
    )
  } catch (error) {
    if (error instanceof ApiResponseError && error.status === 401) {
      throw new AuthError(CREDENTIALS_ERROR_MESSAGE)
    }
    if (error instanceof AuthError) {
      throw error
    }
    throw new AuthError()
  } finally {
    body = null
  }
}

export async function logout(csrfToken: string, signal?: AbortSignal): Promise<void> {
  if (!isBoundedString(csrfToken, MAX_CSRF_LENGTH)) {
    throw new AuthError()
  }
  try {
    await apiRequest(API_ROUTES.logout, {
      method: 'POST',
      csrfToken,
      expectedStatus: 204,
      signal,
    })
  } catch {
    throw new AuthError()
  }
}
