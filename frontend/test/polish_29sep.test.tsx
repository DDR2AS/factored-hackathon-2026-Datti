// Polish of known pending items (29 sep): the verified amount and its USD equivalent, the
// evidence field labels in ES and PT, SLA alert chips of answered cases, and the demo clock
// scale taken from the server (GET /health demo_clock_scale) instead of a fixed "1 día = 1
// minuto". Every payload is SYNTHETIC (test/fixtures.ts).
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { HealthResponse } from '../src/api/types'
import { saveAnalystSession } from '../src/console/auth'
import { ChatView } from '../src/views/ChatView'
import { ConsoleView } from '../src/views/ConsoleView'
import { analystDetail, chatResponse, listItem, listResponse, report, sessionResponse } from './fixtures'

const DEFAULT_SCALE = 1 / 1440 // lifecycle.DEFAULT_SLA_SCALE
const QUICK_SCALE = 0.00001 // docs/como_correr_local.md, --sla-scale 0.00001

function health(scale: number | null | undefined) {
  return vi.fn(async (): Promise<HealthResponse> => {
    const base = { status: 'ok' as const, stage: 'local', deps: 'ok' as const }
    return (scale === undefined ? base : { ...base, demo_clock_scale: scale }) as HealthResponse
  })
}

function fakeApi(overrides: Partial<Record<keyof ApiClient, unknown>> = {}): ApiClient {
  return {
    health: health(DEFAULT_SCALE),
    createSession: vi.fn(async () => sessionResponse()),
    chat: vi.fn(async () => chatResponse()),
    getCase: vi.fn(),
    isChatInFlight: () => false,
    listAnalystCases: vi.fn(async () => listResponse([listItem()])),
    getAnalystCase: vi.fn(async () => analystDetail()),
    decide: vi.fn(),
    ...overrides,
  } as ApiClient
}

function renderConsole(api: ApiClient, caseId: string | null = 'EV-1A2B3C4D') {
  saveAnalystSession({ token: 'local-token-synthetic', provider: 'local', signed_in_at: '2026-09-30T15:00:00Z' })
  return render(
    <ApiContext.Provider value={api}>
      <ConsoleView caseId={caseId} />
    </ApiContext.Provider>,
  )
}

function renderChat(api: ApiClient) {
  return render(
    <ApiContext.Provider value={api}>
      <ChatView demoKey="lucia" />
    </ApiContext.Provider>,
  )
}

// Every field key the provisional investigator writes in evidence_records (investigator_stub.py);
// tests/test_polish_29sep.py checks this list against a real run of the stub.
const STUB_FIELDS = {
  txn_id: 'TX-DEMO-LU-01',
  product_id: 'P-DEMO-LU',
  card_last4: '4821',
  local_date: '2026-06-12',
  local_time: '18:42',
  amount: '449.90',
  currency: 'MXN',
  amount_usd: '24.60',
  merchant_id: 'M-77',
  merchant_name: 'SUPER AHORRO SA',
  merchant_category: 'grocery',
  status: 'approved',
  response_code: '00',
  txn_type: 'purchase',
  reversal_of: null,
  fraud_score: 12,
  threshold: 50,
  source: 'get_transaction.fraud_score',
  window_from: '2026-01-01',
  window_to: '2026-05-29',
  n_txns: 40,
  median_amount: '469.40',
  p90_amount: '784.66',
  top_categories: 'grocery, restaurants',
  usual_hours: '9-21',
  prior_purchases: 3,
  window_days: 90,
  product_name: 'Cuenta demo',
  fee_code: 'MAINT',
  description: 'Mantenimiento',
  frequency: 'monthly',
  contact_id: 'C-1',
  date: '2026-05-01',
  channel: 'app',
  contact_type: 'complaint',
  subcategory: 'wrong_fee',
  card_id: 'DEMO-K-LU-01',
}

