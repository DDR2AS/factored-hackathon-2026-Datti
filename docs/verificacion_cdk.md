# Verificación del CDK de Andrés con nuestro código (sin credenciales de AWS)

29 sep 2026, mañana. Para Arturo y Andrés, antes del despliegue de esta noche.

Todo se hizo en una copia en el scratchpad de la sesión (`cdk_check/`). No se tocó el repo del equipo ni este repo (salvo este documento y `docs/propuesta_infra.patch`), no hubo git commit/push, no se llamó a ningún servicio de AWS con credenciales y no se llamó a ningún modelo. Las credenciales quedaron aisladas en cada `cdk synth` (`AWS_SHARED_CREDENTIALS_FILE` y `AWS_CONFIG_FILE` apuntando a archivos inexistentes, `AWS_EC2_METADATA_DISABLED=true`).

Base: repo del equipo en `f753eb8` (el clon local, sin `fetch`; si main avanzó, repetir) más nuestro `5867ac1` superpuesto según `docs/pasar_al_repo_del_equipo.md`.

Mientras corría esto, el árbol de trabajo de este repo cambió por otro trabajo en paralelo: 28 archivos modificados y archivos nuevos como `scripts/load_test.py` y `frontend/src/chat/demoClock.ts`. Comprobé que la copia es `5867ac1`: los 28 archivos son iguales a HEAD y los nuevos no están. Esos cambios no están verificados aquí. Un vistazo al diff: no agregan rutas ni variables de entorno nuevas (solo lecturas de `LIFECYCLE_BACKEND`, que ya está en la propuesta), y suman `axe-core` y `vitest-axe` como dependencias de desarrollo del front. Antes de desplegar, repetir el synth y el smoke con el sha final.

## Resumen

Con los cambios de PR 1 y PR 2 el CDK de Andrés sintetiza sin tocar nada, sin cuenta y sin lookups contra AWS, y empaqueta bien nuestro código y el front. Pero desplegado tal cual, la API no funciona: sin `STORE_BACKEND` todas las rutas salvo `/health` responden 500, y sin `-c bundle=true` faltan pydantic y PyYAML. Con las variables y el bundle de la propuesta, el mismo asset que produce `cdk synth` pasa el smoke completo en una simulación local (29 escenarios, 0 fallas). La propuesta mínima está en `docs/propuesta_infra.patch` (no aplicada).

## 1. Copia de trabajo

- `git archive HEAD` del repo del equipo en `cdk_check/team` y encima, con el mismo `tar` de la guía: `src/conversation`, `src/handlers/api.py`, los 3 scripts, `tests/`, `frontend/`.
- Simulé el PR 1 en la copia: `src/requirements-lambda.txt` con `pydantic==2.13.5` y `PyYAML==6.0.3`, y `PyYAML` y `pytest` en `requirements.txt`.
- Front: Node v24.19.0, npm 11.17.0. `npm ci` (0 vulnerabilidades) y `npm run build` OK en 458 ms, bundle principal `index-*.js` 247.98 kB (gzip 78.26 kB). Después, `dist/build.txt` = `5867ac1 2026-09-29T14:43Z` (paso 2 del checklist de la guía).

## 2. Pruebas del CDK y synth (código de Andrés sin cambios)

Venv con `infra/requirements-dev.txt` en Python 3.12.8: aws-cdk-lib 2.271.0, constructs 10.8.1, jsii 1.140.0, pytest 9.1.1. CLI de CDK local (`npm install aws-cdk`, sin `-g`): 2.1143.0.

- `python -m pytest -q tests` en `infra/`: 10 passed en 45.09 s. El primer intento falló con `FileNotFoundError: [WinError 2]` porque jsii necesita `node` en el PATH (en Git Bash hay que agregar `/c/Program Files/nodejs`).
- `npx cdk synth -c stage=dev-arturo --quiet`: OK, 6 stacks (Data, Pipeline, Workflow, Api, Web, Monitoring). No pidió cuenta ni región: `account` queda sin definir (stacks sin cuenta fija) y la región sale de `cdk.json` (us-east-2). No hay lookups contra AWS, así que no hubo que detenerse.
- Aviso: el CLI de CDK 2.1143 manda telemetría anónima a `cdk-cli-telemetry.us-east-1.api.aws` por defecto. Los dos primeros `cdk synth` corrieron con la telemetría activa (sin credenciales ni cuenta de por medio); desde ahí usé `CDK_DISABLE_CLI_TELEMETRY=true`. Si Andrés no la quiere, que use la misma variable.

## 3. Qué hay en cdk.out

