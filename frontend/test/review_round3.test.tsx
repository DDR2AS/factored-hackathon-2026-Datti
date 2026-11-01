// Review round of 30 sep (console and front-end findings SEC-08, CX-02..CX-17, H4).
// Every payload is SYNTHETIC (test/fixtures.ts). One test (or group) per finding id.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClientError, type ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { DecisionRequest } from '../src/api/types'
import App from '../src/App'
import { CaseCard } from '../src/components/CaseCard'
import { saveAnalystSession } from '../src/console/auth'
import { neutralize } from '../src/console/decision'
import { emptyDraft, setDraft } from '../src/console/drafts'
import { formatDate, formatMoney } from '../src/format'
import { I18nProvider } from '../src/i18n/I18nProvider'
import type { TurnSnapshot } from '../src/session'
import { ConsoleView } from '../src/views/ConsoleView'
import { analystDetail, caseView, chatResponse, decisionResponse, listItem, listResponse } from './fixtures'

type Fn = ReturnType<typeof vi.fn>
type FakeConsoleApi = ApiClient & { listAnalystCases: Fn; getAnalystCase: Fn; decide: Fn }

function fakeApi(overrides: Partial<Record<keyof ApiClient, unknown>> = {}): FakeConsoleApi {
  return {
    health: vi.fn(async () => ({ status: 'ok' as const, stage: 'local', deps: 'ok' as const })),
    createSession: vi.fn(),
    chat: vi.fn(),
    getCase: vi.fn(),
    isChatInFlight: () => false,
    listAnalystCases: vi.fn(async () => listResponse([listItem()])),
    getAnalystCase: vi.fn(async () => analystDetail()),
    decide: vi.fn(async (_id: string, req: DecisionRequest) => decisionResponse(req)),
    ...overrides,
  } as FakeConsoleApi
}

function signIn() {
  saveAnalystSession({ token: 'local-token-synthetic', provider: 'local', signed_in_at: '2026-09-30T15:00:00Z' })
}

function renderConsole(api: ApiClient, caseId: string | null = 'EV-1A2B3C4D') {
  return render(
    <ApiContext.Provider value={api}>
      <ConsoleView caseId={caseId} />
    </ApiContext.Provider>,
  )
}

const err = (code: ApiClientError['code'], status: number | null, retryable = false) =>
  new ApiClientError({ code, status, retryable, message: code })

const openCase = () => screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })

