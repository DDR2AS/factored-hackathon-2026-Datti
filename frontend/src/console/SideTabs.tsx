// Right panel of the console: Evidencia, Riesgo, Acciones, Historial, Traza (Alt+1..5).
// Everything is rendered as text; structured values go in <pre> as JSON text, collapsed in a
// <details> (CX-14). Field keys get a readable label when one exists (the raw key stays in the
// tooltip). A context part the tools could not read says "No disponible" instead of looking
// empty (H4).

import { useEffect, useRef, type KeyboardEvent } from 'react'
import type { AnalystCaseDetail, ContextPart, EvidenceTxn } from '../api/types'
import { IconAlert } from '../components/Icons'
import { SourceBadge } from '../components/SourceBadge'
import { formatDate, formatMoney } from '../format'
import { hasKey, useI18n } from '../i18n'
import type { MessageKey } from '../i18n/es'
import { customerLocale, dateTime, percent, valueText } from './format'
import { neutralize } from './decision'
import { TABS, type TabId } from './tabs'
import { ClockTimeline } from './SlaClock'

// Written by the backend in Spanish (see CaseWorkspace BACKEND_LANG).
const BACKEND_LANG = 'es'

/**
 * A part of the context that a data tool could not read is listed in context.unavailable (the
 * server marks it, analyst._context): it says "No disponible", never "none", which would look
 * like "no cards" or "no prior contacts" (H4). A server without the marker (before 30 sep) left
 * the part empty; then an empty evidence_txns next to the disputed charge, which is always in
 * the case evidence, still means the tools did not answer.
 */
function unavailable(detail: AnalystCaseDetail, part: ContextPart): boolean {
  const marked = detail.context.unavailable as ContextPart[] | undefined
  if (Array.isArray(marked)) return marked.includes(part)
  return !!detail.case.evidence.transaction?.txn_id && detail.context.evidence_txns.length === 0
}

function EmptyOrUnavailable({ unreadable, emptyKey }: { unreadable: boolean; emptyKey: MessageKey }) {
  const { t } = useI18n()
  return unreadable ? (
    <p className="block__empty block__empty--warn">
      <IconAlert size={14} /> {t('console.ctx.unavailable')}
    </p>
  ) : (
    <p className="block__empty">{t(emptyKey)}</p>
  )
}