Asset de la Lambda (`asset.6d81e02d…`, compartido por Api, Pipeline y Workflow): 36 archivos, 454 079 bytes. Tiene todo `conversation/`, incluidos `rules/lane_rules.yaml`, `rules/promise_times.yaml`, `templates/es.yaml`, `templates/pt.yaml`, `demo/customers.yaml`, `prompts/g1_extract/v1.md` y `llm_prices.yaml`; `handlers/api.py` y los stubs del equipo; sin `etl/` ni `__pycache__`. Sin bundle no trae pydantic ni PyYAML.

Handler del ApiFn: `handlers.api.handler`, python3.12, x86_64, 1769 MB, timeout 29 s, sin concurrencia reservada. Las rutas del template (7, las 3 de `/analyst/*` con JWT) coinciden con las que enruta `api.py`.

Asset web (`asset.d53f9f9c…`): es `frontend/dist` (15 archivos en `assets/`, `index.html`, `favicon.svg`, `build.txt` con `5867ac1 2026-09-29T14:43Z`). Si `dist` no existe, se sube el placeholder sin avisar.

Variables que pone el CDK en el ApiFn: `STAGE`, `POWERTOOLS_SERVICE_NAME`, `TABLE_SESSIONS/CASES/SERVING/DEMO`, `BUCKET_LAKE/ARTIFACTS/TRACES`, `GLUE_DATABASE`, `ATHENA_WORKGROUP`, `CASE_STATE_MACHINE_ARN`, `SLA_SCHEDULE_GROUP`, `SLA_SCHEDULER_ROLE_ARN`, `SLA_TARGET_FUNCTION_ARN`, `LLM_PROVIDER=bedrock`, `MODEL_CHAT/INVESTIGATOR/JUDGE`, `SESSION_TTL_MINUTES=15`, `LLM_TIMEOUT_SECONDS=8`.

Variables que lee nuestro código (`grep` en `src/`): `STAGE`, `STORE_BACKEND`, `LLM_PROVIDER`, `MODEL_CHAT` (solo se muestra), `SESSION_TTL_MINUTES`, `SESSION_RATE_PER_MINUTE`, `LLM_TIMEOUT_SECONDS`, `LIFECYCLE_BACKEND`, `LOCAL_INVESTIGATION_DELAY_SECONDS`, `LOCAL_SLA_SCALE`, `TRACE_DIR`.

Faltan en el CDK: `STORE_BACKEND` (bloqueante), `TRACE_DIR` (sin ella las trazas van a `/var/data/traces`, que no se puede escribir, y se pierden en silencio), `LIFECYCLE_BACKEND`, `LOCAL_INVESTIGATION_DELAY_SECONDS` y `SESSION_RATE_PER_MINUTE` (tienen valor por defecto correcto; mejor explícitas). `LLM_PROVIDER=bedrock` sobra mientras no exista `src/llm`. `LOCAL_SLA_SCALE` puede quedarse sin definir (1/1440 por defecto). Nuestro código no usa las tablas, buckets ni ARNs todavía.

Tiempos frente al timeout: el turno más lento medido es el interruptor `model_slow`, 8028 ms (espera el `LLM_TIMEOUT_SECONDS` de 8), bajo los 29 s de la Lambda y los 30 s de API Gateway. El ciclo de vida local es perezoso (avanza al leer el caso, con presupuesto de 2 s por lectura) y no usa hilos de fondo, así que funciona con el congelamiento de Lambda. La Lambda del investigador del equipo (1024 MB, 120 s) no la usamos: el informe stub corre dentro del ApiFn. Memoria usada y arranque en frío en Lambda: pendientes (ver `REPORT` en CloudWatch después del despliegue). En local, el primer `POST /session` (importar pydantic y armar la app) tardó 617 a 807 ms en Windows; no es una medida de Lambda.

## 4. Simulación del ApiFn con el asset del synth

Corrí `handlers.api.handler` desde el asset de `cdk.out` con las variables leídas del template sintetizado (los `Fn::ImportValue` como texto de relleno), en Python 3.12.8. Para simular el bundle usé las mismas versiones de pydantic y PyYAML en wheels de Windows (las de Linux no cargan en Windows).

| Escenario | Resultado |
|---|---|
| A. Sin bundle, variables del CDK | `GET /health` 200 `deps: missing`; `POST /session` 500 "server dependencies missing (pydantic, PyYAML)" |
| B. Con dependencias, variables del CDK | `/health` `deps: ok`; `POST /session` 500 "server store is not configured" |
| C. B + `STORE_BACKEND=memory` (LLM sigue en bedrock) | sesión 200, chat 200 pero `degraded: ["model_timeout"]` en cada turno |
| D. Variables de la propuesta (`memory`, `none`, `TRACE_DIR`) | sesión 200, chat 200, `degraded: []`, trazas escritas en `TRACE_DIR` |

