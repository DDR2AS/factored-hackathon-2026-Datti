# Despliegue en AWS

30 oct 2026. El lado nube de Expediente Vivo v2 está escrito y probado **sin AWS**: moto para DynamoDB, Step Functions, EventBridge Scheduler y S3, y `cdk synth` para la infraestructura. Nada se desplegó: nadie del equipo tiene credenciales todavía. Regla D10/D12: el despliegue se hace solo con el CDK de `infra/` y un usuario IAM que da Andrés, nunca a mano en la consola.

## 1. Qué está listo

| Pieza | Código | Variable (#9) | Probado con |
|---|---|---|---|
| Stores (sesiones, casos, revisiones) | `src/cloud/dynamodb_store.py` | `STORE_BACKEND=dynamodb` | `tests/test_cloud_store.py`, incluida la app completa sobre DynamoDB (sesión, caso B, decisión del analista) |
| Gateway sobre la tabla serving | `src/cloud/dynamodb_gateway.py` | `GATEWAY_BACKEND=composite` + `GATEWAY_DATA_BACKEND=dynamodb` | `tests/test_cloud_gateway.py`, con ítems armados por `pipeline/serving.py` |
| Ciclo de vida del caso | `src/cloud/stepfunctions_lifecycle.py`, `src/handlers/case_steps.py`, `src/handlers/investigator.py` | `LIFECYCLE_BACKEND=stepfunctions` | `tests/test_cloud_lifecycle.py`, `tests/test_cloud_handlers.py` (incluye un caso reabierto) |
| Pipeline en Lambda | `src/handlers/pipeline.py`, `src/cloud/lake_sync.py` | `DATA_DIR`, `RAW_PREFIX` | `tests/test_cloud_handlers.py`: réplica sintética de 20 clientes, dimensiones y una partición real entre dos buckets |
| Trazas #7 | `src/cloud/traces.py` | `TRACE_SINK=stdout` (CloudWatch) o `s3` | tests de stores y ciclo de vida |
| Infraestructura | `infra/stacks/*.py` | variables en `common.app_environment` | 17 pruebas de `infra/tests` (7 nuevas) y `cdk synth -c stage=ci` |

Las variables las pone el CDK en cada Lambda; localmente nada cambia (`STORE_BACKEND=memory`, `LIFECYCLE_BACKEND=local`, `GATEWAY_BACKEND=composite` sobre DuckDB).

## 2. Decisiones de diseño

- **Sesiones.** El TTL de DynamoDB solo lee números: el store escribe `expires_at_epoch` (segundos) junto al `expires_at` ISO de la app, y la tabla usa ese atributo. La búsqueda por token es un ítem puntero `TOKEN#<sha256>` (dos lecturas consistentes), no un GSI: un GSI es eventual y podía dar 401 al primer mensaje de una sesión recién creada.
- **Casos.** `put` es una escritura condicional sobre `version` (`CaseVersionConflict` → 409). El estado interno de la nube (task token, ejecución, ronda) vive en el ítem, fuera del `CaseRecord`, y nunca llega a una vista. La cola del analista usa el GSI disperso `by_queue` (solo carriles B y C, KEYS_ONLY) y lee los casos con lecturas consistentes.
- **ReviewStore.** Ítems extra en la tabla Cases: `REPORT#<caso>`, `CONV#<caso>` (los últimos 200 turnos, comprimidos) y `DECISION#<clave>` (la primera respuesta gana). Sin tabla, variable ni permiso nuevos.
- **Gateway.** Toda lectura de datos de un cliente es un Query sobre `pk = CUST#<id>` (nunca un Scan). `NotOwned` sale del GSI `by_txn` (KEYS_ONLY): dice de quién es un `txn_id` sin leer el registro ajeno; sin ese índice la respuesta es `NotFound`, que la API contesta igual (403). Cada partición de cliente se lee una vez y queda 5 minutos en memoria (la tabla es una foto de solo lectura). La exportación serving no trae sesiones digitales: en la nube M3 no ve señales de login ni de dispositivo nuevo.
- **Máquina de estados** (`WorkflowStack`). La API inicia `<caso>-r<ronda>` con `{case_id, money_dispute, round}`. Caso de dinero → `Investigate` (si falla → `MarkInvestigationIncomplete`) → `AwaitAnalyst` (`register_task_token`: guarda el token y recién entonces el caso queda `awaiting_analyst`) → la decisión del analista hace `SendTaskSuccess` → `NotifyCustomer` → espera `CASE_CLOSE_AFTER_SECONDS` (30) → `CloseCase`, salvo que se haya pedido información al cliente o se haya escalado. Un caso reabierto inicia la ronda siguiente directo al analista. Los temporizadores SLA son schedules de EventBridge `<caso>-<tipo>` que llaman a `case_steps` con `{action: sla_timer, kind}`; el reloj de demo (`fast_clock`) también funciona en la nube, con al menos 60 s de anticipación.
- **Pipeline** (`PipelineStack`). `Dimensions` (las seis tablas de una sola CSV) → `Map` de particiones diarias (bronze y silver de ese día; cada Lambda baja solo la partición cruda y las tablas silver que su SQL une) → `RefreshGold` (gold desde todo silver y el archivo de importación en `serving/<versión>/`). El paquete del pipeline es aparte (`src/cloud/requirements-pipeline.txt`: DuckDB, pandas, PyYAML); los contratos de calidad no corren en Lambda (necesitan pyarrow, que no entra en 250 MB).
- **Tabla serving.** Con `-c servingImport=<versión>` el CDK crea `expvivo-<stage>-serving-<versión>` importando ese archivo del lago. Los demás stacks la usan por nombre, sin export de CloudFormation, así que cambiar de versión es un `cdk deploy --all`.
- **Bedrock.** `allow_bedrock` da `bedrock-mantle:CreateInference` (el cliente Mantle de `src/llm`) además de `bedrock:InvokeModel*`, limitado a la región. Recurso `*` hasta confirmar el formato de ARN (`-c bedrockMantleResources=...` lo restringe).
- **Evaluación y modelos.** Si `data/eval/report_latest.yaml` existe al sintetizar, el CDK lo sube a `s3://<artifacts>/eval/report_latest.yaml`, que es lo que lee la página de evaluación. Los artefactos de M1 y M2 viajan dentro del paquete (`src/models/artifacts`): no hay que subirlos.

## 3. Cómo desplegar, en orden

Antes: usuario IAM en el grupo `expvivo-deployers` en un perfil `expvivo` (nunca en el repo), Node ≥ 18 con `npm install -g aws-cdk`, Python 3.12 con `pip install -r infra/requirements-dev.txt`, acceso a los modelos de Bedrock en us-east-2 (confirmar los IDs de `infra/cdk.json`), el front construido (`cd frontend && npm ci && npm run build`) y `make eval` corrido si se quiere publicar el informe. En Windows, usar una ruta corta para `cdk.out` (`-o`): pip falla con rutas de más de 260 caracteres.

1. **Bootstrap** (Andrés, una vez por cuenta y región): `cdk bootstrap aws://<cuenta>/us-east-2 --profile expvivo`.
2. **Despliegue**: `cd infra && cdk diff --all -c stage=dev-<nombre> -c bundle=true --profile expvivo`, revisar, y `cdk deploy --all` con los mismos argumentos. `bundle=true` instala las dependencias (pip local sin Docker; si falla, Docker). Si Bedrock todavía no está habilitado: `-c llmProvider=none` (reglas para G1 y la política determinista para G2). Comprobar `curl https://<SiteUrl>/api/health` → `deps: ok`.
3. **Datos crudos**: el rol del pipeline lee `rawBucket` (cdk.json) bajo `rawPrefix` (`data/`). Si es el bucket de los organizadores en otra cuenta y no da lectura a la nuestra, copiar el dataset a un bucket propio (`aws s3 sync` con la llave de solo lectura; es dato, no infraestructura) y desplegar con `-c rawBucket=<bucket>`.
4. **Backfill del pipeline**: una ejecución por tabla diaria, una tras otra (la entrada de Step Functions admite 256 KB). La lista de particiones sale del bucket crudo:

   ```bash
   AWS_PROFILE=expvivo python -c "import boto3,json,re,sys; s3=boto3.client('s3'); t=sys.argv[2]; ks=[o['Key'] for p in s3.get_paginator('list_objects_v2').paginate(Bucket=sys.argv[1],Prefix='data/'+t+'/') for o in p.get('Contents',[])]; ds=sorted({'-'.join(m.groups()) for k in ks for m in [re.search(r'year=(\d+)/month=(\d+)/day=(\d+)',k)] if m}); print(json.dumps({'partitions':[{'table':t,'date':d} for d in ds]}))" <rawBucket> transactions > transactions.json
   aws stepfunctions start-execution --state-machine-arn <PipelineStateMachineArn> --input file://transactions.json --profile expvivo
   ```

   Repetir para `call_center_interactions`, `call_transcripts`, `satisfaction_surveys`, `complaints`, `campaign_sends` y `digital_events`, y dejar `transactions` para el final: el `RefreshGold` de la última ejecución arma gold con todo. Su salida (`aws stepfunctions describe-execution --execution-arn <arn> --query output`) trae `gold.serving.import_prefix` = `serving/<versión>/`.
5. **Importar la tabla serving**: `cdk deploy --all -c stage=dev-<nombre> -c bundle=true -c servingImport=<versión> --profile expvivo`. La importación tarda minutos. Fuera de prod la tabla anterior se borra al final de la actualización del stack Data: hasta que el stack Api termina, las herramientas fallan (`tool_unavailable` → carril C). Hacerlo fuera de la ventana de jueces.
6. **Informe de evaluación**: viaja con el mismo `cdk deploy` (paso 2 o 5) si existe `data/eval/report_latest.yaml`.
7. **Smoke**: `python scripts/smoke_api.py https://<SiteUrl>` (los escenarios de analista quedan SKIP hasta que haya un usuario de Cognito con el flujo que usa la consola). Las trazas se consultan en CloudWatch Logs Insights sobre `/aws/lambda/expvivo-<stage>-api`: `filter ispresent(trace_id) | stats pct(latency_ms, 50), pct(latency_ms, 95) by name`.

Para borrar una etapa personal: `cdk destroy --all -c stage=dev-<nombre> --profile expvivo`.

## 4. Costos y alarmas

- Presupuesto mensual de la cuenta: `-c createBudget=true -c alertEmail=<correo>` en **una sola** etapa (normalmente prod); avisa al pasar 50, 100 y 150 USD (`budgetLimitsUsd`).
- Alarmas al tema SNS `expvivo-<stage>-alerts` (correo con `alertEmail`): errores de la API, 5xx, errores del investigador y de `case-steps`, ejecuciones fallidas del caso y del pipeline.
- Todo es pago por uso. El gasto grande es Bedrock (G1 en cada turno, G2 por caso de dinero). La API limita a 20 req/s (ráfaga 50) y a 60 sesiones por minuto por IP. El backfill son unas 7.700 invocaciones de Lambda de 3 GB: del orden de unos pocos dólares.

## 5. Qué queda sin verificar (no hubo AWS)

- Nada corrió en AWS: permisos IAM reales (sobre todo el ARN de `bedrock-mantle:CreateInference`), el orden de CloudFormation, la importación de DynamoDB desde S3 (el formato es el DynamoDB JSON de `serving.py`) y el cambio del atributo TTL (no se puede cambiar dos veces en menos de una hora).
- moto no ejecuta la máquina de estados: las pruebas llaman los pasos en el orden de la definición y la prueba de infraestructura compara la definición con lo que esperan los handlers. La precisión de EventBridge Scheduler (alrededor de un minuto) y el `at()` en UTC tampoco se midieron.
- Tamaño de los paquetes con `-c bundle=true` (no se corrió: descarga dependencias); estimado para el pipeline, unos 170 MB de 250. Arranque en frío y memoria de cada Lambda.
- El paso gold con el dataset completo dentro de 15 minutos, 3 GB de memoria y 4 GB de `/tmp` (`-c pipelineStorageMb` lo sube hasta 10240). Con la réplica sintética es cuestión de segundos.
- CloudFront reenviando `Authorization` y el inicio de sesión de analistas en Cognito (dependen de `api_stack.py`/`web_stack.py`, ver `docs/verificacion_cdk.md`).
- Límites conocidos: el interruptor `tools_down` y los bloqueos de tarjeta viven en la memoria de cada contenedor (`gateway/state.py`); las sesiones son "última escritura gana" entre contenedores; si `start` falla, el caso queda `open` hasta el próximo turno del cliente; las dimensiones usan la fecha de corte del dataset como fecha de la foto.
