// Evaluation view (#/evaluacion): the latest `make eval` report, from GET /evaluation (public, no
// token). Read-only, like the trace view. Every number comes from the report
// (src/eval/report.py): the page formats it and shows "—" for a null, it never computes one.
// Fixed metric names are translated by metric id; targets, notes and limits are the report's own
// Spanish text, shown as is (marked lang="es" when the page is in Portuguese).

import { useEffect, useState, type ReactNode } from 'react'
import { useApi } from '../api/context'
import { isApiClientError, type ClientErrorCode } from '../api/errors'
import type {
  EvaluationBaseline,
  EvaluationConfig,
  EvaluationFairness,
  EvaluationGap,
  EvaluationGroup,
  EvaluationHeadline,
  EvaluationMetric,
  EvaluationModels,
  EvaluationProbes,
  EvaluationReport,
  EvaluationSecurity,
} from '../api/types'
import { errorMessageKey } from '../chat/errors'
import { Footer, LanguageSwitch, SyntheticBanner, TopBar } from '../components/Chrome'
import { IconAlert, IconBack, IconCheck, IconClock, IconRefresh, IconX } from '../components/Icons'
import {
  formatDecimal,
  formatPercent,
  formatScore,
  formatUtc,
  interval,
  m1Summary,
  m2Summary,
  m3Summary,
  type M1Value,
} from '../evaluation/read'
import { formatCount, formatDate, formatMs, formatUsd } from '../format'
import { hasKey, useI18n, type MessageKey } from '../i18n'
import '../styles/views.css'
import '../styles/console.css'
import '../styles/evaluation.css'

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; code: ClientErrorCode }
  | { kind: 'unavailable' }
  | { kind: 'ready'; report: EvaluationReport }

export function EvaluationView() {
  const { t } = useI18n()
  const api = useApi()
  const [state, setState] = useState<LoadState>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const ctrl = new AbortController()
    api.getEvaluation({ signal: ctrl.signal }).then(
      (res) => {
        if (ctrl.signal.aborted) return
        const report = res && res.available === true && res.report && typeof res.report === 'object' ? res.report : null
        setState(report ? { kind: 'ready', report } : { kind: 'unavailable' })
      },
      (e: unknown) => {
        if (ctrl.signal.aborted) return
        setState({ kind: 'error', code: isApiClientError(e) ? e.code : 'unexpected' })
      },
    )
    return () => ctrl.abort()
  }, [api, attempt])

  const retry = () => {
    setState({ kind: 'loading' })
    setAttempt((n) => n + 1)
  }

  return (
    <div className="page">
      <TopBar>
        <LanguageSwitch />
        <a className="btn btn--ghost btn--small" href="#/">
          <IconBack size={14} /> <span className="btn__label">{t('soon.back')}</span>
        </a>
      </TopBar>
      <SyntheticBanner />
      <main className="evalview" id="main" tabIndex={-1}>
        <header className="evalview__head">
          <h1>{t('eval.title')}</h1>
          {state.kind === 'ready' && <Intro report={state.report} />}
        </header>
        {state.kind === 'loading' && (
          <p className="page-loading" role="status">
            <span className="spinner" aria-hidden="true" /> {t('eval.loading')}
          </p>
        )}
        {state.kind === 'error' && <LoadError code={state.code} onRetry={retry} />}
        {state.kind === 'unavailable' && (
          <section className="card evunavailable" aria-labelledby="ev-unavailable">
            <h2 id="ev-unavailable" className="card__title">
              {t('eval.unavailable.title')}
            </h2>
            <p className="muted">{t('eval.unavailable.body')}</p>
            <a className="btn btn--primary" href="#/">
              {t('soon.back')}
            </a>
          </section>
        )}
        {state.kind === 'ready' && <ReportBody report={state.report} />}
      </main>
      <Footer />
    </div>
  )
}

function LoadError({ code, onRetry }: { code: ClientErrorCode; onRetry: () => void }) {
  const { t } = useI18n()
  return (
    <div className="notice notice--error" role="alert">
      <IconAlert size={16} />
      <div>
        <p>
          <strong>{t('eval.error')}</strong> {t(errorMessageKey(code))}
        </p>
      </div>
      <button type="button" className="btn btn--ghost btn--small" onClick={onRetry}>
        <IconRefresh size={14} /> {t('eval.retry')}
      </button>
    </div>
  )
}

