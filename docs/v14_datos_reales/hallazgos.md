# Hallazgos del dataset LATAM Bank

Resumen de tres pasadas de análisis:
1. Perfil y señal por tabla: [data_profile.md](data_profile.md), [signal_checks.md](signal_checks.md).
2. Cruces entre tablas completas con ventanas de tiempo: [deep_signal.md](deep_signal.md).
3. 8 agentes exploradores con verificación adversaria: cada hallazgo lo reprodujo un agente y otro
   intentó refutarlo. Detalle en [hallazgos_detalle.md](hallazgos_detalle.md): 38 confirmados, 17
   hipótesis descartadas y 9 sin verificar por falta de cuota de uso (sobre todo la decodificación
   del generador aleatorio, "semilla 42", que queda como hipótesis).

**Conclusión general:** el dataset es sintético y casi todo se generó como **sorteos
independientes más unas pocas reglas fijas**. Hay muy poca señal predictiva real, pero las reglas son
exactas y útiles, y la mayoría de los defectos de calidad se pueden detectar. Sirve para (a) medir
la demanda y armar el baseline del call center, (b) que el agente consulte datos coherentes y
(c) demostrar data engineering. No sirve para entrenar modelos sobre texto, crédito ni fuga.

## 1. Call center (686,296 contactos, 3 años)

| Motivo | % contactos | Resuelto al 1er contacto | Duración mediana | CSAT medio |
|---|---|---|---|---|
| Transaccional | 35.0% | 91.5% | 205 s | 2.91 |
| Producto | 22.0% | 89.6% | 263 s | 2.90 |
| Queja | 17.1% | 43.6% | 431 s | 2.43 |
| Técnico | 15.0% | 69.9% | 360 s | 2.70 |
| Comercial | 8.0% | 65.2% | 540 s | 2.66 |
| Retención | 3.0% | 60.2% | 478 s | 2.61 |

- **Demanda:** lunes a viernes igual (~730 contactos/día) y fin de semana exactamente la mitad
  (~367). Las horas del día y los meses son planos, sin estacionalidad, tendencia ni feriados. El
  ruido diario es un multiplicador aleatorio U(0.8, 1.2) independiente entre tablas. Ojo: si se
  agrupa por la fecha de `ts` en vez de `process_date`, aparece un falso "lunes bajo".
- **Canal:** teléfono 85%, email 4%, app 3.8%, WhatsApp 3.3%, web chat 3.3%. La espera es de ~120 s
  en todo.
- **Resolución (FCR 76.6%)** depende **solo del motivo** (AUC 0.76). El sentimiento parece influir
  (neutral 82.6% vs ~64%), pero es una confusión: el 100% de los contactos Transaccionales, que son
  los que más se resuelven, vienen marcados "Neutral". Dentro de cada motivo, el FCR es igual con
  cualquier sentimiento (Queja 43.7% vs 43.6%). Además, **no resolver no genera recontactos**
  (2.87% en ambos casos): es una etiqueta sin consecuencias en los datos.
- `requires_followup` = siempre verdadero si no se resolvió, y 15% al azar si se resolvió.
- **Satisfacción, regla exacta:**
  - CSAT y CES = 2 + (1 si se resolvió) + ruido de −1/0/+1 (15% / 70% / 15%).
  - NPS: se sortea entre 2, 3 y 4 si no se resolvió y entre 5, 6 y 7 si se resolvió. Nunca hay
    promotores, así que el NPS estándar es −74.5, sin importar lo bien que se atienda.
  - Nada más mueve el puntaje: ni la duración, ni la espera, ni el sentimiento.
- **Sin efecto:** escalamiento (10% plano), agente (varianza igual al azar), especialidad del agente
  vs motivo, coincidencia de acento, experiencia del agente, ningún rasgo del cliente (sus contactos
  de un período no predicen los del otro).
- **Transcripts:** son plantillas: 2 aperturas ("consultar saldo") + 4 cierres, repartidas igual
  en todos los motivos. `detected_intents` = "consulta_general" en el 100%, y `main_topics` copia la
  categoría. **No sirven para entrenar un clasificador de intención.**
- **Costo estimado** con el supuesto de 0.15 USD por minuto de agente (ver `gold.cc_category_baseline`):
  Transaccional ≈ 133k USD y Queja ≈ 127k USD en 3 años. Transaccional es el motivo con más volumen y
  más fácil de automatizar.

## 2. Transacciones (4,425,008)

Reglas del generador (confirmadas por 2 verificadores y convertidas en contratos de datos):
- **Fecha contable:** `process_date` = fecha de (`ts` − 6 h); en call center y reclamos es − 8 h.
  Usar `process_date` como "fecha del movimiento".
- **Volumen:** cada producto **activo** recibe ~13 transacciones en 3 años (Poisson) y los productos
  no activos, 0. Nada más explica el volumen: ni segmento, ingreso, edad ni país.
- **Mezcla y secuencia:** el tipo de producto fija la mezcla de tipos de transacción. La secuencia es
  aleatoria: no hay ciclos de nómina, suscripciones, reintentos tras un rechazo, cargos duplicados ni
  reversas emparejadas con su cargo original.
- **Montos:** uniformes dentro de un rango fijo por tipo de transacción (en USD-equivalente) y
  estacionarios: no hay inflación en ARS. `amount_usd` = monto / tasa **fija** (COP 4000, ARS 350),
  no la tabla fx diaria; es nulo en todo USD + 5% al azar.
- **Moneda:** es un atributo del producto. México 100% USD (no existe MXN); Colombia y Argentina ~90%
  moneda local y ~10% USD.
