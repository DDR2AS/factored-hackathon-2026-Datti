# Tratamiento de datos

Flujo: **S3 (CSV diarios)** → extract → `data/raw` → **bronze** → **silver** → **quality** → **gold** → BigQuery.
Todo se corre con `python -m pipeline.run` (ver README). Motor local: DuckDB (`data/warehouse.duckdb`).

## Bronze: copia fiel

- Cada tabla se convierte a un Parquet con **todas las columnas como texto**. Así no se pierde ningún
  valor inválido; los tipos se aplican en silver, donde se puede medir qué falló.
- Contrato de esquema: todos los archivos diarios de una tabla deben tener la misma cabecera; si
  cambia, el pipeline falla. Hoy los 1,097 archivos de cada tabla son consistentes.
- Los CSV traen BOM (`﻿`) que ensucia el nombre de la primera columna; se descarta.
- Linaje en cada fila: `_source_file` (CSV de origen), `_process_date` (partición) e `_ingested_at`.
- Manifest en `data/bronze/_manifest.json` (filas, archivos, fecha de carga). Solo se reconstruye
  lo que cambió.

## Silver: tipado, limpio y con banderas

Reglas generales:
- **Tipos** con `TRY_CAST`: un valor inválido queda NULL y lo detecta el contrato.
- **Deduplicación** por llave primaria (se conserva el registro más reciente). Hoy hay 0
  duplicados, aunque la documentación decía ~2%. La lógica queda por si llegan repetidos.
- **Normalización**: 'Mexico' → 'México', 'True'/'False' → booleano, emails en minúscula.
- **Los defectos no se borran**: se marcan con columnas `dq_*`, para que el análisis y las
  herramientas del agente decidan qué hacer con ellos.
- **Fechas**: `process_date` es la fecha contable. En transacciones el día cierra a las 06:00 y en
  call center y reclamos a las 08:00. Para agrupar por día hay que usar `process_date`: con la fecha
  de `ts`, el 25% de las transacciones y el 33% de los contactos caen en otro día.

| Tabla | Tratamiento principal (banderas con % de filas afectadas) |
|---|---|
| customers | Ingreso local → USD con tasas fijas (MXN 17, COP 4000, ARS 350; con eso los 3 países quedan en ~2.3k USD de mediana). `document_hash` (sha256) para identidad simulada, `age`. Banderas: tipo de documento inválido para el país (50%: México usa DNI), email compartido (53%), sucursal de registro inexistente (100%), `last_updated` en el futuro (6%). |
| products | `is_card`, `is_credit`, `card_last4`, `credit_utilization`, `is_expired`. Banderas: vencido pero activo (14%), número de producto duplicado (12 filas). |
| transactions | `amount_usd` recalculado con las tasas fijas del dataset (el original está vacío en 57%); categoría de comercio imputada por comercio (1.2%); `response_description` (ISO 8583 en español); `fraud_risk_tier`; tipo de producto y últimos 4 dígitos. Banderas: antes de abrir el producto (18.7%), antes del registro del cliente (18.8%), cliente inactivo/cerrado (11.9%), tarjeta vencida (10.4%), código nulo (5%), país distinto al del cliente (4.6%), código de rechazo en Pending/Reversed (2.9%), semántica de código imposible (2.7%). |
| call_center_interactions | Una sola columna `category` (`contact_reason` es idéntico), `weekday_num`. Banderas: duración faltante (14%), espera faltante (30%), productos mencionados que no son del cliente (40%). |
| call_transcripts | Tipado. Bandera: texto del agente con `{monto} {moneda}` sin reemplazar (100%). |
| satisfaction_surveys | Tipado y coherencia con el contacto (cliente 100% coincide). Bandera: categoría NPS incoherente con el puntaje (1.5%). |
| complaints | Subcategoría imputada (función 1:1 de la categoría, 10%), `claimed_amount_usd`, `is_open`. Banderas: producto afectado de OTRO cliente (100% de los que tienen producto), sin contacto de origen (100%), monto sin escala de moneda en COP/ARS (16%). |
| service_agents | `speaks_portuguese`, `speaks_english`, `is_active`. Banderas: sucursal inexistente (69%), código de empleado y email duplicados (~2%). |
| campaign_sends | Banderas: envío sin consentimiento de marketing (50%), fuera del segmento objetivo (44%), asunto "¡Oferta especial en nan!" (2%). |
| digital_events | País de IP normalizado. Banderas: evento sin cliente (24%), producto de otro cliente (9%). |
| branches, exchange_rates, marketing_campaigns | Tipado y país normalizado. Banderas: coordenadas en (0,0) (48% de sucursales), spread compra/venta, fechas de campaña. |

