// Pure helpers of the analyst console: report reliability, the DecisionRequest the console
// sends, the edit diff, an orientative refund-promise check and text neutralization.
// Rules come from docs/contrato_consola.md; the server enforces all of them again.

import { newClientMsgId } from '../api/client'
import type {
  CaseIntent,
  DecisionAction,
  DecisionLabels,
  DecisionRequest,
  InvestigatorReport,
  IntentClass,
  Language,
} from '../api/types'
import type { MessageKey } from '../i18n/es'
import type { DecisionDraft, IntentLabel } from './drafts'

export const INTENT_CLASSES: readonly IntentClass[] = [
  'dispute_charge',
  'dispute_fee',
  'complaint_other',
  'case_status',
  'account_query',
  'lost_card',
  'human_request',
  'out_of_scope',
  'manipulation',
]

export const MAX_REPLY = 2000
export const MAX_REASON = 500

/** Same rule as contract.report_reliable: citations valid and at most one claim removed. */
export function isReportReliable(report: Pick<InvestigatorReport, 'citations_valid' | 'removed_claims'>): boolean {
  return report.citations_valid && report.removed_claims <= 1
}

/** The deterministic stub (G2 is Andrés's) or a report without a model: not GenAI. */
export function isProvisionalInvestigator(report: Pick<InvestigatorReport, 'model_id' | 'prompt_version'>): boolean {
  const id = (report.model_id as string | null) ?? ''
  return !id || id.startsWith('stub-') || report.prompt_version === 'stub-provisional'
}

export function labelsFor(intentLabel: IntentLabel, intent: CaseIntent | null): DecisionLabels | undefined {
  if (!intentLabel || !intent) return undefined
  if (intentLabel.kind === 'confirmed') return { intent_confirmed: true }
  // Correcting to the predicted class is a confirmation (the server rejects both keys together).
  if (intentLabel.intentClass === intent.class) return { intent_confirmed: true }
  return { intent_class: intentLabel.intentClass }
}

export interface BuildArgs {
  action: DecisionAction
  clientDecisionId: string
  version: number
  draft: DecisionDraft
  caseLanguage: Language
  reliable: boolean
  intent: CaseIntent | null
}

/** The body of POST /analyst/cases/{id}/decision, following the approve/edit/reject table. */
export function buildDecisionRequest(a: BuildArgs): DecisionRequest {
  const req: DecisionRequest = { client_decision_id: a.clientDecisionId, version: a.version, action: a.action }
  const labels = labelsFor(a.draft.intentLabel, a.intent)
  if (a.action === 'approve') {
    if (!a.reliable && a.draft.evidenceReviewed) req.evidence_reviewed = true
  } else if (a.action === 'edit') {
    req.reply = { language: a.caseLanguage, text: (a.draft.editText ?? '').trim() }
    req.reason = a.draft.editReason.trim()
    if (!a.reliable && a.draft.evidenceReviewed) req.evidence_reviewed = true
  } else {
    req.reason = a.draft.rejectReason.trim()
    if (a.draft.rejectNext) req.next = a.draft.rejectNext
    const reply = a.draft.rejectReply.trim()
    if (a.draft.rejectNext === 'request_information' && reply) {
      req.reply = { language: a.caseLanguage, text: reply }
    }
  }
  if (labels) req.labels = labels
  return req
}

