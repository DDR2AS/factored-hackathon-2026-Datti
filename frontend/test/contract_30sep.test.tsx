// Contract of 30 sep (types.ts <-> contract.py): context.unavailable, the case conversation in
// the console, G1 in the trace (tokens, model, cost, rule-based fallback) and the new
// request_information_default text. Every payload is SYNTHETIC (test/fixtures.ts).
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { ConversationTurn, DecisionRequest, TraceSummary } from '../src/api/types'
import { TraceStrip } from '../src/components/TraceStrip'
import { saveAnalystSession } from '../src/console/auth'
import { requestInformationDefault } from '../src/console/customerTexts'
import { I18nProvider } from '../src/i18n/I18nProvider'
import { saveTranscript, type TurnSnapshot } from '../src/session'
import { ConsoleView } from '../src/views/ConsoleView'
import { TraceView } from '../src/views/TraceView'
import { analystDetail, chatResponse, decisionResponse, listItem, listResponse } from './fixtures'

function fakeApi(overrides: Partial<Record<keyof ApiClient, unknown>> = {}): ApiClient {
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
  } as ApiClient
}

function renderConsole(api: ApiClient) {
  saveAnalystSession({ token: 'local-token-synthetic', provider: 'local', signed_in_at: '2026-09-30T15:00:00Z' })
  return render(
    <ApiContext.Provider value={api}>
      <ConsoleView caseId="EV-1A2B3C4D" />
    </ApiContext.Provider>,
  )
}

const openCase = () => screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })

const XSS = '<img src=x onerror=alert(1)><b>negrita</b>'

const TURNS: ConversationTurn[] = [
  { role: 'system', text: 'Hola Lucía, ¿en qué te ayudo? (sintético)', language: 'es', source: 'template', at: '2026-09-30T15:00:00Z' },
  { role: 'customer', text: `me cobraron 450 en el súper ${XSS} [DOCUMENTO]`, language: 'es', source: 'human', at: '2026-09-30T15:00:05Z' },
  { role: 'customer', text: '[botón] No lo reconozco', language: 'es', source: 'human', at: '2026-09-30T15:00:09Z' },
  { role: 'system', text: 'Texto de modelo (sintético)', language: 'es', source: 'model', at: '2026-09-30T15:00:10Z' },
  { role: 'analyst', text: 'Olá, revisamos o seu caso (sintético).', language: 'pt', source: 'human', at: '2026-09-30T15:10:00Z' },
]

describe('console: the case conversation (redacted, synthetic)', () => {
  it('is folded by default, says redacted and synthetic, and shows role, source and time per turn as text', async () => {
    const user = userEvent.setup()
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => analystDetail({ case: { conversation: TURNS } })) }))
    await openCase()
    const section = screen.getByTestId('console-conversation') as HTMLDetailsElement
    expect(section.tagName).toBe('DETAILS')
    expect(section.open).toBe(false)
    expect(within(section).getByText('Conversación (redactada, sintética)')).toBeTruthy()
    expect(within(section).getByText('5 turnos')).toBeTruthy()

    await user.click(within(section).getByText('Conversación (redactada, sintética)'))
    expect(section.open).toBe(true)
    const list = within(section).getByRole('list', { name: /Turnos de la conversación/ })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(5)
    expect(within(items[0]).getByText('Sistema')).toBeTruthy()
    expect(within(items[0]).getByText('Plantilla')).toBeTruthy()
    expect(within(items[1]).getByText('Cliente')).toBeTruthy()
    expect(within(items[1]).getByText('Humano')).toBeTruthy()
    expect(within(items[3]).getByText('GenAI')).toBeTruthy()
    expect(within(items[4]).getByText('Analista')).toBeTruthy()
    // Text is text: the pasted HTML is shown literally and creates no element.
    expect(within(items[1]).getByText(`me cobraron 450 en el súper ${XSS} [DOCUMENTO]`)).toBeTruthy()
    expect(section.querySelector('img')).toBeNull()
    expect(section.querySelector('b')).toBeNull()
    expect(within(items[2]).getByText('[botón] No lo reconozco')).toBeTruthy()
    // Each turn in its own language.
    expect(within(items[4]).getByText('Olá, revisamos o seu caso (sintético).').getAttribute('lang')).toBe('pt-BR')
    expect(within(items[0]).getByText(/Hola Lucía/).getAttribute('lang')).toBe('es')
  })

  it('an empty conversation says so', async () => {
    renderConsole(fakeApi())
    await openCase()
    const section = screen.getByTestId('console-conversation')
    expect(within(section).getByText('0 turnos')).toBeTruthy()
    expect(within(section).getByText('No hay turnos registrados para este caso.')).toBeTruthy()
  })

  it('in the Portuguese console', async () => {
    window.sessionStorage.setItem('ev.console.lang', 'pt')
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => analystDetail({ case: { conversation: TURNS.slice(0, 1) } })) }))
    await openCase()
    const section = screen.getByTestId('console-conversation')
    expect(within(section).getByText('Conversa (anonimizada, sintética)')).toBeTruthy()
    expect(within(section).getByText('1 turno')).toBeTruthy()
  })
})

