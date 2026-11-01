# Interfaz de chat y consola del agente: requerimientos y ruta

Expediente Vivo v2. Lunes 28 sep 2026, ~20:00 Lima. Para Arturo, Diego, Andrés y Cristhian.

Esto es diseño; no hay código todavía. Actualizado a las 20:40: Node ya está instalado y la recomendación de stack cambió (sección 6). Lo marcado (nuevo) no existe. Lo marcado (propuesta → X) es un cambio de interfaz que Arturo lleva a su dueño X en INTERFACES.md; no está decidido. Verifiqué contra: repo del equipo en `main` f753eb8 (INTERFACES.md, STATUS.md, DECISIONS.md, .gitignore, Makefile, `src/handlers/api.py`, `infra/stacks/api_stack.py`, `web_stack.py`, `data_stack.py`, `common.py`, `config.py`, `infra/cdk.json`, `.github/workflows`), repo personal en `arturo/lane-b` (`src/conversation/case.py`, `lane_rules.py`, `rules/lane_rules.yaml`, `tests/`) y el texto del plan v2.


## 1. Resumen

1. Construimos tres caras del mismo caso: el chat del cliente con la tarjeta del caso llenándose en vivo, la consola del analista al estilo de la captura de Nubank (pero sobre el caso y el reporte citado, no sobre un chat en vivo) y el modo juez (landing con 6 clientes, trazas, evaluación).
2. Stack: front en React + Vite + TypeScript (Node ya instalado; es lo que asume el plan y lo que espera el CDK de Andrés, que sube `frontend/dist`); respaldo sin framework si el setup se traba. Backend: `src/handlers/api.py` como router delgado hacia `src/conversation/`, con stores, gateway y LLM inyectados; en local, `scripts/local_api.py` con la biblioteca estándar.
3. Hoy (lun 28, hasta 24:00), lo que pide el plan para el lunes y nada más: servidor local mínimo, `/health`, `POST /session` con lista permitida, `POST /chat` con respuesta de plantilla fija, chat shell y landing estática con Lucía y João.
4. Mañana (mar 29, M1): ruta B de punta a punta para Lucía ("No lo reconozco") y João (PT, comisión que no cuadra), tarjeta en vivo, PR del backend y del chat shell a las 12:00, segundo PR del front por la tarde, despliegue con Andrés y prueba en la URL antes de la demo de las 21:00. Lo más probable es que la URL dependa de entregas de Andrés que no caben el mismo día (sección 9).
5. Ninguna ruta nueva para M1: bastan las 4 públicas que ya están en `api_stack.py`. La consola (mié 30), trazas y PT completo (jue 1) y evaluación (vie 2) se construyen encima del mismo contrato.


## 2. La referencia (captura de Nubank)

