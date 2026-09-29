# Latencia y capacidad en local (29 sep)

Medición del costo de nuestro propio código (API del chat, orquestador, G1 mock, consola) con `scripts/load_test.py` contra `scripts/local_api.py`. Sirve para RNF-01 (turno p95 < 6 s, p50 < 3 s propuesto, plantilla < 1 s en caliente; `docs/interfaz_chat_requerimientos.md`) y RNF-04 (capacidad), y para la meta del plan (p95 por turno < 6 s, plan v2 §D5 y tabla de métricas). No mide la nube: ni Bedrock, ni Lambda, ni cold starts, ni red. Eso queda pendiente para esta noche (al final).

Verificación cruzada (29 sep, 10:34–10:45): las 72 filas del anexo son idénticas a las salidas crudas `v3_none.md` y `v3_mock.md` del scratchpad; la tabla de variación coincide con `lat_none`, `lat_none2`, `final_none`, `lat_mock` y `final_mock` (p95, máximos y conexiones rechazadas 11, 19 y 11); la máquina (i5-11320H, 4 núcleos y 8 hilos, 15,8 GB), el pool de 16 hilos, `request_queue_size` 5, los 27 tests de `tests/test_load_test.py` y las cuentas del paso 3 de AWS (72 textos, 39 casos B) se comprobaron. Dos frases corregidas abajo: el investigador sí puede correr dentro de las peticiones medidas de la consola, y el sink de trazas local es el mismo que usará la Lambda con `STORE_BACKEND=memory`.

## En corto

- Con 1 a 20 conversaciones a la vez, sin pausa entre turnos, el p95 de un turno de chat medido por el cliente fue de 46,5 a 342,5 ms sin modelo (`--llm none`) y de 54,2 a 415,9 ms con G1 mock (pasada reportada). El máximo que vi en todas las pasadas fue 1,3 s. Ninguna cifra se acerca a 6 s: nuestro código no es el cuello de botella de RNF-01. Lo que decide RNF-01 es Bedrock, y eso no está medido.
- El orquestador (`trace_summary.latency_ms`) tarda 0 a 4 ms por turno sin modelo; los textos con G1 mock, 5 a 9 ms de mediana. Casi todo lo que ve el cliente es cola, HTTP y trabajo fuera del orquestador, que a 20 conversaciones se reparten un solo proceso Python.
- El servidor local llegó a unas 250 peticiones/s con 5 conversaciones y bajó a 134–151 con 20. Satura un proceso, no una Lambda. Con 10 y 20 conversaciones aparecen conexiones rechazadas en algunas pasadas (backlog de 5 del servidor local); en la pasada que reporto no hubo ninguna.
- Hallazgo propio: la cola de la consola crece con el total de casos en memoria (p95 de 0,9 s a 20 conversaciones). Con un solo hilo, 38 ms con 240 casos y 107 ms con 480. En la nube depende de que `all()` sea una consulta a un GSI y no un scan. Ojo: bajo carga, esa petición también corre las investigaciones vencidas (`advance_all()`, hasta 2 s); el 0,9 s mezcla las dos cosas (ver "Qué NO significa").

## Método

Script: `scripts/load_test.py` (solo biblioteca estándar; pruebas sin red en `tests/test_load_test.py`, 27 pruebas, pasan en 3.12 y 3.11).

