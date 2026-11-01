# Pitch: Expediente Vivo v2 (Team Datti)

> **Nota para el equipo.** Versión final sobre `docs/pitch_borrador.md` (29 sep). Slides y guion en inglés; las
> notas en español son para nosotros y no van en las slides. Todo número coincide con `docs/evaluacion.md` y con el
> README (corrida `make eval` del 29 oct). Tres cosas se dicen siempre, en la slide y en voz:
> 1. Los datos de esta corrida son una **réplica sintética** del dataset (no hubo llave S3 en esta máquina, D15).
>    Las cifras del problema (slide 1) sí son del dataset real (análisis v1.4).
> 2. **No hay URL desplegada**: la demo y el video corren en el build local (`python scripts/local_api.py`).
> 3. G1 y G2 corrieron **sin modelo real** (mock y política determinista); con credenciales usan Claude.

Format: 6 slides + a 5:15 video (range allowed: 4–6 min).

---

## Slides

### Slide 1 · The problem

**Title:** Complaints are the slowest, least-resolved contact at LATAM Bank

**Message:** A complaint gets the worst first-contact outcome of any contact reason and then waits weeks.

- 43.6% of complaint contacts are resolved at first contact, the lowest of six contact reasons (CSAT 2.43)
- Formal complaints: 38 h median to first response, 16 days median to resolution (p90 28), 20,125 never assigned
- No complaint arrives complete: 33% carry an amount, and every referenced product belongs to another customer

**Image:** four big numbers (43.6% · 38 h · 16 days · 20,125) over a light bar "686,296 contacts · 67,095
complaints · 4.4M transactions, 3 years". Source line: "LATAM Bank dataset, full population, team analysis v1.4".

> Notas: fuentes en el README §1 y `docs/v14_datos_reales/`. Estas cifras son del dataset REAL (v1.4), no de la
> réplica.

### Slide 2 · What we built

**Title:** Data prepares, ML ranks, rules decide, GenAI explains, people approve

**Message:** A controlled system, not a chatbot: no model ever chooses what happens to a customer.

- Three lanes chosen by a versioned YAML rule file: **A** resolve now with the record · **B** a case with a promised date and an investigator report · **C** a person now, with a structured file
- ML where the data has ground truth: M1 finds the charge (LightGBM ranker), M2 reads the intent (calibrated classifier), M3 adds risk evidence; G1 extracts details, G2 investigates with read-only tools and cited evidence
- The gateway only reads the signed-in customer's own data; nothing moves money; a card block needs the customer's explicit "yes"; every analyst decision becomes a label

**Image:** the customer-flow diagram from `docs/architecture.md` (8 steps; M1, M2, M3, G1, G2 on the side) in the
five colors of the principle (data, ML, rules, GenAI, people).

> Notas: decir en voz que G1 y G2 corrieron sin modelo en esta demo (mock y política determinista, mismo bucle y
> mismas herramientas) y que con `LLM_PROVIDER=anthropic|bedrock` usan Claude Haiku 4.5 y Sonnet 5.

### Slide 3 · Live demo path

**Title:** Six prepared customers, Spanish and Portuguese

**Message:** Judges can drive every required case themselves in under five minutes.

- Normal: Lucía (ES·MX) "como 450 en el super el 12" → the exact charge → A if she recognizes it, B with case `EV-…` and a date if not
- Ambiguous and unsupported: Sofía (ES·CO) picks among three near-identical charges, then asks for a loan → out of scope, offered a person
- Human required: Martina (ES·AR) fraud_score 82 → lane C, fraud queue, card blocked only after "yes"; João (PT) fee above schedule → B, the analyst edits the Portuguese reply

**Image:** screenshot of the judge landing page (six customer cards with their expected lane) from the local build.

> Notas: no hay URL desplegada. Plan: el video (escenas 3 a 7) y el modo local
> (`python scripts/local_api.py --port 8000`). Al decir "Andrés" (cliente demo) aclarar "Andrés, a demo customer".

### Slide 4 · Evidence it works

**Title:** 240 planted cases, against the rules-only version

**Message:** v2 resolves 41.6% of in-scope cases on its own with zero incorrect closures and zero unsafe outcomes, and its models beat the rules where the data is hard.

