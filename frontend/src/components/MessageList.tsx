// Conversation log. Every dynamic string is rendered as React text (escaped); nothing is
// parsed as HTML or markdown, so pasted markup shows literally (RF-11).

import { useEffect, useRef } from 'react'
import type { ChatButton, ButtonKind } from '../api/types'
import { formatClock } from '../format'
import { translate, useI18n } from '../i18n'
import type { TranscriptEntry } from '../session'
import { IconAlert, IconCheck, IconClock, IconHuman, IconRefresh } from './Icons'
import { SourceBadge } from './SourceBadge'

type SystemEntry = Extract<TranscriptEntry, { kind: 'system' }>
type CustomerEntry = Extract<TranscriptEntry, { kind: 'customer' }>
type ResolutionEntry = Extract<TranscriptEntry, { kind: 'resolution' }>
type NoticeEntry = Extract<TranscriptEntry, { kind: 'notice' }>

export interface MessageListProps {
  entries: TranscriptEntry[]
  usedButtonIds: string[]
  /** Buttons of the last system message are clickable only when the chat is idle. */
  canPress: boolean
  onPress: (button: ChatButton) => void
  onRetry: () => void
  retryEntryId: string | null
  customerLang: string
  /** Customer's time zone (same as the case card), so one instant shows one time. */
  timeZone?: string
}

export function MessageList(props: MessageListProps) {
  const { t } = useI18n()
  const endRef = useRef<HTMLDivElement>(null)
  const lastSystemIndex = findLastSystemIndex(props.entries)

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [props.entries.length])

  return (
    <div className="log" role="log" aria-live="polite" aria-relevant="additions" aria-label={t('chat.logLabel')}>
      {props.entries.map((e, i) =>
        e.kind === 'resolution' ? (
          <ResolutionMessage key={e.id} entry={e} timeZone={props.timeZone} />
        ) : e.kind === 'notice' ? (
          <NoticeMessage key={e.id} entry={e} timeZone={props.timeZone} />
        ) : e.kind === 'customer' ? (
          <CustomerMessage
            key={e.id}
            entry={e}
            lang={props.customerLang}
            onRetry={e.id === props.retryEntryId ? props.onRetry : undefined}
            timeZone={props.timeZone}
          />
        ) : (
          <SystemMessage
            key={e.id}
            entry={e}
            usedButtonIds={props.usedButtonIds}
            isLast={i === lastSystemIndex}
            canPress={props.canPress}
            onPress={props.onPress}
            timeZone={props.timeZone}
          />
        ),
      )}
      <div ref={endRef} />
    </div>
  )
}

function findLastSystemIndex(entries: TranscriptEntry[]): number {
  for (let i = entries.length - 1; i >= 0; i--) if (entries[i].kind === 'system') return i
  return -1
}

function SystemMessage({
  entry,
  usedButtonIds,
  isLast,
  canPress,
  onPress,
  timeZone,
}: {
  entry: SystemEntry
  usedButtonIds: string[]
  isLast: boolean
  canPress: boolean
  onPress: (b: ChatButton) => void
  timeZone?: string
}) {
  const { t, locale } = useI18n()
  const isModel = entry.source === 'model'
  return (
    <div className="msg-row msg-row--system">
      <article className={`msg msg--system ${isModel ? 'msg--model' : 'msg--template'}`} lang={entry.lang === 'pt' ? 'pt-BR' : 'es'}>
        <span className="sr-only">{t('chat.bank')}: </span>
        <p className="msg__text">{entry.text}</p>
        {entry.degraded.map((d) => (
          <p key={d} className="msg__degraded">
            <IconAlert size={14} />
            <span>{t(d === 'model_timeout' ? 'degraded.model_timeout' : 'degraded.tool_unavailable')}</span>
          </p>
        ))}
        <footer className="msg__meta">
          <SourceBadge kind={isModel ? 'genai' : 'template'} label={t(isModel ? 'source.model' : 'source.template')} />
          <time dateTime={entry.at}>{formatClock(entry.at, locale, timeZone)}</time>
        </footer>
      </article>
      {entry.buttons.length > 0 && (
        <ClarifyButtons
          buttons={entry.buttons}
          usedButtonIds={usedButtonIds}
          isLast={isLast}
          active={isLast && canPress}
          onPress={onPress}
        />
      )}
    </div>
  )
}

