// Trace view (#/traza): turns of this tab's session from the transcript in sessionStorage.
// Every payload is SYNTHETIC.
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RouteBoundary } from '../src/components/RouteBoundary'
import { TraceStrip } from '../src/components/TraceStrip'
import { I18nProvider } from '../src/i18n/I18nProvider'
import { saveTranscript, type TraceRecord, type TurnSnapshot } from '../src/session'
import { TraceView } from '../src/views/TraceView'
import { chatResponse } from './fixtures'

const base = chatResponse().trace_summary

const records: TraceRecord[] = [
  {
    turn: 1,
    trace_id: 'tr-0001',
    at: '2026-09-30T15:00:01Z',
    trace_summary: base,
    degraded: [],
    lane: null,
    client_latency_ms: 950,
  },
  {
    turn: 2,
    trace_id: 'tr-0002',
    at: '2026-09-30T15:00:20Z',
    trace_summary: {
      steps: [
        { actor: 'tool', name: 'get_transaction', latency_ms: 30, version: null, error_code: 'tool_unavailable' },
        { actor: 'tool', name: 'get_transaction', latency_ms: 31, version: null, error_code: 'tool_unavailable' },
        { actor: 'rule', name: 'lane_rules', latency_ms: 2, version: '2026-09-29.2', error_code: null },
      ],
      latency_ms: 140,
      cost_usd: 0,
      model_id: null,
      tokens_in: null,
      tokens_out: null,
      rule_id: 'tool_failure',
      rules_version: '2026-09-29.2',
    },
    degraded: ['tool_unavailable'],
    lane: 'C',
    client_latency_ms: 180,
  },
]

function seed(traces: TraceRecord[] | undefined) {
  saveTranscript({ demo_key: 'lucia', entries: [], used_button_ids: [], last: null, pending: null, traces })
}

function renderView(traceId: string | null) {
  return render(
    <I18nProvider>
      <TraceView traceId={traceId} />
    </I18nProvider>,
  )
}

describe('TraceView', () => {
  it('lists every turn with its full trace_summary, rule, version and degradations', () => {
    seed(records)
    renderView('tr-0002')
    expect(screen.getByRole('heading', { level: 1, name: 'Trazas de la sesión' })).toBeTruthy()
    expect(screen.getByText(/Conversación con Lucía en esta pestaña · 2 turnos/)).toBeTruthy()
    const turns = screen.getAllByRole('listitem').filter((li) => li.classList.contains('turn'))
    expect(turns).toHaveLength(2)

    const first = turns[0]
    const rows1 = within(within(first).getByRole('table')).getAllByRole('row')
    expect(rows1).toHaveLength(1 + base.steps.length)
    expect(within(first).getByText('extract')).toBeTruthy()
    expect(within(first).getByText('g1-extract@v1')).toBeTruthy()
    expect(within(first).getByText('GenAI')).toBeTruthy()
    expect(within(first).getByText('mock-chat')).toBeTruthy()

    const second = turns[1]
    expect(second.getAttribute('aria-current')).toBe('true')
    expect(within(second).getAllByText('tool_unavailable')).toHaveLength(2)
    expect(within(second).getByText('tool_failure')).toBeTruthy()
    expect(within(second).getAllByText(/2026-09-29\.2/).length).toBeGreaterThan(0)
    expect(within(second).getByText('sin modelo')).toBeTruthy()
    expect(within(second).getByText('Herramienta no disponible: el caso pasa a una persona')).toBeTruthy()
    expect(within(second).getByText('Ruta C')).toBeTruthy()
  })

  it('says when the requested trace is not in this tab', () => {
    seed(records)
    renderView('tr-9999')
    expect(screen.getByText('La traza tr-9999 no está en esta pestaña.')).toBeTruthy()
  })

  it('shows an empty state without a conversation', () => {
    renderView(null)
    expect(screen.getByText('Todavía no hay turnos')).toBeTruthy()
    expect(screen.getByText('No hay una conversación abierta en esta pestaña.')).toBeTruthy()
  })

  it('reads older transcripts that only kept the last turn', () => {
    const r = chatResponse()
    const last: TurnSnapshot = {
      progress: r.progress,
      case_card: null,
      lane: null,
      degraded: [],
      trace_id: 'tr-0007',
      trace_summary: r.trace_summary,
      client_latency_ms: 900,
      turn: 7,
    }
    saveTranscript({ demo_key: 'joao', entries: [], used_button_ids: [], last, pending: null })
    renderView(null)
    expect(screen.getByText('Turno 7')).toBeTruthy()
    expect(screen.getByText(/João/)).toBeTruthy()
  })
})

describe('TraceStrip', () => {
  it('links to the full trace of the turn', () => {
    const r = chatResponse()
    render(
      <I18nProvider>
        <TraceStrip
          snapshot={{
            progress: r.progress,
            case_card: null,
            lane: null,
            degraded: [],
            trace_id: 'tr-0001',
            trace_summary: r.trace_summary,
            client_latency_ms: 950,
            turn: 1,
          }}
        />
      </I18nProvider>,
    )
    expect(screen.getByRole('link', { name: 'Ver traza completa' }).getAttribute('href')).toBe('#/traza/tr-0001')
  })
})

describe('RouteBoundary', () => {
  it('a lazy view that fails to load offers a reload instead of a blank page', () => {
    const Broken = () => {
      throw new Error('Failed to fetch dynamically imported module')
    }
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <I18nProvider>
        <RouteBoundary>
          <Broken />
        </RouteBoundary>
      </I18nProvider>,
    )
    spy.mockRestore()
    expect(screen.getByRole('alert').textContent).toMatch(/No se pudo cargar esta vista/)
    expect(screen.getByRole('button', { name: 'Recargar' })).toBeTruthy()
  })
})
