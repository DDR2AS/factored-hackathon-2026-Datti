# Perfil de datos: LATAM Bank (capa bronze)

Generado por `analysis/profile_tables.py`. Tablas >1M de filas: muestra por días completos.

## branches

- **Filas:** 350 (completa) · **Columnas:** 22
- **Duplicados exactos:** 0 (0.0%) · **PK `branch_id` repetida:** 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `branch_id` | id | 0.0 | 350 |  |
| `branch_code` | id | 0.0 | 350 |  |
| `branch_name` | categórica | 0.0 | 175 | Banco LATAM Ciudad de México Plaza 3.14%, Banco LATAM Puebla Plaza 2.86%, Banco LATAM Guadalajara Centro 2.57%, Banco LATAM Monterrey Plaza 2.57%, Banco LATAM Tijuana Centro 2.29%, Banco LATAM Ciudad de México Centro 2.29% |
| `branch_type` | categórica | 0.0 | 4 | Express 37.14%, Corporate 36.86%, Premium 13.71%, Main 12.29% |
| `address` | id | 0.0 | 350 |  |
| `city` | categórica | 0.0 | 16 | Puebla 9.43%, Guadalajara 8.57%, Tijuana 8.29%, Ciudad de México 8.29%, Monterrey 8.0%, Querétaro 7.43% |
| `state` | categórica | 0.0 | 16 | Puebla 9.43%, Jalisco 8.57%, Baja California 8.29%, Ciudad de México 8.29%, Nuevo León 8.0%, Querétaro 7.43% |
| `country` | categórica | 0.0 | 3 | México 50.0%, Colombia 30.0%, Argentina 20.0% |
| `postal_code` | categórica | 0.0 | 105 | 72000 9.43%, 44100 8.57%, 22000 8.29%, 01000 8.29%, 64000 8.0%, 76000 7.43% |
| `geographic_zone` | categórica | 0.0 | 1 | Urbana 100.0% |
| `phone` | id | 0.0 | 350 |  |
| `email` | id | 0.0 | 350 |  |
| `opening_time` | categórica | 0.0 | 4 | 09:30:00 29.71%, 09:00:00 25.43%, 08:00:00 23.14%, 08:30:00 21.71% |
| `closing_time` | categórica | 0.0 | 5 | 17:00:00 26.0%, 19:00:00 21.71%, 20:00:00 19.43%, 18:00:00 16.57%, 17:30:00 16.29% |
| `has_atms` | booleano | 0.0 | 1 | True 100.0% |
| `atm_count` | numérico | 0.0 | 7 | 8 15.43%, 4 15.14%, 7 14.86%, 3 14.86%, 6 14.0%, 5 13.14%; rango 2 → 8, mediana 5 |
| `has_teller_windows` | booleano | 0.0 | 1 | True 100.0% |
| `teller_window_count` | numérico | 0.0 | 10 | 12 13.71%, 10 11.14%, 6 10.86%, 7 10.86%, 8 10.29%, 9 9.71%; rango 3 → 12, mediana 8 |
| `latitude` | numérico | 0.0 | 350 | rango -34.6908 → 25.7818, mediana 0.0473613 |
| `longitude` | numérico | 0.0 | 350 | rango -103.449 → 0.0999953, mediana -58.4006 |
| `branch_opening_date` | fecha | 0.0 | 346 | 1990-01-03 → 2023-05-11 |
| `branch_status` | categórica | 0.0 | 2 | Active 96.0%, Temporarily Closed 4.0% |

## call_center_interactions

