# Expediente Vivo v2: complaint and dispute handling with controlled AI

**Team Datti** · Factored AI & Data Hackathon 2026 · LATAM Bank dataset

> **Status (1 Nov 2026).** Every stage of plan v2 is built and runs end to end on a laptop: data pipeline and
> quality contracts, gold tables, gateway, M1/M2/M3, LLM client, G1 extraction, G2 investigator, case lifecycle
> and analyst console, planted-scenario evaluation against the rules-only baseline, trace queries, learning-loop
> demo and the evaluation page. Two things are **not** done, and the README says so wherever it matters:
> 1. **Data source.** The organizers' S3 key was not available on the build machine, so the pipeline, the models
>    and the evaluation ran on a **synthetic replica** of the 13 tables (`src/etl/synth_dataset.py`, same columns,
>    layout and generator rules as the real data; decision D15). Every report and page states its data source.
>    `make data` switches to the real bucket as soon as the key is in `.env`; the real-data findings of the v1.4
>    analysis are in [`docs/v14_datos_reales/`](docs/v14_datos_reales/README.md).
> 2. **Cloud.** Nothing is deployed (AWS access is by IAM user from the account owner, D10). The CDK app and the
>    cloud adapters are in the repo and tested offline ([`docs/despliegue_aws.md`](docs/despliegue_aws.md)).
>
> Without model credentials G1 runs on a deterministic mock and G2 on a transparent deterministic policy (same
> loop, tools and checks); with `LLM_PROVIDER=anthropic|bedrock` both use Claude ([`docs/llm_client.md`](docs/llm_client.md)).
>
> An independent review of this build on 1 Nov 2026 ([`docs/revision_v2.md`](docs/revision_v2.md)) found that
> several numbers below held by construction and that the cloud path had three blockers. The code fixes are in
> the repo; the numbers in sections 1, 6 and 8 are now stated with what they can and cannot show.

A customer writes in Spanish or Portuguese about a charge or a fee they dispute. The system understands the
request, finds the exact charge among **that customer's own** transactions, checks it against data, and a
versioned rule file picks one of three lanes: **A** resolve now with the record, **B** open a complete case with a
promised date (money disputes get an investigator report for an analyst), or **C** hand to a person with a
structured file. The language model reads and extracts; machine learning ranks and scores; rules in code
decide; people approve anything that touches money. Nothing moves money.

- Plan and reasoning: [`docs/plan/expediente-vivo-v2.html`](docs/plan/expediente-vivo-v2.html) · Decisions: [`DECISIONS.md`](DECISIONS.md) · Interfaces: [`INTERFACES.md`](INTERFACES.md) · Architecture: [`docs/architecture.md`](docs/architecture.md)
- Evaluation report: [`docs/evaluacion.md`](docs/evaluacion.md) · Models: [`docs/modelos/`](docs/modelos/) · Data quality: [`docs/data/dq_report.md`](docs/data/dq_report.md) · Gold schemas: [`docs/data/gold_tables.md`](docs/data/gold_tables.md)
- How to run everything: [`docs/como_correr_local.md`](docs/como_correr_local.md) · Pitch and video script: [`docs/pitch.md`](docs/pitch.md)

## How this README maps to the judging criteria

