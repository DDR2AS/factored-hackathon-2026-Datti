// Trace view (#/traza and #/traza/<trace_id>), RF-23. Lists the turns of this tab's chat
// session from the transcript in sessionStorage (no new API route): one row per step of
// trace_summary with actor, name, latency, version and error_code, plus rule, rules version
// and degradations. Everything is text; the trace carries no free text by contract.

import { useEffect, useMemo } from 'react'
import { Footer, LanguageSwitch, SyntheticBanner, TopBar } from '../components/Chrome'
import { IconAlert, IconBack } from '../components/Icons'
import { SourceBadge } from '../components/SourceBadge'
import { DEMO_CUSTOMERS } from '../demo/customers'
import { formatMs } from '../format'
import { hasKey, useI18n } from '../i18n'
import { hrefFor } from '../router'
import { loadAnyTranscript, type TraceRecord } from '../session'
import { actorKind, costText, isG1Step, tokensText } from '../trace'
import { G1StepDetail } from '../components/G1StepDetail'
import { hhmm } from '../console/format'
import '../styles/views.css'
import '../styles/console.css'

export function TraceView({ traceId }: { traceId: string | null }) {
  const { t, locale } = useI18n()
  const transcript = useMemo(() => loadAnyTranscript(), [])
  const records: TraceRecord[] = useMemo(() => {
    if (!transcript) return []
    if (Array.isArray(transcript.traces) && transcript.traces.length > 0) return transcript.traces
    const last = transcript.last
    // Transcripts saved before the trace list existed only kept the last turn.
    return last
      ? [
          {
            turn: last.turn,
            trace_id: last.trace_id,
            at: '',
            trace_summary: last.trace_summary,
            degraded: last.degraded,
            lane: last.lane,
            client_latency_ms: last.client_latency_ms,
          },
        ]
      : []
  }, [transcript])
  const customer = transcript ? (DEMO_CUSTOMERS.find((c) => c.key === transcript.demo_key) ?? null) : null
  const missing = traceId !== null && !records.some((r) => r.trace_id === traceId)

  useEffect(() => {
    if (!traceId) return
    document.getElementById(`turn-${traceId}`)?.scrollIntoView?.({ block: 'start' })
  }, [traceId])

  return (
    <div className="page">
      <TopBar>
        <LanguageSwitch />
        <a className="btn btn--ghost btn--small" href={customer ? hrefFor({ name: 'chat', demoKey: customer.key }) : '#/'}>
          <IconBack size={14} /> <span className="btn__label">{t(customer ? 'traceView.backChat' : 'soon.back')}</span>
        </a>
      </TopBar>
      <SyntheticBanner />
      <main className="traceview" id="main" tabIndex={-1}>
        <header className="section-head">
          <h1>{t('traceView.title')}</h1>
          <p className="muted">
            {customer
              ? t(records.length === 1 ? 'traceView.subtitleOne' : 'traceView.subtitle', {
                  name: customer.nameKey ? t(customer.nameKey) : customer.firstName,
                  n: records.length,
                })
              : t('traceView.subtitleNone')}
          </p>
          <p className="block__note">{t('traceView.scope')}</p>
        </header>

        {missing && (
          <p className="notice notice--warn" role="status">
            <IconAlert size={14} /> {t('traceView.notInTab', { id: traceId ?? '' })}
          </p>
        )}

        {records.length === 0 ? (
          <div className="card empty-state">
            <p>
              <strong>{t('traceView.empty')}</strong>
            </p>
            <p className="muted">{t('traceView.emptyHint')}</p>
            <a className="btn btn--primary" href="#/">
              {t('soon.back')}
            </a>
          </div>
        ) : (
          <ol className="turns">
            {records.map((r) => (
              <TurnCard key={r.trace_id} record={r} selected={r.trace_id === traceId} locale={locale} />
            ))}
          </ol>
        )}
      </main>
      <Footer />
    </div>
  )
}