- **Filas:** 686,296 (completa) · **Columnas:** 21
- **Duplicados exactos:** 0 (0.0%) · **PK `interaction_id` repetida:** 0 (0.0%)
- **Llegadas tardías** (`_process_date` > `interaction_date`): 0.0% · máx. 0 días · adelantadas: 33.27%
- **FK huérfanas:** `customer_id`→customers: 0 (0.0%), `agent_id`→service_agents: 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `interaction_id` | id | 0.0 | 686,296 |  |
| `interaction_date` | fecha | 0.0 | 683,625 | 2023-06-17 → 2026-06-18 |
| `process_date` | fecha | 0.0 | 1,097 | 2023-06-17 → 2026-06-17 |
| `customer_id` | categórica | 0.0 | 148,443 |  |
| `agent_id` | categórica | 0.0 | 1,090 |  |
| `interaction_type` | categórica | 0.0 | 5 | Inbound Call 70.04%, Outbound Call 14.95%, Chat 10.01%, Email 4.01%, Video 0.99% |
| `channel` | categórica | 0.0 | 6 | Phone 84.99%, Email 4.01%, App 3.84%, WhatsApp 3.34%, Web Chat 3.33%, Web 0.49% |
| `contact_reason` | categórica | 0.0 | 6 | Transaccional 34.98%, Producto 21.98%, Queja 17.05%, Técnico 14.99%, Comercial 8.0%, Retención 3.0% |
| `reason_category` | categórica | 0.0 | 6 | Transaccional 34.98%, Producto 21.98%, Queja 17.05%, Técnico 14.99%, Comercial 8.0%, Retención 3.0% |
| `duration_seconds` | numérico | 14.02 | 1,049 | rango 30 → 1204, mediana 291 |
| `wait_time_seconds` | numérico | 29.96 | 370 | rango 0 → 424, mediana 119 |
| `was_resolved` | booleano | 0.0 | 2 | True 76.65% |
| `requires_followup` | booleano | 0.0 | 2 | True 34.83% |
| `detected_sentiment` | categórica | 0.0 | 5 | Neutral 66.98%, Negativo 13.74%, Positivo 11.01%, Muy Negativo 5.5%, Muy Positivo 2.76% |
| `sentiment_score` | numérico | 0.0 | 202 | rango -1 → 1, mediana -0.02 |
| `customer_detected_accent` | categórica | 29.83 | 3 | mexican 49.98%, colombian 30.05%, argentine 19.97% |
| `agent_used_accent` | categórica | 29.83 | 3 | mexican 50.24%, colombian 29.98%, argentine 19.78% |
| `was_escalated` | booleano | 0.0 | 2 | True 9.96% |
| `mentioned_products` | id | 60.03 | 274,341 |  |
| `has_transcript` | booleano | 0.0 | 2 | True 24.96% |
| `has_recording` | booleano | 0.0 | 2 | True 85.98% |

## call_transcripts

- **Filas:** 171,321 (completa) · **Columnas:** 18
- **Duplicados exactos:** 0 (0.0%) · **PK `transcript_id` repetida:** 0 (0.0%)
- **FK huérfanas:** `customer_id`→customers: 0 (0.0%), `agent_id`→service_agents: 0 (0.0%), `interaction_id`→call_center_interactions: 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `transcript_id` | id | 0.0 | 171,321 |  |
| `interaction_id` | id | 0.0 | 171,321 |  |
| `process_date` | fecha | 0.0 | 1,097 | 2023-06-17 → 2026-06-17 |
| `customer_id` | categórica | 0.0 | 101,951 |  |
| `agent_id` | categórica | 0.0 | 1,090 |  |
| `full_text` | texto | 0.0 | 546 | largo prom. 278 car. |
| `customer_text` | texto | 0.0 | 42 | largo prom. 94 car. |
| `agent_text` | texto | 0.0 | 42 | largo prom. 153 car. |
| `detected_language` | categórica | 0.0 | 1 | es 100.0% |
| `detected_accent` | categórica | 36.82 | 3 | mexican 50.03%, colombian 29.83%, argentine 20.14% |
| `accent_confidence` | numérico | 10.01 | 25 | rango 0.75 → 0.99, mediana 0.87 |
| `detected_keywords` | categórica | 5.13 | 12 | banco, servicio, cuenta 11.18%, cuenta, servicio, banco 11.15%, cuenta, banco, servicio 11.14%, servicio, cuenta, banco 11.09%, servicio, banco, cuenta 11.09%, banco, cuenta, servicio 11.02% |
| `mentioned_entities` | texto | 10.02 | 54 | largo prom. 74 car. |
| `detected_intents` | categórica | 4.94 | 1 | consulta_general 100.0% |
| `main_topics` | categórica | 0.0 | 6 | Transaccional 34.9%, Producto 21.98%, Queja 17.04%, Técnico 15.0%, Comercial 8.06%, Retención 3.02% |
| `transcription_model` | categórica | 0.0 | 4 | AWS Transcribe 25.17%, Whisper v3 24.98%, Google STT 24.95%, Azure Speech 24.9% |
| `audio_quality` | categórica | 5.04 | 3 | High 70.3%, Medium 24.8%, Low 4.89% |
| `duration_seconds` | numérico | 14.03 | 973 | rango 30 → 1151, mediana 290 |

## campaign_sends

