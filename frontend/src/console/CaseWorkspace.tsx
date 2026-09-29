// Center and right panel of the console for one case: header, predicted reason (ML) with
// Confirmar / Corregir, the case package (said vs verified), the investigator report with
// citation chips, the decision, and the tabs. Every dynamic text is React text (escaped) and
// passes through neutralize() (bidi and zero-width characters become visible).

import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import type { ApiClient } from '../api/client'
import type { AnalystCaseDetail, DecisionResponse, InvestigatorReport } from '../api/types'
import { LaneChip } from '../components/LaneChip'
import { IconAlert, IconCheck, IconQuote, IconShield } from '../components/Icons'
import { SourceBadge } from '../components/SourceBadge'
import { formatDate } from '../format'
import { hasKey, useI18n } from '../i18n'
import { Conversation } from './Conversation'
import { DecisionPanel } from './DecisionPanel'
import { INTENT_CLASSES, isProvisionalInvestigator, isReportReliable, neutralize } from './decision'
import { dropDraft, getDraft, setDraft, type DecisionDraft } from './drafts'
import { customerLocale, dateTime, moneyWithCode, percent, plainAmount, slaInfo } from './format'
import { SlaAlertChips } from './SlaClock'
import { headerAlerts } from './slaAlerts'
import { SideTabs } from './SideTabs'
import { TABS, type TabId } from './tabs'

export interface CaseWorkspaceProps {
  api: ApiClient
  detail: AnalystCaseDetail
  reload: () => Promise<AnalystCaseDetail | null>
  onDecided: (res: DecisionResponse) => void
  onUnauthorized: () => void
  interactive: boolean
  now: number
  /** Shown above the case (e.g. "desactualizado hh:mm"). */
  notice?: ReactNode
  /** Grows each time the rail's "Trazas" is pressed: open the Traza tab (CX-10). */
  traceRequest?: number
}

// Texts written by the backend (handoff facts and open questions, the provisional investigator's
// findings and evidence summaries) are Spanish whatever the console language: they carry
// lang="es" so a screen reader in the Portuguese console reads them with a Spanish voice (CX-06).
// When G2 writes in the case language this must come from the report instead.
const BACKEND_LANG = 'es'

/** Below this width the queue and the case stack: opening a case moves to it (CX-11). */
const NARROW = '(max-width: 1023px)'

export function CaseWorkspace(p: CaseWorkspaceProps) {
  const { t } = useI18n()
  const caseId = p.detail.case.case_id
  const [draft, setDraftState] = useState<DecisionDraft>(() => getDraft(caseId))
  const [tab, setTab] = useState<TabId>('evidence')
  const [focusEvidence, setFocusEvidence] = useState<{ id: string; n: number } | null>(null)
  const titleRef = useRef<HTMLHeadingElement>(null)

  // On a phone the case is below the queue: bring its title into view and give it focus, or
  // tapping a case seems to do nothing. Wide screens keep the focus in the queue.
  useEffect(() => {
    if (typeof window.matchMedia !== 'function' || !window.matchMedia(NARROW).matches) return
    titleRef.current?.scrollIntoView?.({ block: 'start' })
    titleRef.current?.focus()
  }, [])

  const traceRequest = p.traceRequest ?? 0
  const lastTrace = useRef(traceRequest)
  useEffect(() => {
    if (traceRequest === lastTrace.current) return
    lastTrace.current = traceRequest
    setTab('trace')
    const el = document.getElementById('tab-trace')
    el?.scrollIntoView?.({ block: 'nearest' })
    el?.focus()
  }, [traceRequest])

  const updateDraft = useCallback(
    (patch: Partial<DecisionDraft>) => {
      setDraftState((d) => {
        const next = { ...d, ...patch }
        setDraft(caseId, next)
        return next
      })
    },
    [caseId],
  )

  // Alt+1..5 switch tabs (not a one-key shortcut; works anywhere in the console).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey) return
      const m = /^Digit([1-5])$/.exec(e.code) ?? /^([1-5])$/.exec(e.key)
      if (!m) return
      e.preventDefault()
      setTab(TABS[Number(m[1]) - 1])
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const openCitation = (id: string) => {
    setTab('evidence')
    setFocusEvidence((f) => ({ id, n: (f?.n ?? 0) + 1 }))
  }

  const onDecided = (res: DecisionResponse) => {
    dropDraft(caseId)
    setDraftState(getDraft(caseId))
    p.onDecided(res)
  }

  return (
    <>
      <section className="console-center" aria-labelledby="case-title">
        {p.notice}
        <CaseHeader detail={p.detail} now={p.now} titleRef={titleRef} />
        <IntentChip detail={p.detail} draft={draft} onDraft={updateDraft} editable={p.detail.allowed_actions.length > 0} />
        <CasePackage detail={p.detail} />
        <Conversation turns={p.detail.case.conversation} />
        {p.detail.report ? (
          <ReportView report={p.detail.report} onCite={openCitation} />
        ) : (
          <section className="card report" aria-labelledby="report-title">
            <h2 id="report-title" className="card__title">
              {t('console.report.title')}
            </h2>
            <NoReport detail={p.detail} />
          </section>
        )}
        <DecisionPanel
          api={p.api}
          detail={p.detail}
          draft={draft}
          onDraft={updateDraft}
          onDecided={onDecided}
          reload={p.reload}
          onUnauthorized={p.onUnauthorized}
          interactive={p.interactive}
        />
      </section>
      <div className="console-side">
        <SideTabs detail={p.detail} tab={tab} onTab={setTab} focusEvidence={focusEvidence} />
      </div>
    </>
  )
}

