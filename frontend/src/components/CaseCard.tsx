// Live case card, in blocks kept apart on purpose (RF-07): what the customer said
// (unverified) is never mixed with the bank's record (verified).

import type { CaseStatus, CustomerCaseView, Lane, LifecycleStep, Progress } from '../api/types'
import { caseIsWaiting, LIFECYCLE, lifecycleIndex, promiseState, STEPS, stepIndex, type PromiseState } from '../chat/progress'
import { formatDate, formatDateTimeInZone, formatMoney, zoneForCountry } from '../format'
import { hasKey, useI18n } from '../i18n'
import type { TurnSnapshot } from '../session'
import { IconAlert, IconCheck, IconClock, IconLock, IconQuote, IconShield } from './Icons'
import { LaneChip } from './LaneChip'

export interface CaseCardProps {
  snapshot: TurnSnapshot | null
  /** Customer locale for money and dates (es-MX, pt-BR…). */
  locale: string
  country: string | null
}

export function CaseCard({ snapshot, locale, country }: CaseCardProps) {
  const { t } = useI18n()
  const progress = snapshot?.progress ?? null
  const card = snapshot?.case_card ?? null
  const lane = card?.lane ?? snapshot?.lane ?? null
  return (
    <section className="card case-card" aria-labelledby="case-card-title">
      <header className="card__header">
        <h2 id="case-card-title" className="card__title">
          {t('card.title')}
        </h2>
        <span className="tag tag--synthetic">{t('card.synthetic')}</span>
      </header>

      <Stepper progress={progress} />
      {card && <Lifecycle status={card.status} step={card.lifecycle_step} lane={lane} />}

      <Claimed progress={progress} card={card} locale={locale} />
      <BankRecord card={card} locale={locale} />
      <CaseBlock card={card} lane={lane} locale={locale} country={country} />
    </section>
  )
}

