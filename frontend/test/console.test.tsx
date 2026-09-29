// Analyst console (#/consola). Every payload is SYNTHETIC (test/fixtures.ts).
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClientError, type ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { AnalystCaseDetail, DecisionRequest } from '../src/api/types'
import App from '../src/App'
import { getAnalystToken, saveAnalystSession } from '../src/console/auth'
import { ConsoleView } from '../src/views/ConsoleView'
import { analystDetail, decisionResponse, listItem, listResponse, report } from './fixtures'

type Fn = ReturnType<typeof vi.fn>
type FakeConsoleApi = ApiClient & { listAnalystCases: Fn; getAnalystCase: Fn; decide: Fn }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

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

afterEach(() => {
  vi.useRealTimers()
})

describe('sign-in', () => {
  it('asks for the local analyst token and keeps it only in memory + sessionStorage', async () => {
    const user = userEvent.setup()
    const api = fakeApi()
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    renderConsole(api, null)
    expect(screen.getByRole('heading', { name: /Entrar a la consola del analista/ })).toBeTruthy()
    // Cognito is prepared but not enabled.
    expect(screen.getByText(/USER_PASSWORD_AUTH/)).toBeTruthy()
    expect(api.listAnalystCases).not.toHaveBeenCalled()
    await user.type(screen.getByLabelText('Token de analista'), 'tok-analyst-synthetic')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    await screen.findByRole('listbox', { name: /Casos en la cola/ })
    expect(getAnalystToken()).toBe('tok-analyst-synthetic')
    expect(window.sessionStorage.getItem('ev.analyst.v1')).toContain('tok-analyst-synthetic')
    // Only sessionStorage was written (the Storage spy sees both; the key must be ours).
    expect(setItem.mock.contexts.every((c) => c === window.sessionStorage)).toBe(true)
    expect(api.listAnalystCases).toHaveBeenCalledTimes(1)
  })
})

