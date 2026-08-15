export const DEFAULT_APP_ROUTE = '/' as const
export const ADMIN_LOGS_ROUTE = '/admin/invocations' as const

export const APP_ROUTES = Object.freeze([DEFAULT_APP_ROUTE, ADMIN_LOGS_ROUTE] as const)
export interface EndpointDetailRoute { kind: 'endpoint-detail'; endpointId: string }
export type AppRoute = (typeof APP_ROUTES)[number] | EndpointDetailRoute
const ENDPOINT_ID = /^[A-Za-z0-9_-]{1,128}$/

export function isAppRoute(value: unknown): value is (typeof APP_ROUTES)[number] {
  return typeof value === 'string' && APP_ROUTES.some((route) => route === value)
}

export function parseAppRoute(value: unknown): AppRoute | null {
  if (isAppRoute(value)) return value
  if (typeof value !== 'string') return null
  const match = /^\/endpoints\/([A-Za-z0-9_-]{1,128})$/.exec(value)
  return match && ENDPOINT_ID.test(match[1]) ? { kind: 'endpoint-detail', endpointId: match[1] } : null
}

export function formatEndpointDetailRoute(endpointId: string): string {
  if (!ENDPOINT_ID.test(endpointId)) throw new TypeError('端點識別碼無效')
  return `/endpoints/${endpointId}`
}

export function currentAppRoute(): AppRoute | null {
  if (typeof window === 'undefined') {
    return DEFAULT_APP_ROUTE
  }
  return parseAppRoute(window.location.pathname)
}

export function replaceAppRoute(target: unknown): target is AppRoute {
  if (typeof window === 'undefined') return false
  const path = typeof target === 'string' ? (isAppRoute(target) ? target : null)
    : target !== null && typeof target === 'object' &&
      (target as Partial<EndpointDetailRoute>).kind === 'endpoint-detail' &&
      typeof (target as Partial<EndpointDetailRoute>).endpointId === 'string'
      ? formatEndpointDetailRoute((target as EndpointDetailRoute).endpointId) : null
  if (path === null) return false
  window.history.replaceState(null, '', path)
  return true
}
