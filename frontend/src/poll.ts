// Polling shared by the customer card (GET /cases/{id}, 5 s) and the console queue
// (GET /analyst/cases, 15 s). Section 8 of interfaz_chat_requerimientos.md:
// - only while the tab is visible; a hidden tab stops, and becoming visible polls at once;
// - one request in flight at a time;
// - +/- jitter on every interval (20 % by default);
// - backoff 5 -> 10 -> 20 -> 60 s after 429/5xx/network errors;
// - the caller stops it (401/403, or the server says there is nothing left to wait for).

import { useEffect, useLayoutEffect, useRef } from 'react'
import { isApiClientError } from './api/client'

export type PollOutcome =
  /** Success. nextMs: the server's new pace (poll_after_ms); null stops the poller. */
  | { kind: 'ok'; nextMs?: number | null }
  /** Transient failure: wait the next backoff step. */
  | { kind: 'retry_later' }
  /** Stop for good (401, 403, nothing to wait for). */
  | { kind: 'stop' }

export const POLL_BACKOFF_MS: readonly number[] = [5_000, 10_000, 20_000, 60_000]

export interface PollerOptions {
  enabled: boolean
  /** First interval; null or <= 0 disables the poller. */
  intervalMs: number | null
  run: (signal: AbortSignal) => Promise<PollOutcome>
  /** Changing it restarts the loop (e.g. another case id). */
  resetKey?: string
  jitter?: number
  backoffMs?: readonly number[]
  /** Poll once right away instead of after the first interval. */
  immediate?: boolean
  random?: () => number
}

/** Errors a poller should back off from rather than stop on. */
export function isTransient(e: unknown): boolean {
  if (!isApiClientError(e)) return true
  if (e.code === 'network' || e.code === 'timeout' || e.code === 'rate_limited') return true
  return e.status !== null && e.status >= 500 && e.status !== 501
}

export function withJitter(ms: number, jitter: number, random: () => number): number {
  return Math.max(0, Math.round(ms * (1 - jitter + 2 * jitter * random())))
}

export function usePoller(opts: PollerOptions): void {
  const runRef = useRef(opts.run)
  useLayoutEffect(() => {
    runRef.current = opts.run
  })
  const { enabled, intervalMs, resetKey, immediate = false } = opts
  const jitter = opts.jitter ?? 0.2
  const backoff = opts.backoffMs ?? POLL_BACKOFF_MS
  const random = opts.random ?? Math.random

  useEffect(() => {
    if (!enabled || !intervalMs || intervalMs <= 0) return
    let stopped = false
    let inFlight = false
    let waitingForVisible = false
    let failures = 0
    let pace = intervalMs
    let timer: ReturnType<typeof setTimeout> | null = null
    let controller: AbortController | null = null

    const schedule = (ms: number) => {
      if (timer) clearTimeout(timer)
      if (!stopped) timer = setTimeout(tick, ms)
    }

    async function tick() {
      timer = null
      if (stopped || inFlight) return
      if (document.visibilityState === 'hidden') {
        waitingForVisible = true
        return
      }
      inFlight = true
      controller = new AbortController()
      let out: PollOutcome
      try {
        out = await runRef.current(controller.signal)
      } catch {
        out = { kind: 'retry_later' }
      }
      inFlight = false
      if (stopped) return
      if (out.kind === 'stop') {
        stopped = true
        return
      }
      if (out.kind === 'retry_later') {
        const delay = backoff[Math.min(failures, backoff.length - 1)]
        failures++
        schedule(withJitter(delay, jitter, random))
        return
      }
      failures = 0
      if (out.nextMs === null) {
        stopped = true
        return
      }
      if (typeof out.nextMs === 'number' && out.nextMs > 0) pace = out.nextMs
      schedule(withJitter(pace, jitter, random))
    }

    const onVisibility = () => {
      if (document.visibilityState === 'visible' && waitingForVisible && !stopped) {
        waitingForVisible = false
        void tick()
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    schedule(immediate ? 0 : withJitter(intervalMs, jitter, random))
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
      controller?.abort()
      document.removeEventListener('visibilitychange', onVisibility)
    }
    // backoff/random are configuration; they do not restart the loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, resetKey, immediate, jitter])
}
