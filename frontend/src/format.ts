// Money and dates with Intl, following the customer's locale.
// Money arrives as a decimal string (never a float) plus an ISO 4217 currency.

const DECIMAL_RE = /^-?\d+(\.\d+)?$/

// Currencies shown without cents when the amount is whole (CX-09: "$ 51.500", not "$ 51.500,00";
// real cents, e.g. an ARS fee of 8900.50, are kept).
const WHOLE_WITHOUT_CENTS = new Set(['COP', 'ARS'])

type FormatInput = Parameters<Intl.NumberFormat['format']>[0]

export function formatMoney(amount: string | null, currency: string | null, locale: string): string {
  if (amount === null || amount === '') return '—'
  const trimmed = amount.trim()
  if (!DECIMAL_RE.test(trimmed)) return currency ? `${amount} ${currency}` : amount
  try {
    const whole = !!currency && WHOLE_WITHOUT_CENTS.has(currency.toUpperCase()) && /^-?\d+(\.0+)?$/.test(trimmed)
    const nf = currency
      ? new Intl.NumberFormat(locale, { style: 'currency', currency, ...(whole ? { minimumFractionDigits: 0, maximumFractionDigits: 0 } : {}) })
      : new Intl.NumberFormat(locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 })
    // Intl.NumberFormat accepts decimal strings (keeps exact digits); TS types lag behind.
    return nf.format(trimmed as unknown as FormatInput)
  } catch {
    return currency ? `${trimmed} ${currency}` : trimmed
  }
}

export function formatUsd(value: number, locale: string): string {
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 4,
      maximumFractionDigits: 4,
    }).format(value)
  } catch {
    return `${value.toFixed(4)} USD`
  }
}

const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/

/** Dates use one style in every Spanish locale ("9 jun 2026"): es-CO would write "9/06/2026" and
 *  es-AR "9 jun 2026", so MX, CO and AR cases looked different on one screen (CX-09). Money keeps
 *  the country's separators. */
function dateLocale(locale: string): string {
  return locale === 'es' || locale.startsWith('es-') ? 'es' : locale
}

/** A calendar date (YYYY-MM-DD) or ISO timestamp shown as a date, without shifting days. */
export function formatDate(value: string | null, locale: string): string {
  if (!value) return '—'
  if (DATE_ONLY_RE.test(value)) {
    const d = new Date(`${value}T12:00:00Z`)
    if (Number.isNaN(d.getTime())) return value
    return new Intl.DateTimeFormat(dateLocale(locale), { dateStyle: 'medium', timeZone: 'UTC' }).format(d)
  }
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return new Intl.DateTimeFormat(dateLocale(locale), { dateStyle: 'medium' }).format(d)
}

const COUNTRY_ZONES: Record<string, string> = {
  MX: 'America/Mexico_City',
  CO: 'America/Bogota',
  AR: 'America/Argentina/Buenos_Aires',
  BR: 'America/Sao_Paulo',
}

export function zoneForCountry(country: string | null | undefined): string | undefined {
  return country ? COUNTRY_ZONES[country] : undefined
}

/** Timestamp with the time zone name, e.g. "30 sep 2026, 10:00 GMT-6". */
export function formatDateTimeWithZone(value: string | null, locale: string, timeZone?: string): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  // dateStyle/timeStyle cannot be combined with timeZoneName, so the parts are explicit.
  try {
    return new Intl.DateTimeFormat(locale, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZone,
      timeZoneName: 'short',
    }).format(d)
  } catch {
    return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(d)
  }
}

/**
 * "30 sep 2026, 00:16 (hora del centro de México)": the date, a 24 h clock and the zone in
 * words, in the customer's zone (CX-09). Without a zone label it falls back to the short zone
 * name of formatDateTimeWithZone.
 */
export function formatDateTimeInZone(value: string | null, locale: string, timeZone?: string, zoneLabel?: string): string {
  if (!value) return '—'
  if (!timeZone || !zoneLabel) return formatDateTimeWithZone(value, locale, timeZone)
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  try {
    const date = new Intl.DateTimeFormat(dateLocale(locale), { dateStyle: 'medium', timeZone }).format(d)
    const time = new Intl.DateTimeFormat(dateLocale(locale), { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone }).format(d)
    return `${date}, ${time} (${zoneLabel})`
  } catch {
    return formatDateTimeWithZone(value, locale, timeZone)
  }
}

/** hh:mm in the customer's zone (the same zone the case card uses), not the browser's. */
export function formatClock(value: string, locale: string, timeZone?: string): string {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  try {
    return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', timeZone }).format(d)
  } catch {
    return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(d)
  }
}

/** Plain count with the locale's grouping (pt-BR 1.000; Spanish keeps 1000 per CLDR/RAE). */
export function formatCount(n: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(n)
}

export function formatMs(ms: number, locale: string): string {
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(Math.round(ms))} ms`
}

/** mm:ss for the session clock; never negative. */
export function formatCountdown(msLeft: number): string {
  const total = Math.max(0, Math.floor(msLeft / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