- **Estado y código:** sorteo independiente de todo. `00` ⇔ aprobada. Pending nunca se resuelve y
  Reversed no tiene transacción original. Los códigos contradicen su significado ISO: hay "tarjeta
  vencida" en productos sin tarjeta y "fondos insuficientes" en depósitos.
- **Fraude (0.1%, sorteo independiente):** `fraud_score > 30` ⇒ fraude en el 100% de los casos y
  cubre el 55% de los fraudes. Con score nulo o ≤ 30 no hay información: ninguna de las 44 variables
  probadas recupera el 45% restante (AUC 0.50). **El autorizador aprueba el 92.5% del fraude
  seguro** y el fraude no deja rastro después: sin bloqueo, reclamo ni contacto.
- **Comercios:** 24 comercios, cada uno con una categoría fija (sirve para imputar nulos).
- **Geografía inútil:** lat/lon = centro del país del cliente ± 1° (México cae en (0,0)) y el
  `branch_id` de la transacción es una sucursal al azar del país.

## 3. Cruces entre tablas

- Encuestas y transcripts enlazan al 100% con el call center (mismo cliente y agente).
- **Nada más se conecta:** los contactos, reclamos y eventos digitales no ocurren cerca de las
  transacciones del cliente (igual que el azar en ventanas de 1 h, 24 h, 7 y 30 días). Un rechazo o un
  fraude no genera llamadas ni reclamos.
- Todo ID de producto citado fuera de products/transactions pertenece a **otro** cliente o no existe:
  - `mentioned_products` del call center: solo el 0.65% existe;
  - `affected_product_id` de los reclamos: es un producto al azar;
  - `product_id` de eventos digitales: nunca es del cliente.
- **Búsqueda con fuerza bruta** (~60 objetivos, 253 features por cliente de 8 tablas, held-out por
  cliente): ningún objetivo de negocio es predecible.
  - Crédito: la mora no se explica, AUC 0.508 con 32 features. Fuga de clientes: 0.502.
    Demografía, estados y reclamos: ~0.50.
  - Lo único "predecible" es por exposición: un cliente con más transacciones tiene más
    probabilidad de sufrir un fraude, a la misma tasa por transacción para todos.
  - El segmento se deduce del ingreso y del credit score.
- **Campañas:** embudo fijo por canal. Entrega 94%; apertura SMS 50%, Push 40% y Email 30%; clic
  20% de las aperturas; conversión 10% de los clics. WhatsApp y Voice no registran apertura. Ni el
  segmento, ni el consentimiento, ni la campaña cambian las tasas.

## 4. Calidad de datos (ver [dq_report.md](dq_report.md))

Diferencias con la documentación:
- 23.5M filas en vez de 19M (`digital_events` tiene 15.6M en vez de 10M).
- 0 duplicados y 0 llegadas tardías, aunque se anunciaban.
- Las categorías vienen en español.

Defectos:
- Transacciones fuera del ciclo de vida: 18.7% antes de abrir el producto, 10.4% del total con
  tarjeta vencida (31.5% de las transacciones de productos con vencimiento) y 11.9% de clientes
  inactivos o cerrados.
- Identidad: email compartido entre clientes (53%), todos los clientes mexicanos con teléfono +54
  y documento "DNI" en México. Solo `document_number` es único.
- Llaves rotas: sucursal de registro de clientes (100%), sucursal de agentes (69%) y contacto de
  origen de reclamos (100% nulo).
- Montos de reclamos de 50 a 5,000 en cualquier moneda.
- Otros:
  - `agent_text` con `{monto} {moneda}` sin llenar;
  - asuntos de campaña "¡Oferta especial en nan!";
  - 50% de envíos de campañas sin consentimiento de marketing;
  - 6% de `last_updated` en el futuro;
  - 14% de productos vencidos pero activos.

## 5. Qué implica para la solución (LLM + herramientas + reglas)

- **El LLM entiende y redacta en español y portugués; las herramientas consultan gold; las reglas
  en código deciden.**
  - `customer_profile` y `customer_cards` → contexto y estado de tarjetas por últimos 4 dígitos.
  - `transactions_enriched` → explicar un rechazo con `status_message_es`. Si
    `explanation_reliable = false`, no afirmar el motivo y derivar.
  - `requires_fraud_review` (`fraud_score > 30`) → derivar a Fraudes y ofrecer bloqueo, porque el
    autorizador no lo hace.
  - Verificar que el producto sea del cliente autenticado antes de mostrarlo: los datos mismos traen
    referencias cruzadas a productos ajenos.
- **Autenticación:** usar documento + sesión de prueba (`document_hash`), nunca email ni teléfono,
  porque no son únicos.
- **Componente aprendido:** el texto del dataset no sirve. El clasificador de intención y el
  enrutamiento deben entrenarse y evaluarse con un set **generado por el equipo** en ES y PT,
  rotulado como sintético, comparado contra un baseline de palabras clave.
- **Baseline de negocio:** `gold.cc_category_baseline` (FCR, duración, CSAT y costo por motivo) y
  `gold.cc_weekday_profile` para capacidad.
- **Derivación a humano:** `gold.agents_routing`, con 129 agentes que hablan portugués y
  especialidades (Fraudes, Quejas…). Solo atienden los agentes activos.
- **Flujo mejor respaldado:** soporte de tarjetas y transacciones. Tiene los datos más coherentes, es
  el motivo más grande (35%) y el más corto, y tiene una regla de fraude medible. Crédito queda
  descartado: no hay señal de riesgo.