- Cada trabajador hace conversaciones completas una detrás de otra, rotando entre los seis clientes demo con los mensajes sugeridos del modo juez (`frontend/src/demo/customers.ts`, copiados al pie de la letra; una prueba lo verifica) y los botones que emite el servidor, como en `tests/test_integration_m1.py`: Lucía (texto, Es este, No lo reconozco), Sofía (dos textos, elige RAPPI*RESTAURANTE, No lo reconozco, préstamo), Andrés cliente demo (texto, Es este), João (texto, É esta), Martina (dos textos, Es este, bloquear •••• 7730) y Carlos (un texto que abre ruta C). Después de abrir el caso: `GET /cases/{id}` y, con token de analista, la cola (`GET /analyst/cases?lane=…&limit=50`) y el detalle del caso. Solo lectura: nadie decide nada.
- Cada conversación empieza con `GET /health` (piso del stack HTTP: sin sesión ni estado) y `POST /session`.
- Tipos de petición: botón (plantilla, sin modelo), texto solo reglas, texto con G1 (el turno trae el paso `g1_extract`), apertura de caso (el turno cuya respuesta trae la tarjeta por primera vez; puede ser texto o botón), tarjeta, consola cola y consola detalle.
- Latencia de cliente: `perf_counter` alrededor de la petición (conectar, enviar, esperar, leer). urllib abre una conexión por petición y el servidor local habla HTTP/1.0, así que cada petición paga su conexión TCP. Latencia de servidor: `trace_summary.latency_ms` de cada turno, en ms enteros. Se corta antes de escribir las trazas y no incluye autenticar la sesión, guardarla, validar la respuesta ni el HTTP.
- Percentiles con interpolación lineal entre rangos (como numpy por defecto). Solo cuentan las respuestas 200; las fallidas van aparte como errores.
- Sin pausa entre turnos (lazo cerrado): es el peor caso para el servidor. Una persona tarda segundos en leer y escribir.
- Niveles: 1, 5, 10 y 20 conversaciones concurrentes; 12 conversaciones por trabajador (12, 60, 120 y 240 conversaciones por nivel); 3 repeticiones por nivel. Se reporta la mediana entre repeticiones de cada estadístico (p50, p95, p99, máx) y la suma de errores.
- Servidor: `--spawn-local 8601` levanta un `local_api.py` nuevo por nivel y repetición (memoria limpia), con `--llm none` o `--llm mock`, `SESSION_RATE_PER_MINUTE=0` (si no, 20 trabajadores chocan con el límite de 60 sesiones por minuto y por IP), `TRACE_DIR` en una carpeta temporal y un token de analista aleatorio. Antes de medir corre una conversación de calentamiento que no se cuenta. Lo apaga al terminar.
- Entre pasadas esperé a que se vaciaran los sockets en TIME_WAIT: una pasada completa deja unos 9.000 a 10.000 (`netstat`), y Windows tiene 16.384 puertos efímeros (49152 en adelante).

Comando de la pasada reportada (una vez con `none` y otra con `mock`):

```bash
python scripts/load_test.py --spawn-local 8601 --llm-mode none --concurrency 1,5,10,20 --repeat 3 --per-worker 12 --md-out <archivo.md>
python scripts/load_test.py --spawn-local 8601 --llm-mode mock --concurrency 1,5,10,20 --repeat 3 --per-worker 12 --md-out <archivo.md>
```

## Máquina y código

- Intel Core i5-11320H a 3,20 GHz, 4 núcleos y 8 hilos, 15,8 GB de RAM, Windows 11 Home. Python 3.12.8 (venv del repo).
- Cliente y servidor en la misma máquina, compartiendo CPU. La máquina no estaba aislada: tenía Chrome, Spotify y otras apps abiertas y había 8 procesos de Node de otros trabajos. La carga de CPU justo antes de cada modo de la pasada reportada fue de 23 % y 24 %.
- Servidor: `ThreadingHTTPServer` de un solo proceso (un hilo por conexión, `request_queue_size` 5 por defecto), que llama al handler de la Lambda en un pool de 16 hilos (`scripts/local_api.py`, línea 244). Todo el Python del servidor comparte un GIL.
- Código medido: el árbol de trabajo a las 10:12–10:17, es decir HEAD 5867ac1 más cambios sin commitear de otros trabajos en `src/` (7 archivos, 84 líneas agregadas y 10 quitadas según `git diff --stat`: app, contract, lifecycle, templates, handler). El camino caliente (orquestador, store, g1, analyst) no cambió desde la madrugada.

## Resultados (pasada reportada, 10:12–10:17)

Resumen por nivel (ms; "chat" = todos los turnos `POST /chat`):

| Modo | Concurrencia | Conversaciones completas (3 rep.) | Peticiones por rep. | Peticiones/s | Conversaciones/s | Chat p50 | Chat p95 | Chat p95, rango entre rep. | Chat p99 | Chat máx | Errores |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| none | 1 | 36/36 | 94 | 58,0 | 7,41 | 18,2 | 46,5 | 43,7–46,9 | 52,2 | 54,0 | 0 |
| none | 5 | 180/180 | 470 | 253,7 | 32,39 | 35,2 | 58,8 | 55,9–63,2 | 70,7 | 75,7 | 0 |
| none | 10 | 360/360 | 940 | 222,5 | 28,40 | 72,9 | 142,5 | 142,2–159,2 | 170,7 | 206,7 | 0 |
| none | 20 | 720/720 | 1880 | 151,2 | 19,31 | 178,8 | 342,5 | 340,2–347,4 | 408,1 | 685,0 | 0 |
| mock | 1 | 36/36 | 94 | 51,7 | 6,60 | 22,2 | 54,2 | 49,7–54,7 | 55,9 | 56,4 | 0 |
| mock | 5 | 180/180 | 470 | 225,7 | 28,82 | 42,2 | 69,4 | 66,8–74,5 | 79,2 | 88,8 | 0 |
| mock | 10 | 360/360 | 940 | 199,0 | 25,41 | 88,8 | 173,8 | 169,3–174,2 | 211,6 | 234,2 | 0 |
| mock | 20 | 720/720 | 1880 | 134,4 | 17,15 | 205,4 | 415,9 | 395,2–427,0 | 524,2 | 626,6 | 0 |

