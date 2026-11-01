// Analyst console (#/consola and #/consola/<case_id>), RF-16 to RF-18.
// Layout: rail | queue | case | tabs at >= 1280 px; at 1024-1279 px the tabs go under the
// case; below 1024 px everything stacks. Spanish by default with an ES/PT switch.
// A 401 opens a sign-in dialog OVER the work: the queue, the case and the decision draft stay
// mounted (and drafts live in memory, see console/drafts.ts), so nothing typed is lost.

import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useApi } from '../api/context'
import type { DecisionResponse, Language } from '../api/types'
import { LanguageSwitch, SyntheticBanner } from '../components/Chrome'
import { IconAlert, IconBack, IconChart, IconInbox, IconLock, IconLogout, IconPulse, IconRefresh, IconScale } from '../components/Icons'
import { clearAnalystSession, loadAnalystSession, type AnalystSession } from '../console/auth'
import { CaseWorkspace } from '../console/CaseWorkspace'
import { getDraft } from '../console/drafts'
import { hhmm } from '../console/format'
import { LoginForm } from '../console/LoginScreen'
import { Queue } from '../console/Queue'
import { alertsOf } from '../console/slaAlerts'
import { useCaseDetail, useQueue } from '../console/useConsoleData'
import { errorMessageKey } from '../chat/errors'
import { useI18n } from '../i18n'
import { I18nProvider } from '../i18n/I18nProvider'
import { hrefFor } from '../router'
import '../styles/views.css'
import '../styles/console.css'

// The console language is kept for this tab (a per-viewer convenience): leaving to another view
// and coming back (or Atrás) keeps it (CX-10). Storage may be unavailable: Spanish then.
const LANG_KEY = 'ev.console.lang'

function loadConsoleLang(): Language {
  try {
    return window.sessionStorage.getItem(LANG_KEY) === 'pt' ? 'pt' : 'es'
  } catch {
    return 'es'
  }
}

function saveConsoleLang(lang: Language): void {
  try {
    window.sessionStorage.setItem(LANG_KEY, lang)
  } catch {
    // ignore: the choice then lasts only while the console is open
  }
}

export function ConsoleView({ caseId }: { caseId: string | null }) {
  const [initial] = useState(loadConsoleLang)
  return (
    <I18nProvider initial={initial} strings="views" onLangChange={saveConsoleLang}>
      <ConsoleGate caseId={caseId} />
    </I18nProvider>
  )
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** The sign-in dialog over the work: the page behind is inert and Tab cycles inside (CX-05). */
function ReloginDialog({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab' || !ref.current) return
    const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE))
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (e.shiftKey && (active === first || !ref.current.contains(active))) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && (active === last || !ref.current.contains(active))) {
      e.preventDefault()
      first.focus()
    }
  }
  return (
    <div ref={ref} className="modal" role="dialog" aria-modal="true" aria-labelledby="relogin-title" onKeyDown={onKeyDown}>
      <div className="modal__panel card">{children}</div>
    </div>
  )
}

function ConsoleGate({ caseId }: { caseId: string | null }) {
  const { t } = useI18n()
  const [session, setSession] = useState<AnalystSession | null>(() => loadAnalystSession())
  const [authLost, setAuthLost] = useState(false)

  const onUnauthorized = useCallback(() => {
    clearAnalystSession()
    setAuthLost(true)
  }, [])

  if (!session) {
    return (
      <div className="page page--console-login">
        <ConsoleTopBar signedIn={false} onSignOut={() => {}} />
        <SyntheticBanner />
        <main className="console-login" id="main" tabIndex={-1}>
          <div className="card">
            <LoginForm reason="first" onSignedIn={setSession} />
          </div>
          <a className="btn btn--ghost btn--small" href="#/">
            <IconBack size={14} /> {t('soon.back')}
          </a>
        </main>
      </div>
    )
  }

  return (
    <>
      <ConsoleShell
        caseId={caseId}
        paused={authLost}
        onUnauthorized={onUnauthorized}
        onSignOut={() => {
          clearAnalystSession()
          setSession(null)
          setAuthLost(false)
        }}
      />
      {authLost && (
        <ReloginDialog>
          <LoginForm
            titleId="relogin-title"
            reason="expired"
            onSignedIn={(s) => {
              setSession(s)
              setAuthLost(false)
            }}
          />
        </ReloginDialog>
      )}
    </>
  )
}