function snapshot(over: Partial<TurnSnapshot> = {}): TurnSnapshot {
  const r = chatResponse()
  return {
    progress: r.progress,
    case_card: null,
    lane: null,
    degraded: [],
    trace_id: r.trace_id,
    trace_summary: r.trace_summary,
    client_latency_ms: 950,
    turn: 1,
    ...over,
  }
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

// ---------------------------------------------------------------- SEC-08
describe('SEC-08 neutralize() covers every invisible format character', () => {
  it('marks U+061C, U+00AD, the Mongolian separator, CGJ, line separators, Hangul fillers and tags', () => {
    const chars = ['؜', '­', '᠎', '͏', ' ', ' ', 'ᅟ', 'ᅠ', 'ㅤ', 'ﾠ', '⁪', '\u{E0041}']
    const out = neutralize(`fin ${chars.join('')} x`)
    for (const c of chars) {
      expect(out).not.toContain(c)
      expect(out).toContain(`[U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}]`)
    }
    // Ordinary text, accents and emoji stay as they are.
    expect(neutralize('João · 450 MXN ñ 🙂')).toBe('João · 450 MXN ñ 🙂')
  })

  it('the console shows the stored statement with the markers', async () => {
    signIn()
    renderConsole(
      fakeApi({
        getAnalystCase: vi.fn(async () => analystDetail({ case: { customer_statement: 'me cobraron como 450 ؜­ fin' } })),
      }),
    )
    await openCase()
    const claimed = screen.getByTestId('console-claimed')
    expect(within(claimed).getByText(/\[U\+061C\]\[U\+00AD\] fin/)).toBeTruthy()
  })
})

// ---------------------------------------------------------------- CX-02
describe('CX-02 reject + request information asks to confirm and shows the exact text', () => {
  async function startReject(user: ReturnType<typeof userEvent.setup>) {
    await openCase()
    await user.click(screen.getByRole('button', { name: 'Rechazar' }))
    await user.type(screen.getByLabelText(/Motivo del rechazo/), 'Falta saber si alguien más usa la tarjeta')
    await user.click(screen.getByRole('radio', { name: 'Pedir información al cliente' }))
  }

  it('with an empty question: shows the template (register of the country) with the stamp and sends only after confirming', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi()
    renderConsole(api)
    await startReject(user)
    // The investigator draft is marked as not going out while rejecting.
    const figure = document.querySelector('.draft') as HTMLElement
    expect(figure.className).toContain('draft--discarded')
    expect(within(figure).getByText(/No se enviará/)).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Rechazar la recomendación' }))
    expect(api.decide).not.toHaveBeenCalled()
    const confirm = screen.getByRole('group', { name: /Confirmar el rechazo/ })
    expect(
      within(confirm).getByText(
        'Para avanzar con tu caso EV-1A2B3C4D necesitamos un dato más. Respóndenos por este chat y lo agregamos a tu caso. Respuesta aprobada por una persona del equipo. En esta demo ningún dinero se mueve.',
      ),
    ).toBeTruthy()
    await user.click(within(confirm).getByRole('button', { name: /Confirmar y enviar/ }))
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    const req = api.decide.mock.calls[0][1] as DecisionRequest
    expect(req.action).toBe('reject')
    expect(req.next).toBe('request_information')
    expect(req.reply).toBeUndefined()
  })

  it('usted for CO and the written question when there is one', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi({ getAnalystCase: vi.fn(async () => analystDetail({ case: { country: 'CO' } })) })
    renderConsole(api)
    await startReject(user)
    await user.click(screen.getByRole('button', { name: 'Rechazar la recomendación' }))
    let confirm = screen.getByRole('group', { name: /Confirmar el rechazo/ })
    expect(within(confirm).getByText(/^Para avanzar con su caso EV-1A2B3C4D necesitamos un dato más\. Respóndanos por este chat y lo agregamos a su caso\./)).toBeTruthy()
    await user.click(within(confirm).getByRole('button', { name: /Volver/ }))
    await user.type(screen.getByLabelText(/Pregunta para el cliente/), '¿Alguien más usa la tarjeta?')
    await user.click(screen.getByRole('button', { name: 'Rechazar la recomendación' }))
    confirm = screen.getByRole('group', { name: /Confirmar el rechazo/ })
    expect(
      within(confirm).getByText('¿Alguien más usa la tarjeta? Respuesta aprobada por una persona del equipo. En esta demo ningún dinero se mueve.'),
    ).toBeTruthy()
    expect(api.decide).not.toHaveBeenCalled()
  })

  it('escalating sends nothing to the customer and still goes straight through', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi()
    renderConsole(api)
    await openCase()
    await user.click(screen.getByRole('button', { name: 'Rechazar' }))
    await user.type(screen.getByLabelText(/Motivo del rechazo/), 'Parece fraude')
    await user.click(screen.getByRole('radio', { name: /Escalar/ }))
    await user.click(screen.getByRole('button', { name: 'Rechazar la recomendación' }))
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
  })
})

// ---------------------------------------------------------------- CX-04
describe('CX-04 focus follows each decision step', () => {
  it('Aprobar moves focus to "Confirmar y enviar" and the result notice gets it after sending', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(fakeApi())
    await openCase()
    await user.click(screen.getByRole('button', { name: /Aprobar y notificar/ }))
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /Confirmar y enviar/ }))
    await user.click(screen.getByRole('button', { name: /Confirmar y enviar/ }))
    const notice = await screen.findByText(/Decisión registrada/)
    await waitFor(() => expect(document.activeElement).toBe(notice.closest('[tabindex="-1"]')))
  })

  it('Esc while editing returns focus to "Editar"', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(fakeApi())
    await openCase()
    await user.click(screen.getByRole('button', { name: 'Editar' }))
    fireEvent.keyDown(screen.getByLabelText('Respuesta al cliente (ES)'), { key: 'Escape' })
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Editar' })))
  })

  it('Ctrl+Enter in Editar and a 409 leave focus on the notice, not on <body>', async () => {
    const user = userEvent.setup()
    signIn()
    const decide = vi.fn().mockRejectedValueOnce(err('conflict', 409))
    renderConsole(fakeApi({ decide }))
    await openCase()
    await user.click(screen.getByRole('button', { name: 'Editar' }))
    const box = screen.getByLabelText('Respuesta al cliente (ES)') as HTMLTextAreaElement
    await user.clear(box)
    await user.type(box, 'Texto editado (sintético)')
    await user.type(screen.getByLabelText('Motivo de la edición (obligatorio)'), 'Tono')
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true })
    const alert = await screen.findByText(/El caso cambió mientras se revisaba|ya fue decidido/)
    await waitFor(() => expect(document.activeElement).toBe(alert.closest('[tabindex="-1"]')))
    expect(document.activeElement).not.toBe(document.body)
  })
})