## Calidad: contratos de datos

`contracts/silver.yml` define por tabla: llave primaria, únicos, filas mínimas, no nulos,
**valores permitidos** (si aparece una categoría nueva, el contrato lo detecta: sirve como
control de evolución de esquema), rangos, llaves foráneas, reglas de negocio y frescura.
`pipeline/quality.py` corre los 194 checks y guarda el resultado en `quality.dq_results`,
`gold.dq_scorecard` y [dq_report.md](dq_report.md).

- `error` = el contrato se rompe y el pipeline se detiene antes de gold. Hoy: **0 fallan**.
- `warn` = defecto conocido del dataset, documentado. Hoy: **35**.
- Reglas del generador convertidas en contrato estricto, que se cumplen al 100%:
  - corte contable a las 06:00 (transacciones) y 08:00 (call center y reclamos);
  - código `00` ⇔ Approved;
  - `fraud_score > 30` ⇒ fraude;
  - la transacción es sobre un producto del mismo cliente y con su misma moneda;
  - puntajes de encuesta dentro de su escala;
  - la encuesta corresponde al mismo cliente del contacto.

## Gold: tablas de consumo

| Tabla | Uso |
|---|---|
| `cc_demand_daily` | Demanda diaria del call center por país, canal y motivo |
| `cc_weekday_profile` | Patrón semanal por motivo |
| `cc_category_baseline` | Baseline por motivo: volumen, FCR, escalamiento, duración, CSAT y costo estimado (supuesto: 0.15 USD por minuto de agente) |
| `customer_profile` | Contexto del cliente para el agente y el handoff, sin datos personales en claro |
| `customer_cards` | Estado de cada tarjeta, identificada por sus últimos 4 dígitos |
| `transactions_enriched` | Transacciones con el motivo en español, `explanation_reliable` (si es falso, no afirmar el motivo y derivar) y `requires_fraud_review` |
| `complaints_baseline` | Baseline de reclamos: SLA, días de resolución, compensaciones |
| `agents_routing` | Enrutamiento del handoff (portugués, especialidad, disponibilidad) |
| `fraud_rule_eval` | Precisión y cobertura de la regla de fraude |
| `dq_scorecard` | Último resultado de calidad |

## Política de actualización

- Los datos llegan como particiones diarias. La extracción es incremental (`aws s3 sync` solo
  baja lo nuevo) y bronze solo reconstruye las tablas cuyos CSV cambiaron.
- Silver y gold se reconstruyen completas en ~5 minutos. El resultado es determinista, así que
  correrlo dos veces da lo mismo (idempotente).
- Llegadas tardías: un archivo de un día anterior se baja en el siguiente sync y entra en la
  reconstrucción. La deduplicación por llave evita contar dos veces un registro repetido.
- Frescura: check de fecha máxima vs `SNAPSHOT_DATE` (2026-06-17), el último día del dataset.
- Pendiente para demostrarlo: el bucket tiene `data_backup_20260831/`, una versión anterior del
  dataset. Cargarla primero y luego la actual debería dar lo mismo que cargar la actual desde cero.

## BigQuery

- Proyecto `hackathon-datos-factored`, datasets `bronze`, `silver` y `gold` (sandbox: gratis, las
  tablas expiran a los 60 días, no permite UPDATE ni MERGE).
- Clustering por fecha en lugar de partición: en el sandbox las particiones con más de 60 días se
  borran al cargar, y el dataset es de 2023 a 2026.
- `digital_events` solo en local (sin señal y 5 GB). `gold.transactions_enriched` es una vista.
- Uso actual: 9.56 GB de 10 GB.

## Privacidad

- El repo es público: no incluye datos ni credenciales. El bucket va en `.env`, que no se versiona.
  Los reportes solo tienen agregados (`analysis/sanitize_docs.py` enmascara IDs y emails).
- Gold minimiza datos personales: no incluye el documento en claro, email, teléfono ni dirección.
