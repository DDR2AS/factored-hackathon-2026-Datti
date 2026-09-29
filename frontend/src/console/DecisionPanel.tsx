// Draft reply and the analyst's decision: Aprobar / Editar / Rechazar (RF-16).
// Nothing is sent on its own: approve asks for a confirmation, edit shows the diff and needs a
// reason, reject needs a reason and the next step. With an unreliable report, approving or
// editing needs "Revisé la evidencia". Retries reuse the same client_decision_id and body.
// "Pedir información" shows the exact text that goes out (question or template, with the
// stamp) and asks to confirm, like Aprobar. Focus follows each step (CX-04): the confirm button
// when a confirmation opens, the result notice after sending, the opener after Esc/Cancelar.

import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { isApiClientError, type ApiClient, type ClientErrorCode } from '../api/client'
import type { AnalystCaseDetail, DecisionAction, DecisionRequest, DecisionResponse } from '../api/types'
import { errorMessageKey } from '../chat/errors'
import { IconAlert, IconCheck, IconHuman, IconRefresh, IconX } from '../components/Icons'
import { SourceBadge } from '../components/SourceBadge'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n/es'
import {
  buildDecisionRequest,
  decisionProblems,
  isProvisionalInvestigator,
  isReportReliable,
  MAX_REASON,
  MAX_REPLY,
  mayPromiseRefund,
  neutralize,
  newDecisionId,
  wordDiff,
} from './decision'
import type { DecisionDraft } from './drafts'
import { requestInformationDefault, stamped } from './customerTexts'
import { dateTime, hhmm } from './format'

export interface DecisionPanelProps {
  api: ApiClient
  detail: AnalystCaseDetail
  draft: DecisionDraft
  onDraft: (patch: Partial<DecisionDraft>) => void
  onDecided: (res: DecisionResponse) => void
  /** Re-reads the case (after 409/422); resolves with the fresh detail or null. */
  reload: () => Promise<AnalystCaseDetail | null>
  onUnauthorized: () => void
  /** False while the sign-in dialog is open. */
  interactive: boolean
}

type Outcome =
  | { kind: 'sent'; res: DecisionResponse }
  | { kind: 'conflict'; who: string | null; at: string | null }
  | { kind: 'error'; code: ClientErrorCode; retryable: boolean }
  | null

function sameBody(a: DecisionRequest, b: DecisionRequest): boolean {
  const strip = (r: DecisionRequest) => JSON.stringify({ ...r, client_decision_id: '' })
  return strip(a) === strip(b)
}

