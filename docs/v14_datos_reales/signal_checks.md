# Pruebas de señal: LATAM Bank

> **Nota:** reporte de una pasada anterior. Algunas conclusiones se corrigieron después (ver [hallazgos.md](hallazgos.md)): el patrón semanal real se ve agrupando por `process_date` (lunes a viernes iguales y fin de semana = 50%), y el efecto del sentimiento sobre el FCR es una confusión con el motivo.


Generado por `analysis/signal_checks.py`. AUC ≈ 0.5 = sin señal; 1.0 = determinista (posible fuga).

## 1. Transcripts → motivo de contacto (¿sirve para un clasificador de intención?)

- Transcripts: 171,321 · textos de cliente únicos: 42 · de agente: 42
- Pureza customer_text → reason_category: 34.90%
- main_topics == reason_category: 100.00% (fuga si se usa como feature)
- detected_intents valores: {'consulta_general': 162864, nan: 8457}
- Aperturas distintas: 2 · frases de cierre distintas: 5: ['Entiendo, muchas gracias.', 'Muy bien, ¿hay algo más que deba saber?', 'Perfecto, eso es lo que necesitaba.', 'Quisiera saber cuál es mi saldo actual en mi cuenta de ahorros.', '¿Y eso cuánto tiempo tarda?']
- % de cada apertura dentro de cada categoría (si el texto no informa, todas ≈ iguales):
  - Comercial: «Buenas tardes, necesito consultar el saldo de mi tarjeta de crédito» 51.0%, «Hola, buenos días» 49.0%
  - Producto: «Buenas tardes, necesito consultar el saldo de mi tarjeta de crédito» 49.6%, «Hola, buenos días» 50.4%
  - Queja: «Buenas tardes, necesito consultar el saldo de mi tarjeta de crédito» 50.4%, «Hola, buenos días» 49.6%
  - Retención: «Buenas tardes, necesito consultar el saldo de mi tarjeta de crédito» 50.8%, «Hola, buenos días» 49.2%
  - Transaccional: «Buenas tardes, necesito consultar el saldo de mi tarjeta de crédito» 50.1%, «Hola, buenos días» 49.9%
  - Técnico: «Buenas tardes, necesito consultar el saldo de mi tarjeta de crédito» 50.2%, «Hola, buenos días» 49.8%

## 2. Call center: ¿qué explica resolver en el primer contacto (FCR) y escalar?

- FCR por categoría: Transaccional=91.5% (n=240,056), Producto=89.6% (n=150,863), Técnico=69.9% (n=102,899), Comercial=65.2% (n=54,879), Retención=60.2% (n=20,578), Queja=43.6% (n=117,021)
- FCR por canal: App=77.1% (n=26,364), Email=76.8% (n=27,543), Web=76.7% (n=3,395), Phone=76.6% (n=583,250), WhatsApp=76.6% (n=22,888), Web Chat=76.5% (n=22,856)
- FCR por sentimiento: Neutral=82.6% (n=459,712), Positivo=64.6% (n=75,562), Negativo=64.5% (n=94,322), Muy Negativo=64.4% (n=37,727), Muy Positivo=64.1% (n=18,973)
- FCR por acento cliente/agente: coincide=76.7% (n=392,039), sin dato=76.6% (n=204,750), distinto=76.6% (n=89,507)
- FCR por experiencia del agente: Mid-Senior=77.0% (n=79,177), Junior=76.9% (n=10,088), Specialist=76.6% (n=438,211), Senior=76.6% (n=158,820)
- Escalado por sentimiento: Muy Positivo=10.2% (n=18,973), Negativo=10.0% (n=94,322), Neutral=10.0% (n=459,712), Muy Negativo=9.9% (n=37,727), Positivo=9.9% (n=75,562)
- AUC modelo (held-out por cliente) → was_resolved: 0.771
- AUC modelo (held-out por cliente) → was_escalated: 0.501

## 3. Encuestas: ¿la satisfacción responde a lo que pasó en la llamada?

- CES: rango 1–4; media si resuelto=3.00 vs no resuelto=2.00
- CSAT: rango 1–4; media si resuelto=3.00 vs no resuelto=2.00
- NPS: rango 2–7; media si resuelto=6.00 vs no resuelto=3.00
- AUC wait_time_seconds → CSAT bajo (≤2): 0.5

## 4. Reclamos (disputas): mapeos y señal para prioridad / SLA

- description únicas: 5 · pureza description → category: 100.00%
- Pureza subcategory → category (sin nulos): 100.00%
- Cruce category × subcategory (conteos):
  - Branch: (nulo)=1,469, Atención en sucursal=11,892
  - Fees: (nulo)=1,359, Cobro indebido=12,194
  - Service: (nulo)=1,308, Calidad de servicio=11,886
  - Technical: (nulo)=1,279, Problema con app=12,128
  - Transactions: (nulo)=1,283, Cargo no reconocido=12,297