function NoReport({ detail }: { detail: AnalystCaseDetail }) {
  const { t } = useI18n()
  const s = detail.case.status
  const key =
    detail.case.lane === 'C'
      ? 'console.report.laneC'
      : s === 'open' || s === 'investigating'
        ? 'console.report.pending'
        : 'console.report.none'
  return <p className="block__empty">{t(key)}</p>
}

/** Focus the first available decision control (or the panel): the header stays on screen while
 *  the case scrolls, so the decision is one step away (CX-14). */
function goToDecision() {
  const panel = document.getElementById('decision')
  if (!panel) return
  const target = panel.querySelector<HTMLElement>('.decision__actions button:not([disabled]), textarea, input') ?? panel
  if (target === panel && !panel.hasAttribute('tabindex')) panel.setAttribute('tabindex', '-1')
  target.scrollIntoView?.({ block: 'center' })
  target.focus()
}

function CaseHeader({
  detail,
  now,
  titleRef,
}: {
  detail: AnalystCaseDetail
  now: number
  titleRef: RefObject<HTMLHeadingElement | null>
}) {
  const { t, locale } = useI18n()
  const c = detail.case
  const prof = detail.context.profile
  const sla = slaInfo(c.clock.breach_at, now, locale)
  const ruleKey = c.lane_reason ? `rule.${c.lane_reason}` : ''
  return (
    <header className="case-head card">
      <div className="case-head__top">
        <h1 id="case-title" className="case-head__title" ref={titleRef} tabIndex={-1}>
          <span className="mono">{c.case_id}</span> · {neutralize(prof.display_name)}
        </h1>
        <span className="tag tag--synthetic">{t('card.synthetic')}</span>
        <button type="button" className="btn btn--primary btn--small case-head__jump" onClick={goToDecision}>
          {t('console.goDecision')}
        </button>
      </div>
      <ul className="case-head__meta">
        <li>
          <span className="muted">{t('console.country')}</span> {prof.country ?? c.country ?? '—'}
        </li>
        <li>
          <span className="muted">{t('console.segment')}</span> {prof.segment ?? c.segment ?? '—'}
        </li>
        <li>
          <span className="muted">{t('console.language')}</span> <span className="tag tag--lang">{c.language.toUpperCase()}</span>
        </li>
        <li>
          <span className="muted">{t('card.state')}</span> <span className="tag">{t(`status.${c.status}`)}</span>
        </li>
        <li className={sla?.overdue ? 'sla sla--overdue' : sla?.soon ? 'sla sla--soon' : 'sla'}>
          <span className="muted">SLA</span>{' '}
          {sla ? (sla.overdue ? t('console.slaOverdue', { when: sla.label }) : t('console.slaLeft', { when: sla.label })) : t('console.slaNone')}
        </li>
        {c.promise.expected_date && (
          <li>
            <span className="muted">{t('card.expectedDate')}</span> {formatDate(c.promise.expected_date, locale)}
          </li>
        )}
        {headerAlerts(c).length > 0 && (
          <li>
            <SlaAlertChips alerts={headerAlerts(c)} history={c.status === 'notified' || c.status === 'closed'} />
          </li>
        )}
      </ul>
      {c.lane && (
        <div className="case-head__lane">
          <LaneChip lane={c.lane} />
          <SourceBadge kind="rule" />
          <code className="mono small">{c.lane_reason ?? t('console.none')}</code>
          <span className="muted small">
            {c.rules_version ? t('trace.rulesVersion', { v: c.rules_version }) : t('console.rulesVersionMissing')}
          </span>
          {ruleKey && hasKey(ruleKey) && <p className="small case-head__why">{t(ruleKey)}</p>}
        </div>
      )}
    </header>
  )
}