describe('queue', () => {
  it('lists cases with lane, language, priority, SLA and "reporte no confiable"', async () => {
    signIn()
    const api = fakeApi({
      listAnalystCases: vi.fn(async () =>
        listResponse([
          listItem({ case_id: 'EV-AAAA0001', display_name: 'Carlos', lane: 'C', priority: 'high', has_report: false, report_reliable: null, breach_at: new Date(Date.now() - 3_600_000).toISOString() }),
          listItem({ case_id: 'EV-AAAA0002', display_name: 'João', language: 'pt', country: 'AR', report_reliable: false }),
          listItem({ case_id: 'EV-AAAA0003', display_name: 'Lucía', status: 'notified' }),
        ]),
      ),
    })
    renderConsole(api, null)
    const list = await screen.findByRole('listbox', { name: /Casos en la cola/ })
    const options = within(list).getAllByRole('option')
    expect(options.map((o) => within(o).getByText(/EV-/).textContent)).toEqual(['EV-AAAA0001', 'EV-AAAA0002', 'EV-AAAA0003'])
    // Answered: no SLA countdown.
    expect(within(options[2]).getByText('Respondido')).toBeTruthy()
    expect(within(options[2]).queryByText(/vence dentro de/)).toBeNull()
    expect(within(options[0]).getByText('Ruta C')).toBeTruthy()
    expect(within(options[0]).getByText('Prioridad alta')).toBeTruthy()
    expect(within(options[0]).getByText(/vencido hace/)).toBeTruthy()
    expect(within(options[0]).getByText('Sin reporte')).toBeTruthy()
    expect(within(options[1]).getByText('PT·AR')).toBeTruthy()
    expect(within(options[1]).getByText('Reporte no confiable')).toBeTruthy()
    expect(within(options[1]).getByText(/vence dentro de/)).toBeTruthy()
    expect(screen.getByText('Elegir un caso de la cola')).toBeTruthy()
  })

  it('shows an empty queue', async () => {
    signIn()
    renderConsole(fakeApi({ listAnalystCases: vi.fn(async () => listResponse([])) }), null)
    expect(await screen.findByText('No hay casos en la cola')).toBeTruthy()
  })

  it('arrows and Enter act only with focus on the queue and open the case', async () => {
    signIn()
    window.location.hash = '#/consola'
    const api = fakeApi({
      listAnalystCases: vi.fn(async () =>
        listResponse([listItem({ case_id: 'EV-AAAA0001' }), listItem({ case_id: 'EV-AAAA0002', display_name: 'João' })]),
      ),
      getAnalystCase: vi.fn(async (id: string) => analystDetail({ case: { case_id: id } })),
    })
    render(
      <ApiContext.Provider value={api}>
        <App />
      </ApiContext.Provider>,
    )
    const list = await screen.findByRole('listbox', { name: /Casos en la cola/ })
    // A global ArrowDown/Enter does nothing (no one-key shortcuts outside the queue).
    fireEvent.keyDown(document.body, { key: 'ArrowDown' })
    fireEvent.keyDown(document.body, { key: 'Enter' })
    expect(window.location.hash).toBe('#/consola')
    list.focus()
    fireEvent.keyDown(list, { key: 'ArrowDown' })
    expect(list.getAttribute('aria-activedescendant')).toBe('q-EV-AAAA0002')
    fireEvent.keyDown(list, { key: 'Enter' })
    expect(window.location.hash).toBe('#/consola/EV-AAAA0002')
    await waitFor(() => expect(api.getAnalystCase).toHaveBeenCalledWith('EV-AAAA0002'))
  })

  it('polls every poll_after_ms, and after a 5xx keeps the list marked "desactualizado"', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    // No jitter: with a random draw the 15 s poll can land at 12 s and the 5 s backoff at 4 s,
    // both inside the first 18.1 s window (the test was flaky: 3 calls instead of 2).
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.5)
    signIn()
    const list = vi
      .fn()
      .mockResolvedValueOnce(listResponse([listItem()]))
      .mockRejectedValueOnce(err('internal', 500, true))
      .mockResolvedValue(listResponse([listItem()]))
    const api = fakeApi({ listAnalystCases: list })
    renderConsole(api, null)
    await screen.findByRole('listbox')
    expect(list).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(18_100) // 15 s +/- 20 %
    })
    expect(list).toHaveBeenCalledTimes(2)
    expect(screen.getByText(/Desactualizado \d{2}:\d{2}/)).toBeTruthy()
    expect(screen.getByRole('option')).toBeTruthy() // the last data stays
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_100) // backoff 5 s +/- 20 %
    })
    expect(list).toHaveBeenCalledTimes(3)
    expect(screen.queryByText(/Desactualizado/)).toBeNull()
    random.mockRestore()
  })

  it('a hidden tab does not poll the queue', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    signIn()
    const api = fakeApi()
    renderConsole(api, null)
    await screen.findByRole('listbox')
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000)
      })
      expect(api.listAnalystCases).toHaveBeenCalledTimes(1)
    } finally {
      Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    }
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(api.listAnalystCases).toHaveBeenCalledTimes(2)
  })
})

