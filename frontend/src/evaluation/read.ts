// Pure helpers of the evaluation page (#/evaluacion): defensive readers for the loose parts of
// the report (models.m1, m2, m3 follow the models' own report shapes, and M2 may still be
// training) and number formatting. No React, no i18n; the page never computes a metric, it
// only reads and formats what the report says.

export type Loose = Record<string, unknown>

export function obj(v: unknown): Loose | null {
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Loose) : null
}

export function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

export function str(v: unknown): string | null {
  return typeof v === 'string' && v.trim() !== '' ? v : null
}

export function bool(v: unknown): boolean | null {
  return typeof v === 'boolean' ? v : null
}

/** The value at a path of keys, or undefined when a step is missing or not an object. */
export function at(root: unknown, path: readonly string[]): unknown {
  let cur: unknown = root
  for (const key of path) {
    const o = obj(cur)
    if (!o || !Object.prototype.hasOwnProperty.call(o, key)) return undefined
    cur = o[key]
  }
  return cur
}

/** The first finite number found at one of the paths, in order. */
export function firstNum(root: unknown, paths: readonly (readonly string[])[]): number | null {
  for (const p of paths) {
    const v = num(at(root, p))
    if (v !== null) return v
  }
  return null
}

/** A [low, high] interval of two finite numbers, else null. */
export function interval(v: unknown): [number, number] | null {
  if (!Array.isArray(v) || v.length !== 2) return null
  const lo = num(v[0])
  const hi = num(v[1])
  return lo !== null && hi !== null ? [lo, hi] : null
}

// ---------- M1: charge matcher, model vs the v1.4 rule (src/eval/report.py _m1_summary) ----------

export const M1_METRICS = ['top1', 'recall_at_3', 'wrong_confirmation_rate', 'confirm_rate', 'no_match_detection'] as const
export type M1Metric = (typeof M1_METRICS)[number]
const M1_CI: Partial<Record<M1Metric, string>> = { top1: 'top1_ci95', wrong_confirmation_rate: 'wrong_confirmation_ci95' }

export interface M1Value {
  value: number | null
  ci: [number, number] | null
}

export interface M1Summary {
  version: string | null
  primary: string | null
  confirmThreshold: number | null
  chooseThreshold: number | null
  queries: number | null
  rows: { metric: M1Metric; model: M1Value; rule: M1Value }[]
}

function m1Value(side: unknown, metric: M1Metric): M1Value {
  const ciKey = M1_CI[metric]
  return { value: num(at(side, [metric])), ci: ciKey ? interval(at(side, [ciKey])) : null }
}

export function m1Summary(m1: unknown): M1Summary | null {
  const o = obj(m1)
  if (!o) return null
  return {
    version: str(o.model_version),
    primary: str(o.primary),
    confirmThreshold: num(at(o, ['thresholds', 'confirm_threshold'])),
    chooseThreshold: num(at(o, ['thresholds', 'choose_threshold'])),
    queries: num(o.queries_test),
    rows: M1_METRICS.map((metric) => ({ metric, model: m1Value(o.m1, metric), rule: m1Value(o.rule, metric) })),
  }
}

// ---------- M2: intent classifier vs the keyword baseline ----------
// Only test-set macro-F1 is read. Today's report (m2-hashlr-1) has one block per system under
// test_set.systems ({m2, b1_keywords}: {overall: {macro_f1}, by_language: {es: {macro_f1}}}, the
// shape of evaluate_predictions); the other layouts are kept so a reshaped report still shows:
// the same blocks elsewhere, a flat {macro_f1}, or by_language first ({es: {m2: {...}}}).
// Validation numbers (metrics.validation_cross_fitted, cross_generator) are never shown as test
// numbers.

const MODEL_BLOCKS: readonly (readonly string[])[] = [
  ['test_set', 'systems', 'm2'],
  ['test_set', 'systems', 'model'],
  ['systems', 'm2'],
  ['systems', 'model'],
  [],
  ['metrics'],
  ['metrics', 'test'],
  ['metrics', 'm2'],
  ['metrics', 'model'],
  ['metrics', 'test', 'm2'],
  ['metrics', 'test', 'model'],
  ['test'],
  ['test', 'm2'],
  ['test', 'model'],
]
const KEYWORD_BLOCKS: readonly (readonly string[])[] = [
  ['test_set', 'systems', 'b1_keywords'],
  ['test_set', 'systems', 'keywords'],
  ['systems', 'b1_keywords'],
  ['systems', 'keywords'],
  ['baseline_keywords'],
  ['baseline_keywords', 'test'],
  ['metrics', 'keywords'],
  ['metrics', 'baseline_keywords'],
  ['metrics', 'b1_keywords'],
  ['metrics', 'test', 'keywords'],
  ['metrics', 'test', 'baseline_keywords'],
  ['test', 'keywords'],
  ['test', 'baseline_keywords'],
  ['test', 'b1_keywords'],
]
const MODEL_NAMES = ['m2', 'model']
const KEYWORD_NAMES = ['keywords', 'baseline_keywords', 'b1_keywords']

