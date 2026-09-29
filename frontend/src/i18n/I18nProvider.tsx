import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { Language } from '../api/types'
import { I18nContext, loadStrings, neededPart, stringsReady, translate, uiLocales, useStrings, type I18nValue, type StringsPart } from './index'

// A view with its own language (chat: the customer's; console: the analyst's choice) mounts a
// nested provider. It reports its language to the root provider so what lives outside the view
// (the skip link in App, <html lang>) follows the language on screen (CX-06).
const ReportLang = createContext<((lang: Language | null) => void) | null>(null)

const htmlLang = (l: Language) => (l === 'pt' ? 'pt-BR' : 'es')

export function I18nProvider({
  initial = 'es',
  strings,
  onLangChange,
  children,
}: {
  initial?: Language
  /** Strings a nested view's provider needs ('views' for the chat and the console). */
  strings?: StringsPart
  /** Called after the user switches language (the console keeps it for the tab). */
  onLangChange?: (lang: Language) => void
  children: ReactNode
}) {
  const [lang, setLangState] = useState<Language>(initial)
  const [nestedLang, setNestedLang] = useState<Language | null>(null)
  const report = useContext(ReportLang)
  // A nested provider (chat, console) lives inside the view's Suspense: it waits for its
  // language's strings there (João's chat loads Portuguese only, never the Spanish views). The
  // root provider starts in Spanish, whose first-screen strings are in the bundle, and never
  // suspends (there is no boundary above it).
  useStrings(lang, report ? (strings ?? neededPart()) : null)
  // Switching language first loads that language's strings (lazy chunk), then switches: the
  // screen never shows a half-translated page. The last click wins.
  const switchSeq = useRef(0)
  const setLang = useCallback(
    (l: Language) => {
      const seq = ++switchSeq.current
      const apply = () => {
        if (seq !== switchSeq.current) return
        setLangState(l)
        onLangChange?.(l)
      }
      const part = strings ?? neededPart()
      if (stringsReady(l, part)) apply()
      else loadStrings(l, part).then(apply, () => {
        // The chunk did not load (offline): keep the current language.
      })
    },
    [onLangChange, strings],
  )

  // Nested: tell the root; the root owns <html lang>.
  useEffect(() => {
    if (!report) return
    report(lang)
    return () => report(null)
  }, [report, lang])

  const pageLang = nestedLang ?? lang
  useEffect(() => {
    if (!report) document.documentElement.lang = htmlLang(pageLang)
  }, [report, pageLang])

  const value = useMemo<I18nValue>(
    () => ({ lang, locale: uiLocales[lang], pageLang, setLang, t: (key, vars) => translate(lang, key, vars) }),
    [lang, pageLang, setLang],
  )
  return (
    <ReportLang.Provider value={report ?? setNestedLang}>
      <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
    </ReportLang.Provider>
  )
}