Turnos de chat por tipo, latencia de cliente p50 / p95 (ms) y de servidor (`trace_summary`):

| Tipo de turno | Modo | c=1 p50 / p95 | c=5 p50 / p95 | c=10 p50 / p95 | c=20 p50 / p95 | Servidor p50 / p95 (c=1 → c=20) |
|---|---|---:|---:|---:|---:|---|
| Botón (plantilla) | none | 11 / 24 | 27 / 38 | 48 / 114 | 124 / 243 | 0 / 1 → 0 / 0 |
| Botón (plantilla) | mock | 15 / 30 | 25 / 54 | 59 / 109 | 126 / 289 | 0 / 1 → 0 / 0 |
| Texto, solo reglas | none | 20 / 46 | 38 / 62 | 79 / 152 | 202 / 376 | 2 / 4 → 1 / 1 |
| Texto, con G1 mock | mock | 37 / 54 | 51 / 76 | 116 / 192 | 239 / 446 | 9 / 11 → 5 / 14 |
| Apertura de caso | none | 20 / 38 | 34 / 55 | 76 / 145 | 185 / 327 | 1 / 1 → 0 / 1 |
| Apertura de caso | mock | 22 / 46 | 39 / 61 | 82 / 149 | 198 / 393 | 1 / 9 → 0 / 7 |
| Todos los turnos de chat | none | 18 / 47 | 35 / 59 | 73 / 143 | 179 / 342 | 1 / 3 → 1 / 1 |
| Todos los turnos de chat | mock | 22 / 54 | 42 / 69 | 89 / 174 | 205 / 416 | 4 / 11 → 4 / 11 |

Otras peticiones, cliente p50 / p95 (ms):

| Petición | Modo | c=1 | c=5 | c=10 | c=20 | c=20 máx |
|---|---|---:|---:|---:|---:|---:|
| GET /health | none | 12 / 29 | 3 / 14 | 4 / 24 | 12 / 108 | 1026 |
| GET /health | mock | 4 / 28 | 3 / 20 | 3 / 27 | 15 / 511 | 1054 |
| POST /session | none | 6 / 34 | 3 / 21 | 4 / 23 | 10 / 63 | 1029 |
| POST /session | mock | 16 / 28 | 3 / 20 | 4 / 21 | 14 / 55 | 535 |
| GET /cases/{id} | none | 8 / 29 | 3 / 12 | 4 / 23 | 16 / 51 | 94 |
| GET /cases/{id} | mock | 6 / 27 | 3 / 14 | 4 / 15 | 17 / 63 | 97 |
| Consola: cola | none | 6 / 32 | 9 / 26 | 17 / 69 | 92 / 881 | 1784 |
| Consola: cola | mock | 20 / 33 | 11 / 33 | 20 / 88 | 106 / 916 | 2079 |
| Consola: detalle | none | 12 / 31 | 12 / 28 | 24 / 64 | 51 / 144 | 233 |
| Consola: detalle | mock | 15 / 35 | 12 / 31 | 24 / 58 | 57 / 162 | 310 |

Tamaño de muestra por repetición (sale en la tabla completa del anexo): con c=20, 680 turnos de chat, 320 textos y 240 aperturas de caso; con c=1, 34 turnos de chat. Con c=1 el p99 y el máximo salen de pocas muestras.

### Variación entre pasadas

Hice más pasadas completas con el mismo método. La pasada 1 corrió mientras otros trabajos cambiaban `src/` (09:50–09:56) y con una versión del script que contaba los textos sin modelo como "con G1" (el total de chat no cambia). La pasada 2 todavía no guardaba el código del error de conexión. La 3 y la 4 midieron el mismo código de la app de la misma forma (entre ambas el script solo cambió cómo resume: sumas de conversaciones y nombres de errores). Chat p95 y errores con 10 y 20 conversaciones:

