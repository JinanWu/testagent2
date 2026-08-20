export const DEFAULT_APP_ROUTE = '/' as const
export const ENDPOINTS_ROUTE = '/endpoints' as const
export const ADMIN_LOGS_ROUTE = '/admin/invocations' as const

export const APP_ROUTES = Object.freeze([DEFAULT_APP_ROUTE, ENDPOINTS_ROUTE, ADMIN_LOGS_ROUTE] as const)
export interface EndpointDetailRoute { kind: 'endpoint-detail'; endpointId: string }
export interface EndpointNewRoute { kind: 'endpoint-new' }
export interface EndpointVersionNewRoute { kind: 'endpoint-version-new'; endpointId: string }
export type AppRoute = (typeof APP_ROUTES)[number] | EndpointDetailRoute | EndpointNewRoute | EndpointVersionNewRoute
const ENDPOINT_ID = /^[A-Za-z0-9_-]{1,128}$/

export function isAppRoute(value: unknown): value is (typeof APP_ROUTES)[number] {
  return typeof value === 'string' && APP_ROUTES.some((route) => route === value)
}

export function parseAppRoute(value: unknown): AppRoute | null {
  if (isAppRoute(value)) return value
  if (value === '/endpoints/new') return { kind: 'endpoint-new' }
  if (typeof value !== 'string') return null
  const versionMatch = /^\/endpoints\/([A-Za-z0-9_-]{1,128})\/versions\/new$/.exec(value)
  if (versionMatch) {
    return versionMatch[1] !== 'new' && ENDPOINT_ID.test(versionMatch[1])
      ? { kind: 'endpoint-version-new', endpointId: versionMatch[1] } : null
  }
  const match = /^\/endpoints\/([A-Za-z0-9_-]{1,128})$/.exec(value)
  return match && match[1] !== 'new' && ENDPOINT_ID.test(match[1])
    ? { kind: 'endpoint-detail', endpointId: match[1] } : null
}

export function formatEndpointDetailRoute(endpointId: string): string {
  if (!ENDPOINT_ID.test(endpointId) || endpointId === 'new') throw new TypeError('端點識別碼無效')
  return `/endpoints/${endpointId}`
}

export function formatEndpointVersionBuilderRoute(endpointId: string): string {
  return `${formatEndpointDetailRoute(endpointId)}/versions/new`
}

export function currentAppRoute(): AppRoute | null {
  if (typeof window === 'undefined') return DEFAULT_APP_ROUTE
  return parseAppRoute(window.location.pathname)
}

function formatRoute(target: AppRoute): string {
  if (typeof target === 'string') return target
  if (target.kind === 'endpoint-new') return '/endpoints/new'
  if (target.kind === 'endpoint-version-new') return formatEndpointVersionBuilderRoute(target.endpointId)
  return formatEndpointDetailRoute(target.endpointId)
}

export function replaceAppRoute(target: unknown): target is AppRoute {
  if (typeof window === 'undefined') return false
  let parsed: AppRoute | null = null
  if (typeof target === 'string') parsed = isAppRoute(target) ? target : parseAppRoute(target)
  else if (target !== null && typeof target === 'object') {
    const candidate = target as { kind?: unknown; endpointId?: unknown }
    if (candidate.kind === 'endpoint-new') parsed = { kind: 'endpoint-new' }
    else if ((candidate.kind === 'endpoint-detail' || candidate.kind === 'endpoint-version-new') &&
      typeof candidate.endpointId === 'string') {
      const path = candidate.kind === 'endpoint-detail'
        ? `/endpoints/${(candidate as EndpointDetailRoute).endpointId}`
        : `/endpoints/${(candidate as EndpointVersionNewRoute).endpointId}/versions/new`
      parsed = parseAppRoute(path)
    }
  }
  if (parsed === null) return false
  window.history.replaceState(null, '', formatRoute(parsed))
  return true
}
