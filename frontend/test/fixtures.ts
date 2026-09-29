// SYNTHETIC API payloads for tests, shaped by src/api/types.ts. No real customer data.
import type {
  AnalystCaseDetail,
  AnalystCaseListItem,
  AnalystCaseListResponse,
  ChatResponse,
  CustomerCaseView,
  DecisionRequest,
  DecisionResponse,
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
