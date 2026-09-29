// Customer chat (#/chat/<demo_key>). Desktop (>= 1024 px): judge panel | phone | live card
// with the trace strip. Mobile: full-screen chat, the card as a collapsible bottom sheet.

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useApi } from '../api/context'
import type { DemoKey } from '../api/types'
import { errorMessageKey } from '../chat/errors'
import { useDemoClockScale } from '../chat/demoClock'
import { currentSwitches, useChat, type UseChat } from '../chat/useChat'
import { DESKTOP_QUERY, useMediaQuery } from '../chat/useMedia'
import { CaseCard } from '../components/CaseCard'
import { SyntheticBanner, TopBar } from '../components/Chrome'
import { Composer } from '../components/Composer'
import { IconAlert, IconBack, IconChevron, IconRefresh, IconX } from '../components/Icons'
import { JudgePanel } from '../components/JudgePanel'
import { MessageList } from '../components/MessageList'
import { SessionClock } from '../components/SessionClock'
import { TraceStrip } from '../components/TraceStrip'
import { findCustomer, type DemoCustomer } from '../demo/customers'
import { zoneForCountry } from '../format'
import { useI18n } from '../i18n'
import { I18nProvider } from '../i18n/I18nProvider'
import { STEPS, stepIndex } from '../chat/progress'
import '../styles/views.css'

export function ChatView({ demoKey }: { demoKey: DemoKey }) {
  const customer = findCustomer(demoKey)
  // The UI follows the customer's language (João in PT), independent of the landing toggle.
  return (
    <I18nProvider initial={customer.language} strings="views">
      <ChatScreen customer={customer} />
    </I18nProvider>
  )
}