export function DecisionPanel(p: DecisionPanelProps) {
  const { t, locale } = useI18n()
  const { detail, draft } = p
  const report = detail.report
  const reliable = report ? isReportReliable(report) : true
  const originalText = report?.draft_reply?.text ?? null
  const allowed = detail.allowed_actions
  const [submitting, setSubmitting] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>(null)
  const lastReq = useRef<DecisionRequest | null>(null)
  const ids = { reason: useId(), text: useId(), next: useId(), reply: useId(), review: useId() }
  const editRef = useRef<HTMLTextAreaElement>(null)
  const reasonRef = useRef<HTMLTextAreaElement>(null)
  const approveRef = useRef<HTMLButtonElement>(null)
  const editBtnRef = useRef<HTMLButtonElement>(null)
  const rejectRef = useRef<HTMLButtonElement>(null)
  const confirmApproveRef = useRef<HTMLButtonElement>(null)
  const confirmRejectRef = useRef<HTMLButtonElement>(null)
  const rejectSendRef = useRef<HTMLButtonElement>(null)
  const outcomeRef = useRef<HTMLDivElement>(null)
  // The mode that Esc/Cancelar just closed: focus goes back to the button that opened it.
  const returnTo = useRef<DecisionAction | null>(null)
  // "Pedir información": the exact outgoing text is on screen, waiting for "Confirmar y enviar".
  // The confirmation belongs to the reject form as it was: any change to it withdraws it.
  const rejectFormKey = JSON.stringify([draft.mode, draft.rejectReason, draft.rejectNext, draft.rejectReply])
  const [confirmedForm, setConfirmedForm] = useState<string | null>(null)
  const rejectConfirm = confirmedForm === rejectFormKey
  const setRejectConfirm = (on: boolean) => setConfirmedForm(on ? rejectFormKey : null)
  const backFromConfirm = useRef(false)

  useEffect(() => {
    if (draft.mode === 'edit') editRef.current?.focus()
    else if (draft.mode === 'reject') reasonRef.current?.focus()
    else if (draft.mode === 'approve') confirmApproveRef.current?.focus()
    else if (returnTo.current) {
      const opener = { approve: approveRef, edit: editBtnRef, reject: rejectRef }[returnTo.current]
      opener.current?.focus()
      returnTo.current = null
    }
  }, [draft.mode])

  useEffect(() => {
    if (rejectConfirm) confirmRejectRef.current?.focus()
    else if (backFromConfirm.current) {
      backFromConfirm.current = false
      rejectSendRef.current?.focus()
    }
  }, [rejectConfirm])

  // The result (sent, 409, error) takes the focus so it is announced and not lost on <body>.
  // A 401 leaves the focus to the sign-in dialog.
  useEffect(() => {
    if (outcome && !(outcome.kind === 'error' && outcome.code === 'session_expired')) outcomeRef.current?.focus()
  }, [outcome])

  const send = async (action: DecisionAction) => {
    if (submitting || !p.interactive) return
    const problems = decisionProblems(action, draft, { reliable, originalText })
    if (problems.length > 0) return
    const fresh = buildDecisionRequest({
      action,
      clientDecisionId: newDecisionId(),
      version: detail.version,
      draft,
      caseLanguage: detail.case.language,
      reliable,
      intent: detail.case.intent,
    })
    // Same body as the last attempt (retry, or after signing in again): same id, safe retry.
    const req = lastReq.current && sameBody(lastReq.current, fresh) ? lastReq.current : fresh
    lastReq.current = req
    setSubmitting(true)
    setOutcome(null)
    try {
      const res = await p.api.decide(detail.case.case_id, req)
      lastReq.current = null
      setOutcome({ kind: 'sent', res })
      p.onDecided(res)
    } catch (e) {
      const code: ClientErrorCode = isApiClientError(e) ? e.code : 'unexpected'
      if (code === 'session_expired') {
        p.onUnauthorized()
        setOutcome({ kind: 'error', code, retryable: true })
      } else if (code === 'conflict') {
        lastReq.current = null
        const fresh2 = await p.reload()
        const d = fresh2?.case.analyst_decision ?? null
        setOutcome({ kind: 'conflict', who: d?.decided_by ?? null, at: d?.decided_at ?? null })
      } else if (code === 'precondition' || code === 'invalid_request') {
        lastReq.current = null
        setOutcome({ kind: 'error', code, retryable: false })
        void p.reload()
      } else {
        setOutcome({ kind: 'error', code, retryable: isApiClientError(e) ? e.retryable : true })
      }
    } finally {
      setSubmitting(false)
    }
  }

  const cancel = () => {
    if (draft.mode !== 'none') returnTo.current = draft.mode
    p.onDraft({ mode: 'none' })
  }
  const backToRejectForm = () => {
    backFromConfirm.current = true
    setRejectConfirm(false)
  }
  const onEscape = (e: KeyboardEvent) => {
    // Behind the sign-in dialog nothing reacts (the page is inert; this also guards key events
    // that still reach it).
    if (!p.interactive) return
    if (e.key === 'Escape' && draft.mode !== 'none') {
      e.preventDefault()
      if (rejectConfirm) backToRejectForm()
      else cancel()
    }
  }
  // What the customer receives with "Pedir información" (the server adds the same stamp).
  const rejectOutgoing =
    draft.rejectNext === 'request_information'
      ? stamped(
          draft.rejectReply.trim() || requestInformationDefault(detail.case.language, detail.case.country, detail.case.case_id),
          detail.case.language,
        )
      : null

  const decided = detail.case.analyst_decision
  const provisional = report ? isProvisionalInvestigator(report) : false
  // After the decision the panel shows what the customer actually received (stamped, approved
  // by a person) instead of the draft; an escalation sends nothing, so the draft stays marked
  // as never sent.
  const sent = decided ? detail.case.resolution : null

  return (
    <section id="decision" className="decision card" aria-labelledby="decision-title" onKeyDown={onEscape}>
      <header className="card__header">
        <h2 id="decision-title" className="card__title">
          {t(sent ? 'console.sent.title' : 'console.draft.title')}
        </h2>
        {sent ? (
          <SourceBadge kind="human" label={t('console.sent.badge')} />
        ) : (
          report?.draft_reply && (
            <SourceBadge
              kind={provisional ? 'rule' : 'genai'}
              label={t(provisional ? 'console.draft.provisional' : 'console.draft.generated')}
            />
          )
        )}
      </header>

      {sent ? (
        <figure className="draft draft--sent" lang={sent.language === 'pt' ? 'pt-BR' : 'es'}>
          <figcaption className="muted small">{t('console.sent.caption', { lang: sent.language.toUpperCase() })}</figcaption>
          <p className="draft__text">{neutralize(sent.text)}</p>
        </figure>
      ) : report?.draft_reply ? (
        <figure
          className={!decided && draft.mode === 'reject' ? 'draft draft--discarded' : 'draft'}
          lang={report.draft_reply.language === 'pt' ? 'pt-BR' : 'es'}
        >
          <figcaption className="muted small">
            {!decided && draft.mode === 'reject'
              ? t('console.draft.discarded')
              : t(decided ? 'console.draft.neverSent' : 'console.draft.notSent', { lang: report.draft_reply.language.toUpperCase() })}
          </figcaption>
          <p className="draft__text">{neutralize(report.draft_reply.text)}</p>
          {mayPromiseRefund(report.draft_reply.text) && <RefundHint />}
        </figure>
      ) : (
        <p className="block__empty">{t(report ? 'console.draft.none' : 'console.draft.noReport')}</p>
      )}

      {outcome && (
        <div ref={outcomeRef} tabIndex={-1} className="decision__outcome">
          <OutcomeNotice outcome={outcome} onRetry={() => lastReq.current && void send(lastReq.current.action)} />
        </div>
      )}

      {decided ? (
        // After a send or a 409 in this view the outcome notice already says who decided.
        outcome ? null : (
          <p className="notice notice--warn decision__done" role="status">
            <IconHuman size={14} />{' '}
            {t('console.alreadyDecided', {
              action: t(`console.action.${decided.action}`),
              who: decided.decided_by,
              time: hhmm(decided.decided_at, locale),
            })}
          </p>
        )
      ) : allowed.length === 0 ? (
        <p className="block__note">{t(readOnlyKey(detail))}</p>
      ) : (
        <>
          {!reliable && (draft.mode === 'none' || draft.mode === 'approve' || draft.mode === 'edit') && (
            <label className="check" htmlFor={ids.review}>
              <input
                id={ids.review}
                type="checkbox"
                checked={draft.evidenceReviewed}
                onChange={(e) => p.onDraft({ evidenceReviewed: e.target.checked })}
              />
              <span>{t('console.reviewedEvidence')}</span>
            </label>
          )}

          {draft.mode === 'none' && (
            <div className="decision__actions">
              {allowed.includes('approve') && (
                <button
                  ref={approveRef}
                  type="button"
                  className="btn btn--primary"
                  disabled={!p.interactive || decisionProblems('approve', draft, { reliable, originalText }).length > 0}
                  onClick={() => p.onDraft({ mode: 'approve' })}
                >
                  <IconCheck size={14} /> {t('console.approve')}
                </button>
              )}
              {allowed.includes('edit') && (
                <button
                  ref={editBtnRef}
                  type="button"
                  className="btn btn--ghost"
                  disabled={!p.interactive}
                  onClick={() => p.onDraft({ mode: 'edit', editText: draft.editText ?? originalText ?? '' })}
                >
                  {t('console.edit')}
                </button>
              )}
              {allowed.includes('reject') && (
                <button
                  ref={rejectRef}
                  type="button"
                  className="btn btn--ghost btn--danger"
                  disabled={!p.interactive}
                  onClick={() => p.onDraft({ mode: 'reject' })}
                >
                  <IconX size={14} /> {t('console.reject')}
                </button>
              )}
            </div>
          )}
          {draft.mode === 'none' && !reliable && !draft.evidenceReviewed && allowed.includes('approve') && (
            <p className="block__note">{t('console.problem.reviewEvidence')}</p>
          )}

          {draft.mode === 'approve' && (
            <div className="confirm" role="group" aria-label={t('console.approve')}>
              <p>{t('console.approveConfirm')}</p>
              <Problems keys={decisionProblems('approve', draft, { reliable, originalText })} />
              <div className="decision__actions">
                <button
                  ref={confirmApproveRef}
                  type="button"
                  className="btn btn--primary"
                  disabled={submitting || !p.interactive || decisionProblems('approve', draft, { reliable, originalText }).length > 0}
                  onClick={() => void send('approve')}
                >
                  {submitting ? <span className="spinner" aria-hidden="true" /> : <IconCheck size={14} />} {t('console.approveSend')}
                </button>
                <button type="button" className="btn btn--ghost" onClick={cancel}>
                  {t('console.cancel')}
                </button>
              </div>
            </div>
          )}

          {draft.mode === 'edit' && (
            <div className="editor">
              <label className="field" htmlFor={ids.text}>
                <span className="field__label">{t('console.editText', { lang: detail.case.language.toUpperCase() })}</span>
                <textarea
                  id={ids.text}
                  ref={editRef}
                  className="field__input field__textarea"
                  lang={detail.case.language === 'pt' ? 'pt-BR' : 'es'}
                  rows={6}
                  maxLength={MAX_REPLY}
                  value={draft.editText ?? ''}
                  onChange={(e) => p.onDraft({ editText: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                      e.preventDefault()
                      void send('edit')
                    }
                  }}
                />
              </label>
              <p className="block__note">
                {t('console.counter', { n: (draft.editText ?? '').length, max: MAX_REPLY })} · {t('console.editKeys')}
              </p>
              {mayPromiseRefund(draft.editText ?? '') && <RefundHint />}
              <Diff before={originalText ?? ''} after={draft.editText ?? ''} />
              <label className="field" htmlFor={ids.reason}>
                <span className="field__label">{t('console.reasonEdit')}</span>
                <textarea
                  id={ids.reason}
                  className="field__input field__textarea"
                  rows={2}
                  maxLength={MAX_REASON}
                  value={draft.editReason}
                  onChange={(e) => p.onDraft({ editReason: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                      e.preventDefault()
                      void send('edit')
                    }
                  }}
                />
              </label>
              <Problems keys={decisionProblems('edit', draft, { reliable, originalText })} />
              <div className="decision__actions">
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={submitting || !p.interactive || decisionProblems('edit', draft, { reliable, originalText }).length > 0}
                  onClick={() => void send('edit')}
                >
                  {submitting && <span className="spinner" aria-hidden="true" />} {t('console.editSend')}
                </button>
                <button type="button" className="btn btn--ghost" onClick={cancel}>
                  {t('console.cancel')}
                </button>
              </div>
            </div>
          )}

          {draft.mode === 'reject' && (
            <div className="editor">
              <label className="field" htmlFor={ids.reason}>
                <span className="field__label">{t('console.reasonReject')}</span>
                <textarea
                  id={ids.reason}
                  ref={reasonRef}
                  className="field__input field__textarea"
                  rows={2}
                  maxLength={MAX_REASON}
                  value={draft.rejectReason}
                  onChange={(e) => p.onDraft({ rejectReason: e.target.value })}
                />
              </label>
              <fieldset className="field">
                <legend className="field__label">{t('console.next')}</legend>
                {(['request_information', 'escalate'] as const).map((n) => (
                  <label key={n} className="check">
                    <input
                      type="radio"
                      name={ids.next}
                      value={n}
                      checked={draft.rejectNext === n}
                      onChange={() => p.onDraft({ rejectNext: n })}
                    />
                    <span>{t(`console.next.${n}`)}</span>
                  </label>
                ))}
              </fieldset>
              {draft.rejectNext === 'request_information' && (
                <label className="field" htmlFor={ids.reply}>
                  <span className="field__label">{t('console.rejectReply', { lang: detail.case.language.toUpperCase() })}</span>
                  <textarea
                    id={ids.reply}
                    className="field__input field__textarea"
                    lang={detail.case.language === 'pt' ? 'pt-BR' : 'es'}
                    rows={3}
                    maxLength={MAX_REPLY}
                    value={draft.rejectReply}
                    placeholder={t('console.rejectReplyHint')}
                    onChange={(e) => p.onDraft({ rejectReply: e.target.value })}
                  />
                </label>
              )}
              {draft.rejectNext === 'escalate' && <p className="block__note">{t('console.escalateNote')}</p>}
              <Problems keys={decisionProblems('reject', draft, { reliable, originalText })} />
              {rejectConfirm && rejectOutgoing !== null ? (
                <div className="confirm" role="group" aria-label={t('console.rejectConfirmTitle')}>
                  <p>{t('console.rejectConfirm')}</p>
                  <blockquote className="confirm__text" lang={detail.case.language === 'pt' ? 'pt-BR' : 'es'}>
                    {neutralize(rejectOutgoing)}
                  </blockquote>
                  <div className="decision__actions">
                    <button
                      ref={confirmRejectRef}
                      type="button"
                      className="btn btn--primary btn--danger-solid"
                      disabled={submitting || !p.interactive || decisionProblems('reject', draft, { reliable, originalText }).length > 0}
                      onClick={() => void send('reject')}
                    >
                      {submitting && <span className="spinner" aria-hidden="true" />} {t('console.approveSend')}
                    </button>
                    <button type="button" className="btn btn--ghost" onClick={backToRejectForm}>
                      {t('console.rejectBack')}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="decision__actions">
                  <button
                    ref={rejectSendRef}
                    type="button"
                    className="btn btn--primary btn--danger-solid"
                    disabled={submitting || !p.interactive || decisionProblems('reject', draft, { reliable, originalText }).length > 0}
                    onClick={() => {
                      // Something goes to the customer: show it and confirm first, like Aprobar.
                      if (draft.rejectNext === 'request_information') setRejectConfirm(true)
                      else void send('reject')
                    }}
                  >
                    {submitting && <span className="spinner" aria-hidden="true" />} {t('console.rejectSend')}
                  </button>
                  <button type="button" className="btn btn--ghost" onClick={cancel}>
                    {t('console.cancel')}
                  </button>
                </div>
              )}
            </div>
          )}
          <p className="block__note">{t('console.sendNote')}</p>
        </>
      )}
      {decided && detail.case.resolution && (
        <p className="block__note">{t('console.sentAt', { time: dateTime(detail.case.resolution.sent_at, locale) })}</p>
      )}
    </section>
  )
}

