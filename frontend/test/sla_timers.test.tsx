// SLA timers in the front (plan v2 sections 6 and 12; LOCAL simulation behind the lifecycle
// protocol): the judge's demo clock, the customer's proactive notices and promise state, and the
// console's queue alerts and case clock. Every payload is SYNTHETIC.
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { AnalystCaseListItem, CaseNotice, CaseResponse, ChatRequest } from '../src/api/types'
import { promiseState } from '../src/chat/progress'
import { withCaseMessages, withNotices } from '../src/chat/useChat'
import { saveAnalystSession } from '../src/console/auth'
import { alertsOf, clockTimeline, headerAlerts, newSlaAlerts } from '../src/console/slaAlerts'
import { saveSession, saveTranscript, type TranscriptEntry, type TurnSnapshot } from '../src/session'
import { ChatView } from '../src/views/ChatView'
import { ConsoleView } from '../src/views/ConsoleView'
import { analystDetail, caseView, chatResponse, listItem, listResponse, sessionResponse } from './fixtures'

type Fn = ReturnType<typeof vi.fn>

function fakeApi(overrides: Partial<Record<keyof ApiClient, unknown>> = {}): ApiClient & { chat: Fn; getCase: Fn } {
  return {
    // demo_clock_scale: the server's fast_clock factor (1/1440 = lifecycle.DEFAULT_SLA_SCALE).
    health: vi.fn(async () => ({ status: 'ok' as const, stage: 'local', deps: 'ok' as const, demo_clock_scale: 1 / 1440 })),
    createSession: vi.fn(async () => sessionResponse()),
    chat: vi.fn(async () => chatResponse()),
    getCase: vi.fn(async (): Promise<CaseResponse> => ({ case: caseView(), poll_after_ms: 5000 })),
    isChatInFlight: () => false,
    listAnalystCases: vi.fn(async () => listResponse([listItem()])),
    getAnalystCase: vi.fn(async () => analystDetail()),
    decide: vi.fn(),
    ...overrides,
  } as ApiClient & { chat: Fn; getCase: Fn }
}

const SLA80_TEXT =
  'Seguimos trabajando en tu caso EV-1A2B3C4D. Todavía no tenemos una respuesta final; la fecha estimada sigue siendo el 09/10/2026 (sintético).'
const ESCALATED_TEXT =
  'Tu caso EV-1A2B3C4D está tardando más de lo previsto, así que lo pasamos a un equipo senior con prioridad alta (sintético).'

const n80: CaseNotice = { kind: 'sla_80', text: SLA80_TEXT, language: 'es', at: '2026-09-30T15:00:10Z' }
const nEsc: CaseNotice = { kind: 'escalated', text: ESCALATED_TEXT, language: 'es', at: '2026-09-30T15:00:13Z' }

