# Interfaces

Edit this file in place to record the current state of each interface boundary. Do not append historical versions. This file is intentionally not configured with the union merge driver: union would keep both the old and new value of an interface, leaving contradictory claims in one file. A merge conflict here is a genuine signal that two people are editing the same boundary and should stop to reconcile it.

Every boundary below is **Proposed** (drafted from plan v2). The owner confirms or changes it at the Mon 28 Sep stand-up and flips the status to **Agreed**. Code on both sides of a boundary is written against this file, never against the other side's internals. Field names are snake_case; IDs are opaque strings; money is a decimal string plus an ISO 4217 currency; timestamps are ISO 8601 UTC.

## 1. Chat API

- **Owner:** arturo (front end consumes it)
- **Status:** Agreed (implemented and tested locally; not deployed yet)
- **Current shape:** field-level types are `frontend/src/api/types.ts`, mirrored by `src/conversation/contract.py`; `tests/test_contract.py` fails on any difference. Behaviour and examples: `docs/interfaz_chat_requerimientos.md`.
  - Transport: routes below, served under `/api/*` by CloudFront (prefix stripped). JSON UTF-8, body at most 16 KB, every response carries `Cache-Control: no-store`.
  - Customer session: the app's own opaque token (`secrets.token_urlsafe(32)`; only its sha256 is stored), lifetime `SESSION_TTL_MINUTES` (15). Sent as `Authorization: Bearer <token>`; `x-ev-session: <token>` is accepted as a fallback in case CloudFront drops `Authorization` on GET. Never in the body or the URL. At most 30 turns per session.
  - `GET /health` → `{status: "ok", stage, deps: "ok"|"missing", demo_clock_scale}`. Needs no dependency; `deps: "missing"` means pydantic/PyYAML are not in the Lambda package.
  - `POST /session` `{demo_key: "lucia"|"sofia"|"andres"|"joao"|"martina"|"carlos", channel: "web"|"app"|"whatsapp", language?: "es"|"pt"}` → `{session_token, expires_at, customer: {display_name, language, locale, country}, welcome: ChatTurn, synthetic: true}`. The server maps `demo_key` to a customer from its own allow-list; a body with `customer_id` or any unknown field is 400. At most `SESSION_RATE_PER_MINUTE` (60) per source IP, then 429 `rate_limited`.
  - `POST /chat` `{client_msg_id, message? (max 1000 chars) | button_id?, demo_switches?: {tools_down?, model_slow?, fast_clock?, expire_session?}}` → `{turn, reply_text, reply_language, reply_source: "template"|"model", buttons: [{id, label, kind: "confirm"|"deny"|"choice"|"handoff"}], input_mode: "free_text"|"buttons_only", progress: {step, complaint_type, claimed: {amount, currency, date, merchant_text, card_last4}, missing[]}, case_card: CustomerCaseView|null, lane: "A"|"B"|"C"|null, degraded: ("model_timeout"|"tool_unavailable")[], trace_id, trace_summary: {steps: [{actor, name, latency_ms, version, error_code}], latency_ms, cost_usd, model_id, tokens_in, tokens_out, rule_id, rules_version}, poll_after_ms, demo_switches: {tools_down, model_slow, fast_clock}, synthetic: true}`.
    - Same `client_msg_id` → the same answer and never a second case. Buttons are server-issued and single use (reuse → 409 `conflict`). A second message of the same session while one is running waits up to 1 s, then 409 `conflict` with `retryable: true`.
    - A model timeout never fails the turn: 200 with a template reply and `degraded: ["model_timeout"]`. A tool that fails twice sends the case to lane C `tool_failure` with `degraded: ["tool_unavailable"]`.
    - `demo_switches` exist only for judge-mode sessions (all sessions today); details in `docs/contrato_consola.md`.
  - `GET /cases/{case_id}` → `{case: CustomerCaseView, poll_after_ms}`. Only the session that opened the case may read it; another session's case and a missing case give the same 403 `not_authorized`. `poll_after_ms` is 5000 while a lane B case is `open`, `investigating` or `awaiting_analyst` or has SLA timers pending, else null.
  - `CustomerCaseView` (the customer-visible subset of #3, built only by `case.customer_view`): `{case_id, created_at, status, lane, lane_reason_code, subcategory, language, customer_statement, charge: {local_date, amount, currency, merchant_name, card_last4, status}|null, expected_date, first_response_by, handoff_queue, outcome, resolution: {language, text, sent_at, approved_by_human: true}|null, lifecycle_step: "open"|"investigating"|"in_review"|"notified"|"closed"|null, notices: [{kind: "sla_80"|"escalated", text, language, at}], synthetic: true}`.
  - Errors: `{error: {code, message, retryable}}`. Clients branch on HTTP status, then on `code`, never on `message`. `invalid_request` 400, `session_expired` 401, `not_authorized` 403, `conflict` 409, `session_limit` 409, `precondition` 422, `rate_limited` 429, `internal` 500, `not_implemented` 501, `tool_unavailable` 503, `model_timeout` 504 (never returned by `/chat`). `retryable` is true only for `rate_limited`, `tool_unavailable`, `model_timeout`, `internal`, and for the in-flight `conflict` above. API Gateway's own 401/404 bodies are `{"message": ...}`.
  - Implemented in https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1 (branch `arturo/m1-chat-api-front`), not merged yet.
- **Last changed:** 2026-09-29T19:54Z

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
- **Proposed change (arturo, 2026-09-29T19:54Z, waiting for the owner):** #2 (demo backend, `GatewayContext.for_analyst`, `get_customer_profile`, `txn_type`/`reversal_of`, `NotConfirmed`). Exact text in `docs/propuestas_interfaces.md` (https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1); the current shape above is unchanged until the owner accepts it.

## 3. Case record

- **Owner:** arturo, with andres for storage (front end and evaluation read it)
- **Status:** Agreed (implemented in `src/conversation/case.py`; storage protocol in `src/conversation/store.py`)
- **Current shape:** one document per case (pydantic `CaseRecord`, extra fields forbidden). `case_id` is `EV-` plus 8 characters from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`. Money is a decimal string plus ISO 4217 currency; timestamps ISO 8601 UTC; every record is synthetic.
  - Base: `case_id, created_at, channel, language, customer_id, segment, country, subcategory, intent_confidence, urgency_flags[], evidence {transaction, product, app_error}, customer_statement, lane, lane_reason, promise {expected_date, sla_days, p90_days}, actions[{tool, args, result, verified_at}], handoff {queue, priority, open_questions[], facts_verified[]}, clock {assigned_by, first_response_by, sla_alert_at, breach_at}, status, trace_id`
  - v2: `match {candidates[{txn_id, p}], chosen_id, p_top1, band, model_version}`, `intent {class, p, gate_action, model_version}`, `risk_evidence {features, fraud_score, rule_fired}`, `investigation_id` (the stored report's `report_id`), `analyst_decision {action: "approve"|"edit"|"reject", edited, reason, decided_at, decided_by, next: "request_information"|"escalate"|null, draft_text, sent_text}`, `labels_emitted[]`
  - Console: `version` (int, starts at 1, +1 on every write), `updated_at`, `resolution {language, text, sent_at, approved_by_human: true}` (the text ends with the fixed stamp "approved by a person; no money moves in this demo"), `rules_version` (version of the lane rules YAML that chose the lane).
  - `evidence.transaction` is a #2 `Txn` (with `txn_type` and `reversal_of`, see the #2 proposal). `customer_statement` is the redacted text. No document number, address or phone anywhere in the record.
  - `decided_by` is the analyst JWT `sub`, never taken from a request body.
  - `status`: `open` → `investigating` → `awaiting_analyst` → `notified` → `closed` | `reopened`, plus `resolved_in_contact` (lane A, explained with the record and accepted) and `handed_off` (lane C at creation, or lane B escalated to the senior queue by an analyst).
  - Views, each built by one function: customer → `case.customer_view` (#1 `CustomerCaseView`); analyst → `case.analyst_view` (every field except `customer_id`, plus the redacted conversation of the case, `lifecycle_step` and `synthetic: true`; #10). Internal fields that never leave the server: `awaiting_customer`, and the cloud lifecycle state (`execution_arn`, `task_token`) that andres defines.
  - Storage (`src/conversation/store.py` protocols; memory backend today, DynamoDB by andres): `CaseStore.put(case, session_id)` is a conditional write on `version` (stale → `CaseVersionConflict`, 409); `get(case_id)` returns the case plus the owning `session_id`; `all()` feeds the analyst queue (GSI instead of a scan in DynamoDB). Reports, idempotent decision replies and the case conversation live in `ReviewStore`, outside the record, so they never bump `version`.
  - Implemented in https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1 (branch `arturo/m1-chat-api-front`), not merged yet.
- **Last changed:** 2026-09-29T19:54Z

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
- **Proposed change (arturo, 2026-09-29T19:54Z, waiting for the owner):** #4 (stand-ins m1-rule-0 and m2-keywords-0 in `src/conversation/`; thresholds and artefacts must live under `src/` or be loaded from `BUCKET_ARTIFACTS`). Exact text in `docs/propuestas_interfaces.md` (https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1); the current shape above is unchanged until the owner accepts it.

## 5. Gold and serving tables

- **Owner:** diego (everyone reads them)
- **Status:** Proposed
- **Current shape:** produced by the pipeline from `data/processed/latam_bank.duckdb`; written as Parquet under `data/gold/<table>/` locally. Table list from plan v2 section 8: `customer_profile, customer_products, transactions_enriched, customer_baseline, merchant_stats, complaints_baseline, fee_schedule, contact_history, labels`.
  - Column-level schemas live in `docs/data/gold_tables.md` (diego writes it Monday; this entry then links the agreed version).
  - Pipeline functions take one partition at a time (`run_partition(table, date)`) so andres can run the same code in Lambda.
  - No document number, address or phone in any gold table read by models.
- **Last changed:** 2026-09-28T03:30Z
- **Proposed change (arturo, 2026-09-29T19:54Z, waiting for the owner):** #5 (gold columns a real gateway needs). Exact text in `docs/propuestas_interfaces.md` (https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1); the current shape above is unchanged until the owner accepts it.

## 6. Investigator report

- **Owner:** andres (analyst console and evaluation read it)
- **Status:** Proposed
- **Current shape:** `{report_id, case_id, created_at, findings: [{claim, evidence_ids[]}], hypotheses: [{name: "fraud"|"forgotten_purchase"|"unfamiliar_merchant_name"|"duplicate"|"fee_error"|"pending_reversal", p}], recommendation: "reverse_fee"|"open_chargeback"|"block_card"|"explain_and_close"|"request_information"|"escalate", confidence, draft_reply {language, text}, open_questions[], citations_valid: bool, removed_claims: int, tool_calls: int, latency_ms, model_id, prompt_version}`
  - Every `evidence_id` must appear in this run's tool results; the check runs in code before the report is stored.
- **Last changed:** 2026-09-28T03:30Z
- **Proposed change (arturo, 2026-09-29T19:54Z, waiting for the owner):** #6 (`evidence_records`, citation check rules, stub until G2). Exact text in `docs/propuestas_interfaces.md` (https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1); the current shape above is unchanged until the owner accepts it.

## 7. Trace event

- **Owner:** andres, with diego for storage and queries (trace view and evaluation page read it)
- **Status:** Proposed
- **Current shape:** one JSON line per step: `{trace_id, session_id, case_id, turn, step, actor: "rule"|"model"|"tool"|"human", name, input_ref, output_ref, latency_ms, tokens_in, tokens_out, cost_usd, version, error_code, ts}`
  - Locally written to `data/traces/*.jsonl`; in the cloud to S3 and queried with Athena.
  - No free-text PII in traces; message text is stored by reference.
- **Last changed:** 2026-09-28T03:30Z
- **Proposed change (arturo, 2026-09-29T19:54Z, waiting for the owner):** #7 (fields, ids and actor/name values the application writes). Exact text in `docs/propuestas_interfaces.md` (https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1); the current shape above is unchanged until the owner accepts it.

## 8. LLM client

- **Owner:** andres (used by arturo for G1, andres for G2, cristhian for G3 and baselines)
- **Status:** Proposed
- **Current shape:** Python package `src/llm/`. `complete(prompt_id, variables, output_schema=None, model_role="chat"|"investigator"|"judge") -> {output, usage: {tokens_in, tokens_out, cost_usd}, model_id, prompt_version}`
  - Prompts live in `prompts/<prompt_id>/<version>.md`; callers pass variables, never raw prompt strings.
  - `model_role` maps to a model ID in config (chat → Haiku 4.5, investigator → Sonnet 5, judge → Opus 5). No caller hard-codes a model ID.
  - Providers: `mock` (default locally; returns fixtures from `tests/fixtures/llm/`, deterministic) and `bedrock` (cloud, andres). Tool use for the investigator goes through the same package.
  - Timeout 8 s for `chat`; callers handle `model_timeout` with templates.
- **Last changed:** 2026-09-28T03:30Z
- **Proposed change (arturo, 2026-09-29T19:54Z, waiting for the owner):** #8 (prompts under `src/` because the Lambda asset is only `src/`; `LLM_PROVIDER` values; G1 contract). Exact text in `docs/propuestas_interfaces.md` (https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1); the current shape above is unchanged until the owner accepts it.

## 9. Runtime environment variables (infrastructure ↔ application code)

- **Owner:** andres (defined in `infra/stacks/`; every Lambda handler reads them)
- **Status:** Proposed
- **Current shape:** application code reads configuration only from these variables (locally from `.env`, in AWS set by CDK). Adding or renaming one is an interface change.
  - All functions: `STAGE`
  - Data (`DataStack.table_environment`): `TABLE_SESSIONS`, `TABLE_CASES`, `TABLE_SERVING`, `TABLE_DEMO`, `BUCKET_LAKE`, `BUCKET_ARTIFACTS`, `BUCKET_TRACES`, `GLUE_DATABASE`, `ATHENA_WORKGROUP`
  - Models (`common.model_environment`): `LLM_PROVIDER` (`mock` locally, `bedrock` in AWS), `MODEL_CHAT`, `MODEL_INVESTIGATOR`, `MODEL_JUDGE`
  - API only: `CASE_STATE_MACHINE_ARN`, `SLA_SCHEDULE_GROUP`, `SLA_SCHEDULER_ROLE_ARN`, `SLA_TARGET_FUNCTION_ARN`, `SESSION_TTL_MINUTES` (15), `LLM_TIMEOUT_SECONDS` (8)
  - Investigator only: `INVESTIGATOR_MAX_TOOL_CALLS` (12)
  - Pipeline only: `BUCKET_RAW`
  - Lambda entry points: `src/handlers/api.handler`, `investigator.handler`, `case_steps.handler` (actions `register_task_token`, `mark_incomplete`, `notify_customer`, `sla_timer`), `pipeline.handler` (event `{table, date}`)
  - HTTP routes are served under `/api/*` by CloudFront; CloudFront strips the prefix, so the API itself keeps the routes in #1. Analyst routes are `GET /analyst/cases`, `GET /analyst/cases/{case_id}`, `POST /analyst/cases/{case_id}/decision` and require a Cognito JWT.
- **Last changed:** 2026-09-28T04:40Z
- **Proposed change (arturo, 2026-09-29T19:54Z, waiting for the owner):** #9 (new variables `STORE_BACKEND`, `LIFECYCLE_BACKEND`, `LOCAL_INVESTIGATION_DELAY_SECONDS`, `SESSION_RATE_PER_MINUTE`, `TRACE_DIR`; `x-ev-session` header). Exact text in `docs/propuestas_interfaces.md` (https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1); the current shape above is unchanged until the owner accepts it.

## 10. Analyst console API

- **Owner:** arturo (console front end; andres for Cognito and the cloud lifecycle; cristhian reads `labels_emitted` for evaluation)
- **Status:** Proposed (implemented and tested locally)
- **Current shape:** types in `frontend/src/api/types.ts` (analyst section), mirrored by `src/conversation/contract.py`; rules in `docs/contrato_consola.md`.
  - Auth: API Gateway's Cognito JWT authorizer runs first; the handler reads only `requestContext.authorizer.jwt.claims` (no claims or no `sub` → 401 `session_expired`). `decided_by` = claim `sub`. No authentication bypass in `src/`; locally `scripts/local_api.py` imitates the authorizer with `LOCAL_ANALYST_TOKEN`.
  - `GET /analyst/cases?status=&lane=&language=&limit=&cursor=` → `{items[], next_cursor, as_of, poll_after_ms: 15000}`; each item carries `sla_alerts: ("unassigned"|"sla_80"|"breached")[]`. Lane B and lane C cases (lane C read only); cases still waiting for someone first, then high priority, `breach_at` ascending, `created_at`, `case_id`. `limit` 1..50 (default 20); unknown or out-of-range parameter → 400.
  - `GET /analyst/cases/{case_id}` → `{case: AnalystCaseView (with `clock_events[]` and the redacted `conversation[]`), report: InvestigatorReport|null, context: {profile, cards, evidence_txns, prior_contacts, risk_evidence, unavailable[]}, allowed_actions, version}`. `customer_id` for the context comes from the stored case. Missing case → 403 like any other 403.
  - `POST /analyst/cases/{case_id}/decision` `{client_decision_id, version, action: "approve"|"edit"|"reject", reply?, reason?, next?: "request_information"|"escalate", labels?: {intent_class? | intent_confirmed?}, evidence_reviewed?}` → `{case_id, status, analyst_decision, labels_emitted}`. approve sends the report draft; edit needs `reply` and `reason`; reject needs `reason` and `next` (escalate sends nothing and moves the case to the senior queue). Every sent text is stamped as approved by a person. Bad shape → 400; stale `version` or already decided → 409 (same `client_decision_id` and body → the stored response); wrong state, no draft, wrong reply language, unreliable report without `evidence_reviewed` → 422 `precondition`.
  - `labels_emitted` convention `type:value[:detail]`: `decision:<action>`, `recommendation:<rec>:<accepted|edited|rejected>`, `lane:<lane>:<confirmed|escalated>`, `reply:<as_drafted|edited|template>`, `intent:<class>:confirmed` or `intent:<predicted>:corrected_to:<new>`.
  - Implemented in https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1 (branch `arturo/m1-chat-api-front`), not merged yet.
- **Last changed:** 2026-09-29T19:54Z
