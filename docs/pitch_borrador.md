# Pitch draft: Expediente Vivo v2 (Team Datti)

> **Nota para el equipo.** Borrador del 29 sep sobre `templates/pitch.md` del repo del equipo. Slides y guion en
> inglés; las notas en español son para nosotros y no van en las slides. Regla del template: todo número tiene que
> coincidir con `docs/eval/` y con el README. Hoy **no hay evaluación**: toda cifra de resultados es
> `TODO(<nombre>)`. Las cifras del problema salen del análisis v1.4 (fuentes en `docs/README_borrador.md` §1).
> Los recorridos del guion se probaron en local el 29 sep con `scripts/smoke_api.py` (27 PASS, 2 SKIP, 0 FAIL,
> más SLA y vencimiento de sesión en servidores aparte); en la URL desplegada nada está probado (UNVERIFIED).
> Se graba el domingo 4 oct sobre el build congelado.

Format: 6 slides + a 5:15 video (range allowed: 4–6 min).

---

## Slides

### Slide 1 · The problem

**Title:** Complaints are where LATAM Bank loses customers

**Message:** A complaint gets the worst first-contact outcome of any contact reason and then waits weeks.

- 43.6% of complaint contacts are resolved at first contact, the lowest of six contact reasons (CSAT 2.43)
- Formal complaints: 38 h median to first response, 16 days median to resolution (p90 28), 20,125 never assigned
- No complaint arrives complete: 33% carry an amount, and every referenced product belongs to another customer

**Image:** four big numbers (43.6% · 38 h · 16 days · 20,125) over a light bar of "686,296 contacts · 67,095 complaints · 4.4M transactions, 3 years". Source line at the bottom: "LATAM Bank dataset, full population, team analysis v1.4".

> Notas: fuentes exactas en el README §1 (`docs/hallazgos.md` §1, `docs/analisis_quejas.md` §3, §5, §7;
> 38 h en `docs/proyecto_expediente_vivo.md`). No decir "clientes perdidos" como dato: el título es retórico;
> si molesta, cambiar a "Complaints are the slowest, least-resolved contact at LATAM Bank".

### Slide 2 · What we built

**Title:** Expediente Vivo: data prepares, ML ranks, rules decide, GenAI explains, people approve

**Message:** A controlled system, not a chatbot: the model never chooses what happens to a customer.

- Three lanes chosen by versioned YAML rules: **A** resolve now with the record · **B** case with a promised date and an investigator report · **C** a person now, with a structured file
- The gateway only reads the signed-in customer's own data; nothing moves money; a card block needs the customer's explicit "yes"
- Every decision an analyst makes becomes a label to retrain the models

**Image:** the customer-flow diagram from `docs/architecture.md` (8 steps with M1, M2, M3, G1, G2 on the side), redrawn with the five colors of the principle (data, ML, rules, GenAI, people).

> Notas: ser honestos en voz y en la slide sobre qué es hoy regla o stand-in: M1 = regla v1.4 (`m1-rule-0`),
> M2 = palabras clave (`m2-keywords-0`), G1 = proveedor mock, investigador = stub determinista.
> `TODO(Cristhian, Andrés)`: actualizar según lo que quede en el build congelado del sábado. Si M1 o M2 llegan,
> la slide dice "ML ranks"; si no, "rules rank today; ML is evaluated as an experiment" (regla de corte del plan §15).

### Slide 3 · Live demo path

**Title:** Six prepared customers, Spanish and Portuguese

**Message:** Judges can drive every required case themselves in under five minutes.

- Normal: Lucía (ES·MX) "como 450 en el super el 12" → the exact charge → A if she recognizes it, B with case `EV-…` and date if not
- Ambiguous and unsupported: Sofía (ES·CO) picks among three near-identical charges, then asks for a loan → out of scope, offered a person
- Human required: Martina (ES·AR) fraud_score 82 → lane C, fraud queue, card blocked only after "yes"; João (PT) fee above schedule → B, analyst edits the Portuguese reply

**Image:** screenshot of the judge landing page (six customer cards with expected lane) + the deployed URL as a QR code. `TODO(Andrés)`: URL. `TODO(Arturo)`: screenshot from the frozen build.

> Notas: plan B si la URL falla en vivo: el video grabado (escena 2 a 6) y el modo local
> (`python scripts/local_api.py --port 8000`). El template menciona a Lucía, Sofía, Martina y João; Andrés
> (cliente demo) y Carlos van en el video. Ojo con decir "Andrés" en voz: aclarar "Andrés, a demo customer".

### Slide 4 · Evidence it works

**Title:** Measured on held-out planted cases, against the rules-only version

**Message:** `TODO(Cristhian)`: one sentence with the main result (e.g. safe automated resolution and unsafe outcomes).

