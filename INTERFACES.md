# Interfaces

Edit this file in place to record the current state of each interface boundary. Do not append historical versions. This file is intentionally not configured with the union merge driver: union would keep both the old and new value of an interface, leaving contradictory claims in one file. A merge conflict here is a genuine signal that two people are editing the same boundary and should stop to reconcile it.

Every boundary below is **Proposed** (drafted from plan v2). The owner confirms or changes it at the Mon 28 Sep stand-up and flips the status to **Agreed**. Code on both sides of a boundary is written against this file, never against the other side's internals. Field names are snake_case; IDs are opaque strings; money is a decimal string plus an ISO 4217 currency; timestamps are ISO 8601 UTC.

## 1. Chat API

- **Owner:** arturo (front end consumes it)
- **Status:** Proposed
- **Current shape:**
  - `POST /session` `{customer_id, channel: "app"|"whatsapp"|"web", language: "es"|"pt"}` → `{session_token, expires_at}`. In judge mode `customer_id` comes from the prepared-customer picker; session lifetime is 15 minutes.
  - `POST /chat` `{session_token, message, client_msg_id}` → `{reply_text, buttons: [{id, label}], case_card: <case record, customer-visible fields only> | null, lane: "A"|"B"|"C"|null, trace_id}`
  - `GET /cases/{case_id}` with the session token → customer-visible case fields and status.
  - Errors: `{error: {code, message}}` with stable codes: `session_expired`, `not_authorized`, `tool_unavailable`, `model_timeout`, `invalid_request`. Clients branch on `code`, never on `message`.
- **Last changed:** 2026-09-28T03:30Z

## 2. Gateway tools

- **Owner:** andres (used by arturo's orchestrator, cristhian's models, the investigator)
- **Status:** Proposed
- **Current shape:** Python package `src/gateway/`. Every function takes `ctx: GatewayContext` as its first argument; `ctx.customer_id` comes from the verified session, **never** from message text or a function argument. Functions only return records owned by `ctx.customer_id`; anything else raises.
  - `find_candidate_txns(ctx, date_from, date_to, amount_hint=None, currency=None, limit=200) -> list[Txn]`
  - `get_transaction(ctx, txn_id) -> Txn`
  - `get_txn_history(ctx, days) -> list[Txn]`
  - `find_duplicates(ctx, txn_id) -> list[Txn]`
  - `get_reversals(ctx, txn_id) -> list[Txn]`
  - `get_fee_schedule(ctx, product_id) -> FeeRule` (synthetic table)
  - `get_customer_baseline(ctx) -> Baseline`
  - `get_risk_evidence(ctx, txn_id) -> RiskEvidence` (calls M3)
  - `get_digital_sessions(ctx, around_ts, minutes) -> list[Session]`
  - `get_prior_contacts(ctx) -> list[Contact]`
  - `get_merchant_stats(ctx, merchant_id) -> MerchantStats` (aggregates only)
  - `get_cards(ctx) -> list[Card]`
  - `block_card(ctx, card_id, confirmed_by_customer: bool) -> CardStatus` (simulated core; refuses unless `confirmed_by_customer` is true; returns the read-back status)
  - `Txn`: `{txn_id, product_id, card_last4, local_date, local_time, amount, currency, amount_usd, merchant_id, merchant_name, merchant_category, status, response_code, fraud_score}`
  - Errors: `NotOwned`, `NotFound`, `ToolUnavailable` (retry at most twice with backoff, then the caller takes the safe path).
  - Every call is written to the trace (interface 7) with arguments and result IDs.
  - Backends: `duckdb` (local, reads gold tables) and `dynamodb` (cloud, andres). Selected by config, invisible to callers.
- **Last changed:** 2026-09-28T03:30Z

## 3. Case record

- **Owner:** arturo, with andres for storage (front end and evaluation read it)
- **Status:** Proposed
- **Current shape:** one document per case; base fields from v1.4 appendix A plus v2 additions (plan v2, appendix A).
  - Base: `case_id, created_at, channel, language, customer_id, segment, country, subcategory, intent_confidence, urgency_flags[], evidence {transaction, product, app_error}, customer_statement, lane, lane_reason, promise {expected_date, sla_days, p90_days}, actions[{tool, args, result, verified_at}], handoff {queue, priority, open_questions[], facts_verified[]}, clock {assigned_by, first_response_by, sla_alert_at, breach_at}, status, trace_id`
  - v2: `match {candidates[{txn_id, p}], chosen_id, p_top1, band, model_version}`, `intent {class, p, gate_action, model_version}`, `risk_evidence {features, fraud_score, rule_fired}`, `investigation_id`, `analyst_decision {action, edited, reason, decided_at}`, `labels_emitted[]`
  - `status`: `open` → `investigating` → `awaiting_analyst` → `notified` → `closed` | `reopened`
  - Customer-visible subset is defined by the owner in code (one function), not by each screen.
