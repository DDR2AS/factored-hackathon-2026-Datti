// Evaluation page (#/evaluacion, GET /evaluation) and its defensive readers. Every payload is
// SYNTHETIC (test/fixtures.ts), shaped like the report of src/eval/report.py.
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClientError, type ApiClient } from '../src/api/client'
import { ApiContext } from '../src/api/context'
import type { EvaluationResponse } from '../src/api/types'
import App from '../src/App'
import { formatPercent, m1Summary, m2Summary, m3Summary } from '../src/evaluation/read'
import { I18nProvider } from '../src/i18n/I18nProvider'
import { EvaluationView } from '../src/views/EvaluationView'
import { JudgeLanding } from '../src/views/JudgeLanding'
import { evaluationReport, evaluationResponse } from './fixtures'

function fakeApi(getEvaluation: () => Promise<EvaluationResponse>) {
  return {
    health: vi.fn(async () => ({ status: 'ok' as const, stage: 'local', deps: 'ok' as const, demo_clock_scale: null })),
    getEvaluation: vi.fn(getEvaluation),
  } as unknown as ApiClient & { getEvaluation: ReturnType<typeof vi.fn> }
}

function renderEvaluation(api: ApiClient, initial: 'es' | 'pt' = 'es') {
  return render(
    <ApiContext.Provider value={api}>
      <I18nProvider initial={initial}>
        <EvaluationView />
      </I18nProvider>
    </ApiContext.Provider>,
  )
}

/** The row of a table whose row header (first cell) matches. */
function row(table: HTMLElement, name: string | RegExp): HTMLElement {
  const header = within(table).getByRole('rowheader', { name })
  return header.closest('tr') as HTMLElement
}