Con D levanté `scripts/local_api.py` sobre ese mismo asset y ese `frontend/dist`, y corrí `scripts/smoke_api.py` con token de analista: 27 PASS, 2 SKIP, 0 FAIL. Los 2 SKIP necesitan un servidor aparte y los corrí así: `--only sla --sla-wait 30` con `--sla-scale 0.00001` dio 2/2 PASS (alertas de SLA en 12.3 s), y `--only expiry --expiry-wait 5` con `--ttl-minutes 0.05` dio 1/1 PASS. Servidores en los puertos 8765 a 8767, todos apagados.

Esto prueba que el código y los datos del asset funcionan con esas variables. No prueba CloudFront, API Gateway, Cognito ni el runtime Linux.

## 5. Paquete de la Lambda con dependencias

`pip install --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --target lambda_pkg -r src/requirements-lambda.txt`: pydantic 2.13.5, pydantic_core 2.46.5 (`cpython-312-x86_64-linux-gnu.so`), PyYAML 6.0.3, annotated_types 0.8.0, typing_extensions 4.16.0, typing_inspection 0.4.4. Solo dependencias: 12 641 849 bytes.

Con nuestro `src/` encima: 340 archivos, 12 929 389 bytes descomprimido (12.33 MiB), zip de 4 488 929 bytes (4.28 MiB). Con el bundling local de la propuesta, `cdk synth -c bundle=true` produjo un asset de 341 archivos y 13 095 928 bytes (2 529 505 son `.pyc` que compila pip), zip de 4 557 047 bytes. El límite de Lambda es 250 MB descomprimido: estamos en torno al 5 %. El asset se sube por S3, así que el límite de 50 MB de subida directa no aplica.

Docker: `docker --version` da 25.0.3 (build 4debf41), pero el daemon no corre en esta máquina (`docker info`: "error during connect … docker_engine"). WSL solo tiene la distribución `docker-desktop`. No intenté bajar la imagen `public.ecr.aws/sam/build-python3.12`.

Alternativa sin Docker (en la propuesta, `stacks/common.py`): un `ILocalBundling` que corre ese mismo `pip --platform` y copia `src/` sin `etl/` ni `__pycache__`; si pip falla devuelve False y CDK usa Docker como hoy. Probado: `cdk synth -c stage=dev-arturo -c bundle=true` OK sin Docker; el mismo asset lo usan Api, Pipeline y Workflow (se empaqueta una sola vez). Dos notas del intento:

- En Windows, con la ruta larga del scratchpad, pip falló por el límite de 260 caracteres (`LongPathsEnabled=0` en esta máquina); CDK cayó a Docker y dio `docker exited with status 127`. Con `-o` a una ruta corta funcionó. Si Andrés despliega desde Windows con una ruta larga, puede pasarle lo mismo.
- Ese comando Docker que imprimió CDK monta `src/` entero como `/asset-input` y hace `cp -au .`, así que con Docker el paquete llevaría `src/etl/` aunque `LAMBDA_EXCLUDES` lo excluya. No lo verifiqué ejecutando Docker.

## 6. Propuesta aplicada en la copia

La copia con `docs/propuesta_infra.patch` aplicado:

- `python -m pytest -q tests` en `infra/`: 12 passed (10 de Andrés más 2 guardas nuevas).
- `cdk synth -c stage=dev-arturo`: OK. ApiFn con `STORE_BACKEND=memory`, `LLM_PROVIDER=none`, `LIFECYCLE_BACKEND=local`, `LOCAL_INVESTIGATION_DELAY_SECONDS=4`, `SESSION_RATE_PER_MINUTE=60`, `TRACE_DIR=/tmp/traces`, `ReservedConcurrentExecutions: 1`. `/api/*` usa la cache policy `expvivo-dev-arturo-api-forward-auth` (Authorization en la clave, todas las query strings, TTL 0/0/1 s) con el mismo `AllViewerExceptHostHeader`.
- `cdk synth -c stage=ci --quiet` (lo que corre el CI): OK.
- `-c apiReservedConcurrency=0 -c apiLlmProvider=bedrock`: sin reserva y `LLM_PROVIDER=bedrock`. `-c apiStore=postgres`: `ValueError` claro.
- `git apply --check` OK sobre `f753eb8` exportado con finales LF y con CRLF, y el resultado es igual a la copia probada (salvo fin de línea).
- Las suites de la copia siguen verdes: 1049 pytest en 3.11.9 y en 3.12.8 (`test_local_api.py` lee el `api_stack.py` modificado), `compileall` en 3.11 sin errores, vitest 16 archivos y 211 pruebas, oxlint con código 0, `scripts/licences.py` sin errores (3 manifiestos).

