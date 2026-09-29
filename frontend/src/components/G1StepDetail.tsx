// Detail line of the G1 extraction step (trace strip and trace view): model id, tokens and
// cost of the turn's model call, or the rule-based fallback when G1 gave nothing usable
// (schema_invalid, no_fixture, model_timeout...). The summary carries the turn's tokens and
// cost; G1 is the only model call of a chat turn, so they are G1's.

import type { TraceStep, TraceSummary } from '../api/types'
import { useI18n } from '../i18n'
import { costText, tokensText } from '../trace'
import { IconAlert } from './Icons'

export function G1StepDetail({ step, summary }: { step: TraceStep; summary: TraceSummary }) {
  const { t, locale } = useI18n()
  const tokens = tokensText(summary, locale, t)
  return (
    <>
      {summary.model_id && (
        <span className="trace__g1 mono" data-testid="g1-model">
          {t('trace.g1Model', {
            model: summary.model_id,
            tokens: tokens ?? t('trace.tokensValue', { in: '—', out: '—' }),
            cost: costText(summary, locale, t),
          })}
        </span>
      )}
      {step.error_code && (
        <span className="trace__fallback" data-testid="g1-fallback">
          <IconAlert size={12} /> {t('trace.g1Fallback')}
        </span>
      )}
    </>
  )
}
