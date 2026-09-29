import { createContext, useContext } from 'react'
import type { Language } from '../api/types'
import { esCore } from './es.core'
import type { MessageKey, Messages } from './es'

export type { MessageKey, Messages }

// RNF-11 (first page < 250 KB): only the Spanish strings of the first screen ship in the initial
// bundle (es.core.ts). The views' strings (es.ts) and every Portuguese string (pt.core.ts,
// pt.ts) load on demand, and only for a language that is on screen. A part is either all
// loaded or not loaded: a provider waits (Suspense) instead of showing another language.

/** 'core': first screen (landing, chrome). 'views': core + chat, console and trace views. */
export type StringsPart = 'core' | 'views'

type Table = Partial<Record<MessageKey, string>>

const table: Record<Language, Table> = { es: { ...esCore }, pt: {} }
const loaded = new Set<string>(['es:core'])
const inflight = new Map<string, Promise<void>>()
let viewsWanted = false

const LOADERS: Record<string, () => Promise<Table>> = {
  'es:views': () => import('./es').then((m) => m.es),
  'pt:core': () => import('./pt.core').then((m) => m.ptCore),
  'pt:views': () => import('./pt').then((m) => m.pt),
}

const partsOf = (part: StringsPart): StringsPart[] => (part === 'views' ? ['core', 'views'] : ['core'])

/** Add strings to a language (the loaders use it; tests preload the whole dictionaries with it). */
export function registerStrings(lang: Language, strings: Table, parts: StringsPart[] = ['core', 'views']): void {
  Object.assign(table[lang], strings)
  for (const p of parts) loaded.add(`${lang}:${p}`)
}

export function stringsReady(lang: Language, part: StringsPart = 'core'): boolean {
  return partsOf(part).every((p) => loaded.has(`${lang}:${p}`))
}

// A part's load is one promise, marked with its state (status), so a provider can tell a
// loaded part from one still on its way without waiting a tick.
type Tracked = Promise<void> & { status?: 'pending' | 'fulfilled' | 'rejected' }
const READY: Tracked = Object.assign(Promise.resolve(), { status: 'fulfilled' as const })
const failures = new Map<string, unknown>()

/** Loads what `part` needs for `lang` (a lazy chunk per language and part). */
export function loadStrings(lang: Language, part: StringsPart = 'core'): Promise<void> {
  if (part === 'views') viewsWanted = true
  // One promise per part: every provider waiting for it shares the same download.
  const id = `${lang}:${part}`
  const known = inflight.get(id)
  if (known) return known
  if (stringsReady(lang, part)) return READY
  failures.delete(id)
  const p: Tracked = Promise.all(
    partsOf(part)
      .filter((q) => !loaded.has(`${lang}:${q}`))
      .map((q) => LOADERS[`${lang}:${q}`]().then((strings) => registerStrings(lang, strings, [q]))),
  ).then(
    () => {
      p.status = 'fulfilled'
    },
    (e: unknown) => {
      inflight.delete(id) // a failed chunk (offline) can be tried again (language switch, reload)
      failures.set(id, e)
      p.status = 'rejected'
      throw e
    },
  )
  p.status = 'pending'
  inflight.set(id, p)
  return p
}

/** The part a language needs now: 'views' once any view has been opened in this tab. */
export function neededPart(): StringsPart {
  return viewsWanted ? 'views' : 'core'
}

/**
 * Suspends the caller until `lang` has `part`: the nearest Suspense shows its fallback. A chunk
 * that failed to load goes to the route's error boundary (reload). null: nothing to wait for.
 * It throws the pending promise (Suspense protocol) instead of calling use(), which on React 19
 * must then be called on every later render too and hung the lazy views under act() in tests.
 */
export function useStrings(lang: Language, part: StringsPart | null): void {
  if (part === null) return
  const failed = failures.get(`${lang}:${part}`)
  if (failed !== undefined) throw failed
  const p: Tracked = loadStrings(lang, part)
  if (p.status !== 'fulfilled') throw p
}

/** Locale for UI chrome (numbers in the trace, clock). Customer data uses the session locale. */
export const uiLocales: Record<Language, string> = { es: 'es', pt: 'pt-BR' }

export type Vars = Record<string, string | number>

export function translate(lang: Language, key: MessageKey, vars?: Vars): string {
  const template = table[lang][key] ?? table.es[key] ?? key
  if (!vars) return template
  return template.replace(/\{(\w+)\}/g, (m, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m,
  )
}

/** Keys that are built at runtime (rule ids, statuses) fall back to a default key. */
export function hasKey(key: string): key is MessageKey {
  return Object.prototype.hasOwnProperty.call(table.es, key) || Object.prototype.hasOwnProperty.call(table.pt, key)
}

export interface I18nValue {
  lang: Language
  locale: string
  /** Language of what is on screen: a nested view's (chat, console) or this provider's. */
  pageLang: Language
  setLang: (lang: Language) => void
  t: (key: MessageKey, vars?: Vars) => string
}

export const I18nContext = createContext<I18nValue>({
  lang: 'es',
  locale: uiLocales.es,
  pageLang: 'es',
  setLang: () => {},
  t: (key, vars) => translate('es', key, vars),
})

export function useI18n(): I18nValue {
  return useContext(I18nContext)
}
