// Automatic accessibility checks (axe-core through vitest-axe) on the five screens the judges
// see: the judge-mode landing, Lucía's chat with the live case card, João's chat in Portuguese,
// the analyst console with a case open, and the trace view. The gate is "no serious or critical
// violation". jsdom has no layout, so axe cannot measure color contrast here (it reports it as
// incomplete, not as a violation): contrast is covered by the tokens (tokens.css, WCAG AA) and the
// browser walk, not by this test. Every payload is SYNTHETIC (test/fixtures.ts).
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { AxeResults, Result } from 'axe-core'
import { describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'
import type { ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { ChatRequest } from '../src/api/types'
import { saveAnalystSession } from '../src/console/auth'
import { I18nProvider } from '../src/i18n/I18nProvider'
import { saveTranscript, type TraceRecord } from '../src/session'
import { ChatView } from '../src/views/ChatView'
import { ConsoleView } from '../src/views/ConsoleView'
import { JudgeLanding } from '../src/views/JudgeLanding'
import { TraceView } from '../src/views/TraceView'
import { analystDetail, caseView, chatResponse, listItem, listResponse, sessionResponse } from './fixtures'

const GATE = new Set(['serious', 'critical'])

/** Serious and critical violations, one line each (rule, impact, first targets). */
function blocking(results: AxeResults): string[] {
  return results.violations
    .filter((v: Result) => GATE.has(v.impact ?? ''))
    .map((v) => `${v.id} (${v.impact}): ${v.help} -> ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)
}

/** Every violation (any impact), for the moderate ones fixed on 29 sep: region (the synthetic
 *  banner outside any landmark), landmark-unique (two unnamed asides in the chat, two "Cola"
 *  regions in the console). */
let lastAll: string[] = []

async function audit(): Promise<string[]> {
  // color-contrast needs layout and canvas, which jsdom lacks (it only ever comes back incomplete).
  const results = await axe(document.body, { rules: { 'color-contrast': { enabled: false } } })
  lastAll = results.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)
  return blocking(results)
}

/** The gate (serious/critical) and, after it, nothing else either. */
async function expectClean(label = '') {
  expect(await audit(), `${label} serious/critical`).toEqual([])
  expect(lastAll, `${label} any impact`).toEqual([])
}

function fakeApi(overrides: Partial<Record<keyof ApiClient, unknown>> = {}): ApiClient {
  return {
    health: vi.fn(async () => ({ status: 'ok' as const, stage: 'local', deps: 'ok' as const, demo_clock_scale: 1 / 1440 })),
    createSession: vi.fn(async () => sessionResponse()),
    chat: vi.fn(async () => chatResponse()),
    getCase: vi.fn(async () => ({ case: caseView({ status: 'awaiting_analyst', lifecycle_step: 'in_review' }), poll_after_ms: null })),
    isChatInFlight: () => false,
    listAnalystCases: vi.fn(async () =>
      listResponse([
        listItem({ sla_alerts: ['sla_80'] }),
        listItem({ case_id: 'EV-AAAA0002', display_name: 'João', language: 'pt', country: 'AR', status: 'notified', sla_alerts: ['breached'] }),
      ]),
    ),
    getAnalystCase: vi.fn(async () => analystDetail()),
    decide: vi.fn(),
    ...overrides,
  } as ApiClient
}

describe('axe: no serious or critical violations', () => {
  it('the gate works: it catches a button without a name and an image without alt', async () => {
    render(
      <main>
        <h1>Prueba</h1>
        <button type="button" />
        <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" />
      </main>,
    )
    const found = await audit()
    expect(found.some((l) => l.startsWith('button-name (critical)'))).toBe(true)
    expect(found.some((l) => l.startsWith('image-alt (critical)'))).toBe(true)
  })

  it('judge-mode landing', async () => {
    render(
      <ApiContext.Provider value={fakeApi()}>
        <I18nProvider>
          <JudgeLanding />
        </I18nProvider>
      </ApiContext.Provider>,
    )
    await screen.findByText('API disponible · etapa local')
    await expectClean()
  })

  it("Lucía's chat with the live case card", async () => {
    const user = userEvent.setup()
    const chat = vi.fn(async (_req: ChatRequest) =>
      chatResponse({
        turn: 1,
        reply_text: 'Abrimos el caso EV-1A2B3C4D (sintético).',
        buttons: [],
        input_mode: 'free_text',
        progress: { step: 'case_open', complaint_type: 'unrecognized_charge', claimed: chatResponse().progress.claimed, missing: [] },
        case_card: caseView({ status: 'awaiting_analyst', lifecycle_step: 'in_review' }),
        lane: 'B',
      }),
    )
    render(
      <ApiContext.Provider value={fakeApi({ chat })}>
        <ChatView demoKey="lucia" />
      </ApiContext.Provider>,
    )
    await screen.findByRole('button', { name: 'Un cargo que no reconozco' })
    await user.type(screen.getByRole('textbox'), 'me cobraron 450 en el súper{Enter}')
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(1))
    await screen.findAllByText(/EV-1A2B3C4D/)
    await screen.findByRole('switch', { name: 'Reloj acelerado (1 día = 1 minuto)' })
    await expectClean()
  })

  it("João's chat in Portuguese", async () => {
    const user = userEvent.setup()
    const session = sessionResponse({
      customer: { display_name: 'João', language: 'pt', locale: 'pt-BR', country: 'AR' },
      welcome: {
        turn: 0,
        reply_text: 'Olá João, como posso ajudar? (sintético)',
        reply_language: 'pt',
        reply_source: 'template',
        buttons: [{ id: 'start:p1', label: 'Uma tarifa errada', kind: 'choice' }],
        input_mode: 'free_text',
      },
    })
    const chat = vi.fn(async () =>
      chatResponse({
        reply_text: 'Abrimos o caso EV-JOAO0001 (sintético).',
        reply_language: 'pt',
        buttons: [],
        input_mode: 'free_text',
        case_card: caseView({
          case_id: 'EV-JOAO0001',
          language: 'pt',
          status: 'awaiting_analyst',
          lifecycle_step: 'in_review',
          charge: { local_date: '2026-06-01', amount: '12500.00', currency: 'ARS', merchant_name: 'LATAM BANK', card_last4: '4821', status: 'posted' },
        }),
        lane: 'B',
      }),
    )
    render(
      <ApiContext.Provider value={fakeApi({ createSession: vi.fn(async () => session), chat })}>
        <ChatView demoKey="joao" />
      </ApiContext.Provider>,
    )
    await screen.findByRole('button', { name: 'Uma tarifa errada' })
    await user.type(screen.getByRole('textbox'), 'cobraram uma tarifa errada{Enter}')
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(1))
    await screen.findAllByText(/EV-JOAO0001/)
    expect(document.documentElement.lang === 'pt-BR' || document.querySelector('[lang="pt-BR"]')).toBeTruthy()
    await expectClean()
  })

  it('analyst console with a case open', async () => {
    saveAnalystSession({ token: 'local-token-synthetic', provider: 'local', signed_in_at: '2026-09-30T15:00:00Z' })
    render(
      <ApiContext.Provider value={fakeApi()}>
        <ConsoleView caseId="EV-1A2B3C4D" />
      </ApiContext.Provider>,
    )
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await screen.findByRole('listbox', { name: /Casos en la cola/ })
    await expectClean()
    // The other tabs of the case too (Riesgo, Acciones, Historial, Traza).
    const user = userEvent.setup()
    for (const name of ['Riesgo', 'Acciones', 'Historial', 'Traza']) {
      await user.click(screen.getByRole('tab', { name }))
      await expectClean(name)
    }
  })

  it('trace view', async () => {
    const base = chatResponse().trace_summary
    const traces: TraceRecord[] = [
      { turn: 1, trace_id: 'tr-0001', at: '2026-09-30T15:00:01Z', trace_summary: base, degraded: [], lane: null, client_latency_ms: 950 },
      {
        turn: 2,
        trace_id: 'tr-0002',
        at: '2026-09-30T15:00:20Z',
        trace_summary: { ...base, rule_id: 'tool_failure', model_id: null },
        degraded: ['tool_unavailable'],
        lane: 'C',
        client_latency_ms: 180,
      },
    ]
    saveTranscript({ demo_key: 'lucia', entries: [], used_button_ids: [], last: null, pending: null, traces })
    render(
      <I18nProvider>
        <TraceView traceId={null} />
      </I18nProvider>,
    )
    await screen.findAllByText(/tr-0002/)
    await expectClean()
  })
})