function ChatScreen({ customer }: { customer: DemoCustomer }) {
  const { t, setLang } = useI18n()
  const api = useApi()
  const chat = useChat(api, customer.key, customer.language)
  const clockScale = useDemoClockScale(api)
  const { state } = chat
  const isDesktop = useMediaQuery(DESKTOP_QUERY)
  const [draft, setDraft] = useState('')
  const [guideOpen, setGuideOpen] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const phoneRef = useRef<HTMLElement>(null)
  // A chip pressed from the keyboard is disabled once the turn is answered; move focus to the
  // next useful control instead of letting it fall back to <body> (FE-06).
  const focusAfterTurn = useRef(false)

  const sessionLang = state.session?.customer.language
  useEffect(() => {
    if (sessionLang) setLang(sessionLang)
  }, [sessionLang, setLang])

  const locale = state.session?.customer.locale ?? customer.locale
  const country = state.session?.customer.country ?? null
  const name = customer.nameKey ? t(customer.nameKey) : customer.firstName
  const expiresAt = state.session?.expires_at ?? null
  const active = state.phase === 'ready' || state.phase === 'sending'
  const buttonsOnly = active && lastInputMode(chat) === 'buttons_only'
  const timeZone = zoneForCountry(country)
  const lastEntryId = state.entries[state.entries.length - 1]?.id

  useEffect(() => {
    if (!focusAfterTurn.current || state.phase === 'sending') return
    focusAfterTurn.current = false
    const phone = phoneRef.current
    if (!phone) return
    const retryBtn = phone.querySelector<HTMLButtonElement>('.msg__retry')
    const chip = phone.querySelector<HTMLButtonElement>(
      '.chips[data-latest] .chip:not(:disabled):not([aria-disabled="true"])',
    )
    const input = inputRef.current && !inputRef.current.disabled ? inputRef.current : null
    const target = retryBtn ?? (buttonsOnly ? (chip ?? input) : (input ?? chip))
    target?.focus()
  }, [state.phase, lastEntryId, buttonsOnly])

  const pressButton: UseChat['pressButton'] = (b) => {
    focusAfterTurn.current = true
    chat.pressButton(b)
  }
  const restart = () => {
    setDraft('')
    chat.restart()
  }

  const onSuggestion = (text: string) => {
    setDraft(text)
    if (!isDesktop) setGuideOpen(false)
    inputRef.current?.focus()
  }

  const judge = (
    <JudgePanel
      customer={customer}
      expiresAt={expiresAt}
      onExpire={() => {}}
      onSuggestion={onSuggestion}
      suggestionsEnabled={active && !buttonsOnly}
      serverSwitches={currentSwitches(state)}
      pendingSwitches={state.switchPatch}
      onToggleSwitch={active ? chat.stageSwitch : undefined}
      onExpireNow={chat.expireNow}
      canExpireNow={state.phase === 'ready'}
      caseId={state.last?.case_card?.case_id ?? null}
      onApplyNow={chat.applySwitchesNow}
      canApplyNow={state.phase === 'ready' && Object.keys(state.switchPatch).length > 0}
      applying={state.applyingSwitches}
      switchNote={state.switchNote}
      clockScale={clockScale}
    />
  )
  const cardAndTrace = (
    <>
      <CaseCard snapshot={state.last} locale={locale} country={country} />
      <TraceStrip snapshot={state.last} />
    </>
  )

  return (
    <div className="page page--chat">
      <TopBar>
        <a className="btn btn--ghost btn--small" href="#/" aria-label={t('chat.back')}>
          <IconBack size={14} /> <span className="btn__label">{t('chat.back')}</span>
        </a>
        <button type="button" className="btn btn--ghost btn--small" onClick={restart} aria-label={t('chat.restart')}>
          <IconRefresh size={14} /> <span className="btn__label">{t('chat.restart')}</span>
        </button>
      </TopBar>
      <SyntheticBanner />

      <main className="chat-layout" id="main" tabIndex={-1}>
        {/* Two asides: each needs its own name (axe landmark-unique). */}
        {isDesktop && <aside className="chat-layout__judge" aria-label={t('judge.title')}>{judge}</aside>}

        <section className="phone" aria-label={t('chat.phoneLabel')} ref={phoneRef}>
          <header className="phone__header">
            <span className="avatar avatar--bank" aria-hidden="true">
              LB
            </span>
            <div className="phone__who">
              <strong>{t('chat.bank')}</strong>
              <span className="muted small">
                {name} · {customer.tag} · {t('chat.customerSynthetic')}
              </span>
            </div>
            <SessionClock expiresAt={expiresAt} onExpire={chat.expireLocally} />
          </header>

          {!isDesktop && (
            <div className="guide">
              <button
                type="button"
                className="guide__toggle"
                aria-expanded={guideOpen}
                aria-controls="guide-body"
                onClick={() => setGuideOpen((v) => !v)}
              >
                {t('judge.guide')}
                <IconChevron size={14} />
              </button>
              <div id="guide-body" hidden={!guideOpen}>
                {judge}
              </div>
            </div>
          )}

          <PhoneBody
            chat={chat}
            onPress={pressButton}
            customerLang={customer.language === 'pt' ? 'pt-BR' : 'es'}
            timeZone={timeZone}
          />

          {!isDesktop && active && <BottomSheet stepN={stepIndex(state.last?.progress)}>{cardAndTrace}</BottomSheet>}

          <Composer
            value={draft}
            onChange={setDraft}
            onSend={chat.sendText}
            canSend={state.phase === 'ready' && !state.applyingSwitches}
            disabled={!active}
            buttonsOnly={buttonsOnly}
            inputRef={inputRef}
          />
        </section>

        {isDesktop && <aside className="chat-layout__case" aria-label={t('card.title')}>{cardAndTrace}</aside>}
      </main>
    </div>
  )
}

function lastInputMode(chat: UseChat): 'free_text' | 'buttons_only' {
  const entries = chat.state.entries
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i]
    if (e.kind === 'system') return e.input_mode
  }
  return 'free_text'
}