/** What still blocks the action, as i18n keys; [] means it can be sent. */
export function decisionProblems(
  action: DecisionAction,
  draft: DecisionDraft,
  opts: { reliable: boolean; originalText: string | null },
): MessageKey[] {
  const out: MessageKey[] = []
  if (action === 'approve') {
    if (opts.originalText === null) out.push('console.problem.noDraft')
    if (!opts.reliable && !draft.evidenceReviewed) out.push('console.problem.reviewEvidence')
  } else if (action === 'edit') {
    const text = (draft.editText ?? '').trim()
    if (!text) out.push('console.problem.replyEmpty')
    else if (text.length > MAX_REPLY) out.push('console.problem.replyTooLong')
    else if (opts.originalText !== null && text === opts.originalText.trim()) out.push('console.problem.replyUnchanged')
    const reason = draft.editReason.trim()
    if (!reason) out.push('console.problem.reasonEmpty')
    else if (reason.length > MAX_REASON) out.push('console.problem.reasonTooLong')
    if (!opts.reliable && !draft.evidenceReviewed) out.push('console.problem.reviewEvidence')
  } else {
    const reason = draft.rejectReason.trim()
    if (!reason) out.push('console.problem.reasonEmpty')
    else if (reason.length > MAX_REASON) out.push('console.problem.reasonTooLong')
    if (!draft.rejectNext) out.push('console.problem.nextMissing')
    if (draft.rejectReply.trim().length > MAX_REPLY) out.push('console.problem.replyTooLong')
  }
  return out
}

// ---------- edit diff (word level, LCS) ----------
export interface DiffPart {
  op: 'same' | 'add' | 'del'
  text: string
}

function tokens(s: string): string[] {
  return s.split(/(\s+)/).filter((t) => t !== '')
}

export function wordDiff(before: string, after: string): DiffPart[] {
  const a = tokens(before)
  const b = tokens(after)
  // Bounded: replies are at most 2000 characters, so a few hundred tokens each.
  const n = a.length
  const m = b.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const parts: DiffPart[] = []
  const push = (op: DiffPart['op'], text: string) => {
    const last = parts[parts.length - 1]
    if (last && last.op === op) last.text += text
    else parts.push({ op, text })
  }
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push('same', a[i])
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push('del', a[i++])
    } else {
      push('add', b[j++])
    }
  }
  while (i < n) push('del', a[i++])
  while (j < m) push('add', b[j++])
  return parts
}

// ---------- orientative refund / compensation promise check ----------
// Only a hint for the analyst (the server stamps every reply and checks the investigator's
// draft). Built from word stems so this file never contains a promise itself.
const STEMS = '(reembols|devolv|compens|estorn|ressarc|abon)'
const PROMISE_RES: readonly RegExp[] = [
  new RegExp(`(?<!\\p{L})${STEMS}\\p{L}*?(aremos|eremos|iremos|ará|erá|irá)(?!\\p{L})`, 'iu'),
  new RegExp(`(?<!\\p{L})(vamos|voy)\\s+(a\\s+|te\\s+)?${STEMS}`, 'iu'),
  new RegExp(`(?<!\\p{L})(reembols|devoluc|compensac|compensaç|estorn|ressarcim)\\p{L}*\\s+garanti[zd]`, 'iu'),
  new RegExp(`(?<!\\p{L})(recibir[aá]s?|receber[aá])\\s+(\\p{L}+\\s+)?(reembols|devoluc|compensac|compensaç|estorn)`, 'iu'),
]

export function mayPromiseRefund(text: string): boolean {
  return PROMISE_RES.some((rx) => rx.test(text))
}

// ---------- text from customers and reports ----------
// Bidi controls, zero-width and other invisible characters become visible markers (section 8:
// the console neutralizes them so a stored text cannot reorder or hide what the analyst reads).
// \p{Cf} covers every format character (bidi marks and isolates incl. U+061C, zero-width
// joiners, soft hyphen U+00AD, U+180E, word joiner, BOM, tag characters); the rest are
// invisible characters of other categories: combining grapheme joiner U+034F, line and
// paragraph separators U+2028/U+2029, Hangul fillers and the Khmer inherent vowels.
const INVISIBLE = /[\p{Cf}\u034F\u115F\u1160\u17B4\u17B5\u2028\u2029\u3164\uFFA0]/gu

export function neutralize(text: string | null | undefined): string {
  if (!text) return ''
  return text.replace(INVISIBLE, (c) => `[U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}]`)
}

/** New idempotency key for a decision (same generator as client_msg_id); kept while the
 *  same body is retried. */
export const newDecisionId: () => string = newClientMsgId