describe('evaluation page', () => {
  it('shows the summary, the synthetic-data banner and the headline table with k of n, CI and pass badges', async () => {
    renderEvaluation(fakeApi(async () => evaluationResponse()))
    expect(screen.getByRole('heading', { level: 1, name: 'Evaluación' })).toBeTruthy()
    const table = await screen.findByRole('table', { name: /v2 frente a B1 sobre los mismos escenarios/ })
    expect(screen.getByText('240 escenarios sembrados (36 adversarios) · idiomas: ES 115, PT 125 · países: AR 54, CO 79, MX 107')).toBeTruthy()
    const banner = screen.getByText('Datos: réplica sintética del dataset; los números no son del dataset de los organizadores.')
    expect(banner.closest('[role="note"]')).toBeTruthy()
    expect(screen.getByText(/^Generado el .*UTC · versión eval-2026-10-29\.1 · tablas gold del /)).toBeTruthy()
    expect(
      screen.getByText('Mismos escenarios, dos configuraciones: v2: M1 + M2 + G1 + G2 · B1: v1.4 solo reglas (sin investigador)'),
    ).toBeTruthy()

    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(['Métrica', 'v2', 'B1 (v1.4 solo reglas)', 'Meta', '¿Cumple?'])
    const safe = row(table, 'Resolución automática segura')
    expect(within(safe).getByText(/^41,6\s%$/)).toBeTruthy()
    expect(within(safe).getByText('79 de 190')).toBeTruthy()
    expect(within(safe).getByText(/^IC 95 %: 34,7\s% – 47,9\s%$/)).toBeTruthy()
    expect(within(safe).getByText('77 de 190')).toBeTruthy()
    expect(within(safe).getByText('>= 30% con 0 cierres incorrectos')).toBeTruthy()
    expect(within(safe).getByText('Cumple')).toBeTruthy()
    // Not color alone: the failing row says it in words, with an icon.
    const failing = within(row(table, 'Derivaciones innecesarias')).getByText('No cumple')
    expect(failing.closest('.evbadge')?.querySelector('svg')).toBeTruthy()
    expect(within(row(table, 'Derivaciones innecesarias')).getByText(/^12\s%$/)).toBeTruthy()
    // No pass rule: a dash, explained to screen readers; B1 null: "sin dato".
    const containment = row(table, 'Contención (cerrado sin humano)')
    expect(within(containment).getByText('sin regla de aprobación')).toBeTruthy()
    expect(within(containment).getByText('sin dato')).toBeTruthy()
    // A metric this page does not know yet keeps the report's own label.
    expect(within(row(table, 'Métrica nueva del generador')).getAllByText('sin dato').length).toBeGreaterThan(0)
    expect(within(row(table, 'Investigador: reportes con afirmaciones sin respaldo quitadas')).getByText(/^3,17\s%$/)).toBeTruthy()
  })

  it('shows latency and cost tiles, fairness, models, security probes, B0 and the limits', async () => {
    renderEvaluation(fakeApi(async () => evaluationResponse()))
    await screen.findByRole('heading', { level: 2, name: 'Latencia y costo' })
    const p95 = screen.getByText('Turno de chat · p95').closest('.evtile') as HTMLElement
    expect(within(p95).getByText('72 ms')).toBeTruthy()
    expect(within(p95).getByText(/turnos medidos: 639/)).toBeTruthy()
    expect(within(screen.getByText('Costo por conversación').closest('.evtile') as HTMLElement).getByText(/0,0000/)).toBeTruthy()
    expect(screen.getByText('turno p95 < 6 s; investigador p95 < 90 s')).toBeTruthy()
    // The projection with real models: its own tile, labeled as a projection, and its parts.
    const projected = screen.getByText('Costo proyectado con modelos reales').closest('.evtile') as HTMLElement
    expect(within(projected).getByText(/^0,0123\sUS\$$/)).toBeTruthy()
    expect(within(projected).getByText('proyección, no factura')).toBeTruthy()
    expect(
      screen.getByText(
        /^Proyección: chat 0,0041\sUS\$ por conversación \(claude-chat-synthetic\) más investigador 0,0312\sUS\$ por caso \(claude-investigator-synthetic\) en el 26,3\s% de las conversaciones\.$/,
      ),
    ).toBeTruthy()
    expect(screen.getByText(/^PROYECCIÓN con precios de lista/)).toBeTruthy()

    const byLanguage = screen.getByRole('table', { name: 'Resultados de v2 por idioma del cliente' })
    const pt = row(byLanguage, /Portugués/)
    expect(within(pt).getByText('125')).toBeTruthy()
    expect(within(pt).getByText(/^37,2\s%$/)).toBeTruthy()
    expect(within(pt).getByText('sin dato')).toBeTruthy() // charge_accuracy null
    expect(within(pt).getByText(/^93,5\s%$/)).toBeTruthy() // resolved when it should have been
    expect(within(pt).getByText('de 46 con ruta A esperada')).toBeTruthy()
    const byCountry = screen.getByRole('table', { name: 'Resultados de v2 por país del cliente' })
    expect(within(row(byCountry, /Argentina/)).getByText('sin dato')).toBeTruthy() // an older report: no fair rate

    const gaps = screen.getByRole('table', { name: /Diferencia entre el grupo más alto y el más bajo/ })
    const outside = row(gaps, 'Idioma · Resolución automática segura')
    expect(within(outside).getByText('8,6 puntos')).toBeTruthy()
    expect(within(outside).getByText('Español frente a Portugués')).toBeTruthy()
    expect(within(outside).getByText('Fuera de la meta').closest('.evbadge')?.className).toContain('evbadge--fail')
    expect(within(outside).getByText(/^mezcla de escenarios/)).toBeTruthy()
    const equal = row(gaps, 'Idioma · Ruta correcta')
    expect(within(equal).getByText('igual en todos los grupos')).toBeTruthy()
    expect(within(equal).getByText('Dentro de la meta').closest('.evbadge')?.className).toContain('evbadge--pass')
    // A metric this page does not know keeps the report's text.
    expect(within(row(gaps, 'País · métrica futura')).getByText('México frente a Colombia')).toBeTruthy()

    const m1 = screen.getByRole('table', { name: /M1 frente a la regla v1\.4 en 8\.?000 consultas de prueba/ })
    const wrong = row(m1, 'Confirmaciones equivocadas')
    expect(within(wrong).getByText(/^0,35\s%$/)).toBeTruthy()
    expect(within(wrong).getByText(/^IC 95 %: 0,22\s% – 0,49\s%$/)).toBeTruthy()
    expect(within(wrong).getByText(/^2,88\s%$/)).toBeTruthy()
    expect(screen.getByText(/versión m1-lgbm-1 · en la app decide el modelo · umbrales: confirmar con p ≥ 0,95/)).toBeTruthy()
    expect(screen.getByText('Pendiente: M2 todavía no tiene reporte de prueba.')).toBeTruthy()
    expect(screen.getByText('No significativo')).toBeTruthy()
    expect(screen.getByText('modelo 0,0023 · fraud_score 0,00268')).toBeTruthy()
    expect(screen.getByText(/Solo evidencia para el investigador y el analista/)).toBeTruthy()
    expect(screen.getByText('Política determinista: sin modelo')).toBeTruthy()
    expect(screen.getByText('Sin credenciales de modelo, el investigador corre con la política determinista.')).toBeTruthy()

    const security = screen.getByRole('table', { name: /Sondas sobre la misma app/ })
    const scope = row(security, 'Herramientas pidiendo registros de otro cliente')
    expect(within(scope).getByText('Fugas: 0 · intentos: 204').closest('.evbadge')?.className).toContain('evbadge--pass')
    expect(within(scope).getByText('Fugas: 2 · intentos: 204').closest('.evbadge')?.className).toContain('evbadge--fail')
    expect(within(row(security, 'Mensajes con la sesión vencida')).getByText('Sin intentos')).toBeTruthy()
    expect(within(row(security, 'Consultar el caso de otra sesión')).getByText('sin dato')).toBeTruthy()
    const down = row(security, 'Todas las herramientas caídas')
    expect(within(down).getByText('Camino seguro')).toBeTruthy()
    expect(within(down).getByText('Ruta C · tool_unavailable')).toBeTruthy()
    expect(within(down).getByText('Camino NO seguro')).toBeTruthy()

    const b0 = screen.getByRole('table', { name: /Indicadores medidos en los datos históricos/ })
    expect(within(row(b0, /Mediana de días hasta resolver/)).getByText('15 días')).toBeTruthy()
    expect(within(row(b0, /Precisión de la regla/)).getByText(/^100\s%$/)).toBeTruthy()
    expect(within(row(b0, /CSAT medio/)).getByText('2,43 de 5')).toBeTruthy()
    const limits = screen.getByRole('heading', { level: 2, name: 'Límites' }).closest('section') as HTMLElement
    expect(within(limits).getAllByRole('listitem')).toHaveLength(2)
  })

  it('says when there is no report yet (make eval)', async () => {
    const api = fakeApi(async () => evaluationResponse(null))
    renderEvaluation(api)
    expect(await screen.findByRole('heading', { level: 2, name: 'Todavía no hay una evaluación publicada' })).toBeTruthy()
    expect(screen.getByText(/make eval/)).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('shows a loading state, then an error with a retry that loads the report', async () => {
    const user = userEvent.setup()
    let rejectFirst: (e: unknown) => void = () => {}
    let calls = 0
    const api = fakeApi(() => {
      calls += 1
      if (calls === 1) {
        return new Promise<EvaluationResponse>((_resolve, reject) => {
          rejectFirst = reject
        })
      }
      return Promise.resolve(evaluationResponse())
    })
    renderEvaluation(api)
    expect(screen.getByRole('status').textContent).toContain('Cargando la evaluación…')
    rejectFirst(new ApiClientError({ code: 'network', status: null, retryable: true, message: 'x' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('No pudimos cargar la evaluación.')
    expect(alert.textContent).toContain('Sin conexión con el servidor')
    await user.click(within(alert).getByRole('button', { name: 'Reintentar' }))
    await screen.findByRole('table', { name: /v2 frente a B1/ })
    expect(api.getEvaluation).toHaveBeenCalledTimes(2)
  })

  it('an older API without the route (404) is an error, not a crash', async () => {
    renderEvaluation(fakeApi(async () => Promise.reject(new ApiClientError({ code: 'not_found', status: 404, retryable: false, message: 'x' }))))
    expect((await screen.findByRole('alert')).textContent).toContain('La API no tiene esa ruta.')
  })

  it('renders a report full of nulls with dashes', async () => {
    const empty = evaluationReport({
      version: null, generated_at: null, data_source: null, gold_snapshot: null, scenarios: null, configs: null,
      headline: null, latency: null, cost: null, fairness: null, by_kind: null, security: null, models: null,
      baseline_b0: null, limits: null, row_counts: null,
    })
    renderEvaluation(fakeApi(async () => evaluationResponse(empty)))
    expect(await screen.findByText('El reporte no trae métricas principales.')).toBeTruthy()
    expect(screen.getByText('El reporte no dice de qué datos salen los números.')).toBeTruthy()
    expect(screen.getByText('Sin reporte de M1.')).toBeTruthy()
    expect(screen.getByText('Pendiente: M2 todavía no tiene reporte de prueba.')).toBeTruthy()
    expect(screen.getByText('El reporte no lista límites.')).toBeTruthy()
    expect(screen.getAllByText('sin dato').length).toBeGreaterThan(5)
  })

  it('speaks Portuguese and marks the report’s Spanish text as Spanish', async () => {
    renderEvaluation(fakeApi(async () => evaluationResponse()), 'pt')
    expect(screen.getByRole('heading', { level: 1, name: 'Avaliação' })).toBeTruthy()
    const table = await screen.findByRole('table', { name: /v2 frente a B1 nos mesmos cenários/ })
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Métrica', 'v2', 'B1 (v1.4 só regras)', 'Meta', 'Cumpre?',
    ])
    const safe = row(table, 'Resolução automática segura')
    expect(within(safe).getByText('Cumpre')).toBeTruthy()
    expect(row(table, 'Investigador: relatórios com afirmações sem respaldo retiradas')).toBeTruthy()
    const gaps = screen.getByRole('table', { name: /Diferença entre o grupo mais alto/ })
    expect(within(row(gaps, 'Idioma · Resolução automática segura')).getByText('Espanhol frente a Português')).toBeTruthy()
    expect(within(row(gaps, /métrica futura/)).getByText('métrica futura').getAttribute('lang')).toBe('es')
    expect(within(safe).getByText('>= 30% con 0 cierres incorrectos').getAttribute('lang')).toBe('es')
    expect(screen.getByText('Dados: réplica sintética do dataset; os números não são do dataset dos organizadores.')).toBeTruthy()
    expect(screen.getByText('240 cenários semeados (36 adversariais) · idiomas: ES 115, PT 125 · países: AR 54, CO 79, MX 107')).toBeTruthy()
    expect(screen.getByText('El portugués es generado, sin revisión nativa.').getAttribute('lang')).toBe('es')
    expect(screen.getByText('Pendente: o M2 ainda não tem relatório de teste.')).toBeTruthy()
  })

  it('shows M2 when its report brings test macro-F1 (the report’s compact summary)', async () => {
    const m2 = {
      model_version: 'm2-hashlr-1',
      test_set: {
        n: 180,
        n_adversarial: 30,
        note: 'escrito aparte de los generadores; nunca usado para entrenar ni para elegir umbrales',
        systems: {
          m2: {
            overall: { macro_f1: 0.912, accuracy: 0.93, n: 180 },
            by_language: { es: { macro_f1: 0.93, n: 108 }, pt: { macro_f1: 0.89, n: 65 } },
            calibration: { ece_raw: 0.036, ece_calibrated: 0.035 },
          },
          b1_keywords: { overall: { macro_f1: 0.61 }, by_language: { es: { macro_f1: 0.64 } } },
          b2_zero_shot: { status: 'not_run' },
        },
        bootstrap: {
          m2: { macro_f1_ci95: [0.88, 0.95] },
          b1_keywords: { macro_f1_ci95: [0.53, 0.68] },
          difference: { macro_f1_ci95: [0.25, 0.37] },
        },
      },
      thresholds: { accept_threshold: 0.9, ask_threshold: 0.45, temperature: 1.3 },
    }
    const report = evaluationReport()
    renderEvaluation(fakeApi(async () => evaluationResponse({ ...report, models: { ...report.models!, m2 } })))
    const table = await screen.findByRole('table', { name: 'Macro-F1 en 180 mensajes de prueba: M2 frente a palabras clave' })
    expect(screen.getByText('versión m2-hashlr-1 · umbrales: aceptar con p ≥ 0,9, preguntar con p ≥ 0,45')).toBeTruthy()
    expect(within(row(table, 'Global')).getByText('0,912')).toBeTruthy()
    expect(within(row(table, 'Global')).getByText('IC 95 %: 0,88 – 0,95')).toBeTruthy()
    expect(within(row(table, 'Global')).getByText('0,61')).toBeTruthy()
    expect(within(row(table, 'Global')).getByText('IC 95 %: 0,53 – 0,68')).toBeTruthy()
    expect(screen.getByText('Diferencia de macro-F1, M2 menos palabras clave (bootstrap pareado): IC 95 % 0,25 a 0,37.')).toBeTruthy()
    expect(screen.getByText('Mensajes adversarios en el conjunto de prueba: 30.')).toBeTruthy()
    expect(screen.getByText(/^escrito aparte de los generadores/)).toBeTruthy()
    expect(within(row(table, 'Español')).getByText('0,64')).toBeTruthy()
    expect(within(row(table, 'Portugués')).getByText('0,89')).toBeTruthy()
    expect(within(row(table, 'Portugués')).getByText('sin dato')).toBeTruthy()
  })
})

describe('route and links', () => {
  it('#/evaluacion opens the evaluation (lazy view) and the judge landing links to it', async () => {
    const api = fakeApi(async () => evaluationResponse())
    window.location.hash = '#/evaluacion'
    render(
      <ApiContext.Provider value={api}>
        <I18nProvider>
          <App />
        </I18nProvider>
      </ApiContext.Provider>,
    )
    expect(await screen.findByRole('heading', { level: 1, name: 'Evaluación' }, { timeout: 5000 })).toBeTruthy()
    await waitFor(() => expect(api.getEvaluation).toHaveBeenCalledTimes(1))
    await screen.findByRole('table', { name: /v2 frente a B1/ })
  })

  it('the landing link is a real link, no longer "disponible pronto"', async () => {
    render(
      <ApiContext.Provider value={fakeApi(async () => evaluationResponse())}>
        <I18nProvider>
          <JudgeLanding />
        </I18nProvider>
      </ApiContext.Provider>,
    )
    await screen.findByText('API disponible · etapa local')
    const link = screen.getByRole('link', { name: 'Evaluación' })
    expect(link.getAttribute('href')).toBe('#/evaluacion')
    expect(screen.getByRole('navigation', { name: 'Otras vistas' }).textContent).not.toMatch(/disponible/i)
  })
})

describe('defensive readers', () => {
  it('M1: model and rule side by side, with the intervals the report has', () => {
    const s = m1Summary(evaluationReport().models!.m1)!
    expect(s.version).toBe('m1-lgbm-1')
    expect(s.primary).toBe('model')
    expect([s.confirmThreshold, s.chooseThreshold, s.queries]).toEqual([0.95, 0.15, 8000])
    expect(s.rows.map((r) => r.metric)).toEqual(['top1', 'recall_at_3', 'wrong_confirmation_rate', 'confirm_rate', 'no_match_detection'])
    expect(s.rows[0]).toEqual({ metric: 'top1', model: { value: 0.7552, ci: [0.7453, 0.7654] }, rule: { value: 0.7109, ci: null } })
    expect(m1Summary(null)).toBeNull()
    expect(m1Summary('m1')).toBeNull()
    expect(m1Summary({ m1: { top1: 'high' } })!.rows[0].model.value).toBeNull()
  })

  it.each([
    [
      'today’s report (m2-hashlr-1): test_set.systems',
      {
        model_version: 'm2-hashlr-1',
        test_set: {
          n: 180,
          by_language: { es: 108, pt: 65 },
          systems: {
            m2: { overall: { macro_f1: 0.9355 }, by_language: { es: { macro_f1: 0.9368 }, pt: { macro_f1: 0.9271 } } },
            b1_keywords: { overall: { macro_f1: 0.4953 }, by_language: { es: { macro_f1: 0.435 }, pt: { macro_f1: 0.5767 } } },
            b2_zero_shot: { status: 'not_run' },
          },
        },
        cross_generator: { A_to_B: { macro_f1: 0.8783 } },
      },
      [[0.9355, 0.4953], [0.9368, 0.435], [0.9271, 0.5767]],
    ],
    [
      'one block per system',
      { metrics: { overall: { macro_f1: 0.9 }, by_language: { es: { macro_f1: 0.91 }, pt: { macro_f1: 0.88 } } },
        baseline_keywords: { overall: { macro_f1: 0.6 }, by_language: { es: { macro_f1: 0.62 }, pt: { macro_f1: 0.55 } } } },
      [[0.9, 0.6], [0.91, 0.62], [0.88, 0.55]],
    ],
    [
      'test results per system',
      { test: { m2: { overall: { macro_f1: 0.8 }, by_language: { pt: { macro_f1: 0.7 } } }, keywords: { macro_f1: 0.5 } } },
      [[0.8, 0.5], [null, null], [0.7, null]],
    ],
    [
      'by language first',
      { metrics: { macro_f1: 0.85 }, by_language: { es: { m2: { macro_f1: 0.87 }, keywords: { macro_f1: 0.4 } } } },
      [[0.85, null], [0.87, 0.4], [null, null]],
    ],
  ])('M2 macro-F1: %s', (_name, m2, expected) => {
    const s = m2Summary(m2)!
    expect(s.hasMetrics).toBe(true)
    expect(s.rows.map((r) => [r.cell.model, r.cell.keywords])).toEqual(expected)
  })

  it('M2: test-set size, note, thresholds and the bootstrap intervals of the compact summary', () => {
    const s = m2Summary({
      model_version: 'm2-hashlr-1',
      test_set: {
        n: 180,
        n_adversarial: 30,
        note: 'nota',
        systems: { m2: { overall: { macro_f1: 0.93 } } },
        bootstrap: { m2: { macro_f1_ci95: [0.9, 0.96] }, b1_keywords: { macro_f1_ci95: null }, difference: { macro_f1_ci95: [0.38, 0.49] } },
      },
      thresholds: { accept_threshold: 0.9, ask_threshold: 0.45 },
    })!
    expect([s.n, s.nAdversarial, s.note, s.acceptThreshold, s.askThreshold]).toEqual([180, 30, 'nota', 0.9, 0.45])
    expect(s.ci).toEqual({ model: [0.9, 0.96], keywords: null, difference: [0.38, 0.49] })
  })

  it('M2: validation numbers are never shown as test numbers; anything else is pending', () => {
    const s = m2Summary({ model_version: 'm2-x', metrics: { validation_cross_fitted: { macro_f1: 0.95 }, cross_generator_macro_f1: { A_to_B: 0.8 } } })!
    expect(s.version).toBe('m2-x')
    expect(s.hasMetrics).toBe(false)
    expect(m2Summary(null)).toBeNull()
    expect(m2Summary(['not', 'an', 'object'])).toBeNull()
    expect(m2Summary({ metrics: { overall: { macro_f1: Number.NaN } } })!.hasMetrics).toBe(false)
  })

  it('M3: lift, PR-AUC and the decision', () => {
    expect(m3Summary(evaluationReport().models!.m3)).toEqual({
      version: 'm3-evidence-1',
      liftSignificant: false,
      prAucModel: 0.0023,
      prAucFraudScore: 0.00268,
      diffCi: [-0.00515, 0.00238],
      decision: 'evidence_only',
    })
    expect(m3Summary({ pr_auc_diff_ci95: [1] })!.diffCi).toBeNull()
  })

  it('percentages keep two decimals only below 10 %', () => {
    expect(formatPercent(0.4158, 'es')).toMatch(/^41,6\s%$/)
    expect(formatPercent(0.0035, 'es')).toMatch(/^0,35\s%$/)
    expect(formatPercent(0.0288, 'es')).toMatch(/^2,88\s%$/)
    expect(formatPercent(1, 'es')).toMatch(/^100\s%$/)
    expect(formatPercent(0, 'pt-BR')).toBe('0%')
  })
})