/** A lane B case already open in this tab (restored from sessionStorage). */
function seedOpenCase(pollMs: number | null = 5000) {
  const s = sessionResponse()
  saveSession({ token: s.session_token, expires_at: s.expires_at, demo_key: 'lucia', customer: s.customer })
  const r = chatResponse()
  const last: TurnSnapshot = {
    progress: { ...r.progress, step: 'case_open', missing: [] },
    case_card: caseView({ status: 'awaiting_analyst', lifecycle_step: 'in_review' }),
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

function renderConsole(api: ApiClient, caseId: string | null = null) {
  saveAnalystSession({ token: 'local-token-synthetic', provider: 'local', signed_in_at: '2026-09-30T15:00:00Z' })
  return render(
    <ApiContext.Provider value={api}>
      <ConsoleView caseId={caseId} />
    </ApiContext.Provider>,
  )
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------- pure helpers

describe('notices join the conversation once', () => {
  const base: TranscriptEntry[] = []

  it('adds each notice once per case and kind, oldest first, before the resolution', () => {
    const card = caseView({ notices: [n80] })
    const once = withNotices(base, card)
    expect(once.map((e) => e.id)).toEqual(['n-EV-1A2B3C4D-sla_80'])
    // The same card read again (every poll) adds nothing.
    expect(withNotices(once, card)).toBe(once)
    const both = withNotices(once, caseView({ notices: [n80, nEsc] }))
    expect(both.map((e) => e.id)).toEqual(['n-EV-1A2B3C4D-sla_80', 'n-EV-1A2B3C4D-escalated'])
    const withReply = withCaseMessages(
      [],
      caseView({
        notices: [n80],
        status: 'notified',
        resolution: { language: 'es', text: 'Respuesta (sintético).', sent_at: '2026-09-30T15:10:00Z', approved_by_human: true },
      }),
    )
    expect(withReply.map((e) => e.kind)).toEqual(['notice', 'resolution'])
  })

  it('tolerates cards saved before the timers existed (no notices field)', () => {
    const old = { ...caseView() } as Partial<ReturnType<typeof caseView>>
    delete old.notices
    expect(withNotices(base, old as ReturnType<typeof caseView>)).toBe(base)
    expect(promiseState(old as ReturnType<typeof caseView>)).toBe('on_time')
  })

  it('promise state: a tiempo -> aviso 80 % -> escalado, lane B only', () => {
    expect(promiseState(caseView())).toBe('on_time')
    expect(promiseState(caseView({ notices: [n80] }))).toBe('sla_80')
    expect(promiseState(caseView({ notices: [n80, nEsc] }))).toBe('escalated')
    expect(promiseState(caseView({ lane: 'C' }))).toBeNull()
    expect(promiseState(null)).toBeNull()
  })
})

describe('console SLA helpers', () => {
  const item = (o: Partial<AnalystCaseListItem>) => listItem(o)

  it('alertsOf keeps the contract order and survives a missing field', () => {
    expect(alertsOf(item({ sla_alerts: ['breached', 'unassigned', 'sla_80'] }))).toEqual(['unassigned', 'sla_80', 'breached'])
    const old = item({}) as Partial<AnalystCaseListItem>
    delete old.sla_alerts
    expect(alertsOf(old as AnalystCaseListItem)).toEqual([])
  })

  it('newSlaAlerts: nothing on the first listing, then only what fired since', () => {
    const a0 = [item({ case_id: 'EV-1', sla_alerts: ['unassigned'] })]
    expect(newSlaAlerts([], a0, true)).toEqual([])
    const a1 = [item({ case_id: 'EV-1', sla_alerts: ['unassigned', 'sla_80'] }), item({ case_id: 'EV-2', sla_alerts: [] })]
    expect(newSlaAlerts(a0, a1, false)).toEqual([{ case_id: 'EV-1', display_name: 'Lucía', kind: 'sla_80' }])
    expect(newSlaAlerts(a1, a1, false)).toEqual([])
  })

  it('clockTimeline: fired, pending, cancelled once taken or decided', () => {
    const d = analystDetail({
      case: {
        clock: {
          assigned_by: '2026-10-01T15:00:00Z',
          first_response_by: '2026-10-01T15:00:00Z',
          sla_alert_at: '2026-10-12T15:00:00Z',
          breach_at: '2026-10-15T15:00:00Z',
        },
        clock_events: [{ kind: 'sla_80', fired_at: '2026-09-30T15:00:10Z' }],
      },
    }).case
    const rows = clockTimeline(d)
    expect(rows.map((r) => [r.kind, r.state])).toEqual([
      ['unassigned', 'cancelled'], // the analyst reading the detail took the case
      ['sla_80', 'fired'],
      ['breached', 'scheduled'],
    ])
    expect(rows[1].firedAt).toBe('2026-09-30T15:00:10Z')
    expect(rows[2].planned).toBe('2026-10-15T15:00:00Z')
    const decided = clockTimeline({
      ...d,
      status: 'notified',
      analyst_decision: { action: 'approve', edited: false, reason: null, decided_by: 'a', decided_at: '2026-09-30T15:05:00Z' },
    })
    expect(decided.map((r) => r.state)).toEqual(['cancelled', 'fired', 'cancelled'])
    expect(headerAlerts({ clock_events: [{ kind: 'unassigned', fired_at: 'x' }, { kind: 'breached', fired_at: 'y' }] })).toEqual([
      'breached',
    ])
  })
})

// ---------------------------------------------------------------- judge panel

describe('judge: "Reloj acelerado (1 día = 1 minuto)"', () => {
  it('explains the alarms and when, and the switch travels in demo_switches with the next message', async () => {
    const user = userEvent.setup()
    const chat = vi.fn().mockResolvedValue(
      chatResponse({ input_mode: 'free_text', buttons: [], demo_switches: { tools_down: false, model_slow: false, fast_clock: true } }),
    )
    renderChat(fakeApi({ chat }))
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    // The scale comes from GET /health (test/polish_29sep.test.tsx covers other scales).
    const sw = await screen.findByRole('switch', { name: 'Reloj acelerado (1 día = 1 minuto)' })
    const plan = screen.getByTestId('clock-plan')
    expect(within(plan).getByText(/Tras 1 minuto \(24 h\)/)).toBeTruthy()
    expect(within(plan).getByText(/Tras 12 minutos \(80 % del SLA/)).toBeTruthy()
    expect(within(plan).getByText(/Tras 15 minutos \(SLA vencido\)/)).toBeTruthy()
    expect(within(plan).getByText(/EventBridge Scheduler/)).toBeTruthy()
    expect(within(plan).getByText(/Las fechas prometidas no cambian/)).toBeTruthy()
    expect(screen.getByText(/sin confirmar \(todavía no hay turnos\)/, { selector: '#sw-fast_clock-state' })).toBeTruthy()

    await user.click(sw)
    expect(chat).not.toHaveBeenCalled()
    await user.type(screen.getByRole('textbox'), 'hola{Enter}')
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(1))
    expect((chat.mock.calls[0][0] as ChatRequest).demo_switches).toEqual({ fast_clock: true })
    await waitFor(() => expect(document.getElementById('sw-fast_clock-state')?.textContent).toContain('Servidor: activado'))
    expect((sw as HTMLInputElement).checked).toBe(true)
  })

  it('"Aplicar ahora" sends only the switches, keeps the pending buttons and shows the confirmation', async () => {
    const user = userEvent.setup()
    seedOpenCase(null)
    const confirmText = 'Modo juez: reloj acelerado para los casos de esta sesión (simulado: 1 día = 1 minuto). Las fechas prometidas no cambian.'
    const chat = vi.fn(async (_req: ChatRequest) =>
      chatResponse({
        turn: 3,
        trace_id: 'tr-sw-01',
        reply_text: confirmText,
        buttons: [],
        input_mode: 'free_text',
        case_card: caseView({ status: 'awaiting_analyst', lifecycle_step: 'in_review' }),
        lane: 'B',
        poll_after_ms: 5000,
        demo_switches: { tools_down: false, model_slow: false, fast_clock: true },
      }),
    )
    renderChat(fakeApi({ chat, getCase: vi.fn(async () => ({ case: caseView({ status: 'awaiting_analyst' }), poll_after_ms: null })) }))
    const apply = await screen.findByRole('button', { name: 'Aplicar ahora' })
    expect((apply as HTMLButtonElement).disabled).toBe(true) // nothing staged
    await user.click(await screen.findByRole('switch', { name: 'Reloj acelerado (1 día = 1 minuto)' }))
    expect((apply as HTMLButtonElement).disabled).toBe(false)
    await user.click(apply)
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(1))
    const req = chat.mock.calls[0][0]
    expect(req.demo_switches).toEqual({ fast_clock: true })
    expect(req.message).toBeUndefined()
    expect(req.button_id).toBeUndefined()
    expect(await screen.findByText(confirmText, { selector: '.switch__note' })).toBeTruthy()
    // The confirmation is not a chat turn: the transcript keeps its one system message.
    expect(screen.queryByText(confirmText, { selector: '.msg__text' })).toBeNull()
    expect(document.getElementById('sw-fast_clock-state')?.textContent).toContain('Servidor: activado')
    expect(screen.queryByText(/con el próximo mensaje:/)).toBeNull()
    expect((apply as HTMLButtonElement).disabled).toBe(true)
  })
})

// ---------------------------------------------------------------- customer chat

describe('customer: proactive notices and the promise state', () => {
  it('each notice arrives once as a rule message, and the card goes a tiempo -> aviso 80 % -> escalado', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    seedOpenCase()
    const onTime: CaseResponse = { case: caseView({ status: 'awaiting_analyst', lifecycle_step: 'in_review' }), poll_after_ms: 5000 }
    const at80: CaseResponse = { case: caseView({ status: 'awaiting_analyst', lifecycle_step: 'in_review', notices: [n80] }), poll_after_ms: 5000 }
    const escalated: CaseResponse = {
      case: caseView({ status: 'awaiting_analyst', lifecycle_step: 'in_review', notices: [n80, nEsc] }),
      poll_after_ms: 5000,
    }
    const getCase = vi.fn().mockResolvedValueOnce(onTime).mockResolvedValueOnce(at80).mockResolvedValueOnce(at80).mockResolvedValue(escalated)
    const view = renderChat(fakeApi({ getCase }))
    await waitFor(() => expect(getCase).toHaveBeenCalledTimes(1))
    const promise = await screen.findByTestId('promise-state')
    expect(promise.getAttribute('data-state')).toBe('on_time')
    expect(within(promise).getByText('A tiempo')).toBeTruthy()
    expect(within(promise).getByText(/Si pasa el 80 %/)).toBeTruthy()
    expect(screen.queryByTestId('notice-message')).toBeNull()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_100)
    })
    const first = await screen.findByTestId('notice-message')
    expect(within(first).getByText(SLA80_TEXT)).toBeTruthy()
    expect(within(first).getByText('Regla · aviso automático')).toBeTruthy()
    expect(first.getAttribute('lang')).toBe('es')
    // It sits in the conversation log (role="log", aria-live polite, additions only).
    const log = screen.getByRole('log')
    expect(log.getAttribute('aria-relevant')).toBe('additions')
    expect(log.contains(first)).toBe(true)
    expect(screen.getByTestId('promise-state').getAttribute('data-state')).toBe('sla_80')
    expect(within(screen.getByTestId('promise-state')).getByText('Aviso 80 %')).toBeTruthy()
    // The card's state is not a live region: only the new message is announced.
    expect(screen.getByTestId('promise-state').closest('[aria-live]')).toBeNull()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_100) // same notices again
    })
    expect(getCase).toHaveBeenCalledTimes(3)
    expect(screen.getAllByTestId('notice-message')).toHaveLength(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_100)
    })
    await waitFor(() => expect(screen.getAllByTestId('notice-message')).toHaveLength(2))
    const [, second] = screen.getAllByTestId('notice-message')
    expect(second.getAttribute('data-notice')).toBe('escalated')
    expect(within(second).getByText('Escalado')).toBeTruthy()
    expect(screen.getByTestId('promise-state').getAttribute('data-state')).toBe('escalated')
    expect(within(screen.getByTestId('promise-state')).getByText(/equipo senior con prioridad alta/)).toBeTruthy()

    // After a reload the transcript keeps each notice once.
    view.unmount()
    renderChat(fakeApi({ getCase: vi.fn(async () => escalated) }))
    await screen.findByTestId('promise-state')
    await waitFor(() => expect(screen.getAllByTestId('notice-message')).toHaveLength(2))
  })

  it('a Portuguese notice carries the badge in Portuguese', async () => {
    seedOpenCase(null)
    const pt: CaseNotice = {
      kind: 'sla_80',
      text: 'Seguimos trabalhando no seu caso EV-1A2B3C4D (sintético).',
      language: 'pt',
      at: '2026-09-30T15:00:10Z',
    }
    renderChat(fakeApi({ getCase: vi.fn(async () => ({ case: caseView({ language: 'pt', notices: [pt] }), poll_after_ms: null })) }))
    const msg = await screen.findByTestId('notice-message')
    expect(within(msg).getByText('Regra · aviso automático')).toBeTruthy()
    expect(msg.getAttribute('lang')).toBe('pt-BR')
  })
})