// ---------------------------------------------------------------- CX-05
describe('CX-05 the sign-in dialog is modal', () => {
  async function expire(user: ReturnType<typeof userEvent.setup>) {
    signIn()
    const listAnalystCases = vi
      .fn()
      .mockResolvedValueOnce(listResponse([listItem()]))
      .mockRejectedValue(err('session_expired', 401))
    renderConsole(fakeApi({ listAnalystCases }))
    await openCase()
    await user.click(screen.getByRole('button', { name: 'Editar' }))
    await user.click(screen.getByRole('button', { name: 'Actualizar la cola' }))
    return screen.findByRole('dialog')
  }

  it('the header (language switch, Salir) is inert too, and Tab stays inside the dialog', async () => {
    const user = userEvent.setup()
    const dialog = await expire(user)
    expect(dialog.closest('[inert]')).toBeNull()
    expect(screen.getByRole('button', { name: 'Español', hidden: true }).closest('[inert]')).not.toBeNull()
    for (let i = 0; i < 6; i++) {
      await user.tab()
      expect(dialog.contains(document.activeElement)).toBe(true)
    }
    for (let i = 0; i < 3; i++) {
      await user.tab({ shift: true })
      expect(dialog.contains(document.activeElement)).toBe(true)
    }
  })

  it('Esc behind the dialog does not cancel the edit', async () => {
    const user = userEvent.setup()
    await expire(user)
    const box = screen.getByLabelText('Respuesta al cliente (ES)', { selector: 'textarea' })
    fireEvent.keyDown(box, { key: 'Escape' })
    expect(screen.getByLabelText('Respuesta al cliente (ES)', { selector: 'textarea' })).toBeTruthy()
  })
})

// ---------------------------------------------------------------- CX-06
describe('CX-06 language of the console and of the backend texts', () => {
  it('the skip link follows the language chosen in the console', async () => {
    const user = userEvent.setup()
    signIn()
    window.location.hash = '#/consola/EV-1A2B3C4D'
    render(
      <I18nProvider initial="es">
        <ApiContext.Provider value={fakeApi()}>
          <App />
        </ApiContext.Provider>
      </I18nProvider>,
    )
    await openCase()
    expect(screen.getByText('Saltar al contenido')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Português' }))
    expect(await screen.findByText('Pular para o conteúdo')).toBeTruthy()
    expect(document.documentElement.lang).toBe('pt-BR')
  })

  it('texts written by the backend in Spanish are marked lang="es" in the Portuguese console', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(
      fakeApi({
        getAnalystCase: vi.fn(async () =>
          analystDetail({ case: { handoff: { queue: null, priority: null, open_questions: ['Confirmar que no hubo un cambio de tarifa'], facts_verified: ['Cargo verificado en el registro'] } } }),
        ),
      }),
    )
    await openCase()
    await user.click(screen.getByRole('button', { name: 'Português' }))
    const esOf = (text: string | RegExp) => screen.getByText(text).closest('[lang]')?.getAttribute('lang')
    expect(esOf('Confirmar que no hubo un cambio de tarifa')).toBe('es')
    expect(esOf('Cargo verificado en el registro')).toBe('es')
    expect(esOf(/El cargo de 449.90 MXN en SUPER AHORRO SA está aplicado/)).toBe('es')
    expect(esOf('Gasto mensual típico 3200 MXN')).toBe('es')
  })
})

