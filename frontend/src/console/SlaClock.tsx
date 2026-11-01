// The SLA timers in the console: alert chips (queue, case header) and the case clock's
// timeline (Historial). Every chip carries an icon and text, never color alone.

import { useApi } from '../api/context'
import type { AnalystCaseView, SlaTimerKind } from '../api/types'
import { DAY_S, formatSpan, useDemoClockScale } from '../chat/demoClock'
import { IconAlert, IconCheck, IconClock, IconHand } from '../components/Icons'
import { SourceBadge } from '../components/SourceBadge'
import { useI18n } from '../i18n'
import { dateTime } from './format'
import { clockTimeline, type TimerState } from './slaAlerts'

const ALERT_ICON: Record<SlaTimerKind, (p: { size?: number }) => ReturnType<typeof IconAlert>> = {
  unassigned: IconHand,
  sla_80: IconClock,
  breached: IconAlert,
}

/**
 * Alert chips. `history`: the case was already answered, so the alarms are past events, not
 * calls to action: neutral (dimmed) chips under a visible "Historial del reloj" label.
 */
export function SlaAlertChips({ alerts, className = '', history = false }: { alerts: SlaTimerKind[]; className?: string; history?: boolean }) {
  const { t } = useI18n()
  if (alerts.length === 0) return null
  return (
    <span className={`sla-alerts ${history ? 'sla-alerts--history' : ''} ${className}`} data-testid="sla-alerts">
      {history && <span className="sla-alerts__label small muted">{t('console.alert.history')}:</span>}
      {alerts.map((k) => {
        const Icon = ALERT_ICON[k]
        return (
          <span
            key={k}
            className={`tag sla-alert ${history ? 'sla-alert--past' : `sla-alert--${k}`}`}
            data-alert={k}
            title={t(`console.alert.${k}Hint`)}
          >
            <Icon size={12} />
            {!history && <span className="sr-only">{t('console.alert.prefix')}: </span>}
            {t(`console.alert.${k}`)}
          </span>
        )
      })}
    </span>
  )
}

const STATE_ICON: Record<TimerState, (p: { size?: number }) => ReturnType<typeof IconAlert>> = {
  fired: IconAlert,
  scheduled: IconClock,
  cancelled: IconCheck,
  not_fired: IconCheck,
}

/** Historial: the three timers of the case, planned time and what happened to each one. */
export function ClockTimeline({ c }: { c: AnalystCaseView }) {
  const { t, locale } = useI18n()
  const scale = useDemoClockScale(useApi())
  if (c.lane !== 'B' && !(Array.isArray(c.clock_events) && c.clock_events.length > 0)) {
    return (
      <>
        <h3 className="block__title">{t('console.clock.title')}</h3>
        <p className="block__empty">{t('console.clock.noneLane')}</p>
      </>
    )
  }
  const rows = clockTimeline(c)
  return (
    <>
      <h3 className="block__title" id="clock-title">
        {t('console.clock.title')}
      </h3>
      <ol className="clock-line" aria-labelledby="clock-title" data-testid="clock-timeline">
        {rows.map((r) => {
          const Icon = STATE_ICON[r.state]
          return (
            <li key={r.kind} className={`clock-line__item clock-line__item--${r.state}`} data-kind={r.kind} data-state={r.state}>
              <span className="clock-line__dot" aria-hidden="true" />
              <div className="clock-line__body">
                <p className="clock-line__head">
                  <strong>{t(`console.clock.${r.kind}`)}</strong>{' '}
                  <span className={`tag clock-state clock-state--${r.state}`}>
                    <Icon size={12} /> {t(`console.clock.state.${r.state}`)}
                  </span>
                </p>
                <p className="small">
                  <span className="muted">{t('console.clock.planned')}</span> {dateTime(r.planned, locale)}
                  {r.firedAt && (
                    <>
                      {' · '}
                      <span className="muted">{t('console.clock.fired')}</span> {dateTime(r.firedAt, locale)}
                    </>
                  )}
                </p>
                <p className="small muted">{t(`console.clock.${r.kind}Effect`)}</p>
                {r.firedAt && (
                  <p className="small">
                    <SourceBadge kind="rule" /> <code className="mono small">sla_timer.{r.kind}</code>
                  </p>
                )}
              </div>
            </li>
          )
        })}
      </ol>
      <p className="block__note" data-testid="clock-note">
        {scale
          ? t('console.clock.note', { ratio: t('judge.clock.ratio', { t: formatSpan(DAY_S * scale, locale) }) })
          : t('console.clock.noteNeutral')}
      </p>
    </>
  )
}