const g1Summary = (over: Partial<TraceSummary> = {}): TraceSummary => ({
  steps: [
    { actor: 'rule', name: 'classify', latency_ms: 1, version: 'm2-rule@0', error_code: null },
    { actor: 'rule', name: 'extract', latency_ms: 2, version: 'extract@3', error_code: null },
    { actor: 'model', name: 'g1_extract', latency_ms: 35, version: 'g1_extract@v1', error_code: null },
    { actor: 'tool', name: 'find_candidate_txns', latency_ms: 4, version: null, error_code: null },
  ],
  latency_ms: 60,
  cost_usd: 0.001586,
  model_id: 'mock-g1',
  tokens_in: 1341,
  tokens_out: 49,
  rule_id: null,
  rules_version: '2026-09-29.2',
  ...over,
})

function snapshot(summary: TraceSummary): TurnSnapshot {
  const r = chatResponse()
  return {
    progress: r.progress,
    case_card: null,
    lane: null,
    degraded: [],
    trace_id: 'tr-0101',
    trace_summary: summary,
    client_latency_ms: 90,
    turn: 1,
  }
}

function renderStrip(summary: TraceSummary) {
  return render(
    <I18nProvider>
      <TraceStrip snapshot={snapshot(summary)} />
    </I18nProvider>,
  )
}

const g1Step = () => screen.getByText('g1_extract').closest('li') as HTMLElement

describe('trace: the G1 extraction step', () => {
  it('shows G1 as GenAI with model id, tokens and the (estimated) cost', () => {
    renderStrip(g1Summary())
    const step = g1Step()
    expect(within(step).getByText('GenAI')).toBeTruthy()
    const detail = within(step).getByTestId('g1-model')
    expect(detail.textContent).toContain('mock-g1')
    expect(detail.textContent).toContain('1341 de entrada · 49 de salida')
    expect(detail.textContent).toContain('0,0016')
    expect(detail.textContent).toContain('(estimado)')
    expect(within(step).queryByTestId('g1-fallback')).toBeNull()
    expect(step.className).not.toContain('trace__step--error')
    // The summary has the turn's tokens too; the rule-based extract step stays a Rule.
    expect(screen.getByText('Tokens')).toBeTruthy()
    const extract = screen.getByText('extract').closest('li') as HTMLElement
    expect(within(extract).getByText('Regla')).toBeTruthy()
  })

  it('schema_invalid: the billed model still shows, and the rule-based fallback is visible', () => {
    renderStrip(
      g1Summary({
        steps: [{ actor: 'model', name: 'g1_extract', latency_ms: 30, version: 'g1_extract@v1', error_code: 'schema_invalid' }],
      }),
    )
    const step = g1Step()
    expect(within(step).getByText('error schema_invalid')).toBeTruthy()
    expect(within(step).getByTestId('g1-model').textContent).toContain('mock-g1')
    expect(within(step).getByTestId('g1-fallback').textContent).toContain('Respaldo por reglas')
    expect(step.className).toContain('trace__step--fallback')
  })

  it('no_fixture: no model answered, the rules did', () => {
    renderStrip(
      g1Summary({
        steps: [{ actor: 'model', name: 'g1_extract', latency_ms: 1, version: 'g1_extract@v1', error_code: 'no_fixture' }],
        model_id: null,
        tokens_in: null,
        tokens_out: null,
        cost_usd: 0,
      }),
    )
    const step = g1Step()
    expect(within(step).queryByTestId('g1-model')).toBeNull()
    expect(within(step).getByTestId('g1-fallback')).toBeTruthy()
    expect(screen.getByText('sin modelo')).toBeTruthy()
    expect(screen.queryByText('Tokens')).toBeNull()
  })

  it('the trace view shows the same detail in the step table', () => {
    saveTranscript({
      demo_key: 'sofia',
      entries: [],
      used_button_ids: [],
      last: null,
      pending: null,
      traces: [
        {
          turn: 1,
          trace_id: 'tr-0101',
          at: '2026-09-30T15:00:01Z',
          trace_summary: g1Summary({
            steps: [{ actor: 'model', name: 'g1_extract', latency_ms: 30, version: 'g1_extract@v1', error_code: 'schema_invalid' }],
          }),
          degraded: [],
          lane: null,
          client_latency_ms: 90,
        },
      ],
    })
    render(
      <I18nProvider>
        <TraceView traceId={null} />
      </I18nProvider>,
    )
    const row = screen.getByText('g1_extract').closest('tr') as HTMLElement
    expect(row.className).toBe('row--fallback')
    expect(within(row).getByText('GenAI')).toBeTruthy()
    expect(within(row).getByTestId('g1-model').textContent).toContain('1341 de entrada')
    expect(within(row).getByTestId('g1-fallback')).toBeTruthy()
    expect(screen.getByText('1341 de entrada · 49 de salida')).toBeTruthy()
  })
})

describe('request_information_default (copy of the backend template, 30 sep)', () => {
  it('asks the customer to answer in the chat, per register and in PT', () => {
    expect(requestInformationDefault('es', 'MX', 'EV-1')).toBe(
      'Para avanzar con tu caso EV-1 necesitamos un dato más. Respóndenos por este chat y lo agregamos a tu caso.',
    )
    expect(requestInformationDefault('es', 'CO', 'EV-1')).toBe(
      'Para avanzar con su caso EV-1 necesitamos un dato más. Respóndanos por este chat y lo agregamos a su caso.',
    )
    expect(requestInformationDefault('es', 'AR', 'EV-1')).toBe(
      'Para avanzar con tu caso EV-1 necesitamos un dato más. Respondenos por este chat y lo agregamos a tu caso.',
    )
    expect(requestInformationDefault('pt', null, 'EV-1')).toBe(
      'Para avançar com o seu caso EV-1 precisamos de mais uma informação. Responda por este chat e nós a adicionamos ao seu caso.',
    )
  })
})
