// SYNTHETIC API payloads for tests, shaped by src/api/types.ts. No real customer data.
import type {
  AnalystCaseDetail,
  AnalystCaseListItem,
  AnalystCaseListResponse,
  ChatResponse,
  CustomerCaseView,
  DecisionRequest,
  DecisionResponse,
  EvaluationReport,
  EvaluationResponse,
  InvestigatorReport,
  SessionResponse,
} from '../src/api/types'

export function sessionResponse(overrides: Partial<SessionResponse> = {}): SessionResponse {
  return {
    session_token: 'tok-synthetic-1',
    expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    customer: { display_name: 'Lucía', language: 'es', locale: 'es-MX', country: 'MX' },
    welcome: {
      turn: 0,
      reply_text: 'Hola Lucía, ¿en qué te ayudo? (sintético)',
      reply_language: 'es',
      reply_source: 'template',
      buttons: [
        { id: 'start:n1', label: 'Un cargo que no reconozco', kind: 'choice' },
        { id: 'start:n2', label: 'Hablar con una persona', kind: 'handoff' },
      ],
      input_mode: 'free_text',
    },
    synthetic: true,
    ...overrides,
  }
}

export function caseView(overrides: Partial<CustomerCaseView> = {}): CustomerCaseView {
  return {
    case_id: 'EV-1A2B3C4D',
    created_at: '2026-09-29T15:00:00Z',
    status: 'investigating',
    lane: 'B',
    lane_reason_code: 'unrecognized_low_risk',
    subcategory: null,
    language: 'es',
    customer_statement: 'como 450 en el súper el 12',
    charge: {
      local_date: '2026-06-12',
      amount: '449.90',
      currency: 'MXN',
      merchant_name: 'SUPER AHORRO SA',
      card_last4: '4821',
      status: 'posted',
    },
    expected_date: '2026-10-09',
    first_response_by: '2026-09-30T15:00:00Z',
    handoff_queue: null,
    outcome: null,
    resolution: null,
    lifecycle_step: 'investigating',
    notices: [],
    synthetic: true,
    ...overrides,
  }
}

export function chatResponse(overrides: Partial<ChatResponse> = {}): ChatResponse {
  return {
    turn: 1,
    reply_text: '¿Es este el cargo? SUPER AHORRO SA, 449.90 MXN, 12 jun. (sintético)',
    reply_language: 'es',
    reply_source: 'template',
    buttons: [
      { id: 'confirm:n3', label: 'Es este', kind: 'confirm' },
      { id: 'deny:n4', label: 'Ninguno de estos', kind: 'deny' },
    ],
    input_mode: 'buttons_only',
    progress: {
      step: 'verify',
      complaint_type: 'unrecognized_charge',
      claimed: { amount: '450', currency: 'MXN', date: '2026-06-12', merchant_text: 'el súper', card_last4: null },
      missing: ['charge_confirmed'],
    },
    case_card: null,
    lane: null,
    degraded: [],
    trace_id: 'tr-0001',
    trace_summary: {
      steps: [
        { actor: 'model', name: 'extract', latency_ms: 812, version: 'g1-extract@v1', error_code: null },
        { actor: 'tool', name: 'find_candidate_txns', latency_ms: 41, version: null, error_code: null },
        { actor: 'model', name: 'rank_candidates', latency_ms: 12, version: 'm1-rule@0', error_code: null },
      ],
      latency_ms: 901,
      cost_usd: 0.0012,
      model_id: 'mock-chat',
      tokens_in: 1341,
      tokens_out: 49,
      rule_id: null,
      rules_version: '2026-09-28.1',
    },
    poll_after_ms: null,
    demo_switches: { tools_down: false, model_slow: false, fast_clock: false },
    synthetic: true,
    ...overrides,
  }
}

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

// ---------- Analyst console (SYNTHETIC) ----------