// ---------------------------------------------------------------- CX-07
describe('CX-07 the live card never shows the raw outcome', () => {
  it.each([
    ['edit', 'Respondido por una persona del equipo'],
    ['approve', 'Respondido por una persona del equipo'],
    ['reject', 'Revisado por una persona del equipo'],
  ])('outcome %s', (outcome, text) => {
    render(
      <I18nProvider>
        <CaseCard snapshot={snapshot({ case_card: caseView({ outcome }), lane: 'B' })} locale="es-MX" country="MX" />
      </I18nProvider>,
    )
    expect(screen.queryByText(outcome)).toBeNull()
    expect(screen.getByText(text)).toBeTruthy()
  })
})

// ---------------------------------------------------------------- CX-08
describe('CX-08 raw values translated in the Spanish console', () => {
  it('transaction status, match band and "no rule" are Spanish', async () => {
    const user = userEvent.setup()
    signIn()
    const d = analystDetail({ case: { match: { candidates: [], chosen_id: null, p_top1: 0.5, band: 'choose', model_version: null } } })
    d.context.evidence_txns = [{ ...d.case.evidence.transaction!, status: 'approved' }]
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => d) }))
    await openCase()
    const table = screen.getByRole('table')
    expect(within(table).queryByText('approved')).toBeNull()
    expect(within(table).getByText('Aprobado')).toBeTruthy()
    await user.click(screen.getByRole('tab', { name: 'Riesgo' }))
    expect(screen.queryByText(/banda choose/)).toBeNull()
    expect(screen.getByText(/banda elegir entre candidatos/)).toBeTruthy()
    expect(screen.getByText('Ninguna')).toBeTruthy()
  })
})

// ---------------------------------------------------------------- CX-09
describe('CX-09 one date style and no cents in COP/ARS whole amounts', () => {
  it('dates look the same for MX, CO and AR', () => {
    expect(formatDate('2026-06-09', 'es-CO')).toBe(formatDate('2026-06-09', 'es-MX'))
    expect(formatDate('2026-06-09', 'es-AR')).toBe(formatDate('2026-06-09', 'es-MX'))
  })

  it('COP and ARS whole amounts have no cents; real cents stay', () => {
    const n = (s: string) => s.replace(/\s/g, ' ')
    expect(n(formatMoney('51500.00', 'COP', 'es-CO'))).toBe('$ 51.500')
    expect(n(formatMoney('145000.00', 'ARS', 'es-AR'))).toBe('$ 145.000')
    expect(n(formatMoney('8900.50', 'ARS', 'es-AR'))).toBe('$ 8900,50'.replace('8900', '8.900'))
    expect(n(formatMoney('449.90', 'MXN', 'es-MX'))).toBe('$449.90')
  })

  it('the chat card writes the time as hh:mm with the zone in words', () => {
    render(
      <I18nProvider>
        <CaseCard
          snapshot={snapshot({ case_card: caseView({ created_at: '2026-09-30T06:16:00Z' }), lane: 'B' })}
          locale="es-MX"
          country="MX"
        />
      </I18nProvider>,
    )
    // Node's ICU writes "sept" where browsers write "sep".
    expect(screen.getByText(/^30 sept? 2026, 00:16 \(hora del centro de México\)$/)).toBeTruthy()
  })
})

// ---------------------------------------------------------------- CX-10
describe('CX-10 Trazas stays inside the console and the language is kept', () => {
  it('with a case open, Trazas opens the Traza tab of that case without leaving', async () => {
    const user = userEvent.setup()
    signIn()
    window.location.hash = '#/consola/EV-1A2B3C4D'
    renderConsole(fakeApi())
    await openCase()
    const rail = screen.getByRole('navigation', { name: /Navegación de la consola|consola/i })
    await user.click(within(rail).getByRole('button', { name: 'Trazas' }))
    expect(window.location.hash).toBe('#/consola/EV-1A2B3C4D')
    expect(screen.getByRole('tab', { name: 'Traza' }).getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Traza' }))
  })

  it('without a case, Trazas is not a link out of the console', async () => {
    signIn()
    renderConsole(fakeApi(), null)
    await screen.findByRole('listbox')
    const rail = screen.getByRole('navigation', { name: /consola/i })
    expect(within(rail).queryByRole('link', { name: 'Trazas' })).toBeNull()
  })

  it('the language chosen in the console survives leaving and coming back', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi()
    const first = renderConsole(api)
    await openCase()
    await user.click(screen.getByRole('button', { name: 'Português' }))
    first.unmount()
    renderConsole(api)
    await openCase()
    expect(screen.getByRole('button', { name: 'Português' }).getAttribute('aria-pressed')).toBe('true')
  })
})

