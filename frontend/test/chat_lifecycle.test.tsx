// Customer chat after the case opens: polling GET /cases/{id} (RF-20), the analyst-approved
// reply shown once, and the judge's failure switches (RF-22). Every payload is SYNTHETIC.
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClientError, type ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { CaseResponse, ChatRequest } from '../src/api/types'
import { saveSession, saveTranscript, type TurnSnapshot } from '../src/session'
import { ChatView } from '../src/views/ChatView'
import { caseView, chatResponse, sessionResponse } from './fixtures'

type Fn = ReturnType<typeof vi.fn>
type FakeApi = ApiClient & { createSession: Fn; chat: Fn; getCase: Fn }

function fakeApi(overrides: Partial<Record<keyof ApiClient, unknown>> = {}): FakeApi {
  return {
    health: vi.fn(async () => ({ status: 'ok' as const, stage: 'local', deps: 'ok' as const })),
    createSession: vi.fn(async () => sessionResponse()),
    chat: vi.fn(async () => chatResponse()),
    getCase: vi.fn(async (): Promise<CaseResponse> => ({ case: caseView(), poll_after_ms: 5000 })),
    isChatInFlight: () => false,
    listAnalystCases: vi.fn(),
    getAnalystCase: vi.fn(),
    decide: vi.fn(),
    ...overrides,
  } as FakeApi
}

const STAMP = 'Respuesta aprobada por una persona del equipo. En esta demo ningún dinero se mueve.'
const RESOLUTION_TEXT = `Hola Lucía, revisamos el cargo y abrimos una disputa (sintético). ${STAMP}`

/** A lane B case already open in this tab (restored from sessionStorage). */
function seedOpenCase(pollMs: number | null = 5000) {
  const s = sessionResponse()
  saveSession({ token: s.session_token, expires_at: s.expires_at, demo_key: 'lucia', customer: s.customer })
  const r = chatResponse()
  const last: TurnSnapshot = {
    progress: { ...r.progress, step: 'case_open', missing: [] },
    case_card: caseView({ status: 'investigating', lifecycle_step: 'investigating' }),
    lane: 'B',
    degraded: [],
    trace_id: 'tr-0005',
    trace_summary: r.trace_summary,
    client_latency_ms: 900,
    turn: 3,
    poll_after_ms: pollMs,
    demo_switches: { tools_down: false, model_slow: false, fast_clock: false },
  }
  saveTranscript({
    demo_key: 'lucia',
    entries: [
      {
        kind: 'system',
        id: 's-3',
        turn: 3,
        text: 'Abrimos el caso EV-1A2B3C4D (sintético).',
        lang: 'es',
        source: 'template',
        at: '2026-09-30T15:00:00Z',
        buttons: [],
        input_mode: 'free_text',
        degraded: [],
      },
    ],
    used_button_ids: [],
    last,
    pending: null,
  })
}

function renderChat(api: ApiClient) {
  return render(
    <ApiContext.Provider value={api}>
      <ChatView demoKey="lucia" />
    </ApiContext.Provider>,
  )
}

const inReview: CaseResponse = { case: caseView({ status: 'awaiting_analyst', lifecycle_step: 'in_review' }), poll_after_ms: 5000 }
const notified: CaseResponse = {
  case: caseView({
    status: 'notified',
    lifecycle_step: 'notified',
    resolution: { language: 'es', text: RESOLUTION_TEXT, sent_at: '2026-09-30T15:10:00Z', approved_by_human: true },
  }),
  poll_after_ms: null,
}

afterEach(() => {
  vi.useRealTimers()
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
})