- **Last changed:** 2026-09-28T03:30Z

## 4. Model inputs and outputs (M1, M2, M3)

- **Owner:** cristhian (arturo's orchestrator and the gateway call these)
- **Status:** Proposed
- **Current shape:** Python package `src/models/`, pure functions, no network calls, loaded once per process.
  - M1 `rank_candidates(slots, candidates: list[Txn]) -> {ranked: [{txn_id, p}], band: "confirm"|"choose"|"ask", model_version}`
    - `slots`: `{amount, currency, date, date_is_relative, merchant_text, card_last4}`; any field may be null.
  - M2 `classify(text, language) -> {intent_class, p, gate_action: "accept"|"ask"|"human", runner_up, model_version}`
    - classes: `dispute_charge, dispute_fee, complaint_other, case_status, account_query, lost_card, human_request, out_of_scope, manipulation`
  - M3 `risk_features(txn: Txn, baseline: Baseline) -> {features: {name: value}, lift_score: float|null, model_version}`
  - Thresholds (`band`, `gate_action`) are read from `config/thresholds.yaml`, not hard-coded.
  - Artifacts: `models/<m1|m2|m3>/<version>/`; the version string is returned by every call and stored in the case.
- **Last changed:** 2026-09-28T03:30Z

## 5. Gold and serving tables

- **Owner:** diego (everyone reads them)
- **Status:** Proposed
- **Current shape:** produced by the pipeline from `data/processed/latam_bank.duckdb`; written as Parquet under `data/gold/<table>/` locally. Table list from plan v2 section 8: `customer_profile, customer_products, transactions_enriched, customer_baseline, merchant_stats, complaints_baseline, fee_schedule, contact_history, labels`.
  - Column-level schemas live in `docs/data/gold_tables.md` (diego writes it Monday; this entry then links the agreed version).
  - Pipeline functions take one partition at a time (`run_partition(table, date)`) so andres can run the same code in Lambda.
  - No document number, address or phone in any gold table read by models.
- **Last changed:** 2026-09-28T03:30Z

## 6. Investigator report

- **Owner:** andres (analyst console and evaluation read it)
- **Status:** Proposed
- **Current shape:** `{report_id, case_id, created_at, findings: [{claim, evidence_ids[]}], hypotheses: [{name: "fraud"|"forgotten_purchase"|"unfamiliar_merchant_name"|"duplicate"|"fee_error"|"pending_reversal", p}], recommendation: "reverse_fee"|"open_chargeback"|"block_card"|"explain_and_close"|"request_information"|"escalate", confidence, draft_reply {language, text}, open_questions[], citations_valid: bool, removed_claims: int, tool_calls: int, latency_ms, model_id, prompt_version}`
  - Every `evidence_id` must appear in this run's tool results; the check runs in code before the report is stored.
- **Last changed:** 2026-09-28T03:30Z

## 7. Trace event

- **Owner:** andres, with diego for storage and queries (trace view and evaluation page read it)
- **Status:** Proposed
- **Current shape:** one JSON line per step: `{trace_id, session_id, case_id, turn, step, actor: "rule"|"model"|"tool"|"human", name, input_ref, output_ref, latency_ms, tokens_in, tokens_out, cost_usd, version, error_code, ts}`
  - Locally written to `data/traces/*.jsonl`; in the cloud to S3 and queried with Athena.
  - No free-text PII in traces; message text is stored by reference.
- **Last changed:** 2026-09-28T03:30Z

## 8. LLM client

- **Owner:** andres (used by arturo for G1, andres for G2, cristhian for G3 and baselines)
- **Status:** Proposed
- **Current shape:** Python package `src/llm/`. `complete(prompt_id, variables, output_schema=None, model_role="chat"|"investigator"|"judge") -> {output, usage: {tokens_in, tokens_out, cost_usd}, model_id, prompt_version}`
  - Prompts live in `prompts/<prompt_id>/<version>.md`; callers pass variables, never raw prompt strings.
  - `model_role` maps to a model ID in config (chat → Haiku 4.5, investigator → Sonnet 5, judge → Opus 5). No caller hard-codes a model ID.
  - Providers: `mock` (default locally; returns fixtures from `tests/fixtures/llm/`, deterministic) and `bedrock` (cloud, andres). Tool use for the investigator goes through the same package.
  - Timeout 8 s for `chat`; callers handle `model_timeout` with templates.
- **Last changed:** 2026-09-28T03:30Z
