// Data of the console: the queue (polled at poll_after_ms, 15 s) and the selected case.
// Errors branch like the chat: 401 -> sign in again (drafts stay in memory), 403 -> neutral
// text that never says whether the case exists, 429/5xx -> keep the last data marked
// "desactualizado hh:mm" and back off.

import { useCallback, useEffect, useRef, useState } from 'react'
import { isApiClientError, type ApiClient, type ClientErrorCode } from '../api/client'
import type { AnalystCaseDetail, AnalystCaseListItem } from '../api/types'
import { isTransient, usePoller, type PollOutcome } from '../poll'
import { newSlaAlerts, type NewAlert } from './slaAlerts'

export const QUEUE_POLL_DEFAULT_MS = 15_000

function codeOf(e: unknown): ClientErrorCode {
  return isApiClientError(e) ? e.code : 'unexpected'
}

export interface QueueState {
  items: AnalystCaseListItem[]
  nextCursor: string | null
  /** Server time of the last listing that arrived. */
  asOf: string | null
  loadedOnce: boolean
  error: ClientErrorCode | null
  loadingMore: boolean
  /** SLA alerts that fired since the previous listing (announced once); kept until newer ones. */
  newAlerts: NewAlert[]
}

export function useQueue(api: ApiClient, opts: { paused: boolean; onUnauthorized: () => void }) {
  const [state, setState] = useState<QueueState>({
    items: [],
    nextCursor: null,
    asOf: null,
    loadedOnce: false,
    error: null,
    loadingMore: false,
    newAlerts: [],
  })
  const [pace, setPace] = useState(QUEUE_POLL_DEFAULT_MS)
  const inFlight = useRef(false)
  const onUnauthorized = useRef(opts.onUnauthorized)
  useEffect(() => {
    onUnauthorized.current = opts.onUnauthorized
  })

  const load = useCallback(
    async (signal?: AbortSignal): Promise<PollOutcome> => {
      if (inFlight.current) return { kind: 'ok' }
      inFlight.current = true
      try {
        const res = await api.listAnalystCases({}, { signal, noRetry: true })
        setState((s) => {
          const news = newSlaAlerts(s.items, res.items, !s.loadedOnce)
          return {
          ...s,
          newAlerts: news.length > 0 ? news : s.newAlerts,
          items: res.items,
          nextCursor: res.next_cursor,
          asOf: res.as_of,
          loadedOnce: true,
          error: null,
          }
        })
        if (res.poll_after_ms > 0) setPace(res.poll_after_ms)
        return { kind: 'ok', nextMs: res.poll_after_ms > 0 ? res.poll_after_ms : undefined }
      } catch (e) {
        const code = codeOf(e)
        if (code === 'aborted') return { kind: 'stop' }
        if (code === 'session_expired') {
          onUnauthorized.current()
          return { kind: 'stop' }
        }
        setState((s) => ({ ...s, error: code, loadedOnce: true }))
        return isTransient(e) ? { kind: 'retry_later' } : { kind: 'stop' }
      } finally {
        inFlight.current = false
      }
    },
    [api],
  )

  usePoller({ enabled: !opts.paused, intervalMs: pace, immediate: true, run: load })

  const loadMore = useCallback(async () => {
    const cursor = state.nextCursor
    if (!cursor || state.loadingMore) return
    setState((s) => ({ ...s, loadingMore: true }))
    try {
      const res = await api.listAnalystCases({ cursor }, { noRetry: true })
      setState((s) => {
        const seen = new Set(s.items.map((i) => i.case_id))
        return {
          ...s,
          items: [...s.items, ...res.items.filter((i) => !seen.has(i.case_id))],
          nextCursor: res.next_cursor,
          loadingMore: false,
        }
      })
    } catch (e) {
      if (codeOf(e) === 'session_expired') onUnauthorized.current()
      setState((s) => ({ ...s, loadingMore: false, error: codeOf(e) }))
    }
  }, [api, state.nextCursor, state.loadingMore])

  return { queue: state, refresh: () => void load(), loadMore }
}

export interface DetailState {
  detail: AnalystCaseDetail | null
  error: ClientErrorCode | null
  /** When the detail on screen was fetched (for "desactualizado hh:mm"). */
  fetchedAt: number | null
}

const EMPTY_DETAIL: DetailState = { detail: null, error: null, fetchedAt: null }

export function useCaseDetail(
  api: ApiClient,
  caseId: string | null,
  opts: { paused: boolean; onUnauthorized: () => void },
) {
  // State is per case: switching cases starts empty (adjusting state during render, no effect).
  const [state, setState] = useState<DetailState & { caseId: string | null }>({ ...EMPTY_DETAIL, caseId })
  if (state.caseId !== caseId) setState({ ...EMPTY_DETAIL, caseId })
  const onUnauthorized = useRef(opts.onUnauthorized)
  const currentCase = useRef(caseId)
  useEffect(() => {
    onUnauthorized.current = opts.onUnauthorized
    currentCase.current = caseId
  })
  const gen = useRef(0)

  const reload = useCallback(async (): Promise<AnalystCaseDetail | null> => {
    if (!caseId) return null
    const mine = ++gen.current
    const stale = () => mine !== gen.current || currentCase.current !== caseId
    try {
      const detail = await api.getAnalystCase(caseId)
      if (stale()) return null
      setState({ detail, error: null, fetchedAt: Date.now(), caseId })
      return detail
    } catch (e) {
      if (stale()) return null
      const code = codeOf(e)
      if (code === 'session_expired') onUnauthorized.current()
      setState((s) => ({
        // 403 and 404 never keep a case on screen; transient errors keep it, marked stale.
        detail: isTransient(e) || code === 'session_expired' ? s.detail : null,
        error: code,
        fetchedAt: s.fetchedAt,
        caseId,
      }))
      return null
    }
  }, [api, caseId])

  // Fetch on open and after signing in again.
  const reloadRef = useRef(reload)
  useEffect(() => {
    reloadRef.current = reload
  })
  useEffect(() => {
    if (!caseId || opts.paused) return
    void reloadRef.current()
  }, [caseId, opts.paused])

  const current = state.caseId === caseId ? state : { ...EMPTY_DETAIL, caseId }
  return { detail: current.detail, error: current.error, fetchedAt: current.fetchedAt, loading: !current.detail && !current.error, reload }
}
