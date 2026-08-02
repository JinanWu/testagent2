export const DEFAULT_APP_ROUTE = '/' as const

export const APP_ROUTES = Object.freeze([DEFAULT_APP_ROUTE] as const)
export type AppRoute = (typeof APP_ROUTES)[number]

export function isAppRoute(value: unknown): value is AppRoute {
  return typeof value === 'string' && APP_ROUTES.some((route) => route === value)
}

export function currentAppRoute(): AppRoute | null {
  if (typeof window === 'undefined') {
    return DEFAULT_APP_ROUTE
  }
  return isAppRoute(window.location.pathname) ? window.location.pathname : null
}

export function replaceAppRoute(target: unknown): target is AppRoute {
  if (!isAppRoute(target) || typeof window === 'undefined') {
    return false
  }
  window.history.replaceState(null, '', target)
  return true
}
