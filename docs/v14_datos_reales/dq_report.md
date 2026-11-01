# Reporte de calidad de datos (capa silver)

Generado por `pipeline/quality.py` a partir de `contracts/silver.yml` (2026-09-26 06:26 UTC).

- Checks: 194 · PASS: 159 · WARN (defectos conocidos): 35 · FAIL (rompen contrato): 0

## Checks con problemas

| Tabla | Check | Columna | Severidad | Filas afectadas | % | Estado | Detalle |
|---|---|---|---|---|---|---|---|
| branches | coordenadas_en_isla_nula |  | warn | 167 | 47.714 | WARN |  |
| call_center_interactions | productos_mencionados_ajenos |  | warn | 274,341 | 39.974 | WARN | mentioned_products son IDs aleatorios: casi nunca existen y nunca son del cliente |
| call_center_interactions | espera_faltante |  | warn | 205,618 | 29.961 | WARN |  |
| call_center_interactions | duracion_faltante |  | warn | 96,234 | 14.022 | WARN |  |
| call_transcripts | texto_de_agente_con_placeholder |  | warn | 171,321 | 100.0 | WARN | El texto del agente trae '{monto} {moneda}' sin reemplazar |
| campaign_sends | envio_sin_consentimiento_de_marketing |  | warn | 874,417 | 50.058 | WARN |  |
| campaign_sends | envio_fuera_del_segmento_objetivo |  | warn | 763,550 | 43.711 | WARN |  |
| campaign_sends | asunto_con_nan |  | warn | 38,142 | 2.184 | WARN |  |
| complaints | sin_contacto_de_origen |  | warn | 67,095 | 100.0 | WARN |  |
| complaints | producto_afectado_de_otro_cliente |  | warn | 44,570 | 66.428 | WARN |  |
| complaints | monto_reclamado_sin_escala_de_moneda |  | warn | 10,858 | 16.183 | WARN |  |
| complaints | subcategoria_imputada |  | warn | 6,698 | 9.983 | WARN |  |
| customers | llave_foranea | registration_branch_id | warn | 149,995 | 99.997 | WARN | -> branches.branch_id |
| customers | email_compartido |  | warn | 79,930 | 53.287 | WARN | El email no identifica a un único cliente: no sirve para autenticar |
| customers | tipo_documento_invalido_para_pais |  | warn | 74,907 | 49.938 | WARN | México usa DNI (debería ser CURP/INE/Pasaporte) |
| customers | last_updated_en_el_futuro |  | warn | 9,316 | 6.211 | WARN |  |
| digital_events | evento_sin_cliente |  | warn | 3,745,446 | 23.977 | WARN |  |
| digital_events | producto_de_otro_cliente |  | warn | 1,440,322 | 9.22 | WARN |  |
| products | vencido_pero_activo |  | warn | 56,664 | 14.166 | WARN |  |
| products | last_updated_en_el_futuro |  | warn | 25,113 | 6.278 | WARN |  |
| products | numero_de_producto_duplicado |  | warn | 12 | 0.003 | WARN |  |
| satisfaction_surveys | categoria_nps_incoherente |  | warn | 3,274 | 1.539 | WARN |  |
| service_agents | sucursal_asignada_inexistente |  | warn | 831 | 69.25 | WARN |  |
| service_agents | codigo_de_empleado_duplicado |  | warn | 26 | 2.167 | WARN |  |
| service_agents | email_duplicado |  | warn | 24 | 2.0 | WARN |  |
| transactions | amount_usd_imputado |  | warn | 2,537,456 | 57.344 | WARN |  |
| transactions | tx_antes_del_registro_del_cliente |  | warn | 829,916 | 18.755 | WARN |  |
| transactions | tx_antes_de_apertura_del_producto |  | warn | 827,989 | 18.712 | WARN |  |
| transactions | tx_de_cliente_inactivo_o_cerrado |  | warn | 525,056 | 11.866 | WARN |  |
| transactions | tx_con_tarjeta_vencida |  | warn | 462,295 | 10.447 | WARN |  |
| transactions | codigo_de_respuesta_nulo |  | warn | 221,033 | 4.995 | WARN |  |
| transactions | pais_distinto_al_del_cliente |  | warn | 202,800 | 4.583 | WARN |  |
| transactions | codigo_de_rechazo_en_pending_o_reversed |  | warn | 126,391 | 2.856 | WARN |  |
| transactions | semantica_de_codigo_inconsistente |  | warn | 120,969 | 2.734 | WARN | Códigos de tarjeta (14, 54) en productos sin tarjeta o 'fondos insuficientes' (51) en depósitos |
| transactions | categoria_de_comercio_imputada |  | warn | 51,952 | 1.174 | WARN |  |

## Checks que pasan (por tabla)

