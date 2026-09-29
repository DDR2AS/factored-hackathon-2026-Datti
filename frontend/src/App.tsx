import { lazy, Suspense, useEffect, type ReactNode } from 'react'
import { translate, useI18n, useStrings } from './i18n'
import { useHashRoute } from './router'
import { RouteBoundary } from './components/RouteBoundary'
import { ComingSoon, NotFound } from './views/ComingSoon'
import { JudgeLanding } from './views/JudgeLanding'

// The chat (and its card, trace strip, API state), the console and the trace view load on
// demand, with their strings and styles, so the landing stays light (RNF-11: < 250 KB).
const ChatView = lazy(() => import('./views/ChatView').then((m) => ({ default: m.ChatView })))
const ConsoleView = lazy(() => import('./views/ConsoleView').then((m) => ({ default: m.ConsoleView })))
const TraceView = lazy(() => import('./views/TraceView').then((m) => ({ default: m.TraceView })))

/** Waits (inside the view's Suspense) for the views' strings of the page language. The chat and
 *  the console have their own language (nested provider with strings="views"); the trace view
 *  speaks the page's. */
function ViewStrings({ children }: { children: ReactNode }) {
  const { lang } = useI18n()
  useStrings(lang, 'views')
  return children
}

export default function App() {
  const route = useHashRoute()
  const { t, pageLang } = useI18n()

  useEffect(() => {
    window.scrollTo?.(0, 0)
  }, [route.name])

  return (
    <>
      {/* In the language on screen (the console or chat may have their own), CX-06. */}
      <a className="skip-link" href="#main" lang={pageLang === 'pt' ? 'pt-BR' : 'es'} onClick={(e) => {
        e.preventDefault()
        document.getElementById('main')?.focus()
      }}>
        {translate(pageLang, 'app.skip')}
      </a>
      <RouteBoundary key={route.name}>{renderRoute()}</RouteBoundary>
    </>
  )

  function renderRoute() {
    switch (route.name) {
      case 'judge':
        return <JudgeLanding />
      case 'chat':
        return (
          <Suspense fallback={<div className="page-loading" role="status">{t('chat.opening')}</div>}>
            <ChatView key={route.demoKey} demoKey={route.demoKey} />
          </Suspense>
        )
      case 'console':
        return (
          <Suspense fallback={<div className="page-loading" role="status">{t('console.opening')}</div>}>
            <ConsoleView caseId={route.caseId} />
          </Suspense>
        )
      case 'trace':
        return (
          <Suspense fallback={<div className="page-loading" role="status">{t('console.opening')}</div>}>
            <ViewStrings>
              <TraceView key={route.traceId ?? ''} traceId={route.traceId} />
            </ViewStrings>
          </Suspense>
        )
      case 'evaluation':
        return <ComingSoon nameKey="nav.evaluation" dateKey="avail.fri2" />
      case 'notFound':
        return <NotFound />
    }
  }
}