/** The reply a person approved in the console. Badge in the case language, text as text. */
function ResolutionMessage({ entry, timeZone }: { entry: ResolutionEntry; timeZone?: string }) {
  const { locale } = useI18n()
  return (
    <div className="msg-row msg-row--system">
      <article className="msg msg--system msg--human" lang={entry.lang === 'pt' ? 'pt-BR' : 'es'} data-testid="resolution-message">
        <span className="sr-only">{translate(entry.lang, 'chat.bank')}: </span>
        <p className="msg__text">{entry.text}</p>
        <footer className="msg__meta">
          <SourceBadge kind="human" label={translate(entry.lang, 'chat.resolutionBadge')} />
          <time dateTime={entry.at}>{formatClock(entry.at, locale, timeZone)}</time>
        </footer>
      </article>
    </div>
  )
}

/**
 * A proactive message of the case clock (80 % of the SLA, automatic escalation). A rule sent it,
 * not a person and not a model: the badge says so, in the case language. It joins the log once
 * (role="log" announces additions only), so screen readers hear it when it arrives, not on
 * every poll or after a reload.
 */
function NoticeMessage({ entry, timeZone }: { entry: NoticeEntry; timeZone?: string }) {
  const { locale } = useI18n()
  return (
    <div className="msg-row msg-row--system">
      <article
        className={`msg msg--system msg--notice msg--notice-${entry.notice}`}
        lang={entry.lang === 'pt' ? 'pt-BR' : 'es'}
        data-testid="notice-message"
        data-notice={entry.notice}
      >
        <span className="sr-only">{translate(entry.lang, 'chat.noticeFrom')}: </span>
        <p className="msg__text">{entry.text}</p>
        <footer className="msg__meta">
          <SourceBadge kind="rule" label={translate(entry.lang, 'chat.noticeBadge')} />
          <span className="msg__notice-kind">
            <IconClock size={12} /> {translate(entry.lang, entry.notice === 'escalated' ? 'promise.escalated' : 'promise.sla_80')}
          </span>
          <time dateTime={entry.at}>{formatClock(entry.at, locale, timeZone)}</time>
        </footer>
      </article>
    </div>
  )
}

function CustomerMessage({
  entry,
  lang,
  onRetry,
  timeZone,
}: {
  entry: CustomerEntry
  lang: string
  onRetry?: () => void
  timeZone?: string
}) {
  const { t, locale } = useI18n()
  return (
    <div className="msg-row msg-row--customer">
      <article className={`msg msg--customer msg--${entry.status}`} lang={lang}>
        <span className="sr-only">{t('chat.you')}: </span>
        <p className="msg__text">{entry.text}</p>
        <footer className="msg__meta">
          {entry.status === 'pending' && <span className="msg__status">{t('chat.pending')}</span>}
          {entry.status === 'failed' && (
            <span className="msg__status msg__status--failed">
              <IconAlert size={12} /> {t('chat.failed')}
            </span>
          )}
          <time dateTime={entry.at}>{formatClock(entry.at, locale, timeZone)}</time>
        </footer>
      </article>
      {entry.status === 'failed' && onRetry && (
        <button type="button" className="btn btn--ghost btn--small msg__retry" onClick={onRetry}>
          <IconRefresh size={14} /> {t('chat.retry')}
        </button>
      )}
    </div>
  )
}

const KIND_CLASS: Record<ButtonKind, string> = {
  confirm: 'chip--confirm',
  deny: 'chip--deny',
  choice: 'chip--choice',
  handoff: 'chip--handoff',
}

export function ClarifyButtons({
  buttons,
  usedButtonIds,
  isLast = true,
  active,
  onPress,
}: {
  buttons: ChatButton[]
  usedButtonIds: string[]
  /** Chips of the latest message stay focusable while a turn is sending (aria-disabled, not
   *  disabled), so keyboard focus does not fall back to <body> when one is pressed. */
  isLast?: boolean
  active: boolean
  onPress: (b: ChatButton) => void
}) {
  const { t } = useI18n()
  return (
    <div className="chips" role="group" aria-label={t('chat.buttonsLabel')} data-latest={isLast || undefined}>
      {buttons.map((b) => {
        const used = usedButtonIds.includes(b.id)
        const inert = !active || used
        return (
          <button
            key={b.id}
            type="button"
            className={`chip ${KIND_CLASS[b.kind] ?? 'chip--choice'} ${used ? 'chip--used' : ''}`}
            disabled={inert && !isLast}
            aria-disabled={inert && isLast ? true : undefined}
            onClick={() => {
              if (!inert) onPress(b)
            }}
          >
            {used && <IconCheck size={14} />}
            {b.kind === 'handoff' && !used && <IconHuman size={14} />}
            <span>{b.label}</span>
            {used && <span className="sr-only"> ({t('chat.buttonChosen')})</span>}
          </button>
        )
      })}
    </div>
  )
}
