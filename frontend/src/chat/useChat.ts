// Conversation state for one demo customer: session lifecycle, sending, retries the user
// asks for, transcript persistence (sessionStorage) and the live card snapshot.

import { useCallback, useEffect, useLayoutEffect, useReducer, useRef } from 'react'
import { isApiClientError, newClientMsgId, type ApiClient, type ClientErrorCode } from '../api/client'
import { uiTimings } from '../api/config'
import type {
  ChatButton,
  ChatResponse,
  ChatTurn,
  CustomerCaseView,
  DemoKey,
  DemoSwitchesPatch,
  DemoSwitchState,
  Language,
} from '../api/types'
import { isTransient, usePoller, type PollOutcome } from '../poll'
import {
  clearAll,
  isExpired,
  loadSession,
  loadTranscript,
  saveSession,
  saveTranscript,
  MAX_TRACE_RECORDS,
  type PendingSend,
  type TraceRecord,
  type StoredSession,
  type TranscriptEntry,
  type TurnSnapshot,
} from '../session'

export type { PendingSend }

export type ChatPhase = 'starting' | 'ready' | 'sending' | 'start_failed' | 'expired' | 'limit'

export interface ChatError {
  code: ClientErrorCode
  /** The same turn can be repeated (same client_msg_id). For 'conflict' it means "still processing". */
  canRetry: boolean
}

export interface ChatState {
  phase: ChatPhase
  session: StoredSession | null
  entries: TranscriptEntry[]
  usedButtonIds: string[]
  last: TurnSnapshot | null
  pending: PendingSend | null
  error: ChatError | null
  slow: boolean
  /** One record per answered turn, for the trace view (#/traza). */
  traces: TraceRecord[]
  /** Judge switches staged for the next message (only keys that differ from the server). */
  switchPatch: SwitchPatch
  /** A switches-only request ("Aplicar ahora") is in flight: the chat does not send meanwhile. */
  applyingSwitches: boolean
  /** Switch state the server echoed to a switches-only request made before the first turn
   *  (after the first turn it lives in last.demo_switches). */
  switchEcho: DemoSwitchState | null
  /** The server's confirmation of the last switches-only request, for the judge panel. */
  switchNote: { text: string; lang: Language } | null
}

/** The switches the judge can toggle; expire_session is a button, never staged. */
export type SwitchKey = 'tools_down' | 'model_slow' | 'fast_clock'
export type SwitchPatch = Partial<Pick<DemoSwitchesPatch, SwitchKey>>

const initialState: ChatState = {
  phase: 'starting',
  session: null,
  entries: [],
  usedButtonIds: [],
  last: null,
  pending: null,
  error: null,
  slow: false,
  traces: [],
  switchPatch: {},
  applyingSwitches: false,
  switchEcho: null,
  switchNote: null,
}

type Action =
  | { type: 'starting' }
  | {
      type: 'started'
      session: StoredSession
      entries: TranscriptEntry[]
      usedButtonIds: string[]
      last: TurnSnapshot | null
      pending?: PendingSend | null
      traces?: TraceRecord[]
    }
  | { type: 'start_failed'; code: ClientErrorCode }
  | { type: 'send'; pending: PendingSend; entry: TranscriptEntry; buttonId?: string }
  | { type: 'resend'; pending: PendingSend }
  | { type: 'slow' }
  | { type: 'received'; pending: PendingSend; entry: TranscriptEntry; snapshot: TurnSnapshot }
  | { type: 'send_failed'; pending: PendingSend; error: ChatError }
  | { type: 'expired' }
  | { type: 'limit'; pending: PendingSend }
  | { type: 'case_refreshed'; snapshot: TurnSnapshot }
  | { type: 'dismiss_error' }
  | { type: 'stage_switch'; key: SwitchKey; value: boolean }
  | { type: 'drop_switches' }
  | { type: 'switch_failed'; error: ChatError }
  | { type: 'applying_switches' }
  | { type: 'switches_applied'; sent: SwitchPatch; res: ChatResponse; record: TraceRecord }
  | { type: 'switches_not_applied'; error: ChatError; drop: boolean }

function setEntryStatus(entries: TranscriptEntry[], id: string, status: 'sent' | 'pending' | 'failed') {
  return entries.map((e) => (e.kind === 'customer' && e.id === id ? { ...e, status } : e))
}