describe('RF-20: the card polls the case while it waits', () => {
  it('shows Investigando -> En revisión -> Notificado and adds the approved reply once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    seedOpenCase()
    const getCase = vi.fn().mockResolvedValueOnce(inReview).mockResolvedValue(notified)
    const api = fakeApi({ getCase })
    const view = renderChat(api)
    // Restoring re-reads the case once.
    await waitFor(() => expect(getCase).toHaveBeenCalledTimes(1))
    const current = await screen.findByText('En revisión', { selector: '.stepper__item--current .stepper__label' })
    expect(current).toBeTruthy()
    expect(screen.queryByTestId('resolution-message')).toBeNull()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_100) // 5 s +/- 20 %
    })
    expect(getCase).toHaveBeenCalledTimes(2)
    const msg = await screen.findByTestId('resolution-message')
    expect(within(msg).getByText(RESOLUTION_TEXT)).toBeTruthy()
    expect(within(msg).getByText('Humano · aprobado por un analista')).toBeTruthy()
    expect(msg.getAttribute('lang')).toBe('es')
    expect(screen.getByText('Notificado', { selector: '.stepper__item--current .stepper__label' })).toBeTruthy()

    // poll_after_ms null: polling stops; the message is never duplicated.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(getCase).toHaveBeenCalledTimes(2)
    expect(screen.getAllByTestId('resolution-message')).toHaveLength(1)

    // After a reload the transcript keeps it once, even if the case is read again.
    view.unmount()
    renderChat(fakeApi({ getCase: vi.fn(async () => notified) }))
    await screen.findByText('Notificado', { selector: '.stepper__item--current .stepper__label' })
    expect(screen.getAllByTestId('resolution-message')).toHaveLength(1)
  })

  it('the approved reply shows in Portuguese with its badge in Portuguese', async () => {
    seedOpenCase()
    const text = 'Olá João, revisamos a tarifa (sintético). Resposta aprovada por uma pessoa da equipe. Nesta demonstração nenhum dinheiro é movimentado.'
    const api = fakeApi({
      getCase: vi.fn(async () => ({
        case: caseView({
          status: 'notified',
          language: 'pt',
          lifecycle_step: 'notified',
          resolution: { language: 'pt', text, sent_at: '2026-09-30T15:10:00Z', approved_by_human: true },
        }),
        poll_after_ms: null,
      })),
    })
    renderChat(api)
    const msg = await screen.findByTestId('resolution-message')
    expect(within(msg).getByText('Humano · aprovado por um analista')).toBeTruthy()
    expect(msg.getAttribute('lang')).toBe('pt-BR')
  })

  it('stops on 403 and on 401 (401 ends the session)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    seedOpenCase()
    const forbidden = new ApiClientError({ code: 'not_authorized', status: 403, retryable: false, message: 'x' })
    const getCase = vi.fn().mockResolvedValueOnce(inReview).mockRejectedValue(forbidden)
    renderChat(fakeApi({ getCase }))
    await waitFor(() => expect(getCase).toHaveBeenCalledTimes(1))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(getCase).toHaveBeenCalledTimes(2)
  })

  it('401 while polling wipes the session', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    seedOpenCase()
    const expired = new ApiClientError({ code: 'session_expired', status: 401, retryable: false, message: 'x' })
    const getCase = vi.fn().mockResolvedValueOnce(inReview).mockRejectedValue(expired)
    renderChat(fakeApi({ getCase }))
    await waitFor(() => expect(getCase).toHaveBeenCalledTimes(1))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_100)
    })
    expect(await screen.findByText('La sesión terminó')).toBeTruthy()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(getCase).toHaveBeenCalledTimes(2)
  })

  it('backs off after a 5xx and only polls while the tab is visible', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.spyOn(Math, 'random').mockReturnValue(0.5) // jitter factor exactly 1
    seedOpenCase()
    const down = new ApiClientError({ code: 'internal', status: 503, retryable: true, message: 'x' })
    const getCase = vi
      .fn()
      .mockResolvedValueOnce(inReview)
      .mockRejectedValueOnce(down)
      .mockRejectedValueOnce(down)
      .mockResolvedValue(inReview)
    renderChat(fakeApi({ getCase }))
    await waitFor(() => expect(getCase).toHaveBeenCalledTimes(1))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_050) // first poll at 5 s -> 503
    })
    expect(getCase).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000) // backoff step 1 is 5 s: not yet
    })
    expect(getCase).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_100) // -> 503 again
    })
    expect(getCase).toHaveBeenCalledTimes(3)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8_500) // backoff step 2 is 10 s: not yet
    })
    expect(getCase).toHaveBeenCalledTimes(3)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_600)
    })
    expect(getCase).toHaveBeenCalledTimes(4)

    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    const whileHidden = getCase.mock.calls.length
    expect(whileHidden).toBe(4) // the tick that found the tab hidden did not call
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(getCase.mock.calls.length).toBe(whileHidden)
    // Visible again: one read at once (the chat's own refresh and the poller share one slot).
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await waitFor(() => expect(getCase.mock.calls.length).toBe(whileHidden + 1))
  })

  it('does not poll a case that is not lane B or has no poll_after_ms', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    seedOpenCase(null)
    const getCase = vi.fn(async () => ({ case: caseView({ status: 'handed_off', lane: 'C', lifecycle_step: null }), poll_after_ms: null }))
    renderChat(fakeApi({ getCase }))
    await waitFor(() => expect(getCase).toHaveBeenCalledTimes(1)) // the one re-read on restore
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(getCase).toHaveBeenCalledTimes(1)
  })
})

