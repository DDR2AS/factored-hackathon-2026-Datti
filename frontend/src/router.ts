// Tiny hash router. Hash routes keep CloudFront untouched: a custom error response would
// apply to the whole distribution and turn /api/* 4xx into index.html.
// Only case_id or trace_id may appear in a URL; never tokens or customer ids.

import { useEffect, useState } from 'react'
import type { DemoKey } from './api/types'
import { DEMO_KEYS } from './demo/customers'

export type Route =
  | { name: 'judge' }
  | { name: 'chat'; demoKey: DemoKey }
  | { name: 'console'; caseId: string | null }
  | { name: 'trace'; traceId: string | null }
  | { name: 'evaluation' }
  | { name: 'notFound' }

const SAFE_ID = /^[A-Za-z0-9_-]{1,80}$/

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, '').replace(/\/+$/, '')
  if (path === '' || path === '/') return { name: 'judge' }
  const parts = path.split('/').filter(Boolean)
  const [head, arg] = parts
  switch (head) {
    case 'chat': {
      if (parts.length === 2 && (DEMO_KEYS as readonly string[]).includes(arg)) {
        return { name: 'chat', demoKey: arg as DemoKey }
      }
      return parts.length === 1 ? { name: 'judge' } : { name: 'notFound' }
    }
    case 'consola':
      if (parts.length === 1) return { name: 'console', caseId: null }
      return parts.length === 2 && SAFE_ID.test(arg) ? { name: 'console', caseId: arg } : { name: 'notFound' }
    case 'traza':
      if (parts.length === 1) return { name: 'trace', traceId: null }
      return parts.length === 2 && SAFE_ID.test(arg) ? { name: 'trace', traceId: arg } : { name: 'notFound' }
    case 'evaluacion':
      return parts.length === 1 ? { name: 'evaluation' } : { name: 'notFound' }
    default:
      return { name: 'notFound' }
  }
}

export function hrefFor(route: Route): string {
  switch (route.name) {
    case 'judge':
      return '#/'
    case 'chat':
      return `#/chat/${route.demoKey}`
    case 'console':
      return route.caseId ? `#/consola/${encodeURIComponent(route.caseId)}` : '#/consola'
    case 'trace':
      return route.traceId ? `#/traza/${encodeURIComponent(route.traceId)}` : '#/traza'
    case 'evaluation':
      return '#/evaluacion'
    case 'notFound':
      return '#/'
  }
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