- **Filas:** 1,746,801 (muestra de 437,421 filas (1 de cada 4 días, 271 días)) · **Columnas:** 22
- **Duplicados exactos:** 0 (0.0%) · **PK `send_id` repetida:** 0 (0.0%)
- **Llegadas tardías** (`_process_date` > `send_date`): 0.0% · máx. 0 días · adelantadas: 24.98%
- **FK huérfanas:** `customer_id`→customers: 0 (0.0%), `campaign_id`→marketing_campaigns: 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `send_id` | id | 0.0 | 437,421 |  |
| `send_date` | fecha | 0.0 | 433,102 | 2023-07-01 → 2026-06-16 |
| `process_date` | fecha | 0.0 | 271 | 2023-07-01 → 2026-06-15 |
| `campaign_id` | categórica | 0.0 | 175 |  |
| `customer_id` | categórica | 0.0 | 141,819 |  |
| `send_channel` | categórica | 0.0 | 5 | Email 35.8%, SMS 24.57%, WhatsApp 19.92%, Push 16.59%, Voice 3.11% |
| `template_used` | categórica | 10.05 | 875 |  |
| `subject` | categórica | 67.75 | 8 | ¡Oferta especial en Tarjeta Crédito! 35.33%, ¡Oferta especial en Préstamo Personal! 17.08%, ¡Oferta especial en Cuenta Corriente! 14.04%, ¡Oferta especial en Cuenta Ahorro! 11.53%, ¡Oferta especial en Inversión! 6.77%, ¡Oferta especial en nan! 6.6% |
| `send_status` | categórica | 0.0 | 4 | Sent 94.04%, Failed 2.96%, Bounced 1.99%, Blocked 1.02% |
| `was_delivered` | booleano | 0.0 | 2 | True 94.04% |
| `was_opened` | booleano | 27.63 | 2 | True 38.42% |
| `open_date` | fecha | 72.2 | 121,524 | 2023-07-01 → 2026-06-23 |
| `was_clicked` | booleano | 0.0 | 2 | True 5.5% |
| `click_date` | fecha | 94.5 | 24,065 | 2023-07-01 → 2026-06-22 |
| `click_count` | numérico | 94.5 | 5 | 4.0 20.81%, 1.0 20.13%, 5.0 19.75%, 3.0 19.7%, 2.0 19.61%; rango 1 → 5, mediana 3 |
| `had_conversion` | booleano | 0.0 | 2 | True 0.55% |
| `conversion_date` | fecha | 99.45 | 2,391 | 2023-07-03 → 2026-06-23 |
| `conversion_value` | numérico | 99.45 | 2,388 | rango 100.88 → 4999.16, mediana 2524.65 |
| `open_device` | categórica | 75.0 | 3 | Tablet 33.46%, Desktop 33.4%, Mobile 33.14% |
| `open_country` | categórica | 74.99 | 3 | México 49.86%, Colombia 30.13%, Argentina 20.01% |
| `failure_reason` | categórica | 94.34 | 3 | SMTP error 49.63%, Invalid email address 33.36%, User blocked sender 17.01% |
| `send_cost` | numérico | 15.03 | 2,898 | rango 0.0001 → 0.3, mediana 0.0094 |

## complaints

- **Filas:** 67,095 (completa) · **Columnas:** 27
- **Duplicados exactos:** 0 (0.0%) · **PK `complaint_id` repetida:** 0 (0.0%)
- **Llegadas tardías** (`_process_date` > `creation_date`): 0.0% · máx. 0 días · adelantadas: 33.66%
- **FK huérfanas:** `customer_id`→customers: 0 (0.0%), `affected_product_id`→products: 0 (0.0%), `assigned_agent_id`→service_agents: 0 (0.0%), `related_branch_id`→branches: 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `complaint_id` | id | 0.0 | 67,095 |  |
| `creation_date` | fecha | 0.0 | 67,074 | 2023-06-17 → 2026-06-18 |
| `process_date` | fecha | 0.0 | 1,097 | 2023-06-17 → 2026-06-17 |
| `customer_id` | categórica | 0.0 | 54,145 |  |
| `case_type` | categórica | 0.0 | 4 | Complaint 60.29%, Claim 24.74%, Request 10.08%, Suggestion 4.89% |
| `category` | categórica | 0.0 | 5 | Transactions 20.24%, Fees 20.2%, Technical 19.98%, Branch 19.91%, Service 19.66% |
| `subcategory` | categórica | 9.98 | 5 | Cargo no reconocido 20.36%, Cobro indebido 20.19%, Problema con app 20.08%, Atención en sucursal 19.69%, Calidad de servicio 19.68% |
| `reception_channel` | categórica | 0.0 | 6 | Call Center 50.32%, Email 19.86%, Web 14.73%, App 10.03%, Branch 4.0%, Regulator 1.07% |
| `affected_product_id` | id | 33.57 | 42,184 |  |
| `related_branch_id` | categórica | 71.42 | 350 |  |
| `origin_interaction_id` | vacía | 100.0 | 0 |  |
| `description` | categórica | 0.0 | 5 | Queja relacionada con transactions 20.24%, Queja relacionada con fees 20.2%, Queja relacionada con technical 19.98%, Queja relacionada con branch 19.91%, Queja relacionada con service 19.66% |
| `claimed_amount` | numérico | 67.58 | 21,303 | rango 50.27 → 4999.93, mediana 2533.08 |
| `currency` | categórica | 67.54 | 4 | MXN 25.2%, COP 25.06%, USD 24.94%, ARS 24.81% |
| `priority` | categórica | 0.0 | 4 | Medium 49.84%, Low 30.42%, High 14.74%, Critical 5.0% |
| `status` | categórica | 0.0 | 6 | In Process 39.98%, Open 29.99%, Resolved 20.14%, Escalated 4.95%, Closed 3.89%, Rejected 1.05% |
| `assigned_agent_id` | categórica | 34.45 | 1,200 |  |
| `assignment_date` | fecha | 34.47 | 43,955 | 2023-06-17 → 2026-06-19 |
| `first_response_date` | fecha | 39.11 | 40,847 | 2023-06-18 → 2026-06-20 |
| `resolution_date` | fecha | 77.12 | 15,348 | 2023-06-20 → 2026-07-18 |
| `closing_date` | fecha | 96.3 | 2,480 | 2023-06-26 → 2026-07-18 |
| `sla_breached` | booleano | 0.0 | 2 | True 20.11% |
| `resolution_days` | numérico | 77.1 | 30 | rango 1 → 30, mediana 16 |
| `resolution` | texto | 77.18 | 5 | largo prom. 71 car. |
| `compensation_granted` | numérico | 93.08 | 4,436 | rango 10.15 → 499.98, mediana 252.59 |
| `resolution_satisfaction` | numérico | 96.3 | 5 | 5.0 21.18%, 3.0 20.69%, 1.0 20.13%, 2.0 19.0%, 4.0 19.0%; rango 1 → 5, mediana 3 |
| `is_repeat_complainer` | booleano | 0.0 | 2 | True 15.03% |

