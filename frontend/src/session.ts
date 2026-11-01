// Customer session: token in memory plus sessionStorage (per tab, survives a reload).
// Never in the URL, never in persistent browser storage, never in logs.
// The transcript lives in sessionStorage too (there is no message store in M1) and is
// wiped together with the token on session_expired or "Volver a empezar".

import type {
  ChatButton,
  CustomerCaseView,
  DemoKey,
  DemoSwitchesPatch,
  DemoSwitchState,
  Degradation,
  Language,
  Lane,
  NoticeKind,
  Progress,
  ReplySource,
  SessionCustomer,
  TraceSummary,
} from './api/types'

const SESSION_KEY = 'ev.session.v1'
const TRANSCRIPT_KEY = 'ev.transcript.v1'

export interface StoredSession {
  token: string
  expires_at: string
  demo_key: DemoKey
  customer: SessionCustomer
}

export type TranscriptEntry =
  | {
      kind: 'customer'
      id: string
      text: string
      at: string
      /** Label of the pressed button, when the turn was a button. */
      via_button: boolean
      status: 'sent' | 'pending' | 'failed'
    }
  | {
      kind: 'system'
      id: string
      turn: number
      text: string
      lang: Language
      source: ReplySource
      at: string
      buttons: ChatButton[]
      input_mode: 'free_text' | 'buttons_only'
      degraded: Degradation[]
    }
  | {
      /** The reply an analyst approved (GET /cases/{id} resolution), added once per case. */
      kind: 'resolution'
      id: string
      case_id: string
      text: string
      lang: Language
      at: string
    }
  | {
      /** A proactive message of the case clock (CustomerCaseView.notices: 80 % of the SLA,
       *  automatic escalation), added once per case and kind (id n-<case_id>-<kind>). */
      kind: 'notice'
      id: string
      case_id: string
      notice: NoticeKind
      text: string
      lang: Language
      at: string
    }

export interface TurnSnapshot {
  progress: Progress
  case_card: CustomerCaseView | null
  lane: Lane | null
  degraded: Degradation[]
  trace_id: string
  trace_summary: TraceSummary
  client_latency_ms: number
  turn: number
  /** Absent in snapshots saved before the lifecycle existed. */
  poll_after_ms?: number | null
  demo_switches?: DemoSwitchState
}

/** One answered turn as the trace view (#/traza) lists it. No free text: only trace_summary. */
export interface TraceRecord {
  turn: number
  trace_id: string
  at: string
  trace_summary: TraceSummary
  degraded: Degradation[]
  lane: Lane | null
  client_latency_ms: number
}

/** Kept per tab: at most one record per answered turn (30-turn cap + switches-only turns). */
export const MAX_TRACE_RECORDS = 40

/** A turn sent (or being sent) whose answer has not arrived. Kept so a reload can repeat
 *  it with the same client_msg_id (idempotent on the server). */
export interface PendingSend {
  client_msg_id: string
  message?: string
  button_id?: string
  /** Judge switches that travel with this turn (same body when it is repeated). */
  demo_switches?: DemoSwitchesPatch
  entryId: string
}

export interface StoredTranscript {
  demo_key: DemoKey
  entries: TranscriptEntry[]
  used_button_ids: string[]
  last: TurnSnapshot | null
  /** Absent in transcripts saved before this field existed. */
  pending?: PendingSend | null
  /** Absent in transcripts saved before the trace view existed. */
  traces?: TraceRecord[]
}

let memoryToken: string | null = null
let memorySession: StoredSession | null = null

function readJson<T>(key: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Storage full or blocked: the session still works in memory for this page view.
  }
}

function remove(key: string): void {
  try {
    window.sessionStorage.removeItem(key)
  } catch {
    // ignore
  }
}

export function saveSession(s: StoredSession): void {
  memoryToken = s.token
  memorySession = s
  writeJson(SESSION_KEY, s)
}

export function loadSession(): StoredSession | null {
  if (memorySession) return memorySession
  const s = readJson<StoredSession>(SESSION_KEY)
  if (s && typeof s.token === 'string' && typeof s.expires_at === 'string') {
    memorySession = s
    memoryToken = s.token
    return s
  }
  return null
}

export function getToken(): string | null {
  if (memoryToken) return memoryToken
  return loadSession()?.token ?? null
}

export function isExpired(s: Pick<StoredSession, 'expires_at'>, now: number = Date.now()): boolean {
  const t = Date.parse(s.expires_at)
  return Number.isNaN(t) || t <= now
}

export function saveTranscript(t: StoredTranscript): void {
  writeJson(TRANSCRIPT_KEY, t)
}

/** The transcript of this tab, whatever its customer (the trace view reads it). */
export function loadAnyTranscript(): StoredTranscript | null {
  const t = readJson<StoredTranscript>(TRANSCRIPT_KEY)
  if (!t || !Array.isArray(t.entries)) return null
  return t
}

export function loadTranscript(demoKey: DemoKey): StoredTranscript | null {
  const t = readJson<StoredTranscript>(TRANSCRIPT_KEY)
  if (!t || t.demo_key !== demoKey || !Array.isArray(t.entries)) return null
  return t
}

/** Wipes token, transcript and case from memory and sessionStorage. */
export function clearAll(): void {
  memoryToken = null
  memorySession = null
  remove(SESSION_KEY)
  remove(TRANSCRIPT_KEY)
}
