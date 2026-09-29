// Decision drafts of the console, per case, in MEMORY ONLY (never in any browser storage).
// They survive switching cases and a 401 (sign in again) because they live outside the React
// tree; a page reload drops them on purpose (the reply text can quote the customer).

import type { DecisionNext, IntentClass } from '../api/types'

export type IntentLabel = { kind: 'confirmed' } | { kind: 'corrected'; intentClass: IntentClass } | null

export interface DecisionDraft {
  mode: 'none' | 'approve' | 'edit' | 'reject'
  editText: string | null // null until the analyst opens Edit (then it starts from the draft)
  editReason: string
  rejectReason: string
  rejectNext: DecisionNext | null
  rejectReply: string
  evidenceReviewed: boolean
  intentLabel: IntentLabel
}

export const emptyDraft = (): DecisionDraft => ({
  mode: 'none',
  editText: null,
  editReason: '',
  rejectReason: '',
  rejectNext: null,
  rejectReply: '',
  evidenceReviewed: false,
  intentLabel: null,
})

const drafts = new Map<string, DecisionDraft>()

export function getDraft(caseId: string): DecisionDraft {
  return drafts.get(caseId) ?? emptyDraft()
}

export function setDraft(caseId: string, draft: DecisionDraft): void {
  drafts.set(caseId, draft)
}

export function dropDraft(caseId: string): void {
  drafts.delete(caseId)
}

export function clearDrafts(): void {
  drafts.clear()
}