export function listItem(overrides: Partial<AnalystCaseListItem> = {}): AnalystCaseListItem {
  return {
    case_id: 'EV-1A2B3C4D',
    updated_at: '2026-09-30T15:00:04Z',
    status: 'awaiting_analyst',
    lane: 'B',
    lane_rule_id: 'unrecognized_low_risk',
    queue: 'disputes',
    priority: 'normal',
    language: 'es',
    country: 'MX',
    display_name: 'Lucía',
    intent_class: 'dispute_charge',
    intent_p: 0.91,
    subcategory: 'unrecognized_charge',
    breach_at: new Date(Date.now() + 3 * 86_400_000).toISOString(),
    sla_alerts: [],
    has_report: true,
    report_reliable: true,
    ...overrides,
  }
}

export function listResponse(items: AnalystCaseListItem[], overrides: Partial<AnalystCaseListResponse> = {}): AnalystCaseListResponse {
  return { items, next_cursor: null, as_of: new Date().toISOString(), poll_after_ms: 15_000, ...overrides }
}

export function report(overrides: Partial<InvestigatorReport> = {}): InvestigatorReport {
  return {
    report_id: 'RPT-0001',
    case_id: 'EV-1A2B3C4D',
    created_at: '2026-09-30T15:00:04Z',
    findings: [
      { claim: 'El cargo de 449.90 MXN en SUPER AHORRO SA está aplicado (sintético).', evidence_ids: ['TX-DEMO-LU-01'] },
      { claim: 'La clienta no tiene compras previas en ese comercio (sintético).', evidence_ids: ['BASELINE', 'MERCHANT:M-77'] },
    ],
    hypotheses: [
      { name: 'unfamiliar_merchant_name', p: 0.46 },
      { name: 'fraud', p: 0.22 },
      { name: 'forgotten_purchase', p: 0.18 },
      { name: 'duplicate', p: 0.06 },
      { name: 'pending_reversal', p: 0.05 },
      { name: 'fee_error', p: 0.03 },
    ],
    recommendation: 'open_chargeback',
    confidence: 0.62,
    draft_reply: {
      language: 'es',
      text: 'Hola Lucía, revisamos el cargo de SUPER AHORRO SA del 12 de junio. Abrimos una disputa y te avisaremos el resultado. (sintético)',
    },
    open_questions: ['¿La tarjeta estuvo en poder de la clienta ese día?'],
    citations_valid: true,
    removed_claims: 0,
    tool_calls: 5,
    latency_ms: 2140,
    model_id: 'stub-g2-deterministic',
    prompt_version: 'stub-provisional',
    evidence_records: {
      'TX-DEMO-LU-01': {
        kind: 'transaction',
        summary: '2026-06-12 18:42 · SUPER AHORRO SA · 449.90 MXN',
        fields: { txn_id: 'TX-DEMO-LU-01', amount: '449.90', currency: 'MXN', status: 'posted' },
      },
      BASELINE: { kind: 'customer_baseline', summary: 'Gasto mensual típico 3200 MXN', fields: { monthly_spend: '3200.00' } },
      'MERCHANT:M-77': { kind: 'merchant_stats', summary: 'SUPER AHORRO SA · 0 compras previas', fields: { prior_purchases: 0 } },
    },
    ...overrides,
  }
}

