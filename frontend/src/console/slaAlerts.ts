// SLA timers of a lane B case as the console shows them (plan v2 sections 6 and 12). LOCAL
// simulation behind the lifecycle protocol (src/conversation/lifecycle.py); in the cloud the
// same three timers are EventBridge Scheduler one-time schedules per case.
// - unassigned: clock.assigned_by (+24 h) and no analyst took the case -> alert in the queue
// - sla_80: clock.sla_alert_at (80 % of the SLA) -> proactive notice to the customer
// - breached: clock.breach_at -> escalation to the senior queue, priority high, notice

import type { AnalystCaseListItem, AnalystCaseView, CaseClock, ClockEvent, SlaTimerKind } from '../api/types'

export const SLA_TIMER_KINDS: readonly SlaTimerKind[] = ['unassigned', 'sla_80', 'breached']

/** The alerts of a queue item, in the contract's order; [] for servers before the timers. */
export function alertsOf(item: Pick<AnalystCaseListItem, 'sla_alerts'>): SlaTimerKind[] {
  const raw = Array.isArray(item.sla_alerts) ? item.sla_alerts : []
  return SLA_TIMER_KINDS.filter((k) => raw.includes(k))
}

export interface NewAlert {
  case_id: string
  display_name: string
  kind: SlaTimerKind
}

/**
 * Alerts that fired between two listings (for the console's live region). The first listing
 * announces nothing: what was already there when the analyst arrived is not news, and a poll
 * that brings the same alerts again announces nothing either.
 */
export function newSlaAlerts(prev: AnalystCaseListItem[], next: AnalystCaseListItem[], firstLoad: boolean): NewAlert[] {
  if (firstLoad) return []
  const before = new Map(prev.map((i) => [i.case_id, new Set(alertsOf(i))]))
  const out: NewAlert[] = []
  for (const item of next) {
    const had = before.get(item.case_id) ?? new Set<SlaTimerKind>()
    for (const kind of alertsOf(item)) {
      if (!had.has(kind)) out.push({ case_id: item.case_id, display_name: item.display_name, kind })
    }
  }
  return out
}

/** Where the planned time of each timer lives in the case clock. */
const PLANNED: Record<SlaTimerKind, keyof CaseClock> = {
  unassigned: 'assigned_by',
  sla_80: 'sla_alert_at',
  breached: 'breach_at',
}

export type TimerState = 'fired' | 'scheduled' | 'cancelled' | 'not_fired'

export interface TimelineRow {
  kind: SlaTimerKind
  planned: string | null
  firedAt: string | null
  state: TimerState
}

/**
 * The clock of a case, one row per timer: when it was planned (real time; the judge's fast
 * clock fires earlier and never moves these dates) and what happened to it.
 * - fired: it is in clock_events.
 * - scheduled: still pending (the case waits for an answer and nobody decided).
 * - cancelled: "unassigned" once an analyst took the case (opening it here counts), or any
 *   timer after a decision, which deletes the pending schedules.
 * - not_fired: the case left the waiting states without a decision (lane C, closed).
 */
export function clockTimeline(c: Pick<AnalystCaseView, 'clock' | 'clock_events' | 'status' | 'analyst_decision'>): TimelineRow[] {
  const events: ClockEvent[] = Array.isArray(c.clock_events) ? c.clock_events : []
  const waiting = c.status === 'open' || c.status === 'investigating' || c.status === 'awaiting_analyst'
  const decided = !!c.analyst_decision
  return SLA_TIMER_KINDS.map((kind) => {
    const ev = events.find((e) => e.kind === kind)
    const planned = (c.clock?.[PLANNED[kind]] as string | null | undefined) ?? null
    let state: TimerState
    if (ev) state = 'fired'
    else if (decided) state = 'cancelled'
    // The detail on screen was read by an analyst, which takes the case: no unassigned alert.
    else if (kind === 'unassigned') state = waiting ? 'cancelled' : 'not_fired'
    else state = waiting ? 'scheduled' : 'not_fired'
    return { kind, planned, firedAt: ev?.fired_at ?? null, state }
  })
}

/** The timers that fired, for the case header (unassigned drops out once taken, as in the queue). */
export function headerAlerts(c: Pick<AnalystCaseView, 'clock_events'>): SlaTimerKind[] {
  const fired = new Set((Array.isArray(c.clock_events) ? c.clock_events : []).map((e) => e.kind))
  return SLA_TIMER_KINDS.filter((k) => k !== 'unassigned' && fired.has(k))
}