## 7. CI (`.github/workflows/ci.yml`)

- Job `checks`: Python 3.11, sin `setup-node`. Hoy `make test` y `make build` solo imprimen texto. Con el PR 2, `make test` corre `npm ci && npm test`: vitest 5.0.2 pide Node `^22.12 || ^24 || >=26` y jsdom 30.1.1 `^22.22.2 || ^24.15 || >=26`. No sé qué Node trae hoy el runner `ubuntu-latest` (no medido), así que hay que fijarlo: agregar `actions/setup-node@v4` con `node-version: "24"` antes de "Install". Sin `pytest` en `requirements.txt`, `make test` falla con "No module named pytest" (entra en el PR 1). En 3.11 nuestra suite pasa (1049).
- `make licences` no tiene modo de comprobación: regenera `LICENCES.md` y no falla. En el CI, sin `node_modules`, las licencias del front salen "unknown".
- Job `infra`: Python 3.12 y Node 20. El CLI de CDK 2.1143.0 pide Node `>= 18`, así que Node 20 sirve. Ese job no compila el front: sintetiza con el placeholder, lo que está bien para el CI.

## 8. Qué tiene que hacer Andrés esta noche (en orden)

1. Variables del ApiFn (bloqueante). Sin `STORE_BACKEND` la API no sirve (escenario B). Con `LLM_PROVIDER=bedrock` y sin `src/llm`, cada turno sale como "Respuesta de respaldo" (escenario C). Está en la propuesta.
2. Dependencias (bloqueante). El PR 1 trae `src/requirements-lambda.txt` con las versiones fijas y hay que desplegar con `-c bundle=true`. Si tiene Docker, el daemon tiene que estar corriendo. Si no, el bundling local de la propuesta (probado aquí sin Docker). Después del deploy, `GET /api/health` tiene que decir `deps: ok`.
3. Concurrencia reservada 1 con el store en memoria. La decisión es de Arturo y Andrés:
   - Sin reserva, dos contenedores no comparten sesiones y el cliente ve 401 al azar.
   - Con reserva, las peticiones que se solapen (el poll mientras corre un chat, o el `model_slow` de 8 s) se rechazan por throttling; qué código ve el navegador está sin medir.
   - La cuenta tiene que dejar al menos 10 de concurrencia sin reservar. Si el límite de la cuenta es 10, el deploy falla: Andrés lo ve con `aws lambda get-account-settings`, y si pasa, `-c apiReservedConcurrency=0`.
   - En los dos casos, las sesiones se pierden en cada arranque en frío y en cada redeploy. Conviene `-c warmup=true` en la ventana de jueces. El arreglo de verdad es `STORE_BACKEND=dynamodb`, que no existe todavía.
4. `Authorization` por CloudFront. `CACHING_DISABLED` no permite headers en la clave, y según la documentación de CloudFront el header `Authorization` solo se reenvía si la cache policy lo incluye. La propuesta agrega esa policy; solo se valida en AWS. Se comprueba con el escenario "Lucía: B" del smoke contra la URL de CloudFront. Plan B para el cliente: `sessionHeader: 'x-ev-session'` (la guía, paso 6). Para `/api/analyst/*` no hay plan B.
5. Node del CI: `setup-node` 24 en el job `checks` cuando entre el PR 2 (el cambio va en `ci.yml`, no en `infra/`).
6. Cognito: no es para esta noche. El cliente del pool solo tiene `ALLOW_USER_SRP_AUTH` y `ALLOW_REFRESH_TOKEN_AUTH`, y `CognitoPasswordProvider` del front lanza "not enabled". En la nube, `/api/analyst/*` va a responder 401 del autorizador, así que la consola solo funciona en local. Lo que falta: `USER_PASSWORD_AUTH`, un usuario analista demo, `cognito-idp.us-east-2.amazonaws.com` en la CSP y el proveedor del front.
7. Después del deploy:
   - `curl <SiteUrl>/build.txt` y `curl <SiteUrl>/api/health`.
   - `python scripts/smoke_api.py <SiteUrl>` (sin token de analista, la consola sale SKIP).
   - En CloudWatch, `Max Memory Used` e `Init Duration` del ApiFn (pendientes).
   - Anotar todo en STATUS.md.

## 9. Observaciones menores (no bloquean)

