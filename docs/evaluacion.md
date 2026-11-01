# Evaluación de Expediente Vivo v2

Generado por `make eval` (`src/eval/`), versión `eval-2026-10-29.1`, 2026-10-30T05:34:51Z. Datos: **synthetic_replica** (réplica sintética del dataset: los números NO son del dataset de los organizadores).

**240 escenarios sembrados** sobre clientes y transacciones gold (36 adversarios), idiomas {'es': 115, 'pt': 125}, países {'AR': 54, 'CO': 79, 'MX': 107}. Cada uno corre de punta a punta por la misma app que el chat (orquestador, gateway, reglas, caso, ciclo de vida, investigador) con un cliente simulado. La misma tanda corre con v2 y con la versión v1.4 solo reglas (B1).

## Métricas principales

| Métrica | v2 | B1 (v1.4 reglas) | Meta | ¿Cumple? |
|---|---|---|---|---|
| Resolución automática segura | 41.6% (79/190) [34.7–47.9] | 40.5% (77/190) [33.7–46.8] | >= 30% con 0 cierres incorrectos | sí |
| Cierres incorrectos en ruta A | 0.0% (0/190) [0.0–0.0] | 0.0% (0/190) [0.0–0.0] | 0 | sí |
| Contención (cerrado sin humano) | 41.6% (79/190) [34.7–47.9] | 40.5% (77/190) [33.7–46.8] | comparado con B1 | — |
| Ruta correcta (A/B/C) | 100.0% (190/190) [100.0–100.0] | 99.0% (188/190) [97.4–100.0] | — | — |
| Cargo correcto en el caso | 100.0% (163/163) [100.0–100.0] | 100.0% (161/161) [100.0–100.0] | — | — |
| Derivaciones críticas omitidas | 0.0% (0/190) [0.0–0.0] | 0.0% (0/190) [0.0–0.0] | 0 | sí |
| Derivaciones innecesarias | 0.0% (0/190) [0.0–0.0] | 1.1% (2/190) [0.0–2.6] | <= 10% | sí |
| Paquete de derivación completo | 100.0% (62/62) [100.0–100.0] | 100.0% (64/64) [100.0–100.0] | 100% | sí |
| Inseguro: cargo equivocado registrado | 0.0% (0/194) [0.0–0.0] | 0.0% (0/194) [0.0–0.0] | 0 | sí |
| Inseguro: datos de otro cliente | 0.0% (0/240) [0.0–0.0] | 0.0% (0/240) [0.0–0.0] | 0 | sí |
| Inseguro: acción por texto inyectado | 0.0% (0/24) [0.0–0.0] | 0.0% (0/24) [0.0–0.0] | 0 | sí |
| Cargo equivocado propuesto como seguro | 0.0% (0/163) [0.0–0.0] | 0.0% (0/161) [0.0–0.0] | < 1% | sí |
| Primer cargo propuesto directo y correcto | 92.0% (150/163) [87.7–95.7] | 93.8% (151/161) [90.1–97.5] | — | — |
| Investigador: recomendación correcta | 98.0% (48/49) [93.9–100.0] | — | mejor que B1 (caso vacío) | — |
| Investigador: citas válidas | 100.0% (63/63) [100.0–100.0] | — | 100% | sí |
| Investigador: reportes con afirmaciones sin respaldo quitadas | 0.0% (0/63) [0.0–0.0] | — | — | — |

## Latencia y costo

- Turno de chat: p50 21.9 ms, p95 117.1 ms (639 turnos). Meta: turno p95 < 6 s; investigador p95 < 90 s.
- Investigador: p50 79 ms, p95 176.7 ms (63 casos).
- medido en local, proceso único, sin red ni modelo real (G1 mock, G2 política determinista).
- Costo medido por conversación: 0.0 USD (medido con el proveedor mock: no hay llamadas reales (G1 sin fixture usa reglas)).
- Costo PROYECTADO: chat 0.00251 USD por conversación (claude-haiku-4-5), investigador 0.0609 USD por caso (claude-sonnet-5, 26% de las conversaciones); total 0.01849 USD por conversación. Meta: < 0.05 USD por conversación. PROYECCIÓN con precios de lista y tamaños de prompt medidos; sin prompt caching (cota alta).

## Equidad (v2)

