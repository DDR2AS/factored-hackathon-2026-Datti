import { describe, expect, it, vi } from 'vitest'
import { clearAll, getToken, isExpired, loadSession, loadTranscript, saveSession, saveTranscript } from '../src/session'

const session = {
  token: 'tok-synthetic',
  expires_at: new Date(Date.now() + 60_000).toISOString(),
  demo_key: 'lucia' as const,
  customer: { display_name: 'Lucía', language: 'es' as const, locale: 'es-MX', country: 'MX' as const },
}

describe('session storage', () => {
  it('keeps the token in memory and sessionStorage only', () => {
    const persistent = vi.spyOn(Storage.prototype, 'setItem')
    saveSession(session)
    expect(getToken()).toBe('tok-synthetic')
    expect(window.sessionStorage.getItem('ev.session.v1')).toContain('tok-synthetic')
    // Every write went to sessionStorage (the same Storage prototype backs both in jsdom,
    // so check the instance).
    for (const call of persistent.mock.contexts) expect(call).toBe(window.sessionStorage)
    expect(window.location.href).not.toContain('tok-synthetic')
  })

  it('clearAll wipes token and transcript', () => {
    saveSession(session)
    saveTranscript({ demo_key: 'lucia', entries: [], used_button_ids: ['a'], last: null })
    clearAll()
    expect(getToken()).toBeNull()
    expect(loadSession()).toBeNull()
    expect(loadTranscript('lucia')).toBeNull()
  })

  it('transcript belongs to one demo customer', () => {
    saveTranscript({ demo_key: 'lucia', entries: [], used_button_ids: [], last: null })
    expect(loadTranscript('joao')).toBeNull()
    expect(loadTranscript('lucia')).not.toBeNull()
  })

  it('isExpired', () => {
    expect(isExpired({ expires_at: new Date(Date.now() - 1).toISOString() })).toBe(true)
    expect(isExpired({ expires_at: 'garbage' })).toBe(true)
    expect(isExpired(session)).toBe(false)
  })
})