- El límite de sesiones por minuto usa `requestContext.http.sourceIp`. Detrás de CloudFront, esa IP probablemente sea la del edge y no la del cliente, así que el conteo sería por edge. A confirmar después del despliegue; con 60 por minuto no molesta para la demo.
- `x-ev-session` no está en `allow_headers` del CORS del HTTP API. No importa mientras el front llame por el mismo dominio de CloudFront.
- El asset de `src/` también va en las Lambdas de Pipeline y Workflow. Son 454 KB sin bundle y unos 13 MB con él; no les afecta.

## 10. Re-verificación con el árbol final (29 sep, 10:30–10:40)

Hecha después, sobre `5867ac1` MÁS los cambios sin commit (pulido, `demo_clock_scale` en `/health`, latencia). Copia nueva en `scratchpad/final29/patchcheck/`: `infra/` del clon del equipo (copiado con `cp`, sin git en ese repo), `src/` del equipo con nuestro `src/conversation` y `src/handlers/api.py` encima, `src/requirements-lambda.txt` simulado y el `frontend/dist` recién construido. Sin credenciales (`AWS_SHARED_CREDENTIALS_FILE`/`AWS_CONFIG_FILE` a archivos inexistentes, `AWS_EC2_METADATA_DISABLED=true`) y con `CDK_DISABLE_CLI_TELEMETRY=true`.

- `git apply --check` de `docs/propuesta_infra.patch` sobre esa copia: OK; aplicado: 6 archivos, 103 inserciones y 2 borrados.
- `python -m pytest -q tests` en `infra/`: 12 passed.
- `cdk synth -c stage=dev-arturo --quiet` (CLI 2.1143.0): OK, 6 stacks. ApiFn: `handlers.api.handler`, python3.12, 1769 MB, 29 s, `ReservedConcurrentExecutions` 1, variables de la propuesta (`STORE_BACKEND=memory`, `LLM_PROVIDER=none`, `LIFECYCLE_BACKEND=local`, `LOCAL_INVESTIGATION_DELAY_SECONDS=4`, `SESSION_RATE_PER_MINUTE=60`, `TRACE_DIR=/tmp/traces`). 7 rutas, las 3 de `/analyst/*` con JWT. Cache policy `expvivo-dev-arturo-api-forward-auth` con `Authorization`.
- Asset de la Lambda: 36 archivos, 459 375 bytes; `conversation/` y `handlers/api.py` idénticos al árbol de trabajo (`diff -rq`).
- Simulación del handler desde ese asset con las variables del template: sin dependencias, `/health` `deps: missing` y `POST /session` 500; con pydantic y PyYAML (wheels de Windows), `/health` `deps: ok` y `demo_clock_scale` 0.000694…, sesión 200 y chat 200 con `degraded: []`. Primer intento: 500 "dependencies missing" aunque `/health` decía `deps: ok`, porque la ruta del asset en el scratchpad pasaba de 260 caracteres (`LongPathsEnabled` = 0) y Python no encontraba `conversation.contract`; copiado a una ruta corta, funcionó. Es un artefacto de Windows, no de Lambda, pero confirma el aviso de rutas largas del §5.
- No repetí el smoke completo sobre el asset: el smoke de este árbol pasó contra `local_api.py` (27 PASS, 2 SKIP, 0 FAIL; SLA 2/2; vencimiento 1/1), y el asset es el mismo código.

Nuevos avisos:
- `-c apiStore=dynamodb` pasa la validación de `config.py` y sintetiza, pero `make_stores` lanza `StoreConfigError` ("not implemented in this repo yet"), así que todas las rutas salvo `/health` responderían 500 hasta que exista el store de Andrés. No usarlo esta noche.
- En la Lambda, con `LIFECYCLE_BACKEND=local`, el informe stub corre dentro de la petición que lee el caso vencido (`GET /cases/{id}` o la cola de la consola, presupuesto de 2 s en la cola); con concurrencia reservada 1, esa petición ocupa el único contenedor mientras tanto. Tiempo en Lambda: sin medir.
- El front suma `axe-core` 4.13.0, licencia MPL-2.0 (solo desarrollo, no va en el bundle); `scripts/licences.py` la va a listar. Decisión pendiente de Arturo (README §12).

## Rutas

- Copia de trabajo: una carpeta temporal fuera del repo, `cdk_check/` (`team/` con la propuesta aplicada, `team_orig/infra` sin cambios, `lambda_pkg/`, `sim/` con `invoke.py`, `run_local.py`, `smoke.log`, `o/` con el synth con bundle).
- Propuesta: `docs/propuesta_infra.patch`.