/** The analyst-approved reply joins the conversation once per case (id r-<case_id>). */
export function withResolution(entries: TranscriptEntry[], card: CustomerCaseView | null | undefined): TranscriptEntry[] {
  const r = card?.resolution
  if (!card || !r || !r.text) return entries
  const id = `r-${card.case_id}`
  if (entries.some((e) => e.id === id)) return entries
  return [...entries, { kind: 'resolution', id, case_id: card.case_id, text: r.text, lang: r.language, at: r.sent_at }]
}

/**
 * The case clock's proactive notices (80 % of the SLA, automatic escalation) join the
 * conversation as system messages, once per case and kind (id n-<case_id>-<kind>): the server
 * sends them in the case card on every read, never as a chat turn. Oldest first, as sent.
 * Snapshots saved before the SLA timers existed have no `notices`.
 */
export function withNotices(entries: TranscriptEntry[], card: CustomerCaseView | null | undefined): TranscriptEntry[] {
  const notices = Array.isArray(card?.notices) ? card.notices : []
  if (!card || notices.length === 0) return entries
  let out = entries
  for (const n of notices) {
    if (!n || !n.text) continue
    const id = `n-${card.case_id}-${n.kind}`
    if (out.some((e) => e.id === id)) continue
    out = [...out, { kind: 'notice', id, case_id: card.case_id, notice: n.kind, text: n.text, lang: n.language, at: n.at }]
  }
  return out
}

/** Everything the case card brings into the conversation: clock notices, then the resolution. */
export function withCaseMessages(entries: TranscriptEntry[], card: CustomerCaseView | null | undefined): TranscriptEntry[] {
  return withResolution(withNotices(entries, card), card)
}

/** What the server last said about the switches (null before it said anything). */
export function currentSwitches(state: Pick<ChatState, 'last' | 'switchEcho'>): DemoSwitchState | null {
  return state.last?.demo_switches ?? state.switchEcho ?? null
}

function withoutSent(patch: SwitchPatch, sent: DemoSwitchesPatch | undefined): SwitchPatch {
  const out: SwitchPatch = {}
  for (const k of Object.keys(patch) as SwitchKey[]) {
    if (!sent || !(k in sent)) out[k] = patch[k]
  }
  return out
}

function reducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case 'starting':
      return { ...initialState }
    case 'started':
      return {
        ...initialState,
        phase: 'ready',
        session: action.session,
        entries: withCaseMessages(action.entries, action.last?.case_card),
        usedButtonIds: action.usedButtonIds,
        last: action.last,
        pending: action.pending ?? null,
        traces: action.traces ?? [],
      }
    case 'start_failed':
      return { ...initialState, phase: 'start_failed', error: { code: action.code, canRetry: true } }
    case 'send':
      return {
        ...state,
        phase: 'sending',
        pending: action.pending,
        error: null,
        slow: false,
        entries: [...state.entries, action.entry],
        usedButtonIds: action.buttonId ? [...state.usedButtonIds, action.buttonId] : state.usedButtonIds,
      }
    case 'resend':
      return {
        ...state,
        phase: 'sending',
        pending: action.pending,
        error: null,
        slow: false,
        entries: setEntryStatus(state.entries, action.pending.entryId, 'pending'),
      }
    case 'slow':
      return state.phase === 'sending' ? { ...state, slow: true } : state
    case 'received': {
      // Keys that travelled with this turn are now the server's; drop them from the patch.
      const switchPatch = withoutSent(state.switchPatch, action.pending.demo_switches)
      const snap = action.snapshot
      const record: TraceRecord = {
        turn: snap.turn,
        trace_id: snap.trace_id,
        at: action.entry.at,
        trace_summary: snap.trace_summary,
        degraded: snap.degraded,
        lane: snap.lane,
        client_latency_ms: snap.client_latency_ms,
      }
      const traces = [...state.traces.filter((t) => t.trace_id !== snap.trace_id), record].slice(-MAX_TRACE_RECORDS)
      return {
        ...state,
        phase: 'ready',
        pending: null,
        error: null,
        slow: false,
        entries: withCaseMessages(
          [...setEntryStatus(state.entries, action.pending.entryId, 'sent'), action.entry],
          snap.case_card,
        ),
        last: snap,
        traces,
        switchPatch,
        switchEcho: null,
      }
    }
    case 'send_failed': {
      // Not retryable means the server did not take the turn: a pressed button is free again.
      const releasedButton = !action.error.canRetry ? action.pending.button_id : undefined
      // A server without the switches (501) would fail every later turn: drop the staged ones.
      const dropSwitches = !action.error.canRetry && !!action.pending.demo_switches
      return {
        ...state,
        switchPatch: dropSwitches ? {} : state.switchPatch,
        phase: 'ready',
        slow: false,
        pending: action.error.canRetry ? action.pending : null,
        error: action.error,
        entries: setEntryStatus(state.entries, action.pending.entryId, 'failed'),
        usedButtonIds: releasedButton
          ? state.usedButtonIds.filter((id) => id !== releasedButton)
          : state.usedButtonIds,
      }
    }
    case 'expired':
      return { ...initialState, phase: 'expired' }
    case 'limit':
      return {
        ...state,
        phase: 'limit',
        slow: false,
        pending: null,
        error: { code: 'session_limit', canRetry: false },
        entries: setEntryStatus(state.entries, action.pending.entryId, 'failed'),
      }
    case 'case_refreshed':
      return { ...state, last: action.snapshot, entries: withCaseMessages(state.entries, action.snapshot.case_card) }
    case 'dismiss_error':
      // Only the notice goes away; a failed turn keeps its "Reintentar" on the bubble (pending).
      return { ...state, error: null }
    case 'stage_switch': {
      const server = currentSwitches(state)
      const switchPatch: SwitchPatch = { ...state.switchPatch }
      if ((server?.[action.key] ?? false) === action.value) delete switchPatch[action.key]
      else switchPatch[action.key] = action.value
      return { ...state, switchPatch }
    }
    case 'drop_switches':
      return { ...state, switchPatch: {} }
    case 'switch_failed':
      return { ...state, error: action.error }
    case 'applying_switches':
      return { ...state, applyingSwitches: true, switchNote: null, error: null }
    case 'switches_applied': {
      // A switches-only answer does not advance the conversation (same turn, no buttons): the
      // pending buttons stay valid, so no system entry is added; the card and the switch state
      // are the server's now, and its short confirmation goes to the judge panel.
      const res = action.res
      const last: TurnSnapshot | null = state.last
        ? {
            ...state.last,
            case_card: res.case_card ?? state.last.case_card,
            lane: res.lane ?? state.last.lane,
            poll_after_ms: res.poll_after_ms ?? null,
            demo_switches: res.demo_switches,
          }
        : null
      return {
        ...state,
        applyingSwitches: false,
        switchPatch: withoutSent(state.switchPatch, action.sent),
        switchEcho: res.demo_switches ?? null,
        switchNote: res.reply_text ? { text: res.reply_text, lang: res.reply_language } : null,
        last,
        entries: withCaseMessages(state.entries, last?.case_card),
        traces: [...state.traces.filter((t) => t.trace_id !== action.record.trace_id), action.record].slice(-MAX_TRACE_RECORDS),
      }
    }
    case 'switches_not_applied':
      return {
        ...state,
        applyingSwitches: false,
        error: action.error,
        switchPatch: action.drop ? {} : state.switchPatch,
      }
  }
}

function turnToEntry(turn: ChatTurn, degraded: ChatResponse['degraded'], idSuffix: string): TranscriptEntry {
  return {
    kind: 'system',
    id: `s-${turn.turn}-${idSuffix}`,
    turn: turn.turn,
    text: turn.reply_text,
    lang: turn.reply_language,
    source: turn.reply_source,
    at: new Date().toISOString(),
    buttons: turn.buttons ?? [],
    input_mode: turn.input_mode,
    degraded,
  }
}

function errorCode(e: unknown): ClientErrorCode {
  return isApiClientError(e) ? e.code : 'unexpected'
}

// 'busy' is retryable: the earlier request finishes or is aborted, and the same id can go again.
const NO_RETRY: ReadonlySet<ClientErrorCode> = new Set<ClientErrorCode>([
  'invalid_request',
  'not_authorized',
  'not_implemented',
  'not_found',
])

function canRetryError(e: unknown, code: ClientErrorCode): boolean {
  // 409 conflict: retryable when the server still processes this client_msg_id; a stale
  // button (retryable=false) is not.
  if (code === 'conflict') return isApiClientError(e) && e.retryable
  return !NO_RETRY.has(code)
}

function switchesFor(s: ChatState): { demo_switches?: DemoSwitchesPatch } {
  return Object.keys(s.switchPatch).length > 0 ? { demo_switches: { ...s.switchPatch } } : {}
}

function hasFailedEntry(entries: TranscriptEntry[], entryId: string): boolean {
  return entries.some((e) => e.kind === 'customer' && e.id === entryId && e.status === 'failed')
}

