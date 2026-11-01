// Case queue (GET /analyst/cases), sorted by the server by clock.breach_at. Keyboard: arrows,
// Home/End and Enter act ONLY while the list has focus (no global one-key shortcuts, WCAG 2.1.4).

import { useState, type KeyboardEvent } from 'react'
import type { ClientErrorCode } from '../api/client'
import type { AnalystCaseListItem } from '../api/types'
import { errorMessageKey } from '../chat/errors'
import { IconAlert, IconRefresh } from '../components/Icons'
import { hasKey, useI18n } from '../i18n'
import { hhmm, slaInfo } from './format'
import { neutralize } from './decision'
import { SlaAlertChips } from './SlaClock'
import { alertsOf, type NewAlert } from './slaAlerts'

export interface QueueProps {
  items: AnalystCaseListItem[]
  selectedId: string | null
  loadedOnce: boolean
  error: ClientErrorCode | null
  asOf: string | null
  hasMore: boolean
  loadingMore: boolean
  now: number
  onOpen: (caseId: string) => void
  onRefresh: () => void
  onLoadMore: () => void
  /** SLA alerts that fired since the previous listing: announced once (aria-live). */
  newAlerts?: NewAlert[]
}

export function Queue(p: QueueProps) {
  const { t, locale } = useI18n()
  // The keyboard cursor: the case the analyst moved to, else the open case, else the first.
  const [cursorId, setCursorId] = useState<string | null>(null)
  const selectedIndex = p.items.findIndex((i) => i.case_id === p.selectedId)
  const cursorIndex = cursorId ? p.items.findIndex((i) => i.case_id === cursorId) : -1
  const activeIndex = cursorIndex >= 0 ? cursorIndex : selectedIndex >= 0 ? selectedIndex : 0
  const activeId = p.items[activeIndex] ? `q-${p.items[activeIndex].case_id}` : undefined
  const setActive = (i: number) => setCursorId(p.items[i]?.case_id ?? null)

  const onKeyDown = (e: KeyboardEvent<HTMLUListElement>) => {
    if (e.altKey || e.ctrlKey || e.metaKey || p.items.length === 0) return
    let next = activeIndex
    if (e.key === 'ArrowDown') next = Math.min(p.items.length - 1, activeIndex + 1)
    else if (e.key === 'ArrowUp') next = Math.max(0, activeIndex - 1)
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = p.items.length - 1
    else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      p.onOpen(p.items[activeIndex].case_id)
      return
    } else return
    e.preventDefault()
    setActive(next)
    document.getElementById(`q-${p.items[next].case_id}`)?.scrollIntoView?.({ block: 'nearest' })
  }

  const stale = p.error !== null && p.asOf !== null
  return (
    <section className="queue" aria-labelledby="queue-title">
      <header className="queue__head">
        {/* The count sits outside the heading: the heading is just "Cola" (CX-17 (2)). */}
        <div className="queue__title">
          <h2 id="queue-title" className="card__title">
            {t('console.queue')}
          </h2>
          {p.loadedOnce && (
            <span className="tag">{t(p.items.length === 1 ? 'console.queueCountOne' : 'console.queueCount', { n: p.items.length })}</span>
          )}
        </div>
        <button type="button" className="btn btn--ghost btn--small" onClick={p.onRefresh} aria-label={t('console.refresh')}>
          <IconRefresh size={14} />
        </button>
      </header>
      {/* The clock is not announced on every poll; only "desactualizado" is (CX-17 (1)). */}
      <p className="queue__meta muted small" role={stale ? 'status' : undefined} aria-live={stale ? undefined : 'off'}>
        {stale
          ? t('console.stale', { time: hhmm(p.asOf, locale) })
          : p.asOf
            ? t('console.updatedAt', { time: hhmm(p.asOf, locale) })
            : ''}
      </p>
      {/* Only alerts that fired since the last listing are announced; a poll that brings the
          same ones again changes nothing here, so nothing is repeated (the chips are not live). */}
      <p className="sr-only" aria-live="polite" aria-atomic="true" data-testid="queue-alert-news">
        {(p.newAlerts ?? [])
          .map((a) => t('console.alert.news', { name: neutralize(a.display_name), id: a.case_id, alert: t(`console.alert.${a.kind}`) }))
          .join(' ')}
      </p>
      {p.error && (
        <div className={`notice ${p.error === 'not_authorized' ? 'notice--warn' : 'notice--error'} queue__notice`} role="alert">
          <IconAlert size={14} />
          <p>{t(queueErrorKey(p.error))}</p>
        </div>
      )}
      {!p.loadedOnce ? (
        <p className="block__empty" role="status">
          <span className="spinner" aria-hidden="true" /> {t('console.loadingQueue')}
        </p>
      ) : p.items.length === 0 && !p.error ? (
        <div className="queue__empty">
          <p>
            <strong>{t('console.queueEmpty')}</strong>
          </p>
          <p className="muted small">{t('console.queueEmptyHint')}</p>
        </div>
      ) : (
        <ul
          className="queue__list"
          role="listbox"
          tabIndex={0}
          aria-label={t('console.queueList')}
          aria-activedescendant={activeId}
          onKeyDown={onKeyDown}
        >
          {p.items.map((item, i) => (
            <QueueItem
              key={item.case_id}
              item={item}
              selected={item.case_id === p.selectedId}
              active={i === activeIndex}
              now={p.now}
              onOpen={() => {
                setActive(i)
                p.onOpen(item.case_id)
              }}
            />
          ))}
        </ul>
      )}
      {p.hasMore && (
        <button type="button" className="btn btn--ghost btn--small queue__more" onClick={p.onLoadMore} disabled={p.loadingMore}>
          {t('console.loadMore')}
        </button>
      )}
    </section>
  )
}