export function analystDetail(
  overrides: { case?: Partial<AnalystCaseDetail['case']>; report?: InvestigatorReport | null } & Partial<
    Omit<AnalystCaseDetail, 'case' | 'report'>
  > = {},
): AnalystCaseDetail {
  const { case: caseOver, report: reportOver, ...rest } = overrides
  return {
    case: {
      case_id: 'EV-1A2B3C4D',
      created_at: '2026-09-30T15:00:00Z',
      updated_at: '2026-09-30T15:00:04Z',
      channel: 'web',
      language: 'es',
      segment: 'mass',
      country: 'MX',
      subcategory: 'unrecognized_charge',
      intent_confidence: 0.91,
      urgency_flags: [],
      evidence: {
        transaction: {
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
          status: 'posted',
          response_code: '00',
          fraud_score: 12,
          txn_type: 'purchase',
          reversal_of: null,
        },
        product: null,
        app_error: null,
      },
      customer_statement: 'como 450 en el súper el 12, no lo reconozco',
      lane: 'B',
      lane_reason: 'unrecognized_low_risk',
      rules_version: '2026-09-29.2',
      promise: { expected_date: '2026-10-09', sla_days: 10, p90_days: 12 },
      actions: [
        { tool: 'verify_charge', args: { txn_id: 'TX-DEMO-LU-01' }, result: { ok: true }, verified_at: '2026-09-30T14:59:50Z' },
      ],
      handoff: { queue: null, priority: null, open_questions: [], facts_verified: ['Cargo confirmado por la clienta'] },
      clock: {
        assigned_by: null,
        first_response_by: '2026-10-01T15:00:00Z',
        sla_alert_at: null,
        breach_at: new Date(Date.now() + 3 * 86_400_000).toISOString(),
      },
      clock_events: [],
      status: 'awaiting_analyst',
      trace_id: 'tr-0009',
      match: {
        candidates: [{ txn_id: 'TX-DEMO-LU-01', p: 0.93 }],
        chosen_id: 'TX-DEMO-LU-01',
        p_top1: 0.93,
        band: 'confirm',
        model_version: 'm1-rule@0',
      },
      intent: { class: 'dispute_charge', p: 0.91, gate_action: 'accept', model_version: 'm2-rule@0' },
      risk_evidence: { features: { night_txn: false }, fraud_score: 12, rule_fired: null },
      investigation_id: 'RPT-0001',
      analyst_decision: null,
      labels_emitted: [],
      resolution: null,
      lifecycle_step: 'in_review',
      conversation: [],
      synthetic: true,
      ...caseOver,
    },
    report: reportOver === undefined ? report() : reportOver,
    context: {
      profile: { display_name: 'Lucía', country: 'MX', segment: 'mass', language: 'es' },
      cards: [{ card_last4: '4821', status: 'active' }],
      evidence_txns: [],
      prior_contacts: [],
      risk_evidence: null,
      unavailable: [],
    },
    allowed_actions: ['approve', 'edit', 'reject'],
    version: 3,
    ...rest,
  }
}

export function decisionResponse(req: DecisionRequest, overrides: Partial<DecisionResponse> = {}): DecisionResponse {
  return {
    case_id: 'EV-1A2B3C4D',
    status: req.action === 'reject' && req.next === 'escalate' ? 'handed_off' : 'notified',
    analyst_decision: {
      action: req.action,
      edited: req.action === 'edit',
      reason: req.reason ?? null,
      decided_by: 'local-analyst',
      decided_at: '2026-09-30T15:10:00Z',
    },
    labels_emitted: [`decision:${req.action}`],
    ...overrides,
  }
}

// ---------- Evaluation report (GET /evaluation), SYNTHETIC, shaped like src/eval/report.py ----------

const rate = (value: number, n: number, k: number, lo: number, hi: number) => ({ value, n, k, ci95: [lo, hi] })

