// RNF-11: the first bundle carries only the Spanish strings of the first screen (es.core.ts);
// the views' strings and Portuguese load on demand. test/setup.ts preloads every dictionary for
// the other tests, so these tests use a fresh module registry (vi.resetModules) where only the
// first-screen Spanish strings are loaded, like a browser opening #/.
// Every payload is SYNTHETIC.
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../src/api/client'
import { es as fullEs } from '../src/i18n/es'
import { esCore } from '../src/i18n/es.core'
import { pt as fullPt } from '../src/i18n/pt'
import { ptCore } from '../src/i18n/pt.core'
import { chatResponse, sessionResponse } from './fixtures'

async function fresh() {
  vi.resetModules()
  const i18n = await import('../src/i18n')
  const rtl = await import('@testing-library/react')
  const { default: App } = await import('../src/App')
  const { I18nProvider } = await import('../src/i18n/I18nProvider')
  const { ApiContext } = await import('../src/api/context')
  return { i18n, rtl, App, I18nProvider, ApiContext }
}

function fakeApi(): ApiClient {
  return {
    health: vi.fn(async () => ({ status: 'ok' as const, stage: 'local', deps: 'ok' as const })),
    createSession: vi.fn(async () => sessionResponse({ customer: { display_name: 'João', language: 'pt', locale: 'pt-BR', country: 'MX' } })),
    chat: vi.fn(async () => chatResponse()),
    getCase: vi.fn(),
    isChatInFlight: () => false,
    listAnalystCases: vi.fn(),
    getAnalystCase: vi.fn(),
    decide: vi.fn(),
  } as unknown as ApiClient
}

const viewKeys = Object.keys(fullEs).filter((k) => !(k in esCore))

describe('dictionaries split for the first page (RNF-11)', () => {
  it('core + views = the whole dictionary, in both languages, with the same keys', () => {
    expect(Object.keys(ptCore).sort()).toEqual(Object.keys(esCore).sort())
    expect(Object.keys(fullPt).sort()).toEqual(Object.keys(fullEs).sort())
    expect(viewKeys.length).toBeGreaterThan(400)
    // The Suspense fallbacks and the error boundary speak before any view loads.
    for (const k of ['chat.opening', 'console.opening', 'app.loadFailed', 'app.reload', 'app.skip']) {
      expect(Object.keys(esCore)).toContain(k)
    }
  })

  it('a fresh page has only the Spanish first-screen strings; the rest loads on demand', async () => {
    const { i18n } = await fresh()
    expect(i18n.stringsReady('es', 'core')).toBe(true)
    expect(i18n.stringsReady('es', 'views')).toBe(false)
    expect(i18n.stringsReady('pt', 'core')).toBe(false)
    const p1 = i18n.loadStrings('pt', 'core')
    expect(i18n.loadStrings('pt', 'core')).toBe(p1) // one download per part
    await p1
    expect(i18n.translate('pt', 'landing.start', { name: 'João' })).toBe('Conversar como João')
    expect(i18n.stringsReady('pt', 'views')).toBe(false)
    await i18n.loadStrings('pt', 'views')
    expect(i18n.translate('pt', 'traceView.title')).toBe(fullPt['traceView.title'])
    expect(i18n.hasKey('evfield.amount')).toBe(true)
  })

  it('the landing renders from the first-screen strings only, switches to PT after loading it, and a view waits for its strings', async () => {
    const { rtl, App, I18nProvider, ApiContext, i18n } = await fresh()
    const { act, render, screen, fireEvent, cleanup, waitFor } = rtl
    window.location.hash = '#/'
    // A view suspends until its strings arrive: the retry runs in the act queue, so the first
    // render is awaited.
    await act(async () => {
      render(
        <ApiContext.Provider value={fakeApi()}>
          <I18nProvider initial="es">
            <App />
          </I18nProvider>
        </ApiContext.Provider>,
      )
    })
    try {
      expect(await screen.findByText('API disponible · etapa local')).toBeTruthy()
      const html = document.body.innerHTML
      // No view key leaked as raw text (a key used by the landing but missing from es.core.ts).
      expect(viewKeys.filter((k) => html.includes(k))).toEqual([])
      expect(html).not.toMatch(/>[a-z]+\.[a-zA-Z]+(\.[a-zA-Z_]+)*</)
      expect(i18n.stringsReady('pt', 'core')).toBe(false)

      // Portuguese loads when the judge asks for it; the page switches once it is there.
      fireEvent.click(screen.getByRole('button', { name: 'Português' }))
      expect(await screen.findByText(ptCore['landing.title'])).toBeTruthy()
      expect(i18n.stringsReady('pt', 'core')).toBe(true)
      expect(i18n.stringsReady('pt', 'views')).toBe(false)

      // The trace view needs the views' strings of the language on screen: it waits for them.
      window.location.hash = '#/traza'
      fireEvent(window, new HashChangeEvent('hashchange'))
      expect(await screen.findByRole('heading', { level: 1, name: fullPt['traceView.title'] })).toBeTruthy()
      expect(i18n.stringsReady('pt', 'views')).toBe(true)
      expect(i18n.stringsReady('es', 'views')).toBe(false) // Spanish views never loaded

      // Back to Spanish on a view: the Spanish views' strings load before the switch.
      fireEvent.click(screen.getByRole('button', { name: 'Español' }))
      await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: fullEs['traceView.title'] })).toBeTruthy())
    } finally {
      cleanup()
    }
  })

  it("João's chat (PT) opens from a Spanish landing: it waits for the Portuguese strings only", async () => {
    const { rtl, App, I18nProvider, ApiContext, i18n } = await fresh()
    const { act, render, screen, cleanup } = rtl
    window.location.hash = '#/chat/joao'
    // A view suspends until its strings arrive: the retry runs in the act queue, so the first
    // render is awaited.
    await act(async () => {
      render(
        <ApiContext.Provider value={fakeApi()}>
          <I18nProvider initial="es">
            <App />
          </I18nProvider>
        </ApiContext.Provider>,
      )
    })
    try {
      expect(await screen.findByText(fullPt['trace.empty'])).toBeTruthy()
      expect(i18n.stringsReady('pt', 'views')).toBe(true)
      expect(screen.queryByText(fullEs['trace.empty'])).toBeNull()
      // The chat has its own language: the Spanish views' strings are never downloaded.
      expect(i18n.stringsReady('es', 'views')).toBe(false)
    } finally {
      cleanup()
    }
  })
})