// ---------------------------------------------------------------- CX-11
describe('CX-11 on a phone the opened case gets focus', () => {
  it('focuses the case heading when the viewport is narrow', async () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn((q: string) => ({ matches: q.includes('max-width'), media: q, addEventListener() {}, removeEventListener() {}, onchange: null, addListener() {}, removeListener() {}, dispatchEvent: () => false })),
    )
    signIn()
    renderConsole(fakeApi())
    const h1 = await openCase()
    await waitFor(() => expect(document.activeElement).toBe(h1))
  })

  it('does not steal focus from the queue on a wide screen', async () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, onchange: null, addListener() {}, removeListener() {}, dispatchEvent: () => false })),
    )
    signIn()
    renderConsole(fakeApi())
    const h1 = await openCase()
    await act(async () => {})
    expect(document.activeElement).not.toBe(h1)
  })
})

// ---------------------------------------------------------------- CX-12
describe('CX-12 the header Salir button keeps a name when its label is hidden', () => {
  it('has aria-label', async () => {
    signIn()
    renderConsole(fakeApi())
    await openCase()
    const header = document.querySelector('.console-topbar') as HTMLElement
    const btn = within(header).getByRole('button', { name: 'Salir' })
    expect(btn.getAttribute('aria-label')).toBe('Salir')
  })
})

// ---------------------------------------------------------------- CX-14
describe('CX-14 decide without scrolling past the whole case', () => {
  it('the case header offers a jump to the decision and moves focus there', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(fakeApi())
    await openCase()
    const head = screen.getByRole('heading', { level: 1, name: /EV-1A2B3C4D/ }).closest('header') as HTMLElement
    await user.click(within(head).getByRole('button', { name: /Ir a la decisión/ }))
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /Aprobar y notificar/ }))
  })

  it('raw JSON of the actions sits in a collapsed <details>', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(fakeApi())
    await openCase()
    await user.click(screen.getByRole('tab', { name: 'Acciones' }))
    const panel = screen.getByRole('tabpanel')
    const pre = panel.querySelector('pre') as HTMLElement
    expect(pre.closest('details')).not.toBeNull()
    expect((pre.closest('details') as HTMLDetailsElement).open).toBe(false)
  })

  it('evidence field keys get readable labels', async () => {
    signIn()
    renderConsole(fakeApi())
    await openCase()
    const rec = screen.getByTestId('evidence-TX-DEMO-LU-01')
    expect(within(rec).getByText('Moneda')).toBeTruthy()
    expect(within(rec).queryByText('currency')).toBeNull()
  })
})