describe('case detail', () => {
  it('renders header, predicted reason, package, report and the provisional investigator label', async () => {
    signIn()
    renderConsole(fakeApi())
    const title = await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    expect(title.textContent).toContain('Lucía')
    expect(screen.getByText('unrecognized_low_risk')).toBeTruthy()
    const intent = screen.getByRole('group', { name: 'Motivo previsto' })
    expect(within(intent).getByText('Disputa de un cargo')).toBeTruthy()
    expect(within(intent).getByText('p 91 %')).toBeTruthy()
    expect(within(intent).getByText('compuerta: aceptar')).toBeTruthy()
    expect(within(intent).getByText('m2-rule@0')).toBeTruthy()
    // Said (unverified) and verified are separate blocks.
    const said = screen.getByTestId('console-claimed')
    expect(within(said).getByText('como 450 en el súper el 12, no lo reconozco')).toBeTruthy()
    expect(within(said).getByText('Sin verificar')).toBeTruthy()
    const verified = screen.getByTestId('console-verified')
    expect(within(verified).getByText('SUPER AHORRO SA')).toBeTruthy()
    expect(within(verified).getByText(/449\.90/)).toBeTruthy()
    // Report: findings, hypotheses, recommendation; stub labeled as not GenAI.
    expect(screen.getByText('Investigador provisional (reglas) · sin GenAI')).toBeTruthy()
    expect(screen.getByText('Abrir un contracargo')).toBeTruthy()
    expect(screen.getByText('Nombre de comercio desconocido')).toBeTruthy()
    expect(screen.getByText('¿La tarjeta estuvo en poder de la clienta ese día?')).toBeTruthy()
    expect(screen.queryByTestId('unreliable-strip')).toBeNull()
  })

  it('a citation chip opens the record in the Evidencia tab', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(fakeApi())
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('tab', { name: 'Riesgo' }))
    expect(screen.queryByTestId('evidence-MERCHANT:M-77')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Abrir el registro MERCHANT:M-77 en Evidencia' }))
    expect(screen.getByRole('tab', { name: 'Evidencia' }).getAttribute('aria-selected')).toBe('true')
    const rec = screen.getByTestId('evidence-MERCHANT:M-77')
    expect(rec.className).toContain('evrec--focus')
    expect(document.activeElement).toBe(rec)
    expect(within(rec).getByText('SUPER AHORRO SA · 0 compras previas')).toBeTruthy()
  })

  it('Alt+1..5 switch tabs', async () => {
    signIn()
    renderConsole(fakeApi())
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    // The shortcut listener is a passive effect: under a loaded run it can attach a moment after
    // the heading shows (flaky 1 in ~10 full runs on 30 sep), so the first press is retried.
    await waitFor(() => {
      fireEvent.keyDown(window, { key: '2', code: 'Digit2', altKey: true })
      expect(screen.getByRole('tab', { name: 'Riesgo' }).getAttribute('aria-selected')).toBe('true')
    })
    fireEvent.keyDown(window, { key: '5', code: 'Digit5', altKey: true })
    expect(screen.getByRole('tab', { name: 'Traza' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByText('stub-g2-deterministic · stub-provisional')).toBeTruthy()
    // A bare digit is not a shortcut.
    fireEvent.keyDown(window, { key: '1', code: 'Digit1' })
    expect(screen.getByRole('tab', { name: 'Traza' }).getAttribute('aria-selected')).toBe('true')
  })

  it('lane C shows the full handoff and no decision buttons', async () => {
    signIn()
    const detail = analystDetail({
      case: {
        lane: 'C',
        lane_reason: 'regulator_or_legal',
        status: 'handed_off',
        lifecycle_step: null,
        handoff: { queue: 'regulator', priority: 'high', open_questions: ['Pide respuesta formal'], facts_verified: ['Tres quejas previas'] },
      },
      report: null,
      allowed_actions: [],
    })
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => detail) }))
    const handoff = await screen.findByTestId('console-handoff')
    expect(within(handoff).getByText('Regulador')).toBeTruthy()
    expect(within(handoff).getByText('Alta')).toBeTruthy()
    expect(within(handoff).getByText('Tres quejas previas')).toBeTruthy()
    expect(within(handoff).getByText('Pide respuesta formal')).toBeTruthy()
    // Shown once: the package does not repeat the handoff facts or questions in lane C.
    expect(screen.getAllByText('Tres quejas previas')).toHaveLength(1)
    expect(screen.getAllByText('Pide respuesta formal')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Aprobar y notificar' })).toBeNull()
    expect(screen.getByText(/Ruta C: solo lectura/)).toBeTruthy()
  })

  it('403 is neutral and does not say whether the case exists', async () => {
    signIn()
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => Promise.reject(err('not_authorized', 403))) }))
    expect(await screen.findByText('Sin acceso a este caso o a esta vista.')).toBeTruthy()
    expect(screen.queryByText(/no existe/i)).toBeNull()
  })

  it('shows customer and report text literally and makes bidi controls visible', async () => {
    signIn()
    const detail = analystDetail({
      case: { customer_statement: '<img src=x onerror=alert(1)> pago‮evil' },
    })
    const { container } = renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => detail) }))
    await screen.findByText(/<img src=x onerror=alert\(1\)> pago\[U\+202E\]evil/)
    expect(container.querySelector('img')).toBeNull()
  })
})