function ConsoleTopBar({ signedIn, onSignOut }: { signedIn: boolean; onSignOut: () => void }) {
  const { t } = useI18n()
  return (
    <header className="topbar console-topbar">
      <a className="brand" href="#/" aria-label={t('brand.home')}>
        <span className="brand__mark" aria-hidden="true">
          EV
        </span>
        <span className="brand__text">
          <span className="brand__product">{t('nav.console')}</span>
          <span className="brand__bank">
            {t('brand.bank')} · <em>{t('brand.demo')}</em>
          </span>
        </span>
      </a>
      <div className="topbar__actions">
        {signedIn && <span className="tag">{t('console.signedInLocal')}</span>}
        <LanguageSwitch />
        {signedIn && (
          // aria-label: on phones .btn__label is display:none and the icon is aria-hidden (CX-12).
          <button type="button" className="btn btn--ghost btn--small" onClick={onSignOut} aria-label={t('console.signOut')}>
            <IconLock size={14} /> <span className="btn__label">{t('console.signOut')}</span>
          </button>
        )}
      </div>
    </header>
  )
}

function Rail({ onSignOut, onTrace }: { onSignOut: () => void; onTrace: (() => void) | null }) {
  const { t } = useI18n()
  return (
    <nav className="rail" aria-label={t('console.rail')}>
      <a className="rail__item" href={hrefFor({ name: 'console', caseId: null })} aria-current="page">
        <span className="rail__icon" aria-hidden="true">
          <IconInbox size={18} />
        </span>
        <span>{t('console.queue')}</span>
      </a>
      {/* The trace of the open case, inside the console (#/traza only lists this tab's chat
          session, which a console tab never has: CX-10). */}
      <button
        type="button"
        className="rail__item"
        onClick={onTrace ?? undefined}
        aria-disabled={onTrace ? undefined : true}
        aria-describedby={onTrace ? undefined : 'rail-trace-hint'}
      >
        <span className="rail__icon" aria-hidden="true">
          <IconPulse size={18} />
        </span>
        <span>{t('nav.traces')}</span>
      </button>
      {!onTrace && (
        <span id="rail-trace-hint" className="sr-only">
          {t('console.traceNeedsCase')}
        </span>
      )}
      <a className="rail__item" href={hrefFor({ name: 'evaluation' })}>
        <span className="rail__icon" aria-hidden="true">
          <IconChart size={18} />
        </span>
        <span>{t('nav.evaluation')}</span>
      </a>
      <a className="rail__item" href={hrefFor({ name: 'judge' })}>
        <span className="rail__icon" aria-hidden="true">
          <IconScale size={18} />
        </span>
        <span>{t('console.judgeMode')}</span>
      </a>
      <button type="button" className="rail__item rail__item--end" onClick={onSignOut}>
        <span className="rail__icon" aria-hidden="true">
          <IconLogout size={18} />
        </span>
        <span>{t('console.signOut')}</span>
      </button>
    </nav>
  )
}