## customers

- **Filas:** 150,000 (completa) · **Columnas:** 27
- **Duplicados exactos:** 0 (0.0%) · **PK `customer_id` repetida:** 0 (0.0%)
- **FK huérfanas:** `registration_branch_id`→branches: 149,995 (100.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `customer_id` | id | 0.0 | 150,000 |  |
| `document_number` | id | 0.0 | 150,000 |  |
| `document_type` | categórica | 0.0 | 4 | DNI 69.83%, CE 10.1%, Pasaporte 10.04%, CC 10.03% |
| `first_name` | categórica | 0.0 | 7,480 |  |
| `last_name` | categórica | 0.0 | 3,460 |  |
| `date_of_birth` | fecha | 0.0 | 22,956 | 1942-07-07 → 2005-06-21 |
| `gender` | categórica | 0.0 | 3 | F 33.67%, O 33.21%, M 33.12% |
| `email` | categórica | 1.99 | 91,289 |  |
| `mobile_phone` | id | 3.14 | 145,285 |  |
| `landline_phone` | id | 50.04 | 74,942 |  |
| `address` | id | 4.91 | 134,009 |  |
| `city` | categórica | 0.0 | 16 | Guadalajara 8.43%, Ciudad de México 8.34%, Querétaro 8.33%, Tijuana 8.33%, Puebla 8.27%, Monterrey 8.25% |
| `state` | categórica | 0.0 | 16 | Jalisco 8.43%, Ciudad de México 8.34%, Querétaro 8.33%, Baja California 8.33%, Puebla 8.27%, Nuevo León 8.25% |
| `country` | categórica | 0.0 | 3 | México 49.94%, Colombia 30.17%, Argentina 19.89% |
| `postal_code` | categórica | 10.03 | 8,419 | 44100 8.46%, 22000 8.34%, 76000 8.33%, 01000 8.32%, 72000 8.26%, 64000 8.23% |
| `detected_accent` | categórica | 29.88 | 3 | mexican 49.92%, colombian 30.11%, argentine 19.98% |
| `segment` | categórica | 0.0 | 4 | Basic 59.84%, Plus 25.03%, Premium 10.14%, Student 4.99% |
| `credit_score` | numérico | 14.99 | 400 | rango 422 → 850, mediana 631 |
| `estimated_monthly_income` | numérico | 20.02 | 119,734 | rango 5100.28 → 1.11895e+08, mediana 342974 |
| `occupation` | categórica | 10.03 | 20 | Manager 5.08%, Accountant 5.08%, Salesperson 5.06%, Homemaker 5.04%, Entrepreneur 5.04%, Doctor 5.03% |
| `marital_status` | categórica | 7.97 | 4 | Married 25.18%, Divorced 25.16%, Single 24.97%, Widowed 24.68% |
| `education_level` | categórica | 11.97 | 5 | College Prep 29.98%, High School 25.08%, University 24.89%, Graduate 10.05%, Elementary 10.0% |
| `registration_date` | fecha | 0.0 | 149,947 | 2018-06-18 → 2026-06-17 |
| `registration_branch_id` | id | 0.0 | 150,000 |  |
| `customer_status` | categórica | 0.0 | 4 | Active 85.13%, Inactive 9.94%, Suspended 2.94%, Closed 1.99% |
| `last_updated` | fecha | 0.0 | 149,961 | 2018-06-18 → 2027-06-15 |
| `accepts_marketing` | booleano | 0.0 | 2 | True 50.0% |

## daily_exchange_rates

- **Filas:** 13,164 (completa) · **Columnas:** 7
- **Duplicados exactos:** 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `date` | fecha | 0.0 | 1,097 | 2023-06-17 → 2026-06-17 |
| `source_currency` | categórica | 0.0 | 4 | MXN 25.0%, COP 25.0%, ARS 25.0%, USD 25.0% |
| `target_currency` | categórica | 0.0 | 4 | USD 25.0%, MXN 25.0%, COP 25.0%, ARS 25.0% |
| `exchange_rate` | numérico | 0.0 | 9,520 | rango 0.000245 → 4079.94, mediana 5.64513 |
| `buy_rate` | numérico | 0.0 | 9,614 | rango 0.000241 → 4057.98, mediana 5.57108 |
| `sell_rate` | numérico | 0.0 | 9,636 | rango 0.000246 → 4138.59, mediana 5.67493 |
| `source` | categórica | 0.0 | 4 | Bloomberg 25.09%, Reuters 25.05%, Internal 25.02%, Central Bank 24.85% |

## digital_events

- **Filas:** 15,620,994 (muestra de 555,907 filas (1 de cada 32 días, 35 días)) · **Columnas:** 26
- **Duplicados exactos:** 0 (0.0%) · **PK `event_id` repetida:** 0 (0.0%)
- **Llegadas tardías** (`_process_date` > `event_date`): 0.0% · máx. 0 días · adelantadas: 25.1%
- **FK huérfanas:** `customer_id`→customers: 0 (0.0%), `product_id`→products: 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `event_id` | id | 0.0 | 555,907 |  |
| `event_date` | fecha | 0.0 | 504,120 | 2023-06-17 → 2026-06-10 |
| `process_date` | fecha | 0.0 | 35 | 2023-06-17 → 2026-06-09 |
| `customer_id` | categórica | 24.27 | 44,217 |  |
| `session_id` | categórica | 0.0 | 65,534 |  |
| `event_type` | categórica | 0.0 | 7 | PageView 38.2%, Click 22.88%, Logout 15.63%, Login 15.62%, FormSubmit 3.79%, Error 2.34% |
| `event_category` | categórica | 0.0 | 4 | Authentication 31.25%, Navigation 25.35%, Product 24.15%, Transaction 19.25% |
| `channel` | categórica | 0.0 | 4 | Android App 35.1%, iOS App 24.93%, Desktop Web 20.1%, Mobile Web 19.87% |
| `platform` | categórica | 5.02 | 5 | Android 44.91%, iOS 34.99%, Linux 6.97%, MacOS 6.63%, Windows 6.51% |
| `browser` | categórica | 62.05 | 5 | Safari 29.15%, Chrome 28.98%, Samsung Internet 16.68%, Firefox 12.68%, Edge 12.51% |
| `app_version` | categórica | 42.93 | 500 | 4.6.6 0.3%, 5.5.4 0.27%, 3.2.8 0.27%, 2.1.4 0.26%, 4.8.7 0.26%, 1.7.0 0.26% |
| `page_url` | categórica | 4.95 | 12 | /logout 15.66%, /login 15.59%, /products/loans 8.06%, /products/credit-card 8.06%, /products/savings 8.02%, /payments 6.43% |
| `page_title` | categórica | 5.03 | 12 | Cerrar Sesión 15.64%, Iniciar Sesión 15.58%, Tarjeta de Crédito 8.07%, Préstamos 8.05%, Cuenta de Ahorro 8.03%, Pagar Servicios 6.44% |
| `action` | categórica | 9.93 | 10 | view_product 24.14%, logout 15.68%, login 15.6%, initiate_payment 6.44%, view_transactions 6.41%, initiate_transfer 6.4% |
| `element_id` | categórica | 15.01 | 12 |  |
| `product_id` | id | 90.78 | 48,106 |  |
| `event_value` | numérico | 94.91 | 27,497 | rango 10.2 → 4999.52, mediana 2508.38 |
| `duration_seconds` | numérico | 63.7 | 296 | rango 5 → 300, mediana 153 |
| `ip_address` | categórica | 5.03 | 65,525 |  |
| `ip_country` | categórica | 0.0 | 4 | México 40.1%, Colombia 30.7%, Argentina 22.39%, Mexico 6.82% |
| `ip_city` | categórica | 28.28 | 16 | Querétaro 8.73%, Guadalajara 8.58%, Puebla 8.41%, Tijuana 8.27%, Monterrey 8.21%, Ciudad de México 8.08% |
| `is_mobile` | booleano | 0.0 | 2 | True 79.9% |
| `referrer` | categórica | 93.28 | 4 | https://www.instagram.com 25.11%, https://email.marketing.com 25.07%, https://www.facebook.com 24.92%, https://www.google.com 24.91% |
| `utm_source` | categórica | 94.64 | 4 | direct 25.37%, google 25.29%, email 24.68%, facebook 24.67% |
| `utm_medium` | categórica | 94.63 | 4 | organic 25.26%, cpc 25.03%, social 24.99%, email 24.72% |
| `utm_campaign` | categórica | 94.64 | 3 | spring_promo 33.61%, new_users 33.35%, retention 33.04% |

## marketing_campaigns

- **Filas:** 200 (completa) · **Columnas:** 13
- **Duplicados exactos:** 0 (0.0%) · **PK `campaign_id` repetida:** 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `campaign_id` | id | 0.0 | 200 |  |
| `campaign_name` | id | 0.0 | 200 |  |
| `description` | categórica | 19.5 | 37 | Campaña de retention para Tarjeta Crédito 9.94%, Campaña de acquisition para Tarjeta Crédito 6.83%, Campaña de cross-sell para Tarjeta Crédito 6.83%, Campaña de retention para Cuenta Ahorro 6.21%, Campaña de cross-sell para Cuenta Ahorro 6.21%, Campaña de up-sell para Préstamo Personal 4.35% |
| `campaign_type` | categórica | 0.0 | 6 | Email 33.5%, SMS 21.0%, WhatsApp 18.5%, Push 12.0%, Mix 10.5%, Voice 4.5% |
| `campaign_objective` | categórica | 0.0 | 5 | Retention 31.0%, Cross-sell 25.5%, Acquisition 19.0%, Reactivation 12.5%, Up-sell 12.0% |
| `promoted_product` | categórica | 11.0 | 7 | Tarjeta Crédito 29.21%, Cuenta Ahorro 22.47%, Préstamo Personal 15.73%, Inversión 11.24%, Cuenta Corriente 9.55%, Préstamo Hipotecario 6.18% |
| `target_segment` | categórica | 39.5 | 4 | Plus 26.45%, Premium 26.45%, Basic 24.79%, Student 22.31% |
| `target_country` | categórica | 55.5 | 3 | Colombia 37.08%, Mexico 31.46%, Argentina 31.46% |
| `start_date` | fecha | 0.0 | 186 | 2023-07-01 → 2026-06-14 |
| `end_date` | fecha | 0.0 | 180 | 2023-08-11 → 2026-08-20 |
| `budget` | numérico | 15.5 | 169 | rango 6355.31 → 499954, mediana 272364 |
| `campaign_status` | categórica | 0.0 | 3 | Completed 86.0%, Paused 12.5%, Active 1.5% |
| `expected_conversion_rate` | numérico | 7.0 | 176 | rango 0.55 → 14.69, mediana 7.53 |

## products

- **Filas:** 400,000 (completa) · **Columnas:** 17
- **Duplicados exactos:** 0 (0.0%) · **PK `product_id` repetida:** 0 (0.0%)
- **FK huérfanas:** `customer_id`→customers: 0 (0.0%), `opening_branch_id`→branches: 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `product_id` | id | 0.0 | 400,000 |  |
| `customer_id` | categórica | 0.0 | 139,578 |  |
| `product_type` | categórica | 0.0 | 8 | Cuenta Ahorro 30.05%, Tarjeta Crédito 25.03%, Cuenta Corriente 24.99%, Tarjeta Débito 9.98%, Préstamo Personal 4.99%, Préstamo Hipotecario 2.98% |
| `product_number` | id | 0.0 | 399,994 |  |
| `currency` | categórica | 0.0 | 3 | USD 55.13%, COP 26.99%, ARS 17.88% |
| `current_balance` | numérico | 0.0 | 359,956 | rango 0 → 8.81512e+08, mediana 7511.25 |
| `credit_limit` | numérico | 68.67 | 124,986 | rango 1000.33 → 5.99984e+08, mediana 93046.1 |
| `interest_rate` | numérico | 10.02 | 4,443 | rango 0 → 45, mediana 2.2 |
| `opening_date` | fecha | 0.0 | 2,922 | 2018-06-18 → 2026-06-17 |
| `expiration_date` | fecha | 66.71 | 3,652 | 2021-06-17 → 2031-06-16 |
| `opening_branch_id` | categórica | 0.0 | 350 |  |
| `product_status` | categórica | 0.0 | 4 | Active 84.99%, Closed 8.01%, Blocked 4.98%, Suspended 2.02% |
| `opening_channel` | categórica | 0.0 | 4 | Branch 49.96%, Web 25.07%, App 19.93%, Call Center 5.04% |
| `has_linked_app` | booleano | 0.0 | 2 | True 49.96% |
| `days_past_due` | numérico | 68.66 | 7 | 0.0 85.03%, 90.0 2.58%, 30.0 2.49%, 15.0 2.48%, 60.0 2.48%, 180.0 2.48%; rango 0 → 180, mediana 0 |
| `last_transaction_date` | fecha | 23.57 | 305,391 | 2018-06-21 → 2026-06-17 |
| `last_updated` | fecha | 0.0 | 399,686 | 2018-06-20 → 2027-06-15 |

## satisfaction_surveys

- **Filas:** 212,759 (completa) · **Columnas:** 20
- **Duplicados exactos:** 0 (0.0%) · **PK `survey_id` repetida:** 0 (0.0%)
- **Llegadas tardías** (`_process_date` > `survey_date`): 0.0% · máx. 0 días · adelantadas: 79.48%
- **FK huérfanas:** `customer_id`→customers: 0 (0.0%), `agent_id`→service_agents: 0 (0.0%), `interaction_id`→call_center_interactions: 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `survey_id` | id | 0.0 | 212,759 |  |
| `survey_date` | fecha | 0.0 | 212,505 | 2023-06-17 → 2026-06-19 |
| `process_date` | fecha | 0.0 | 1,097 | 2023-06-17 → 2026-06-17 |
| `interaction_id` | id | 0.0 | 212,759 |  |
| `customer_id` | categórica | 0.0 | 113,640 |  |
| `agent_id` | categórica | 0.0 | 1,090 |  |
| `survey_type` | categórica | 0.0 | 3 | CSAT 60.09%, NPS 29.92%, CES 9.98% |
| `send_channel` | categórica | 0.0 | 5 | Email 39.89%, SMS 29.99%, App 20.02%, IVR 5.09%, Web 5.01% |
| `main_score` | numérico | 0.0 | 7 | 3 42.47%, 2 21.81%, 4 10.25%, 5 7.74%, 6 7.67%, 7 7.63%; rango 1 → 7, mediana 3 |
| `nps_category` | categórica | 71.61 | 2 | Detractor 74.52%, Passive 25.48% |
| `question_1_text` | categórica | 42.99 | 3 | ¿Cómo calificaría la atención brindada? 33.53%, ¿El agente resolvió su consulta satisfactoriamente? 33.38%, ¿Qué tan satisfecho está con el servicio recibido? 33.1% |
| `question_1_response` | numérico | 42.95 | 5 | 5.0 20.25%, 3.0 20.03%, 2.0 19.95%, 4.0 19.95%, 1.0 19.82%; rango 1 → 5, mediana 3 |
| `question_2_text` | categórica | 61.75 | 1 | ¿El tiempo de espera fue aceptable? 100.0% |
| `question_2_response` | numérico | 61.7 | 5 | 3.0 20.33%, 4.0 20.21%, 2.0 19.9%, 5.0 19.87%, 1.0 19.69%; rango 1 → 5, mediana 3 |
| `question_3_text` | categórica | 81.13 | 1 | ¿Volvería a contactarnos por este canal? 100.0% |
| `question_3_response` | numérico | 81.15 | 5 | 1.0 20.39%, 4.0 20.19%, 3.0 20.02%, 2.0 19.83%, 5.0 19.56%; rango 1 → 5, mediana 3 |
| `open_comments` | categórica | 52.44 | 13 | Tardaron mucho en atenderme. 13.46%, No resolvieron mi problema completamente. 13.39%, Tuve que esperar demasiado tiempo. 13.34%, No estoy satisfecho con la solución. 13.31%, El agente no fue muy claro en sus explicaciones. 13.18%, Normal, sin problemas mayores. 8.87% |
| `comment_sentiment` | categórica | 52.41 | 3 | Negative 66.69%, Neutral 26.44%, Positive 6.87% |
| `response_time_hours` | numérico | 0.0 | 3,473 | rango 1.01 → 35.97, mediana 18.46 |
| `campaign_response_rate` | numérico | 15.06 | 3,001 | rango 15 → 45, mediana 29.95 |

## service_agents

- **Filas:** 1,200 (completa) · **Columnas:** 18
- **Duplicados exactos:** 0 (0.0%) · **PK `agent_id` repetida:** 0 (0.0%)
- **FK huérfanas:** `assigned_branch_id`→branches: 831 (99.76%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `agent_id` | id | 0.0 | 1,200 |  |
| `employee_code` | id | 0.0 | 1,187 |  |
| `first_name` | categórica | 0.0 | 444 |  |
| `last_name` | categórica | 0.0 | 975 |  |
| `email` | id | 0.0 | 1,188 |  |
| `phone` | id | 5.75 | 1,131 |  |
| `native_accent` | categórica | 0.0 | 3 | mexican 50.0%, colombian 30.0%, argentine 20.0% |
| `country_of_origin` | categórica | 0.0 | 3 | Mexico 50.0%, Colombia 30.0%, Argentina 20.0% |
| `assigned_branch_id` | id | 30.58 | 833 |  |
| `agent_type` | categórica | 0.0 | 4 | Phone 49.0%, Digital 20.92%, In-Person 19.17%, Hybrid 10.92% |
| `experience_level` | categórica | 0.0 | 4 | Specialist 63.42%, Senior 23.83%, Mid-Senior 11.25%, Junior 1.5% |
| `languages` | categórica | 0.0 | 4 | español 54.08%, español, inglés 35.17%, español, portugués 5.67%, español, inglés, portugués 5.08% |
| `specialty` | categórica | 39.67 | 8 | Fraudes 14.5%, Cobranza 13.4%, Retención 13.26%, Soporte Técnico 13.26%, Créditos 12.02%, Inversiones 11.46% |
| `hire_date` | fecha | 0.0 | 1,055 | 2013-06-20 → 2026-03-17 |
| `avg_csat` | numérico | 11.17 | 149 | rango 3.5 → 5, mediana 4.27 |
| `total_monthly_interactions` | numérico | 9.25 | 559 | rango 100 → 800, mediana 452 |
| `agent_status` | categórica | 0.0 | 4 | Active 90.83%, Vacation 5.17%, Leave 2.42%, Inactive 1.58% |
| `work_shift` | categórica | 0.0 | 4 | Afternoon 34.83%, Morning 33.17%, Rotating 16.92%, Night 15.08% |

## transactions

- **Filas:** 4,425,008 (muestra de 495,197 filas (1 de cada 9 días, 122 días)) · **Columnas:** 22
- **Duplicados exactos:** 0 (0.0%) · **PK `transaction_id` repetida:** 0 (0.0%)
- **Llegadas tardías** (`_process_date` > `transaction_date`): 0.0% · máx. 0 días · adelantadas: 25.05%
- **FK huérfanas:** `customer_id`→customers: 0 (0.0%), `product_id`→products: 0 (0.0%), `branch_id`→branches: 0 (0.0%)

| Columna | Tipo | Nulos % | Distintos | Detalle |
|---|---|---|---|---|
| `transaction_id` | id | 0.0 | 495,197 |  |
| `transaction_date` | fecha | 0.0 | 483,263 | 2023-06-17 → 2026-06-11 |
| `process_date` | fecha | 0.0 | 122 | 2023-06-17 → 2026-06-10 |
| `product_id` | categórica | 0.0 | 260,612 |  |
| `customer_id` | categórica | 0.0 | 123,628 |  |
| `transaction_type` | categórica | 0.0 | 6 | Purchase 24.46%, Withdrawal 21.8%, Transfer 20.22%, Payment 16.69%, Deposit 13.8%, Adjustment 3.02% |
| `transaction_category` | categórica | 60.88 | 6 | Food 24.66%, Services 20.08%, Transport 15.17%, Other 15.12%, Entertainment 14.93%, Health 10.05% |
| `amount` | numérico | 0.0 | 381,906 | rango 5 → 3.99919e+07, mediana 5394.11 |
| `currency` | categórica | 0.0 | 3 | USD 55.08%, COP 27.02%, ARS 17.9% |
| `amount_usd` | numérico | 57.33 | 133,691 | rango 5 → 9999.74, mediana 467.03 |
| `channel` | categórica | 0.0 | 6 | POS 35.03%, ATM 29.94%, Web 15.05%, App 14.98%, Branch 3.0%, Transfer 1.99% |
| `branch_id` | categórica | 68.69 | 350 |  |
| `merchant_name` | categórica | 76.75 | 24 | Restaurante El Buen Sabor 6.22%, Super Ahorro 6.17%, Tienda Don José 6.16%, Mercado Central 6.11%, Cable TV 5.07%, Empresa Telefónica 5.04% |
| `merchant_category` | categórica | 76.78 | 6 | Food 24.64%, Services 19.98%, Transport 15.31%, Other 15.11%, Entertainment 14.9%, Health 10.06% |
| `transaction_country` | categórica | 0.0 | 7 | México 47.58%, Colombia 29.17%, Argentina 19.59%, Brazil 0.93%, USA 0.92%, Mexico 0.91% |
| `transaction_city` | categórica | 10.03 | 28 | Monterrey 8.25%, Guadalajara 8.19%, Puebla 8.15%, Ciudad de México 8.06%, Tijuana 7.93%, Querétaro 7.93% |
| `transaction_status` | categórica | 0.0 | 4 | Approved 91.97%, Declined 5.0%, Pending 1.99%, Reversed 1.04% |
| `response_code` | numérico | 4.98 | 5 | 00 91.98%, 14 2.01%, 54 2.01%, 51 2.01%, 05 1.99%; rango 0 → 54, mediana 0 |
| `is_fraud` | booleano | 0.0 | 2 | True 0.1% |
| `fraud_score` | numérico | 19.96 | 3,260 | rango 0 → 99.99, mediana 14.98 |
| `latitude` | numérico | 80.65 | 95,718 | rango -35.6036 → 5.71095, mediana 0.20983 |
| `longitude` | numérico | 80.66 | 95,702 | rango -75.072 → 0.999893, mediana -0.99952 |