- SLA incumplido por prioridad: High=20.5% (n=9,890), Medium=20.1% (n=33,439), Low=20.1% (n=20,411), Critical=19.2% (n=3,355)
- SLA incumplido por categoría: Branch=20.4% (n=13,361), Service=20.2% (n=13,194), Transactions=20.2% (n=13,580), Technical=20.0% (n=13,407), Fees=19.8% (n=13,553)
- SLA incumplido por canal: Regulator=22.5% (n=717), App=20.5% (n=6,727), Web=20.4% (n=9,884), Call Center=20.1% (n=33,761), Branch=19.9% (n=2,683), Email=19.8% (n=13,323)
- Días de resolución (mediana) por prioridad: Critical=15, High=16, Low=16, Medium=16
- AUC claimed_amount → prioridad alta/crítica: 0.503
- AUC modelo → sla_breached: 0.501
- AUC modelo → prioridad alta/crítica: 0.498
- Estados: {'In Process': 26823, 'Open': 20125, 'Resolved': 13512, 'Escalated': 3321, 'Closed': 2609, 'Rejected': 705}

## 5. Transacciones: códigos de rechazo y fraude

- Muestra: 495,197 filas
- Cruce transaction_status × response_code:
  - Approved: (nulo)=22,611, 00=432,840
  - Declined: (nulo)=1,305, 05=5,769, 14=5,904, 51=5,857, 54=5,927
  - Pending: (nulo)=461, 05=2,377, 14=2,341, 51=2,337, 54=2,318
  - Reversed: (nulo)=260, 05=1,209, 14=1,221, 51=1,241, 54=1,219
- Fraudes en muestra: 496 (0.100%)
- AUC fraud_score → is_fraud: 0.844
- AUC amount → is_fraud: 0.514
- Fraude por canal: Transfer=0.2% (n=9,842), Web=0.1% (n=74,544), ATM=0.1% (n=148,269), Branch=0.1% (n=14,857), App=0.1% (n=74,195), POS=0.1% (n=173,490)
- Fraude por país: Spain=0.2% (n=4,463), USA=0.1% (n=4,576), Argentina=0.1% (n=96,998), Colombia=0.1% (n=144,470), México=0.1% (n=235,607), Mexico=0.1% (n=4,498), Brazil=0.1% (n=4,585)
- AUC modelo → is_fraud: 0.534
- Monedas: {'USD': 272758, 'COP': 133821, 'ARS': 88618} (¿MXN ausente?)
- Países: {'México': 235607, 'Colombia': 144470, 'Argentina': 96998, 'Brazil': 4585, 'USA': 4576, 'Mexico': 4498, 'Spain': 4463}

## 6. Crédito: ¿la mora (days_past_due) tiene relación con el perfil del cliente?

- Productos de crédito: 131,972 · tipos de producto: {'Cuenta Ahorro': 120203, 'Tarjeta Crédito': 100102, 'Cuenta Corriente': 99979, 'Tarjeta Débito': 39938, 'Préstamo Personal': 19960, 'Préstamo Hipotecario': 11910, 'Inversión': 5859, 'Seguro': 2049}
- days_past_due no nulo en crédito: 95.0% · valores: {'0.0': 106585, nan: 6622, '90.0': 3233, '30.0': 3117, '15.0': 3114, '60.0': 3112, '180.0': 3106, '120.0': 3083}
- ¿days_past_due en productos NO crédito?: 0
- Mora ≥30 días: 12.49% de 125,350 productos con dato
- AUC -credit_score → mora≥30: 0.503
- AUC -ingreso → mora≥30: 0.498
- Mora por segmento: Plus=12.7% (n=31,500), Student=12.6% (n=6,223), Basic=12.4% (n=75,002), Premium=12.3% (n=12,625)
- AUC modelo → mora≥30: 0.499
- Monedas de productos: {'USD': 220501, 'COP': 107975, 'ARS': 71524}

## 7. Consistencia entre columnas

- document_type por país: Argentina: {'DNI': 29842}; Colombia: {'CE': 15150, 'Pasaporte': 15062, 'CC': 15039}; México: {'DNI': 74907}
- Ejemplos registration_branch_id: ['SUC-<id>', 'SUC-<id>', 'SUC-<id>'] vs branch_id: ['SUC-<id>', 'SUC-<id>', 'SUC-<id>']
- Ejemplos assigned_branch_id agentes: ['SUC-<id>', 'SUC-<id>', 'SUC-<id>']
- customers.last_updated en el futuro (> 2026-06-17): 6.2%
- products.last_updated en el futuro: 6.3%
- Productos bloqueados: 19,935 · por tipo: {'Cuenta Ahorro': 5923, 'Cuenta Corriente': 4962, 'Tarjeta Crédito': 4932, 'Tarjeta Débito': 2112, 'Préstamo Personal': 1010, 'Préstamo Hipotecario': 612, 'Inversión': 275, 'Seguro': 109}
- Productos vencidos (expiration < 2026-06-17) pero Active: 56,664
- Agentes que hablan portugués: 129 de 1200 · especialidades: {nan: 476, 'Fraudes': 105, 'Cobranza': 97, 'Retención': 96, 'Soporte Técnico': 96, 'Créditos': 87, 'Inversiones': 83, 'Ventas': 82, 'Quejas y Reclamos': 78}