export function SideTabs({
  detail,
  tab,
  onTab,
  focusEvidence,
}: {
  detail: AnalystCaseDetail
  tab: TabId
  onTab: (t: TabId) => void
  /** Evidence id a citation chip asked to open; the record gets focus and a highlight. */
  focusEvidence: { id: string; n: number } | null
}) {
  const { t } = useI18n()
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.indexOf(tab)
    let next = -1
    if (e.key === 'ArrowRight') next = (i + 1) % TABS.length
    else if (e.key === 'ArrowLeft') next = (i - 1 + TABS.length) % TABS.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = TABS.length - 1
    if (next < 0) return
    e.preventDefault()
    onTab(TABS[next])
    tabRefs.current[next]?.focus()
  }

  return (
    <section className="side" aria-label={t('console.sidePanel')}>
      <div className="tabs" role="tablist" aria-label={t('console.sidePanel')} onKeyDown={onKeyDown}>
        {TABS.map((id, i) => (
          <button
            key={id}
            ref={(el) => {
              tabRefs.current[i] = el
            }}
            type="button"
            role="tab"
            id={`tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            tabIndex={tab === id ? 0 : -1}
            className="tabs__tab"
            onClick={() => onTab(id)}
            title={t('console.tabShortcut', { n: i + 1 })}
          >
            {t(`console.tab.${id}`)}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="tabs__panel" tabIndex={-1}>
        {tab === 'evidence' && <EvidenceTab detail={detail} focus={focusEvidence} />}
        {tab === 'risk' && <RiskTab detail={detail} />}
        {tab === 'actions' && <ActionsTab detail={detail} />}
        {tab === 'history' && <HistoryTab detail={detail} />}
        {tab === 'trace' && <TraceTab detail={detail} />}
      </div>
    </section>
  )
}

function Fields({ fields }: { fields: Record<string, unknown> }) {
  const { t } = useI18n()
  const entries = Object.entries(fields)
  if (entries.length === 0) return null
  return (
    <dl className="kv">
      {entries.map(([k, v]) => {
        const labelKey = `evfield.${k}`
        const readable = hasKey(labelKey)
        return (
          <div key={k} className="kv__row">
            <dt className={readable ? undefined : 'mono'} title={readable ? k : undefined}>
              {readable ? t(labelKey) : k}
            </dt>
            <dd>
              {typeof v === 'object' && v !== null ? (
                <details className="kv__json">
                  <summary>{t('console.showJson')}</summary>
                  <pre className="pre">{valueText(v)}</pre>
                </details>
              ) : (
                neutralize(valueText(v))
              )}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

function EvidenceTab({ detail, focus }: { detail: AnalystCaseDetail; focus: { id: string; n: number } | null }) {
  const { t, lang } = useI18n()
  const records = detail.report?.evidence_records ?? {}
  const ids = Object.keys(records)
  const locale = customerLocale(lang, detail.case.country)
  const refs = useRef<Record<string, HTMLElement | null>>({})

  useEffect(() => {
    if (!focus) return
    const el = refs.current[focus.id]
    el?.focus()
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [focus])

  return (
    <div className="tabbody">
      <h3 className="block__title">{t('console.ev.records')}</h3>
      {ids.length === 0 ? (
        <p className="block__empty">{t('console.ev.noRecords')}</p>
      ) : (
        <ul className="evlist">
          {ids.map((id) => {
            const r = records[id]
            const kindKey = `evkind.${r.kind}`
            return (
              <li
                key={id}
                id={`ev-${id}`}
                ref={(el) => {
                  refs.current[id] = el
                }}
                tabIndex={-1}
                className={`evrec ${focus?.id === id ? 'evrec--focus' : ''}`}
                data-testid={`evidence-${id}`}
              >
                <div className="evrec__head">
                  <code className="mono">{id}</code>
                  <span className="tag">{hasKey(kindKey) ? t(kindKey) : r.kind}</span>
                </div>
                <p className="evrec__summary" lang={BACKEND_LANG}>
                  {neutralize(r.summary)}
                </p>
                <Fields fields={r.fields} />
              </li>
            )
          })}
        </ul>
      )}

      <h3 className="block__title">{t('console.ev.txns')}</h3>
      {unavailable(detail, 'evidence_txns') || detail.context.evidence_txns.length === 0 ? (
        <EmptyOrUnavailable unreadable={unavailable(detail, 'evidence_txns')} emptyKey="console.ev.noTxns" />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <caption className="sr-only">{t('console.ev.txns')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('field.date')}</th>
                <th scope="col">{t('field.merchant')}</th>
                <th scope="col" className="num">
                  {t('field.amount')}
                </th>
                <th scope="col">{t('console.ev.type')}</th>
                <th scope="col">{t('field.status')}</th>
                <th scope="col">txn_id</th>
              </tr>
            </thead>
            <tbody>
              {detail.context.evidence_txns.map((x) => (
                <TxnRow key={x.txn_id} txn={x} locale={locale} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(detail.case.evidence.product || detail.case.evidence.app_error) && (
        <>
          <h3 className="block__title">{t('console.ev.other')}</h3>
          {detail.case.evidence.product && <Fields fields={detail.case.evidence.product} />}
          {detail.case.evidence.app_error && <Fields fields={detail.case.evidence.app_error} />}
        </>
      )}
    </div>
  )
}

function TxnRow({ txn, locale }: { txn: EvidenceTxn; locale: string }) {
  const { t } = useI18n()
  const typeKey = `txnType.${txn.txn_type}`
  const statusKey = `chargeStatus.${txn.status.toLowerCase()}`
  return (
    <tr>
      <td>
        {formatDate(txn.local_date, locale)} <span className="muted small">{txn.local_time}</span>
      </td>
      <td>{neutralize(txn.merchant_name)}</td>
      <td className="num">{formatMoney(txn.amount, txn.currency, locale)}</td>
      <td>
        {hasKey(typeKey) ? t(typeKey) : txn.txn_type}
        {txn.reversal_of && <span className="muted small"> · {t('console.ev.reverses', { id: txn.reversal_of })}</span>}
      </td>
      <td>{hasKey(statusKey) ? t(statusKey) : txn.status}</td>
      <td className="mono small">{txn.txn_id}</td>
    </tr>
  )
}

function RiskTab({ detail }: { detail: AnalystCaseDetail }) {
  const { t, locale } = useI18n()
  const risk = detail.case.risk_evidence ?? detail.context.risk_evidence
  return (
    <div className="tabbody">
      <h3 className="block__title">{t('console.risk.title')}</h3>
      {!risk ? (
        <p className="block__empty">{t('console.risk.none')}</p>
      ) : (
        <>
          <dl className="facts">
            <div className="facts__row">
              <dt>fraud_score</dt>
              <dd className="mono">{risk.fraud_score ?? '—'}</dd>
            </div>
            <div className="facts__row">
              <dt>{t('console.risk.rule')}</dt>
              <dd className={risk.rule_fired ? 'mono' : undefined}>{risk.rule_fired ?? t('console.risk.noRule')}</dd>
            </div>
          </dl>
          <h4 className="block__subtitle">{t('console.risk.features')}</h4>
          {Object.keys(risk.features).length === 0 ? <p className="block__empty">{t('console.none')}</p> : <Fields fields={risk.features} />}
        </>
      )}
      <h3 className="block__title">{t('console.risk.cards')}</h3>
      {unavailable(detail, 'cards') || detail.context.cards.length === 0 ? (
        <EmptyOrUnavailable unreadable={unavailable(detail, 'cards')} emptyKey="console.risk.noCards" />
      ) : (
        <ul className="plainlist">
          {detail.context.cards.map((c) => {
            const key = `cardStatus.${c.status}`
            return (
              <li key={c.card_last4}>
                <span className="mono">•••• {c.card_last4}</span> · {hasKey(key) ? t(key) : c.status}
              </li>
            )
          })}
        </ul>
      )}
      {detail.case.match && (
        <>
          <h3 className="block__title">{t('console.risk.match')}</h3>
          <p className="small">
            <SourceBadge kind="ml" /> {t('console.risk.matchLine', {
              p: percent(detail.case.match.p_top1, locale),
              band: detail.case.match.band ? t(`band.${detail.case.match.band}`) : '—',
            })}
            {detail.case.match.model_version && <span className="mono small"> · {detail.case.match.model_version}</span>}
          </p>
        </>
      )}
    </div>
  )
}

function ActionsTab({ detail }: { detail: AnalystCaseDetail }) {
  const { t, locale } = useI18n()
  const actions = detail.case.actions
  return (
    <div className="tabbody">
      <h3 className="block__title">{t('console.actions.title')}</h3>
      {actions.length === 0 ? (
        <p className="block__empty">{t('console.actions.none')}</p>
      ) : (
        <ol className="evlist">
          {actions.map((a, i) => (
            <li key={`${a.tool}-${i}`} className="evrec">
              <div className="evrec__head">
                <SourceBadge kind="tool" /> <code className="mono">{a.tool}</code>
                {a.verified_at ? (
                  <span className="tag tag--verified">{t('console.verifiedAt', { when: dateTime(a.verified_at, locale) })}</span>
                ) : (
                  <span className="tag tag--unverified">{t('card.unverified')}</span>
                )}
              </div>
              <Fields fields={{ args: a.args, result: a.result }} />
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function HistoryTab({ detail }: { detail: AnalystCaseDetail }) {
  const { t, locale } = useI18n()
  const c = detail.case
  const d = c.analyst_decision
  return (
    <div className="tabbody">
      <h3 className="block__title">{t('console.history.case')}</h3>
      <dl className="facts">
        <div className="facts__row">
          <dt>{t('card.created')}</dt>
          <dd>{dateTime(c.created_at, locale)}</dd>
        </div>
        <div className="facts__row">
          <dt>{t('console.history.updated')}</dt>
          <dd>{dateTime(c.updated_at, locale)}</dd>
        </div>
        <div className="facts__row">
          <dt>{t('console.history.channel')}</dt>
          <dd>{c.channel}</dd>
        </div>
        <div className="facts__row">
          <dt>{t('console.version')}</dt>
          <dd className="mono">{detail.version}</dd>
        </div>
      </dl>
      <ClockTimeline c={c} />
      {d && (
        <>
          <h3 className="block__title">{t('console.history.decision')}</h3>
          <p>
            <SourceBadge kind="human" /> {t(`console.action.${d.action}`)} · {t('console.decidedBy', { who: d.decided_by, time: dateTime(d.decided_at, locale) })}
          </p>
          {d.reason && <p className="small">{t('console.history.reason', { reason: neutralize(d.reason) })}</p>}
        </>
      )}
      {c.resolution && (
        <>
          <h3 className="block__title">{t('console.history.sent')}</h3>
          <figure className="statement">
            <figcaption>
              <SourceBadge kind="human" label={t('chat.resolutionBadge')} /> {dateTime(c.resolution.sent_at, locale)}
            </figcaption>
            <blockquote lang={c.resolution.language === 'pt' ? 'pt-BR' : 'es'}>{neutralize(c.resolution.text)}</blockquote>
          </figure>
        </>
      )}
      {c.labels_emitted.length > 0 && (
        <>
          <h3 className="block__title">{t('console.history.labels')}</h3>
          <ul className="chips-inline">
            {c.labels_emitted.map((l) => (
              <li key={l} className="tag mono">
                {l}
              </li>
            ))}
          </ul>
        </>
      )}
      <h3 className="block__title">{t('console.history.prior')}</h3>
      {unavailable(detail, 'prior_contacts') || detail.context.prior_contacts.length === 0 ? (
        <EmptyOrUnavailable unreadable={unavailable(detail, 'prior_contacts')} emptyKey="console.history.noPrior" />
      ) : (
        <ul className="plainlist">
          {detail.context.prior_contacts.map((p) => (
            <li key={p.contact_id}>
              <span className="mono small">{p.contact_id}</span> · {formatDate(p.date, locale)} · {p.channel} · {p.contact_type}
              {p.subcategory ? ` · ${p.subcategory}` : ''} · {p.status}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function TraceTab({ detail }: { detail: AnalystCaseDetail }) {
  const { t } = useI18n()
  const c = detail.case
  const r = detail.report
  return (
    <div className="tabbody">
      <h3 className="block__title">{t('console.trace.title')}</h3>
      <dl className="facts">
        <div className="facts__row">
          <dt>trace_id</dt>
          <dd className="mono">{c.trace_id ?? '—'}</dd>
        </div>
        <div className="facts__row">
          <dt>{t('trace.rule')}</dt>
          <dd className="mono">
            {c.lane_reason ?? '—'}
            {c.rules_version && <span className="muted"> · {t('trace.rulesVersion', { v: c.rules_version })}</span>}
          </dd>
        </div>
        {c.intent && (
          <div className="facts__row">
            <dt>{t('console.trace.intentModel')}</dt>
            <dd className="mono">{c.intent.model_version ?? t('trace.noModel')}</dd>
          </div>
        )}
        {c.match && (
          <div className="facts__row">
            <dt>{t('console.trace.matchModel')}</dt>
            <dd className="mono">{c.match.model_version ?? t('trace.noModel')}</dd>
          </div>
        )}
        {r && (
          <>
            <div className="facts__row">
              <dt>{t('console.trace.investigator')}</dt>
              <dd className="mono">
                {r.model_id || '—'} · {r.prompt_version}
              </dd>
            </div>
            <div className="facts__row">
              <dt>{t('console.trace.toolCalls')}</dt>
              <dd>{r.tool_calls}</dd>
            </div>
            <div className="facts__row">
              <dt>{t('console.trace.latency')}</dt>
              <dd>{r.latency_ms} ms</dd>
            </div>
            <div className="facts__row">
              <dt>{t('console.trace.citations')}</dt>
              <dd>
                {t(r.citations_valid ? 'console.trace.citationsOk' : 'console.trace.citationsBad')} ·{' '}
                {t('console.trace.removed', { n: r.removed_claims })}
              </dd>
            </div>
          </>
        )}
      </dl>
      <p className="block__note">{t('console.trace.note')}</p>
    </div>
  )
}