- Safe automated resolution 41.6% (79/190, CI 34.7–47.9) vs 40.5% rules-only; 0 incorrect closures, 0 wrong charges filed, 0 leaks of another customer's data, 0 actions from 24 injections
- M1 wrong "this is your charge" confirmations 0.35% vs 2.9% for the rule (8,000 held-out queries); M2 intent macro-F1 0.935 vs 0.495 (180 held-out messages); M3 showed no lift, so it stays evidence only
- Investigator: right recommendation in 48 of 49 cases, 100% valid citations; projected cost ≈ 0.018 USD per conversation (target < 0.05)

**Image:** the metrics table of the evaluation page (`#/evaluacion`) with n and intervals visible and the
"synthetic replica" label; M3's negative result in the same table.

> Notas: decir "on a synthetic replica of the dataset" en voz. La ganancia de v2 sobre B1 en los escenarios es
> chica (41.6 vs 40.5) porque ambos deciden la ruta con las mismas reglas y el cliente simulado es cuidadoso; la
> ganancia grande está en M1, M2 y el investigador. Decirlo así, sin inflar. Latencia local p95 117 ms por turno,
> sin modelo: no es la latencia de producción.

### Slide 5 · Controls and failures

**Title:** Permissions and limits live in code, and judges can break things on purpose

**Message:** Every failure has a safe path, and the trace shows which rule fired.

- Outside the model: customer scope from the signed session (0 leaks in 204 cross-customer tool calls); another customer's case → 403; injection → template reply, no tools, flagged
- Failure switches: transactions tool down → 2 retries then lane C, case marked incomplete; model slow → fallback at 8 s; session expired → 401, no data
- Audit: one trace line per step (rule and version, model and version, latency, tokens, cost, error code), no free text; SLA timers alert at 24 h unassigned, 80% of SLA and breach

**Image:** split screenshot: judge panel with the switches on the left, trace view of the same turn on the right
showing `tool_failure` and the retries.

> Notas: todo esto está probado en local (README §4, con archivo y test por fila). Nada en la nube.

### Slide 6 · What it would take to go live

**Title:** From a controlled demo to a bank

**Message:** The control design carries over; real data, identity, core banking and Portuguese need real work.

- Data: run the same pipeline on the real dataset (one command once the key is set), real complaint-to-transaction links and conversation logs, retrain M1/M2 on real analyst decisions
- Integrations: real authentication and step-up, core banking and case system, card-network chargebacks, per-country rules (MX, CO, AR); Claude on Bedrock with the G3 judge validated on 50 hand-scored replies
- Operations: deploy the CDK app (already written and tested offline), native Portuguese review, fairness by country monitored live; we estimate ≈ 190 USD/month for 10,000 conversations

**Image:** a three-column "Today · Needed" table (README §9), or the cloud diagram from `docs/architecture.md`
with the simulated pieces greyed out.

> Notas: el costo mensual es estimación del plan v2 §10; el costo por conversación de la slide 4 es proyección con
> precios de lista. Decir "we estimate".

---

## Video script (5:15)

Voice-over in English. Customer messages stay in Spanish or Portuguese on screen, with an English subtitle.
Recorded on the local build; a caption says so on screen ("recorded on the local build, synthetic data").