function queueErrorKey(code: ClientErrorCode) {
  if (code === 'not_authorized') return 'console.error.forbidden' as const
  if (code === 'not_implemented') return 'console.error.notImplemented' as const
  return errorMessageKey(code)
}

function QueueItem({
  item,
  selected,
  active,
  now,
  onOpen,
}: {
  item: AnalystCaseListItem
  selected: boolean
  active: boolean
  now: number
  onOpen: () => void
}) {
  const { t, locale } = useI18n()
  // Answered (notified/closed): the SLA no longer runs; the server lists these last.
  const answered = item.status === 'notified' || item.status === 'closed'
  const sla = answered ? null : slaInfo(item.breach_at, now, locale)
  const intentKey = item.intent_class ? `intent.${item.intent_class}` : ''
  return (
    <li
      id={`q-${item.case_id}`}
      role="option"
      aria-selected={selected}
      className={`qitem ${selected ? 'qitem--selected' : ''} ${active ? 'qitem--active' : ''}`}
      onClick={onOpen}
    >
      <div className="qitem__row">
        <strong className="qitem__name">{neutralize(item.display_name)}</strong>
        {item.priority === 'high' && <span className="tag tag--danger">{t('console.priorityHigh')}</span>}
        <span className={`qitem__sla ${sla?.overdue ? 'qitem__sla--overdue' : sla?.soon ? 'qitem__sla--soon' : ''}`}>
          {answered
            ? t('console.slaAnswered')
            : sla
              ? sla.overdue
                ? t('console.slaOverdue', { when: sla.label })
                : t('console.slaLeft', { when: sla.label })
              : t('console.slaNone')}
        </span>
      </div>
      <div className="qitem__row qitem__row--meta">
        <span className="mono small">{item.case_id}</span>
        {item.lane && <span className={`lane lane--${item.lane}`}>{t('lane.label', { lane: item.lane })}</span>}
        <span className="tag tag--lang">
          {item.language.toUpperCase()}
          {item.country ? `·${item.country}` : ''}
        </span>
        <span className="tag">{t(`status.${item.status}`)}</span>
      </div>
      {/* The queue is a to-do list: an answered case drops its alert chips (they called for an
          action that already happened). The fired alarms stay in the case, in the header as
          dimmed history and in Historial with their times. */}
      {!answered && alertsOf(item).length > 0 && (
        <div className="qitem__row qitem__row--meta">
          <SlaAlertChips alerts={alertsOf(item)} />
        </div>
      )}
      <div className="qitem__row qitem__row--meta">
        {intentKey && hasKey(intentKey) && <span className="small muted">{t(intentKey)}</span>}
        {item.has_report ? (
          item.report_reliable === false ? (
            <span className="tag tag--danger">
              <IconAlert size={12} /> {t('console.unreliableShort')}
            </span>
          ) : (
            <span className="tag tag--verified">{t('console.reportReady')}</span>
          )
        ) : (
          <span className="tag">{t('console.noReport')}</span>
        )}
      </div>
    </li>
  )
}
