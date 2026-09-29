// Origin labels shared by every screen: Regla, ML, GenAI, Herramienta, Humano, Plantilla.
// The contract's TraceActor has a single "model" value; ML (classifier/ranker) and GenAI
// (G1 extraction, G2 investigator, G3 judge) are told apart by the step name or version.

import type { TraceStep, TraceSummary } from './api/types'
import { formatUsd } from './format'
import type { I18nValue } from './i18n'

export type OriginKind = 'rule' | 'ml' | 'genai' | 'tool' | 'human' | 'template'

const GENAI_HINT = /(extract|generat|draft|reply|compose|investigat|judge|summar|llm|\bg[123]\b|claude|anthropic|haiku|sonnet|opus|prompt)/i

export function actorKind(step: Pick<TraceStep, 'actor' | 'name' | 'version'>): OriginKind {
  switch (step.actor) {
    case 'rule':
      return 'rule'
    case 'tool':
      return 'tool'
    case 'human':
      return 'human'
    case 'model':
      return GENAI_HINT.test(step.name) || GENAI_HINT.test(step.version ?? '') ? 'genai' : 'ml'
    default:
      return 'tool'
  }
}

/** The G1 extraction step (the model reads the redacted message and only extracts data). */
export const G1_STEP = 'g1_extract'

export function isG1Step(step: Pick<TraceStep, 'actor' | 'name'>): boolean {
  return step.actor === 'model' && step.name === G1_STEP
}

export function tokensText(s: Pick<TraceSummary, 'tokens_in' | 'tokens_out'>, locale: string, t: I18nValue['t']): string | null {
  if (s.tokens_in == null && s.tokens_out == null) return null
  const n = (v: number | null | undefined) => (v == null ? '—' : new Intl.NumberFormat(locale).format(v))
  return t('trace.tokensValue', { in: n(s.tokens_in), out: n(s.tokens_out) })
}

/** Cost, marked "estimado" for the local mock provider (list prices, no real call). */
export function costText(s: Pick<TraceSummary, 'cost_usd' | 'model_id'>, locale: string, t: I18nValue['t']): string {
  const cost = formatUsd(s.cost_usd, locale)
  return s.model_id?.startsWith('mock') ? `${cost} (${t('trace.estimated')})` : cost
}
