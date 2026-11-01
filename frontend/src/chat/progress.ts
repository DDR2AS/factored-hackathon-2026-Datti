import type { CaseStatus, CustomerCaseView, LifecycleStep, Progress, ProgressStep } from '../api/types'

export const STEPS: readonly Exclude<ProgressStep, 'done'>[] = ['understand', 'find', 'verify', 'case_open']

/** Index of the current step (0..3); 4 once the case is open or the conversation is done. */
export function stepIndex(progress: Progress | null | undefined): number {
  if (!progress) return 0
  if (progress.step === 'done' || progress.step === 'case_open') return STEPS.length
  const i = STEPS.indexOf(progress.step)
  return i < 0 ? 0 : i
}

// Customer-facing steps after the case opens, from customer_view.lifecycle_step (RF-20).
export const LIFECYCLE: readonly Exclude<LifecycleStep, 'open' | 'closed'>[] = ['investigating', 'in_review', 'notified']

/** Index into LIFECYCLE; -1 before investigating, LIFECYCLE.length once closed. Older payloads
 *  without lifecycle_step fall back to the status. */
export function lifecycleIndex(step: LifecycleStep | null | undefined, status: CaseStatus): number {
  const s: LifecycleStep | null =
    step ??
    (status === 'investigating'
      ? 'investigating'
      : status === 'awaiting_analyst'
        ? 'in_review'
        : status === 'notified'
          ? 'notified'
          : status === 'closed'
            ? 'closed'
            : status === 'open' || status === 'reopened'
              ? 'open'
              : null)
  if (s === null || s === 'open') return -1
  if (s === 'closed') return LIFECYCLE.length
  return LIFECYCLE.indexOf(s)
}

// State of the promise on the customer's card (lane B), from the SLA timers' notices: on time
// until 80 % of the internal SLA elapses; then the 80 % notice; after the breach, escalated to
// the senior queue. The promised date itself never changes.
export type PromiseState = 'on_time' | 'sla_80' | 'escalated'

export function promiseState(card: Pick<CustomerCaseView, 'lane' | 'notices'> | null | undefined): PromiseState | null {
  if (!card || card.lane !== 'B') return null
  const kinds = new Set((Array.isArray(card.notices) ? card.notices : []).map((n) => n.kind))
  if (kinds.has('escalated')) return 'escalated'
  if (kinds.has('sla_80')) return 'sla_80'
  return 'on_time'
}

/** The SLA timers only run while the case waits for an answer (open, investigating, in review). */
export function caseIsWaiting(status: CaseStatus): boolean {
  return status === 'open' || status === 'investigating' || status === 'awaiting_analyst' || status === 'reopened'
}