function IntentChip({
  detail,
  draft,
  onDraft,
  editable,
}: {
  detail: AnalystCaseDetail
  draft: DecisionDraft
  onDraft: (p: Partial<DecisionDraft>) => void
  editable: boolean
}) {
  const { t, locale } = useI18n()
  const intent = detail.case.intent
  const [correcting, setCorrecting] = useState(false)
  if (!intent) {
    return (
      <div className="intent card">
        <SourceBadge kind="ml" /> <span className="muted">{t('console.intent.none')}</span>
      </div>
    )
  }
  const label = draft.intentLabel
  const clsKey = `intent.${intent.class}`
  return (
    <div className="intent card" role="group" aria-label={t('console.intent.title')}>
      <div className="intent__main">
        <SourceBadge kind="ml" />
        <strong>{t('console.intent.title')}:</strong>
        <span className="intent__class">{hasKey(clsKey) ? t(clsKey) : intent.class}</span>
        <span className="tag">p {percent(intent.p, locale)}</span>
        <span className="tag">{t('console.intent.gate', { gate: t(`gate.${intent.gate_action}`) })}</span>
        <code className="mono small">{intent.model_version ?? t('trace.noModel')}</code>
      </div>
      {editable && (
        <div className="intent__actions">
          {label?.kind === 'confirmed' ? (
            <span className="tag tag--verified">
              <IconCheck size={12} /> {t('console.intent.confirmed')}
            </span>
          ) : label?.kind === 'corrected' ? (
            <span className="tag tag--unverified">
              {t('console.intent.correctedTo', { cls: t(`intent.${label.intentClass}`) })}
            </span>
          ) : null}
          {/* Once confirmed, the tag says so and the button becomes "Deshacer" (CX-17 (5)). */}
          {label?.kind === 'confirmed' ? (
            <button type="button" className="btn btn--ghost btn--small" onClick={() => onDraft({ intentLabel: null })}>
              {t('console.intent.undo')}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn--ghost btn--small"
              onClick={() => {
                setCorrecting(false)
                onDraft({ intentLabel: { kind: 'confirmed' } })
              }}
            >
              {t('console.intent.confirm')}
            </button>
          )}
          <button
            type="button"
            className="btn btn--ghost btn--small"
            aria-expanded={correcting}
            onClick={() => setCorrecting((v) => !v)}
          >
            {t('console.intent.correct')}
          </button>
          {correcting && (
            <label className="intent__select">
              <span className="sr-only">{t('console.intent.pick')}</span>
              {/* aria-label: a <select> inside its <label> otherwise takes the chosen option as
                  its name (CX-17 (3)). */}
              <select
                aria-label={t('console.intent.pick')}
                value={label?.kind === 'corrected' ? label.intentClass : ''}
                onChange={(e) => {
                  const v = e.target.value
                  onDraft({
                    intentLabel: v ? { kind: 'corrected', intentClass: v as (typeof INTENT_CLASSES)[number] } : null,
                  })
                }}
              >
                <option value="">{t('console.intent.pick')}</option>
                {INTENT_CLASSES.filter((c) => c !== intent.class).map((c) => (
                  <option key={c} value={c}>
                    {t(`intent.${c}`)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <span className="block__note">{t('console.intent.note')}</span>
        </div>
      )}
    </div>
  )
}

function CasePackage({ detail }: { detail: AnalystCaseDetail }) {
  const { t, lang } = useI18n()
  const c = detail.case
  const loc = customerLocale(lang, c.country)
  const tx = c.evidence.transaction
  // Lane C: the handoff block below owns facts_verified and open_questions (shown once).
  const laneC = c.lane === 'C'
  const openQuestions = [...(laneC ? [] : c.handoff.open_questions), ...(detail.report?.open_questions ?? [])]
  const verifiedActions = c.actions.filter((a) => a.verified_at)
  const subKey = `complaint.${c.subcategory ?? ''}`
  return (
    <section className="card package" aria-labelledby="package-title">
      <header className="card__header">
        <h2 id="package-title" className="card__title">
          {t('console.package')}
        </h2>
      </header>
      <div className="package__grid">
        <div className="block block--claimed" data-testid="console-claimed">
          <div className="block__head">
            <h3 className="block__title">
              <IconQuote size={14} /> {t('console.said')}
            </h3>
            <span className="tag tag--unverified">{t('card.unverified')}</span>
          </div>
          {c.customer_statement ? (
            <blockquote className="statement" lang={c.language === 'pt' ? 'pt-BR' : 'es'}>
              {neutralize(c.customer_statement)}
            </blockquote>
          ) : (
            <p className="block__empty">{t('console.saidNone')}</p>
          )}
          {c.subcategory && (
            <p className="small">
              {t('console.subcategory')}:{' '}
              {hasKey(subKey) ? t(subKey) : <span className="mono">{c.subcategory}</span>}
            </p>
          )}
          {c.urgency_flags.length > 0 && (
            <p className="small">
              {t('console.flags')}:{' '}
              {c.urgency_flags.map((f) => (
                <span key={f} className="tag mono">
                  {f}
                </span>
              ))}
            </p>
          )}
        </div>
        <div className="block block--verified" data-testid="console-verified">
          <div className="block__head">
            <h3 className="block__title">
              <IconShield size={14} /> {t('console.verified')}
            </h3>
            <span className="tag tag--verified">{t('card.verified')}</span>
          </div>
          {tx ? (
            <dl className="facts">
              <div className="facts__row">
                <dt>{t('field.date')}</dt>
                <dd>
                  {formatDate(tx.local_date, loc)} {tx.local_time}
                </dd>
              </div>
              {/* Local currency with its ISO code ("$449.90" alone does not say MXN), then the USD
                  equivalent once: the label says USD, the value is the plain amount (es-MX would
                  otherwise print "USD 24.60" under a "USD" label). */}
              <div className="facts__row">
                <dt>{t('console.amountLocal')}</dt>
                <dd className="facts__money">{moneyWithCode(tx.amount, tx.currency, loc)}</dd>
              </div>
              {tx.amount_usd && (
                <div className="facts__row">
                  <dt>{t('console.amountUsd')}</dt>
                  <dd>
                    <span data-testid="verified-usd">{plainAmount(tx.amount_usd, loc)}</span>{' '}
                    <span className="muted small">({t('console.fxSynthetic')})</span>
                  </dd>
                </div>
              )}
              <div className="facts__row">
                <dt>{t('field.merchant')}</dt>
                <dd>{neutralize(tx.merchant_name)}</dd>
              </div>
              <div className="facts__row">
                <dt>{t('field.card')}</dt>
                <dd className="mono">{tx.card_last4 ? `•••• ${tx.card_last4}` : '—'}</dd>
              </div>
              <div className="facts__row">
                <dt>txn_id</dt>
                <dd className="mono">{tx.txn_id}</dd>
              </div>
            </dl>
          ) : (
            <p className="block__empty">{t('console.verifiedNone')}</p>
          )}
          {!laneC && c.handoff.facts_verified.length > 0 && (
            <ul className="bullets">
              {c.handoff.facts_verified.map((f, i) => (
                <li key={i} lang={BACKEND_LANG}>
                  {neutralize(f)}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="block">
        <h3 className="block__title">{t('console.actionsTaken')}</h3>
        {verifiedActions.length === 0 && c.actions.length === 0 ? (
          <p className="block__empty">{t('console.actions.none')}</p>
        ) : (
          <ul className="plainlist">
            {c.actions.map((a, i) => (
              <li key={`${a.tool}-${i}`}>
                <SourceBadge kind="tool" /> <code className="mono">{a.tool}</code>{' '}
                {a.verified_at ? (
                  <span className="small muted">{t('console.verifiedAt', { when: dateTime(a.verified_at, loc) })}</span>
                ) : (
                  <span className="tag tag--unverified">{t('card.unverified')}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {!(laneC && openQuestions.length === 0) && (
        <div className="block">
          <h3 className="block__title">{t('console.openQuestions')}</h3>
          {openQuestions.length === 0 ? (
            <p className="block__empty">{t('console.none')}</p>
          ) : (
            <ul className="bullets">
              {openQuestions.map((q, i) => (
                <li key={i} lang={BACKEND_LANG}>
                  {neutralize(q)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {c.lane === 'C' && (
        <div className="block block--handoff" data-testid="console-handoff">
          <h3 className="block__title">{t('console.handoff')}</h3>
          <dl className="facts">
            <div className="facts__row">
              <dt>{t('card.queue')}</dt>
              <dd>{c.handoff.queue ? queueLabel(c.handoff.queue, t) : '—'}</dd>
            </div>
            <div className="facts__row">
              <dt>{t('console.priority')}</dt>
              <dd>{c.handoff.priority ? t(`console.priority.${c.handoff.priority}`) : '—'}</dd>
            </div>
            <div className="facts__row facts__row--stack">
              <dt>{t('console.handoffFacts')}</dt>
              <dd>
                {c.handoff.facts_verified.length > 0 ? (
                  <ul className="bullets">
                    {c.handoff.facts_verified.map((f, i) => (
                      <li key={i} lang={BACKEND_LANG}>
                        {neutralize(f)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  t('console.none')
                )}
              </dd>
            </div>
            <div className="facts__row facts__row--stack">
              <dt>{t('console.openQuestions')}</dt>
              <dd>
                {c.handoff.open_questions.length > 0 ? (
                  <ul className="bullets">
                    {c.handoff.open_questions.map((q, i) => (
                      <li key={i} lang={BACKEND_LANG}>
                        {neutralize(q)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  t('console.none')
                )}
              </dd>
            </div>
          </dl>
        </div>
      )}
    </section>
  )
}

function queueLabel(q: string, t: ReturnType<typeof useI18n>['t']): string {
  const k = `queue.${q}`
  return hasKey(k) ? t(k) : q
}

function ReportView({ report, onCite }: { report: InvestigatorReport; onCite: (id: string) => void }) {
  const { t, locale } = useI18n()
  const reliable = isReportReliable(report)
  const provisional = isProvisionalInvestigator(report)
  const recKey = `rec.${report.recommendation}`
  return (
    <section className="card report" aria-labelledby="report-title">
      <header className="card__header">
        <h2 id="report-title" className="card__title">
          {t('console.report.title')}
        </h2>
        {provisional ? (
          <SourceBadge kind="rule" label={t('console.report.provisional')} />
        ) : (
          <SourceBadge kind="genai" label={t('console.report.genai', { model: report.model_id })} />
        )}
      </header>
      {!reliable && (
        <div className="unreliable" role="alert" data-testid="unreliable-strip">
          <IconAlert size={16} />
          <div>
            <strong>{t('console.unreliable')}</strong>
            <p className="small">
              {t('console.unreliableBody', {
                removed: report.removed_claims,
                citations: t(report.citations_valid ? 'console.trace.citationsOk' : 'console.trace.citationsBad'),
              })}
            </p>
          </div>
        </div>
      )}

      <div className="block">
        <h3 className="block__title">{t('console.report.findings')}</h3>
        {report.findings.length === 0 ? (
          <p className="block__empty">{t('console.none')}</p>
        ) : (
          <ol className="findings">
            {report.findings.map((f, i) => (
              <li key={i} className="finding">
                <p lang={BACKEND_LANG}>{neutralize(f.claim)}</p>
                <div className="cites" role="group" aria-label={t('console.report.citations')}>
                  {f.evidence_ids.length === 0 && <span className="tag tag--danger">{t('console.report.noCitation')}</span>}
                  {f.evidence_ids.map((id) => {
                    const rec = report.evidence_records[id]
                    return rec ? (
                      <button
                        key={id}
                        type="button"
                        className="cite"
                        onClick={() => onCite(id)}
                        aria-label={t('console.report.openCitation', { id })}
                        title={neutralize(rec.summary)}
                      >
                        <IconShield size={12} /> <span className="mono">{id}</span>
                      </button>
                    ) : (
                      <span key={id} className="cite cite--missing" title={t('console.report.missingRecord')}>
                        <IconAlert size={12} /> <span className="mono">{id}</span>
                      </span>
                    )
                  })}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="report__row">
        <div className="block">
          <h3 className="block__title">{t('console.report.hypotheses')}</h3>
          <ul className="hyps">
            {report.hypotheses.map((h) => (
              <li key={h.name} className="hyp">
                <span className="hyp__name">{t(`hyp.${h.name}`)}</span>
                <span className="hyp__bar" aria-hidden="true">
                  <span style={{ width: `${Math.round(Math.max(0, Math.min(1, h.p)) * 100)}%` }} />
                </span>
                <span className="hyp__p">{percent(h.p, locale)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="block">
          <h3 className="block__title">{t('console.report.recommendation')}</h3>
          <p className="rec">
            <strong>{hasKey(recKey) ? t(recKey) : report.recommendation}</strong>
          </p>
          <p className="small muted">{t('console.report.confidence', { p: percent(report.confidence, locale) })}</p>
          <p className="block__note">{t('console.report.proposalOnly')}</p>
        </div>
      </div>
    </section>
  )
}