export interface UseChat {
  state: ChatState
  sendText: (text: string) => void
  pressButton: (button: ChatButton) => void
  retry: () => void
  restart: () => void
  expireLocally: () => void
  dismissError: () => void
  /** Stage a judge switch; it travels in demo_switches with the next message or button. */
  stageSwitch: (key: SwitchKey, value: boolean) => void
  /** Judge button: POST /chat with demo_switches {expire_session: true} and nothing else. */
  expireNow: () => void
  /** Judge button "Aplicar ahora": the staged switches go at once in a switches-only POST /chat
   *  (no message, no button), so a case already open gets them without writing anything. */
  applySwitchesNow: () => void
}

export function useChat(api: ApiClient, demoKey: DemoKey, language: Language): UseChat {
  const [state, dispatch] = useReducer(reducer, initialState)
  const stateRef = useRef(state)
  // Handlers read the latest committed state (no stale closures).
  useLayoutEffect(() => {
    stateRef.current = state
  })
  const startedFor = useRef<string | null>(null)
  const slowTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Results of async work (createSession, chat) are dropped once the view is gone or a newer
  // start superseded them, so a stale response never touches the token or the transcript.
  const mounted = useRef(false)
  const startGen = useRef(0)
  const inflight = useRef<AbortController | null>(null)
  const caseInFlight = useRef(false)

  const clearSlowTimer = () => {
    if (slowTimer.current) clearTimeout(slowTimer.current)
    slowTimer.current = null
  }

  const abortInflight = () => {
    inflight.current?.abort()
    inflight.current = null
  }

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      // Leaving the chat frees the client's single chat slot for the next screen. Deferred one
      // tick: StrictMode's simulated unmount remounts at once and must not cancel the turn.
      setTimeout(() => {
        if (mounted.current) return
        abortInflight()
        clearSlowTimer()
      }, 0)
    }
  }, [])

  const expire = useCallback(() => {
    clearAll()
    dispatch({ type: 'expired' })
  }, [])

  /** Re-reads the case. Returns what a poller should do next. */
  const refreshCase = useCallback(
    async (signal?: AbortSignal): Promise<PollOutcome> => {
      const snap = stateRef.current.last
      const caseId = snap?.case_card?.case_id
      if (!snap || !caseId || stateRef.current.phase === 'expired') return { kind: 'stop' }
      // One GET /cases in flight at a time (poller, reload and tab-visible can coincide).
      if (caseInFlight.current) return { kind: 'ok' }
      caseInFlight.current = true
      try {
        const res = await api.getCase(caseId, { signal, noRetry: true })
        if (!mounted.current) return { kind: 'stop' }
        const current = stateRef.current.last
        if (current && current.case_card?.case_id === res.case.case_id) {
          dispatch({
            type: 'case_refreshed',
            snapshot: {
              ...current,
              case_card: res.case,
              lane: res.case.lane ?? current.lane,
              poll_after_ms: res.poll_after_ms,
            },
          })
        }
        return { kind: 'ok', nextMs: res.poll_after_ms }
      } catch (e) {
        const code = errorCode(e)
        if (code === 'aborted') return { kind: 'stop' }
        if (code === 'session_expired') {
          expire()
          return { kind: 'stop' }
        }
        // 403 (or anything that will not change by waiting) stops; 429/5xx/network back off.
        // The last known card stays on screen either way.
        return isTransient(e) ? { kind: 'retry_later' } : { kind: 'stop' }
      } finally {
        caseInFlight.current = false
      }
    },
    [api, expire],
  )

  const run = useCallback(
    async (pending: PendingSend) => {
      clearSlowTimer()
      abortInflight()
      const controller = new AbortController()
      inflight.current = controller
      slowTimer.current = setTimeout(() => dispatch({ type: 'slow' }), uiTimings.chatSlowMs)
      const t0 = performance.now()
      try {
        const res = await api.chat(
          {
            client_msg_id: pending.client_msg_id,
            ...(pending.button_id !== undefined ? { button_id: pending.button_id } : { message: pending.message }),
            ...(pending.demo_switches ? { demo_switches: pending.demo_switches } : {}),
          },
          { signal: controller.signal },
        )
        if (controller.signal.aborted) return
        const clientMs = performance.now() - t0
        const entry = turnToEntry(res, res.degraded ?? [], res.trace_id || pending.client_msg_id)
        dispatch({
          type: 'received',
          pending,
          entry,
          snapshot: {
            progress: res.progress,
            case_card: res.case_card,
            lane: res.lane,
            degraded: res.degraded ?? [],
            trace_id: res.trace_id,
            trace_summary: res.trace_summary,
            client_latency_ms: clientMs,
            turn: res.turn,
            poll_after_ms: res.poll_after_ms ?? null,
            demo_switches: res.demo_switches,
          },
        })
      } catch (e) {
        const code = errorCode(e)
        // Aborted: the view left or restarted; nothing to show.
        if (code === 'aborted' || controller.signal.aborted) return
        if (code === 'session_expired') expire()
        else if (code === 'session_limit') dispatch({ type: 'limit', pending })
        else dispatch({ type: 'send_failed', pending, error: { code, canRetry: canRetryError(e, code) } })
      } finally {
        if (inflight.current === controller) {
          inflight.current = null
          clearSlowTimer()
        }
      }
    },
    [api, expire],
  )

  const start = useCallback(
    async (forceNew: boolean) => {
      const gen = ++startGen.current
      abortInflight()
      dispatch({ type: 'starting' })
      const existing = forceNew ? null : loadSession()
      if (existing && existing.demo_key === demoKey && !isExpired(existing)) {
        const t = loadTranscript(demoKey)
        let pending = t?.pending ?? null
        // A turn that was in flight when the page reloaded is repeated with the same
        // client_msg_id (the server answers it once); any other "Enviando…" left over from an
        // older transcript becomes "No se envió".
        const wasInFlight =
          !!pending &&
          (t?.entries ?? []).some((e) => e.kind === 'customer' && e.id === pending?.entryId && e.status === 'pending')
        const entries = (t?.entries ?? []).map((e) =>
          e.kind === 'customer' && e.status === 'pending' ? { ...e, status: 'failed' as const } : e,
        )
        if (pending && !hasFailedEntry(entries, pending.entryId)) pending = null
        dispatch({
          type: 'started',
          session: existing,
          entries,
          usedButtonIds: t?.used_button_ids ?? [],
          last: t?.last ?? null,
          pending,
          traces: Array.isArray(t?.traces) ? t.traces : [],
        })
        if (wasInFlight && pending) {
          dispatch({ type: 'resend', pending })
          void run(pending)
        }
        return
      }
      clearAll()
      try {
        const res = await api.createSession({ demo_key: demoKey, channel: 'web', language })
        if (!mounted.current || gen !== startGen.current) return
        const session: StoredSession = {
          token: res.session_token,
          expires_at: res.expires_at,
          demo_key: demoKey,
          customer: res.customer,
        }
        saveSession(session)
        const entries = res.welcome ? [turnToEntry(res.welcome, [], 'welcome')] : []
        dispatch({ type: 'started', session, entries, usedButtonIds: [], last: null })
      } catch (e) {
        if (!mounted.current || gen !== startGen.current) return
        dispatch({ type: 'start_failed', code: errorCode(e) })
      }
    },
    [api, demoKey, language, run],
  )

  // Open (or restore) the session once per demo key, also under StrictMode double effects.
  useEffect(() => {
    if (startedFor.current === demoKey) return
    startedFor.current = demoKey
    void start(false)
  }, [demoKey, start])

  // After a reload, re-read the case once (section 8: "al recargar").
  const restoredOnce = useRef(false)
  useEffect(() => {
    if (state.phase === 'ready' && !restoredOnce.current) {
      restoredOnce.current = true
      if (state.last?.case_card) void refreshCase()
    }
  }, [state.phase, state.last, refreshCase])

  // RF-20: while a lane B case waits (poll_after_ms > 0), re-read it at the server's pace, with
  // jitter, only while the tab is visible, one request at a time; 401/403 or a null pace stop it.
  const card = state.last?.case_card ?? null
  const pollMs = state.last?.poll_after_ms ?? null
  usePoller({
    enabled: (state.phase === 'ready' || state.phase === 'sending') && !!card && card.lane === 'B' && !!pollMs,
    intervalMs: pollMs,
    resetKey: card?.case_id,
    run: (signal) => refreshCase(signal),
  })

  // Re-read the case when the tab becomes visible again.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === 'visible') void refreshCase()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [refreshCase])

  // Persist the transcript for this tab, with the turn in flight so a reload can repeat it.
  useEffect(() => {
    if (!state.session || state.phase === 'expired' || state.phase === 'starting') return
    saveTranscript({
      demo_key: demoKey,
      entries: state.entries,
      used_button_ids: state.usedButtonIds,
      last: state.last,
      pending: state.pending,
      traces: state.traces,
    })
  }, [demoKey, state.session, state.phase, state.entries, state.usedButtonIds, state.last, state.pending, state.traces])

  const sendText = useCallback(
    (text: string) => {
      const s = stateRef.current
      const message = text.trim()
      if (s.phase !== 'ready' || s.applyingSwitches || !message || message.length > uiTimings.maxMessageChars) return
      const id = newClientMsgId()
      const pending: PendingSend = { client_msg_id: id, message, entryId: `c-${id}`, ...switchesFor(s) }
      dispatch({
        type: 'send',
        pending,
        entry: { kind: 'customer', id: pending.entryId, text: message, at: new Date().toISOString(), via_button: false, status: 'pending' },
      })
      void run(pending)
    },
    [run],
  )

  const pressButton = useCallback(
    (button: ChatButton) => {
      const s = stateRef.current
      if (s.phase !== 'ready' || s.applyingSwitches || s.usedButtonIds.includes(button.id)) return
      const id = newClientMsgId()
      const pending: PendingSend = { client_msg_id: id, button_id: button.id, entryId: `c-${id}`, ...switchesFor(s) }
      dispatch({
        type: 'send',
        pending,
        buttonId: button.id,
        entry: { kind: 'customer', id: pending.entryId, text: button.label, at: new Date().toISOString(), via_button: true, status: 'pending' },
      })
      void run(pending)
    },
    [run],
  )

  // Retry depends on the failed turn (pending), not on the notice: closing the notice keeps it.
  const retry = useCallback(() => {
    const s = stateRef.current
    if (s.phase === 'start_failed') {
      void start(true)
      return
    }
    if (s.phase !== 'ready' || !s.pending || !hasFailedEntry(s.entries, s.pending.entryId)) return
    const pending = s.pending
    dispatch({ type: 'resend', pending })
    void run(pending)
  }, [run, start])

  const restart = useCallback(() => {
    clearAll()
    restoredOnce.current = true
    void start(true)
  }, [start])

  const dismissError = useCallback(() => dispatch({ type: 'dismiss_error' }), [])

  const stageSwitch = useCallback((key: SwitchKey, value: boolean) => dispatch({ type: 'stage_switch', key, value }), [])

  const expireNow = useCallback(async () => {
    const s = stateRef.current
    if (s.phase !== 'ready' || api.isChatInFlight()) return
    try {
      await api.chat({ client_msg_id: newClientMsgId(), demo_switches: { expire_session: true } })
      // A 200 means the server ignored it; the session clock still ends it at expires_at.
      if (mounted.current) dispatch({ type: 'switch_failed', error: { code: 'unexpected', canRetry: false } })
    } catch (e) {
      if (!mounted.current) return
      const code = errorCode(e)
      if (code === 'session_expired') expire()
      else if (code !== 'aborted') dispatch({ type: 'switch_failed', error: { code, canRetry: false } })
    }
  }, [api, expire])

  const applySwitchesNow = useCallback(async () => {
    const s = stateRef.current
    const sent = { ...s.switchPatch }
    if (s.phase !== 'ready' || s.applyingSwitches || Object.keys(sent).length === 0 || api.isChatInFlight()) return
    dispatch({ type: 'applying_switches' })
    const t0 = performance.now()
    try {
      const res = await api.chat({ client_msg_id: newClientMsgId(), demo_switches: sent })
      if (!mounted.current) return
      const record: TraceRecord = {
        turn: res.turn,
        trace_id: res.trace_id,
        at: new Date().toISOString(),
        trace_summary: res.trace_summary,
        degraded: res.degraded ?? [],
        lane: res.lane,
        client_latency_ms: performance.now() - t0,
      }
      dispatch({ type: 'switches_applied', sent, res, record })
    } catch (e) {
      if (!mounted.current) return
      const code = errorCode(e)
      if (code === 'session_expired') {
        expire()
        return
      }
      // Not retryable (a server without this switch answers 400/501): drop the staged keys, or
      // every later message would carry them and fail too. Otherwise they stay staged.
      const drop = code !== 'aborted' && !canRetryError(e, code)
      dispatch({ type: 'switches_not_applied', error: { code, canRetry: false }, drop })
    }
  }, [api, expire])

  return {
    state,
    sendText,
    pressButton,
    retry,
    restart,
    expireLocally: expire,
    dismissError,
    stageSwitch,
    expireNow: () => void expireNow(),
    applySwitchesNow: () => void applySwitchesNow(),
  }
}
