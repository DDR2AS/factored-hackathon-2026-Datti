# Cómo correr la API y el front en local

Expediente Vivo v2, repo del equipo. Todo es local: no llama a AWS ni a ningún modelo (G1 usa un proveedor mock determinista, ver "Extracción G1"). Clientes, cargos y textos son sintéticos.

## Requisitos

- Python 3.12 (la Lambda corre 3.12; la suite también pasa en 3.11, que usa el CI). En un clon limpio, crear el venv una vez: `py -3.12 -m venv .venv` (o `python -m venv .venv`), activarlo y `python -m pip install -r requirements.txt`.
- Node 24 (o 22.22+) para el front (hay Node 24 instalado).

En Git Bash, desde la raíz del repo:

```bash
source .venv/Scripts/activate
export PATH="$PATH:/c/Program Files/nodejs"
cp .env.test .env        # opcional: .env.test es la plantilla; sin .env usa los mismos valores por defecto. No hagas `source .env`
```

## Levantar la API

```bash
python scripts/local_api.py --port 8000
```

- Health: http://127.0.0.1:8000/api/health → `{"status":"ok","stage":"local","deps":"ok","demo_clock_scale":…}`.
- Corre el mismo `src/handlers/api.py` que la Lambda. Imita a CloudFront (quita `/api`) y a API Gateway (resuelve la ruta y arma el evento v2). Una ruta que no existe da 404 `{"message":"Not Found"}`.
- También sirve `frontend/dist` en `/`, si ya se hizo el build.
- Sesiones y casos viven en memoria y se pierden al reiniciar. Las trazas quedan en `data/traces/<fecha>.jsonl`.
- Al arrancar imprime el token de la consola del analista si `LOCAL_ANALYST_TOKEN` no está en `.env` (ver "Entrar a la consola del analista"). Cada reinicio genera uno nuevo.

Opciones:

| Opción | Qué hace |
|---|---|
| `--fail tools` | Todas las herramientas del gateway demo fallan. El chat pasa a ruta C por `tool_failure` y devuelve `degraded: ["tool_unavailable"]` |
| `--ttl-minutes 1` | Sesiones de 1 minuto para probar `session_expired`. Acepta decimales, por ejemplo `0.05` son 3 s |
| `--fail model` | Conecta un modelo falso que responde después de `LLM_TIMEOUT_SECONDS` (8 s por defecto; para la demo conviene `LLM_TIMEOUT_SECONDS=2`). Cada turno devuelve `degraded: ["model_timeout"]`; responde el extractor por reglas y, si no entendió nada, la plantilla `model_slow` con botones fijos. Se puede combinar con `--fail tools` |
| `SESSION_RATE_PER_MINUTE` (variable) | Máximo de `POST /session` por minuto y por IP (60 por defecto, 0 lo apaga); el siguiente es 429 `rate_limited`. Las sesiones vencidas se borran de la memoria |
| `--investigation-delay 2` | Segundos entre abrir un caso de ruta B y tener el reporte del investigador (stub provisional). 4 por defecto; 2 es cómodo para la demo. También `LOCAL_INVESTIGATION_DELAY_SECONDS` |
| `--sla-scale 0.00001` | Qué hace el interruptor `fast_clock` con los temporizadores de SLA de los casos de esa sesión: sus tiempos se multiplican por este factor. Por defecto 1/1440 (1 día = 1 minuto: sin asignar a 1 min, aviso del 80 % a 12 min, vencido a 15 min). Para una prueba rápida, `0.00001`: sin asignar a 0,9 s, 80 % a 10,4 s, vencido a 13 s. Las sesiones sin `fast_clock` van siempre en tiempo real. También `LOCAL_SLA_SCALE` (0 < x ≤ 1) |
| `--llm mock` / `--llm none` | Extractor G1. `mock` (por defecto, `LLM_PROVIDER=mock`): el proveedor mock responde desde `tests/fixtures/llm/g1_extract/*.yaml`; `none`: solo el extractor por reglas. Gana sobre `LLM_PROVIDER` de `.env` y del entorno. La segunda línea al arrancar dice qué quedó activo |
| `--port`, `--host` | Por defecto `127.0.0.1:8000` |

`--fail tools` y `--fail model` afectan a todo el proceso. `--sla-scale` no acelera nada por sí solo: solo define cuánto acelera `fast_clock`. Para una sola conversación están los interruptores del panel del juez (ver abajo), que viajan en `demo_switches` con el siguiente mensaje.

## Extracción G1 (modelo) y `LLM_PROVIDER`