- Safe automated resolution: `TODO(Cristhian)` v2 vs `TODO` rules-only, n = `TODO`, 95% CI; unsafe outcomes `TODO` / n
- Escalation quality: missing handoffs `TODO`, unnecessary `TODO`; matching top-1 and wrong confirmations `TODO(Cristhian)`
- Latency p50/p95 per turn `TODO(Andrés, Arturo)` · cost per conversation `TODO(Andrés, Cristhian)` · by language ES/PT and country MX/CO/AR `TODO(Cristhian)`

**Image:** the metrics table from the evaluation page (`#/evaluacion`) or from `docs/eval/`, with n and intervals visible; one negative result in the table (e.g. M3 no lift) if that is what we found.

> Notas: hoy solo hay pruebas funcionales (1103 pytest, 232 vitest, smoke 27 PASS + 2 SKIP en local, 29 sep 10:22–10:35, árbol sin commit). **No usarlas como
> evaluación.** Si el sábado no hay reporte, esta slide dice "Evaluation design" (sets, baselines, métricas del
> README §6) y declara "results pending". Separar medido / simulado / proyectado, como pide el enunciado.

### Slide 5 · Controls and failures

**Title:** Permissions and limits live in code, and judges can break things on purpose

**Message:** Every failure has a safe path, and the trace shows which rule fired.

- Outside the model: customer scope from the signed session; another customer's case → 403; injection → template reply, no tools, flagged
- Failure switches: transactions tool down → 2 retries then lane C, case marked incomplete; model slow → fallback at 8 s; session expired → 401, no data
- Audit: one trace line per step (rule and version, model and version, latency, tokens, cost, error code), no free text; SLA timers alert at 24 h unassigned, 80% of SLA and breach

**Image:** split screenshot: judge panel with the switches on the left, trace view of the same turn on the right showing `tool_failure` and the retries.

> Notas: todo esto existe y está probado en local (README §4 con archivo y test por fila). En la nube:
> Cognito para la consola y DynamoDB son UNVERIFIED, `TODO(Andrés)`.

### Slide 6 · What it would take to go live

**Title:** From a controlled demo to a bank

**Message:** The control design carries over; identity, core banking, data and Portuguese need real work.

- Integrations: real authentication and step-up, core banking and case system, card-network chargebacks, per-country rules (MX, CO, AR), retention and deletion
- Data and language: real complaint-to-transaction links and conversation logs; native Portuguese data and review (today generated, no native review)
- Operations: quota increases and load tests, fairness by country monitored live, analyst acceptance rates tracked; estimated ≈ 190 USD/month for 10,000 conversations (plan v2 §10, estimate)

**Image:** a three-column "Today · Needed · Owner" table (README §9), or the cloud diagram from `docs/architecture.md` with the simulated pieces greyed out.

> Notas: el costo es estimación del plan, decirlo así en voz ("we estimate"). `TODO(Andrés)`: gasto real de AWS y
> Bedrock al entregar, si se quiere mostrar.

---

## Video script (5:15)

Voice-over in English. Customer messages stay in Spanish or Portuguese on screen, with an English subtitle.
Screen: the deployed URL (`TODO(Andrés)`); if it isn't stable on Sunday, record against the local build and say so
on screen ("recorded on the local build").