// ---------------------------------------------------------------- console

describe('console: SLA alerts in the queue and the case clock', () => {
  it('shows alert chips with text (never only color) and announces only newly fired alerts', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const listAnalystCases = vi
      .fn()
      .mockResolvedValueOnce(listResponse([listItem({ case_id: 'EV-AAAA0001', display_name: 'Lucía', sla_alerts: ['unassigned'] })]))
      .mockResolvedValue(
        listResponse([
          listItem({ case_id: 'EV-AAAA0001', display_name: 'Lucía', sla_alerts: ['unassigned', 'sla_80', 'breached'], priority: 'high', queue: 'senior' }),
        ]),
      )
    renderConsole(fakeApi({ listAnalystCases }))
    const option = await screen.findByRole('option', { name: /Lucía/ })
    const chips = within(option).getByTestId('sla-alerts')
    expect(within(chips).getByText('Sin asignar')).toBeTruthy()
    // What was already there on arrival is not announced.
    const news = screen.getByTestId('queue-alert-news')
    expect(news.getAttribute('aria-live')).toBe('polite')
    expect(news.textContent).toBe('')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(18_100) // 15 s +/- 20 %
    })
    await waitFor(() => expect(listAnalystCases).toHaveBeenCalledTimes(2))
    const opt2 = screen.getByRole('option', { name: /Lucía/ })
    const kinds = within(opt2)
      .getAllByText((_, el) => !!el?.classList.contains('sla-alert'))
      .map((el) => el.getAttribute('data-alert'))
    expect(kinds).toEqual(['unassigned', 'sla_80', 'breached'])
    expect(within(opt2).getByText('80 % del SLA')).toBeTruthy()
    expect(within(opt2).getByText('SLA vencido · escalado')).toBeTruthy()
    await waitFor(() =>
      expect(screen.getByTestId('queue-alert-news').textContent).toBe(
        'Nueva alerta del reloj: Lucía (EV-AAAA0001), 80 % del SLA. Nueva alerta del reloj: Lucía (EV-AAAA0001), SLA vencido · escalado.',
      ),
    )
    // The chips themselves are not live regions (the queue polls every 15 s).
    expect(opt2.closest('[aria-live]')).toBeNull()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(18_100) // same alerts again: the announcement does not change
    })
    await waitFor(() => expect(listAnalystCases).toHaveBeenCalledTimes(3))
    expect(screen.getByTestId('queue-alert-news').textContent).toContain('SLA vencido · escalado.')
  })

  it('Historial shows the clock timeline with planned and fired times; the header carries the fired alerts', async () => {
    const user = userEvent.setup()
    const detail = analystDetail({
      case: {
        clock: {
          assigned_by: '2026-10-01T15:00:00Z',
          first_response_by: '2026-10-01T15:00:00Z',
          sla_alert_at: '2026-10-12T15:00:00Z',
          breach_at: '2026-10-15T15:00:00Z',
        },
        clock_events: [
          { kind: 'unassigned', fired_at: '2026-09-30T15:00:01Z' },
          { kind: 'sla_80', fired_at: '2026-09-30T15:00:10Z' },
          { kind: 'breached', fired_at: '2026-09-30T15:00:13Z' },
        ],
        handoff: { queue: 'senior', priority: 'high', open_questions: [], facts_verified: [] },
      },
    })
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => detail) }), 'EV-1A2B3C4D')
    const head = await screen.findByRole('heading', { level: 1 })
    const header = head.closest('header') as HTMLElement
    const headChips = within(header).getByTestId('sla-alerts')
    expect(within(headChips).getByText('80 % del SLA')).toBeTruthy()
    expect(within(headChips).getByText('SLA vencido · escalado')).toBeTruthy()
    expect(within(headChips).queryByText('Sin asignar')).toBeNull() // opening the case took it

    await user.click(screen.getByRole('tab', { name: 'Historial' }))
    const line = await screen.findByTestId('clock-timeline')
    const items = within(line).getAllByRole('listitem')
    expect(items.map((li) => li.getAttribute('data-kind'))).toEqual(['unassigned', 'sla_80', 'breached'])
    expect(items.every((li) => li.getAttribute('data-state') === 'fired')).toBe(true)
    expect(within(items[2]).getByText('SLA vencido')).toBeTruthy()
    expect(within(items[2]).getByText('Disparado')).toBeTruthy()
    expect(within(items[2]).getByText('sla_timer.breached')).toBeTruthy()
    expect(within(items[2]).getByText(/cola senior con prioridad alta/)).toBeTruthy()
    expect(screen.getByText(/EventBridge Scheduler/)).toBeTruthy()
  })

  it('opening a case takes it: the queue is read again at once and "Sin asignar" goes away', async () => {
    const listAnalystCases = vi
      .fn()
      .mockResolvedValueOnce(listResponse([listItem({ sla_alerts: ['unassigned'] })]))
      .mockResolvedValue(listResponse([listItem({ sla_alerts: [] })]))
    renderConsole(fakeApi({ listAnalystCases }), 'EV-1A2B3C4D')
    await screen.findByRole('heading', { level: 1 })
    await waitFor(() => expect(listAnalystCases).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(within(screen.getByRole('listbox')).queryByText('Sin asignar')).toBeNull())
    // No loop: the second listing no longer lists the alert, nothing else is read.
    await new Promise((r) => setTimeout(r, 50))
    expect(listAnalystCases).toHaveBeenCalledTimes(2)
  })

  it('a lane C case says it has no SLA timers', async () => {
    const user = userEvent.setup()
    const detail = analystDetail({ case: { lane: 'C', status: 'handed_off', clock_events: [] }, report: null, allowed_actions: [] })
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => detail) }), 'EV-1A2B3C4D')
    await screen.findByRole('heading', { level: 1 })
    await user.click(screen.getByRole('tab', { name: 'Historial' }))
    expect(await screen.findByText('Sin temporizadores: solo los casos de ruta B tienen reloj de SLA.')).toBeTruthy()
  })
})