function ReportBody({ report }: { report: EvaluationReport }) {
  return (
    <>
      <HeadlineSection headline={report.headline} configs={report.configs} />
      <LatencySection report={report} />
      <FairnessSection fairness={report.fairness} />
      <ModelsSection models={report.models} />
      <SecuritySection security={report.security} />
      <BaselineSection rows={report.baseline_b0} />
      <LimitsSection limits={report.limits} />
    </>
  )
}

// ---------- small pieces ----------

/** "—" on screen, "sin dato" for a screen reader. */
function Dash() {
  const { t } = useI18n()
  return (
    <>
      <span aria-hidden="true">—</span>
      <span className="sr-only">{t('eval.na')}</span>
    </>
  )
}

/** Text written by the report (Spanish), marked as such when the page is in Portuguese. */
function Data({ text }: { text: string | null | undefined }) {
  const { lang } = useI18n()
  if (!text) return <Dash />
  return lang === 'es' ? <>{text}</> : <span lang="es">{text}</span>
}

function Rate({ value }: { value: number | null | undefined }) {
  const { locale } = useI18n()
  return value === null || value === undefined || !Number.isFinite(value) ? <Dash /> : <>{formatPercent(value, locale)}</>
}

function Count({ value }: { value: number | null | undefined }) {
  const { locale } = useI18n()
  return value === null || value === undefined ? <Dash /> : <>{formatCount(value, locale)}</>
}

/** A table in its scroll wrapper. The description shows above the scroll area (a caption would
 *  scroll away with a wide table on a phone) and stays the table's caption for screen readers. */
function Table({
  caption,
  showCaption = true,
  wide = false,
  children,
}: {
  caption: string
  showCaption?: boolean
  wide?: boolean
  children: ReactNode
}) {
  return (
    <>
      {showCaption && (
        <p className="evnote" aria-hidden="true">
          {caption}
        </p>
      )}
      <div className="table-wrap">
        <table className={wide ? 'table evtable evtable--wide' : 'table evtable'}>
          <caption className="sr-only">{caption}</caption>
          {children}
        </table>
      </div>
    </>
  )
}

function groupName(t: ReturnType<typeof useI18n>['t'], code: string): string {
  const key = `eval.group.${code}`
  return hasKey(key) ? t(key) : code
}

// ---------- header ----------

function Intro({ report }: { report: EvaluationReport }) {
  const { t, locale } = useI18n()
  const s = report.scenarios
  const list = (m: Record<string, number> | null | undefined) =>
    Object.entries(m ?? {})
      .map(([k, v]) => `${k.toUpperCase()} ${typeof v === 'number' ? formatCount(v, locale) : '—'}`)
      .join(', ')
  const summary =
    s && s.n !== null
      ? t('eval.summary', {
          n: formatCount(s.n, locale),
          adversarial: s.adversarial !== null ? formatCount(s.adversarial, locale) : '—',
          languages: list(s.by_language) || '—',
          countries: list(s.by_country) || '—',
        })
      : null
  const meta = [
    report.generated_at ? t('eval.generated', { date: formatUtc(report.generated_at, locale) }) : null,
    report.version ? t('eval.version', { v: report.version }) : null,
    report.gold_snapshot ? t('eval.gold', { date: formatDate(report.gold_snapshot, locale) }) : null,
  ].filter((x): x is string => !!x)
  return (
    <>
      {summary && <p className="evalview__summary">{summary}</p>}
      {meta.length > 0 && <p className="muted small">{meta.join(' · ')}</p>}
      <SourceNotice source={report.data_source} />
      <p className="block__note">{t('eval.dataNote')}</p>
    </>
  )
}

function SourceNotice({ source }: { source: string | null }) {
  const { t } = useI18n()
  if (source === 'synthetic_replica') {
    return (
      <p className="notice notice--warn" role="note">
        <IconAlert size={16} /> <strong>{t('eval.source.synthetic')}</strong>
      </p>
    )
  }
  const text =
    source === 'organizers_dataset'
      ? t('eval.source.organizers')
      : source
        ? t('eval.source.other', { source })
        : t('eval.source.unknown')
  return <p className="small">{text}</p>
}

// ---------- headline ----------