| Time | Scene | On screen | Voice-over (EN) | Notas (ES) |
|---|---|---|---|---|
| 0:00–0:25 | 1 · Hook | Slide 1 numbers | "At LATAM Bank, a complaint is the contact least likely to be solved on the first try: 43.6%. A formal complaint waits a median 38 hours for a first answer and 16 days to close, and 20,125 were never assigned to anyone." | Cifras del README §1. No agregar otras. |
| 0:25–0:55 | 2 · What we built | Slide 2 diagram | "Expediente Vivo handles charge and fee disputes in Spanish and Portuguese. Data prepares the facts, ranking picks the charge, versioned rules decide the lane, the language model only reads and explains, and people approve anything that touches money." | Si M1/M2 no llegan, decir "a rule ranks the charge today". |
| 0:55–1:45 | 3 · Normal path, Lucía (ES·MX) | Judge landing → Lucía → message "me cobraron como 450 en el super el 12" → "Es este" → "No lo reconozco"; live case card fills; case `EV-…` and promised date | "Lucía writes the way people do. The system finds the one charge that fits among her own transactions and shows the record. She doesn't recognize it, so a rule opens a case with a number and a promised date, in seconds instead of 38 hours." Then 5 s cut to the other branch: "Had she recognized it, it closes here, lane A." | Mostrar la tarjeta en vivo (Entender → Encontrar → Verificar → Caso abierto). La fecha prometida es el p90 histórico (28 días). Ojo: el mensaje sugerido del modo juez es otro ("Hola, me salió un cobro como de 450 en el súper el 12 y no lo ubico"); los dos llegan al mismo cargo (smoke y `tests/test_integration_m1.py`). Si se usa el botón de sugerencia, cambiar el texto en pantalla. |
| 1:45–2:15 | 4 · Ambiguous and unsupported, Sofía (ES·CO) | "Buenas, me cobraron algo raro" → system asks; "Fueron como 52 mil de un domicilio el 9" → three charges as buttons → picks one; "Y de paso, quisiera pedir un préstamo" → out-of-scope reply | "When the system isn't sure, it asks, and offers the three charges that fit instead of guessing. A loan is out of scope: it says so plainly and offers a person. It never sells and never promises conditions." | Mensajes sugeridos del modo juez (`frontend/src/demo/customers.ts`). |
| 2:15–2:55 | 5 · Human required, Martina (ES·AR) + Carlos | Martina: "Me aparece un consumo rarísimo en la tarjeta, creo que me la clonaron" → "Fueron 145 mil en ELECTRO MUNDO ONLINE el 15" → "Es este" → lane C → offer to block card •••• 7730 → "Sí, bloquear tarjeta" → status read back. 8 s of Carlos: regulator mention → lane C, high priority | "Martina's charge has a fraud score of 82. In this data a score over 30 was fraud every time, so a rule sends her straight to the fraud team. The card is blocked only after her explicit yes, and the status is read back from the core. Carlos mentions the regulator: straight to a person, high priority, with the file already built." | fraud_score > 30 = fraude en 2,373 de 2,373 (README §1). Carlos también tiene 3 reclamos previos. |
| 2:55–3:45 | 6 · Analyst console + Portuguese, João (PT) | Second tab `#/consola`: queue with SLA; open Andrés (demo customer) duplicate case: said vs verified, rule and rules version, investigator report with citations → click a citation → Evidence tab. Then João's case: "Editar", change the PT draft, reason, send. Back to João's chat: "Humano · aprovado por um analista" | "Lane B cases reach an analyst with the evidence already gathered: what the customer said, what the data verified, the rule that fired, and an investigator report where every claim cites a record. The analyst decides. João wrote in Portuguese; the case, the draft and the answer stay in Portuguese. Each decision becomes a training label." | Si el investigador sigue siendo el stub, la consola lo muestra como "provisional"; decirlo o grabar cuando exista G2 (`TODO(Andrés)`). No leer el texto portugués en voz si nadie lo pronuncia bien. |
| 3:45–4:20 | 7 · Break it on purpose | Judge panel: switch "Herramienta de transacciones caída" → next message → lane C `tool_failure`, "Herramienta no disponible". Then paste the injection probe → template, no buttons. Then trace view `#/traza` of those turns | "Judges can break it. Take the transactions tool down: two retries, then the case goes to a person, marked incomplete. Paste an injection: it's treated as data, gets a fixed reply, and is flagged. The trace shows every step: which rule, which version, how long, and what failed. No hidden reasoning, just a record." | Opcional 5 s: "Modelo lento" (espera 8 s reales; mejor cortar en edición). Reloj acelerado solo si sobra tiempo (15 min reales). |
| 4:20–4:50 | 8 · Evidence | Evaluation page or slide 4 | `TODO(Cristhian)`: "On `n` held-out planted cases, v2 resolved `X`% safely against `Y`% for the rules-only version, with `Z` unsafe outcomes out of `n` …" | Solo cifras del reporte final. Si no hay reporte: "Evaluation runs on planted cases against the rules-only baseline; results are in the README." |
| 4:50–5:15 | 9 · Architecture and path to production | Cloud diagram (`docs/architecture.md`) → slide 6 | "It runs serverless on AWS and costs almost nothing when idle. To go live it needs real identity and core banking, real complaint data, and native Portuguese review. The controls are already in code. The repo, the deployed tool and this README tell you exactly what is measured and what is not." | Decir "serverless on AWS" solo si Andrés desplegó y se probó (hoy UNVERIFIED). Si no: "designed for serverless AWS; this recording runs locally". |

Total: 5:15. If it runs long, cut scene 5's Carlos segment and shorten scene 7 to one switch.

---

## Qué falta y quién lo aporta

| Falta | Para | Quién |
|---|---|---|
| URL desplegada y estable (prod), Cognito para la consola, store DynamoDB | Slide 3, escenas 3–7, README "Try it" | Andrés (UNVERIFIED hoy) |
| G2 real (o decisión de mostrar el stub como "provisional") | Escena 6, slide 2 | Andrés |
| Reporte de evaluación: resolución segura, contención, escalamiento, unsafe con n, M1/M2 (y M3 negativo si toca), por idioma y país, con IC | Slide 4, escena 8, README §6 | Cristhian |
| Latencia p50/p95 por turno en la URL y costo por conversación medidos | Slide 4 | Andrés, Arturo (medición), Cristhian (reporte) |
| Estado de M1 y M2 en el build congelado | Slide 2, escena 2 | Cristhian |
| Pipeline final, gold tables y escenarios plantados; página de evaluación con datos | README §7, escena 8 | Diego |
| Gasto real de AWS/Bedrock | Slide 6 (opcional) | Andrés |
| Capturas del build congelado, QR de la URL, grabación y edición, voz en inglés | Slides 1–6, video | Arturo |