export function evaluationReport(overrides: Partial<EvaluationReport> = {}): EvaluationReport {
  return {
    version: 'eval-2026-10-29.1',
    generated_at: '2026-10-30T04:37:02Z',
    data_source: 'synthetic_replica',
    synthetic: true,
    gold_snapshot: '2026-06-17',
    seconds: 34.6,
    scenarios: {
      n: 240,
      adversarial: 36,
      by_kind: { fee_matches: 12, injection: 24 },
      by_language: { es: 115, pt: 125 },
      by_country: { AR: 54, CO: 79, MX: 107 },
      seed: 20261029,
    },
    configs: [
      { id: 'v2', label: 'v2: M1 + M2 + G1 + G2', env: { M1_BACKEND: 'model' } },
      { id: 'b1_rules_v14', label: 'B1: v1.4 solo reglas (sin investigador)', env: { M1_BACKEND: 'rule' } },
    ],
    headline: [
      {
        metric: 'safe_automated_resolution',
        label: 'Resolución automática segura',
        unit: 'ratio',
        target: '>= 30% con 0 cierres incorrectos',
        v2: rate(0.4158, 190, 79, 0.3474, 0.4789),
        b1: rate(0.4053, 190, 77, 0.3367, 0.4684),
        pass: true,
      },
      {
        metric: 'unnecessary_handoffs',
        label: 'Derivaciones innecesarias',
        unit: 'ratio',
        target: '<= 10%',
        v2: rate(0.12, 190, 23, 0.08, 0.17),
        b1: rate(0.0105, 190, 2, 0, 0.0263),
        pass: false,
      },
      {
        metric: 'containment',
        label: 'Contención (cerrado sin humano)',
        unit: 'ratio',
        target: 'comparado con B1',
        v2: rate(0.4158, 190, 79, 0.3474, 0.4789),
        b1: null,
        pass: null,
      },
      {
        metric: 'investigator_unsupported_claims',
        label: 'Investigador: reportes con afirmaciones sin respaldo quitadas',
        unit: 'ratio',
        target: '—',
        v2: rate(0.0317, 63, 2, 0, 0.0794),
        b1: rate(0, 63, 0, 0, 0),
        pass: null,
      },
      {
        metric: 'brand_new_metric',
        label: 'Métrica nueva del generador',
        unit: 'ratio',
        target: '—',
        v2: { value: null, n: null, k: null, ci95: null },
        b1: null,
        pass: null,
      },
    ],
    latency: {
      turn_p50_ms: 17.4,
      turn_p95_ms: 72.4,
      turns: 639,
      investigator_p50_ms: 56,
      investigator_p95_ms: 77.9,
      investigations: 63,
      target: 'turno p95 < 6 s; investigador p95 < 90 s',
      note: 'medido en local, proceso único, sin red ni modelo real (G1 mock, G2 política determinista)',
    },
    cost: {
      per_conversation_usd: 0,
      target: '< 0.05 USD por conversación',
      note: 'medido con el proveedor mock: no hay llamadas reales',
      projected: {
        chat_model: 'claude-chat-synthetic',
        investigator_model: 'claude-investigator-synthetic',
        chat_per_conversation_usd: 0.0041,
        investigator_per_case_usd: 0.0312,
        share_of_conversations_investigated: 0.263,
        total_per_conversation_usd: 0.0123,
        note: 'PROYECCIÓN con precios de lista y tamaños de prompt medidos; sin prompt caching (cota alta)',
      },
    },
    fairness: {
      by_language: {
        es: {
          n: 115,
          lane_accuracy: 1,
          charge_accuracy: 1,
          safe_automated_resolution: 0.4583,
          auto_resolution_when_expected: 0.9483,
          expected_a: 58,
          unsafe_wrong_charge_filed: 0,
        },
        pt: {
          n: 125,
          lane_accuracy: 1,
          charge_accuracy: null,
          safe_automated_resolution: 0.3723,
          auto_resolution_when_expected: 0.9348,
          expected_a: 46,
          unsafe_wrong_charge_filed: 0,
        },
      },
      by_country: {
        AR: {
          n: 54,
          lane_accuracy: 1,
          charge_accuracy: 1,
          safe_automated_resolution: 0.5333,
          auto_resolution_when_expected: null,
          expected_a: null,
          unsafe_wrong_charge_filed: 0,
        },
        MX: {
          n: 107,
          lane_accuracy: 1,
          charge_accuracy: 1,
          safe_automated_resolution: 0.3864,
          auto_resolution_when_expected: 0.94,
          expected_a: 50,
          unsafe_wrong_charge_filed: 0,
        },
      },
      target: 'brechas <= 5 puntos o explicadas',
      gaps: [
        {
          dimension: 'idioma',
          metric: 'resolución automática segura',
          gap_points: 8.6,
          max: 'es',
          min: 'pt',
          within_limit: false,
          explanation: 'mezcla de escenarios: la parte de casos que deben resolverse en ruta A cambia por grupo',
        },
        { dimension: 'idioma', metric: 'ruta correcta', gap_points: 0, max: 'es', min: 'pt', within_limit: true, explanation: null },
        { dimension: 'país', metric: 'métrica futura', gap_points: 3.2, max: 'MX', min: 'CO', within_limit: true, explanation: null },
      ],
    },
    by_kind: { v2: { fee_matches: { n: 12, expected_lane: 'A', lanes: { A: 12 } } } },
    security: {
      v2: {
        tool_scope: { n: 204, leaks: 0 },
        expired_session: { n: 3, data_shown: 0 },
        other_session_case: { n: 1, leaks: 0 },
        tool_failure: { lane: 'C', degraded: ['tool_unavailable'], safe: true },
      },
      b1: {
        tool_scope: { n: 204, leaks: 2 },
        expired_session: { n: 0, data_shown: 0 },
        other_session_case: null,
        tool_failure: { lane: 'A', degraded: [], safe: false },
      },
    },
    models: {
      m1: {
        model_version: 'm1-lgbm-1',
        primary: 'model',
        thresholds: { confirm_threshold: 0.95, choose_threshold: 0.15 },
        queries_test: 8000,
        m1: {
          top1: 0.7552,
          top1_ci95: [0.7453, 0.7654],
          recall_at_3: 0.9709,
          wrong_confirmation_rate: 0.0035,
          wrong_confirmation_ci95: [0.0022, 0.0049],
          confirm_rate: 0.2389,
          no_match_detection: 0.9663,
        },
        rule: { top1: 0.7109, recall_at_3: 0.9577, wrong_confirmation_rate: 0.0288, confirm_rate: 0.3563, no_match_detection: 0.7976 },
      },
      m2: null,
      m3: {
        model_version: 'm3-evidence-1',
        lift_significant: false,
        decision: 'evidence_only',
        test: { pr_auc_model: 0.0023, pr_auc_fraud_score: 0.00268 },
        pr_auc_diff_ci95: [-0.00515, 0.00238],
      },
      g1: { status: 'mock', note: 'LLM_PROVIDER=mock: fixtures solo para los mensajes del modo juez.' },
      g2: { status: 'policy', note: 'Sin credenciales de modelo, el investigador corre con la política determinista.' },
      g3: { status: 'not_run', note: 'El juez offline necesita un modelo.' },
      b2_zero_shot: { status: 'not_run', note: 'Requiere un modelo (LLM_PROVIDER=anthropic|bedrock).' },
    },
    baseline_b0: [
      { metric: 'median_resolution_days', value: 15, unit: 'days', n: 431, source: 'complaints', description: 'Mediana de días hasta resolver un reclamo' },
      { metric: 'fraud_rule_precision', value: 1, unit: 'ratio', n: 260, source: 'transactions', description: 'Precisión de la regla fraud_score > 30' },
      { metric: 'complaint_contact_csat', value: 2.4288, unit: 'score_1_5', n: 534, source: 'satisfaction_surveys', description: 'CSAT medio de los contactos por queja' },
    ],
    limits: [
      'Datos: réplica sintética del dataset (los organizadores no dieron acceso en esta máquina).',
      'El portugués es generado, sin revisión nativa.',
    ],
    row_counts: { v2: 240, b1: 240 },
    ...overrides,
  }
}

export function evaluationResponse(report: EvaluationReport | null = evaluationReport()): EvaluationResponse {
  return { available: report !== null, report, synthetic: true }
}