| Pasada | Modo | c=10 chat p95 | c=20 chat p50 | c=20 chat p95 | c=20 chat máx | Conexiones rechazadas (c=10 + c=20, 3 rep.) |
|---|---|---:|---:|---:|---:|---:|
| 1 (09:52) | none | 148,9 | 207,3 | 400,5 | 1299,6 | 0 |
| 2 (09:59) | none | 223,5 | 185,6 | 420,7 | 1214,5 | 11 (`URLError`, sin el código de error en esa versión) |
| 3 (10:03) | none | 226,7 | 179,3 | 440,1 | 869,2 | 19 (`ConnectionRefusedError` 10061) |
| 4, reportada (10:12) | none | 142,5 | 178,8 | 342,5 | 685,0 | 0 |
| 1 (09:55) | mock | 176,5 | 213,8 | 434,7 | 700,2 | 0 |
| 3 (10:07) | mock | 256,8 | 236,8 | 508,6 | 1295,9 | 11 (`ConnectionRefusedError` 10061) |
| 4, reportada (10:15) | mock | 173,8 | 205,4 | 415,9 | 626,6 | 0 |

En la pasada 3, la primera repetición de 20 conversaciones tardó 23,07 s (none) y 26,61 s (mock), contra 13,03 a 16,78 s de las otras. No busqué la causa. La mediana de 3 repeticiones la amortigua; los errores se suman igual.

## Qué significa

- En esta máquina, nuestro código no está cerca del límite de RNF-01. El peor p95 de un turno fue 508,6 ms (mock, 20 conversaciones sin pausa, pasada 3), menos de una décima parte de 6 s. El peor máximo fue 1,3 s. Los turnos de plantilla (botones) quedan debajo de 1 s en caliente, como pide RNF-01, incluso a 20 conversaciones: en la pasada reportada el peor p95 fue 288,6 ms y el máximo, 408,3 ms (tabla completa del anexo).
- G1 mock suma poco: en el servidor pasa de 1–2 ms (texto solo reglas) a 5–9 ms de mediana. En el cliente, a una conversación, el p50 de un texto pasa de 20 a 37 ms. El perfil (abajo) muestra que buena parte es regenerar el JSON Schema de `Extraction` en cada llamada, no el mock en sí.
- La latencia del cliente crece casi lineal con la concurrencia mientras el orquestador sigue en 0–15 ms de p95: es cola en un solo proceso. El throughput máximo fue de 254 peticiones/s (none) y 226 (mock) con 5 conversaciones; con más trabajadores baja (151 y 134 con 20). Más concurrencia no da más capacidad en este servidor.
- Los picos de unos 0,5 s y 1 s en `GET /health` y `POST /session` (p99 y máximos de 1026–1054 ms) y los `ConnectionRefusedError` 10061 coinciden con el backlog de 5 conexiones del `ThreadingHTTPServer` (lo verifiqué: `request_queue_size` = 5, protocolo HTTP/1.0). Encaja con que Windows reintente la conexión a los 0,5 s y 1 s y al final la rechace, pero no lo comprobé con una captura. Es un artefacto del servidor local: API Gateway y Lambda no tienen ese backlog.

## Qué NO significa

- No es la latencia en la nube. Falta todo lo que va entre el navegador y la Lambda: red, CloudFront, API Gateway, el init de Lambda (cold start), DynamoDB en lugar de memoria, trazas a la nube en lugar de un JSONL local y, sobre todo, Bedrock. Con Haiku en el chat, cada texto suma una llamada al modelo que probablemente pesa más que todo lo medido aquí; no tengo esa cifra.
- No es capacidad de la nube. El servidor local es un proceso con GIL; en AWS cada petición concurrente va a su propio contenedor de Lambda (1769 MB, `infra/stacks/api_stack.py`) y el techo lo ponen el throttling de la API (20 peticiones/s, ráfaga de 50), la concurrencia de Lambda de la cuenta y los TPM de Bedrock (RNF-04).
- No son usuarios reales. 20 trabajadores sin pausa generan 134 a 151 peticiones/s; un juez que lee y escribe genera mucho menos. RNF-04 espera menos de 10 jueces. No medí cuántas personas reales equivalen a esta carga.
- No es una máquina aislada: cliente, servidor y otras apps compartían CPU. Las cifras sirven para comparar none contra mock y niveles entre sí, no como valores absolutos.
- No mide el investigador por separado. Corrección (verificación del 29 sep, 10:40): el ciclo de vida local no tiene hilos de fondo; el stub corre de forma perezosa dentro de la primera lectura que encuentra el caso vencido (4 s después de abrirlo): `GET /cases/{id}` del mismo caso o la cola de la consola, cuyo `advance_all()` investiga los casos vencidos de todos con un presupuesto de 2 s (`src/conversation/lifecycle.py`). Con 10 y 20 conversaciones, los casos B que otros trabajadores abrieron hace más de 4 s probablemente se investigaron dentro de las peticiones "Consola: cola" medidas; no está separado cuánto de su p95 es eso (UNVERIFIED). Tampoco cubre la consola escribiendo decisiones, los interruptores del juez ni el modelo lento (`--fail model`), que por diseño tarda 8 s.