describe('console: verified amount and its USD equivalent', () => {
  it('shows the local currency code and the USD equivalent once, without "USD USD"', async () => {
    renderConsole(fakeApi())
    const block = await screen.findByTestId('console-verified')
    const text = block.textContent ?? ''
    expect(text).not.toMatch(/USD\s*USD/)
    expect(within(block).getByText('Monto (moneda local)')).toBeTruthy()
    expect(within(block).getByText(/449\.90\sMXN/)).toBeTruthy()
    expect(within(block).getByText('Equivalente en USD')).toBeTruthy()
    // es-MX writes USD as "USD 24.60"; the row label already says USD, so the value is the plain amount.
    const usd = within(block).getByTestId('verified-usd')
    expect(usd.textContent).toMatch(/24\.60/)
    expect(usd.textContent).not.toMatch(/USD/)
    expect(within(block).getByText(/tipo de cambio sintético/)).toBeTruthy()
  })

  it('in Portuguese too', async () => {
    const user = userEvent.setup()
    renderConsole(fakeApi())
    await screen.findByTestId('console-verified')
    await user.click(screen.getByRole('button', { name: 'Português' }))
    const block = screen.getByTestId('console-verified')
    expect(block.textContent ?? '').not.toMatch(/USD\s*USD/)
    expect(within(block).getByText('Valor (moeda local)')).toBeTruthy()
    expect(within(block).getByText('Equivalente em USD')).toBeTruthy()
  })
})

describe('console: evidence field labels', () => {
  const detail = analystDetail({
    report: report({
      findings: [{ claim: 'Registro completo (sintético).', evidence_ids: ['ALL'] }],
      evidence_records: { ALL: { kind: 'transaction', summary: 'todos los campos (sintético)', fields: STUB_FIELDS } },
    }),
  })

  it.each([
    ['es', 'Umbral', 'Fuente', 'Tarjeta (ID)'],
    ['pt', 'Limite', 'Fonte', 'Cartão (ID)'],
  ])('every field of the stub has a readable label (%s)', async (lang, threshold, source, cardId) => {
    const user = userEvent.setup()
    renderConsole(fakeApi({ getAnalystCase: vi.fn(async () => detail) }))
    const rec = await screen.findByTestId('evidence-ALL')
    if (lang === 'pt') await user.click(screen.getByRole('button', { name: 'Português' }))
    const raw = within(screen.getByTestId('evidence-ALL'))
      .getAllByRole('term')
      .filter((dt) => dt.classList.contains('mono'))
      .map((dt) => dt.textContent)
    expect(raw).toEqual([])
    expect(within(rec).getByText(threshold)).toBeTruthy()
    expect(within(rec).getByText(source)).toBeTruthy()
    expect(within(rec).getByText(cardId)).toBeTruthy()
  })
})

describe('queue: SLA alert chips of answered cases', () => {
  it('an answered case drops the chips (the queue is a to-do list); an open one keeps them', async () => {
    renderConsole(
      fakeApi({
        listAnalystCases: vi.fn(async () =>
          listResponse([
            listItem({ case_id: 'EV-OPEN0001', display_name: 'Lucía', sla_alerts: ['sla_80', 'breached'] }),
            listItem({ case_id: 'EV-DONE0001', display_name: 'João', status: 'notified', sla_alerts: ['sla_80', 'breached'] }),
            listItem({ case_id: 'EV-DONE0002', display_name: 'Sofía', status: 'closed', sla_alerts: ['breached'] }),
          ]),
        ),
      }),
      null,
    )
    const list = await screen.findByRole('listbox', { name: /Casos en la cola/ })
    const [open, notified, closed] = within(list).getAllByRole('option')
    expect(within(open).getByTestId('sla-alerts')).toBeTruthy()
    expect(within(notified).queryByTestId('sla-alerts')).toBeNull()
    expect(within(closed).queryByTestId('sla-alerts')).toBeNull()
    expect(within(notified).getByText('Respondido')).toBeTruthy()
  })

  it('the header of an answered case keeps them, dimmed and labeled as history', async () => {
    renderConsole(
      fakeApi({
        getAnalystCase: vi.fn(async () =>
          analystDetail({
            case: {
              status: 'notified',
              clock_events: [
                { kind: 'sla_80', fired_at: '2026-09-30T15:00:10Z' },
                { kind: 'breached', fired_at: '2026-09-30T15:00:13Z' },
              ],
            },
          }),
        ),
      }),
    )
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    const chips = screen.getByTestId('sla-alerts')
    expect(chips.classList.contains('sla-alerts--history')).toBe(true)
    expect(within(chips).getByText(/Historial del reloj/)).toBeTruthy()
    for (const chip of chips.querySelectorAll('.sla-alert')) expect(chip.classList.contains('sla-alert--past')).toBe(true)
  })

  it('an open case keeps live chips in the header', async () => {
    renderConsole(
      fakeApi({
        getAnalystCase: vi.fn(async () =>
          analystDetail({ case: { clock_events: [{ kind: 'sla_80', fired_at: '2026-09-30T15:00:10Z' }] } }),
        ),
      }),
    )
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    const chips = screen.getByTestId('sla-alerts')
    expect(chips.classList.contains('sla-alerts--history')).toBe(false)
    expect(within(chips).queryByText(/Historial del reloj/)).toBeNull()
  })
})

