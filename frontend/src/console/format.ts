// Formatting helpers of the console (Intl only; no free text is parsed).

import type { Language } from '../api/types'
import { formatMoney } from '../format'

const COUNTRY_LOCALES: Record<string, string> = { MX: 'es-MX', CO: 'es-CO', AR: 'es-AR', BR: 'pt-BR' }

/**
 * Locale for the customer's money and dates in the console: the ANALYST's UI language (month
 * names and words match the rest of the screen) with the customer's country conventions when
 * the UI is Spanish (es-AR, es-CO, es-MX separators). A Portuguese-speaking customer in AR is
 * shown "1 jun 2026 · $ 12.500,00" in a Spanish console, not "1 de jun. de 2026".
 */
export function customerLocale(uiLanguage: Language, country: string | null | undefined): string {
  if (uiLanguage === 'pt') return 'pt-BR'
  return (country && country !== 'BR' && COUNTRY_LOCALES[country]) || 'es'
}

export interface SlaInfo {
  /** Localized "en 2 días" / "hace 3 horas". */
  label: string
  overdue: boolean
  /** Less than 4 hours left. */
  soon: boolean
}

/** Remaining time to clock.breach_at, with Intl.RelativeTimeFormat. null without a deadline. */
export function slaInfo(breachAt: string | null | undefined, now: number, locale: string): SlaInfo | null {
  if (!breachAt) return null
  const t = Date.parse(breachAt)
  if (Number.isNaN(t)) return null
  const diff = t - now
  const abs = Math.abs(diff)
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'always', style: 'short' })
  const sign = diff < 0 ? -1 : 1
  let label: string
  if (abs >= 86_400_000) label = rtf.format(sign * Math.floor(abs / 86_400_000), 'day')
  else if (abs >= 3_600_000) label = rtf.format(sign * Math.floor(abs / 3_600_000), 'hour')
  else label = rtf.format(sign * Math.max(1, Math.floor(abs / 60_000)), 'minute')
  return { label, overdue: diff < 0, soon: diff >= 0 && diff < 4 * 3_600_000 }
}

/** hh:mm in the browser's zone (for "desactualizado hh:mm" and "decidido a las hh:mm"). */
export function hhmm(value: string | number | null | undefined, locale: string): string {
  if (value === null || value === undefined) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(d)
}

export function dateTime(value: string | null | undefined, locale: string): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(d)
}

export function percent(p: number | null | undefined, locale: string): string {
  if (p === null || p === undefined || Number.isNaN(p)) return '—'
  return new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 0 }).format(p)
}

/**
 * Money with its ISO code always visible: es-MX writes MXN as "$449.90", which the analyst
 * cannot tell from USD, so the code is appended when the locale printed only a symbol.
 */
export function moneyWithCode(amount: string | null, currency: string | null, locale: string): string {
  const text = formatMoney(amount, currency, locale)
  if (!currency || text === '—' || text.includes(currency)) return text
  return `${text} ${currency}`
}

/** A decimal string with two decimals and the locale's separators, without a currency. */
export function plainAmount(amount: string | null, locale: string): string {
  if (amount === null || amount === '') return '—'
  const trimmed = amount.trim()
  if (!/^-?\d+(\.\d+)?$/.test(trimmed)) return amount
  // Intl.NumberFormat accepts decimal strings (keeps exact digits); TS types lag behind.
  return new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(
    trimmed as unknown as number,
  )
}

/** A scalar field value as text; objects and arrays as indented JSON text (never HTML). */
export function valueText(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}