function HeadlineSection({ headline, configs }: { headline: EvaluationHeadline[] | null; configs: EvaluationConfig[] | null }) {
  const { t } = useI18n()
  const rows = headline ?? []
  const labels = (configs ?? []).map((c) => c.label).filter((l): l is string => !!l)
  return (
    <section className="card evsection" aria-labelledby="ev-headline">
      <h2 id="ev-headline" className="card__title">
        {t('eval.headline.title')}
      </h2>
      {labels.length > 0 && (
        <p className="evnote">
          {t('eval.configs')} <Data text={labels.join(' · ')} />
        </p>
      )}
      {rows.length === 0 ? (
        <p className="block__empty">{t('eval.headline.empty')}</p>
      ) : (
        <Table caption={t('eval.headline.caption')} wide>
          <thead>
            <tr>
              <th scope="col">{t('eval.col.metric')}</th>
              <th scope="col" className="num">
                {t('eval.col.v2')}
              </th>
              <th scope="col" className="num">
                {t('eval.col.b1')}
              </th>
              <th scope="col">{t('eval.col.target')}</th>
              <th scope="col">{t('eval.col.pass')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((h, i) => (
              <tr key={`${h.metric ?? 'metric'}-${i}`}>
                <th scope="row">
                  <MetricName row={h} />
                </th>
                <td className="num">
                  <MetricCell metric={h.v2} unit={h.unit} />
                </td>
                <td className="num">
                  <MetricCell metric={h.b1} unit={h.unit} />
                </td>
                <td>
                  <Data text={h.target} />
                </td>
                <td>
                  <PassBadge pass={h.pass} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  )
}

function MetricName({ row }: { row: EvaluationHeadline }) {
  const { t } = useI18n()
  const key = row.metric ? `eval.metric.${row.metric}` : ''
  if (key && hasKey(key)) return <>{t(key)}</>
  return <Data text={row.label ?? row.metric} />
}

function formatValue(v: number, unit: string | null, locale: string): string {
  return !unit || unit === 'ratio' ? formatPercent(v, locale) : `${formatDecimal(v, locale)} ${unit}`
}

function MetricCell({ metric, unit }: { metric: EvaluationMetric | null; unit: string | null }) {
  const { t, locale } = useI18n()
  const value = metric?.value
  if (!metric || value === null || value === undefined) return <Dash />
  const ci = interval(metric.ci95)
  return (
    <>
      <span className="evnum">{formatValue(value, unit, locale)}</span>
      {metric.k !== null && metric.n !== null && (
        <span className="evsub">{t('eval.kOfN', { k: formatCount(metric.k, locale), n: formatCount(metric.n, locale) })}</span>
      )}
      {ci && (
        <span className="evsub">
          {t('eval.ci', { lo: formatValue(ci[0], unit, locale), hi: formatValue(ci[1], unit, locale) })}
        </span>
      )}
    </>
  )
}

function PassBadge({ pass }: { pass: boolean | null }) {
  const { t } = useI18n()
  if (pass === true) {
    return (
      <span className="evbadge evbadge--pass">
        <IconCheck size={12} /> {t('eval.pass.yes')}
      </span>
    )
  }
  if (pass === false) {
    return (
      <span className="evbadge evbadge--fail">
        <IconX size={12} /> {t('eval.pass.no')}
      </span>
    )
  }
  return (
    <span className="evbadge evbadge--na">
      <span aria-hidden="true">—</span>
      <span className="sr-only">{t('eval.pass.na')}</span>
    </span>
  )
}

// ---------- latency and cost ----------

function LatencySection({ report }: { report: EvaluationReport }) {
  const { t, locale } = useI18n()
  const lat = report.latency
  const cost = report.cost
  const ms = (v: number | null | undefined) => (v === null || v === undefined ? null : formatMs(v, locale))
  const turns = lat && lat.turns !== null ? t('eval.lat.turns', { n: formatCount(lat.turns, locale) }) : null
  const investigations =
    lat && lat.investigations !== null ? t('eval.lat.investigations', { n: formatCount(lat.investigations, locale) }) : null
  const usd = (v: number | null | undefined) => (v === null || v === undefined ? null : formatUsd(v, locale))
  const projected = cost?.projected ?? null
  const tiles = [
    { id: 'turnP50', label: t('eval.lat.turnP50'), value: ms(lat?.turn_p50_ms), sub: turns },
    { id: 'turnP95', label: t('eval.lat.turnP95'), value: ms(lat?.turn_p95_ms), sub: turns },
    { id: 'invP50', label: t('eval.lat.invP50'), value: ms(lat?.investigator_p50_ms), sub: investigations },
    { id: 'invP95', label: t('eval.lat.invP95'), value: ms(lat?.investigator_p95_ms), sub: investigations },
    { id: 'cost', label: t('eval.cost.perConversation'), value: usd(cost?.per_conversation_usd), sub: t('eval.cost.estimated') },
    ...(projected
      ? [
          {
            id: 'projected',
            label: t('eval.cost.projected'),
            value: usd(projected.total_per_conversation_usd),
            sub: t('eval.cost.projection'),
          },
        ]
      : []),
  ]
  const projectedDetail =
    projected && projected.chat_per_conversation_usd !== null && projected.investigator_per_case_usd !== null
      ? t('eval.cost.projectedDetail', {
          chat: usd(projected.chat_per_conversation_usd) ?? '—',
          chatModel: projected.chat_model ?? '—',
          inv: usd(projected.investigator_per_case_usd) ?? '—',
          invModel: projected.investigator_model ?? '—',
          share:
            projected.share_of_conversations_investigated !== null
              ? formatPercent(projected.share_of_conversations_investigated, locale)
              : '—',
        })
      : null
  return (
    <section className="card evsection" aria-labelledby="ev-latency">
      <h2 id="ev-latency" className="card__title">
        {t('eval.latency.title')}
      </h2>
      <dl className="evtiles">
        {tiles.map((tile) => (
          <div key={tile.id} className="evtile">
            <dt>{tile.label}</dt>
            <dd>
              <span className="evtile__value">{tile.value ?? <Dash />}</span>
              {tile.sub && <span className="evsub">{tile.sub}</span>}
            </dd>
          </div>
        ))}
      </dl>
      <div className="evnotes">
        {lat?.target && (
          <p>
            <strong>{t('eval.latency.target')}</strong> <Data text={lat.target} />
          </p>
        )}
        {lat?.note && (
          <p>
            <Data text={lat.note} />
          </p>
        )}
        {cost?.target && (
          <p>
            <strong>{t('eval.cost.target')}</strong> <Data text={cost.target} />
          </p>
        )}
        {cost?.note && (
          <p>
            <Data text={cost.note} />
          </p>
        )}
        {projectedDetail && <p>{projectedDetail}</p>}
        {projected?.note && (
          <p>
            <Data text={projected.note} />
          </p>
        )}
      </div>
    </section>
  )
}

// ---------- fairness ----------

function FairnessSection({ fairness }: { fairness: EvaluationFairness | null }) {
  const { t } = useI18n()
  return (
    <section className="card evsection" aria-labelledby="ev-fairness">
      <h2 id="ev-fairness" className="card__title">
        {t('eval.fairness.title')}
      </h2>
      <div className="evstack">
        <GroupTable title={t('eval.fairness.byLanguage')} caption={t('eval.fairness.captionLanguage')} groups={fairness?.by_language ?? null} />
        <GroupTable title={t('eval.fairness.byCountry')} caption={t('eval.fairness.captionCountry')} groups={fairness?.by_country ?? null} />
      </div>
      <div className="evnotes">
        <p>{t('eval.fairness.autoNote')}</p>
        {fairness?.target && (
          <p>
            <strong>{t('eval.fairness.target')}</strong> <Data text={fairness.target} />
          </p>
        )}
      </div>
      {fairness?.gaps && fairness.gaps.length > 0 && <Gaps gaps={fairness.gaps} />}
    </section>
  )
}

// The gaps name their dimension and metric in the report's Spanish; the known ones are translated.
const GAP_DIMENSIONS: Record<string, MessageKey> = { idioma: 'eval.gap.language', 'país': 'eval.gap.country' }
const GAP_METRICS: Record<string, MessageKey> = {
  'ruta correcta': 'eval.col.lane',
  'cargo correcto': 'eval.col.charge',
  'resolución automática segura': 'eval.col.safeAuto',
  'resuelto cuando debía': 'eval.col.autoWhenExpected',
}

function Known({ text, keys }: { text: string | null; keys: Record<string, MessageKey> }) {
  const { t } = useI18n()
  const key = text ? keys[text] : undefined
  return key ? <>{t(key)}</> : <Data text={text} />
}

function Gaps({ gaps }: { gaps: EvaluationGap[] }) {
  const { t, locale } = useI18n()
  return (
    <>
      <h3>{t('eval.gaps.title')}</h3>
      <Table caption={t('eval.gaps.caption')} wide>
        <thead>
          <tr>
            <th scope="col">{t('eval.col.gapWhat')}</th>
            <th scope="col" className="num">
              {t('eval.col.gap')}
            </th>
            <th scope="col">{t('eval.col.gapBetween')}</th>
            <th scope="col">{t('eval.col.gapLimit')}</th>
            <th scope="col">{t('eval.col.explanation')}</th>
          </tr>
        </thead>
        <tbody>
          {gaps.map((g, i) => (
            <tr key={`${g.dimension ?? 'dim'}-${g.metric ?? 'metric'}-${i}`}>
              <th scope="row">
                <Known text={g.dimension} keys={GAP_DIMENSIONS} /> · <Known text={g.metric} keys={GAP_METRICS} />
              </th>
              <td className="num">
                {g.gap_points === null ? <Dash /> : t('eval.gap.points', { v: formatDecimal(g.gap_points, locale, 1) })}
              </td>
              <td>
                {g.gap_points === 0 ? (
                  t('eval.gap.equal')
                ) : g.max && g.min ? (
                  t('eval.gap.between', { max: groupName(t, g.max), min: groupName(t, g.min) })
                ) : (
                  <Dash />
                )}
              </td>
              <td>
                {g.within_limit === null ? (
                  <Dash />
                ) : (
                  <Outcome ok={g.within_limit} text={t(g.within_limit ? 'eval.gap.within' : 'eval.gap.outside')} />
                )}
              </td>
              <td>
                <Data text={g.explanation} />
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  )
}

function GroupTable({ title, caption, groups }: { title: string; caption: string; groups: Record<string, EvaluationGroup> | null }) {
  const { t, locale } = useI18n()
  const entries = Object.entries(groups ?? {})
  return (
    <div>
      <h3>{title}</h3>
      {entries.length === 0 ? (
        <p className="block__empty">{t('eval.fairness.none')}</p>
      ) : (
        <Table caption={caption} showCaption={false}>
          <thead>
            <tr>
              <th scope="col">{t('eval.col.group')}</th>
              <th scope="col" className="num">
                {t('eval.col.scenarios')}
              </th>
              <th scope="col" className="num">
                {t('eval.col.lane')}
              </th>
              <th scope="col" className="num">
                {t('eval.col.charge')}
              </th>
              <th scope="col" className="num">
                {t('eval.col.safeAuto')}
              </th>
              <th scope="col" className="num">
                {t('eval.col.autoWhenExpected')}
              </th>
              <th scope="col" className="num">
                {t('eval.col.wrongCharge')}
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([code, g]) => (
              <tr key={code}>
                <th scope="row">
                  {groupName(t, code)} <span className="mono small muted">{code}</span>
                </th>
                <td className="num">
                  <Count value={g?.n} />
                </td>
                <td className="num">
                  <Rate value={g?.lane_accuracy} />
                </td>
                <td className="num">
                  <Rate value={g?.charge_accuracy} />
                </td>
                <td className="num">
                  <Rate value={g?.safe_automated_resolution} />
                </td>
                <td className="num">
                  <Rate value={g?.auto_resolution_when_expected} />
                  {g && g.expected_a !== null && (
                    <span className="evsub">{t('eval.fairness.ofExpected', { n: formatCount(g.expected_a, locale) })}</span>
                  )}
                </td>
                <td className="num">
                  <Rate value={g?.unsafe_wrong_charge_filed} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  )
}

// ---------- models ----------

function ModelsSection({ models }: { models: EvaluationModels | null }) {
  const { t } = useI18n()
  return (
    <section className="card evsection" aria-labelledby="ev-models">
      <h2 id="ev-models" className="card__title">
        {t('eval.models.title')}
      </h2>
      <M1Block m1={models?.m1 ?? null} />
      <M2Block m2={models?.m2 ?? null} />
      <M3Block m3={models?.m3 ?? null} />
      <ComponentsBlock models={models} />
    </section>
  )
}

function ValueCi({ v }: { v: M1Value }) {
  const { t, locale } = useI18n()
  if (v.value === null) return <Dash />
  return (
    <>
      <span className="evnum">{formatPercent(v.value, locale)}</span>
      {v.ci && <span className="evsub">{t('eval.ci', { lo: formatPercent(v.ci[0], locale), hi: formatPercent(v.ci[1], locale) })}</span>}
    </>
  )
}

function M1Block({ m1 }: { m1: unknown }) {
  const { t, locale } = useI18n()
  const s = m1Summary(m1)
  const meta: string[] = []
  if (s?.version) meta.push(t('eval.version', { v: s.version }))
  if (s?.primary === 'model' || s?.primary === 'rule') meta.push(t(s.primary === 'model' ? 'eval.m1.primary.model' : 'eval.m1.primary.rule'))
  if (s && s.confirmThreshold !== null && s.chooseThreshold !== null) {
    meta.push(
      t('eval.m1.thresholds', {
        confirm: formatDecimal(s.confirmThreshold, locale),
        choose: formatDecimal(s.chooseThreshold, locale),
      }),
    )
  }
  return (
    <div className="evblock">
      <h3>{t('eval.m1.title')}</h3>
      {!s ? (
        <p className="block__empty">{t('eval.m1.none')}</p>
      ) : (
        <>
          {meta.length > 0 && <p className="evmeta">{meta.join(' · ')}</p>}
          <Table caption={s.queries !== null ? t('eval.m1.caption', { n: formatCount(s.queries, locale) }) : t('eval.m1.captionNoN')}>
            <thead>
              <tr>
                <th scope="col">{t('eval.col.metric')}</th>
                <th scope="col" className="num">
                  {t('eval.col.m1')}
                </th>
                <th scope="col" className="num">
                  {t('eval.col.rule')}
                </th>
              </tr>
            </thead>
            <tbody>
              {s.rows.map((r) => (
                <tr key={r.metric}>
                  <th scope="row">{t(`eval.m1.${r.metric}`)}</th>
                  <td className="num">
                    <ValueCi v={r.model} />
                  </td>
                  <td className="num">
                    <ValueCi v={r.rule} />
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </div>
  )
}

function M2Block({ m2 }: { m2: unknown }) {
  const { t, locale } = useI18n()
  const s = m2Summary(m2)
  const fmt = (v: number) => formatScore(v, locale)
  const score = (v: number | null, ci: [number, number] | null = null) =>
    v === null ? (
      <Dash />
    ) : (
      <>
        <span className="evnum">{fmt(v)}</span>
        {ci && <span className="evsub">{t('eval.ci', { lo: fmt(ci[0]), hi: fmt(ci[1]) })}</span>}
      </>
    )
  const meta: string[] = []
  if (s?.version) meta.push(t('eval.version', { v: s.version }))
  if (s && s.acceptThreshold !== null && s.askThreshold !== null) {
    meta.push(t('eval.m2.thresholds', { accept: formatDecimal(s.acceptThreshold, locale), ask: formatDecimal(s.askThreshold, locale) }))
  }
  return (
    <div className="evblock">
      <h3>{t('eval.m2.title')}</h3>
      {meta.length > 0 && <p className="evmeta">{meta.join(' · ')}</p>}
      {!s || !s.hasMetrics ? (
        <p className="evpending">
          <IconClock size={14} /> {t(s ? 'eval.m2.noMetrics' : 'eval.m2.pending')}
        </p>
      ) : (
        <>
          <Table caption={s.n !== null ? t('eval.m2.captionN', { n: formatCount(s.n, locale) }) : t('eval.m2.caption')}>
            <thead>
              <tr>
                <th scope="col">{t('eval.col.group')}</th>
                <th scope="col" className="num">
                  {t('eval.col.m2')}
                </th>
                <th scope="col" className="num">
                  {t('eval.col.keywords')}
                </th>
              </tr>
            </thead>
            <tbody>
              {s.rows.map((r) => (
                <tr key={r.group}>
                  <th scope="row">{r.group === 'overall' ? t('eval.m2.overall') : groupName(t, r.group)}</th>
                  <td className="num">{score(r.cell.model, r.group === 'overall' ? s.ci.model : null)}</td>
                  <td className="num">{score(r.cell.keywords, r.group === 'overall' ? s.ci.keywords : null)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          <div className="evnotes">
            {s.ci.difference && <p>{t('eval.m2.diff', { lo: fmt(s.ci.difference[0]), hi: fmt(s.ci.difference[1]) })}</p>}
            {s.nAdversarial !== null && <p>{t('eval.m2.adversarial', { n: formatCount(s.nAdversarial, locale) })}</p>}
            {s.note && (
              <p>
                <Data text={s.note} />
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function M3Block({ m3 }: { m3: unknown }) {
  const { t, locale } = useI18n()
  const s = m3Summary(m3)
  const decisionKey = s?.decision ? `eval.m3.decision.${s.decision}` : ''
  return (
    <div className="evblock">
      <h3>{t('eval.m3.title')}</h3>
      {!s ? (
        <p className="block__empty">{t('eval.m3.none')}</p>
      ) : (
        <dl className="evfacts">
          {s.version && (
            <>
              <dt>{t('eval.m3.version')}</dt>
              <dd className="mono">{s.version}</dd>
            </>
          )}
          <dt>{t('eval.m3.lift')}</dt>
          <dd>
            {s.liftSignificant === null ? (
              <Dash />
            ) : s.liftSignificant ? (
              <span className="evbadge evbadge--pass">
                <IconCheck size={12} /> {t('eval.m3.significant')}
              </span>
            ) : (
              <span className="evbadge evbadge--neutral">
                <IconX size={12} /> {t('eval.m3.notSignificant')}
              </span>
            )}
          </dd>
          <dt>{t('eval.m3.prAuc')}</dt>
          <dd>
            {s.prAucModel === null || s.prAucFraudScore === null ? (
              <Dash />
            ) : (
              t('eval.m3.prAucValue', { model: formatScore(s.prAucModel, locale), score: formatScore(s.prAucFraudScore, locale) })
            )}
          </dd>
          <dt>{t('eval.m3.diffCi')}</dt>
          <dd>
            {s.diffCi ? t('eval.m3.range', { lo: formatScore(s.diffCi[0], locale), hi: formatScore(s.diffCi[1], locale) }) : <Dash />}
          </dd>
          <dt>{t('eval.m3.decision')}</dt>
          <dd>
            {!s.decision ? (
              <Dash />
            ) : hasKey(decisionKey) ? (
              <>
                {t(decisionKey)} <span className="mono small muted">{s.decision}</span>
              </>
            ) : (
              <span className="mono">{s.decision}</span>
            )}
          </dd>
        </dl>
      )}
    </div>
  )
}

const COMPONENTS = ['g1', 'g2', 'g3', 'b2_zero_shot'] as const

function ComponentsBlock({ models }: { models: EvaluationModels | null }) {
  const { t } = useI18n()
  return (
    <div className="evblock">
      <h3>{t('eval.genai.title')}</h3>
      <ul className="evstatus">
        {COMPONENTS.map((k) => {
          const c = models?.[k] ?? null
          const statusKey = c?.status ? `eval.status.${c.status}` : ''
          return (
            <li key={k}>
              <div className="evstatus__head">
                <span>{t(`eval.genai.${k}`)}</span>
                <span className="tag">{!c?.status ? <Dash /> : hasKey(statusKey) ? t(statusKey) : c.status}</span>
              </div>
              {c?.note && (
                <p className="evstatus__note">
                  <Data text={c.note} />
                </p>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// ---------- security ----------

const PROBES = ['tool_scope', 'expired_session', 'other_session_case', 'tool_failure'] as const
type Probe = (typeof PROBES)[number]

function SecuritySection({ security }: { security: EvaluationSecurity | null }) {
  const { t } = useI18n()
  return (
    <section className="card evsection" aria-labelledby="ev-security">
      <h2 id="ev-security" className="card__title">
        {t('eval.security.title')}
      </h2>
      <Table caption={t('eval.security.caption')} wide>
        <thead>
          <tr>
            <th scope="col">{t('eval.col.probe')}</th>
            <th scope="col">{t('eval.col.v2')}</th>
            <th scope="col">{t('eval.col.b1')}</th>
          </tr>
        </thead>
        <tbody>
          {PROBES.map((p) => (
            <tr key={p}>
              <th scope="row">{t(`eval.security.${p}`)}</th>
              <td>
                <ProbeCell probes={security?.v2 ?? null} probe={p} />
              </td>
              <td>
                <ProbeCell probes={security?.b1 ?? null} probe={p} />
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </section>
  )
}

function Outcome({ ok, text }: { ok: boolean | null; text: string }) {
  if (ok === null) return <span className="evbadge evbadge--neutral">{text}</span>
  return (
    <span className={`evbadge ${ok ? 'evbadge--pass' : 'evbadge--fail'}`}>
      {ok ? <IconCheck size={12} /> : <IconAlert size={12} />} {text}
    </span>
  )
}

function ProbeCell({ probes, probe }: { probes: EvaluationProbes | null; probe: Probe }) {
  const { t, locale } = useI18n()
  const fmt = (n: number) => formatCount(n, locale)
  if (!probes) return <Dash />
  if (probe === 'tool_failure') {
    const p = probes.tool_failure
    if (!p || p.safe === null) return <Dash />
    const details = [p.lane ? t('lane.label', { lane: p.lane }) : null, (p.degraded ?? []).join(', ') || null].filter(
      (x): x is string => !!x,
    )
    return (
      <>
        <Outcome ok={p.safe} text={t(p.safe ? 'eval.security.safe' : 'eval.security.unsafe')} />
        {details.length > 0 && <span className="evsub mono">{details.join(' · ')}</span>}
      </>
    )
  }
  if (probe === 'expired_session') {
    const p = probes.expired_session
    if (!p || p.n === null || p.data_shown === null) return <Dash />
    if (p.n === 0) return <Outcome ok={null} text={t('eval.security.noAttempts')} />
    return <Outcome ok={p.data_shown === 0} text={t('eval.security.shown', { k: fmt(p.data_shown), n: fmt(p.n) })} />
  }
  const p = probes[probe]
  if (!p || p.n === null || p.leaks === null) return <Dash />
  if (p.n === 0) return <Outcome ok={null} text={t('eval.security.noAttempts')} />
  return <Outcome ok={p.leaks === 0} text={t('eval.security.leaks', { k: fmt(p.leaks), n: fmt(p.n) })} />
}

// ---------- B0 and limits ----------

function BaselineValue({ row }: { row: EvaluationBaseline }) {
  const { t, locale } = useI18n()
  const v = row.value
  if (v === null) return <Dash />
  switch (row.unit) {
    case 'ratio':
      return <>{formatPercent(v, locale)}</>
    case 'hours':
      return <>{t('eval.unit.hours', { v: formatDecimal(v, locale, 1) })}</>
    case 'days':
      return <>{t('eval.unit.days', { v: formatDecimal(v, locale, 1) })}</>
    case 'count':
      return <>{formatCount(v, locale)}</>
    case 'score_1_5':
      return <>{t('eval.unit.score_1_5', { v: formatDecimal(v, locale, 2) })}</>
    default:
      return <>{row.unit ? `${formatDecimal(v, locale)} ${row.unit}` : formatDecimal(v, locale)}</>
  }
}

function BaselineSection({ rows }: { rows: EvaluationBaseline[] | null }) {
  const { t } = useI18n()
  const list = rows ?? []
  return (
    <section className="card evsection" aria-labelledby="ev-b0">
      <h2 id="ev-b0" className="card__title">
        {t('eval.b0.title')}
      </h2>
      {list.length === 0 ? (
        <p className="block__empty">{t('eval.b0.empty')}</p>
      ) : (
        <Table caption={t('eval.b0.caption')} wide>
          <thead>
            <tr>
              <th scope="col">{t('eval.col.indicator')}</th>
              <th scope="col" className="num">
                {t('eval.col.value')}
              </th>
              <th scope="col" className="num">
                {t('eval.col.records')}
              </th>
              <th scope="col">{t('eval.col.source')}</th>
            </tr>
          </thead>
          <tbody>
            {list.map((b, i) => (
              <tr key={`${b.metric ?? 'b0'}-${i}`}>
                <th scope="row">
                  <Data text={b.description ?? b.metric} />
                  {b.description && b.metric && <span className="evsub mono">{b.metric}</span>}
                </th>
                <td className="num">
                  <BaselineValue row={b} />
                </td>
                <td className="num">
                  <Count value={b.n} />
                </td>
                <td className="mono small">{b.source ?? <Dash />}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  )
}

function LimitsSection({ limits }: { limits: string[] | null }) {
  const { t } = useI18n()
  const list = (limits ?? []).filter((l) => typeof l === 'string' && l.trim() !== '')
  return (
    <section className="card evsection" aria-labelledby="ev-limits">
      <h2 id="ev-limits" className="card__title">
        {t('eval.limits.title')}
      </h2>
      {list.length === 0 ? (
        <p className="block__empty">{t('eval.limits.empty')}</p>
      ) : (
        <ul className="bullets">
          {list.map((l, i) => (
            <li key={i}>
              <Data text={l} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