function readOnlyKey(detail: AnalystCaseDetail): MessageKey {
  const s = detail.case.status
  if (detail.case.lane === 'C') return 'console.readOnlyLaneC'
  if (s === 'open' || s === 'investigating') return 'console.readOnlyInvestigating'
  return 'console.readOnly'
}

function Problems({ keys }: { keys: MessageKey[] }) {
  const { t } = useI18n()
  if (keys.length === 0) return null
  return (
    <ul className="problems" aria-live="polite">
      {keys.map((k) => (
        <li key={k}>{t(k)}</li>
      ))}
    </ul>
  )
}

function RefundHint() {
  const { t } = useI18n()
  return (
    <p className="notice notice--warn refund-hint" role="note">
      <IconAlert size={14} /> {t('console.refundHint')}
    </p>
  )
}

function Diff({ before, after }: { before: string; after: string }) {
  const { t } = useI18n()
  const parts = wordDiff(before, after)
  const changed = parts.some((x) => x.op !== 'same')
  return (
    <figure className="diff">
      <figcaption className="field__label">{t('console.diff')}</figcaption>
      {!changed ? (
        <p className="block__empty">{t('console.diffNone')}</p>
      ) : (
        <p className="diff__text" data-testid="edit-diff">
          {parts.map((x, i) =>
            x.op === 'same' ? (
              <span key={i}>{neutralize(x.text)}</span>
            ) : x.op === 'add' ? (
              <ins key={i} className="diff__add">
                <span className="sr-only">{t('console.diffAdded')} </span>
                {neutralize(x.text)}
              </ins>
            ) : (
              <del key={i} className="diff__del">
                <span className="sr-only">{t('console.diffRemoved')} </span>
                {neutralize(x.text)}
              </del>
            ),
          )}
        </p>
      )}
    </figure>
  )
}