| Time | Scene | On screen | Voice-over (EN) | Notas (ES) |
|---|---|---|---|---|
| 0:00–0:25 | 1 · Hook | Slide 1 numbers | "At LATAM Bank, a complaint is the contact least likely to be solved on the first try: 43.6%. A formal complaint waits a median 38 hours for a first answer and 16 days to close, and 20,125 were never assigned to anyone." | Cifras reales (v1.4). No agregar otras. |
| 0:25–0:55 | 2 · What we built | Slide 2 diagram | "Expediente Vivo handles charge and fee disputes in Spanish and Portuguese. Data prepares the facts, a trained ranker finds the charge, versioned rules decide the lane, the language model only reads and explains, and people approve anything that touches money." | |
| 0:55–1:45 | 3 · Normal path, Lucía (ES·MX) | Judge landing → Lucía → suggestion → "Es este" → "No lo reconozco"; the live case card fills; case `EV-…` and the promised date | "Lucía writes the way people do. The system finds the one charge that fits among her own transactions and shows the record. She doesn't recognize it, so a rule opens a case with a number and a promised date, in seconds instead of 38 hours." Then 5 s on the other branch: "Had she recognized it, it closes here, lane A." | La tarjeta en vivo: Entender → Encontrar → Verificar → Caso abierto. La fecha prometida es el p90 histórico (28 días). |
| 1:45–2:15 | 4 · Ambiguous and unsupported, Sofía (ES·CO) | "Buenas, me cobraron algo raro" → the system asks; amount and day → three charges as buttons → picks one; "Y de paso, quisiera pedir un préstamo" → out-of-scope reply | "When the system isn't sure, it asks, and offers the three charges that fit instead of guessing. A loan is out of scope: it says so plainly and offers a person. It never sells and never promises conditions." | Mensajes sugeridos del modo juez. |
| 2:15–2:55 | 5 · Human required, Martina (ES·AR) + Carlos | Martina: suggestion → "Es este" → lane C → offer to block the card → "Sí, bloquear tarjeta" → status read back. 8 s of Carlos: regulator mention → lane C, high priority | "Martina's charge has a fraud score of 82. In this bank's data a score over 30 was fraud every time, so a rule sends her straight to the fraud team. The card is blocked only after her explicit yes, and the status is read back from the core. Carlos mentions the regulator: straight to a person, high priority, with the file already built." | fraud_score > 30 = fraude siempre en los datos reales (README §1). |
| 2:55–3:45 | 6 · Analyst console + Portuguese, João (PT) | Second tab `#/consola`: queue with SLA; open Andrés's duplicate case: said vs verified, rule and rules version, investigator report with citations → click a citation → Evidence tab. Then João's case: "Editar", change the PT draft, reason, send. Back to João's chat: "Humano · aprovado por um analista" | "Lane B cases reach an analyst with the evidence already gathered: what the customer said, what the data verified, the rule that fired, and an investigator report where every claim cites a record. The analyst decides. João wrote in Portuguese; the case, the draft and the answer stay in Portuguese. Each decision becomes a training label." | La consola marca el reporte como "sin modelo" (política determinista). Decirlo: "in this recording the investigator runs its deterministic policy; with model access it's Claude, same tools and checks". |
| 3:45–4:20 | 7 · Break it on purpose | Judge panel: "Herramienta de transacciones caída" → next message → lane C `tool_failure`. Then the injection probe → template, no buttons. Then the trace view `#/traza` | "Judges can break it. Take the transactions tool down: two retries, then the case goes to a person, marked incomplete. Paste an injection: it's treated as data, gets a fixed reply, and is flagged. The trace shows every step: which rule, which version, how long, and what failed." | Opcional: "Modelo lento" (8 s reales; cortar en edición). |
| 4:20–4:50 | 8 · Evidence | Evaluation page `#/evaluacion` | "We measured it on 240 planted cases on a synthetic replica of the dataset, against the rules-only version. v2 resolved 41.6% safely on its own, with zero incorrect closures and zero unsafe outcomes. The trained models beat the rules where it's hard: eight times fewer wrong charge confirmations, and intent accuracy nearly doubled. One model, risk scoring, showed no lift, so it stays evidence only. We report that too." | Solo cifras de `docs/evaluacion.md`. |
| 4:50–5:15 | 9 · Path to production | Cloud diagram (`docs/architecture.md`) → slide 6 | "It's designed for serverless AWS, and the infrastructure code is written and tested, though this recording runs locally. To go live it needs the real data, real identity and core banking, and native Portuguese review. The controls are already in code, and the README says exactly what is measured and what is not." | |

Total: 5:15. If it runs long, cut Carlos in scene 5 and keep one switch in scene 7.

---

## Antes de grabar

| Falta | Para | Cómo |
|---|---|---|
| Capturas del build local (landing, consola, traza, evaluación) | Slides 3–5 | `python scripts/local_api.py --port 8000 --investigation-delay 2` con el front compilado |
| Grabación y edición, voz en inglés | Video | Recorrido de la tabla; probar antes con `scripts/smoke_api.py` |
| (Opcional) Corrida con datos reales | Slide 4, README §6 | Llave S3 en `.env` → `make data pipeline train eval` |
| (Opcional) Corrida con modelo real | Slides 2 y 4 | `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` en el entorno → `make eval`, `python scripts/judge.py --run` |
| (Opcional) Despliegue | Slide 3 | Usuario IAM de Andrés (D10), `cdk deploy` (`docs/despliegue_aws.md`) |
