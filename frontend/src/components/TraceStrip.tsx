// Per-turn trace strip from trace_summary (RF-15): actors, rule and version, server and
// client latency, model id or "sin modelo", trace_id. No free text is ever in the trace.

import { useEffect, useRef, useState } from 'react'
import { formatMs } from '../format'
import { hasKey, useI18n } from '../i18n'
import { hrefFor } from '../router'
import type { TurnSnapshot } from '../session'
import type { TraceStep } from '../api/types'
import { actorKind, costText, isG1Step, tokensText } from '../trace'
import { G1StepDetail } from './G1StepDetail'
import { IconAlert, IconCopy } from './Icons'
import { SourceBadge } from './SourceBadge'

export function TraceStrip({ snapshot }: { snapshot: TurnSnapshot | null }) {
  const { t, locale } = useI18n()
  const [copied, setCopied] = useState(false)
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (copiedTimer.current) clearTimeout(copiedTimer.current)
    },
    [],
  )

  if (!snapshot) {
    return (
      <section className="card trace" aria-labelledby="trace-title">
        <header className="card__header">
          <h2 id="trace-title" className="card__title">
            {t('trace.title')}
          </h2>
        </header>
        <p className="block__empty">{t('trace.empty')}</p>
      </section>
    )
  }

  const s = snapshot.trace_summary
  // The exact criterion of a sensitive rule (threshold, history) is shown to the judge here,
  // never in the customer's card (SEC-07).
  const detailKey = s.rule_id ? `judgeRule.${s.rule_id}` : ''
  const ruleDetail = detailKey && hasKey(detailKey) ? t(detailKey) : null
  const tokens = tokensText(s, locale, t)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snapshot.trace_id)
      setCopied(true)
      // One timer at a time: a second click restarts the 1.5 s instead of being cut short.
      if (copiedTimer.current) clearTimeout(copiedTimer.current)
      copiedTimer.current = setTimeout(() => {
        copiedTimer.current = null
        setCopied(false)
      }, 1500)
    } catch {
      // Clipboard blocked: the id stays selectable as text.
    }
  }

  return (
    <section className="card trace" aria-labelledby="trace-title">
      <header className="card__header">
        <h2 id="trace-title" className="card__title">
          {t('trace.title')}
        </h2>
        <span className="tag">{t('trace.turn', { n: snapshot.turn })}</span>
      </header>

      <ol className="trace__steps" aria-label={t('trace.steps')}>
        {s.steps.map((step, i) => (
          <li key={`${step.name}-${i}`} className={`trace__step ${stepClass(step)}`}>
            <SourceBadge kind={actorKind(step)} />
            <span className="trace__name mono">{step.name}</span>
            <span className="trace__lat">{formatMs(step.latency_ms, locale)}</span>
            {step.version && <span className="trace__ver mono">{step.version}</span>}
            {step.error_code && (
              <span className="trace__err">
                <IconAlert size={12} /> {t('trace.error', { code: step.error_code })}
              </span>
            )}
            {isG1Step(step) && <G1StepDetail step={step} summary={s} />}
          </li>
        ))}
      </ol>

      <dl className="trace__summary">
        <div>
          <dt>{t('trace.server')}</dt>
          <dd>{formatMs(s.latency_ms, locale)}</dd>
        </div>
        <div>
          <dt>{t('trace.client')}</dt>
          <dd>{formatMs(snapshot.client_latency_ms, locale)}</dd>
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
          <dt>{t('trace.rule')}</dt>
          <dd>
            {s.rule_id ? <span className="mono">{s.rule_id}</span> : <span className="muted">{t('trace.noRule')}</span>}
            {s.rules_version && <span className="muted"> · {t('trace.rulesVersion', { v: s.rules_version })}</span>}
          </dd>
        </div>
        {ruleDetail && (
          <div className="trace__ruledetail">
            <dt>{t('trace.ruleDetail')}</dt>
            <dd>{ruleDetail}</dd>
          </div>
        )}
        <div>
          <dt>{t('trace.cost')}</dt>
          <dd>{costText(s, locale, t)}</dd>
        </div>
        <div className="trace__id">
          <dt>{t('trace.id')}</dt>
          <dd>
            <code className="mono">{snapshot.trace_id}</code>
            <button type="button" className="btn btn--ghost btn--icon" onClick={copy} aria-label={t('trace.copy')}>
              <IconCopy size={14} />
            </button>
            <span role="status" className="trace__copied">
              {copied ? t('trace.copied') : ''}
            </span>
          </dd>
        </div>
      </dl>
      {snapshot.degraded.length > 0 && (
        <ul className="trace__degraded">
          {snapshot.degraded.map((d) => (
            <li key={d}>
              <IconAlert size={12} /> {t(d === 'model_timeout' ? 'degraded.model_timeout' : 'degraded.tool_unavailable')}
            </li>
          ))}
        </ul>
      )}
      <a className="trace__full" href={hrefFor({ name: 'trace', traceId: snapshot.trace_id })}>
        {t('trace.fullView')}
      </a>
    </section>
  )
}

/** A G1 step with an error is a fallback (the rules answered), not a failed turn. */
function stepClass(step: TraceStep): string {
  if (!step.error_code) return ''
  return isG1Step(step) ? 'trace__step--fallback' : 'trace__step--error'
}