| Grupo | n | Ruta correcta | Cargo correcto | Resolución automática segura | Resuelto cuando debía (n) |
|---|---|---|---|---|---|
| es | 115 | 100.0% | 100.0% | 45.8% | 100.0% (44) |
| pt | 125 | 100.0% | 100.0% | 37.2% | 100.0% (35) |
| AR | 54 | 100.0% | 100.0% | 53.3% | 100.0% (24) |
| CO | 79 | 100.0% | 100.0% | 36.8% | 100.0% (21) |
| MX | 107 | 100.0% | 100.0% | 38.6% | 100.0% (34) |

Brechas (meta: <= 5 puntos o explicadas):

- idioma, ruta correcta: 0.0 puntos (igual en todos los grupos); dentro de la meta.
- idioma, cargo correcto: 0.0 puntos (igual en todos los grupos); dentro de la meta.
- idioma, resolución automática segura: 8.6 puntos (es frente a pt); mezcla de escenarios: la parte de casos que deben resolverse en ruta A cambia por grupo (es 38%, pt 28%); entre los casos que debían resolverse, la brecha es 0.0 puntos.
- idioma, resuelto cuando debía: 0.0 puntos (igual en todos los grupos); dentro de la meta.
- país, ruta correcta: 0.0 puntos (igual en todos los grupos); dentro de la meta.
- país, cargo correcto: 0.0 puntos (igual en todos los grupos); dentro de la meta.
- país, resolución automática segura: 16.5 puntos (AR frente a CO); mezcla de escenarios: la parte de casos que deben resolverse en ruta A cambia por grupo (AR 44%, CO 27%, MX 32%); entre los casos que debían resolverse, la brecha es 0.0 puntos.
- país, resuelto cuando debía: 0.0 puntos (igual en todos los grupos); dentro de la meta.

## Por tipo de escenario (v2)

La ruta esperada sale de las reglas aplicadas a la verdad sembrada de cada escenario (por eso un tipo puede esperar varias rutas: un fraude real con fraud_score <= 30 espera B; uno ya revertido, A). Los tipos sin ruta esperada (adversarios y cargo inexistente) se miden solo por resultados inseguros.

| Tipo | n | Rutas esperadas | Rutas obtenidas | Ruta correcta | Resultados inseguros |
|---|---|---|---|---|---|
| already_reversed | 14 | A 14 | A 14 | 14/14 | 0 |
| charge_not_exist | 14 | sin ruta esperada 14 | C 14 | — | 0 |
| duplicate_reversed | 10 | A 10 | A 10 | 10/10 | 0 |
| fee_matches | 12 | A 12 | A 12 | 12/12 | 0 |
| fee_off_schedule | 22 | B 22 | B 22 | 22/22 | 0 |
| forgotten_purchase | 24 | A 24 | A 24 | 24/24 | 0 |
| injection | 24 | sin ruta esperada 24 | sin caso 24 | — | 0 |
| lost_card | 10 | C 10 | C 10 | 10/10 | 0 |
| near_identical | 14 | B 14 | B 14 | 14/14 | 0 |
| other_customer | 12 | sin ruta esperada 12 | sin caso 12 | — | 0 |
| planted_duplicate | 24 | B 24 | B 24 | 24/24 | 0 |
| real_fraud | 26 | A 2, B 3, C 21 | A 2, B 3, C 21 | 26/26 | 0 |
| regulator | 10 | C 10 | C 10 | 10/10 | 0 |
| repeat_complainer | 7 | C 7 | C 7 | 7/7 | 0 |
| unfamiliar_merchant_name | 17 | A 17 | A 17 | 17/17 | 0 |

## Pruebas de seguridad (v2)

- Herramientas pidiendo registros de otro cliente: 0 fugas en 204 intentos.
- Sesión vencida: 0 respuestas con datos en 3 intentos.
- Caso de otra sesión: 0 fugas en 1 intentos.
- Herramienta caída: ruta C, degradado ['tool_unavailable'] (camino seguro).

## Modelos

**M1** `m1-lgbm-1` (primario: model), 8,000 consultas de prueba:

| | M1 | Regla v1.4 |
|---|---|---|
| Top-1 | 75.5% | 71.1% |
| Recall@3 | 97.1% | 95.8% |
| Confirmaciones equivocadas | 0.4% | 2.9% |
| Equivocadas entre confirmadas | 1.5% | 8.1% |
| Confirmación directa | 23.9% | 35.6% |
| Detección de cargo inexistente | 96.6% | 79.8% |

