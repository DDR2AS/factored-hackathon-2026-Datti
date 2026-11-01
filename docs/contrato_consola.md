# Contrato de la consola del analista, ciclo de vida local e interruptores

29 sep 2026. Dueño: Arturo. Fuente de verdad de las formas: `frontend/src/api/types.ts` (espejo en `src/conversation/contract.py`; `tests/test_contract.py` falla ante cualquier diferencia de nombres, opcionales, nulos o valores de las uniones). Este documento explica reglas y comportamiento; si algo aquí contradice a `types.ts`, manda `types.ts` y se corrige este archivo.

Todo dato de la demo es sintético. Nada mueve dinero. La ruta la deciden las reglas del YAML, nunca un modelo. El sistema nunca promete reembolso ni compensación por su cuenta: solo un analista aprueba, y lo que se envía después lleva `approved_by_human: true` y la frase fija "En esta demo ningún dinero se mueve".

## Estado hoy

Hecho y probado en local (29 sep, sin commit): todo lo de este documento. Las tres rutas `/analyst/*` (`src/conversation/analyst.py`, enrutadas en `src/handlers/api.py`), el ciclo de vida local (`src/conversation/lifecycle.py`), el stub del investigador (`src/conversation/investigator_stub.py`), el lado servidor de los interruptores (`src/conversation/app.py`) y la exigencia de token en `scripts/local_api.py`. Pruebas en `tests/test_console_backend.py`; escenarios nuevos en `scripts/smoke_api.py` (`--analyst-token`, `--only console|switches`).

Lo que sigue siendo de otros: G2 real (Andrés; el stub es provisional), Step Functions (Andrés; `LIFECYCLE_BACKEND=stepfunctions` falla a propósito), Cognito para la consola en la nube (Andrés). La consola del front (`frontend/src/console`, `#/consola`) está hecha e integrada con estas rutas en local.

## Rutas del analista

Las tres ya existen en `infra/stacks/api_stack.py` (Andrés) detrás del autorizador JWT de Cognito, y en `ANALYST_ROUTES` de `src/handlers/api.py`.

`GET /analyst/cases?status=&lane=&language=&limit=&cursor=` → `AnalystCaseListResponse {items, next_cursor, as_of, poll_after_ms}`
- Filtros opcionales; `limit` 1..50, por defecto 20; `cursor` opaco (el `next_cursor` anterior). Parámetro desconocido o fuera de rango → 400 `invalid_request`.
- Orden: primero los casos que todavía esperan a alguien; los respondidos (`notified`, `closed`) van al final porque su SLA ya no corre (integración 29 sep). Dentro de cada grupo, prioridad `high` primero (fraude, regulador, tarjeta perdida, caso reabierto), luego `breach_at` ascendente con nulos al final, luego `created_at` ascendente (orden de llegada: una escritura posterior, como el reporte del investigador, no sube un caso) y por último `case_id` (revisión del 29 sep, CX-03). El cursor lleva esa clave de 6 elementos; un cursor con números no finitos es 400. La consola muestra "Respondido" en lugar del SLA para esos casos.
- Cada item: `case_id, updated_at, status, lane, lane_rule_id, queue, priority, language, country, display_name, intent_class, intent_p, subcategory, breach_at, has_report, report_reliable`. `queue` es el id interno (fraud, fees, regulator...), la UI lo traduce. `report_reliable` es null sin reporte y false si `citations_valid` es false o `removed_claims > 1` (`contract.report_reliable`).
- `poll_after_ms`: 15000 (`ANALYST_QUEUE_POLL_MS`). La consola sondea la cola a ese ritmo.
- Entran los casos de ruta B y los de ruta C (estos solo lectura). Los de ruta A resueltos en contacto no entran.