| Criterion | Where to look |
|---|---|
| It works | [5. Run it and try it](#5-run-it-and-try-it) |
| Problem backed by data | [1. The problem in the data](#1-the-problem-in-the-data) |
| AI engineering, controlled automation | [2. What the system does](#2-what-the-system-does), [4. Controls and safety](#4-controls-and-safety) |
| Architecture, production thinking | [3. Architecture](#3-architecture), [9. Path to production](#9-path-to-production) |
| Machine learning | [6. Evaluation](#6-evaluation), [`docs/modelos/`](docs/modelos/) |
| Data engineering, data quality | [7. Data engineering](#7-data-engineering) |
| Reliability, privacy, fairness | [4. Controls and safety](#4-controls-and-safety), fairness rows in [6](#6-evaluation) |
| Rationale and documentation | [10. Decisions](#10-decisions), [8. Costs](#8-costs), [11. Declared limits](#11-declared-limits) |
| Reproducibility | [5. Run it](#run-it), [12. Pre-existing components and licences](#12-pre-existing-components-and-licences) |

## 1. The problem in the data

Figures from the v1.4 analysis over the **full real dataset** (686,296 call-center contacts, 67,095 formal
complaints, 4,425,008 transactions, 2023-06-17 to 2026-06-17); sources in [`docs/v14_datos_reales/`](docs/v14_datos_reales/README.md).

| Finding | Figure |
|---|---|
| Complaints are the worst-served contact reason | 17.1% of contacts; **43.6%** resolved at first contact, the lowest of six reasons; CSAT 2.43 |
| First response on a formal complaint is slow | median **38 h** |
| Resolution takes weeks | median **16 days**, p90 28 days |
| Many cases are never picked up | **20,125** formal complaints (30%) "Open" with no agent; 20% breach the SLA |
| Money disputes are a large share | "Cargo no reconocido" + "Cobro indebido" = **40%** of complaints |
| No complaint arrives complete | 33% carry an amount; the product they reference belongs to **another customer in 100%** of cases |
| `fraud_score > 30` is an exact rule | fraud in 100% of such transactions, 55% of all frauds; the other 45% match no signal in 44 variables (AUC 0.50) |
| Transcripts carry no complaint text | balance-inquiry templates; no Portuguese anywhere |

Consequences (plan v2 §2–3, D1, D3, D7): go deep on the two money complaint types; the system must build the
evidence itself (find the charge, verify ownership); real complaints can't be matched to real transactions, so
the evaluation plants ground truth over real transactions; keep `fraud_score > 30` as a rule.

The synthetic replica is **parameterised** with these baselines, so the B0 the pipeline computes on it
(complaint-contact FCR 43.6%, CSAT 2.43, median first response 38.4 h, p90 resolution 28 days, money complaints
40.6%, affected product of another customer 100%, fraud rule precision 100%) matches them by construction. That
checks that the pipeline reads the generator back correctly; it is not evidence about the system.

## 2. What the system does

1. The customer writes (ES or PT) in a signed session.
2. The message is **redacted** (documents, cards, phones, e-mails, addresses) and classified (M2, nine classes;
   a manipulation guard by rules always runs too).
3. Details are extracted (amount, date, merchant, card) by a rule extractor and by G1 when available, schema-
   validated with anti-hallucination guards.
4. The charge is looked up **only among the session customer's transactions** (gateway) and ranked (M1).
5. Duplicates, reversals, the fee schedule and `fraud_score` are checked; M3 adds risk evidence.
6. **YAML rules pick the lane** ([`lane_rules.yaml`](src/conversation/rules/lane_rules.yaml); first match wins; default lane C).
7. Lane B opens a case with a number, a promised date and SLA timers; money cases get a G2 investigator report.
8. An analyst approves, edits or rejects in the console; each decision emits labels for the learning loop.

| Lane | When (rule ids) | Customer gets |
|---|---|---|
| **A** resolve now | `customer_recognizes`, `already_reversed`, `duplicate_already_reversed`, `fee_matches_schedule` | The exact record and "is this resolved?"; if not, B (`explanation_not_accepted`) |
| **B** case with a promise | `unrecognized_low_risk`, `fee_does_not_match`, `duplicate_not_reversed`, `purchase_amount_disputed`, `other_complaint_complete` | Case `EV-…` and a promised date in seconds; status in the same chat; the analyst's approved reply |
| **C** human now | `tool_failure`, `lost_card`, `asks_for_human`, `regulator_or_legal`, `repeat_complainer`, `compensation_requested`, `high_amount` (> 500 USD), `high_fraud_score` (> 30), `complaint_about_person`, `no_progress`, default `no_rule_matched` | Who will contact them and when; the analyst gets a structured handoff |

### Who decides what (and what runs today)

| Step | Decided by | Component | State |
|---|---|---|---|
| Which customer data can be read | Gateway (code) | `src/gateway/`: tools scoped by the server-side `customer_id`; backends `demo` (judge-mode customers), `duckdb` (gold tables), `composite` | Built and tested; DynamoDB backend in `src/cloud/` (offline-tested) |
| Intent class (9) | ML: M2 | `src/models/m2/`: hashed character and word n-grams plus an ES/PT concept lexicon, multinomial logistic regression, temperature-calibrated, accept/ask/human thresholds from validation; the keyword guard runs with it | Built (`m2-hashlr-1`, the default backend; its class enters the rules only above its accept threshold) |
| Slot extraction | GenAI: G1 | rule extractor + G1 through `src/llm` (`g1_extract@v1`) | Mock without credentials; Claude with `LLM_PROVIDER=anthropic|bedrock` |
| Which charge | ML: M1 | `src/models/m1/`: LightGBM LambdaRank + calibrated confidence model; the v1.4 rule is the baseline and fallback | Built (`m1-lgbm-1`, primary because it beats the rule on the frozen test) |
| Risk evidence | Data + ML: M3 | `src/models/m3/`: amount vs the customer's usual, first time at merchant, hour, velocity, login/new device | Built; lift experiment says **no lift**, so evidence only |
| **The lane** | **Rules** | `lane_rules.yaml` evaluates evidence from code (gateway results, the customer's confirmations) and model outputs used as data: M2's class above its accept threshold, G1's flags and its `complaint_type` suggestion. Three lane C rules (`lost_card`, `asks_for_human`, the regulator/compensation/staff flags) can fire on such a model output alone, always towards a person. No model output picks lane A or B by itself: the A rules need the customer to recognise the charge or to accept the explanation of a fee that matches the published schedule (D2, as implemented) | Built |
| Customer-facing text | Templates | ES (tú/usted/vos) and PT templates; `reply_source: template` | Built |
| Investigation of lane B money cases | GenAI: G2 | `src/investigator/`: read-only tools, ≤ 12 calls, ≤ 90 s, cited findings, citation check in code | Deterministic policy without credentials (labelled "sin modelo" in the console); Claude with credentials |
| Anything touching money or the account | **People** | Analyst console: approve, edit, reject | Built |
| Card block | **Customer** | Only after the customer's explicit "yes" button, with read-back of the new status | Built (simulated core) |

### The six judge-mode customers

All synthetic (first names only), in [`src/conversation/demo/customers.yaml`](src/conversation/demo/customers.yaml);
`tests/test_integration_v2.py` replays the judge-mode suggestions with the full v2 configuration.

| Customer | Language · country | Judge-mode suggestions | Result |
|---|---|---|---|
| Lucía | ES · MX | "Hola, me salió un cobro como de 450 en el súper el 12 y no lo ubico" | M1 finds SUPER AHORRO SA 449.90 on 12 Jun; "No lo reconozco" → **B** `unrecognized_low_risk`; "Sí, lo reconozco" → **A** |
| Sofía | ES · CO | "Buenas, me cobraron algo raro" → "Fueron como 52 mil de un domicilio el 9" → "Y de paso, quisiera pedir un préstamo" | the system asks, then offers three near-identical charges as buttons → **B** `unrecognized_low_risk`; the loan is out of scope and a person is offered |
| Andrés | ES · CO | "Me cobraron dos veces lo mismo, con minutos de diferencia" | duplicate not reversed → **B** `duplicate_not_reversed`; G2 recommends a chargeback; the analyst decides |
| João | PT · AR | "Oi, me cobraram uma tarifa que não bate com a tabela de vocês" | maintenance fee 12,500 vs 8,900 in the schedule → **B** `fee_does_not_match`, all in Portuguese; G2 recommends reversing the fee |
| Martina | ES · AR | "Me aparece un consumo rarísimo en la tarjeta, creo que me la clonaron" → "Fueron 145 mil en ELECTRO MUNDO ONLINE el 15" | `fraud_score` 82 → **C** `high_fraud_score`, fraud queue; the card is blocked only after her "yes" |
| Carlos | ES · MX | "Es la tercera vez que me quejo; si no lo arreglan voy al regulador" | **C** `regulator_or_legal`, high priority, with the structured handoff |

## 3. Architecture

Diagrams: [`docs/architecture.md`](docs/architecture.md). Boundaries, each with a contract in [`INTERFACES.md`](INTERFACES.md):

| Boundary | Contract | Code |
|---|---|---|
| Chat API, analyst API, evaluation API | #1, #10 | `src/handlers/api.py`, `src/conversation/contract.py` (mirrors `frontend/src/api/types.ts`; a test fails on drift) |
| Gateway tools (the only path to customer data) | #2 | `src/gateway/` (demo, duckdb, composite), `src/cloud/dynamodb_gateway.py` |
| Case record and lifecycle | #3 | `src/conversation/case.py`, `lifecycle.py` (local), `src/cloud/stepfunctions_lifecycle.py` (cloud) |
| Model I/O (M1, M2, M3) | #4 | `src/models/`, thresholds in `src/models/thresholds.yaml`, selected by `M1_BACKEND`/`M2_BACKEND` |
| Gold and serving tables | #5 | `src/pipeline/` (`run_partition(table, date)`), `docs/data/gold_tables.md`, DynamoDB export `src/pipeline/serving.py` |
| Investigator report | #6 | `src/investigator/` |
| Trace event | #7 | JSONL per step (no free text); queries `src/eval/traces.py` (same SQL as Athena) |
| LLM client | #8 | `src/llm/` (mock, anthropic, bedrock; `complete` and `converse`) |
| Runtime configuration | #9 | environment variables, `.env.test` |

Locally one Python process (`scripts/local_api.py`) runs the same `src/handlers/api.py` the Lambda runs, imitates
CloudFront and the API Gateway JWT authorizer for the analyst, and serves the React front end. In AWS (CDK in
[`infra/`](infra/README.md), us-east-2): S3 + CloudFront, API Gateway, Lambda, DynamoDB, Cognito, Step Functions,
EventBridge Scheduler, Bedrock; the pipeline runs per partition in Lambda (the 194 quality checks stay a local
`make pipeline` step: in Lambda they would need pyarrow, past the 250 MB package limit). The cloud adapters
(`src/cloud/`: DynamoDB store and gateway, Step Functions lifecycle, traces) are tested offline with moto and
`cdk synth`; how to deploy them is in [`docs/despliegue_aws.md`](docs/despliegue_aws.md).

## 4. Controls and safety

| Brief requirement | Mechanism | Evidence |
|---|---|---|
| Permissions outside model prose | The gateway takes `customer_id` from the server-side session, never from text; a record of another customer raises `NotOwned`; a case of another session answers 403 like a missing one | Evaluation probes: 0 leaks in 204 cross-customer tool calls; `tests/test_pipeline_gateway.py` |
| Models never pick lane A or B | Lane chosen by `lane_rules.yaml` over evidence from code and model outputs used as data (M2 class above its accept threshold, G1 flags); three C rules can fire on a model output alone, always towards a person; G1 returns slots and flags only, no tools; G2 proposes, an analyst decides | Trace shows `actor: rule` for the lane; `tests/test_review_round4.py` |
| Prompt injection | Customer text wrapped as data in the user turn, G1's instructions in the system prompt; the manipulation guard (patterns) or a confident M2 verdict answers with a template, no tools, no case. The patterns alone catch a minority of new attempts (1 of 4 in the review; the trained M2 caught 4 of 4), which is why M2 is the default backend. G2 reads the redacted statement as data: a manipulated recommendation inside the allowed list, with real citations, would still reach the analyst as a proposal, never as an action | 0 actions from injected text in 24 adversarial scenarios, measured with the mock G1 and the policy G2, which never read the text: it proves the guard and the templates, not G1 or G2 against a real model |
| Expired session | 15-minute TTL; expired or unknown token → 401, no data | Probe: 0 responses with data after expiry |
| Unauthorized console access | `/analyst/*` trusts only JWT claims from the authorizer; no bypass in `src/` | `tests/test_api.py` |
| Tool failure | Two retries with backoff, then lane C `tool_failure`, case marked incomplete | Probe: safe path taken |
| Model slow or down | 8 s timeout; rule extractor and fixed buttons take over. G2: any model failure (unavailable, timeout, HTTP 4xx, refusal, output cut by `max_tokens`, no report) falls back to the deterministic policy in the same run, with the reason as an open question; an investigator crash leaves the case with the analyst marked incomplete, never stuck in "investigating" | `tests/test_g1.py`, `tests/test_llm.py`, `tests/test_review_round4.py` |
| Structured handoff | Queue, priority, verified facts, open questions | 100% of lane C cases complete in the evaluation |
| No money movement, no promises | No tool moves money; card block needs the customer's yes; drafts are checked against forbidden promises | `tests/test_investigator_g2.py` |
| Investigator citations | Every cited id must exist in this run's tool results (the check verifies the id, not the claim: a wrong record of the same customer would pass); unsupported ids removed and counted | 100% valid citations in the evaluation, by construction: the deterministic policy only cites its own results |
| Data minimization | Redaction before any model call or storage; gold tables carry no document, e-mail, phone, address or full product number | `tests/test_pipeline_gateway.py::test_gold_has_no_pii_columns` |
| Tracing and audit | One JSONL event per step (rule/model and version, tool, latency, tokens, cost, error code) | Trace view `#/traza`, `scripts/trace_report.py` |

## 5. Run it and try it

### Run it

Python 3.12 (3.11 works), Node 24. On Windows without `make`, run the command of each target (see
[`docs/como_correr_local.md`](docs/como_correr_local.md), which also has the time each step takes).

```bash
python -m venv .venv && source .venv/Scripts/activate     # Linux/macOS: .venv/bin/activate
make install          # pip install -r requirements.txt
make data             # real bucket with the S3 key in .env; otherwise the SYNTHETIC replica (D15)
make pipeline         # bronze -> silver -> 194 quality contracts -> gold -> serving export
make train            # M1, M2, M3 (+ reports in docs/modelos/)
make eval             # 240 planted scenarios, v2 vs B1 -> docs/evaluacion.md and the evaluation page
make up               # builds the front and serves API + front at http://127.0.0.1:8000
```

The local server runs the full v2 configuration by default (gold customers when `data/gold` exists, trained M1
and M2, G2 investigator); `python scripts/local_api.py --config b1` runs the v1.4 rules-only version to compare.
`python scripts/learning_loop.py` runs one learning-loop cycle (simulated analyst decisions → labels → M1
candidate → promoted only if better on the frozen test). `python scripts/llm_smoke.py` checks a real model once
credentials are set. Tests: `make test` (pytest) and `cd frontend && npm test`.

### Try it as a judge

At http://127.0.0.1:8000 (no deployed URL, see the status box):

1. The landing page shows the six customers with their expected lane; one click opens a signed 15-minute session.
2. Lucía: send the suggestion, "Es este", "No lo reconozco". The live case card goes to "Caso abierto", then
   "Investigando" → "En revisión" when the G2 report is ready.
3. Analyst console (`#/consola`, token printed by `local_api.py` on start): open Lucía's case: said vs verified,
   rule and rules version, the G2 report with citations (each opens its record). Approve and send.
4. Back in Lucía's tab: "aprobado por un analista" with the sealed text.
5. João (PT) the same in Portuguese; Martina blocks her card after "yes"; Carlos goes straight to a person.
6. Failure switches on any customer: tools down, model slow, expire session, fast clock; probes: injection and
   "another customer's charges".
7. Trace view (`#/traza`) and evaluation page (`#/evaluacion`, fed by `make eval`).

## 6. Evaluation

`make eval` runs **240 planted scenarios** end to end through the same app the chat uses (orchestrator, gateway,
lane rules, case, lifecycle, investigator) with a simulated customer: ground truth planted over gold customers and
transactions, 115 in Spanish and 125 in Portuguese, MX 107 · CO 79 · AR 54, 36 adversarial (24 injection, 12
another customer's data) plus 14 with a charge that doesn't exist. The same scenarios run with **v2** and with
**B1**, the v1.4 rules-only version (keyword triage, rule ranking, no investigator). Data: **synthetic replica**
(D15). Every rate has its n and a Wilson 95% interval in [`docs/evaluacion.md`](docs/evaluacion.md); the
evaluation page (`#/evaluacion`) reads the same report.

**What this evaluation can and cannot show.** The expected lane of each scenario is the same rule file applied to
the planted facts, and the simulated customer knows the planted charge: it never confirms a wrong one, never
rejects an explanation and never asks for a person. So "correct lane", "correct charge" and "0 incorrect closures"
hold largely **by construction**: they show that the orchestrator, the gateway and the rules do not break each
other on 240 planted stories, not how the system decides with real customers. What can fail, and did not: the
regulator and lost-card detection, the unsafe outcomes (wrong charge filed, another customer's data, action from
injected text) and the convergence of each conversation. The rules `identity_not_verified`, `asks_for_human`,
`asks_compensation`, `complaint_about_person` and `explanation_not_accepted` are not exercised by the scenarios.

| Metric (plan v2 §11) | v2 | B1 rules-only | Target |
|---|---|---|---|
| Safe automated resolution | **41.6%** (79/190) [34.8–48.7] | 40.5% (77/190) [33.8–47.6] | ≥ 30% with 0 incorrect closures |
| Incorrect closures | **0/190** | 0/190 | 0 |
| Correct lane | 100% (190/190) | 99.0% (188/190) | — |
| Missing critical handoffs · unnecessary handoffs | 0/190 · 0/190 | 0/190 · 2/190 | 0 · ≤ 10% |
| Handoff package complete | 100% (62/62) | 100% (64/64) | 100% |
| Unsafe: wrong charge filed · another customer's data · action from injected text | **0/194 · 0/240 · 0/24** | 0/194 · 0/240 · 0/24 | 0 each |
| Unsafe probes: tools asked for other customers' records · data after session expiry | 0 leaks in 204 · 0 of 3 | — | 0 |
| Wrong charge proposed as "this one" | 0/163 | 0/161 | < 1% |
| First offer is the right charge, proposed directly | 92.0% (150/163) | 93.8% (151/161) | — |
| Investigator recommendation matches the planted truth (deterministic policy, no model) | **98.0%** (48/49) | no investigator in this run (its table-driven stub scored 49/49 in an earlier run) | better than an empty case |
| Investigator citations valid · reports with unsupported claims removed | 100% (63/63) · 0/63, by construction | — | 100% valid |
| Latency per chat turn, p50 / p95 (local, no model) | 22 / 117 ms | — | p95 < 6 s |
| Investigator per case, p50 / p95 (deterministic policy) | 79 / 177 ms | — | p95 < 90 s |

Model results on held-out sets (synthetic; details in [`docs/modelos/`](docs/modelos/)):

| Model | Result | Compared with |
|---|---|---|
| **M1** ranker `m1-lgbm-1`, 8,000 frozen test queries (time split by charge date; same query generator and same customers as training) | top-1 **75.5%** [74.5–76.5], recall@3 97.1%, wrong confirmations 0.35%, "no such charge" detected 96.6% | v1.4 rule: 71.1% [70.0–72.2], 95.8%, 2.9%, 79.8%. Top-1 is the comparable figure (disjoint intervals), so M1 is primary. The confirmation figures are not comparable: M1's confirm threshold was chosen on validation for ≤ 0.9% wrong confirmations (it confirms directly 23.9% of the time vs 35.6%), the rule's thresholds are fixed |
| **M2** intent `m2-hashlr-1`, 180 messages written apart from the generators (30 adversarial; 12 of them are still close to generated training messages, Jaccard > 0.5, so 0.935 is optimistic) | macro-F1 **0.935** [0.897–0.968], ES 0.937 / PT 0.927; manipulation caught 91.7%, none accepted as another intent | keywords: 0.495 [0.411–0.565], 47.2%, 11 accepted. B2 zero-shot LLM not run (no model) |
| **M3** risk evidence, frauds the `fraud_score > 30` rule misses | PR-AUC 0.0016 vs 0.0027 for `fraud_score`; difference CI [-0.0057, 0.0003]: **no lift** | So M3 stays evidence for the analyst and the rule stays; the same negative result as the real data (AUC 0.50) |
| **Learning loop**, one cycle with simulated analyst decisions ([`docs/modelos/ciclo_aprendizaje.md`](docs/modelos/ciclo_aprendizaje.md)) | 240 new scenarios → 464 labels → 107 labelled M1 queries → candidate top-1 75.3%, wrong confirmations 0.88%: **not promoted** | Current M1 on the same frozen test (75.5%, 0.35%): the gate keeps a candidate that is less safe out. One cycle with labels derived from the planted truth (107 queries, 0.36% of the training set) demonstrates the pipeline, not learning: the candidate's shift is the threshold re-selection |

How to read it:

- **v2 against B1.** On the planted scenarios v2 and B1 resolve the same 77 of 190 cases; v2 resolves two more
  (SC-130 and SC-133, identical maintenance fees that the rule ranker never found and M1 did). 41.6% vs 40.5%,
  100% vs 99% correct lane and 0 vs 2 unnecessary handoffs are that same pair of cases; the difference is not
  significant. B1 is slightly better at proposing the right charge directly (93.8% vs 92.0%) because M1 asks more
  often. The scenario messages are close to the templates M2 was trained on, so the end-to-end run does not
  stress M2 or the extractor; that is why keyword triage routes the same.
- **Where the models beat the rules.** M1 ranks the right charge first in 75.5% vs 71.1% of 8,000 synthetic
  queries (disjoint intervals); its fewer wrong confirmations (0.35% vs 2.9%) come mostly from a threshold chosen
  for that goal. M2 nearly doubles intent macro-F1 on messages written apart from the generators (0.935 vs
  0.495) and catches 91.7% of manipulation attempts against 47.2% for the keyword guard; the review tried four
  new attempts and the patterns alone caught one, M2 all four.
- **The investigator.** In this run it is the deterministic policy, not a model. In 46 of the 49 scored cases it
  recommends what the lane rule already implies (duplicate not reversed → chargeback, fee off schedule →
  reverse); it gets 2 of the 3 real frauds; the 14 near-identical cases have no planted truth and are not scored.
  Every citation is an id the tools returned, by construction. The value it adds over B1's empty case is the
  gathered evidence and the draft, which the analyst edits or rejects.
- **Fairness.** Lane and charge accuracy are identical across ES/PT and MX/CO/AR. Safe automated resolution
  differs (ES 45.8% vs PT 37.2%; AR 53.3% vs CO 36.8%) only because the share of scenarios that should end in lane
  A differs by group; among those, every group resolves 100%. The report computes these gaps and their explanation.
- **Security probes.** The 204 cross-customer tool calls, the expired-session and the other-session probes call
  the gateway and the app in process, not over HTTP (`tests/test_api.py` covers the HTTP layer). "0 of 3" for
  the expired session is n = 3: its Wilson interval reaches 56%, so it proves little by itself.
- **Not run.** G1 and G2 with a real model (they ran on the mock and the deterministic policy), the G3 judge (needs
  a model and 50 replies scored by two people: `python scripts/judge.py --prepare` writes the sheet) and B2.
  The M2 test set was written by a separate agent, not by the team as the plan asked (declared in
  [`docs/modelos/m2.md`](docs/modelos/m2.md)).

## 7. Data engineering

- **Pipeline** (`src/pipeline/`, `make pipeline`): raw CSV → **bronze** (every column as text plus lineage:
  source file, partition date, ingestion time; a header that differs from the table's first file stops the run)
  → **silver** (the v1.4 SQL: typing, normalisation, de-duplication, `dq_` flags, FX normalisation) → **quality**
  (194 contracts: keys, allowed values, ranges, foreign keys, business rules, freshness) → **gold** (14 tables) →
  DynamoDB import file. `run_partition(table, date)` reprocesses one daily partition (idempotent in row counts;
  the Lambda unit; late corrections replace the partition). It rebuilds the 14 gold tables in full and does not
  run the quality contracts, which stay a `make pipeline` step; silver de-duplicates within the partition, so a
  correction that lands in another partition is caught by the next `make pipeline`, not by `run_partition`.
  `backfill` does all partitions in one DuckDB statement (not yet run at the real data's scale: `digital_events`
  alone is 15.6 M rows).
- **Quality**: on the replica 194 checks, **164 PASS, 30 WARN, 0 FAIL** ([`docs/data/dq_report.md`](docs/data/dq_report.md));
  on the real data (v1.4): 194 checks, 159 PASS, 35 WARN, 0 FAIL ([`docs/v14_datos_reales/dq_report.md`](docs/v14_datos_reales/dq_report.md)).
  WARN rows are the dataset's known defects (transactions before product opening, cards used after expiry, shared
  e-mails, Mexican "DNI" documents, complaint products of other customers…), documented, never silently fixed.
- **Gold** ([`docs/data/gold_tables.md`](docs/data/gold_tables.md)): `customer_profile`, `customer_products`,
  `transactions_enriched`, `fee_schedule` and `fee_charges` (synthetic, labelled), `customer_baseline`,
  `customer_category_baseline`, `merchant_stats`, `complaints_baseline`, `contact_history`, `digital_sessions`,
  `kpi_baseline`, `cc_category_baseline`, `labels`. No document, e-mail, phone, address or full number in any of them.
- **Synthetic replica** (`src/etl/synth_dataset.py`, D15): 4,000 customers (1/37.5 of the real 150,000), three
  years, same 13 tables, columns and S3 layout, and the generator rules the v1.4 analysis found (independent
  draws plus a few exact rules; fraud rate scaled to 0.4% to keep ~450 frauds). It is a stand-in for running every
  stage, not a substitute for the real data, and it is less faithful than the rules suggest: ~30 digital events
  per customer against ~104 real, campaign sends at half the real rate, five real defects absent (that is why the
  replica has 30 WARN and the real data 35), and shared e-mails or placeholder transcripts at different rates
  (the module docstring lists every deviation).

## 8. Costs

Plan v2 §10 estimates (list prices; Bedrock bills separately): ≈ 2 USD/month idle, 75–200 USD for the hackathon,
≈ 190 USD/month for 10,000 conversations in production.

Per conversation, from `make eval` ([`docs/evaluacion.md`](docs/evaluacion.md)):

| | Value | How it was obtained |
|---|---|---|
| Measured in this build | 0 USD | G1 ran on the mock provider and G2 on the deterministic policy: no model calls were made |
| **Projected** chat (G1, Claude Haiku 4.5) | ≈ 0.0025 USD per conversation | List prices (`src/llm/prices.yaml`) × prompt sizes measured from the rendered prompts × the evaluation's free-text turns |
| **Projected** investigator (G2, Claude Sonnet 5) | ≈ 0.061 USD per investigated case | Same method, with the tool calls of each report and the agent loop re-sending its growing context |
| **Projected** total | **≈ 0.02 USD per conversation** (26% of conversations investigated, adversarial ones in the denominator) | Target < 0.05 USD. An order of magnitude (0.01 to 0.04), not a bound: the projection assumes 4 characters per token, no extended thinking (Sonnet 5 thinks adaptively by default and bills it as output), no retries and no prompt caching |

The projection is an estimate, not a bill; the first real numbers come from `python scripts/llm_smoke.py` and
the traces (`tokens_in`, `tokens_out`, `cost_usd` per step) once a model is configured.

## 9. Path to production

| Area | Today | Needed |
|---|---|---|
| Data | Synthetic replica in this build; real complaints not linked to transactions | Run on the real data; real case-to-transaction links; real conversation logs; drift monitoring |
| Models | M1/M2 trained on generated queries and text; M3 shows no lift | Retrain on real analyst decisions (the learning loop runs on simulated ones); monitor calibration live |
| GenAI | G1 mock, G2 deterministic policy without credentials | Bedrock access (D5 models), G3 judge validated on 50 hand-scored replies, prompt caching |
| Deployment | CDK + cloud adapters tested offline; nothing deployed | IAM user (D10), deploy through CDK (D12), import the serving table, Cognito analyst users, alarms |
| Integrations | Simulated identity and core | Real authentication and step-up, core banking and case system, card-network chargebacks, per-country rules (MX, CO, AR), retention and deletion |
| Language | Portuguese generated, no native review | Native PT data and review; regional Spanish variants tested |
| Risks | Offline evaluation only | Analyst over-reliance (track acceptance), adversarial users at scale, model version changes, fairness by country monitored live |

## 10. Decisions

[`DECISIONS.md`](DECISIONS.md) (append-only, rationale in the decider's own words). The ones that shaped the system
most: **D1** go deep on the two money complaint types; **D2** models understand and rank, YAML rules decide, the
gateway enforces permissions; **D3** ML only where the data has ground truth (M1, M2, M3 + learning loop);
**D7** evaluate on planted scenarios against the v1.4 rules-only version; **D14** Arturo finished every
unfinished owner's work; **D15** run every stage on a synthetic replica when the S3 key is missing.

## 11. Declared limits

- The data in this build is a **synthetic replica**; numbers labelled `synthetic_replica` are not dataset results.
- All conversation text is generated and labelled synthetic; Portuguese has no native review.
- The case lifecycle and the bank's core (card blocks) are simulated; nothing moves money.
- The learning loop is demonstrated once, with simulated analyst decisions.
- G1 and G2 ran without a real model in the reported evaluation; G3 (judge) and the zero-shot baseline B2 did not run.
- Latency was measured locally (one process, no network, no cold start); costs are projections from list prices.
- Nothing is deployed: there is no public URL; the CDK app and the cloud adapters are tested offline only.
- The M2 test set (180 messages) was written by a separate agent, not by the team; its labels still need a human review.
- The planted evaluation holds largely by construction (section 6): the expected lane is the rule file applied to
  the planted facts and the simulated customer knows the planted charge. v2 and B1 differ in two scenarios.
- The investigator numbers are those of the deterministic policy; its citation check verifies ids, not claims.
- The security probes run in process, not over HTTP; "session expired 0 of 3" is three attempts.
- The replica deviates from the real data in volumes and defects (section 7); its B0 matches the real one by
  construction. The real-data path (`make data` with the key) has not been run at scale by the v2 pipeline.
- The lane rules consume M2's class (above its accept threshold) and G1's flags as data: three lane C rules can
  fire on a model output alone (section 2).

## 12. Pre-existing components and licences

The coordination files (`RUNBOOK.md`, `STATUS.md`, `DECISIONS.md`, `INTERFACES.md`, `AGENTS.md`, `CLAUDE.md`), the
agent skills in `.agents/skills/`, the `Makefile` contract, the CI workflow, `.gitattributes`, `scripts/licences.py`
and the templates in `templates/` were adapted from the team's pre-event `nextwave-kit` repository (files copied,
no shared history). The silver SQL and the data contracts come from Arturo's v1.4 pipeline
([Arturo-GA/Hackathon-Factored](https://github.com/Arturo-GA/Hackathon-Factored)). Third-party packages and their
licences: [`LICENCES.md`](LICENCES.md) (regenerate with `python scripts/licences.py`).

## 13. Review

An independent review of this build on 1 Nov 2026 (tests run, demo driven in a browser, code read area by area)
is summarised in [`docs/revision_v2.md`](docs/revision_v2.md): what held, what held only by construction, and
what was changed afterwards (investigator fallback and lifecycle containment, G1 prompt roles, M2 as the default
classifier with its class gated by confidence, Wilson intervals, Lambda packaging, CloudFront and Cognito for the
console, and the wording of sections 1, 2, 4, 6, 7 and 8 above).

## 14. Team

Plan v2 split the work between Andrés (AWS platform, gateway, LLM client, investigator), Diego (data engineering),
Cristhian (ML and evaluation) and Arturo (conversation, rules, front end, coordination). Arturo built the chat
API, the conversation backend and the React front end ([PR #1](https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1)) and then, after the rest of the team did not
deliver, finished every remaining stage with his coding agent (D14): pipeline and gold tables, gateway, models,
LLM client, investigator, evaluation, trace queries and the evaluation page.
