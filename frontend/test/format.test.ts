import { describe, expect, it } from 'vitest'
import { formatCountdown, formatDate, formatDateTimeWithZone, formatMoney } from '../src/format'

const norm = (s: string) => s.replace(/\s/g, ' ')

describe('format', () => {
  it('formats decimal strings with Intl and the currency', () => {
    expect(norm(formatMoney('449.90', 'MXN', 'es-MX'))).toBe('$449.90')
    expect(norm(formatMoney('1234.5', 'BRL', 'pt-BR'))).toBe('R$ 1.234,50')
  })

  it('keeps the exact digits of long decimal strings', () => {
    expect(norm(formatMoney('12345678901234.56', 'USD', 'en-US'))).toBe('$12,345,678,901,234.56')
  })

  it('shows invalid amounts as text instead of NaN', () => {
    expect(formatMoney('abc', 'MXN', 'es-MX')).toBe('abc MXN')
    expect(formatMoney(null, 'MXN', 'es-MX')).toBe('—')
  })

  it('does not shift calendar dates across time zones', () => {
    expect(formatDate('2026-06-12', 'es-MX')).toMatch(/12/)
  })

  it('includes a time zone name', () => {
    const s = formatDateTimeWithZone('2026-09-30T15:00:00Z', 'es-MX', 'America/Mexico_City')
    expect(s).toMatch(/9:00|09:00/)
    expect(s).toMatch(/GMT|CST|UTC/)
  })

  it('countdown never goes negative', () => {
    expect(formatCountdown(61_000)).toBe('01:01')
    expect(formatCountdown(-5)).toBe('00:00')
  })
})