describe('RF-22: judge failure switches', () => {
  it('a switch travels in demo_switches with the next message, and the panel shows the server state', async () => {
    const user = userEvent.setup()
    const chat = vi
      .fn()
      .mockResolvedValueOnce(
        chatResponse({ input_mode: 'free_text', buttons: [], demo_switches: { tools_down: true, model_slow: false, fast_clock: false } }),
      )
      .mockResolvedValue(
        chatResponse({ turn: 2, trace_id: 'tr-0002', input_mode: 'free_text', buttons: [], demo_switches: { tools_down: true, model_slow: false, fast_clock: false } }),
      )
    const api = fakeApi({ chat })
    renderChat(api)
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    expect(screen.getByText(/Viajan en demo_switches con el próximo mensaje/)).toBeTruthy()
    const toolSwitch = screen.getByRole('switch', { name: 'Herramienta de transacciones caída' })
    await user.click(toolSwitch)
    // Nothing is sent by the switch itself.
    expect(chat).not.toHaveBeenCalled()
    expect(screen.getByText('con el próximo mensaje: activado')).toBeTruthy()

    await user.type(screen.getByRole('textbox'), 'hola{Enter}')
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(1))
    const first = chat.mock.calls[0][0] as ChatRequest
    expect(first.message).toBe('hola')
    expect(first.demo_switches).toEqual({ tools_down: true })
    await waitFor(() => expect(screen.getAllByText('Servidor: activado').length).toBe(1))
    expect(screen.queryByText(/con el próximo mensaje:/)).toBeNull()
    expect((toolSwitch as HTMLInputElement).checked).toBe(true)

    await user.type(screen.getByRole('textbox'), 'otra vez{Enter}')
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(2))
    expect((chat.mock.calls[1][0] as ChatRequest).demo_switches).toBeUndefined()
  })

  it('a server without switches (501) drops them so the next message goes clean', async () => {
    const user = userEvent.setup()
    const chat = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError({ code: 'not_implemented', status: 501, retryable: false, message: 'x' }))
      .mockResolvedValue(chatResponse())
    renderChat(fakeApi({ chat }))
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.click(screen.getByRole('switch', { name: 'Modelo lento' }))
    await user.type(screen.getByRole('textbox'), 'hola{Enter}')
    expect(await screen.findByText('Esta función del backend estará disponible pronto.')).toBeTruthy()
    expect((chat.mock.calls[0][0] as ChatRequest).demo_switches).toEqual({ model_slow: true })
    await user.type(screen.getByRole('textbox'), 'hola de nuevo{Enter}')
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(2))
    expect((chat.mock.calls[1][0] as ChatRequest).demo_switches).toBeUndefined()
  })

  it('"Expirar la sesión ahora" sends only expire_session and the 401 ends the session', async () => {
    const user = userEvent.setup()
    const chat = vi.fn(async (_req: ChatRequest) => {
      throw new ApiClientError({ code: 'session_expired', status: 401, retryable: false, message: 'x' })
    })
    renderChat(fakeApi({ chat }))
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.click(screen.getByRole('button', { name: 'Expirar la sesión ahora' }))
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(1))
    const req = chat.mock.calls[0][0]
    expect(req.demo_switches).toEqual({ expire_session: true })
    expect(req.message).toBeUndefined()
    expect(req.button_id).toBeUndefined()
    expect(await screen.findByText('La sesión terminó')).toBeTruthy()
    expect(window.sessionStorage.getItem('ev.session.v1')).toBeNull()
  })

  it('links to the analyst console for the open case', async () => {
    seedOpenCase(null)
    renderChat(fakeApi({ getCase: vi.fn(async () => ({ case: caseView(), poll_after_ms: null })) }))
    const link = await screen.findByRole('link', { name: 'Abrir consola del analista' })
    expect(link.getAttribute('href')).toBe('#/consola/EV-1A2B3C4D')
    expect(link.getAttribute('rel')).toContain('noopener')
  })
})