Cada mensaje escrito pasa por: redacción (documento, dirección, teléfono, tarjeta completa) → extractor por reglas → G1 con `LLM_TIMEOUT_SECONDS` (8 s) → validación con el esquema Pydantic `Extraction` → guardas anti-alucinación → combinación. Código: `src/conversation/llm_port.py` (puerto #8 y proveedores), `src/conversation/g1.py` (validación, guardas, combinación), prompt `src/conversation/prompts/g1_extract/v1.md`.

| `LLM_PROVIDER` | Qué pasa |
|---|---|
| `mock` (por defecto en `local_api.py`) | `MockLLMClient`: busca el texto REDACTADO y normalizado (más la fecha de referencia del cliente) en `tests/fixtures/llm/g1_extract/*.yaml`. Con fixture: `model_id: "mock-g1"`, `prompt_version: "g1_extract@v1"`, tokens estimados (caracteres / 4) y `cost_usd` con los precios por rol de `src/conversation/llm_prices.yaml` (estimación, `usage.estimated: true`). Sin fixture: `NoFixture`, responde la regex, sin `degraded`, y la traza dice `error_code: "no_fixture"`. `MODEL_CHAT` solo se muestra al arrancar |
| `none` (o sin definir) | Sin modelo: la regex en cada turno |
| `bedrock` | El paquete `src/llm` de Andrés. Todavía no existe: cada turno cae a la regex con `degraded: ["model_timeout"]` y `error_code: "model_unavailable"` ("pendiente de Andrés") |

Reglas que no cambian con el proveedor: el modelo solo extrae datos; nunca elige la ruta (la decide `lane_rules.yaml`), nunca ve documento, dirección ni teléfono, no tiene herramientas y ningún texto que lee el cliente sale de él (`reply_source` sigue en `"template"`). El texto del cliente va entre `<texto_cliente>` y `</texto_cliente>`, declarado como dato; si trae esas etiquetas se borran antes. Un mensaje con inyección lo marca antes el clasificador y G1 ni se llama.

Qué se usa del modelo:
- Salida inválida para el esquema (campo extra, tipo equivocado): se descarta, responde la regex, `error_code: "schema_invalid"`.
- Guardas: un monto o últimos 4 que no aparecen en el texto redactado (normalizado: "cuatrocientos cincuenta", "52 lucas", "449 con 90", "51 mil 500", dígitos en palabras) se descartan; también una moneda que no se dijo, un comercio cuyas palabras no están en el texto y una fecha posterior a la referencia del cliente o de hace más de 400 días. El campo descartado toma el valor de la regex, si hay; la traza lo anota en `output_ref` (`dropped:amount,...`).
- Cuando G1 responde bien, sus slots mandan (`null` = no lo dijo) y la regex solo completa el tipo de reclamo y los campos descartados. Las banderas de ruta C se combinan con las de la lista de patrones (OR): el modelo puede agregar una, nunca quitar una que encontraron los patrones.
- Timeout o proveedor caído: responde la regex con `degraded: ["model_timeout"]`.

En `trace_summary`: paso `{actor: "model", name: "g1_extract", version: "g1_extract@v1", error_code}`, más `model_id`, `tokens_in`, `tokens_out` y `cost_usd` del turno. El interruptor "Modelo lento" usa el mismo nombre de paso.

Fixtures: un archivo por cliente demo, marcado `synthetic: true`, con los mensajes sugeridos del modo juez (`kind: suggestion`) y 5 variantes por cliente donde el modelo gana a la regex (`kind: variant`, `beats_regex` dice en qué campos: "el martes pasado", "ontem", "no dia primeiro", "cuatrocientos cincuenta", "52 lucas", "terminada en cinco cinco uno dos", "¿me pueden pasar con alguien?", …). Son distintas de los mensajes de prueba escritos por el equipo; `tests/test_g1.py` lo comprueba. Para probar a mano: Sofía, "El martes pasado me cobraron un domicilio de cincuenta y dos mil pesos que no pedí" → con `--llm mock` la fecha dicha es 2026-06-09; con `--llm none`, 2026-06-16.

Ubicación de los prompts: INTERFACES.md #8 dice `prompts/<prompt_id>/<version>.md` en la raíz, pero el asset de la Lambda es solo `src/` (`infra/stacks/common.py`), así que una carpeta `prompts/` en la raíz no se desplegaría. Propuesta a Andrés: los prompts viven en `src/conversation/prompts/`; el cargador (`llm_port.load_prompt`) busca ahí primero y después en `prompts/`. Los fixtures del mock siguen en `tests/fixtures/llm/` como dice #8 (no van en la Lambda: un mock desplegado respondería siempre con la regex).

## Levantar el front

Hay dos formas:

- **Desarrollo:** en `frontend/`, correr `npm run dev` y abrir http://localhost:5173. Vite reenvía `/api/*` tal cual a `http://127.0.0.1:8000` y es `local_api.py` el que quita el prefijo, igual que la CloudFront Function; la API tiene que estar corriendo.
- **Build real:** en `frontend/`, correr `npm run build`. Después, `local_api.py` sirve `frontend/dist` en http://127.0.0.1:8000.

## Entrar a la consola del analista

1. Levantar la API con el front ya compilado: `python scripts/local_api.py --port 8000 --investigation-delay 2`.
2. Copiar el token de la línea que empieza con "Consola del analista:" ("… token generado para esta ejecución: …"). Para uno fijo, poner `LOCAL_ANALYST_TOKEN=<algo largo>` en `.env` (nunca se commitea). Solo `scripts/local_api.py` conoce ese nombre: `src/` no tiene bypass; el script imita al autorizador JWT de Cognito, valida `Authorization: Bearer <token>` en `/api/analyst/*` e inyecta los claims `{sub: "analista-local", email: "analista@demo.local"}`. Sin token o con uno equivocado: 401 `{"message":"Unauthorized"}`, como API Gateway.
3. Abrir http://127.0.0.1:8000/#/consola (o el botón "Abrir consola del analista" del panel del juez, que abre otra pestaña), elegir "Token de analista local" y pegarlo. El token queda en memoria y en `sessionStorage` de esa pestaña; "Salir" lo borra. Si la API se reinicia, el token viejo da 401 y la consola pide entrar de nuevo sin perder el borrador.
4. Cognito (nube) aparece como "no habilitado": falta que Andrés habilite `USER_PASSWORD_AUTH`, cree un usuario analista demo y agregue el dominio de Cognito a la CSP.

Circuito completo para mostrar (dos pestañas):

1. Pestaña del chat: Lucía, "me cobraron como 450 en el super el 12" → Es este → No lo reconozco. La tarjeta pasa sola de "Investigando" a "En revisión" (sondea `GET /cases/{id}` cada 5 s, solo con la pestaña visible).
2. Pestaña de la consola: el caso aparece en la cola (sondeo cada 15 s o el botón de actualizar). Abrirlo: paquete (dicho frente a verificado), regla y versión de reglas, motivo previsto, reporte del investigador PROVISIONAL (stub determinista, sin modelo) con citas; cada cita abre su registro en la pestaña Evidencia. "Confirmar" el motivo previsto, "Aprobar y notificar" → "Confirmar y enviar". El panel pasa a "Respuesta enviada" con el sello.
3. Volver al chat: aparece el mensaje "Humano · aprobado por un analista" con el texto sellado ("… En esta demo ningún dinero se mueve.") y la tarjeta en "Notificado". Si Lucía escribe de nuevo, la respuesta no repite la resolución.
4. João (PT): "Editar", cambiar el texto en portugués, escribir el motivo y `Ctrl+Enter`. Se ve la diferencia con el borrador antes de enviar.
5. Martina: aparece en la cola como ruta C, prioridad alta, cola Fraude, con su traspaso (hechos verificados y preguntas abiertas), solo lectura.

Límites de la demo (revisión del 29 sep):
- El dueño de un caso es la sesión que lo abrió. Si la sesión vence (15 min por defecto, o el interruptor `expire_session`) la resolución que apruebe el analista queda en el caso y en la consola, pero ese cliente ya no la recibe: `GET /cases` con el token viejo da 401 y una sesión nueva del mismo cliente da 403 y no encuentra el caso ("¿cómo va mi caso EV-…?" responde que no lo encontró en esta conversación). En producción la primera respuesta llega hasta 24 h después, así que el dueño debe ser el `customer_id` verificado (no la sesión) o la resolución debe salir por otro canal (propuesta, H7).
- Dos mensajes a la vez de la misma sesión: el segundo espera como mucho 1 s y luego recibe 409 `conflict` reintentable (un turno con `model_slow` dura 8 s y no debe ocupar el servidor).
- Si el analista pide información (aprobar un borrador que la pide o rechazar con "Pedir información"), el caso queda notificado esperando al cliente y no se cierra solo: lo que el cliente escriba vuelve a la cola del analista. "No estoy de acuerdo" sobre un caso notificado o cerrado lo reabre en la cola senior con prioridad alta.

Atajos de la consola: flechas y Enter en la cola, `Alt+1…5` para las pestañas de contexto, `Ctrl+Enter` guarda una edición, `Esc` cancela. Los casos respondidos (notificado, cerrado) van al final de la cola y muestran "Respondido" en lugar del SLA. La consola cambia a portugués con el selector de idioma.

Interruptores del panel del juez (una sola conversación, viajan con el siguiente mensaje o botón):

| Interruptor | Qué se ve |
|---|---|
| Herramienta de transacciones caída | Ruta C `tool_failure`, caso marcado incompleto, aviso "Herramienta no disponible" en la burbuja |
| Modelo lento | A los 6 s el aviso "Está tardando más de lo normal"; a los 8 s responde la plantilla con "Respuesta de respaldo: el modelo no respondió" |
| Expirar la sesión ahora | La sesión termina en esa misma petición: el chat muestra "La sesión terminó" y "Volver a empezar" |

La vista de trazas (`#/traza`, o "Trazas" en la barra de la consola) lista los turnos de la conversación abierta en esa pestaña: actor, paso, latencia, versión y `error_code`, sin texto libre.

## Pruebas

```bash
python -m pytest -q tests                  # toda la suite
python -m pytest -q tests/test_api.py      # solo el handler con eventos v2
```

Qué cubre cada archivo nuevo:

| Archivo | Qué prueba |
|---|---|
| `test_api.py` | Handler, errores y códigos HTTP, dueño del caso (403 idéntico), `session_limit`, warm-up, `/health` sin dependencias |
| `test_orchestrator.py` | Los 6 clientes demo en su ruta, las dos preguntas de RF-04, inyección, herramienta caída, modelo lento, redacción, 3 turnos sin avance |
| `test_sessions.py` | Token con hash, vencimiento, botones de un solo uso, idempotencia, selección de store |
| `test_contract.py` | `contract.py` frente a `frontend/src/api/types.ts`, leído como texto |
| `test_local_api.py` | Copia de rutas frente a `infra/stacks/api_stack.py` del repo del equipo (se salta si no está al lado) y servidor real en un puerto libre |
| `test_review_fixes.py` | Hallazgos de la revisión del 28 sep: redacción (tarjeta con puntos, CVV, vencimiento, DNI después del número, teléfono con coma o punto, cuentas), JSON anidado, inyecciones sin frases clásicas, límite de sesiones, herramientas caídas con tarjeta perdida, relato después del traspaso, préstamo tras ruta B, idioma escrito, 3 turnos sin avance en todas las ramas, respuestas cortas de persona, fechas y montos en palabras |
| `test_integration_m1.py` | Front frente a backend: los mensajes sugeridos de `frontend/src/demo/customers.ts` llevan a cada cliente a su ruta y regla esperadas; toda regla del YAML, estado de cargo y estado de caso tiene texto en `es.ts` y `pt.ts` |
| `test_console_backend.py` | Ciclo de vida local, stub del investigador y validación de citas, decisiones (approve, edit, reject), 409/422/400/401/403, cola con filtros, orden y cursor, interruptores, `rules_version` en la vista del analista, resolución que no se repite en el chat |
| `test_console_contract.py` | Formas de la consola en `contract.py` frente a `types.ts` |
| `test_frontend_static.py` | Reglas estáticas del front (sin cookies, sin URLs externas, sin promesas de reembolso, toda cola que emite el backend tiene etiqueta en ES y PT, …) |
| `test_g1.py` | G1: fixtures (sintéticos, esquema, 5 variantes por cliente que ganan a la regex, cubren las sugerencias del front), mock (#8, uso estimado, `NoFixture`), selección de proveedor, prompt versionado con el texto como dato, sin model IDs en el código, los 6 clientes en su ruta con el mock, esquema inválido, alucinación descartada, banderas OR, timeout, bedrock pendiente, redacción, inyección, portugués, `--llm` |
| `test_sla_timers.py` | Temporizadores de SLA con reloj inyectado: se programan al abrir un caso B, sin asignar a las 24 h (alerta en la cola, desaparece al tomar el caso), aviso del 80 % en ES tú/usted/vos y PT con la fecha prometida y sin promesas, vencido a la cola senior con prioridad alta y aviso, cada aviso una sola vez, decidir cancela los pendientes, nada se dispara en casos cerrados, `poll_after_ms` con temporizadores pendientes, `fast_clock` por sesión sin tocar otras, encendido después de abrir el caso, `LOCAL_SLA_SCALE` y `--sla-scale` |
| `test_console_conversation.py` | `context.unavailable` cuando una herramienta falla, conversación redactada del caso para el analista (solo ese caso, nunca en la vista del cliente, turno del analista), texto nuevo de `request_information_default` |

Front: en `frontend/`, `npm test` (vitest), `npm run build` y `npx oxlint`.

## Smoke contra un servidor levantado

Con la API corriendo en el puerto 8000:

```bash
python scripts/smoke_api.py http://127.0.0.1:8000 --analyst-token <token impreso por local_api.py>
```

Sin `--analyst-token` los escenarios de la consola salen SKIP. `--only console`, `--only switches` o `--only sla` corren solo esa parte.

Temporizadores de SLA en segundos (simulación local; ver `docs/contrato_consola.md`): levantar un servidor con un factor mínimo y darle al smoke tiempo de espera:

```bash
python scripts/local_api.py --port 8501 --sla-scale 0.00001 &
python scripts/smoke_api.py http://127.0.0.1:8501 --analyst-token <token> --only sla --sla-wait 30
```

El escenario abre tres casos: Lucía con `fast_clock` que nadie toma (alerta sin asignar en la cola, aviso del 80 %, vencido: cola senior y prioridad alta, ~13 s), João con `fast_clock` decidido enseguida (no le llega ningún aviso) y Sofía sin `fast_clock` (no se dispara nada). Sin `--sla-wait` sale SKIP. Con el reloj de demo por defecto (1 día = 1 minuto) el mismo recorrido tarda 15 minutos: sirve para mostrarlo en vivo, no para el smoke.

Imprime PASS, FAIL o SKIP por escenario. Termina con código distinto de 0 si algo falla. Usa solo `urllib`. Contra la URL desplegada se usa igual, con la raíz del sitio, porque el script agrega `/api` (si se le pasa una base que ya termina en `/api`, la recorta).

Registro legible de las siete conversaciones demo y de las pruebas "como desconocido", con las mismas peticiones que el front:

```bash
python scripts/recorridos_m1.py http://127.0.0.1:8000 --analyst-token <token>     # escribe docs/recorridos_m1.md
```

Con el token agrega el circuito con analista (Lucía aprueba, João edita en PT, Andrés rechaza y escala, Martina en la cola de ruta C), los errores de la consola y los tres interruptores. Sin el token, esa sección dice que no se corrió.

La expiración real del token necesita un servidor con TTL corto:

```bash
python scripts/local_api.py --port 8001 --ttl-minutes 0.05 &
python scripts/smoke_api.py http://127.0.0.1:8001 --only expiry --expiry-wait 5
```

## Guiones demo (sintéticos)

| Cliente | Mensaje | Botones | Resultado |
|---|---|---|---|
| Lucía | "me cobraron como 450 en el super el 12" | Es este → No lo reconozco | B `unrecognized_low_risk`, EV- y fecha |
| Lucía | el mismo | Es este → Sí, lo reconozco → Sí, quedó resuelto | A, `resolved_in_contact` |
| João | "Olá, cobraram uma tarifa de manutenção de 12.500 pesos no dia 1 e acho que está errada" | É esta | B `fee_does_not_match`, todo en PT |
| Andrés (cliente demo) | "Me cobraron dos veces lo mismo, con minutos de diferencia" | Es este | B `duplicate_not_reversed` |
| Sofía | "me cobraron 52000 el 9 y no lo reconozco", y después "quisiera pedir un préstamo" | uno de los 3 → No lo reconozco | B, y después fuera de alcance con derivación |
| Lucía | "Me cobraron de más en SUPER AHORRO el 12, fueron 449.90" | Es este | B `purchase_amount_disputed` (cobro de más sobre una compra, no una comisión) |
| Martina | "Me aparece un consumo de 145 mil en ELECTRO MUNDO ONLINE el 15 que no reconozco" | Es este → Sí, bloquear tarjeta •••• 7730 | C `high_fraud_score`, cola de fraude, bloqueo con lectura del estado |
| Carlos | "Me cobraron 1.299 de STREAMING PLUS el 11, no lo reconozco y voy a ir a la CONDUSEF" | — | C `regulator_or_legal`, prioridad alta |

Martina sale por `high_fraud_score` (fraud_score 82): su cargo bajó el 29 sep de 245.000 a 145.000 ARS (≈414 USD, debajo del umbral de 500 USD de `high_amount`), como pide el plan §12. Reglas en la versión `2026-09-29.2`.