function OutcomeNotice({ outcome, onRetry }: { outcome: NonNullable<Outcome>; onRetry: () => void }) {
  const { t, locale } = useI18n()
  if (outcome.kind === 'sent') {
    const r = outcome.res
    return (
      <p className="notice notice--ok" role="status">
        <IconCheck size={14} />{' '}
        {t('console.decisionSaved', { action: t(`console.action.${r.analyst_decision.action}`), status: t(`status.${r.status}`) })}
      </p>
    )
  }
  if (outcome.kind === 'conflict') {
    return (
      <p className="notice notice--warn" role="alert">
        <IconAlert size={14} />{' '}
        {outcome.who
          ? t('console.conflictDecided', { who: outcome.who, time: hhmm(outcome.at, locale) })
          : t('console.conflictChanged')}
      </p>
    )
  }
  const key: MessageKey =
    outcome.code === 'session_expired'
      ? 'console.error.relogin'
      : outcome.code === 'not_authorized'
        ? 'console.error.forbidden'
        : outcome.code === 'not_implemented'
          ? 'console.error.notImplemented'
          : errorMessageKey(outcome.code)
  return (
    <div className="notice notice--error" role="alert">
      <IconAlert size={14} />
      <p>{t(key)}</p>
      {outcome.retryable && outcome.code !== 'session_expired' && (
        <div className="notice__actions">
          <button type="button" className="btn btn--ghost btn--small" onClick={onRetry}>
            <IconRefresh size={14} /> {t('console.retrySame')}
          </button>
        </div>
      )}
    </div>
  )
}