describe('decision', () => {
  it('approve asks for confirmation and sends version, a uuid and the confirmed label', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi()
    renderConsole(api)
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Confirmar' })) // predicted reason is right
    await user.click(screen.getByRole('button', { name: 'Aprobar y notificar' }))
    expect(api.decide).not.toHaveBeenCalled()
    expect(screen.getByText(/En esta demo ningún dinero se mueve/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Confirmar y enviar' }))
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    const [caseId, req] = api.decide.mock.calls[0] as [string, DecisionRequest]
    expect(caseId).toBe('EV-1A2B3C4D')
    expect(req.client_decision_id).toMatch(UUID)
    expect(req).toEqual({
      client_decision_id: req.client_decision_id,
      version: 3,
      action: 'approve',
      labels: { intent_confirmed: true },
    })
    expect(await screen.findByText(/Decisión registrada: Aprobado/)).toBeTruthy()
    expect(api.getAnalystCase.mock.calls.length).toBeGreaterThanOrEqual(2) // re-read after deciding
  })

  it('edit shows the diff, needs a reason, and Ctrl+Enter sends reply + reason', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi()
    renderConsole(api)
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Corregir' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Elegir la clase correcta' }), 'dispute_fee')
    await user.click(screen.getByRole('button', { name: 'Editar' }))
    const box = screen.getByLabelText('Respuesta al cliente (ES)') as HTMLTextAreaElement
    expect(box.value).toBe(report().draft_reply.text)
    await user.clear(box)
    await user.type(box, 'Hola Lucía, revisamos el cargo. Abrimos una disputa. (sintético)')
    const diff = screen.getByTestId('edit-diff')
    expect(diff.querySelector('del')).toBeTruthy()
    expect(diff.querySelector('ins')).toBeTruthy()
    // Without a reason the edit cannot be sent, not even with Ctrl+Enter.
    const send = screen.getByRole('button', { name: /Guardar la edición y enviar/ }) as HTMLButtonElement
    expect(send.disabled).toBe(true)
    expect(screen.getByText('Falta el motivo.')).toBeTruthy()
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true })
    expect(api.decide).not.toHaveBeenCalled()
    await user.type(screen.getByLabelText('Motivo de la edición (obligatorio)'), 'Texto más corto')
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    const req = api.decide.mock.calls[0][1] as DecisionRequest
    expect(req).toEqual({
      client_decision_id: req.client_decision_id,
      version: 3,
      action: 'edit',
      reply: { language: 'es', text: 'Hola Lucía, revisamos el cargo. Abrimos una disputa. (sintético)' },
      reason: 'Texto más corto',
      labels: { intent_class: 'dispute_fee' },
    })
    expect(req.next).toBeUndefined()
  })

  it('Esc cancels an edit', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(fakeApi())
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Editar' }))
    const box = screen.getByLabelText('Respuesta al cliente (ES)')
    fireEvent.keyDown(box, { key: 'Escape' })
    expect(screen.queryByLabelText('Respuesta al cliente (ES)')).toBeNull()
    expect(screen.getByRole('button', { name: 'Aprobar y notificar' })).toBeTruthy()
  })

  it('reject needs a reason and the next step; escalate sends no reply', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi()
    renderConsole(api)
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Rechazar' }))
    const send = screen.getByRole('button', { name: 'Rechazar la recomendación' }) as HTMLButtonElement
    expect(send.disabled).toBe(true)
    await user.type(screen.getByLabelText('Motivo del rechazo (obligatorio)'), 'Falta evidencia del comercio')
    expect(send.disabled).toBe(true)
    await user.click(screen.getByLabelText('Escalar a la cola senior'))
    await user.click(send)
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    const req = api.decide.mock.calls[0][1] as DecisionRequest
    expect(req).toEqual({
      client_decision_id: req.client_decision_id,
      version: 3,
      action: 'reject',
      reason: 'Falta evidencia del comercio',
      next: 'escalate',
    })
  })

  it('reject + request_information carries the optional question as reply', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi()
    renderConsole(api)
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Rechazar' }))
    await user.type(screen.getByLabelText('Motivo del rechazo (obligatorio)'), 'Faltan datos')
    await user.click(screen.getByLabelText('Pedir información al cliente'))
    await user.type(screen.getByLabelText('Pregunta para el cliente (ES, opcional)'), '¿Tenía la tarjeta con usted?')
    await user.click(screen.getByRole('button', { name: 'Rechazar la recomendación' }))
    // CX-02: something goes to the customer, so the exact text is confirmed first.
    expect(api.decide).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /Confirmar y enviar/ }))
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    const req = api.decide.mock.calls[0][1] as DecisionRequest
    expect(req.next).toBe('request_information')
    expect(req.reply).toEqual({ language: 'es', text: '¿Tenía la tarjeta con usted?' })
  })

  it('an unreliable report shows the strip and approving needs "Revisé la evidencia"', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi({
      getAnalystCase: vi.fn(async () => analystDetail({ report: report({ citations_valid: true, removed_claims: 2 }) })),
    })
    renderConsole(api)
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    expect(screen.getByTestId('unreliable-strip').textContent).toMatch(/Reporte no confiable/)
    const approve = screen.getByRole('button', { name: 'Aprobar y notificar' }) as HTMLButtonElement
    expect(approve.disabled).toBe(true)
    await user.click(screen.getByLabelText('Revisé la evidencia'))
    expect(approve.disabled).toBe(false)
    await user.click(approve)
    await user.click(screen.getByRole('button', { name: 'Confirmar y enviar' }))
    await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(1))
    const req = api.decide.mock.calls[0][1] as DecisionRequest
    expect(req.evidence_reviewed).toBe(true)
    expect(req.action).toBe('approve')
  })

  it('citations_valid=false alone also marks the report unreliable', async () => {
    signIn()
    renderConsole(
      fakeApi({ getAnalystCase: vi.fn(async () => analystDetail({ report: report({ citations_valid: false, removed_claims: 0 }) })) }),
    )
    expect(await screen.findByTestId('unreliable-strip')).toBeTruthy()
  })

  it('409 re-reads the case and says who already decided and when', async () => {
    const user = userEvent.setup()
    signIn()
    const decided: AnalystCaseDetail = analystDetail({
      case: {
        status: 'notified',
        analyst_decision: {
          action: 'approve',
          edited: false,
          reason: null,
          decided_by: 'otra-analista',
          decided_at: '2026-09-30T15:07:00Z',
        },
      },
      allowed_actions: [],
      version: 4,
    })
    const get = vi.fn().mockResolvedValueOnce(analystDetail()).mockResolvedValue(decided)
    const api = fakeApi({
      getAnalystCase: get,
      decide: vi.fn(async () => Promise.reject(err('conflict', 409))),
    })
    renderConsole(api)
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Aprobar y notificar' }))
    await user.click(screen.getByRole('button', { name: 'Confirmar y enviar' }))
    const time = new Intl.DateTimeFormat('es', { hour: '2-digit', minute: '2-digit' }).format(new Date('2026-09-30T15:07:00Z'))
    expect(await screen.findByText(`Este caso ya fue decidido por otra-analista a las ${time}. Se muestra la versión actual.`)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Aprobar y notificar' })).toBeNull()
  })

  it('after the decision shows what the customer received (stamped), not the draft as "no enviado"', async () => {
    signIn()
    const sentText = 'Revisamos tu caso EV-1A2B3C4D (texto sintético). Respuesta aprobada por una persona del equipo. En esta demo ningún dinero se mueve.'
    const detail = analystDetail({
      case: {
        status: 'notified',
        lifecycle_step: 'notified',
        analyst_decision: { action: 'approve', edited: false, reason: null, decided_by: 'analista-local', decided_at: '2026-09-30T15:07:00Z' },
        resolution: { language: 'es', text: sentText, sent_at: '2026-09-30T15:07:00Z', approved_by_human: true },
      },
      allowed_actions: [],
    })
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => detail) }))
    const panel = (await screen.findByRole('heading', { name: 'Respuesta enviada' })).closest('section') as HTMLElement
    expect(within(panel).getByText(sentText)).toBeTruthy()
    expect(within(panel).getByText(/Enviado al cliente · en ES/)).toBeTruthy()
    expect(within(panel).getByText('Aprobado por una persona')).toBeTruthy()
    expect(within(panel).queryByText(/solo se envía si una persona lo aprueba/)).toBeNull()
  })

  it('an escalation sends nothing: the draft stays, marked as never sent', async () => {
    signIn()
    const detail = analystDetail({
      case: {
        status: 'handed_off',
        lifecycle_step: 'in_review',
        analyst_decision: { action: 'reject', edited: false, reason: 'monto alto', decided_by: 'analista-local', decided_at: '2026-09-30T15:07:00Z' },
        resolution: null,
      },
      allowed_actions: [],
    })
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => detail) }))
    const panel = (await screen.findByRole('heading', { name: 'Borrador de respuesta' })).closest('section') as HTMLElement
    expect(within(panel).getByText(/la decisión no envió este borrador/)).toBeTruthy()
  })

  it('401 while deciding asks to sign in again, keeps the draft and retries with the same id', async () => {
    const user = userEvent.setup()
    signIn()
    const decide = vi
      .fn()
      .mockRejectedValueOnce(err('session_expired', 401))
      .mockImplementation(async (_id: string, req: DecisionRequest) => decisionResponse(req))
    const api = fakeApi({ decide })
    renderConsole(api)
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Editar' }))
    const box = screen.getByLabelText('Respuesta al cliente (ES)') as HTMLTextAreaElement
    await user.clear(box)
    await user.type(box, 'Texto editado a mano (sintético)')
    await user.type(screen.getByLabelText('Motivo de la edición (obligatorio)'), 'Tono')
    await user.click(screen.getByRole('button', { name: /Guardar la edición y enviar/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('La sesión del analista terminó')).toBeTruthy()
    expect(getAnalystToken()).toBeNull()
    await user.type(within(dialog).getByLabelText('Token de analista'), 'tok-new-synthetic')
    await user.click(within(dialog).getByRole('button', { name: 'Entrar' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    // The draft is still there.
    expect((screen.getByLabelText('Respuesta al cliente (ES)') as HTMLTextAreaElement).value).toBe('Texto editado a mano (sintético)')
    await user.click(screen.getByRole('button', { name: /Guardar la edición y enviar/ }))
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(2))
    const first = decide.mock.calls[0][1] as DecisionRequest
    const second = decide.mock.calls[1][1] as DecisionRequest
    expect(second.client_decision_id).toBe(first.client_decision_id)
    expect(second).toEqual(first)
  })

  it('a 422 precondition is explained and the case is re-read', async () => {
    const user = userEvent.setup()
    signIn()
    const api = fakeApi({ decide: vi.fn(async () => Promise.reject(err('precondition', 422))) })
    renderConsole(api)
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Aprobar y notificar' }))
    await user.click(screen.getByRole('button', { name: 'Confirmar y enviar' }))
    expect(await screen.findByText(/El caso cambió o no está listo para esta acción/)).toBeTruthy()
    await waitFor(() => expect(api.getAnalystCase.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('warns (orientative) when an edited reply seems to promise a refund', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(fakeApi())
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Editar' }))
    const box = screen.getByLabelText('Respuesta al cliente (ES)')
    await user.clear(box)
    await user.type(box, 'Te lo ' + 'devolveremos mañana')
    expect(screen.getByText(/Orientativo: el texto parece prometer/)).toBeTruthy()
  })

  it('the console switches to Portuguese', async () => {
    const user = userEvent.setup()
    signIn()
    renderConsole(fakeApi())
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('button', { name: 'Português' }))
    expect(screen.getByRole('button', { name: 'Aprovar e notificar' })).toBeTruthy()
    expect(screen.getByText('Investigador provisório (regras) · sem GenAI')).toBeTruthy()
  })
})