function useNow(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

function ConsoleShell({
  caseId,
  paused,
  onUnauthorized,
  onSignOut,
}: {
  caseId: string | null
  paused: boolean
  onUnauthorized: () => void
  onSignOut: () => void
}) {
  const { t, locale } = useI18n()
  const api = useApi()
  const now = useNow()
  const { queue, refresh, loadMore } = useQueue(api, { paused, onUnauthorized })
  const selectedItem = queue.items.find((i) => i.case_id === caseId) ?? null
  const detail = useCaseDetail(api, caseId, { paused, onUnauthorized })
  // The queue says the open case changed after the version on screen: re-read it once.
  const shown = detail.detail?.case.updated_at ?? null
  const queueNewer =
    selectedItem && detail.detail && shown && Date.parse(selectedItem.updated_at) > Date.parse(shown) ? selectedItem.updated_at : null
  const reloadDetail = detail.reload
  useEffect(() => {
    if (queueNewer && !paused) void reloadDetail()
  }, [queueNewer, paused, reloadDetail])

  // Reading a case takes it (the server drops its "unassigned" alert): re-read the queue once so
  // the chip does not linger until the next 15 s poll. Keyed by case, so it never loops.
  const refreshRef = useRef(refresh)
  useEffect(() => {
    refreshRef.current = refresh
  })
  const takenStillListed =
    detail.detail && selectedItem && alertsOf(selectedItem).includes('unassigned') ? detail.detail.case.case_id : null
  useEffect(() => {
    if (takenStillListed && !paused) refreshRef.current()
  }, [takenStillListed, paused])

  const open = (id: string) => {
    window.location.hash = hrefFor({ name: 'console', caseId: id })
  }
  const onDecided = (_res: DecisionResponse) => {
    void detail.reload()
    refresh()
  }

  const stale = detail.error !== null && detail.detail !== null && detail.error !== 'session_expired'
  // Rail "Trazas": opens the Traza tab of the open case (a new request each click).
  const [traceRequest, setTraceRequest] = useState(0)
  const caseOnScreen = caseId !== null && detail.detail !== null

  return (
    // While the sign-in dialog is open the WHOLE page behind it is inert, header included (CX-05).
    <div className="page page--console" inert={paused || undefined}>
      <ConsoleTopBar signedIn onSignOut={onSignOut} />
      <SyntheticBanner />
      <div className="console">
        <Rail onSignOut={onSignOut} onTrace={caseOnScreen ? () => setTraceRequest((n) => n + 1) : null} />
        <main className="console-main" id="main" tabIndex={-1}>
          {/* A plain wrapper: the Queue inside is the "Cola" region (a second one with the same name
              failed axe landmark-unique). */}
          <div className="console-queue">
            <Queue
              items={queue.items}
              selectedId={caseId}
              loadedOnce={queue.loadedOnce}
              error={queue.error}
              asOf={queue.asOf}
              hasMore={!!queue.nextCursor}
              loadingMore={queue.loadingMore}
              now={now}
              onOpen={open}
              onRefresh={refresh}
              onLoadMore={() => void loadMore()}
              newAlerts={queue.newAlerts}
            />
          </div>

          {!caseId ? (
            <section className="console-center console-center--empty" aria-label={t('console.caseArea')}>
              <div className="empty-state">
                <h1>{t('console.pickTitle')}</h1>
                <p className="muted">{t('console.pickBody')}</p>
                <ul className="bullets">
                  <li>{t('console.pickKeys')}</li>
                  <li>{t('console.pickTabs')}</li>
                </ul>
              </div>
            </section>
          ) : detail.detail ? (
            <CaseWorkspace
                key={detail.detail.case.case_id}
                api={api}
                detail={detail.detail}
                reload={detail.reload}
                onDecided={onDecided}
                onUnauthorized={onUnauthorized}
                interactive={!paused}
                now={now}
                traceRequest={traceRequest}
                notice={
                  stale ? (
                    <div className="notice notice--warn console-stale" role="status">
                      <IconAlert size={14} />
                      <p>{t('console.stale', { time: hhmm(detail.fetchedAt, locale) })}</p>
                      <button type="button" className="btn btn--ghost btn--small" onClick={() => void detail.reload()}>
                        <IconRefresh size={14} /> {t('chat.retry')}
                      </button>
                    </div>
                  ) : null
                }
              />
          ) : (
            <section className="console-center console-center--empty" aria-label={t('console.caseArea')}>
              {detail.error && detail.error !== 'session_expired' ? (
                <div className={`notice ${detail.error === 'not_authorized' || detail.error === 'not_found' ? 'notice--warn' : 'notice--error'}`} role="alert">
                  <IconAlert size={16} />
                  <div>
                    <p>
                      {t(
                        detail.error === 'not_authorized' || detail.error === 'not_found'
                          ? 'console.error.forbidden'
                          : detail.error === 'not_implemented'
                            ? 'console.error.notImplemented'
                            : errorMessageKey(detail.error),
                      )}
                    </p>
                    <div className="notice__actions">
                      <button type="button" className="btn btn--ghost btn--small" onClick={() => void detail.reload()}>
                        <IconRefresh size={14} /> {t('chat.retry')}
                      </button>
                      <a className="btn btn--ghost btn--small" href={hrefFor({ name: 'console', caseId: null })}>
                        {t('console.backToQueue')}
                      </a>
                    </div>
                    {caseId && (detail.error === 'not_authorized' || detail.error === 'not_found') && <KeptDraft caseId={caseId} />}
                  </div>
                </div>
              ) : (
                <p className="block__empty" role="status">
                  <span className="spinner" aria-hidden="true" /> {t('console.loadingCase')}
                </p>
              )}
            </section>
          )}
        </main>
      </div>
    </div>
  )
}

/** The case is gone (403/404) after signing in again: the draft the dialog promised to keep is
 *  still shown, read-only, with a copy button, instead of vanishing (CX-17 (9)). */
function KeptDraft({ caseId }: { caseId: string }) {
  const { t } = useI18n()
  const id = useId()
  const [copied, setCopied] = useState(false)
  const d = getDraft(caseId)
  const text = (d.mode === 'reject' ? d.rejectReply : (d.editText ?? '')).trim() || d.rejectReply.trim()
  if (!text) return null
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="kept-draft">
      <label className="field" htmlFor={id}>
        <span className="field__label">{t('console.keptDraft')}</span>
        <textarea id={id} className="field__input field__textarea" readOnly rows={4} value={text} />
      </label>
      <button type="button" className="btn btn--ghost btn--small" onClick={() => void copy()}>
        {t('console.copyDraft')}
      </button>
      <span className="muted small" role="status">
        {copied ? t('console.copied') : ''}
      </span>
    </div>
  )
}