function Stepper({ progress }: { progress: Progress | null }) {
  const { t } = useI18n()
  const current = stepIndex(progress)
  return (
    <div className="block">
      <h3 className="block__title">{t('card.steps')}</h3>
      <ol className="stepper">
        {STEPS.map((s, i) => {
          const state = i < current ? 'done' : i === current ? 'current' : 'todo'
          return (
            <li key={s} className={`stepper__item stepper__item--${state}`} aria-current={state === 'current' ? 'step' : undefined}>
              <span className="stepper__dot" aria-hidden="true">
                {state === 'done' ? <IconCheck size={12} /> : i + 1}
              </span>
              <span className="stepper__label">{t(`step.${s}`)}</span>
              <span className="sr-only">
                {' '}
                ({t(state === 'done' ? 'step.stateDone' : state === 'current' ? 'step.stateCurrent' : 'step.stateTodo')})
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function Lifecycle({ status, step, lane }: { status: CaseStatus; step: LifecycleStep | null | undefined; lane: Lane | null }) {
  const { t } = useI18n()
  if (lane !== 'B') return null
  const idx = lifecycleIndex(step, status)
  return (
    <div className="block block--flush">
      <h3 className="block__title block__title--muted">{t('card.lifecycle')}</h3>
      <ol className="stepper stepper--small">
        {LIFECYCLE.map((s, i) => {
          const state = idx < 0 ? 'todo' : i < idx ? 'done' : i === idx ? 'current' : 'todo'
          return (
            <li key={s} className={`stepper__item stepper__item--${state}`} aria-current={state === 'current' ? 'step' : undefined}>
              <span className="stepper__dot" aria-hidden="true">
                {state === 'done' ? <IconCheck size={10} /> : ''}
              </span>
              <span className="stepper__label">{t(`lifecycle.${s}`)}</span>
              <span className="sr-only">
                {' '}
                ({t(state === 'done' ? 'step.stateDone' : state === 'current' ? 'step.stateCurrent' : 'step.stateTodo')})
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function Claimed({ progress, card, locale }: { progress: Progress | null; card: CustomerCaseView | null; locale: string }) {
  const { t } = useI18n()
  const c = progress?.claimed
  const rows: { label: string; value: string }[] = []
  if (progress?.complaint_type) {
    // The backend groups duplicated charges under wrong_fee; once the rule says "duplicate",
    // name it that way instead of "comisión" (F11).
    const isDuplicate = card?.lane_reason_code?.startsWith('duplicate_') ?? false
    const typeLabel = isDuplicate ? t('complaint.duplicate') : t(`complaint.${progress.complaint_type}`)
    rows.push({
      label: t('field.type'),
      value: `${typeLabel} (${t('complaint.provisional')})`,
    })
  }
  if (c?.amount) rows.push({ label: t('field.amount'), value: formatMoney(c.amount, c.currency, locale) })
  if (c?.date) rows.push({ label: t('field.date'), value: formatDate(c.date, locale) })
  if (c?.merchant_text) rows.push({ label: t('field.merchant'), value: c.merchant_text })
  if (c?.card_last4) rows.push({ label: t('field.card'), value: `•••• ${c.card_last4}` })
  const missing = progress?.missing ?? []

  return (
    <div className="block block--claimed" data-testid="block-claimed">
      <div className="block__head">
        <h3 className="block__title">
          <IconQuote size={14} /> {t('card.claimed')}
        </h3>
        <span className="tag tag--unverified">{t('card.unverified')}</span>
      </div>
      {rows.length === 0 && !card?.customer_statement ? (
        <p className="block__empty">{t('card.claimedEmpty')}</p>
      ) : (
        <dl className="facts">
          {rows.map((r) => (
            <div key={r.label} className="facts__row">
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {card?.customer_statement && (
        <figure className="statement">
          <figcaption>{t('card.statement')}</figcaption>
          <blockquote>{card.customer_statement}</blockquote>
        </figure>
      )}
      {/* Before the first turn there is nothing to be missing yet (CX-17 (8)). */}
      {progress && (
        <div className="missing">
          <span className="missing__label">{t('card.missing')}:</span>{' '}
          {missing.length === 0 ? (
            <span>{t('card.missingNone')}</span>
          ) : (
            <ul className="missing__list">
              {missing.map((m) => {
                const key = `slot.${m}`
                return <li key={m}>{hasKey(key) ? t(key) : t('slot.unknown', { name: m })}</li>
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function BankRecord({ card, locale }: { card: CustomerCaseView | null; locale: string }) {
  const { t } = useI18n()
  const ch = card?.charge ?? null
  const statusKey = ch ? `chargeStatus.${ch.status.toLowerCase()}` : ''
  return (
    <div className="block block--verified" data-testid="block-bank">
      <div className="block__head">
        <h3 className="block__title">
          <IconShield size={14} /> {t('card.bank')}
        </h3>
        {ch && <span className="tag tag--verified">{t('card.verified')}</span>}
      </div>
      {!ch ? (
        <p className="block__empty">{t('card.bankEmpty')}</p>
      ) : (
        <dl className="facts">
          <div className="facts__row">
            <dt>{t('field.date')}</dt>
            <dd>{formatDate(ch.local_date, locale)}</dd>
          </div>
          <div className="facts__row">
            <dt>{t('field.amount')}</dt>
            <dd className="facts__money">{formatMoney(ch.amount, ch.currency, locale)}</dd>
          </div>
          <div className="facts__row">
            <dt>{t('field.merchant')}</dt>
            <dd>{ch.merchant_name}</dd>
          </div>
          <div className="facts__row">
            <dt>{t('field.card')}</dt>
            <dd className="mono">•••• {ch.card_last4}</dd>
          </div>
          <div className="facts__row">
            <dt>{t('field.status')}</dt>
            <dd>{hasKey(statusKey) ? t(statusKey) : ch.status}</dd>
          </div>
        </dl>
      )}
    </div>
  )
}

function CaseBlock({
  card,
  lane,
  locale,
  country,
}: {
  card: CustomerCaseView | null
  lane: Lane | null
  locale: string
  country: string | null
}) {
  const { t } = useI18n()
  if (!card) {
    return (
      <div className="block">
        <h3 className="block__title">
          <IconLock size={14} /> {t('card.case')}
        </h3>
        <p className="block__empty">{t('card.caseEmpty')}</p>
      </div>
    )
  }
  const reasonKey = card.lane_reason_code ? `rule.${card.lane_reason_code}` : ''
  const reason = card.lane_reason_code
    ? hasKey(reasonKey)
      ? t(reasonKey)
      : t('rule.unknown', { id: card.lane_reason_code })
    : null
  const zone = zoneForCountry(country)
  // One time style with the zone in words, like the bot's "00:16 (hora del centro de México)" (CX-09).
  const zoneKey = `tz.${country ?? ''}`
  const zoneLabel = hasKey(zoneKey) ? t(zoneKey) : undefined
  // The outcome is the analyst's action id: never shown raw to the customer (CX-07).
  const outcomeKey = card.outcome ? `card.outcome.${card.outcome}` : ''
  return (
    <div className="block block--case">
      <div className="block__head">
        <h3 className="block__title">
          <IconLock size={14} /> {t('card.case')}
        </h3>
        {lane && <LaneChip lane={lane} />}
      </div>
      <dl className="facts">
        <div className="facts__row">
          <dt>{t('card.caseId')}</dt>
          <dd className="mono case-id">{card.case_id}</dd>
        </div>
        {reason && (
          <div className="facts__row facts__row--stack">
            <dt>{t('card.reason')}</dt>
            <dd>{reason}</dd>
          </div>
        )}
        <div className="facts__row">
          <dt>{t('card.state')}</dt>
          <dd>{t(`status.${card.status}`)}</dd>
        </div>
        {card.expected_date && (
          <div className="facts__row">
            <dt>{t('card.expectedDate')}</dt>
            <dd>{formatDate(card.expected_date, locale)}</dd>
          </div>
        )}
        <PromiseRow card={card} />
        {card.first_response_by && (
          <div className="facts__row">
            <dt>{t('card.firstResponse')}</dt>
            <dd>{formatDateTimeInZone(card.first_response_by, locale, zone, zoneLabel)}</dd>
          </div>
        )}
        {lane === 'C' && card.handoff_queue && (
          <div className="facts__row">
            <dt>{t('card.queue')}</dt>
            <dd>{card.handoff_queue}</dd>
          </div>
        )}
        {outcomeKey && hasKey(outcomeKey) && (
          <div className="facts__row facts__row--stack">
            <dt>{t('card.outcome')}</dt>
            <dd>{t(outcomeKey)}</dd>
          </div>
        )}
        <div className="facts__row">
          <dt>{t('card.created')}</dt>
          <dd>{formatDateTimeInZone(card.created_at, locale, zone, zoneLabel)}</dd>
        </div>
      </dl>
      <p className="block__note">{t('card.restartNote')}</p>
    </div>
  )
}


const PROMISE_ICON: Record<PromiseState, (p: { size?: number }) => ReturnType<typeof IconCheck>> = {
  on_time: IconCheck,
  sla_80: IconClock,
  escalated: IconAlert,
}

/**
 * Lane B: where the case stands against its internal SLA, from the clock's notices (a tiempo,
 * aviso 80 %, escalado). Icon + text, never color alone. Not a live region: the notice itself
 * is announced once in the conversation log, so a poll never repeats it.
 */
function PromiseRow({ card }: { card: CustomerCaseView }) {
  const { t } = useI18n()
  const state = promiseState(card)
  if (!state) return null
  const Icon = PROMISE_ICON[state]
  const waiting = caseIsWaiting(card.status)
  return (
    <div className="facts__row facts__row--stack" data-testid="promise-state" data-state={state}>
      <dt>{t('card.promise')}</dt>
      <dd>
        <span className={`tag promise promise--${state}`}>
          <Icon size={12} /> {t(`promise.${state}`)}
        </span>
        {(waiting || state !== 'on_time') && <span className="promise__hint small">{t(`promise.${state}Hint`)}</span>}
      </dd>
    </div>
  )
}
