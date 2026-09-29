// Scale of the judge's demo clock (fast_clock), as the server reports it in GET /health
// (demo_clock_scale: 1/1440 by default, `local_api.py --sla-scale` changes it; null where the
// server has no demo clock). The judge panel and the console's Historial say the real scale,
// or a neutral text when the server does not report one (older server, cloud, health down).

import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'

// promise_times.yaml (default): the three SLA timers, counted from the case creation.
export const DAY_S = 86_400
export const DEMO_TIMERS_S = {
  unassigned: 24 * 3600, // assign_hours: 24
  sla_80: 15 * 0.8 * DAY_S, // sla_days: 15, sla_alert_fraction: 0.8
  breached: 15 * DAY_S, // sla_days: 15
} as const

const cache = new WeakMap<ApiClient, Promise<number | null>>()

function valid(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) && v > 0 && v <= 1 ? v : null
}

/** One GET /health per client; a failed call is not cached (the next view asks again). */
export function demoClockScale(api: ApiClient): Promise<number | null> {
  let p = cache.get(api)
  if (!p) {
    p = Promise.resolve()
      .then(() => api.health())
      .then(
        (h) => valid((h as { demo_clock_scale?: unknown } | null | undefined)?.demo_clock_scale),
        () => {
          cache.delete(api)
          return null
        },
      )
    cache.set(api, p)
  }
  return p
}

/** The server's demo clock scale; null while unknown or when the server has none. */
export function useDemoClockScale(api: ApiClient): number | null {
  const [scale, setScale] = useState<number | null>(null)
  useEffect(() => {
    let live = true
    void demoClockScale(api).then((s) => {
      if (live) setScale(s)
    })
    return () => {
      live = false
    }
  }, [api])
  return scale
}

/** A simulated span in the largest unit that fits, at most one decimal: "1 minuto", "0,9 segundos". */
export function formatSpan(seconds: number, locale: string): string {
  const [value, unit] = seconds >= 3600 ? [seconds / 3600, 'hour'] : seconds >= 60 ? [seconds / 60, 'minute'] : [seconds, 'second']
  try {
    return new Intl.NumberFormat(locale, { style: 'unit', unit, unitDisplay: 'long', maximumFractionDigits: 1 }).format(value)
  } catch {
    return `${Math.round(value * 10) / 10} ${unit}`
  }
}

/** How many times faster than real time ("1440", "100.000"). */
export function formatFactor(scale: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(Math.round(1 / scale))
}
