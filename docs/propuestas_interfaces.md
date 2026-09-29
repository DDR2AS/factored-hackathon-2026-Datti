# Propuestas para INTERFACES.md

29 sep 2026. Para Arturo. Cada bloque en inglés es el texto EXACTO para pegar en `INTERFACES.md` del repo del equipo (el archivo está en inglés y se edita en su lugar). Arriba de cada bloque, en español, quién puede publicarlo y qué cambia.

Regla del equipo (boundary-conflict-stop): #1 y #3 son de Arturo y los puede publicar él. Las demás son de otro dueño: Arturo no las edita en `INTERFACES.md`; se las pasa al dueño (este archivo copiado a `docs/` del equipo + una línea en STATUS.md) y el dueño las pega, cambia o rechaza.

Todo lo descrito está implementado y probado en local (1003 pytest, 197 vitest, smoke 26 PASS + 1 SKIP, 29 sep). Nada está desplegado. Reemplazar `HH:MM` por la hora UTC al pegar.

---

## #1 Chat API (dueño Arturo: publicar)

Cambios frente a lo propuesto el 28 sep: `customer_id` sale del request (se manda `demo_key` de una lista cerrada), el token pasa del body al header, la respuesta del chat creció, hay códigos de error nuevos y `retryable`. Fuente de verdad de los tipos: `frontend/src/api/types.ts`, espejo en `src/conversation/contract.py`; `tests/test_contract.py` falla ante cualquier diferencia.