function TurnCard({ record: r, selected, locale }: { record: TraceRecord; selected: boolean; locale: string }) {
  const { t } = useI18n()
  const s = r.trace_summary
  const detailKey = s.rule_id ? `judgeRule.${s.rule_id}` : ''
  const tokens = tokensText(s, locale, t)
  return (
    <li id={`turn-${r.trace_id}`} className={`card turn ${selected ? 'turn--selected' : ''}`} aria-current={selected ? 'true' : undefined}>
      <header className="card__header">
        <h2 className="card__title">
          {t('trace.turn', { n: r.turn })}
          {r.at && <span className="muted small"> · {hhmm(r.at, locale)}</span>}
        </h2>
        <code className="mono small">{r.trace_id}</code>
      </header>
      <dl className="trace__summary">
        <div>
          <dt>{t('trace.rule')}</dt>
          <dd>
            {s.rule_id ? <span className="mono">{s.rule_id}</span> : <span className="muted">{t('trace.noRule')}</span>}
            {s.rules_version && <span className="muted"> · {t('trace.rulesVersion', { v: s.rules_version })}</span>}
          </dd>
        </div>
        {detailKey && hasKey(detailKey) && (
          <div className="trace__ruledetail">
            <dt>{t('trace.ruleDetail')}</dt>
            <dd>{t(detailKey)}</dd>
          </div>
        )}
        <div>
          <dt>{t('card.lane')}</dt>
          <dd>{r.lane ? t('lane.label', { lane: r.lane }) : '—'}</dd>
        </div>
        <div>
          <dt>{t('trace.server')}</dt>
          <dd>{formatMs(s.latency_ms, locale)}</dd>
        </div>
        <div>
          <dt>{t('trace.client')}</dt>
          <dd>{formatMs(r.client_latency_ms, locale)}</dd>
        </div>
        <div>
          <dt>{t('trace.model')}</dt>
          <dd className={s.model_id ? 'mono' : 'muted'}>{s.model_id ?? t('trace.noModel')}</dd>
        </div>
        {tokens && (
          <div>
            <dt>{t('trace.tokens')}</dt>
            <dd>{tokens}</dd>
          </div>
        )}
        <div>
          <dt>{t('trace.cost')}</dt>
          <dd>{costText(s, locale, t)}</dd>
        </div>
      </dl>
      <div className="table-wrap">
        <table className="table">
          <caption className="sr-only">{t('traceView.steps', { n: r.turn })}</caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">{t('traceView.actor')}</th>
              <th scope="col">{t('traceView.name')}</th>
              <th scope="col" className="num">
                {t('traceView.latency')}
              </th>
              <th scope="col">{t('traceView.version')}</th>
              <th scope="col">error_code</th>
            </tr>
          </thead>
          <tbody>
            {s.steps.map((step, i) => (
              <tr
                key={`${step.name}-${i}`}
                className={step.error_code ? (isG1Step(step) ? 'row--fallback' : 'row--error') : undefined}
              >
                <td>{i + 1}</td>
                <td>
                  <SourceBadge kind={actorKind(step)} />
                </td>
                <td className="mono">
                  {step.name}
                  {isG1Step(step) && (
                    <span className="trace__g1-cell">
                      <G1StepDetail step={step} summary={s} />
                    </span>
                  )}
                </td>
                <td className="num">{formatMs(step.latency_ms, locale)}</td>
                <td className="mono small">{step.version ?? '—'}</td>
                <td className="mono small">
                  {step.error_code ? (
                    <span className="trace__err">
                      <IconAlert size={12} /> {step.error_code}
                    </span>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {r.degraded.length > 0 && (
        <ul className="trace__degraded">
          {r.degraded.map((d) => (
            <li key={d}>
              <IconAlert size={12} /> {t(d === 'model_timeout' ? 'degraded.model_timeout' : 'degraded.tool_unavailable')}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}