- **branches**: primary_key_no_nula(branch_id), primary_key_unica(branch_id)
- **call_center_interactions**: contact_reason_distinto_de_categoria, fecha_contable_corte_08h, filas_minimas, frescura(process_date), llave_foranea(agent_id), llave_foranea(customer_id), no_nulo(agent_id), no_nulo(category), no_nulo(channel), no_nulo(customer_id), no_nulo(interaction_type), no_nulo(process_date), no_nulo(ts), primary_key_no_nula(interaction_id), primary_key_unica(interaction_id), rango(duration_s), rango(sentiment_score), rango(wait_s), valores_permitidos(category), valores_permitidos(channel), valores_permitidos(interaction_type), valores_permitidos(sentiment)
- **call_transcripts**: frescura(process_date), llave_foranea(interaction_id), no_nulo(customer_id), no_nulo(full_text), no_nulo(interaction_id), primary_key_no_nula(transcript_id), primary_key_unica(transcript_id), unico(interaction_id), valores_permitidos(detected_language)
- **campaign_sends**: llave_foranea(campaign_id), llave_foranea(customer_id), primary_key_no_nula(send_id), primary_key_unica(send_id)
- **complaints**: fecha_contable_corte_08h, filas_minimas, frescura(process_date), llave_foranea(affected_product_id), llave_foranea(assigned_agent_id), llave_foranea(customer_id), llave_foranea(related_branch_id), no_nulo(case_type), no_nulo(category), no_nulo(customer_id), no_nulo(priority), no_nulo(process_date), no_nulo(status), no_nulo(subcategory), no_nulo(ts), primary_key_no_nula(complaint_id), primary_key_unica(complaint_id), rango(resolution_days), valores_permitidos(case_type), valores_permitidos(category), valores_permitidos(claimed_currency), valores_permitidos(priority), valores_permitidos(reception_channel), valores_permitidos(status)
- **customers**: filas_minimas, no_nulo(country), no_nulo(customer_status), no_nulo(date_of_birth), no_nulo(document_number), no_nulo(document_type), no_nulo(first_name), no_nulo(last_name), no_nulo(registration_date), no_nulo(segment), primary_key_no_nula(customer_id), primary_key_unica(customer_id), rango(age), rango(credit_score), unico(document_number), valores_permitidos(country), valores_permitidos(customer_status), valores_permitidos(document_type), valores_permitidos(gender), valores_permitidos(segment)
- **digital_events**: llave_foranea(customer_id), primary_key_no_nula(event_id), primary_key_unica(event_id)
- **exchange_rates**: frescura(rate_date), primary_key_no_nula(rate_date, source_currency, target_currency), primary_key_unica(rate_date, source_currency, target_currency), spread_compra_venta_inconsistente, valores_permitidos(source_currency), valores_permitidos(target_currency)
- **marketing_campaigns**: fin_antes_del_inicio, primary_key_no_nula(campaign_id), primary_key_unica(campaign_id)
- **products**: filas_minimas, llave_foranea(customer_id), llave_foranea(opening_branch_id), mora_solo_en_productos_de_credito, no_nulo(balance), no_nulo(currency), no_nulo(customer_id), no_nulo(opening_date), no_nulo(product_number), no_nulo(product_status), no_nulo(product_type), primary_key_no_nula(product_id), primary_key_unica(product_id), valores_permitidos(currency), valores_permitidos(product_status), valores_permitidos(product_type), vencimiento_antes_de_apertura
- **satisfaction_surveys**: agente_distinto_al_del_contacto, ces_fuera_de_escala_1_7, cliente_distinto_al_del_contacto, csat_fuera_de_escala_1_5, frescura(process_date), llave_foranea(interaction_id), no_nulo(interaction_id), no_nulo(score), no_nulo(survey_type), nps_fuera_de_escala_0_10, primary_key_no_nula(survey_id), primary_key_unica(survey_id), unico(interaction_id), valores_permitidos(survey_type)
- **service_agents**: filas_minimas, primary_key_no_nula(agent_id), primary_key_unica(agent_id), valores_permitidos(agent_status), valores_permitidos(agent_type), valores_permitidos(experience_level)
- **transactions**: codigo_00_si_y_solo_si_aprobada, fecha_contable_corte_06h, filas_minimas, fraude_confirmado_por_score_alto, frescura(process_date), llave_foranea(branch_id), llave_foranea(customer_id), llave_foranea(product_id), moneda_distinta_a_la_del_producto, no_nulo(amount), no_nulo(amount_usd), no_nulo(currency), no_nulo(customer_id), no_nulo(process_date), no_nulo(product_id), no_nulo(status), no_nulo(transaction_type), no_nulo(ts), primary_key_no_nula(transaction_id), primary_key_unica(transaction_id), producto_de_otro_cliente, rango(amount), rango(fraud_score), valores_permitidos(channel), valores_permitidos(currency), valores_permitidos(fraud_risk_tier), valores_permitidos(response_code), valores_permitidos(status), valores_permitidos(transaction_type)