```markdown
## 1. Chat API

- **Owner:** arturo (front end consumes it)
- **Status:** Agreed (implemented and tested locally; not deployed yet)
- **Current shape:** field-level types are `frontend/src/api/types.ts`, mirrored by `src/conversation/contract.py`; `tests/test_contract.py` fails on any difference. Behaviour and examples: `docs/interfaz_chat_requerimientos.md`.
  - Transport: routes below, served under `/api/*` by CloudFront (prefix stripped). JSON UTF-8, body at most 16 KB, every response carries `Cache-Control: no-store`.
  - Customer session: the app's own opaque token (`secrets.token_urlsafe(32)`; only its sha256 is stored), lifetime `SESSION_TTL_MINUTES` (15). Sent as `Authorization: Bearer <token>`; `x-ev-session: <token>` is accepted as a fallback in case CloudFront drops `Authorization` on GET. Never in the body or the URL. At most 30 turns per session.
  - `GET /health` → `{status: "ok", stage, deps: "ok"|"missing"}`. Needs no dependency; `deps: "missing"` means pydantic/PyYAML are not in the Lambda package.
  - `POST /session` `{demo_key: "lucia"|"sofia"|"andres"|"joao"|"martina"|"carlos", channel: "web"|"app"|"whatsapp", language?: "es"|"pt"}` → `{session_token, expires_at, customer: {display_name, language, locale, country}, welcome: ChatTurn, synthetic: true}`. The server maps `demo_key` to a customer from its own allow-list; a body with `customer_id` or any unknown field is 400. At most `SESSION_RATE_PER_MINUTE` (60) per source IP, then 429 `rate_limited`.
  - `POST /chat` `{client_msg_id, message? (max 1000 chars) | button_id?, demo_switches?: {tools_down?, model_slow?, expire_session?}}` → `{turn, reply_text, reply_language, reply_source: "template"|"model", buttons: [{id, label, kind: "confirm"|"deny"|"choice"|"handoff"}], input_mode: "free_text"|"buttons_only", progress: {step, complaint_type, claimed: {amount, currency, date, merchant_text, card_last4}, missing[]}, case_card: CustomerCaseView|null, lane: "A"|"B"|"C"|null, degraded: ("model_timeout"|"tool_unavailable")[], trace_id, trace_summary: {steps: [{actor, name, latency_ms, version, error_code}], latency_ms, cost_usd, model_id, tokens_in, tokens_out, rule_id, rules_version}, poll_after_ms, demo_switches: {tools_down, model_slow}, synthetic: true}`.
    - Same `client_msg_id` → the same answer and never a second case. Buttons are server-issued and single use (reuse → 409 `conflict`). A second message of the same session while one is running waits up to 1 s, then 409 `conflict` with `retryable: true`.
    - A model timeout never fails the turn: 200 with a template reply and `degraded: ["model_timeout"]`. A tool that fails twice sends the case to lane C `tool_failure` with `degraded: ["tool_unavailable"]`.
    - `demo_switches` exist only for judge-mode sessions (all sessions today); details in `docs/contrato_consola.md`.
  - `GET /cases/{case_id}` → `{case: CustomerCaseView, poll_after_ms}`. Only the session that opened the case may read it; another session's case and a missing case give the same 403 `not_authorized`. `poll_after_ms` is 5000 while a lane B case is `open`, `investigating` or `awaiting_analyst`, else null.
  - `CustomerCaseView` (the customer-visible subset of #3, built only by `case.customer_view`): `{case_id, created_at, status, lane, lane_reason_code, subcategory, language, customer_statement, charge: {local_date, amount, currency, merchant_name, card_last4, status}|null, expected_date, first_response_by, handoff_queue, outcome, resolution: {language, text, sent_at, approved_by_human: true}|null, lifecycle_step: "open"|"investigating"|"in_review"|"notified"|"closed"|null, synthetic: true}`.
  - Errors: `{error: {code, message, retryable}}`. Clients branch on HTTP status, then on `code`, never on `message`. `invalid_request` 400, `session_expired` 401, `not_authorized` 403, `conflict` 409, `session_limit` 409, `precondition` 422, `rate_limited` 429, `internal` 500, `not_implemented` 501, `tool_unavailable` 503, `model_timeout` 504 (never returned by `/chat`). `retryable` is true only for `rate_limited`, `tool_unavailable`, `model_timeout`, `internal`, and for the in-flight `conflict` above. API Gateway's own 401/404 bodies are `{"message": ...}`.
- **Last changed:** 2026-09-29THH:MMZ
```

---

## #3 Case record (dueño Arturo, con Andrés para el almacenamiento: publicar)

Cambios: dos estados nuevos (`resolved_in_contact` para ruta A, `handed_off` para ruta C y escalado), campos de la consola (`version`, `updated_at`, `resolution`, `rules_version`, más en `analyst_decision`), la vista del analista y el protocolo de almacenamiento. Andrés implementa el store en DynamoDB contra ese protocolo.

```markdown
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
- **Last changed:** 2026-09-29THH:MMZ
```

---

## Propuestas a Andrés

### #2 Gateway tools

Hoy existe un backend `demo` (`src/conversation/demo_gateway.py`, datos en `src/conversation/demo/customers.yaml`) con las firmas de #2. Propuesta: que sea el backend `demo` de `src/gateway/` o que se cargue en la tabla Demo; lo decide Andrés. Texto para agregar al final de "Current shape" de #2:

```markdown
  - `GatewayContext {customer_id, session_id, trace_id, actor: "customer"|"analyst"}`, built only by the server. `GatewayContext.for_analyst(customer_id, session_id, trace_id)` builds the analyst console's context from the STORED case (owner and session from the case, never from the request); a per-session judge switch (`tools_down`) never empties what the analyst sees.
  - `get_customer_profile(ctx) -> CustomerProfile {display_name (first name only), language, locale, country, currency, segment, reference_date, products[{product_id, kind, name, currency, last4}]}`; `reference_date` is the customer's latest transaction date on or before the data cut-off, used to resolve "el 12" or "ayer".
  - `Txn` adds `txn_type: "purchase"|"fee"|"reversal"` and `reversal_of: txn_id|null` (lane rules need "is this charge a fee" from the record, never from the text; `get_reversals` needs the link).
  - `block_card` without `confirmed_by_customer=True` raises `NotConfirmed` (new error, alongside `NotOwned`, `NotFound`, `ToolUnavailable`).
  - Backend `demo` (judge-mode customers from `src/conversation/demo/customers.yaml`, synthetic, packaged with `src/`): session bootstrap helpers `customer_id_for_demo_key(demo_key)` and `profile_for_demo_key(demo_key)`, used only by `POST /session`. Not implemented in `demo`: `get_risk_evidence`, `get_digital_sessions`, `get_merchant_stats`.
```

Nota honesta: `analyst.py` hoy llama `profile_for_customer_id(customer_id)` (sin ctx) para el perfil del analista; con `get_customer_profile(ctx)` y el ctx de `for_analyst` se puede quitar. Y `docs/contrato_consola.md`, propuesta 4, escribe `for_analyst(case_id, analyst_sub)`; la firma real es la de arriba (el `sub` del analista va a la traza de la decisión, no al contexto). Corregir ese documento al copiarlo.

### #6 Investigator report

```markdown
  - Adds `evidence_records: {evidence_id: {kind, summary, fields}}`: a snapshot of every cited record, stored with the report, so a citation opens without another route and the citation check stays auditable. `kind` ∈ `transaction, reversal, fee_schedule, customer_baseline, risk_evidence, digital_session, prior_contact, merchant_stats, card`; `fields` are scalars only, never document, address or phone. Ids: the record id when there is one (`TX-…`, `DEMO-K-…`), `FEE:<product_id>:<fee_code>`, `RISK:<txn_id>`, `BASELINE`, `MERCHANT:<merchant_id>`; never the `customer_id`.
  - `hypotheses`: each name at most once, sorted by `p` descending. `recommendation` is a proposal; nothing executes it.
  - Citation check (in code, before storing): findings without evidence or citing an id no tool returned in this run are removed and counted in `removed_claims`; `citations_valid` then means every citation LEFT in the stored report has its record in `evidence_records`. The report is unreliable when `citations_valid` is false or `removed_claims > 1`; an unreliable report can only be approved with `evidence_reviewed: true`.
  - `draft_reply.text` must pass the forbidden-phrases check (no refund or compensation promises) before it is stored.
  - If a tool fails after two retries there is no report: the case goes to `awaiting_analyst` flagged `evidence_incomplete` and cannot be approved.
  - Until G2 lands, a deterministic stub (`src/conversation/investigator_stub.py`, `model_id: "stub-g2-deterministic"`, `prompt_version: "stub-provisional"`) produces this exact shape; the console labels it provisional and evaluation must not count it as G2.
```

### #7 Trace event (campos usados)

```markdown
  - As written by the application today: exactly the 16 fields above plus `source` (`"demo"` for judge-mode sessions, so they stay out of prompts and training). `step` is the ordinal inside the trace.
  - `trace_id`: `tr-<16 hex>` per chat turn; `lc-<case_id>` for lifecycle events and the analyst decision. `session_id`: `S-<16 hex>`.
  - `actor`/`name` in use: `rule` (`detect_language`, `redact`, `extract`, `button`, `lane_rules`, `case_store.put`, `demo_switches`, `lifecycle.transition`, `investigator_stub`, `guard.*`), `model` (`classify`, `rank_candidates`, `g1_extract`), `tool` (every #2 function called), `human` (`analyst_decision`).
  - `input_ref`/`output_ref` are references or hashes, never free text: `msg:<session_id>:<turn>`, `txn:<id>`, `intent:<class>:<p>`, `case:<id>:v<version>:by:<sub>`, sha256 (first 16 hex) and length of reasons and texts.
  - `tokens_in`, `tokens_out`, `cost_usd` are set only on model steps; with the mock provider they are estimates. `error_code` values in use: `model_timeout`, `model_unavailable`, `model_error`, `no_fixture`, `schema_invalid`, `tool_unavailable` (one event per attempt), `notowned`, `notfound`, `manipulation`.
  - Local sink: `TRACE_DIR` (default `data/traces/<UTC date>.jsonl`). The sink never raises into a request.
```

### #8 LLM client (ruta de prompts)

`INTERFACES.md` dice `prompts/<prompt_id>/<version>.md` en la raíz, pero el asset de la Lambda es solo `src/` (`infra/stacks/common.py`, `SRC_DIR`): una carpeta `prompts/` en la raíz no se despliega. Implementado hoy: `src/conversation/prompts/g1_extract/v1.md`; el cargador (`llm_port.load_prompt`) busca ahí y después en `prompts/`. Si Andrés prefiere una carpeta común para G1, G2 y G3, la alternativa es `src/prompts/`, y Arturo mueve el archivo y agrega esa carpeta al cargador (cambio de una línea).

```markdown
  - Prompts live under `src/`, because the Lambda asset is only `src/`: `src/conversation/prompts/<prompt_id>/<version>.md` (G1 today: `g1_extract/v1.md`). `prompt_version` is `<prompt_id>@v<n>`. The customer's text goes inside `<texto_cliente>…</texto_cliente>`, declared as data; copies of those tags in the text are removed first.
  - Mock fixtures stay in `tests/fixtures/llm/<prompt_id>/*.yaml` (YAML, every file `synthetic: true`), outside the Lambda package.
  - `LLM_PROVIDER`: `mock` | `none` (no model: the rule extractor answers) | `bedrock`. With `bedrock` the application loads `src/llm` and calls `complete(prompt_id, variables, output_schema=..., model_role=...)`; a `TimeoutError` is treated as `model_timeout`, and the caller also enforces `LLM_TIMEOUT_SECONDS` on its side.
  - G1 contract: `output` must validate against the JSON schema passed in `output_schema` (pydantic `Extraction`); the model only extracts data. It never chooses the lane, never sees document, address or phone (the text is redacted first), has no tools, and no customer-facing text comes from it.
```

### #9 Variables de entorno

Todas las que lee el código nuevo. `STAGE`, `SESSION_TTL_MINUTES`, `LLM_TIMEOUT_SECONDS`, `LLM_PROVIDER` y `MODEL_CHAT` ya están en #9.

```markdown
  - API only, new: `STORE_BACKEND` (`memory` | `dynamodb`; required whenever `STAGE` is not `local`, the app refuses to serve without it; `memory` loses state between Lambda containers), `LIFECYCLE_BACKEND` (`local` | `stepfunctions`, default `local`), `LOCAL_INVESTIGATION_DELAY_SECONDS` (4; seconds between opening a lane B case and the stub report, only with `LIFECYCLE_BACKEND=local`), `SESSION_RATE_PER_MINUTE` (60; `POST /session` per source IP, 0 disables), `TRACE_DIR` (JSONL trace sink folder; `data/traces` locally, `/tmp/traces` in Lambda, unused once traces go to S3)
  - `LLM_PROVIDER` values: `mock` (local default), `none` (no model call), `bedrock`. `MODEL_CHAT` is read only to display it; no code selects a model ID.
  - Local only, never set in AWS: `LOCAL_ANALYST_TOKEN` (read only by `scripts/local_api.py`, which imitates the Cognito authorizer; `src/` never reads it)
  - Headers: `/chat` and `/cases/{case_id}` accept the session token as `Authorization: Bearer` or `x-ev-session`. `/analyst/*` needs `Authorization` to reach the JWT authorizer through CloudFront on GET, which requires a cache policy that includes it.
```

Además, para el store DynamoDB (no es texto de INTERFACES, es para Andrés):
- La tabla Sessions tiene TTL en `expires_at`, pero `SessionRecord.expires_at` es un string ISO. DynamoDB solo expira con un número (epoch en segundos): escribir un atributo numérico aparte y apuntar el TTL ahí, o cambiar el nombre del atributo TTL en `data_stack.py`.
- `SessionStore.get_by_token_hash` necesita buscar por hash del token: clave de la tabla o un GSI `by_token_hash`. La tabla hoy solo tiene `session_id`.
- `ReviewStore` (reportes, respuestas idempotentes de decisiones, conversación del caso con tope de 200 turnos) no tiene tabla. Opciones: tabla propia o ítems aparte en Cases (los GSI `by_customer`/`by_status` no indexan ítems sin esos atributos).
- Los interruptores del juez (`tools_down`, `model_slow`) viven en memoria del proceso y en la sesión; en la nube, guardarlos en el registro de sesión.

### Ciclo de vida local ↔ Step Functions

```markdown
## Case lifecycle (proposal, application side owned by arturo, cloud side by andres)

- Code that opens cases and the analyst console depend only on `CaseLifecycle` (`src/conversation/lifecycle.py`): `start(case_id)` when a lane B case is saved; `advance(case_id)` (local: applies due transitions; cloud: only reads); `advance_all()` before listing the queue; `on_decision(case_id, expected_version, decision, resolution, labels)` = conditional write on `version` plus the transition. Selected by `LIFECYCLE_BACKEND`.
- Mapping to `WorkflowStack`: `start` = StartExecution with `{case_id, money_dispute}` (`money_dispute` = `is_money_case`: `unrecognized_charge` or `wrong_fee`, duplicates and `purchase_amount_disputed` included); `investigating` = the Investigate task; `awaiting_analyst` = AwaitAnalyst with the task token stored in the case (internal, never in a view); `on_decision` = conditional write + SendTaskSuccess; `notified → closed` = a one-time Scheduler timer (30 s in the demo).
- Transitions the state machine does not have yet: analyst asks for information (`awaiting_customer`: the case waits for the customer instead of closing, and the customer's answer returns it to `awaiting_analyst`); `reject` + `escalate` (`handed_off`, senior queue, decided again); customer disagrees after `notified`/`closed` (`reopened`, senior queue, high priority). Each needs another `.waitForTaskToken` state or a new execution.
- `notify_customer` locally is not a push: `on_decision` stores `resolution` and the chat shows it once per session (live card via `GET /cases`, or at the start of the next reply). Every transition is a #7 event with `trace_id = lc-<case_id>`.
```

---

## Propuesta a Cristhian (#4)

Los sustitutos viven en `src/conversation/` y son los que su modelo reemplaza. El punto de cambio es el import en `src/conversation/orchestrator.py` (líneas 43–47).

```markdown
  - Stand-ins in use until the real models land (same signatures): M1 `conversation.ranker.rank_candidates(slots, candidates) -> {ranked: [{txn_id, p}], band, model_version: "m1-rule-0"}` (v1.4 rule turned into probabilities with a "none of these" term, so p values sum to less than 1; every candidate returned, highest p first, ties by `txn_id`, p rounded to 4 decimals; no candidates → `ranked: []`, band `ask`). M2 `conversation.classifier.classify(text, language) -> {intent_class, p, gate_action, runner_up, model_version: "m2-keywords-0"}` (keywords, not calibrated; `manipulation` wins with p ≥ 0.9 and gate `accept`; no keyword at all → `complaint_other`, p = 1/9, gate `ask`).
  - Inputs: `text` is the REDACTED message; `language` is `es` or `pt`. `candidates` are #2 `Txn` objects (with `txn_type`, `reversal_of`) or mappings with at least `txn_id, amount, currency, local_date, merchant_name`; `slots` as above, amount as decimal string, date ISO.
  - Besides `rank_candidates`, the orchestrator uses `band_for(p_top1)` (the band of a group of duplicate charges, whose p values are summed) and the confirm threshold; the real M1 package should export `band_for` reading the same thresholds.
  - `config/thresholds.yaml` and `models/<m1|m2|m3>/<version>/` at the repo root are NOT in the Lambda package (the asset is only `src/`). Proposal: thresholds in `src/models/thresholds.yaml`, artifacts either small enough under `src/models/` or loaded from `BUCKET_ARTIFACTS` (the API function can read it). Runtime libraries (lightgbm, numpy, scikit-learn) go in `src/requirements-lambda.txt` and count toward the 250 MB unzipped Lambda limit.
  - Called synchronously once per chat turn: load once per process, keep warm latency low.
```

Dos cosas que siguen siendo de Arturo aunque llegue M2 (no van en #4): `classifier.scores(text)` se usa para saber si "no se entendió nada" cuando el modelo tampoco respondió, y hoy la detección de inyección vive en los patrones de `manipulation` del clasificador. Si M2 reemplaza al clasificador, esos patrones deben quedar como guardia por reglas en el código de Arturo (OR con M2), para que un falso negativo del modelo no deje pasar una inyección. `merchant_similarity` también queda del lado de Arturo.

---

## Propuesta a Diego (#5)

Lo que necesitaría un gateway real (DynamoDB Serving, `pk = CUST#<customer_id>`, `sk = PROFILE | CARD#<id> | TXN#<local_date>#<txn_id> | BASELINE`) para reemplazar al demo. Nombres del lado del gateway; entre paréntesis, lo que tenía la gold v1.4 de este repo (`sql/gold/*.sql`) cuando existe.

```markdown
  - Columns the gateway (#2) needs from gold, per table:
    - `customer_profile`: `customer_id`, `display_name` (first name only; v1.4 `first_name`), `country`, `segment`, `language` (the dataset has none: `es` for every real customer; Portuguese exists only in synthetic demo customers), `locale` (derived from country), `currency`, `reference_date` (latest transaction date on or before the data cut-off), products `{product_id, kind (v1.4 product_type), name, currency, last4}`. Never `document_hash`, document type, email, phone or address in anything the gateway returns.
    - `transactions_enriched`: `txn_id` (v1.4 `transaction_id`), `product_id`, `card_last4`, `local_date` and `local_time` (the date and HH:MM the customer sees on the statement; v1.4 has `ts` and `process_date`, which is the accounting date `date(ts − 6 h)`: pick one and document it), `amount` (decimal string), `currency`, `amount_usd`, `merchant_id` (not in the dataset: stable id from the normalized merchant name), `merchant_name`, `merchant_category`, `status`, `response_code`, `fraud_score`, `txn_type` (`purchase` | `fee` | `reversal`, from `transaction_type`), `reversal_of` (the dataset has no link: null, or a documented heuristic).
    - `customer_cards`: `card_id`, `product_id`, `card_last4`, `status` (`active` | `blocked`, from `product_status`).
    - `fee_schedule` (synthetic): per `product_id`: `product_name`, `currency`, fees `[{fee_code, merchant_id, description, amount, frequency}]`; a fee charge links to its row by `merchant_id`.
    - `customer_baseline`: `window_from`, `window_to`, `n_txns`, `currency`, `median_amount`, `p90_amount`, `top_categories` (3), `top_merchants` (3), `usual_hours` `[from, to]`; computed from purchases older than 14 days before `reference_date`, so the disputed recent charges do not define "normal".
    - `contact_history`: `contact_id`, `date`, `channel`, `contact_type` (`complaint` | `inquiry`), `subcategory`, `status`.
```

---

## Nueva interfaz #10: contrato de la consola del analista (dueño Arturo)

Aplica: la consola, la evaluación (Cristhian lee `labels_emitted`) y el ciclo de vida en la nube (Andrés: Cognito, `SendTaskSuccess`) dependen de estas formas, y hoy no están en ninguna interfaz. Arturo la puede publicar; queda **Proposed** hasta que Andrés confirme la parte de autenticación.

```markdown
## 10. Analyst console API

- **Owner:** arturo (console front end; andres for Cognito and the cloud lifecycle; cristhian reads `labels_emitted` for evaluation)
- **Status:** Proposed (implemented and tested locally)
- **Current shape:** types in `frontend/src/api/types.ts` (analyst section), mirrored by `src/conversation/contract.py`; rules in `docs/contrato_consola.md`.
  - Auth: API Gateway's Cognito JWT authorizer runs first; the handler reads only `requestContext.authorizer.jwt.claims` (no claims or no `sub` → 401 `session_expired`). `decided_by` = claim `sub`. No authentication bypass in `src/`; locally `scripts/local_api.py` imitates the authorizer with `LOCAL_ANALYST_TOKEN`.
  - `GET /analyst/cases?status=&lane=&language=&limit=&cursor=` → `{items[], next_cursor, as_of, poll_after_ms: 15000}`. Lane B and lane C cases (lane C read only); cases still waiting for someone first, then high priority, `breach_at` ascending, `created_at`, `case_id`. `limit` 1..50 (default 20); unknown or out-of-range parameter → 400.
  - `GET /analyst/cases/{case_id}` → `{case: AnalystCaseView, report: InvestigatorReport|null, context: {profile, cards, evidence_txns, prior_contacts, risk_evidence, unavailable[]}, allowed_actions, version}`. `customer_id` for the context comes from the stored case. Missing case → 403 like any other 403.
  - `POST /analyst/cases/{case_id}/decision` `{client_decision_id, version, action: "approve"|"edit"|"reject", reply?, reason?, next?: "request_information"|"escalate", labels?: {intent_class? | intent_confirmed?}, evidence_reviewed?}` → `{case_id, status, analyst_decision, labels_emitted}`. approve sends the report draft; edit needs `reply` and `reason`; reject needs `reason` and `next` (escalate sends nothing and moves the case to the senior queue). Every sent text is stamped as approved by a person. Bad shape → 400; stale `version` or already decided → 409 (same `client_decision_id` and body → the stored response); wrong state, no draft, wrong reply language, unreliable report without `evidence_reviewed` → 422 `precondition`.
  - `labels_emitted` convention `type:value[:detail]`: `decision:<action>`, `recommendation:<rec>:<accepted|edited|rejected>`, `lane:<lane>:<confirmed|escalated>`, `reply:<as_drafted|edited|template>`, `intent:<class>:confirmed` or `intent:<predicted>:corrected_to:<new>`.
- **Last changed:** 2026-09-29THH:MMZ
```