// ---------------------------------------------------------------- CX-17
describe('CX-17 screen reader and text details', () => {
  it('(1)(2) the queue clock is not a live region and the heading is only "Cola"', async () => {
    signIn()
    renderConsole(fakeApi(), null)
    // No count while the queue is still loading.
    expect(screen.queryByText('0 casos')).toBeNull()
    await screen.findByRole('listbox')
    await screen.findByText(/Actualizado/)
    expect(screen.getByText(/Actualizado/).closest('[role="status"],[aria-live="polite"]')).toBeNull()
    expect(screen.getByRole('heading', { level: 2, name: 'Cola' })).toBeTruthy()
    expect(screen.getByText('1 caso')).toBeTruthy()
  })

  it('(3)(5) the Corregir select is labeled and Confirmar is replaced once confirmed', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(fakeApi())
    await openCase()
    await user.click(screen.getByRole('button', { name: 'Corregir' }))
    expect(screen.getByRole('combobox', { name: 'Elegir la clase correcta' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Confirmar' }))
    expect(screen.getByText('Confirmado')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Confirmar' })).toBeNull()
    await user.click(screen.getByRole('button', { name: /Deshacer/ }))
    expect(screen.getByRole('button', { name: 'Confirmar' })).toBeTruthy()
  })

  it('(4) every section and aside inside main has a name', async () => {
    signIn()
    renderConsole(fakeApi(), null)
    await screen.findByRole('listbox')
    const main = document.getElementById('main') as HTMLElement
    for (const el of Array.from(main.querySelectorAll('section, aside'))) {
      expect(el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')).toBeTruthy()
    }
  })

  it('(4) also with a case open', async () => {
    signIn()
    renderConsole(fakeApi())
    await openCase()
    const main = document.getElementById('main') as HTMLElement
    for (const el of Array.from(main.querySelectorAll('section, aside'))) {
      expect(el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')).toBeTruthy()
    }
  })

  it('(8) the empty chat card does not say "Nos falta: Nada por ahora"', () => {
    render(
      <I18nProvider>
        <CaseCard snapshot={null} locale="es-MX" country="MX" />
      </I18nProvider>,
    )
    expect(screen.queryByText(/Nos falta/)).toBeNull()
  })

  it('(9) when the case is gone, the kept draft can still be copied', async () => {
    const user = userEvent.setup()
    signIn()
    setDraft('EV-1A2B3C4D', { ...emptyDraft(), mode: 'edit', editText: 'Borrador a conservar (sintético)', editReason: 'Tono' })
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => Promise.reject(err('not_found', 404))) }))
    await screen.findByText(/No tienes acceso a este caso|no existe|acceso/)
    expect((screen.getByLabelText(/Borrador guardado/) as HTMLTextAreaElement).value).toBe('Borrador a conservar (sintético)')
    await user.click(screen.getByRole('button', { name: /Copiar el borrador/ }))
    await expect(navigator.clipboard.readText()).resolves.toBe('Borrador a conservar (sintético)')
  })
})

// ---------------------------------------------------------------- H4
describe('H4 a context that could not be read is not shown as an empty history', () => {
  it('says "No disponible (la herramienta no respondió)" for each part in context.unavailable', async () => {
    const user = userEvent.setup()
    signIn()
    const d = analystDetail()
    d.context.cards = []
    d.context.evidence_txns = []
    d.context.prior_contacts = []
    d.context.unavailable = ['cards', 'evidence_txns', 'prior_contacts']
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => d) }))
    await openCase()
    const NA = 'No disponible (la herramienta no respondió)'
    expect(screen.queryByText('No hay movimientos relacionados.')).toBeNull()
    expect(screen.getByText(NA)).toBeTruthy()
    await user.click(screen.getByRole('tab', { name: 'Historial' }))
    expect(screen.queryByText('Sin contactos previos.')).toBeNull()
    expect(screen.getByText(NA)).toBeTruthy()
    await user.click(screen.getByRole('tab', { name: 'Riesgo' }))
    expect(screen.queryByText('Sin tarjetas.')).toBeNull()
    expect(screen.getByText(NA)).toBeTruthy()
  })

  it('only the parts listed are unavailable; the others keep their plain empty state', async () => {
    const user = userEvent.setup()
    signIn()
    const d = analystDetail()
    d.context.cards = []
    d.context.evidence_txns = []
    d.context.prior_contacts = []
    d.context.unavailable = ['prior_contacts']
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => d) }))
    await openCase()
    expect(screen.getByText('No hay movimientos relacionados.')).toBeTruthy()
    await user.click(screen.getByRole('tab', { name: 'Riesgo' }))
    expect(screen.getByText('Sin tarjetas.')).toBeTruthy()
    await user.click(screen.getByRole('tab', { name: 'Historial' }))
    expect(screen.getByText('No disponible (la herramienta no respondió)')).toBeTruthy()
  })

  it('a server without the marker: an empty evidence list next to the disputed charge still reads as unavailable', async () => {
    signIn()
    const d = analystDetail()
    d.context.evidence_txns = []
    delete (d.context as { unavailable?: unknown }).unavailable
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => d) }))
    await openCase()
    expect(screen.getByText('No disponible (la herramienta no respondió)')).toBeTruthy()
  })

  it('a readable context keeps the plain empty states', async () => {
    const user = userEvent.setup()
    signIn()
    const d = analystDetail()
    d.context.evidence_txns = [{ ...d.case.evidence.transaction! }]
    d.context.prior_contacts = []
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => d) }))
    await openCase()
    await user.click(screen.getByRole('tab', { name: 'Historial' }))
    expect(screen.getByText('Sin contactos previos.')).toBeTruthy()
  })
})
