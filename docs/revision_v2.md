# Revisión independiente del build v2 (1 de noviembre de 2026)

Revisión del repositorio en `main` (commit `345b916`, PR #2 fusionado) hecha con el principio del repo
`hold-under-fire`: no aceptar la afirmación, mirar el artefacto. Se corrieron las tres suites de tests, se recorrió la
demo en un navegador como lo haría un jurado, y se leyó el código por áreas (datos y gateway; conversación, modelos y
LLM; evaluación; front, API, nube e infraestructura), verificando cada hallazgo en el código o con un comando. Este
documento resume qué se encontró y qué se cambió después. Datos: réplica sintética (D15) en todo lo que sigue.

## 1. Qué se verificó directamente

| Qué | Resultado |
|---|---|
| `compileall` y pytest completo | 1.367 tests pasan |
| vitest | 256 tests pasan |
| Tests de infra | 17 pasan, solo con Node en el PATH (jsii lo necesita) |
| Demo en el navegador | Rutas A, B y C; portugués completo; consola con aprobación que llega al chat; bloqueo de tarjeta solo con "sí"; inyección y pedido de datos ajenos con plantilla; herramienta caída con dos reintentos y ruta C; sesión vencida con 401 |
| Trazas | 17 claves fijas más `source`; ningún fragmento del texto del cliente |
| GitHub | Repos públicos; PR #2 fusionado con 5 chequeos en verde |

## 2. Lo que se encontró

### Cifras que valían por construcción

- **Evaluación de 240 escenarios.** La ruta esperada es el mismo archivo de reglas aplicado a los hechos sembrados, y
  el cliente simulado elige el cargo por su id oculto (`src/eval/runner.py`, `_decide`): nunca confirma un cargo
  ajeno, nunca rechaza una explicación, nunca pide una persona. "Ruta correcta 100 %", "cargo correcto 100 %" y
  "0 cierres incorrectos" salen en gran parte de ese diseño. Cinco reglas (`identity_not_verified`,
  `asks_for_human`, `asks_compensation`, `complaint_about_person`, `explanation_not_accepted`) no se ejercitan.
- **v2 frente a B1.** Recontado desde `report_latest.yaml`: solo SC-130 y SC-133 (`fee_matches`) cambian de ruta;
  41,6 % frente a 40,5 %, 100 % frente a 99 % y 0 frente a 2 derivaciones innecesarias son ese mismo par de casos.
- **Investigador 48/49.** Es la política determinista, no un modelo; en 46 de 49 casos recomienda lo que la regla
  de ruta ya implica. En el primer reporte, B1 con su stub acertaba 49/49; después B1 corrió sin investigador.
  "Citas válidas 100 %" es por construcción: la política solo cita sus propios resultados, y el chequeo verifica
  que el id exista, no que respalde la afirmación.
- **"M1 hace 8 veces menos confirmaciones equivocadas"** compara un umbral elegido en validación para eso contra
  una regla con umbrales fijos. La cifra comparable es top-1 75,5 % frente a 71,1 %, sobre consultas sintéticas
  del mismo generador.
- **Intervalos bootstrap** que colapsaban a [0, 0] en 0/190 y a [100, 100] en 190/190; "sesión vencida 0 de 3"
  tiene un intervalo de Wilson hasta 56 %.
- **Pruebas de seguridad** en proceso (gateway y `app.chat`), no por HTTP; **ciclo de aprendizaje** con etiquetas
  derivadas de la verdad sembrada; **costo** correcto como orden de magnitud, no como cota alta.
- **Réplica** menos fiel de lo dicho (30 eventos digitales por cliente frente a 104 reales, envíos de campaña a la
  mitad, cinco defectos ausentes) y **B0 "reproducidos"** que son parámetros del generador leídos de vuelta.

### Errores de código

- **D2 matizable.** La clase de M2 por sí sola disparaba `lost_card` y `asks_for_human` (ruta C) sin mirar su
  confianza; con el modelo entrenado como clasificador, "voy a ir a la CONDUSEF" (manipulación con p 0,61) bloqueaba
  la escalada al regulador, y "quiero una segunda opinión" (`human_request` con p 0,33) derivaba a una persona en
  vez de reabrir el caso para el analista.
- **Con modelo real, el ciclo de vida local se caía.** `InvestigatorError` (respuesta malformada, `max_tokens`,
  4xx) no se capturaba en `lifecycle._investigate` y el caso quedaba en "investigando".
- **Guardia por patrones.** Sola detecta 1 de 4 mensajes de manipulación nuevos (M2 modelo, 4 de 4), y era el
  clasificador por defecto fuera de `local_api.py`, la evaluación y el CDK.
- **Prompt de G1** en un solo turno `user` (instrucciones y texto del cliente juntos).
- **Cliente LLM.** Cualquier HTTP 400 disparaba el fallback de modo JSON (hasta 3 peticiones por un error ajeno al
  esquema, con el error real enmascarado); el presupuesto de 90 s de G2 solo se comprobaba entre vueltas.
- **Despliegue.** `duckdb==2.0.0` sin wheel `manylinux2014` rompía el empaquetado local de la Lambda (y la Lambda
  del API no usa duckdb); CloudFront descarta `Authorization` en GET con `CACHING_DISABLED`; Cognito solo con SRP,
  el front lanzaba "not enabled yet" y la CSP bloqueaba el dominio de Cognito; `states:SendTask*` con recurso
  acotado.
- **Menores.** `data/serving` no existía aunque `make pipeline` lo promete; `gold.labels` con 0 filas; test de PII
  sobre 4 de 14 tablas y solo por nombre; test de contratos con `>= 150` en vez de 194; textos desactualizados en
  `INTERFACES.md` ("not merged yet"), la pantalla de la consola, `docs/llm_client.md`; `build.txt` = `dev`.

## 3. Lo que se cambió después (rama `arturo/revision-fixes`)

- `src/investigator/agent.py`: toda falla del modelo (no disponible, timeout, HTTP 4xx, rechazo, `max_tokens`, sin
  `submit_report`) pasa la misma corrida a la política determinista con el motivo en `open_questions`; cada llamada
  recibe como timeout lo que queda del presupuesto de 90 s.
- `src/conversation/lifecycle.py`: cualquier excepción del investigador deja el caso con el analista, marcado
  `evidence_incomplete`, con `error_code` `investigator_error` o `investigator_crash` en la traza.
- `src/conversation/orchestrator.py`: la clase de M2 entra a las reglas y a las banderas C solo con
  `gate_action: accept`; un veredicto de manipulación bloquea solo desde la guardia por patrones o desde el modelo
  por encima de su umbral; una duda queda en la traza y el mensaje sigue su camino.
- `src/conversation/models_port.py`: `M2_BACKEND=model` por defecto (cae a `keywords` si falta el artefacto).
- `src/conversation/prompts/g1_extract/v1.md`: instrucciones como system prompt, texto del cliente solo en el turno
  `user`; el render completo (mock y fixtures) no cambia.
- `src/llm/client.py`: el reintento al siguiente modo JSON solo ante un 400 que hable del formato, el esquema o las
  herramientas; el mensaje del error incluye el texto de la API.
- `src/eval/runner.py`: intervalos de Wilson en todas las tasas.
- `src/etl/synth_dataset.py`, `src/gateway/state.py`: docstrings que dicen lo que el código hace.
- `tests/`: contratos `== 194`, PII sobre las 14 tablas (nombre y contenido), `tests/test_review_round4.py` con un
  test por hallazgo corregido.
- Infra y front: empaquetado local de la Lambda sin Docker (wheels `manylinux_2_28/2_17/2014`, sin duckdb en el API:
  33,6 MiB; pipeline 213 MiB de 250), cache policy propia de CloudFront para `/api/*` con `Authorization`,
  `USER_PASSWORD_AUTH` en Cognito e inicio de sesión real en la consola (el sitio publica `/config.json` con la
  región, el pool y el client id; en local no existe y se usa el token), `states:SendTask*` con `Resource: "*"`,
  `dist/build.txt` con sha y fecha al compilar, nombres accesibles en los interruptores del panel del juez y en los
  botones de respuesta rápida; 6 tests de infra y 9 de vitest nuevos.
- Documentación: README (secciones 1, 2, 4, 6, 7, 8, 11 y 13), `INTERFACES.md`, `docs/pitch.md`,
  `docs/despliegue_aws.md`, `docs/llm_client.md`, `docs/como_correr_local.md`; `make pipeline` regenerado con
  `data/serving` y `gold.labels`; `make eval` regenerado con los nuevos intervalos.

## 4. Lo que sigue sin cubrir

- La evaluación sigue sembrada y con un cliente simulado que conoce la verdad. Un cliente simulado que elija por
  monto, fecha y comercio (sin el id oculto) separaría mejor a v2 de B1; no se construyó.
- G1 y G2 no han corrido con un modelo real; G3 y B2 siguen sin ejecutar.
- La corrida con los datos reales (`make data` con la llave S3) y el despliegue (usuario IAM) siguen pendientes; el
  backfill de `digital_events` a escala real no está medido.
- El estado de la demo (interruptores, bloqueos de tarjeta) vive en memoria del proceso: con varios contenedores
  Lambda hace falta `ReservedConcurrentExecutions` o moverlo al registro de sesión.