## Hallazgos del código propio (perfil)

Perfil en proceso, sin HTTP (`cProfile`, 20 conversaciones de Lucía = 40 turnos a través de `handlers.api.handler`, G1 mock, trazas a un JSONL temporal). Sin perfilador: 8,1 a 9,0 ms por texto en el handler, contra 4 ms en `trace_summary`, y 3,0 a 3,3 ms por botón, contra 0 ms. Con el perfilador, que infla todo, 0,389 s en total:

- Trazas: `JsonlTraceSink.emit`, 300 llamadas, 0,133 s acumulados. Cada evento hace `mkdir` y abre y cierra el archivo bajo un candado global (`src/conversation/store.py`), así que todos los hilos se turnan para escribir. Corrección: no es solo local. Con la propuesta de esta noche (`STORE_BACKEND=memory`, `TRACE_DIR=/tmp/traces`) la Lambda usa este mismo sink (`make_stores` en `store.py`); el sink de la nube (#7) no existe todavía. Explica parte de la distancia entre cliente y servidor y queda fuera de `trace_summary.latency_ms`.
- G1: `model_json_schema` de pydantic, 20 llamadas (una por texto), 0,093 s. El esquema se regenera en cada turno y se podría cachear. Hay que ver si el cliente de Bedrock de Andrés hace lo mismo.
- `copy.deepcopy` del store en memoria: 0,048 s.
- Cola de la consola: `AnalystService.list_cases` llama a `lifecycle.advance_all()`, recorre `stores.cases.all()` y ordena todo antes de cortar a `limit`. Medido en proceso con un solo hilo (mediana de 20 llamadas, cola B, `limit=50`): 12 casos 1,5 ms; 60 casos 10,1 ms; 120 casos 17,4 ms; 240 casos 38,0 ms; 480 casos 106,8 ms. En memoria crece con el total de casos. `CaseStore.all()` dice que en DynamoDB será un GSI por status/breach_at y no un scan: conviene confirmarlo con Andrés antes de la demo, porque con un scan la cola de la consola sería la petición más lenta del sistema.

No toqué nada de eso: los scripts de este trabajo solo miden.

## Qué medir en AWS esta noche

Cuando Andrés despliegue. Todo con clientes y textos sintéticos. El script agrega `/api` a la raíz del sitio. Solo lectura desde la consola (no se decide nada), y la consola necesita el JWT de Cognito, que sigue pendiente; sin él, `--no-console`. `--llm-mode` es lo que se espera del servidor: `mock` si se despliega con `LLM_PROVIDER=mock`, `bedrock` cuando esté el cliente de Andrés, o `any` para no chequearlo.

1. Cold start, justo después del despliegue o tras 15 minutos sin tráfico (sin calentamiento; el primer `GET /health` y el primer `POST /session` incluyen el init):

```bash
python scripts/load_test.py https://<dominio-de-cloudfront> --llm-mode any --concurrency 1 --per-worker 1 --warmup 0 --no-console --md-out <scratch>/aws_frio.md
```

2. En caliente, debajo del throttling (20 peticiones/s): 1 y 3 conversaciones a la vez, 3 repeticiones, con tope de 10 peticiones/s entre todos los trabajadores:

```bash
python scripts/load_test.py https://<dominio-de-cloudfront> --llm-mode any --concurrency 1,3 --repeat 3 --per-worker 4 --max-rps 10 --no-console --md-out <scratch>/aws_caliente.md
```

Eso crea 4 + 12 sesiones por repetición, 48 en total, debajo de 60 por minuto aunque el límite por IP también corra en la Lambda. Si aparece `429 rate_limited` o `429 Too Many Requests` en "Errores", no es latencia: hay que bajar `--max-rps` o esperar un minuto.

3. Con Bedrock (Haiku), el mismo comando con `--llm-mode bedrock`. Cuesta: cada conversación hace de 1 a 3 textos (Sofía 3, Martina 2, el resto 1). Por el reparto de clientes entre trabajadores, el paso 2 manda 72 textos (llamadas a G1) y abre 39 casos de ruta B, más 1 texto y 1 caso del calentamiento. Si el investigador ya usa un modelo en la nube, cada caso B dispara una investigación. El script avisa si ninguna traza trae `model_id`.

Ojo: esas conversaciones dejan casos sintéticos reales en DynamoDB, que aparecen en la cola de la consola. No encontré un procedimiento de reset en el RUNBOOK ni en el Makefile del equipo; hay que acordarlo con Andrés antes de medir si la consola se va a mostrar después.

4. Del lado de AWS (lo corre Andrés; yo no toco AWS), en CloudWatch Logs Insights sobre el log group de la Lambda de la API:

```
filter @type = "REPORT"
| stats count(), pct(@duration, 50), pct(@duration, 95), max(@duration), max(@initDuration) by bin(5m)
```

Con eso se separa el tiempo de la Lambda (y el init) del tiempo de red y API Gateway que ve el cliente.

Queda pendiente, porque no está medido: p50/p95 por turno con Bedrock, cold start, latencia de red desde fuera de AWS, la cola de la consola sobre DynamoDB y la latencia p95 del investigador (meta < 90 s en el plan).

## Anexo: tabla completa de la pasada reportada

Salida de `--md-out`, sin editar.

Modo none (10:12):

| Modo LLM | Conc. | Tipo | n por rep. | Cliente p50 | Cliente p95 | Cliente p99 | Cliente máx | Servidor p50 | Servidor p95 | Servidor p99 | Servidor máx |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| none | 1 | GET /health (piso HTTP) | 12 | 11.5 | 28.9 | 32.0 | 32.8 | — | — | — | — |
| none | 1 | POST /session | 12 | 5.9 | 33.8 | 43.8 | 47.1 | — | — | — | — |
| none | 1 | botón (plantilla) | 6 | 11.0 | 24.3 | 25.6 | 25.9 | 0.5 | 1.0 | 1.0 | 1.0 |
| none | 1 | texto (solo reglas) | 16 | 20.2 | 46.0 | 49.1 | 49.9 | 2.0 | 4.0 | 4.0 | 4.0 |
| none | 1 | apertura de caso | 12 | 19.5 | 38.3 | 47.5 | 47.7 | 1.0 | 1.4 | 1.9 | 2.0 |
| none | 1 | turno de chat (todos) | 34 | 18.2 | 46.5 | 52.2 | 54.0 | 1.0 | 3.4 | 4.0 | 4.0 |
| none | 1 | tarjeta GET /cases/{id} | 12 | 7.7 | 29.0 | 30.0 | 30.2 | — | — | — | — |
| none | 1 | consola: cola | 12 | 6.5 | 32.4 | 33.1 | 33.3 | — | — | — | — |
| none | 1 | consola: detalle | 12 | 12.2 | 31.3 | 32.7 | 33.1 | — | — | — | — |
| none | 5 | GET /health (piso HTTP) | 60 | 3.3 | 13.6 | 25.8 | 28.6 | — | — | — | — |
| none | 5 | POST /session | 60 | 3.4 | 21.3 | 25.8 | 27.3 | — | — | — | — |
| none | 5 | botón (plantilla) | 30 | 27.5 | 37.6 | 51.2 | 51.8 | 0.0 | 0.0 | 0.7 | 1.0 |
| none | 5 | texto (solo reglas) | 80 | 38.3 | 62.2 | 72.3 | 74.0 | 1.0 | 1.0 | 2.0 | 2.0 |
| none | 5 | apertura de caso | 60 | 34.3 | 54.8 | 62.5 | 65.6 | 0.0 | 1.0 | 1.4 | 2.0 |
| none | 5 | turno de chat (todos) | 170 | 35.2 | 58.8 | 70.7 | 75.7 | 1.0 | 1.0 | 2.0 | 2.0 |
| none | 5 | tarjeta GET /cases/{id} | 60 | 3.3 | 11.9 | 27.7 | 27.9 | — | — | — | — |
| none | 5 | consola: cola | 60 | 9.4 | 25.6 | 38.1 | 42.3 | — | — | — | — |
| none | 5 | consola: detalle | 60 | 12.1 | 27.8 | 30.2 | 31.7 | — | — | — | — |
| none | 10 | GET /health (piso HTTP) | 120 | 3.7 | 23.8 | 521.4 | 536.1 | — | — | — | — |
| none | 10 | POST /session | 120 | 4.1 | 23.2 | 32.3 | 96.8 | — | — | — | — |
| none | 10 | botón (plantilla) | 60 | 47.7 | 114.0 | 134.2 | 143.9 | 0.0 | 0.0 | 0.4 | 1.0 |
| none | 10 | texto (solo reglas) | 160 | 79.1 | 152.3 | 175.8 | 206.7 | 1.0 | 1.0 | 2.0 | 2.0 |
| none | 10 | apertura de caso | 120 | 75.8 | 145.4 | 164.4 | 172.4 | 0.0 | 1.0 | 1.8 | 2.0 |
| none | 10 | turno de chat (todos) | 340 | 72.9 | 142.5 | 170.7 | 206.7 | 1.0 | 1.0 | 2.0 | 3.0 |
| none | 10 | tarjeta GET /cases/{id} | 120 | 4.0 | 23.0 | 29.3 | 32.7 | — | — | — | — |
| none | 10 | consola: cola | 120 | 17.0 | 68.6 | 88.7 | 115.7 | — | — | — | — |
| none | 10 | consola: detalle | 120 | 24.2 | 64.0 | 82.1 | 91.4 | — | — | — | — |
| none | 20 | GET /health (piso HTTP) | 240 | 12.3 | 107.8 | 827.4 | 1026.4 | — | — | — | — |
| none | 20 | POST /session | 240 | 10.0 | 62.9 | 518.4 | 1028.7 | — | — | — | — |
| none | 20 | botón (plantilla) | 120 | 123.8 | 242.8 | 300.8 | 307.9 | 0.0 | 0.0 | 0.0 | 1.0 |
| none | 20 | texto (solo reglas) | 320 | 201.5 | 376.1 | 430.4 | 685.0 | 1.0 | 1.0 | 2.0 | 3.0 |
| none | 20 | apertura de caso | 240 | 185.0 | 326.5 | 362.9 | 405.6 | 0.0 | 1.0 | 1.0 | 2.0 |
| none | 20 | turno de chat (todos) | 680 | 178.8 | 342.5 | 408.1 | 685.0 | 1.0 | 1.0 | 2.0 | 3.0 |
| none | 20 | tarjeta GET /cases/{id} | 240 | 16.0 | 50.9 | 78.6 | 93.5 | — | — | — | — |
| none | 20 | consola: cola | 240 | 91.6 | 880.6 | 1184.3 | 1784.4 | — | — | — | — |
| none | 20 | consola: detalle | 240 | 51.4 | 144.4 | 189.8 | 233.3 | — | — | — | — |

Modo mock (10:15; modelo visto en las trazas: `mock-g1`):

| Modo LLM | Conc. | Tipo | n por rep. | Cliente p50 | Cliente p95 | Cliente p99 | Cliente máx | Servidor p50 | Servidor p95 | Servidor p99 | Servidor máx |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mock | 1 | GET /health (piso HTTP) | 12 | 4.3 | 28.3 | 29.4 | 29.4 | — | — | — | — |
| mock | 1 | POST /session | 12 | 16.3 | 28.4 | 28.5 | 28.6 | — | — | — | — |
| mock | 1 | botón (plantilla) | 6 | 15.0 | 30.0 | 32.1 | 32.7 | 0.5 | 1.0 | 1.0 | 1.0 |
| mock | 1 | texto (con G1) | 16 | 36.7 | 54.4 | 56.0 | 56.4 | 9.0 | 11.2 | 11.8 | 12.0 |
| mock | 1 | apertura de caso | 12 | 22.4 | 46.1 | 53.3 | 54.9 | 1.0 | 8.8 | 9.9 | 10.0 |
| mock | 1 | turno de chat (todos) | 34 | 22.2 | 54.2 | 55.9 | 56.4 | 3.5 | 11.0 | 11.7 | 12.0 |
| mock | 1 | tarjeta GET /cases/{id} | 12 | 6.1 | 26.6 | 28.8 | 29.4 | — | — | — | — |
| mock | 1 | consola: cola | 12 | 20.4 | 33.2 | 34.2 | 34.5 | — | — | — | — |
| mock | 1 | consola: detalle | 12 | 14.6 | 34.8 | 35.1 | 35.2 | — | — | — | — |
| mock | 5 | GET /health (piso HTTP) | 60 | 2.6 | 20.4 | 30.4 | 30.8 | — | — | — | — |
| mock | 5 | POST /session | 60 | 3.3 | 20.5 | 28.5 | 29.8 | — | — | — | — |
| mock | 5 | botón (plantilla) | 30 | 24.9 | 54.5 | 65.1 | 66.4 | 0.0 | 0.0 | 0.0 | 0.0 |
| mock | 5 | texto (con G1) | 80 | 51.3 | 75.6 | 82.4 | 88.8 | 5.0 | 13.0 | 18.2 | 19.0 |
| mock | 5 | apertura de caso | 60 | 39.0 | 61.4 | 67.7 | 72.3 | 0.5 | 5.1 | 8.8 | 11.0 |
| mock | 5 | turno de chat (todos) | 170 | 42.2 | 69.4 | 79.2 | 88.8 | 3.0 | 10.0 | 15.2 | 19.0 |
| mock | 5 | tarjeta GET /cases/{id} | 60 | 3.4 | 13.8 | 25.4 | 28.9 | — | — | — | — |
| mock | 5 | consola: cola | 60 | 10.7 | 33.3 | 37.1 | 39.7 | — | — | — | — |
| mock | 5 | consola: detalle | 60 | 12.1 | 30.6 | 32.9 | 35.4 | — | — | — | — |
| mock | 10 | GET /health (piso HTTP) | 120 | 3.2 | 27.3 | 520.8 | 524.2 | — | — | — | — |
| mock | 10 | POST /session | 120 | 3.9 | 20.8 | 29.1 | 34.5 | — | — | — | — |
| mock | 10 | botón (plantilla) | 60 | 59.4 | 108.8 | 136.0 | 155.5 | 0.0 | 0.0 | 0.4 | 1.0 |
| mock | 10 | texto (con G1) | 160 | 116.4 | 191.6 | 223.5 | 234.2 | 5.0 | 15.0 | 20.2 | 36.0 |
| mock | 10 | apertura de caso | 120 | 82.1 | 149.3 | 182.0 | 192.9 | 0.0 | 7.0 | 9.8 | 20.0 |
| mock | 10 | turno de chat (todos) | 340 | 88.8 | 173.8 | 211.6 | 234.2 | 3.0 | 11.0 | 19.0 | 36.0 |
| mock | 10 | tarjeta GET /cases/{id} | 120 | 3.7 | 15.2 | 29.5 | 35.1 | — | — | — | — |
| mock | 10 | consola: cola | 120 | 19.9 | 88.0 | 174.4 | 228.7 | — | — | — | — |
| mock | 10 | consola: detalle | 120 | 24.1 | 58.1 | 75.5 | 98.2 | — | — | — | — |
| mock | 20 | GET /health (piso HTTP) | 240 | 14.8 | 510.7 | 1033.5 | 1053.6 | — | — | — | — |
| mock | 20 | POST /session | 240 | 14.1 | 55.2 | 96.4 | 535.0 | — | — | — | — |
| mock | 20 | botón (plantilla) | 120 | 126.5 | 288.6 | 369.9 | 408.3 | 0.0 | 0.0 | 0.8 | 1.0 |
| mock | 20 | texto (con G1) | 320 | 238.7 | 445.8 | 531.9 | 626.6 | 5.0 | 14.0 | 29.7 | 77.0 |
| mock | 20 | apertura de caso | 240 | 197.7 | 393.0 | 448.6 | 540.5 | 0.0 | 7.1 | 19.3 | 39.0 |
| mock | 20 | turno de chat (todos) | 680 | 205.4 | 415.9 | 524.2 | 626.6 | 4.0 | 11.0 | 22.4 | 77.0 |
| mock | 20 | tarjeta GET /cases/{id} | 240 | 17.3 | 62.9 | 89.0 | 97.2 | — | — | — | — |
| mock | 20 | consola: cola | 240 | 106.0 | 915.8 | 1457.4 | 2079.5 | — | — | — | — |
| mock | 20 | consola: detalle | 240 | 57.4 | 162.4 | 221.9 | 310.2 | — | — | — | — |