describe('demo clock scale from the server', () => {
  it('judge panel: 1/1440 reads "1 día = 1 minuto" and the times of the three alarms', async () => {
    renderChat(fakeApi())
    expect(await screen.findByRole('switch', { name: 'Reloj acelerado (1 día = 1 minuto)' })).toBeTruthy()
    const plan = screen.getByTestId('clock-plan')
    expect(within(plan).getByText('Tras 1 minuto (24 h):')).toBeTruthy()
    expect(within(plan).getByText('Tras 12 minutos (80 % del SLA de 15 días):')).toBeTruthy()
    expect(within(plan).getByText('Tras 15 minutos (SLA vencido):')).toBeTruthy()
    expect(screen.getByText(/corren 1440 veces más rápido/)).toBeTruthy()
  })

  it('judge panel: another scale (--sla-scale 0.00001) is what the text says', async () => {
    renderChat(fakeApi({ health: health(QUICK_SCALE) }))
    expect(await screen.findByRole('switch', { name: 'Reloj acelerado (1 día = 0,9 segundos)' })).toBeTruthy()
    const plan = screen.getByTestId('clock-plan')
    expect(within(plan).getByText('Tras 0,9 segundos (24 h):')).toBeTruthy()
    expect(within(plan).getByText('Tras 10,4 segundos (80 % del SLA de 15 días):')).toBeTruthy()
    expect(within(plan).getByText('Tras 13 segundos (SLA vencido):')).toBeTruthy()
    expect(screen.getByText(/corren 100.000 veces más rápido/)).toBeTruthy()
    expect(screen.queryByText(/1 minuto/)).toBeNull()
  })

  it.each([
    ['without the field (older server)', undefined],
    ['null (no demo clock)', null],
    ['health failing', 'fail' as const],
  ])('judge panel: neutral text %s', async (_label, scale) => {
    const h = scale === 'fail' ? vi.fn(async () => Promise.reject(new Error('down'))) : health(scale)
    renderChat(fakeApi({ health: h }))
    await waitFor(() => expect(h).toHaveBeenCalled())
    expect(await screen.findByRole('switch', { name: 'Reloj acelerado' })).toBeTruthy()
    const plan = screen.getByTestId('clock-plan')
    expect(within(plan).getByText('A las 24 h simuladas:')).toBeTruthy()
    expect(within(plan).getByText('Al 80 % del SLA de 15 días:')).toBeTruthy()
    expect(within(plan).getByText('Al vencer el SLA:')).toBeTruthy()
    expect(screen.queryByText(/1 minuto|1440/)).toBeNull()
  })

  it.each([
    [DEFAULT_SCALE, /1 día = 1 minuto/],
    [QUICK_SCALE, /1 día = 0,9 segundos/],
  ])('console Historial note uses the scale %s', async (scale, text) => {
    const user = userEvent.setup()
    renderConsole(fakeApi({ health: health(scale) }))
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('tab', { name: 'Historial' }))
    await waitFor(() => expect(screen.getByTestId('clock-note').textContent).toMatch(text))
  })

  it('console Historial note is neutral without a scale', async () => {
    const user = userEvent.setup()
    const h = health(undefined)
    renderConsole(fakeApi({ health: h }))
    await screen.findByRole('heading', { level: 1, name: /EV-1A2B3C4D/ })
    await user.click(screen.getByRole('tab', { name: 'Historial' }))
    await waitFor(() => expect(h).toHaveBeenCalled())
    const note = screen.getByTestId('clock-note').textContent ?? ''
    expect(note).toMatch(/disparan antes/)
    expect(note).not.toMatch(/1 minuto|=/)
  })
})