La captura es la consola de un agente humano que chatea en vivo con el cliente, con IA que predice el motivo y sugiere la respuesta. Según lo que dice el usuario, Nubank lo hace con OpenAI; nosotros llamamos a Claude en Bedrock solo a través de `src/llm` (INTERFACES #8), y la UI nunca fija proveedor ni modelo: muestra el `model_id` que llega en la traza o el reporte.

Qué tomamos: una sola acción rellena por pantalla; colores y rótulos distintos para cliente, sistema y borrador de IA; la IA pegada al dato que la origina; nada se envía solo; contexto del cliente en pestañas a la derecha; densidad controlada.

Qué no tomamos: la marca y el morado de Nubank (identidad propia "LATAM Bank", sintética); el chat en vivo agente-cliente (en nuestro plan el cliente conversa con el sistema y el analista revisa el caso de forma asíncrona); "regenerar" (G2 es asíncrono y tiene costo); sugerencias sin fuente (la de la captura afirma un saldo sin decir de dónde sale); el motivo previsto sin confianza.

| Elemento de la captura | Equivalente en Expediente Vivo |
|---|---|
| Barra lateral morada | Riel: Cola, Trazas, Evaluación, Modo juez, salir. Sin estado "conectado" ni teléfono |
| Lista de conversaciones | Cola de casos (`GET /analyst/cases`) ordenada por `clock.breach_at`, con ruta, idioma, prioridad, SLA restante y "reporte no confiable" |
| Encabezado "Maria (Maria Silva)" | `case_id`, nombre de pila sintético, país, segmento, idioma, estado, SLA; chip de ruta con la regla y la versión del YAML |
| "Motivo previsto" | Chip ML: clase de M2, p calibrada, compuerta, segunda opción, `model_version`; "Confirmar" en un clic o "Corregir" entre las 9 clases (se guarda como etiqueta) |
| "Escolha um motivo..." | La corrección de arriba, que viaja en `labels` dentro de la decisión |
| "Resposta sugerida" (tarjeta celeste) | Reporte G2 (#6): hallazgos con citas que se abren, hipótesis con p, recomendación de la lista fija y, debajo, el borrador "generado por IA, no enviado" en el idioma del cliente |
| Enviar / editar / × | Aprobar y notificar / Editar y aprobar / Rechazar, con motivo obligatorio al editar o rechazar |
| Regenerar, Pular, adjuntos, emoji, "/" | No aplican |
| Mais ações | Solo `request_information` y `escalate`. Nada mueve dinero |
| Finalizar | "Aprobar y notificar"; el cierre lo hace el ciclo de vida de Andrés |
| Burbujas y horas | Paquete del caso: dicho por el cliente (no verificado) frente a hechos verificados con su registro; la transcripción queda plegada y marcada sintética |
| Panel derecho con pestañas | Evidencia, Riesgo (M3), Acciones, Historial, Traza |
| Franja "Enviar feedback" | La decisión del analista es el feedback |

En el chat del cliente el equivalente de la "sugerencia" son los botones de aclaración (confirmar cargo, elegir entre candidatos, confirmar bloqueo).


## 3. Pantallas

Rutas hash (`#/`, `#/chat`, `#/consola`, `#/traza/<trace_id>`, `#/evaluacion`) para no tocar CloudFront: una custom error response aplicaría a toda la distribución y convertiría los 4xx de `/api/*` en `index.html`. Rótulos de origen iguales en todas las pantallas, siempre con ícono y texto: Regla, ML, GenAI, Herramienta, Humano, Plantilla.

**3.1 Landing del modo juez (`#/`)**: hoy.
- Qué es el sistema en tres líneas; franja "Datos sintéticos · identidad simulada · portugués generado sin revisión nativa".
- Seis tarjetas: Lucía (ES·MX), Sofía (ES·CO), Andrés (cliente demo, ES·CO), João (PT), Martina (ES·AR), Carlos (ES·MX), cada una con qué demuestra y qué resultado se espera. Hoy solo dos tarjetas estáticas (Lucía y João); las seis, mañana temprano, con las otras en "disponible mié 30" o "jue 1". Andrés (cliente demo) se activa mañana solo si hace falta como reserva (sección 9).
- Aviso en la landing si el estado del demo es compartido entre jueces (ver RF-10 y RF-26).
- `GET /api/health` al cargar para despertar la Lambda; "iniciando…" si tarda más de 3 s.
- Enlaces a Consola, Trazas y Evaluación deshabilitados hasta que existan.

**3.2 Chat del cliente y tarjeta en vivo (`#/chat`)**: chat hoy, tarjeta mar 29.
- Móvil (320–430 px): encabezado, registro, compositor; la tarjeta es una hoja inferior plegable.
- Escritorio en modo juez (≥1024 px), tres columnas: panel del juez (escenario, resultado esperado, chip con el mensaje sugerido que se pone en el compositor y no se envía solo, reloj de sesión; interruptores de falla el jue 1), "teléfono" de 420 px al centro y la tarjeta en vivo a la derecha con una tira de traza del turno debajo.
- Registro: `role="log"`, `aria-live="polite"`; cada mensaje del sistema lleva "Plantilla" o "Generado por IA · sintético", su `lang` y la hora.
- Botones de aclaración como chips bajo el último mensaje; los usados quedan deshabilitados.
- Compositor: máximo 1.000 caracteres con contador, Enter envía, Shift+Enter salto de línea; indicador de espera inmediato y "está tardando más de lo normal" a los 6 s.
- Tarjeta, en bloques separados a propósito: pasos (Entender → Encontrar → Verificar → Caso abierto; luego Investigando → En revisión → Notificado); "Lo que nos dijiste (sin verificar)" y "Nos falta: …" desde `progress`; "Registro del banco" con el cargo confirmado (fecha local, monto con `Intl`, comercio, •••• últimos 4, estado); caso con `case_id`, ruta explicada, fecha prometida, primera respuesta antes de (con zona) y resultado.
- Tira de traza: actores, regla y versión, latencia servidor y cliente, "sin modelo" o `model_id`, `trace_id`.
- Diálogo de bloqueo (Martina, mié 30): `role="alertdialog"`, foco en "No", botón "Sí, bloquear tarjeta •••• 1234" y luego la lectura del nuevo estado.

**3.3 Consola del analista (`#/consola`)**: contrato fijado mar 29, construida mié 30.
- Riel, cola, encabezado del caso, centro y panel derecho como en la tabla de la sección 2.
- Centro: chip de motivo previsto; ruta con regla; paquete (dicho frente a verificado, acciones con `verified_at`, preguntas abiertas; en ruta C el handoff completo); reporte G2 con chips de cita que abren el registro en la pestaña Evidencia; borrador con Aprobar / Editar / Rechazar. Si `citations_valid=false` o `removed_claims>1`: franja "Reporte no confiable" y "Aprobar" exige marcar "Revisé la evidencia".
- Editar muestra la diferencia con el original y pide motivo. Aviso orientativo si el texto promete reembolso o compensación (la barrera real está en el servidor).
- Estados vacíos y de error: cola vacía, JWT vencido (volver a entrar sin perder el borrador en memoria), 409 "ya decidido por … a las hh:mm", 503 con datos marcados "desactualizado hh:mm", 403 sin revelar si el caso existe.
- Teclado: sin atajos de una sola tecla globales (WCAG 2.1.4); flechas y Enter solo con foco en la cola; `Alt+1…5` pestañas; `Ctrl+Enter` guarda la edición; `Esc` cancela.
- Layout: 3 paneles ≥1280 px, 2 entre 1024 y 1279, pestañas por debajo.

**3.4 Trazas (`#/traza/<trace_id>`)**: jue 1. Una fila por paso: actor, nombre, latencia, tokens, costo, versión (modelo, prompt o reglas), `error_code`, reintentos; payloads en `<pre>` como texto. Reutiliza la misma forma que `trace_summary` de `/chat`.

**3.5 Evaluación (`#/evaluacion`)**: vie 2. v2 frente a B0, B1 y B2 con n e IC, cortes por idioma y país, latencia p50/p95, costo, y lo medido, simulado y proyectado por separado. El esquema del reporte es de Cristhian (nueva interfaz #10, propuesta). Campos mínimos que #10 tiene que traer, por métrica del enunciado y del plan: resolución automática segura (con el % de casos intentados), contención, calidad de escalamiento (handoffs faltantes, innecesarios y completitud del paquete), resultados inseguros por tipo con su denominador (0 / n), latencia p50/p95 y costo por resolución exitosa ("no definido" si no hay ninguna).

| Fecha | Pantallas |
|---|---|
| Lun 28 (hoy) | Landing, chat shell, errores, etiquetas |
| Mar 29 (M1) | Tarjeta en vivo, tira de traza, conversación de João en PT |
| Mié 30 | Consola (cola, reporte, aprobar/editar/rechazar), bloqueo, sondeo de la tarjeta |
| Jue 1 (M2) | Trazas, interruptores de falla, UI en PT completa, botones de aclaración de Sofía |
| Vie 2 | Evaluación, pulido ES/PT |
| Sáb 3 18:00 | Congelamiento |


## 4. Requerimientos funcionales

Fuentes: [E] enunciado; [P§n] plan v2; [I#n] INTERFACES.md.

| ID | Requerimiento | Prioridad | Fuente |
|---|---|---|---|
| RF-01 | Landing del modo juez con 6 clientes; un clic abre una sesión de 15 min sin instrucciones previas, también en incógnito | M1 (2 activos) | [P§12], [E] jueces en frío |
| RF-02 | `POST /session` recibe `demo_key` de una lista permitida en el servidor; el front nunca envía `customer_id` | M1 | [I#1], [E] permisos |
| RF-03 | Chat: enviar texto o pulsar botón (`button_id` opaco), recibir respuesta, botones, `progress`, `case_card`, `lane`, `trace_id` | M1 | [I#1] |
| RF-04 | Dos preguntas separadas. 1) "¿Es este el cargo?" con "Es este" / "Ninguno de estos": solo fija `charge_confirmed`. 2) Solo si `complaint_type=unrecognized_charge`: "¿Lo reconoces?" con "Sí, lo reconozco" / "No lo reconozco": fija `customer_recognizes`. En `wrong_fee` no se pregunta y `customer_recognizes` queda null. Nada se abre sin confirmación del cliente | M1 | [P§5], lane_rules.yaml |
| RF-05 | La ruta la decide `lane_rules.evaluate`; ningún request lleva `lane`. La tarjeta muestra la razón (`lane_reason_code` traducido, M1); regla y versión (`2026-09-28.1`) en la tira de traza, con la misma prioridad que RF-15 | M1 (razón), RF-15 (regla y versión) | [P§4], D2 |
| RF-06 | Ruta B: caso con número `EV-XXXXXXXX`, fecha prometida y `first_response_by`, visible en la tarjeta | M1 | [P§13] mar 29, [I#3] |
| RF-07 | Tarjeta en vivo con "sin verificar" separado de "registro del banco" | M1 | [P§13] mar 29 |
| RF-08 | João de punta a punta en PT (plantillas, botones, tarjeta) | M1 | [E] idiomas, [P§12] |
| RF-09 | `session_expired` borra token, registro y tarjeta y ofrece "Volver a empezar" (sesión nueva, sin extensión silenciosa) | M1 | [E] fallas, [P§7] |
| RF-10 | `GET /cases/{id}` solo para el dueño; 403 idéntico si es ajeno o no existe. Dueño = `customer_id` del demo más el id de la sesión que creó el caso; varios jueces con el mismo cliente no ven los casos del otro. Tras "Volver a empezar" el juez no recupera el caso anterior (aceptado en M1; la UI lo dice) | M1 | [E] acceso no autorizado |
| RF-11 | Texto pegado (HTML, inyección) se ve literal; respuesta de plantilla sin acciones | M1 (render), jue 1 (clase `manipulation`) | [E] inyección, [P§7] |
| RF-12 | Modelo lento o caído: respuesta de plantilla con botones y rótulo visible | M1 en backend, jue 1 interruptor | [I#8], [P§7] |
| RF-13 | Herramienta caída: 2 reintentos y ruta C por `tool_failure`, caso marcado incompleto, mensaje seguro | M2 (jue 1) | [P§7], lane_rules.yaml |
| RF-14 | Etiquetas permanentes de sintético, "Plantilla"/"Generado por IA" por mensaje, "portugués generado sin revisión nativa" | M1 | RUNBOOK §4, [E] |
| RF-15 | Tira de traza por turno (`trace_summary`) | M1 si hay tiempo, si no mié 30 | [E] trazas |
| RF-16 | Consola: cola, detalle, reporte con citas, aprobar/editar/rechazar con motivo, auditoría | mié 30 | [P§13], [I#6] |
| RF-17 | Motivo previsto confirmable o corregible como etiqueta | mié 30 | [P§5] learning loop |
| RF-18 | Handoff estructurado en ruta C sin volcar transcript | mié 30 | [E] caso humano, [I#3] |
| RF-19 | Bloqueo de tarjeta solo con "sí" explícito y lectura del nuevo estado | mié 30 | [P§4], [I#2] |
| RF-20 | Sondeo de la tarjeta mientras el caso está en `investigating` o `awaiting_analyst` | mié 30 | [P§13] mié 30 (ciclo de vida) |
| RF-21 | Elección entre 3 cargos (Sofía) con "Ninguno" y "Hablar con una persona"; fuera de alcance (préstamo) derivado sin prometer | M2 | [E] ambiguo/no soportado |
| RF-22 | Interruptores de falla por sesión, aplicados en el servidor | M2 | [P§12] |
| RF-23 | Vista de trazas | M2 | [E] trazas, [P§13] jue 1 |
| RF-24 | UI completa en PT | M2 | [P§13] jue 1 |
| RF-25 | Página de evaluación | vie 2 | [E] métricas, [P§11] |
| RF-26 | Reiniciar demo y reloj acelerado. El estado mutable del demo (tarjetas de Martina) vive por cliente en la tabla Demo: o se hace un overlay por sesión, o se acepta que es compartido y se avisa en la landing. Decidir el mié 30 con Andrés | después (jue 1 si Andrés llega) | [P§12] |
| RF-27 | Ambigüedad multilingüe: idioma detectado por turno con una regla (palabras marcadoras ES/PT); se responde en el idioma del mensaje, o se pregunta si es ambiguo o mezclado; `reply_language` sale de esa regla (la sesión solo da el valor inicial) y el cambio queda en la traza. Escenario de prueba en ES y PT | M2 (jue 1) | [E] fallas |


## 5. Requerimientos no funcionales

| ID | Tema | Objetivo |
|---|---|---|
| RNF-01 | Latencia de turno | p95 < 6 s ([P§11], D5), p50 < 3 s (propuesta). Turnos de plantilla < 1 s en caliente. Aviso "tarda" a los 6 s; el cliente aborta a los 25 s |
| RNF-02 | Cortes | LLM 8 s → plantilla; Lambda 29 s; API Gateway 30 s. Ningún turno termina en error por el modelo |
| RNF-03 | Arranque en frío | < 3 s; medirlo en el primer despliegue con pydantic y PyYAML; `/health` en la landing |
| RNF-04 | Escalabilidad | Throttling de 20 req/s y ráfaga 50, más concurrencia de Lambda de la cuenta y TPM de Bedrock (sección 8). Se esperan < 10 jueces; cualquier cifra mayor es estimación hasta una prueba de carga corta con `smoke_api.py`. Lambda sin estado: nada de sesión en memoria del contenedor en la nube |
| RNF-05 | Seguridad | Render solo como texto; CSP `script-src 'self'`; token nunca en URL ni `localStorage`; permisos solo en el servidor |
| RNF-06 | Privacidad | Nunca documento, dirección ni teléfono; tarjeta solo con últimos 4; solo nombre de pila; `customer_id` nunca sale del servidor. Texto libre del cliente: se redacta antes de llamar al LLM y antes de guardar `customer_statement` (patrones de documento, teléfono, correo y tarjeta completa en ES y PT), porque ese texto llega a la consola y el enunciado prohíbe mandar datos privados a modelos externos. Un pytest y un escenario de smoke |
| RNF-07 | i18n | es (es-MX, es-CO, es-AR) y pt-BR, respaldo es; mismas claves en `es.ts` y `pt.ts` verificado por test; montos con `Intl.NumberFormat` desde el string decimal |
| RNF-08 | Accesibilidad | WCAG 2.2 AA (propuesta): contraste 4,5:1, foco visible, estado nunca solo por color, reflow a 320 px, zoom 200 %, `prefers-reduced-motion`, objetivos táctiles ≥ 44 px en móvil |
| RNF-09 | Robustez | Reintentos automáticos solo ante red, 429, 502, 503, 504; máximo 2 (1 s, 3 s + jitter); idempotencia por `client_msg_id`; 30 turnos por sesión (`session_limit`, sin reintento); recarga no pierde la conversación |
| RNF-10 | Costo | < 0,05 USD por conversación ([P§10]); sondeo ≈ 2,7×10⁻⁶ USD por petición (estimado: HTTP API 1 USD por millón + Lambda de 1769 MB con ~50 ms facturados y su cargo por invocación; sin CloudFront ni lecturas de DynamoDB) |
| RNF-11 | Peso | Página inicial < 250 KB sin comprimir; sin fuentes externas |
| RNF-12 | Navegadores | Últimas 2 versiones de Chrome, Edge, Firefox y Safari |
| RNF-13 | Reproducibilidad | Desde un clon limpio: `npm ci && npm run build` en `frontend/` (Node 20+) y el venv de Python; `build.txt` identifica la versión publicada |


## 6. Frontend

Actualizado lun 28, 20:40: cuando se escribió la primera versión, Node no estaba instalado y por eso se recomendaba un front sin build. Arturo ya instaló Node (v24.19.0, npm 11.17.0), así que la recomendación cambia. Decide Arturo con Diego.

**Recomendado: React + Vite + TypeScript**, con CSS propio (variables en `:root`, CSS Modules que Vite trae de fábrica), sin librería de componentes ni Tailwind para no sumar configuración. Por qué:
- El plan v2 ya asume React ("If the React front end is late on Wednesday"): no es un cambio de rumbo y no necesita entrada en DECISIONS.md.
- El CDK de Andrés ya espera un build de Vite: `web_stack.py` sube `frontend/dist`, que es la carpeta de salida por defecto de Vite. Andrés ya tiene Node (lo pide `aws-cdk`) y el job `infra` del CI instala Node 20.
- La consola del analista (cola, reporte con citas, edición con diferencia, estados vacíos y de error, atajos) es donde un framework ahorra más: componentes, estado y tipos compartidos con el contrato de la API.
- React escapa todo el texto por defecto; la regla de XSS se reduce a prohibir `dangerouslySetInnerHTML` (test estático).
- TypeScript permite escribir los tipos de #1 y #3 una vez y que el front no se desvíe del contrato.

**Lo que hay que resolver por usar React:**
- `.gitignore` del equipo ignora `*.json`, así que `package.json`, `package-lock.json` y `tsconfig.json` no se subirían. Andrés ya lo anticipó en STATUS.md ("package.json will need to be committed"). Propuesta al equipo: agregar excepciones `!frontend/package.json`, `!frontend/package-lock.json`, `!frontend/tsconfig*.json`. En el repo personal no hay problema (su `.gitignore` no ignora esos archivos).
- `frontend/dist` es generado: quien despliega corre `npm ci && npm run build` en `frontend/` antes de `cdk deploy`, o `web_stack.py` sube el placeholder sin avisar. Mitigación: `build.txt` con el sha dentro de `dist` y `smoke_api.py` lo verifica en la URL. Alternativa: commitear `dist` (decide el equipo).
- Setup de esta noche: unos 20-30 minutos (`npm create vite@latest frontend -- --template react-ts` y limpiar la plantilla).

**Alternativa de respaldo:** HTML + módulos ES sin framework ni build, servido tal cual desde `frontend/dist` (era la recomendación original). Sirve si el setup de React se traba esta noche o si Diego no puede usar Node. La regla de recorte del plan ya prevé pasar a algo más simple el miércoles si React va atrasado.

**Dependencias (mínimas):** `react`, `react-dom`; de desarrollo `vite`, `@vitejs/plugin-react`, `typescript`, y desde mañana `vitest` y `@testing-library/react` para pruebas de componentes. Nada de librerías de estado, router ni i18n: hash router propio, diccionarios `es.ts` / `pt.ts`.

**Estructura (todo nuevo):**
```
frontend/
  README.md            cómo correr; reglas: sin dangerouslySetInnerHTML, solo rutas /api relativas
  package.json, package-lock.json, tsconfig.json, vite.config.ts
  index.html           CSP en <meta>
  public/build.txt     versión publicada (se copia a dist)
  src/
    main.tsx, App.tsx  hash router (#/, #/chat, #/consola, #/traza/<id>, #/evaluacion)
    styles/tokens.css  colores, tipografía, espacios; claro/oscuro
    api/types.ts       tipos de #1 y #3 (customer_view, errores)
    api/client.ts      fetch /api/*: timeout 25 s, reintentos acotados, client_msg_id, ramifica por status y luego por code
    api/config.ts      {sessionHeader: "authorization", stage}; cambiar el header no toca las vistas
    session.ts         token en memoria + sessionStorage; transcript en sessionStorage; se borra ante session_expired
    format.ts          Intl de montos y fechas
    i18n/ es.ts pt.ts index.ts
    demo/customers.ts  6 demo_key con nombre, idioma, qué demuestra, activo/próximo (sin customer_id)
    components/        Message, ClarifyButtons, Composer, CaseCard, TraceStrip, SourceBadge (Regla, ML, GenAI, Plantilla...)
    views/             Judge, Chat (M1); Console (mié); Trace (jue); Evaluation (vie)
  dist/                generado por `npm run build`; es lo que sube el CDK
scripts/local_api.py   API local (sección 7)
scripts/smoke_api.py   pruebas contra BASE_URL (sección 8)
tests/test_frontend_static.py  prohíbe dangerouslySetInnerHTML, eval, new Function, localStorage y URLs
                       http(s) externas salvo una lista permitida (solo el endpoint de Cognito de la región,
                       para la consola); claves iguales en es.ts y pt.ts; frases de reembolso o
                       compensación prohibidas en ES y PT
```

**Cómo corre local:** dos procesos. `.venv\Scripts\python scripts\local_api.py --port 8000` (la API) y, en `frontend/`, `npm run dev` (Vite en el puerto 5173). `vite.config.ts` redirige `/api/*` a `http://localhost:8000` quitando el prefijo `/api`, igual que la CloudFront Function; el navegador ve un solo origen y no hay CORS. Para probar el build real: `npm run build` y `local_api.py` sirve `frontend/dist`.

**Cómo llega a S3/CloudFront:** `npm ci && npm run build` en `frontend/`, luego Andrés hace synth y deploy; `web_stack.py` sube `frontend/dist` y `BucketDeployment` invalida `/*`. `build.txt` (sha y hora) en la raíz del sitio para detectar si se sirve el placeholder o una versión vieja; `smoke_api.py` lo lee en la URL. Vite ya pone hash en los nombres de los archivos JS y CSS, así que solo `index.html` necesita `Cache-Control: no-cache` (propuesta → Andrés).

**Qué instalar:** Node ya está en la máquina de Arturo. Diego necesita Node 20 o superior si toma piezas del front; preguntarle esta noche.

**Qué acordar del `.gitignore`** (pregunta abierta en STATUS.md desde el 28 a las 03:30Z): las excepciones de `frontend/` de arriba, y además `*.json` deja fuera `tests/fixtures/llm/` del proveedor mock de #8 (Andrés) y el reporte de evaluación (Cristhian). Es una pregunta del equipo; si se cambia, va a DECISIONS.md con las palabras de quien decide.


## 7. Backend

**Rutas que ya existen y se usan** (`api_stack.py`, todas a `handlers.api.handler`, hoy stub con `/health` y 501 al resto):

| Ruta | Auth | Uso |
|---|---|---|
| `GET /health` | pública | warm-up desde la landing (ya responde) |
| `POST /session` | pública, token propio | M1 |
| `POST /chat` | pública, token propio | M1 |
| `GET /cases/{case_id}` | pública, token propio | M1 (al abrir caso, recargar y volver el foco); sondeo desde mié |
| `GET /analyst/cases`, `GET /analyst/cases/{case_id}`, `POST /analyst/cases/{case_id}/decision` | JWT Cognito | mié 30 |

**Forma propuesta para M1** (propuesta a #1 y #3, dueño Arturo; se escribe en INTERFACES.md y se avisa a Diego):

Token: `Authorization: Bearer <session_token>` en todo salvo `POST /session`; se quita `session_token` del body. Plan B desde el primer despliegue: header `x-ev-session` (el nombre sale de `config.ts`).

`POST /session`
```
req  {demo_key: "lucia"|"sofia"|"andres"|"joao"|"martina"|"carlos", channel: "web", language?: "es"|"pt"}
200  {session_token, expires_at (ISO), customer: {display_name, language, locale, country}, synthetic: true}
400  invalid_request si demo_key no está en la lista permitida
```
Token opaco `secrets.token_urlsafe(32)`; el store guarda solo su hash. Es un cambio respecto del plan, que describe la sesión del cliente como "JWT valid 15 minutes plus DynamoDB TTL" (fila de sesión expirada): va como decisión 9 de la sección 10. `expires_at` se revisa en cada lectura. En DynamoDB la tabla Sessions tiene el TTL en el atributo `expires_at` (`data_stack.py` línea 35), que debe ser epoch numérico; la API devuelve ISO calculado. Acordar ese mapeo con Andrés.

`POST /chat`
```
req  {client_msg_id: uuid, message?: str ≤ 1000 | button_id?: str}      exactamente uno
200  {turn, reply_text, reply_language, reply_source: "template"|"model",
      buttons: [{id, label, kind: "confirm"|"deny"|"choice"|"handoff"}],
      input_mode: "free_text"|"buttons_only",
      progress: {step: "understand"|"find"|"verify"|"case_open"|"done",
                 claimed: {amount, currency, date, merchant_text, card_last4}, missing: [slot]},
      case_card: <customer_view> | null, lane: "A"|"B"|"C"|null,
      degraded: [] | ["model_timeout"|"tool_unavailable"],
      trace_id,
      trace_summary: {steps: [{actor, name, latency_ms, version, error_code}], latency_ms, cost_usd, model_id|null},
      poll_after_ms: int|null, synthetic: true}
```
- `progress` es estado de la conversación, no del caso: no va en `customer_view` ni en #3.
- `trace_summary` usa los nombres de #7 y no lleva texto libre; solo en sesiones demo. La vista de trazas del jueves reutiliza la forma.
- `button_id` lo emite el servidor (p. ej. `confirm:<nonce>`) y guarda los pendientes en la sesión; vencido o ajeno → 409 `conflict`. Así "Sí" nunca se interpreta desde el label en ES o PT.
- Idempotencia: el mismo `client_msg_id` devuelve la misma respuesta guardada; nunca abre un segundo caso. En vuelo → 409 `conflict` con `retryable: true`. Hasta 30 entradas por sesión, que coincide con el tope de 30 turnos (`session_limit`, no `rate_limited`: el 429 queda solo para throttling).
- `poll_after_ms` es null en M1; desde el miércoles es la palanca del servidor para bajar el sondeo sin redesplegar el front.

`GET /cases/{case_id}` → `{case: <customer_view>, poll_after_ms}`; 403 `not_authorized` igual si es ajeno o no existe.

`customer_view` (#3, `case.py`): se agregan `language`, `synthetic: true` y `lane_reason_code` (M1, el front lo traduce; lo pide RF-05), y desde el miércoles `resolution {language, text, sent_at}` y los estados de ruta A resuelta y ruta C derivada. No se agregan `rule_id` ni probabilidades (van en `trace_summary`).

Errores `{error: {code, message, retryable}}` (se amplía la lista de #1):

| code | HTTP | La UI |
|---|---|---|
| `invalid_request` | 400 | mensaje en línea, no reintenta |
| `session_expired` | 401 | borra todo, "Volver a empezar" |
| `not_authorized` | 403 | mensaje neutro, sin datos |
| `conflict` | 409 | refresca estado; si `retryable`, repite el mismo `client_msg_id` a 1 s |
| `session_limit` | 409, `retryable: false` | "Llegaste al máximo de mensajes", "Volver a empezar"; no reintenta |
| `rate_limited` | 429 (solo throttling) | backoff |
| `tool_unavailable` | 503 solo en lecturas; en `/chat` es 200 con `degraded` y ruta C | aviso y Reintentar |
| `model_timeout` | nunca en `/chat` (200 con plantilla) | rótulo "Plantilla" |
| `not_implemented` | 501 | "disponible pronto" (ya lo usa el stub) |
| `internal` | 500 | Reintentar |

El 429 de throttling, el 401 del autorizador, el 404 de ruta y los 5xx de API Gateway llegan como `{"message": ...}`: `api/client.ts` ramifica primero por status y luego por `code`.

**Piezas del backend (repo personal primero, mismas rutas de archivo que en el repo del equipo):**
- `src/handlers/api.py`: router por `routeKey`, mismo warm-up que el stub; parsea, autentica la sesión, llama al orquestador, responde con `Cache-Control: no-store`. Importa el orquestador (pydantic, PyYAML) de forma diferida, dentro de las rutas que lo usan, para que `/health` responda aunque la Lambda se despliegue sin bundle e informe `deps: ok|missing`; la landing distingue "faltan dependencias" de "iniciando…".
- `src/conversation/orchestrator.py` (nuevo): `understand → find → verify → decide → case_open`. `decide` llama a `lane_rules.evaluate`. Lucía confirma el cargo ("Es este") y luego "No lo reconozco" → `unrecognized_low_risk` → B; con "Sí, lo reconozco" → `customer_recognizes` → A (esa regla va antes en el YAML, así que el camino A de la demo final no cambia). João confirma el cargo y no se le pregunta si lo reconoce (RF-04) → `fee_does_not_match` → B; pytest que lo cubra, porque si la confirmación fijara `customer_recognizes` caería en A. Andrés (cliente demo) → `duplicate_not_reversed` → B.
- Cómo se llena `Evidence` en M1 (sin M2 de Cristhian). Todo va a los pytest de mañana:

| Campo | De dónde sale en M1 |
|---|---|
| `identity_verified` | `True` si la sesión es válida (lista permitida + token vigente); nunca del texto |
| `complaint_type` | Botón o palabras clave ES/PT; marcado provisional hasta M2 |
| `evidence_complete` | Lista mínima por tipo: `unrecognized_charge` → cargo confirmado y respuesta a "¿Lo reconoces?"; `wrong_fee` → cargo confirmado y tarifa consultada. Vive en código del orquestador (hoy no existe en el YAML ni en `lane_rules.py`) |
| `charge_confirmed` / `customer_recognizes` | Botones de RF-04; el segundo solo en `unrecognized_charge` |
| `amount_usd`, `fraud_score`, `fee_matches_schedule`, duplicados | Fixture del gateway demo |
| Banderas (`asks_for_human`, etc.) | Lista de patrones ES/PT; G1 las complementa cuando exista |

- Extracción G1 a través de un cliente con la firma `complete()` de #8 (inyectado); con timeout, proveedor mock o sin `src/llm` cae a un extractor determinista por regex en ES y PT (monto, "el 12", "dia 12", comercio), con `reply_source: "template"` y `degraded` visible. El modo degradado es el mismo camino de código. El CDK pone `LLM_PROVIDER=bedrock` (`common.py`), pero `src/llm` no existe: si no llega el martes con el proveedor bedrock, el M1 en la URL se muestra sin GenAI y el p95 < 6 s queda sin medir. Hay que decirlo así en la demo.
- `src/conversation/templates/es.yaml` y `pt.yaml` (nuevo) con registro por país. No en `templates/` de la raíz: esa carpeta ya existe (README.md, pitch.md) y el asset de la Lambda es solo `src/` (`common.py`: `SRC_DIR = REPO_ROOT / "src"`).
- Ranker M1 provisional por regla (monto ±2 %, fecha ±1 día) con la firma de #4, hasta que Cristhian entregue M1 v1.
- Dependencias inyectadas detrás de protocolos: `SessionStore`, `CaseStore`, `TraceSink`, gateway, llm, ranker. Hoy no existen `src/gateway`, `src/llm` ni `src/models`.
- El código de `src/conversation` debe correr en Python 3.11 (el job `checks` del CI usa 3.11; la Lambda, 3.12). Una búsqueda rápida no encontró sintaxis exclusiva de 3.12 en `case.py` ni `lane_rules.py`, pero eso no lo prueba. En la máquina solo hay un Python 3.11 de la Store sin venv; armar uno con pydantic, PyYAML y pytest es una instalación que Arturo tiene que aprobar. Sin esa aprobación, la vía es proponer a Andrés subir el job `checks` a 3.12.

**Propuestas a Andrés** (dueño de almacenamiento, gateway, LLM, stacks):
1. Protocolo `SessionStore` / `CaseStore` / `TraceSink`; Arturo escribe la implementación en memoria, Andrés la de DynamoDB. Selección con `STORE_BACKEND=memory|dynamodb` (nueva en #9) que falla si no está definida fuera de `STAGE=local`, en vez de caer en silencio a memoria.
2. Gateway demo de fixtures sintéticos para los 6 clientes, con la forma de #2 (`find_candidate_txns`, `get_transaction`, `find_duplicates`, `get_reversals`, `get_fee_schedule`…), alcance por `ctx.customer_id` y `NotOwned`. Tiene que vivir en `src/` para que la Lambda lo tenga; Andrés decide la ruta exacta (p. ej. como backend `demo` de `src/gateway`). Datos en YAML commiteado, nunca en `data/` (ignorado) ni en JSON. El plan ya dice que los clientes demo viven en un demo store y nunca en gold. Dos opciones, decide Andrés: YAML de solo lectura empaquetado en la Lambda para M1, o cargar esos mismos fixtures en la tabla Demo que ya crea `data_stack.py` (`TABLE_DEMO` en #9). El estado que cambia (bloqueo de tarjeta de Martina, reset del demo) no puede vivir en un YAML empaquetado: desde el miércoles va en la tabla Demo.
3. `pydantic` y `PyYAML` en `src/requirements-lambda.txt` (hoy solo comentarios) y synth con `-c bundle=true`, que usa la imagen de build de Python 3.12 y necesita Docker (`cdk.json` tiene `bundle: false`). Preguntar esta noche si tiene Docker; alternativa suya: wheels manylinux con `pip --platform`. Sin esto `case.py` y `lane_rules.py` no importan en la Lambda.
4. Probar `Authorization` en GET por CloudFront en el primer despliegue (sección 8).
5. `make test` corre pytest, y `pytest` y `PyYAML` entran a `requirements.txt` (hoy `make test` solo imprime "no tests yet" y el CI no ejecuta ninguna prueba). El contrato del Makefile permite llenar el cuerpo del target sin renombrarlo; se le avisa a Andrés porque el CI y `requirements.txt` los armó él.

**Rutas y campos para después** (un solo paquete el miércoles):
- `GET /analyst/cases?status=&lane=&language=&limit≤50&cursor=` → `{items[{case_id, updated_at, status, lane, lane_rule_id, queue, priority, language, intent_class, intent_p, breach_at, has_report, report_reliable}], next_cursor, as_of, poll_after_ms}`.
- `GET /analyst/cases/{id}` → `{case (vista analista, sin lifecycle ni PII), report: #6|null, context: {profile, cards[{card_last4, status}], evidence_txns, prior_contacts, risk_evidence}, allowed_actions[], version}`. Propuestas → Andrés: `GatewayContext.for_analyst(case_id, analyst_sub)` en #2, que toma el `customer_id` del caso y nunca del request; e instantánea `evidence_records` guardada junto al reporte #6 para abrir citas sin otra ruta.
- `POST /analyst/cases/{id}/decision` `{client_decision_id, version, action: approve|edit|reject, reply?: {language, text}, reason?, next?: escalate|request_information, labels?: {intent_class?, intent_confirmed?}}` → `{case_id, status, analyst_decision{action, edited, reason, decided_by, decided_at}, labels_emitted[]}` o 409. `decided_by` sale del `sub` del JWT. En #3 faltan `decided_by`, texto antes y después, `version`, `updated_at` y `lifecycle{execution_arn, task_token}` oculto.
- Jueves: `GET /traces/{trace_id}`, interruptores por sesión, reinicio de demo (Andrés); evaluación como archivo estático o ruta (Cristhian).

**Local, reutilizando `src/handlers/api.py`:** `scripts/local_api.py` (nuevo, biblioteca estándar, `ThreadingHTTPServer`; puede llenar el target `up` del Makefile, que es de Arturo):
Hoy solo 1, 2 y 4 (versión mínima); 3 y el resto de 5, mañana.
1. Sirve `frontend/dist` en `/` (para probar el build; en desarrollo el front lo sirve `npm run dev` con proxy a esta API).
2. En `/api/*` quita el prefijo con la misma regex que la CloudFront Function (`^/api`).
3. Resuelve `routeKey` y `pathParameters` desde una copia de `PUBLIC_ROUTES` y `ANALYST_ROUTES`; un test compara la copia leyendo `infra/stacks/api_stack.py` como texto (aws_cdk no está en el venv personal).
4. Arma un evento HTTP API v2 y llama `handlers.api.handler(event, ctx_falso_29s)`.
5. Imita a API Gateway: 404 `{"message":"Not Found"}`, corte a 30 s y, desde el miércoles, 401 en `/analyst` sin `Bearer $LOCAL_ANALYST_TOKEN` (leído de `.env` solo en `scripts/`; inyecta claims falsos). En `src/` no hay ningún bypass: el handler responde 401 si no hay claims, y un test comprueba que `src/` no menciona `LOCAL_ANALYST_TOKEN`.
6. Flags `--fail tools` y `--ttl-minutes 1` para probar fallas.

`.env` local: `STAGE=local`, `LLM_PROVIDER=mock`, `SESSION_TTL_MINUTES=15`, `LLM_TIMEOUT_SECONDS=8`, `STORE_BACKEND=memory`.

**Almacenamiento local:** sesiones (hash del token, `expires_at`, cliente demo, estado de conversación, botones pendientes, respuestas por `client_msg_id`) y casos en memoria del proceso; se pierden al reiniciar el servidor, lo cual es aceptable en local. Trazas en JSONL en `data/traces/` como dice #7 (carpeta ignorada). Mensajes: no hay almacén de mensajes en M1; el transcript vive en `sessionStorage` de la pestaña y se borra ante `session_expired`. Nunca se despliega el store en memoria como si funcionara: en la nube las sesiones se perderían entre contenedores.


## 8. Tiempo real, escalabilidad y seguridad

**Tiempo real.** HTTP API no tiene WebSocket. El turno de chat es request/response dentro del corte de 30 s.
- M1: sin sondeo. `GET /cases/{id}` al abrir el caso, al recargar y al volver el foco. El caso no cambia solo hasta que exista el ciclo de vida de Andrés (mié 30).
- Desde el miércoles: tarjeta cada `poll_after_ms` (5 s por defecto) ±20 % de jitter, solo si el estado es `investigating` o `awaiting_analyst`, la pestaña está visible y la sesión vigente; una petición en vuelo; backoff 5 → 10 → 20 → 60 s ante 429 o 5xx; se detiene con 401/403. Consola: cola cada 15 s.
- Long-polling descartado: ocupa una Lambda de 1769 MB por espera y choca con los 30 s.

**Números contra 20 req/s (ráfaga 50).** El CDK lo pone en `default_route_settings`; hay que confirmar con Andrés si en la práctica es por ruta o global; asumo global, que es el peor caso.

| Fuente | req/s por pestaña |
|---|---|
| Chat activo (1 turno cada ~20 s) | 0,05 |
| Tarjeta sondeando (5 s) | 0,20 |
| Consola (cola 15 s) | 0,07 |
| Juez con las tres | ≈ 0,32 |

Diez jueces ≈ 3,2 req/s (16 % del tope); contra el throttling el techo ronda 60 jueces, pero no es el único límite. La ráfaga de 50 cubre que varios abran la landing a la vez. Un script agresivo de un juez sí puede afectar a los demás; en producción se resuelve con WAF por IP.

| Límite | Cuenta | Dato que falta |
|---|---|---|
| Concurrencia de Lambda | jueces × 0,05 turnos/s × hasta 8 s de LLM por turno (10 jueces ≈ 4 ejecuciones simultáneas, más el sondeo) | Límite de concurrencia de la cuenta; las cuentas nuevas pueden tenerlo bajo (Andrés) |
| TPM de Bedrock (Haiku) | jueces × 3 turnos/min × tokens por turno de G1 | Cuota de tokens por minuto; el plan pide revisarla (Andrés) |

Todas las cifras de esta sección son estimación hasta hacer una prueba de carga corta con `smoke_api.py` contra la URL.

**XSS.** Todo texto dinámico (cliente, modelo, comercio, reporte, traza) se renderiza como texto con el escape por defecto de React; `dangerouslySetInnerHTML` está prohibido. Sin markdown; URLs como texto plano; nunca imágenes remotas; en la consola se neutralizan caracteres bidi y de ancho cero. El riesgo real es un XSS almacenado de un visitante anónimo que se ejecute en la consola con el JWT del analista. `test_frontend_static.py` lo hace cumplir.

**CSP** en `<meta>`: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'`. Cuando llegue la consola, `connect-src` agrega `https://cognito-idp.us-east-2.amazonaws.com` (región us-east-2 según `config.py` y `cdk.json`); es la única URL externa en la lista permitida de `test_frontend_static.py`. `frame-ancestors` no funciona en `<meta>`: va como header en una ResponseHeadersPolicy de CloudFront (propuesta → Andrés, con HSTS, nosniff y `Referrer-Policy: no-referrer`).

**Tokens.**
- Cliente: memoria + `sessionStorage` (por pestaña, sobrevive a recargar); nunca en URL, `localStorage` ni logs; siempre en header.
- Riesgo alto: en `web_stack.py` el comportamiento `/api/*` usa `CACHING_DISABLED` con `ALL_VIEWER_EXCEPT_HOST_HEADER`. Según la documentación de CloudFront, en GET y HEAD el header `Authorization` solo llega al origen si está en la cache policy; `POST /chat` no se afecta, `GET /cases/{id}` probablemente sí. Probarlo en el primer despliegue con `curl` por CloudFront frente al `execute-api` directo. Plan B cliente: `x-ev-session` (mismo origen, sin preflight, la política lo reenvía); se cambia en `config.ts`. Plan B analista: cache policy propia con `Authorization` (Andrés), porque el autorizador JWT lee ese header.
- Analista: Cognito sin auto-registro, solo SRP, tokens de 1 h. Propuesta → Andrés: habilitar `USER_PASSWORD_AUTH` para hacer `InitiateAuth` con `fetch` sin librería SRP, y un usuario analista demo con credenciales solo en la entrega, nunca en el repo. JWT solo en memoria; al vencer se vuelve a entrar sin perder el borrador.

**Auditoría.** Cada decisión del analista registra `decided_by` desde el claim (nunca del body), hora, acción, texto antes y después y motivo, como evento de traza `actor: human`. Decisión inmutable: la segunda da 409.

**Datos mínimos.** Una sola función del servidor por rol (`customer_view`, y `analyst_view` el miércoles). Nada de documento, dirección ni teléfono; tarjeta por últimos 4; en la URL solo `case_id` o `trace_id`, nunca ids de cliente. Nada mueve dinero; no existe control de reembolso ni de desbloqueo; un test prohíbe frases de reembolso o compensación en ES y PT. Los mensajes sugeridos del modo juez son distintos de los mensajes de prueba del equipo, y las trazas de sesiones demo llevan `source=demo` para quedar fuera de prompts y entrenamiento.

**Pruebas hold-under-fire.** Nada se declara "funciona" sin evidencia; si solo pasa en local, se dice "funciona en local".
1. pytest del handler con eventos v2: sesión expirada (reloj inyectado), caso ajeno e inexistente con 403 idéntico, inyección → plantilla sin acciones, herramienta caída → C, modelo lento (fake de 9 s) → plantilla, mismo `client_msg_id` → misma respuesta y un solo caso, João con `reply_language == "pt"` en cada turno, João confirma el cargo → B `fee_does_not_match` (no A), Lucía "No lo reconozco" → B con `EV-` y fecha, texto con documento o teléfono → redactado antes del LLM y en `customer_statement`, turno 31 → `session_limit`.
2. `scripts/smoke_api.py BASE_URL` (solo urllib), PASS/FAIL por escenario, en local y contra la URL; incluye `build.txt`, `Authorization` en GET y 404 de ruta inexistente.
3. Recorrido manual en el navegador, en incógnito, con capturas.


## 9. Ruta

Supuestos: Arturo trabaja solo en backend y front; Diego ayuda en el front en piezas acotadas (i18n PT, estilos de la tarjeta) porque también tiene su backfill; Andrés despliega. Todo se escribe primero en el repo personal; en el repo del equipo no se hace commit ni push sin confirmación de Arturo.

### Esta noche, lunes 28

Meta de esta noche = lo que el plan pide para el lun 28 (chat shell contra la API local y landing), en versión mínima. La lectura de este documento ya consume parte del primer bloque.

| Hora | Bloque | Listo cuando |
|---|---|---|
| 20:00–20:45 | Arturo lee y decide los puntos de la sección 10 marcados "esta noche". Mensajes a Andrés y Diego con las preguntas de abajo; a Diego, visto bueno al stack antes de escribir código de front. Borrador de #1/#3 y línea de claim (ver abajo) | Mensajes enviados |
| 20:45–21:00 | `npm create vite@latest frontend -- --template react-ts` y proxy `/api` en `vite.config.ts`; `scripts/local_api.py` mínimo (`/api` → handler) + `src/handlers/api.py` con `/health` (importación diferida); la app llama `/api/health` | El navegador muestra "API ok · stage local" |
| 21:00–21:20 | Demo interna: stack, contrato de M1, bloqueantes | Cada pregunta con dueño y hora de respuesta |
| 21:20–22:30 | `POST /session` (lista permitida, store en memoria), `POST /chat` con respuesta de plantilla fija, errores `invalid_request` y `session_expired`; `dom.js`, `api/client.ts` sin reintentos, vista del chat | Ida y vuelta en el navegador; pytest de 400 y 401 en verde |
| 22:30–23:15 | Landing estática con 2 tarjetas (Lucía, João), etiquetas de sintético, "Volver a empezar" ante `session_expired` | Entrar como Lucía o João desde la landing en incógnito |
| 23:15–24:00 | Colchón. Prueba rápida: pegar `<img src=x onerror=alert(1)>` y JSON inválido. Nota del avance | Lista de pendientes para mañana |

Recorte de esta noche: si a las 22:30 el chat no hace ida y vuelta, la landing pasa a mañana y se entra al chat con un enlace fijo a Lucía.

Pasa a mañana temprano (no entra esta noche): extractor regex, botones con nonce, idempotencia, reintentos de `api/client.ts`, token hasheado, paridad de rutas con `api_stack.py`, `--ttl-minutes`, `test_frontend_static.py`, `es.ts`/`pt.ts` y las 6 tarjetas.

Coordinación antes de construir. El RUNBOOK dice que el trabajo se reclama en STATUS.md antes de construir y que STATUS.md e INTERFACES.md se commitean directo a `main`, separados del código. Propuesta a Arturo: publicar esta noche, con su confirmación, la línea de claim y el borrador de #1/#3 directo a `main`, sin esperar el PR de código. Si prefiere no tocar el repo del equipo hoy, queda escrito como desviación consciente de claim-before-build y se publica en el stand-up de las 09:00.

Despliegue hoy (D8). El plan pide para el lun 28 "Walking skeleton: public URL, sign-in, chat round trip through Lambda and Bedrock", y D8 pide algo desplegado al final de cada día. Esta ruta es solo local. Lo que sí se podría desplegar hoy, si Andrés puede: la landing estática con `/health` (`web_stack.py` ya sube `frontend/dist` si existe). Si no se hace, el lun 28 no cumple D8; el motivo lo escribe Arturo: ____.

Preguntas de esta noche:
- Andrés: ¿Docker para `-c bundle=true`, y agrega `pydantic` y `PyYAML` a `requirements-lambda.txt`? ¿Store DynamoDB contra el protocolo propuesto y `STORE_BACKEND`? ¿Gateway demo como YAML en `src/` o fixtures en la tabla Demo? ¿Qué habrá mañana de `src/llm`: mock, y proveedor bedrock en el stage desplegado? ¿Qué puede entregar de verdad el martes además de lo que el plan ya le asigna (gateway DuckDB y DynamoDB, pipeline en Lambda, importación a DynamoDB)? ¿Puede desplegar un stage personal desde la rama sin esperar el merge? ¿Puede desplegar hoy la landing con `/health`? ¿Límite de concurrencia de Lambda de la cuenta y TPM de Haiku? ¿Throttling global o por ruta? ¿Mapeo de `expires_at` epoch/ISO? ¿`make test` con pytest?
- Diego: país, moneda y cargo de João, y su regla de comisión, en un fixture sintético para M1 (la tabla real es del miércoles). ¿Tiene Node? ¿De acuerdo con React + Vite + TypeScript? ¿Qué piezas del front toma?
- Cristhian: nada bloqueante; confirmar que la firma de M1 en #4 es estable para usar un ranker por regla mientras tanto.
- Equipo: `.gitignore` con `*.json` (afecta fixtures del mock LLM y el reporte de evaluación).

### Mañana, martes 29 (M1)

| Hora | Bloque | Listo cuando | Depende de |
|---|---|---|---|
| 07:30–09:00 | Pendientes de anoche: token hasheado, paridad de rutas con `api_stack.py`, `test_frontend_static.py`, `es.ts`/`pt.ts`, las 6 tarjetas; borrador de #1 y #3 listo si no se publicó anoche | Tests en verde en local | — |
| 09:00 | Stand-up: cerrar decisiones de la sección 10; las de rumbo van a DECISIONS.md con las palabras de quien decide | Decisiones escritas | Todos |
| 09:15–12:00 | Orquestador ruta B: extractor regex, find (gateway de fixtures) → ranker por regla → verify con `button_id` con nonce (RF-04, dos preguntas) → `Evidence` según la tabla de la sección 7 → `lane_rules.evaluate` → `CaseRecord` con `promise` y `clock` → `customer_view` con `lane_reason_code`. Idempotencia, redacción de texto libre. Plantillas ES y PT. Trazas a JSONL. Pruebas en 3.11 solo si Arturo aprobó el entorno | pytest: Lucía "No lo reconozco" → B `unrecognized_low_risk`; João confirma el cargo → B `fee_does_not_match`; ambos con `EV-` y fecha prometida; idempotencia y botón vencido cubiertos | Fixture de João (Diego); si no llega, fixture de Arturo marcado "supuesto" |
| 12:00–13:00 | PR en el repo del equipo (rama `arturo/chat-api-m1`): `src/conversation`, `src/handlers/api.py`, gateway demo propuesto, `scripts/local_api.py`, tests y el chat shell con la landing (`frontend/dist`); INTERFACES.md #1 y #3 van aparte, directo a `main`, si no se publicaron anoche; aviso a Diego y Andrés | PR abierto con la salida de pytest local en 3.12 pegada (el CI no corre pruebas hasta que `make test` lo haga) | Confirmación de Arturo para commit y push |
| 13:00–13:45 | Almuerzo | | |
| 13:45–15:45 | Tarjeta en vivo (`progress` + `case_card`), `GET /cases/{id}` con dueño verificado, tira de traza desde `trace_summary` | La tarjeta pasa por los 4 pasos en ES y PT; "sin verificar" y "registro del banco" separados | — |
| 15:45–16:45 | João en PT de punta a punta (Diego en `pt.ts` y formato de montos); modo degradado (LLM fake lento → plantilla); `smoke_api.py` | Smoke en verde en local | País y moneda de João |
| 16:45–17:15 | Segundo PR del front (tarjeta, tira de traza, `build.txt`) | PR abierto | Revisión de Andrés o Diego |
| 17:15–19:30 | Con Andrés: despliegue en un stage personal desde la rama (sin esperar el merge, si Andrés acepta) y verificación en la URL: `build.txt`, `/health` con `deps`, `Authorization` en GET (activar `x-ev-session` si falla), smoke, recorrido en incógnito de Lucía y João, arranque en frío | Smoke en verde en la URL, anotado en STATUS.md; o el motivo exacto de lo que falla | Store DynamoDB, bundle, gateway demo, `src/llm` con bedrock (Andrés) |
| 19:30–20:45 | Arreglos, ensayo del guion (Lucía B, João PT, sesión expirada, caso ajeno) y video de respaldo del recorrido | Guion de 3 min | — |
| 21:00 | Demo interna M1 | Ruta B de punta a punta, en la URL o declarada "en local" | — |

Plan B, en este orden:
1. Fixture de João no llega o su camino falla: M1 con Lucía y Andrés (cliente demo, `duplicate_not_reversed`, no depende de la tabla de comisiones); João pasa al miércoles temprano.
2. Tira de traza y bloque `trace_summary`: basta el `trace_id` en texto.
3. Tarjeta antes del caso: se muestra solo al abrir el caso; `progress` queda para el miércoles.
4. UI en PT: solo la conversación de João en PT; rótulos en ES anotados como pendientes (el plan pone la UI en PT el jueves).
5. Despliegue: es el camino más probable, no una excepción. El M1 en la URL depende de tres entregas de Andrés (store DynamoDB contra un protocolo que se propone hoy, bundle, gateway demo) sumadas a lo que el plan ya le da el martes. Si a las 19:30 no están, M1 se muestra en local con video, se dice así en la demo y la URL pasa al miércoles temprano. Motivo exacto en STATUS.md. Si falta solo `src/llm`, M1 se muestra en la URL sin GenAI (plantillas y regex) y se dice.
6. Nunca se recortan: lista permitida de sesiones, verificación de dueño, render como texto, ruta decidida por reglas, etiquetas de sintético, manejo de `session_expired`.

Dependencias por persona para mañana:
- Andrés: bundle con Docker, store DynamoDB, decisión sobre el gateway demo, `src/llm` con bedrock, prueba de `Authorization`, despliegue antes de las 19:30. Preguntarle esta noche qué de esto puede entregar de verdad.
- Diego: fixture de João (país, moneda, comisión) antes de las 10:00; `pt.ts` y formato de montos por la tarde.
- Cristhian: ninguna para M1.
- Arturo: todo lo demás.


## 10. Qué necesita decisión

Esta noche o en el stand-up:
1. Lucía en ruta B para M1 respondiendo "No lo reconozco" (desviación del guion del plan §12, que la pone en A). Decide Arturo. Cambia el guion de demo: va a DECISIONS.md con el motivo en palabras de Arturo.
2. Stack del front: React + Vite + TypeScript (lo que asume el plan; no necesita entrada en DECISIONS.md) o el respaldo sin framework (eso sí sería cambio de rumbo, con el motivo en palabras de quien decide). Deciden Arturo y Diego antes de escribir código de front esta noche.
3. `frontend/dist` generado en cada despliegue (`npm run build` antes de `cdk deploy`) o commiteado. Deciden Arturo, Diego y Andrés.
4. Gateway demo: YAML en `src/` o fixtures en la tabla Demo, y dónde. Decide Andrés.
5. `STORE_BACKEND` y mapeo de `expires_at`. Decide Andrés (#9, almacenamiento de #3).
6. País y moneda de João. Deciden Diego y Arturo.
7. `.gitignore` con `*.json`. Decide el equipo; si cambia, va a DECISIONS.md con las palabras de quien decide.
8. CI en 3.11 frente a 3.12, y `make test` con pytest. Decide Andrés.
9. Token de sesión opaco con hash en vez del JWT de 15 min que describe el plan. Decide Arturo (dueño de #1); va a DECISIONS.md con su motivo.
10. Publicar esta noche el claim y el borrador de #1/#3 directo a `main`, o dejarlo como desviación de claim-before-build hasta el stand-up. Decide Arturo.
11. Lun 28 sin despliegue (D8), si Andrés no despliega la landing. Motivo en palabras de Arturo.

Para el miércoles:
12. Login del analista (`USER_PASSWORD_AUTH`) y usuario analista demo para los jueces. Decide Andrés.
13. Si React va atrasado el miércoles, aplicar la regla de recorte del plan (pasar a algo más simple). Deciden Arturo y Diego; va a DECISIONS.md.

No escribo ningún motivo en DECISIONS.md: lo escribe quien decide, con sus palabras.


## 11. Riesgos

| Riesgo | Prob. | Mitigación |
|---|---|---|
| La URL no llega a M1 (store DynamoDB, bundle, gateway o PR tarde) | Alta | Preguntas a Andrés esta noche; PR del backend a las 12:00; plan B 5; video local |
| CloudFront descarta `Authorization` en `GET /cases/{id}` | Alta | Probar en el primer despliegue; `x-ev-session` preparado desde hoy vía `config.ts`; cache policy para el analista |
| Lambda sin pydantic ni PyYAML | Alta | `requirements-lambda.txt` + `bundle=true` con Docker, o wheels manylinux |
| Se publica el placeholder o una versión vieja sin aviso | Media | `npm run build` antes de cada deploy; `build.txt` verificado por `smoke_api.py` |
| Datos de João sin definir | Media | Fixture marcado "supuesto"; reserva con Andrés (cliente demo) |
| Incoherencia Lucía A/B | Alta (ya existe) | Decisión 1; las reglas cubren los dos caminos |
| El código del repo personal no pasa el CI (3.11, `make test` vacío, sin pytest ni PyYAML en `requirements.txt`) | Media | Salida de pytest local pegada en el PR; proponer a Andrés `make test` con pytest y CI en 3.12; 3.11 local solo con aprobación de Arturo |
| M1 en la URL sin GenAI (`src/llm` no llega) | Media | Regex y plantillas con `degraded` visible; decirlo en la demo |
| Setup de React se traba esta noche o se publica el placeholder por falta de build | Media | Respaldo sin framework; `build.txt` verificado por `smoke_api.py`; `npm run build` en la checklist de despliegue |
| XSS almacenado hacia la consola | Baja, alto impacto | Solo `textContent`, test estático, CSP, "dicho" separado de "verificado" |
| Arranque en frío > 3 s | Media | `/health` en la landing, warm-up (Andrés), "iniciando…" |
| Throttling por un juez con script | Baja | Backoff, jitter, una petición en vuelo, `poll_after_ms`; WAF en producción |
| Jueces sin acceso a la consola | Alta el miércoles | Decisión 12 |
| Reloj acelerado (1 día = 1 min) frente a sesión de 15 min: el SLA vence con la sesión ya expirada | Media | Timers visibles en la consola; decidir el jueves con Andrés |
| Arturo sobrecargado (backend + front) | Alta | Diego en piezas acotadas; orden de recorte explícito; nada de consola antes del miércoles |
| Confusión entre Andrés (cliente demo) y Andrés del equipo | Media | Siempre "Andrés (cliente demo)" en UI y docs |
| Decir "funciona" sin prueba | — | Smoke y pytest en local y en la URL antes de afirmarlo |