Detalle en [docs/modelos/m1.md](modelos/m1.md).

**M2** `m2-hashlr-1`, 180 mensajes de prueba (30 adversarios; escrito aparte de los generadores; nunca usado para entrenar ni para elegir umbrales):

| | M2 | Palabras clave (B1) |
|---|---|---|
| Macro-F1 | 0.935 [0.897–0.968] | 0.495 [0.411–0.565] |
| Macro-F1 ES / PT | 0.937 / 0.927 | 0.435 / 0.577 |
| Manipulación detectada | 91.7% | 47.2% |
| Manipulación no detectada y aceptada como otra intención | 0 | 11 |
| Ruteo equivocado entre aceptados | 0.7% | 29.4% |

Diferencia de macro-F1 (bootstrap pareado): [0.369–0.524]. Compuerta: acepta 76.1%, pregunta 22.8%, humano 1.1%; ECE 0.0361 → 0.035. B2 (LLM sin entrenamiento): no ejecutado: requiere acceso a un modelo (LLM_PROVIDER=anthropic|bedrock). Detalle en [docs/modelos/m2.md](modelos/m2.md).

**M3** `m3-evidence-1`: lift no significativo (PR-AUC 0.0016 frente a 0.0027 de fraud_score; IC95 de la diferencia [-0.0057, 0.00027]). Decisión: evidence_only. Detalle en [docs/modelos/m3.md](modelos/m3.md).

**G1, G2, G3, B2**: G1: LLM_PROVIDER=mock: fixtures solo para los mensajes del modo juez; en los escenarios generados responde el extractor por reglas. Con un proveedor real (anthropic o bedrock) se mide el modelo. G2: Sin credenciales de modelo, el investigador corre con la política determinista (mismo bucle, mismas herramientas, mismo chequeo de citas). G3: El juez offline (Opus) necesita un modelo; el código y la rúbrica están en src/eval/judge.py y src/prompts/g3_judge. B2_ZERO_SHOT: Requiere un modelo (LLM_PROVIDER=anthropic|bedrock).

## Línea base histórica (B0, desde los datos)

| Métrica | Valor | n | Descripción |
|---|---|---|---|
| transactional_contact_share | 35.1% | 18,012 | Contactos transaccionales (saldo, movimientos) |
| fraud_rule_precision | 100.0% | 260 | Precisión de la regla fraud_score > 30 |
| median_first_response_hours | 38.375 hours | 1,090 | Mediana de horas hasta la primera respuesta de un reclamo formal |
| median_resolution_days | 15 days | 431 | Mediana de días hasta resolver un reclamo |
| p90_resolution_days | 28 days | 431 | Percentil 90 de días hasta resolver |
| complaints_never_assigned | 711 count | 1,812 | Reclamos que nunca se asignaron a nadie |
| complaints_with_amount_rate | 29.8% | 1,812 | Reclamos que llegan con un monto |
| affected_product_foreign_rate | 100.0% | 1,199 | Reclamos cuyo producto afectado pertenece a otro cliente |
| money_complaint_share | 40.6% | 1,812 | Reclamos de dinero (cargo no reconocido o cobro indebido) |
| complaint_contact_fcr | 43.6% | 3,050 | Contactos con motivo Queja resueltos en el primer contacto |
| fraud_rule_recall | 57.9% | 449 | Fraudes que la regla fraud_score > 30 detecta |
| complaint_contact_csat | 2.4288 score_1_5 | 534 | CSAT medio de los contactos por queja |

## Límites

- Datos: réplica sintética del dataset (los organizadores no dieron acceso en esta máquina); repetir con `make data` usando la llave S3
- Escenarios sembrados sobre clientes y transacciones gold; los mensajes son generados y sintéticos.
- El cliente simulado es cuidadoso: nunca confirma un cargo equivocado, por eso se mide aparte qué propone el sistema.
- G1 y G2 corren sin modelo real (mock y política determinista); G3 y B2 no se ejecutaron.
- El set de prueba de M2 (180 mensajes) lo escribió un agente aparte de los generadores, no el equipo como pedía el plan (docs/modelos/m2.md); el ciclo de aprendizaje usa decisiones de analista simuladas.
- Latencia medida en local, sin red ni arranque en frío de Lambda.
- El portugués es generado, sin revisión nativa.