function overallPaths(blocks: readonly (readonly string[])[]): string[][] {
  return blocks.flatMap((b) => [[...b, 'overall', 'macro_f1'], [...b, 'macro_f1']])
}

function languagePaths(blocks: readonly (readonly string[])[], names: string[], lang: string): string[][] {
  return [
    ...blocks.flatMap((b) => [[...b, 'by_language', lang, 'macro_f1'], [...b, lang, 'macro_f1']]),
    ...names.flatMap((n) => [
      ['by_language', lang, n, 'macro_f1'],
      ['by_language', n, lang, 'macro_f1'],
    ]),
  ]
}

export interface M2Cell {
  model: number | null
  keywords: number | null
}

export interface M2Summary {
  version: string | null
  /** Size of the test set and how many of its messages are adversarial. */
  n: number | null
  nAdversarial: number | null
  note: string | null
  acceptThreshold: number | null
  askThreshold: number | null
  rows: { group: 'overall' | 'es' | 'pt'; cell: M2Cell }[]
  /** Bootstrap 95 % intervals of the overall macro-F1, and of the paired difference M2 - keywords. */
  ci: { model: [number, number] | null; keywords: [number, number] | null; difference: [number, number] | null }
  /** At least one macro-F1 was found. */
  hasMetrics: boolean
}

function firstInterval(root: unknown, paths: readonly (readonly string[])[]): [number, number] | null {
  for (const p of paths) {
    const v = interval(at(root, p))
    if (v) return v
  }
  return null
}

const bootstrapPaths = (names: string[]) =>
  names.flatMap((n) => [
    ['test_set', 'bootstrap', n, 'macro_f1_ci95'],
    ['bootstrap', n, 'macro_f1_ci95'],
  ])

export function m2Summary(m2: unknown): M2Summary | null {
  const o = obj(m2)
  if (!o) return null
  const overall: M2Cell = { model: firstNum(o, overallPaths(MODEL_BLOCKS)), keywords: firstNum(o, overallPaths(KEYWORD_BLOCKS)) }
  const byLang = (lang: string): M2Cell => ({
    model: firstNum(o, languagePaths(MODEL_BLOCKS, MODEL_NAMES, lang)),
    keywords: firstNum(o, languagePaths(KEYWORD_BLOCKS, KEYWORD_NAMES, lang)),
  })
  const rows: M2Summary['rows'] = [
    { group: 'overall', cell: overall },
    { group: 'es', cell: byLang('es') },
    { group: 'pt', cell: byLang('pt') },
  ]
  const version = str(o.model_version) ?? str(at(o, ['model', 'model_version'])) ?? str(at(o, ['thresholds', 'model_version']))
  return {
    version,
    n: num(at(o, ['test_set', 'n'])),
    nAdversarial: num(at(o, ['test_set', 'n_adversarial'])),
    note: str(at(o, ['test_set', 'note'])),
    acceptThreshold: num(at(o, ['thresholds', 'accept_threshold'])),
    askThreshold: num(at(o, ['thresholds', 'ask_threshold'])),
    rows,
    ci: {
      model: firstInterval(o, bootstrapPaths(MODEL_NAMES)),
      keywords: firstInterval(o, bootstrapPaths(KEYWORD_NAMES)),
      difference: firstInterval(o, bootstrapPaths(['difference'])),
    },
    hasMetrics: rows.some((r) => r.cell.model !== null || r.cell.keywords !== null),
  }
}

// ---------- M3: risk evidence, lift experiment over fraud_score ----------

export interface M3Summary {
  version: string | null
  liftSignificant: boolean | null
  prAucModel: number | null
  prAucFraudScore: number | null
  diffCi: [number, number] | null
  decision: string | null
}

export function m3Summary(m3: unknown): M3Summary | null {
  const o = obj(m3)
  if (!o) return null
  return {
    version: str(o.model_version),
    liftSignificant: bool(o.lift_significant),
    prAucModel: num(at(o, ['test', 'pr_auc_model'])),
    prAucFraudScore: num(at(o, ['test', 'pr_auc_fraud_score'])),
    diffCi: interval(o.pr_auc_diff_ci95),
    decision: str(o.decision),
  }
}

// ---------- formatting ----------

/** A rate (0..1) as a percentage: one decimal; two below 10 %, where targets like "< 1%" live
 *  (0,35 % against 2,88 %). */
export function formatPercent(v: number, locale: string): string {
  const digits = v !== 0 && Math.abs(v) < 0.1 ? 2 : 1
  return new Intl.NumberFormat(locale, { style: 'percent', minimumFractionDigits: 0, maximumFractionDigits: digits }).format(v)
}

export function formatDecimal(v: number, locale: string, maxDigits = 2): string {
  return new Intl.NumberFormat(locale, { minimumFractionDigits: 0, maximumFractionDigits: maxDigits }).format(v)
}

/** A small score (PR-AUC, macro-F1) with enough significant digits to compare (0,0023). */
export function formatScore(v: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumSignificantDigits: 3 }).format(v)
}

/** generated_at (ISO, UTC) as "30 oct 2026, 04:37 UTC"; the raw text if it is not a date. */
export function formatUtc(iso: string, locale: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(d)} UTC`
}