`GET /analyst/cases/{case_id}` → `AnalystCaseDetail {case, report, context, allowed_actions, version}`
- `case` es `AnalystCaseView` = todos los campos de #3 salvo `customer_id`, más `updated_at`, `resolution`, `lifecycle_step`, `rules_version` (versión del YAML de reglas que eligió la ruta; la consola la muestra junto a la regla), `conversation` (abajo) y `synthetic: true`. #3 no tiene documento, dirección ni teléfono y esta vista no los agrega. Tampoco lleva internos del ciclo de vida (ARN de ejecución, task token). `intent` viaja con la clave `class` (en Python el campo es `class_` con alias; se vuelca con `by_alias=True`).
- `report`: `InvestigatorReport` o null (casos sin investigador: ruta C, quejas no monetarias, o todavía investigando).
- `context`: `profile {display_name, country, segment, language}`, `cards [{card_last4, status}]`, `evidence_txns` (el cargo en disputa más sus duplicados, reversos y la fila de comisión, con la forma `EvidenceTxn` = `Txn` de #2 con `txn_type` y `reversal_of`), `prior_contacts`, `risk_evidence` y `unavailable`. El `customer_id` para leer esto sale del caso en el servidor, nunca del request (propuesta `GatewayContext.for_analyst`, abajo).
- `context.unavailable: ("cards" | "evidence_txns" | "prior_contacts")[]` (30 sep): las partes que no se pudieron leer porque una herramienta falló (por ejemplo `local_api.py --fail tools`). La parte queda vacía y aparece aquí, en ese orden; la consola debe mostrar "no disponible" en lugar de una lista vacía que parezca "sin tarjetas" o "sin contactos". `evidence_txns` entra si falla `get_transaction`, `find_duplicates` o `get_reversals`. Sin fallas, `[]`. Un caso sin cargo (ruta C sin transacción) tiene `evidence_txns: []` y no lo marca como no disponible.
- `case.conversation: ConversationTurn[]` (30 sep): la conversación del caso, REDACTADA (sin documento, dirección ni teléfono), de la más vieja a la más nueva. `ConversationTurn = {role: "customer"|"system"|"analyst", text, language, source: "template"|"model"|"human", at}`. Incluye los turnos de la sesión que abrió el caso desde la bienvenida (al abrirse el caso se copian todos), cada turno posterior de esa sesión y lo que envió el analista (`role: "analyst"`, `source: "human"`, el texto sellado tal como lo recibió el cliente). El texto del cliente es el mismo redactado que ve G1 y que va a `customer_statement`; un botón se guarda como `"[botón] <etiqueta>"` (`"[botão] …"` en PT); las respuestas del sistema son plantillas (`source: "template"`; `"model"` queda reservado, hoy ningún texto lo escribe un modelo). Es secundaria al paquete estructurado: la consola la muestra plegada y marcada como sintética, y el traspaso no vuelca el transcript. Nunca lleva turnos de otro cliente ni de otro caso (se guarda por `case_id` y solo la escribe la sesión dueña del caso) y nunca sale en la vista del cliente. Se guarda en el `ReviewStore` (`append_conversation`/`get_conversation`, propuesta a Andrés: un ítem por caso, tope de 200 turnos), fuera del `CaseRecord`, para que un "hola" del cliente no suba `version` y deje vieja la decisión que el analista está escribiendo. La sesión guarda sus últimos 60 turnos.
- `allowed_actions`: `["approve","edit","reject"]` solo si `status == awaiting_analyst`; si no, `[]`. Sin reporte con borrador, `approve` no aparece.
- `version`: entero ≥ 1 del caso; sube en cada escritura. Se devuelve tal cual en la decisión.
- Caso inexistente → 403 `not_authorized`, igual que cualquier otro 403 (no revela si existe).

`POST /analyst/cases/{case_id}/decision` con `DecisionRequest` → `DecisionResponse {case_id, status, analyst_decision, labels_emitted}`

| Acción | Obligatorio | Prohibido | Envía al cliente | Estado final |
|---|---|---|---|---|
| `approve` | — | `reply`, `next` | `report.draft_reply` tal cual + sello | `notified` |
| `edit` | `reply`, `reason` | `next` | `reply` + sello | `notified` |
| `reject` + `request_information` | `reason`, `next` | — | `reply` si viene, si no la plantilla `request_information_default` (30 sep: pide responder por el chat, ya no dice "te va a contactar"); + sello | `notified` |
| `reject` + `escalate` | `reason`, `next` | `reply` | nada | `handed_off` (sigue en ruta B, cola `senior`) |

- Forma inválida (lo de la tabla, textos vacíos, `reply` > 2000, `reason` > 500, campo desconocido como `decided_by`) → 400 `invalid_request`. Lo valida `DecisionRequest` en `contract.py`.
- `version` distinta de la actual, o el caso ya tiene decisión → 409 `conflict` (`retryable: false`). La consola muestra "ya decidido por … a las hh:mm" releyendo el detalle.
- Mismo `client_decision_id` con el mismo cuerpo → se devuelve la `DecisionResponse` guardada (reintento seguro). Mismo id con otro cuerpo → 409.
- 422 `precondition` (nuevo): el caso no está en `awaiting_analyst`; `approve` sin reporte o sin borrador; `reply.language` distinto del idioma del caso; aprobar o editar con reporte no confiable sin `evidence_reviewed: true`.
- `decided_by` = claim `sub` del JWT. Nunca del cuerpo.
- `labels.intent_confirmed: true` confirma la clase predicha; `labels.intent_class` la corrige (no ambas).
- El texto enviado queda en `resolution {language, text, sent_at, approved_by_human: true}` y `text` termina con `resolution_stamp` del idioma: "Respuesta aprobada por una persona del equipo. En esta demo ningún dinero se mueve." / "Resposta aprovada por uma pessoa da equipe. Nesta demonstração nenhum dinheiro é movimentado.". La barrera del servidor sobre el texto es esta: el borrador del investigador (G2 o el stub) debe pasar `templates.forbidden_hits` sin coincidencias antes de guardarse; el texto del analista no se bloquea por palabras (lo decidió una persona) pero siempre sale sellado. La consola avisa, a modo orientativo, si el texto promete reembolso o compensación.
- Auditoría: `analyst_decision` guarda `action, edited, reason, decided_by, decided_at, next, draft_text, sent_text` (los dos últimos no salen en la vista, quedan en el registro) y se emite un evento de traza `actor: "human"`, `name: "analyst_decision"` (`trace_id: lc-<case_id>`) sin texto libre: `input_ref = case:<id>:v<version>:by:<sub>` y `output_ref` con acción, next, edited, sha256 (16 hex) y largo del motivo, sha256 del borrador y del texto enviado, y las etiquetas. El motivo y los textos quedan solo en el caso (#7 no admite texto libre).
- `labels_emitted` (convención `tipo:valor[:detalle]`): `decision:<approve|edit|reject>`; `recommendation:<rec>:<accepted|edited|rejected>` si hubo reporte (approve acepta, edit edita, reject rechaza); `lane:<ruta>:<confirmed|escalated>` (escalated solo con reject + escalate); `reply:<as_drafted|edited|template>` cuando se envía algo (template = `request_information_default`); `intent:<clase>:confirmed` o `intent:<predicha>:corrected_to:<nueva>` si vino `labels`.

## Reporte del investigador (#6) y stub provisional

`InvestigatorReport` es exactamente #6 (`report_id, case_id, created_at, findings[{claim, evidence_ids[]}], hypotheses[{name, p}], recommendation, confidence, draft_reply{language, text}, open_questions[], citations_valid, removed_claims, tool_calls, latency_ms, model_id, prompt_version`) más `evidence_records: {evidence_id: {kind, summary, fields}}`.

- `hypotheses`: nombres de la lista cerrada de 6 (`fraud, forgotten_purchase, unfamiliar_merchant_name, duplicate, fee_error, pending_reversal`), cada uno a lo sumo una vez, ordenados por `p` descendente. El stub emite los 6.
- `recommendation`: una de `reverse_fee, open_chargeback, block_card, explain_and_close, request_information, escalate`. Es una propuesta: ninguna ejecuta nada.
- `evidence_records`: instantánea de cada registro citado, para abrir una cita sin otra ruta. `kind` ∈ `transaction, reversal, fee_schedule, customer_baseline, risk_evidence, digital_session, prior_contact, merchant_stats, card`. `fields` solo escalares, sin documento, dirección ni teléfono. Convención de ids: el id del registro cuando existe (`TX-…`, `DEMO-CT-…`, `DEMO-K-…`), `FEE:<product_id>:<fee_code>`, `RISK:<txn_id>`, `BASELINE`, `MERCHANT:<merchant_id>`. Nunca el `customer_id`.
- Validación en `contract.py`: si `citations_valid` es true, todo id citado debe estar en `evidence_records`.
- G2 es de Andrés. Hasta que llegue, el backend usa un STUB determinista con esta forma, marcado `model_id: "stub-g2-deterministic"` y `prompt_version: "stub-provisional"`, que la consola muestra como "Investigador provisional (sin modelo)". Mismo caso, mismo reporte. Recomendaciones del stub: duplicado sin reverso → `open_chargeback`; comisión fuera de tabla → `reverse_fee`; cobro de más en compra (`purchase_amount_disputed`) → `request_information`; no reconocido de bajo riesgo en un comercio sin compras previas en 90 días → `open_chargeback`, y en un comercio conocido → `request_information`; reverso ya aplicado o comisión que coincide → `explain_and_close`; lo demás → `escalate`. Herramientas: `get_transaction`, `find_duplicates`, `get_reversals` (por cada cargo del grupo), `get_fee_schedule` (solo comisiones), `get_customer_baseline`, `get_txn_history(90)` (solo compras; de ahí sale `MERCHANT:<id>`, la demo no tiene `get_merchant_stats`), `get_prior_contacts`, `get_cards`; cada intento cuenta en `tool_calls` (tope 12). `RISK:<txn_id>` sale del `fraud_score` del registro. `validate_citations(report, tool_results)` elimina los hallazgos sin cita o con ids que ninguna herramienta devolvió y los cuenta en `removed_claims`; `citations_valid` dice que toda cita que QUEDA en el reporte guardado tiene su registro en `evidence_records` (true tras la limpieza). Manda la regla del plan §6 (decidido en la integración del 29 sep): el reporte es no confiable si `citations_valid` es false o si se eliminaron más de uno (`removed_claims > 1`, `contract.report_reliable`, igual en `console/decision.ts`); un hallazgo eliminado se tolera y se muestra. Si una herramienta falla tras 2 reintentos, no hay reporte: el caso pasa a `awaiting_analyst` con `evidence_incomplete` y una pregunta abierta, y no se puede aprobar.

## Autenticación

Nube: API Gateway valida el JWT de Cognito (pool `analysts`, sin auto-registro, tokens de 1 h) antes de invocar la Lambda; los claims llegan en `event.requestContext.authorizer.jwt.claims`. Sin token o vencido, API Gateway responde 401 `{"message":"Unauthorized"}` sin pasar por el handler. El handler igual verifica: sin claims o sin `sub` → 401 con código `session_expired` (para la consola significa "volver a entrar"; el front ramifica primero por status). No hay bypass de autenticación en `src/`.

Local: `scripts/local_api.py` exige `Authorization: Bearer <LOCAL_ANALYST_TOKEN>` en `/analyst/*` (comparación en tiempo constante). Si falta o no coincide, responde 401 `{"message":"Unauthorized"}` como API Gateway. Si coincide, inyecta `requestContext.authorizer.jwt.claims = {sub: "analista-local", email: "analista@demo.local"}` en el evento. `LOCAL_ANALYST_TOKEN` se lee de `.env`; si no está, se genera uno al arrancar (`secrets.token_urlsafe`) y se imprime en la consola. Solo `scripts/` conoce ese nombre; un test comprueba que `src/` no lo menciona.

Riesgo conocido (sección 8 de `interfaz_chat_requerimientos.md`): en GET, CloudFront solo reenvía `Authorization` si la cache policy lo incluye. Para `/analyst/*` no hay plan B de header: el autorizador JWT lee `Authorization`. Hace falta una cache policy propia (Andrés).

## Ciclo de vida local (ruta B)

Detrás de un protocolo; la implementación en la nube (Step Functions + EventBridge Scheduler) es de Andrés.

```
class CaseLifecycle(Protocol):
    def start(self, case_id: str, fast_clock: bool | None = None) -> None: ...  # al guardar un caso de ruta B; programa sus temporizadores
    def advance(self, case_id: str) -> StoredCase | None: ...  # local: aplica las transiciones vencidas; nube: solo lee
    def advance_all(self) -> None: ...                  # local: antes de listar la cola
    def on_decision(self, case_id, expected_version, decision, resolution, labels) -> CaseRecord: ...
        # escritura condicional por version + transición; nube: escritura condicional + SendTaskSuccess
        # también borra los temporizadores de SLA pendientes
    def on_opened(self, case_id, analyst) -> StoredCase | None: ...  # el analista abrió el caso: queda tomado
    def set_fast_clock(self, case_id, on) -> None: ...  # solo demo: reescala los temporizadores pendientes
    def has_pending_timers(self, case_id) -> bool: ...  # la tarjeta sigue sondeando
```

Local, sin hilos ni temporizadores: las transiciones se aplican de forma perezosa al leer el caso (turno de chat, `GET /cases/{id}`, lista y detalle del analista), bajo un `RLock` por caso (una investigación lenta no bloquea otros casos ni otros chats; la lista corre investigaciones pendientes durante 2 s como mucho y deja el resto para el siguiente sondeo, H3), con el reloj inyectado, así los tests son deterministas y el investigador corre una sola vez aunque lleguen lecturas en paralelo. El retardo es `LOCAL_INVESTIGATION_DELAY_SECONDS` (4 por defecto; `--investigation-delay` en `local_api.py`; 0 en pruebas). `CaseStore.put` es condicional sobre `version` (sube en cada escritura y sella `updated_at`); una escritura vieja lanza `CaseVersionConflict` (409 en la decisión, 409 reintentable en un turno de chat). Cada transición es un evento #7 `lifecycle.transition` con `trace_id: lc-<case_id>`; cada llamada del investigador, un evento `tool`. El mapeo a Step Functions está en el docstring de `lifecycle.py`.

| De | A | Cuándo (local) |
|---|---|---|
| `open` (dinero: `unrecognized_charge`, `wrong_fee`, incluidos duplicados y `purchase_amount_disputed`) | `investigating` | inmediato en `start` (el turno que abre el caso ya lo muestra) |
| `open` (no monetaria) | `awaiting_analyst` sin reporte | inmediato en `start` |
| `investigating` | `awaiting_analyst` con reporte del stub | 4 s después de `start` |
| `awaiting_analyst` | `notified` o `handed_off` | decisión del analista (tabla de arriba) |
| `notified` | `closed` | 30 s después de `sent_at`, salvo que el caso espere al cliente |
| `notified` esperando al cliente | `reopened` | el cliente responde en el chat: su mensaje (redactado) se agrega a `customer_statement` y a `open_questions` |
| `notified` o `closed` | `reopened`, cola `senior`, prioridad `high` | el cliente dice que no está de acuerdo ("no estoy de acuerdo", "segunda opinión", "reabrir el caso", "não concordo") |
| `reopened` | `awaiting_analyst` | en la siguiente lectura; la decisión anterior queda en `open_questions`, `labels_emitted` y la traza, y el caso se puede decidir otra vez |

Esperar al cliente (revisión del 29 sep, CX-01/H1): la decisión pide información cuando es `reject` + `request_information`, o `approve`/`edit` de un reporte que recomienda `request_information` (su borrador dice "puedes responder por este canal"). Ese caso no se cierra a los 30 s (campo interno `CaseRecord.awaiting_customer`, fuera de toda vista); "¿cómo va mi caso?" responde el estado y recuerda que esperamos su respuesta. La segunda opinión es la versión mínima (H6): vuelve a la cola `senior` con prioridad alta, pero la consola todavía no exige que decida otra persona (decisión de Arturo). Cada nueva resolución de un caso reabierto se dice una vez en el chat (la clave de entrega es `case_id@sent_at`). Las rutas A y C no tienen ciclo de vida (`lifecycle_step: null`). Los tiempos son de demo; los temporizadores de SLA y el reloj acelerado están en la sección siguiente.

`lifecycle_step` (lo ve el cliente) se deriva del estado: `open|reopened → open`, `investigating → investigating`, `awaiting_analyst → in_review`, `handed_off` en ruta B → `in_review`, `notified → notified`, `closed → closed`; fuera de ruta B es null (`case.lifecycle_step`).

El texto de `resolution` se entrega una sola vez por sesión. Si la tarjeta en vivo lo leyó con `GET /cases/{id}` (el front lo muestra como mensaje "Humano · aprobado por un analista"), la sesión lo marca como entregado y el chat no lo repite. Si el cliente nunca leyó `GET /cases` (un canal sin tarjeta, como WhatsApp) y escribe, se dice al inicio de la siguiente respuesta (plantilla `resolution_intro`, "Novedades de tu caso EV-…:"). Una vez respondido el caso, el estado del caso en el chat ya no repite la fecha prometida.

`poll_after_ms` (ya activo): 5000 (`CASE_POLL_MS`) en `ChatResponse` y `CaseResponse` mientras el caso sea de ruta B y esté en `open`, `investigating` o `awaiting_analyst`, o mientras le queden temporizadores de SLA pendientes; null en cualquier otro caso. Incluye `open` porque en la nube el paso a `investigating` es asíncrono. El front sondea con ±20 % de jitter, solo con la pestaña visible y la sesión vigente, y se detiene cuando llega null.

## Temporizadores de SLA (ruta B, simulación local)

Plan v2 secciones 6 y 12. En la nube son schedules de un solo uso de EventBridge Scheduler (Andrés); aquí es una SIMULACIÓN LOCAL detrás del mismo protocolo `CaseLifecycle`, perezosa como el resto del ciclo de vida (se evalúa al leer el caso: turno de chat, `GET /cases/{id}`, lista y detalle del analista) y con el reloj inyectado en pruebas.

| Temporizador | Se dispara en (`promise.py`) | Condición | Efecto |
|---|---|---|---|
| `unassigned` (sin asignar) | `clock.assigned_by` = creación + 24 h | nadie tomó el caso | alerta en la cola: `AnalystCaseListItem.sla_alerts` incluye `"unassigned"` |
| `sla_80` (aviso 80 %) | `clock.sla_alert_at` = creación + 80 % del SLA (12 días de 15) | | aviso proactivo al cliente (`CustomerCaseView.notices`, plantilla `sla_notice.sla_80`) |
| `breached` (vencido) | `clock.breach_at` = creación + SLA (15 días) | | cola `senior`, prioridad `high`, `urgency_flags += sla_breached`, nota en `open_questions`, aviso `sla_notice.escalated` |

- Se programan los tres en `start` (al abrirse el caso B; campo interno `CaseRecord.sla_timers`, fuera de toda vista). Las rutas A y C no tienen.
- Solo se disparan si el caso sigue abierto para el SLA: `open`, `investigating` o `awaiting_analyst` y sin decisión. Un temporizador que quedara vivo en un caso `notified`, `closed`, `handed_off`, `reopened` o `resolved_in_contact` no hace nada (la función que lo atiende relee el caso, igual que el target en la nube) y se borra en esa lectura (`sla_timer.cancel`, `why:not_open:<status>`); tampoco cuenta como pendiente para `poll_after_ms` (revisión del 29 sep: antes la tarjeta de un caso así seguía consultando para siempre).
- Decidir el caso (cualquier acción, incluido escalar) borra los pendientes (`sla_timer.cancel`, `why:decided`). Un caso reabierto después no los recupera: la reapertura ya lo manda a la cola senior con prioridad alta.
- "Tomado" = un analista lo abrió en `GET /analyst/cases/{id}` o lo decidió (campo interno `taken_at`). Abrirlo borra `unassigned` (`sla_timer.cancel`, `why:taken`) con una escritura condicional (sube `version`; el detalle se lee después, así que el `version` que devuelve es el vigente). Si la alerta ya había saltado, queda en `clock_events` pero sale de `sla_alerts`.
- Vencido no cambia el estado: el caso sigue `awaiting_analyst` (o `investigating`) y lo decide el analista senior desde la consola.
- Cada disparo es un evento #7 `actor: "rule"`, `name: "sla_timer.unassigned|sla_80|breached"`, `trace_id: lc-<case_id>`, con `input_ref` `case:<id>;due:<hora programada>;scale:<factor>` y `output_ref` con el efecto (sin texto libre). Programar, borrar y reescalar son `sla_timer.schedule|cancel|rescale`; tomar el caso, `case_taken` (`actor: "human"`, hash del analista).
- Si una lectura llega tarde, se disparan en orden todos los vencidos, en una sola escritura, con `fired_at` = el reloj de ese momento.
- Avisos al cliente: en el idioma del caso y el registro del país (tú MX, usted CO, vos AR; PT você), con el número de caso y la fecha prometida, sin prometer resultado (pasan por `forbidden_hits`). Uno por tipo (`notices` deduplicados por `kind`). También se agregan a la conversación del caso para el analista (`role: "system"`, `source: "template"`). No se repiten en el chat; un canal sin tarjeta en vivo (WhatsApp) los recibiría por el canal en la nube.

Contrato nuevo (`types.ts` ↔ `contract.py`, `test_contract.py`):

```ts
export type SlaTimerKind = "unassigned" | "sla_80" | "breached";
export type NoticeKind = "sla_80" | "escalated";
export interface CaseNotice { kind: NoticeKind; text: string; language: Language; at: string; }
export interface ClockEvent { kind: SlaTimerKind; fired_at: string; }
CustomerCaseView.notices: CaseNotice[];          // después de lifecycle_step
AnalystCaseListItem.sla_alerts: SlaTimerKind[];  // orden unassigned, sla_80, breached; unassigned sale al tomar el caso
AnalystCaseView.clock_events: ClockEvent[];      // después de clock
DemoSwitchesPatch.fast_clock?: boolean;
DemoSwitchState.fast_clock: boolean;
```

Reloj de demo (`fast_clock`): interruptor del modo juez por sesión. Los temporizadores de los casos de esa sesión se disparan en `created_at + (t − created_at) × LOCAL_SLA_SCALE`, 1/1440 por defecto (1 día = 1 minuto: sin asignar al minuto, 80 % a los 12 min, vencido a los 15 min). Vale para el caso ya abierto en la sesión (se reescala, `sla_timer.rescale`) y para los que abra después; apagarlo devuelve lo pendiente al tiempo real. Las fechas prometidas (`expected_date`, `first_response_by`, `clock.*`) no cambian. No afecta a otras sesiones. `local_api.py --sla-scale` cambia el factor para pruebas rápidas (0.00001 → ~13 s hasta el vencido). EN PRODUCCIÓN NO EXISTE: como los demás interruptores, solo se acepta en sesiones `source == "demo"` y la nube no tiene factor.

Mapeo a EventBridge Scheduler (para Andrés):

| Local (`lifecycle.py`) | Nube |
|---|---|
| `start` programa `sla_timers` | Tres `CreateSchedule` al guardar el caso B: nombre `<case_id>-<kind>`, grupo `case-sla`, `ScheduleExpression: at(<hora UTC de promise.py>)`, `FlexibleTimeWindow: OFF`, `ActionAfterCompletion: DELETE`, target la Lambda del ciclo de vida con `{case_id, kind}` |
| `_fire_due` al leer el caso | La Lambda target relee el caso y actúa solo si está en `open`/`investigating`/`awaiting_analyst` sin decisión (y, para `unassigned`, sin `taken_at`); escribe con condición sobre `version` y reintenta ante conflicto |
| `on_decision` borra los pendientes | `DeleteSchedule` de los que queden (ignorar `ResourceNotFoundException`), en la misma tarea que `SendTaskSuccess` |
| `on_opened` (analista abre el caso) | `DeleteSchedule <case_id>-unassigned` y escribir `taken_at` |
| aviso al cliente | la Lambda target renderiza la plantilla y lo envía por el canal (y lo guarda en `notices`) |
| `fast_clock` / `--sla-scale` | no existe |

## Interruptores del modo juez (RF-22)

`ChatRequest.demo_switches?: {tools_down?, model_slow?, expire_session?, fast_clock?}` (`DemoSwitchesPatch`). Son de la sesión y valen desde la petición que los trae (se aplican antes de su mensaje o botón). Solo cambian las claves presentes; al menos una.

- `tools_down: true|false`: toda herramienta del gateway falla para esta sesión → dos reintentos y ruta C `tool_failure`, caso marcado incompleto, `degraded: ["tool_unavailable"]`.
- `model_slow: true|false`: G1 de esta sesión excede `LLM_TIMEOUT_SECONDS` (el turno espera el timeout real, así se ve el aviso de los 6 s) → respuesta de plantilla, `degraded: ["model_timeout"]`.
- `fast_clock: true|false`: reloj de demo de los temporizadores de SLA de esta sesión (ver la sección anterior). Plantillas `switch.fast_clock_on|off`.
- `expire_session: true`: la sesión vence ya; esta petición y las siguientes responden 401 `session_expired` (el mensaje o botón de la misma petición no se procesa). `false` no hace nada.
- Petición solo con interruptores (sin `message` ni `button_id`): 200 con un `ChatResponse` corto de plantilla (`switch.tools_down_on|off`, `switch.model_slow_on|off`) que no avanza la conversación: `turn` repite el último número y no cuenta para el tope de 30, `buttons: []` mientras los botones pendientes siguen válidos (la UI los mantiene), `progress`, `case_card` y `lane` son los vigentes, `degraded: []`, y `trace_summary` tiene un paso `rule` llamado `demo_switches`. Es idempotente por `client_msg_id` como cualquier otro envío.
- Cada `ChatResponse` trae `demo_switches: {tools_down, model_slow, fast_clock}` con el estado vigente.
- Implementación: `tools_down` usa `demo_gateway.set_tool_failure(session_id, …)` (también afecta al investigador de los casos de esa sesión); `model_slow` se guarda en la sesión (`SessionRecord.model_slow`) y el orquestador espera `LLM_TIMEOUT_SECONDS` con su `sleep` inyectable antes de responder con el extractor de reglas; `expire_session` borra la sesión y su interruptor `tools_down`, pero NO los bloqueos de tarjeta (una tarjeta bloqueada con el sí del cliente sigue bloqueada; SEC-01). El contexto del analista se lee como analista (`GatewayContext.for_analyst`): el `tools_down` de la sesión del cliente no lo vacía (SEC-02; `--fail tools` de todo el proceso sí), y un bloqueo registrado en `actions` del caso se muestra como `blocked` aunque se pierda el estado demo. Una sesión que vence por TTL también apaga su `tools_down`. Las peticiones solo con interruptores tienen su propio tope: 60 por sesión, luego 409 `session_limit` (SEC-04). Solo existen para sesiones `source == "demo"` (hoy todas); una sesión de producción recibe 400 y en producción este camino no existiría.

## Cambios en #3 (propuesta, dueño Arturo)

`CaseRecord` gana `version` (int, empieza en 1), `updated_at`, `resolution {language, text, sent_at, approved_by_human: true}` y `rules_version` (versión de las reglas al decidir la ruta). Temporizadores de SLA: `notices [{kind, text, language, at}]` y `clock_events [{kind, fired_at}]` (visibles), y los internos `sla_timers`, `timer_scale` y `taken_at` (nunca en una vista). `AnalystDecision` gana `decided_by`, `next`, `draft_text`, `sent_text`. El estado interno del ciclo de vida en la nube (`execution_arn`, `task_token`) lo define Andrés y nunca sale en una vista.

## Ajustes del 29 sep incluidos

- Martina: cargo bajado de 245.000 a 145.000 ARS (≈414 USD). Ahora dispara `high_fraud_score` (fraud_score 82), no `high_amount`, como promete el plan §12. Datos demo en versión `2026-09-29.1`.
- Regla nueva `purchase_amount_disputed` (ruta B): `{complaint_type: wrong_fee, charge_confirmed: true, charge_is_fee: false}`, justo después de `fee_does_not_match`. `Evidence.charge_is_fee` lo llena el orquestador con `txn_type == "fee"` del registro, nunca del texto. Antes "me cobraron de más" sobre una compra caía en `no_rule_matched` (C). Reglas en versión `2026-09-29.2`.

## Propuestas a Andrés

1. `evidence_records` en #6: instantánea de los registros citados guardada con el reporte, para abrir citas sin otra ruta y para que la validación de citas sea auditable después.
2. `STORE_BACKEND=memory|dynamodb` en #9, que falle fuera de `STAGE=local` si no está definido (ya implementado así en `src/conversation/store.py` del lado memoria). Los stores ganan el ciclo de vida: `CaseStore.put` con verificación de `version` (escritura condicional en DynamoDB) para el 409.
3. Ciclo de vida: protocolo `CaseLifecycle` de arriba, con `LIFECYCLE_BACKEND=local|stepfunctions` en #9 (mismo criterio de fallar fuera de local). En la nube, `start` arranca la ejecución y `on_decision` hace `SendTaskSuccess` con el task token guardado en el caso; `advance` solo lee.
4. `GatewayContext.for_analyst(case_id, analyst_sub)` en #2: toma el `customer_id` del caso guardado, nunca del request, y deja `analyst_sub` en la traza.
5. Cache policy de CloudFront que reenvíe `Authorization` en `/api/analyst/*`.
6. Interruptores en la nube: guardarlos en el registro de sesión (no en memoria del proceso) y pasar `tools_down` al gateway por el contexto de la llamada.
7. `ReviewStore.append_conversation(case_id, turns)` / `get_conversation(case_id)` (30 sep): la conversación redactada de cada caso, un ítem por caso con tope de turnos, fuera del registro del caso (no toca `version`).
8. Prompts dentro de `src/` (30 sep): #8 dice `prompts/` en la raíz, pero el asset de la Lambda es solo `src/`. Propuesta: `src/conversation/prompts/<prompt_id>/<version>.md`; el cargador de este repo busca ahí primero y después en `prompts/`.

## Integración front + backend (29 sep)

Se capturaron 32 respuestas reales de `local_api.py` (sesión, chat, interruptores, casos en cada paso del ciclo de vida, cola, detalle de Lucía, João, Andrés, Sofía y Martina, las cuatro decisiones y los errores 400, 401, 403, 409 y 422) y se asignaron a sus interfaces de `types.ts` en un archivo temporal compilado con `tsc` (con verificación de propiedades sobrantes): cero diferencias de forma. Desvíos de comportamiento que se corrigieron en ambos lados:

- `rules_version` no llegaba a la consola: se agregó a #3 y a `AnalystCaseView` (`case.py`, `contract.py`, `types.ts`); la consola muestra "regla X · versión Y".
- La resolución salía dos veces en el chat (mensaje humano por la tarjeta y otra vez al inicio de la siguiente respuesta): ahora `GET /cases` la marca como entregada.
- Regla de confiabilidad: el stub marcaba `citations_valid=false` con un solo hallazgo eliminado, lo que anulaba el umbral "más de uno" del plan §6. Ahora `citations_valid` dice que lo que QUEDA está bien citado y manda `removed_claims > 1`.
- Casos respondidos arriba de la cola: ahora van al final.

## Qué se probó

Temporizadores de SLA (29 sep): `pytest` completo con `tests/test_sla_timers.py` (reloj inyectado) y `scripts/smoke_api.py` contra `local_api.py --port 8501 --sla-scale 0.00001` con `--sla-wait 30` (29/29; el escenario de SLA tarda ~13 s). Front: `vitest` pasa; `tsc -b` marca 7 errores en `frontend/test/fixtures.ts` y `frontend/test/chat_lifecycle.test.tsx` porque sus objetos de prueba no traen los campos nuevos (`notices`, `sla_alerts`, `clock_events`, `fast_clock`): los ajusta el agente de front, igual que mostrar los avisos en la tarjeta y las alertas en la cola.

30 sep (G1 y pendientes de la consola): `pytest` completo con `tests/test_g1.py` y `tests/test_console_conversation.py`, y `scripts/smoke_api.py` contra `local_api.py` en el puerto 8401 con G1 en mock por defecto (27/27). La consola del front todavía no muestra `unavailable` ni `conversation` (es del agente de front).

En local, sin AWS ni modelos (30 sep): `pytest` completo y `scripts/smoke_api.py` contra `scripts/local_api.py` en el puerto 8202 con `--analyst-token` (retardo real de 4 s y `model_slow` esperando los 8 s reales). Detalle y cifras en el reporte del agente de backend. El escenario de TTL corto sigue omitido. Integración del 29 sep: el mismo circuito recorrido en el navegador (chat + consola en dos pestañas) contra `local_api.py --investigation-delay 2`; ver `docs/como_correr_local.md` y `docs/recorridos_m1.md`.