function PhoneBody({
  chat,
  onPress,
  customerLang,
  timeZone,
}: {
  chat: UseChat
  onPress: UseChat['pressButton']
  customerLang: string
  timeZone?: string
}) {
  const { t } = useI18n()
  const { state } = chat

  if (state.phase === 'starting') {
    return (
      <div className="phone__center" role="status">
        <span className="spinner" aria-hidden="true" /> {t('chat.opening')}
      </div>
    )
  }
  if (state.phase === 'start_failed') {
    return (
      <div className="phone__center">
        <div className="notice notice--error" role="alert">
          <IconAlert size={16} />
          <div>
            <p>
              <strong>{t('chat.openFailed')}</strong>
            </p>
            <p>{t(errorMessageKey(state.error?.code ?? 'unexpected'))}</p>
            <div className="notice__actions">
              <button type="button" className="btn btn--primary btn--small" onClick={chat.retry}>
                <IconRefresh size={14} /> {t('chat.retry')}
              </button>
              <a className="btn btn--ghost btn--small" href="#/">
                {t('soon.back')}
              </a>
            </div>
          </div>
        </div>
      </div>
    )
  }
  if (state.phase === 'expired') {
    return (
      <div className="phone__center">
        <div className="notice notice--warn" role="alert">
          <IconAlert size={16} />
          <div>
            <p>
              <strong>{t('error.session_expired.title')}</strong>
            </p>
            <p>{t('error.session_expired.body')}</p>
            <div className="notice__actions">
              <button type="button" className="btn btn--primary btn--small" onClick={chat.restart}>
                <IconRefresh size={14} /> {t('chat.restart')}
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // Retry follows the failed turn (pending), not the notice: closing the notice keeps it (FE-03).
  const retryEntryId = state.phase === 'ready' && state.pending ? state.pending.entryId : null
  return (
    <>
      <MessageList
        entries={state.entries}
        usedButtonIds={state.usedButtonIds}
        canPress={state.phase === 'ready' && !state.applyingSwitches}
        onPress={onPress}
        onRetry={chat.retry}
        retryEntryId={retryEntryId}
        customerLang={customerLang}
        timeZone={timeZone}
      />
      <div className="phone__status" role="status" aria-live="polite">
        {state.phase === 'sending' && (
          <span className={`typing ${state.slow ? 'typing--slow' : ''}`}>
            <span className="typing__dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            {state.slow ? t('chat.slow') : t('chat.typing')}
          </span>
        )}
      </div>
      {state.phase === 'limit' && (
        <div className="notice notice--warn" role="alert">
          <IconAlert size={16} />
          <div>
            <p>
              <strong>{t('error.session_limit.title')}</strong>
            </p>
            <p>{t('error.session_limit.body')}</p>
            <div className="notice__actions">
              <button type="button" className="btn btn--primary btn--small" onClick={chat.restart}>
                <IconRefresh size={14} /> {t('chat.restart')}
              </button>
            </div>
          </div>
        </div>
      )}
      {state.phase !== 'limit' && state.error && (
        <div className="notice notice--error" role="alert">
          <IconAlert size={16} />
          <p>{t(errorMessageKey(state.error.code, state.error.canRetry))}</p>
          <div className="notice__actions">
            {state.error.canRetry && (
              <button type="button" className="btn btn--primary btn--small" onClick={chat.retry}>
                <IconRefresh size={14} /> {t('chat.retry')}
              </button>
            )}
            <button type="button" className="btn btn--ghost btn--icon" onClick={chat.dismissError} aria-label={t('common.dismiss')}>
              <IconX size={14} />
            </button>
          </div>
        </div>
      )}
    </>
  )
}

function BottomSheet({ stepN, children }: { stepN: number; children: ReactNode }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const label = STEPS[Math.min(stepN, 3)]
  return (
    <div
      className={`sheet ${open ? 'sheet--open' : ''}`}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) setOpen(false)
      }}
    >
      {open && <div className="sheet__backdrop" onClick={() => setOpen(false)} aria-hidden="true" />}
      <div className="sheet__panel">
        <button
          type="button"
          className="sheet__handle"
          aria-expanded={open}
          aria-controls="case-sheet"
          onClick={() => setOpen((v) => !v)}
        >
          <span className="sheet__grip" aria-hidden="true" />
          <span className="sheet__summary">
            <strong>{open ? t('card.sheetClose') : t('card.sheetOpen')}</strong>
            <span className="muted small">
              {t('card.sheetSummary', { n: Math.min(stepN + 1, 4), step: t(`step.${label}`) })}
            </span>
          </span>
          <IconChevron size={16} />
        </button>
        <div id="case-sheet" className="sheet__body" hidden={!open}>
          {children}
        </div>
      </div>
    </div>
  )
}
