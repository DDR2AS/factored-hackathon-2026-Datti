// GET /api/health on landing load: wakes the Lambda, tells "iniciando…" (> 3 s) apart
// from "faltan dependencias" (deps=missing) and from an unreachable API.

import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { ClientErrorCode } from '../api/errors'
import { isApiClientError } from '../api/errors'
import { uiTimings } from '../api/config'

export type HealthState =
  | { kind: 'checking' }
  | { kind: 'starting' }
  | { kind: 'ok'; stage: string | null }
  | { kind: 'deps_missing'; stage: string | null }
  | { kind: 'down'; code: ClientErrorCode }

export function useHealth(api: ApiClient): { health: HealthState; recheck: () => void } {
  const [health, setHealth] = useState<HealthState>({ kind: 'checking' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    const slow = setTimeout(() => {
      if (!cancelled) setHealth({ kind: 'starting' })
    }, uiTimings.healthSlowMs)
    api
      .health()
      .then(
        (res) => {
          if (cancelled) return
          setHealth(res.deps === 'missing' ? { kind: 'deps_missing', stage: res.stage } : { kind: 'ok', stage: res.stage })
        },
        (e: unknown) => {
          if (!cancelled) setHealth({ kind: 'down', code: isApiClientError(e) ? e.code : 'unexpected' })
        },
      )
      .finally(() => clearTimeout(slow))
    return () => {
      cancelled = true
      clearTimeout(slow)
    }
  }, [api, attempt])

  const recheck = useCallback(() => {
    setHealth({ kind: 'checking' })
    setAttempt((n) => n + 1)
  }, [])

  return { health, recheck }
}
