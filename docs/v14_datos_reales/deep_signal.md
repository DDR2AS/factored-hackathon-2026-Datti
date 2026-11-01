# Segunda pasada de señal: cruces entre tablas (LATAM Bank)

> **Nota:** reporte de una pasada anterior. Algunas conclusiones se corrigieron después (ver [hallazgos.md](hallazgos.md)): el patrón semanal real se ve agrupando por `process_date` (lunes a viernes iguales y fin de semana = 50%), y el efecto del sentimiento sobre el FCR es una confusión con el motivo.


Generado por `analysis/deep_signal.py` con DuckDB sobre tablas completas. AUC con IC 95% bootstrap en held-out por cliente; V de Cramér < 0.02 = efecto despreciable.

## 1. Patrones de demanda del call center (686k contactos, completos)

- Por hora (%): 0h=4.2 1h=4.2 2h=4.1 3h=4.2 4h=4.1 5h=4.2 6h=4.2 7h=4.1 8h=4.2 9h=4.2 10h=4.2 11h=4.2 12h=4.2 13h=4.2 14h=4.2 15h=4.1 16h=4.2 17h=4.2 18h=4.2 19h=4.2 20h=4.2 21h=4.1 22h=4.1 23h=4.2
- Por día de semana: Sunday=57,671, Monday=96,388, Tuesday=114,794, Wednesday=114,736, Thursday=113,216, Friday=113,314, Saturday=76,177
- Por mes: 1=57,291, 2=53,421, 3=58,881, 4=57,710, 5=57,674, 6=56,569, 7=58,553, 8=58,722, 9=56,506, 10=57,798, 11=55,432, 12=57,739
- Por trimestre: 2023-Q2=9,012, 2023-Q3=57,339, 2023-Q4=57,043, 2024-Q1=56,373, 2024-Q2=57,140, 2024-Q3=58,332, 2024-Q4=56,281, 2025-Q1=56,207, 2025-Q2=57,064, 2025-Q3=58,110, 2025-Q4=57,645, 2026-Q1=57,013, 2026-Q2=48,737
- Volumen diario: media 625, desv. 149, índice de dispersión var/media=35.42 (≈1 = puro azar Poisson)
- Motivo × h: chi² p=0.45, V de Cramér=0.006 (despreciable)
- Motivo × dow: chi² p=0.11, V de Cramér=0.003 (despreciable)
- Motivo × mes: chi² p=0.15, V de Cramér=0.004 (despreciable)
- Motivo × channel: chi² p=0.66, V de Cramér=0.003 (despreciable)
- Motivo × country: chi² p=0.49, V de Cramér=0.003 (despreciable)
- Motivo × segment: chi² p=0.33, V de Cramér=0.003 (despreciable)
- Motivo × edad: chi² p=0.29, V de Cramér=0.003 (despreciable)
- Motivo por segmento: Basic: Transaccional 35.0%, Producto 22.0%, Queja 17.1%; Plus: Transaccional 35.0%, Producto 21.9%, Queja 17.1%; Premium: Transaccional 35.1%, Producto 21.7%, Queja 16.9%; Student: Transaccional 34.8%, Producto 22.2%, Queja 16.8%
- Contactos por cliente: media 4.62, p90 7, máx 18 · clientes sin contacto: 1,557
- Duración/espera mediana (s) por motivo: Transaccional=205/119, Producto=263/119, Queja=431/120, Técnico=360/119, Comercial=540/120, Retención=478/120

## 2. Recontacto: ¿'no resuelto' implica que el cliente vuelve a llamar?

- Recontacto ≤7 días por resuelto: True=2.87% (n=526,030), False=2.87% (n=160,266)
- Recontacto ≤30 días por resuelto: True=11.65% (n=526,030), False=11.53% (n=160,266)
- Recontacto ≤7 días por requiere seguimiento: True=2.89% (n=239,054), False=2.86% (n=447,242)
- Siguiente contacto con el MISMO motivo: 23.0% (esperado si fuera azar: 23.0%)
- Resuelto × recontacto 7d: chi² p=0.99, V de Cramér=0.000 (despreciable)

## 3. ¿Qué dispara un contacto o un reclamo? (transacciones → call center / reclamos)

- Por estado: Approved: contacto 7d después 2.87% vs antes 2.87%, Transaccional 1.01%, Queja 0.49% (n=4,070,681); Declined: contacto 7d después 2.85% vs antes 2.83%, Transaccional 1.01%, Queja 0.49% (n=221,234); Pending: contacto 7d después 2.85% vs antes 2.95%, Transaccional 1.01%, Queja 0.47% (n=88,343); Reversed: contacto 7d después 2.85% vs antes 2.99%, Transaccional 1.01%, Queja 0.51% (n=44,750)
- Por código: (nulo): contacto 7d después 2.84% vs antes 2.86%, Transaccional 1.01%, Queja 0.48% (n=221,033); 00: contacto 7d después 2.87% vs antes 2.87%, Transaccional 1.01%, Queja 0.49% (n=3,867,312); 05: contacto 7d después 2.83% vs antes 2.92%, Transaccional 0.97%, Queja 0.50% (n=84,141); 14: contacto 7d después 2.82% vs antes 2.92%, Transaccional 1.03%, Queja 0.46% (n=84,472); 51: contacto 7d después 2.88% vs antes 2.82%, Transaccional 1.03%, Queja 0.49% (n=84,179); 54: contacto 7d después 2.90% vs antes 2.87%, Transaccional 1.00%, Queja 0.51% (n=83,871)
- Por fraude: False: contacto 7d después 2.87% vs antes 2.87%, Transaccional 1.01%, Queja 0.49% (n=4,420,692); True: contacto 7d después 3.17% vs antes 2.80%, Transaccional 1.00%, Queja 0.53% (n=4,316)
- Reclamo en los 30 días siguientes a la transacción: Approved/fraude=False: cualquiera 1.22%, categoría Transactions 0.25% (n=4,066,695); Approved/fraude=True: cualquiera 0.98%, categoría Transactions 0.23% (n=3,986); Declined/fraude=False: cualquiera 1.27%, categoría Transactions 0.27% (n=221,019); Declined/fraude=True: cualquiera 0.47%, categoría Transactions 0.00% (n=215); Pending/fraude=False: cualquiera 1.25%, categoría Transactions 0.28% (n=88,264); Pending/fraude=True: cualquiera 0.00%, categoría Transactions 0.00% (n=79); Reversed/fraude=False: cualquiera 1.30%, categoría Transactions 0.28% (n=44,714); Reversed/fraude=True: cualquiera 0.00%, categoría Transactions 0.00% (n=36)
- Contactos promedio del cliente según estado de su producto: Active=4.58, Blocked=4.56, Closed=4.59, Suspended=4.58

## 4. Disputas: ¿los reclamos se pueden anclar a transacciones reales?

- Reclamos con producto afectado: 44,570
- El producto afectado pertenece al mismo cliente: 0.0%
- % que tiene transacciones en ese producto en los 30 días previos, por categoría: Transactions=25.61% (n=9,001), Service=25.36% (n=8,837), Fees=25.21% (n=9,019), Branch=24.48% (n=8,808), Technical=24.29% (n=8,905)
- % que tiene una transacción marcada fraude en 90 días previos, por categoría: Technical=0.13% (n=8,905), Service=0.10% (n=8,837), Fees=0.10% (n=9,019), Branch=0.07% (n=8,808), Transactions=0.07% (n=9,001)
- % que tiene una transacción rechazada/revertida en 90 días previos, por categoría: Service=5.30% (n=8,837), Technical=5.15% (n=8,905), Transactions=4.98% (n=9,001), Branch=4.95% (n=8,808), Fees=4.92% (n=9,019)
- Monto reclamado coincide (±1%) con alguna transacción del producto en 90 días: Transactions=0.27% (n=2,996), Service=0.14% (n=2,781), Fees=0.14% (n=2,953), Branch=0.11% (n=2,780), Technical=0.03% (n=2,878)
- Tipo de producto afectado × categoría del reclamo: chi² p=0.24, V de Cramér=0.014 (despreciable)
- Top tipo de producto por categoría: Branch: Cuenta Ahorro 31%, Tarjeta Crédito 26%, Cuenta Corriente 24%; Fees: Cuenta Ahorro 30%, Tarjeta Crédito 25%, Cuenta Corriente 25%; Service: Cuenta Ahorro 30%, Cuenta Corriente 25%, Tarjeta Crédito 25%; Technical: Cuenta Ahorro 30%, Cuenta Corriente 25%, Tarjeta Crédito 25%; Transactions: Cuenta Ahorro 30%, Cuenta Corriente 27%, Tarjeta Crédito 24%

## 5. Códigos de rechazo vs estado real del producto (¿explicaciones consistentes?)

| Estado | Código | n | Producto vencido | Producto bloqueado/cerrado | Monto > saldo | Moneda ≠ producto |
|---|---|---|---|---|---|---|
| Approved | (nulo) | 203,369 | 10.4% | 0.0% | 22.6% | 0.0% |
| Approved | 00 | 3,867,312 | 10.4% | 0.0% | 22.5% | 0.0% |
| Declined | (nulo) | 10,962 | 10.2% | 0.0% | 23.3% | 0.0% |
| Declined | 05 | 52,246 | 10.4% | 0.0% | 22.7% | 0.0% |
| Declined | 14 | 52,711 | 10.5% | 0.0% | 22.4% | 0.0% |
| Declined | 51 | 52,788 | 10.7% | 0.0% | 22.3% | 0.0% |
| Declined | 54 | 52,527 | 10.5% | 0.0% | 22.5% | 0.0% |
| Pending | (nulo) | 4,392 | 10.3% | 0.0% | 22.2% | 0.0% |
| Pending | 05 | 21,191 | 10.5% | 0.0% | 22.8% | 0.0% |
| Pending | 14 | 21,096 | 10.6% | 0.0% | 22.3% | 0.0% |
| Pending | 51 | 20,911 | 10.1% | 0.0% | 23.0% | 0.0% |
| Pending | 54 | 20,753 | 10.2% | 0.0% | 22.1% | 0.0% |
| Reversed | (nulo) | 2,310 | 10.1% | 0.0% | 21.9% | 0.0% |
| Reversed | 05 | 10,704 | 10.4% | 0.0% | 22.4% | 0.0% |
| Reversed | 14 | 10,665 | 10.8% | 0.0% | 22.1% | 0.0% |
| Reversed | 51 | 10,480 | 10.1% | 0.0% | 22.5% | 0.0% |
| Reversed | 54 | 10,591 | 10.9% | 0.0% | 22.5% | 0.0% |
- Tipo de producto × tipo de transacción: 0.479 V de Cramér (0 = cualquier producto hace cualquier cosa)
- Ejemplos raros: 
- Transacciones en el mismo país del cliente: 95.4%

## 6. Crédito (segunda pasada): mora con features cruzadas de 6 tablas

- **Mora ≥30 días** (n=125,350, positivos 12.5%): AUC 0.502 [IC95 0.494–0.511] → SIN señal · features más informativas: [('age', 0.509), ('n_fees', 0.507), ('antig', 0.506), ('dias_sin_tx', 0.506)]
- **Cualquier mora (>0 días)** (n=125,350, positivos 15.0%): AUC 0.499 [IC95 0.492–0.506] → SIN señal · features más informativas: [('age', 0.508), ('n_fees', 0.508), ('n_cp', 0.506), ('n_prod', 0.506)]
- Mora≥30 por estado del producto: Suspended=13.08% (n=2,507), Blocked=13.08% (n=6,223), Closed=12.64% (n=10,024), Active=12.42% (n=106,596)
- Mora≥30 por tipo: Préstamo Hipotecario=12.79% (n=11,324), Tarjeta Crédito=12.47% (n=95,033), Préstamo Personal=12.40% (n=18,993)

## 7. Fraude (segunda pasada): features de comportamiento y cruce con cliente/producto

- Filas: 371,924 (todos los fraudes + ~8% de legítimas) · fraudes: 4,316
- fraud_score solo: AUC 0.847 [IC95 0.838–0.857] → CON señal
- **Modelo SIN fraud_score** (n=300,000, positivos 1.2%): AUC 0.507 [IC95 0.491–0.525] → SIN señal · features más informativas: [('amt_ratio', 0.517), ('ttype', 0.513), ('ptype', 0.513), ('dow', 0.511)]
- **Modelo CON fraud_score** (n=300,000, positivos 1.2%): AUC 0.825 [IC95 0.810–0.841] → CON señal · features más informativas: [('fscore', 0.85), ('amt_ratio', 0.517), ('ttype', 0.513), ('ptype', 0.513)]
- Fraude por país distinto al del cliente: True=1.27% (n=17,015), False=1.16% (n=354,909)
- fraud_score ≥ 50: marca 0.56% de transacciones, precisión 100.00%, recall 48.8%
- fraud_score ≥ 70: marca 0.34% de transacciones, precisión 100.00%, recall 29.2%
- fraud_score ≥ 80: marca 0.23% de transacciones, precisión 100.00%, recall 19.6%
- fraud_score ≥ 90: marca 0.11% de transacciones, precisión 100.00%, recall 9.9%

## 8. Encuestas y agentes: ¿hay efecto real del agente?

- Comentario × cuartil de espera: chi² p=0.89, V de Cramér=0.011 (despreciable)
- Comentario × resuelto: chi² p=0, V de Cramér=0.276
- Sentimiento del comentario × resuelto: chi² p=0, V de Cramér=0.276
- CSAT: puntaje por resuelto (medias) False=2.00, True=3.00 · distribución: {1.0: 4422, 2.0: 35646, 3.0: 73313, 4.0: 14475}
- NPS: puntaje por resuelto (medias) False=3.00, True=6.00 · distribución: {2.0: 4858, 3.0: 4900, 4.0: 4876, 5.0: 16475, 6.0: 16329, 7.0: 16230}
- CES: puntaje por resuelto (medias) False=2.00, True=3.00 · distribución: {1.0: 737, 2.0: 5894, 3.0: 12150, 4.0: 2454}
- FCR por agente (1090 agentes con ≥300 contactos): rango 69.9%–81.6%; varianza observada / esperada por azar = 1.02 (≈1 = no hay efecto agente)
- avg_csat declarado del agente vs CSAT observado: Spearman ρ=-0.018 (p=0.58, 971 agentes)
- Especialidad del agente × motivo atendido: chi² p=0.59, V de Cramér=0.004 (despreciable) (si hubiera enrutamiento por especialidad, V sería alto)
- FCR por especialidad: Quejas y Reclamos=76.90% (n=40,305), Retención=76.85% (n=53,741), Soporte Técnico=76.83% (n=56,403), Ventas=76.78% (n=46,430), Fraudes=76.71% (n=60,528), Cobranza=76.56% (n=54,204), Créditos=76.54% (n=50,394), Inversiones=76.36% (n=46,342)

## 9. Transcripts (segunda pasada): texto del agente, entidades y coherencia con productos

- Pureza agent_text → motivo: 34.9% (azar ≈ 34.9%) · valores únicos: 42
- Pureza detected_keywords → motivo: 34.9% (azar ≈ 34.9%) · valores únicos: 12
- Pureza mentioned_entities → motivo: 34.9% (azar ≈ 34.9%) · valores únicos: 54
- Claves en mentioned_entities: {('account_numbers', 'amounts', 'dates', 'products'): 20000}
- Ejemplo de mentioned_entities: {"account_numbers": 0, "dates": 0, "amounts": 2, "products": null}
- Ejemplo de agent_text: Buenas tardes, claro que sí. Déjeme revisar esa información. Su saldo actual es {monto} {moneda} y su límite disponible es de {limite} {moneda}. No hay problema, que tenga buen día. No hay problema, que tenga buen día.
- Cliente que pregunta por tarjeta de crédito y SÍ tiene una: 48.8% vs quien pregunta por ahorros: 48.9%
- Cliente que pregunta por ahorros y SÍ tiene cuenta de ahorro: 55.2% vs quien pregunta por TC: 55.1%

## 10. Fuga de clientes (customer_status ≠ Active) con historial de 5 tablas

- **Cliente Inactive/Closed** (n=150,000, positivos 11.9%): AUC 0.496 [IC95 0.489–0.504] → SIN señal · features más informativas: [('n_decl', 0.51), ('country', 0.507), ('n_tx', 0.506), ('n_dig', 0.506)]
- Tasa de fuga según contactos de Retención (0 vs ≥1): False=11.97% (n=130,726), True=11.66% (n=19,274)
- Clientes Closed/Inactive con transacciones: 89.8% (inconsistencia si es alto)

## 11. Campañas: conversión y cumplimiento de consentimiento

- Envíos entregados (muestra 25%): 409,671 · a clientes que NO aceptan marketing: 50.1%
- Envío a segmento distinto del objetivo de la campaña: 43.7%
- Conversión si ya tiene el producto promovido: False=0.60% (n=283,053), True=0.59% (n=126,618)
- **Conversión** (n=300,000, positivos 0.6%): AUC 0.649 [IC95 0.631–0.668] → CON señal · features más informativas: [('send_channel', 0.571), ('objective', 0.526), ('country', 0.516), ('ya_tiene', 0.51)]

## 12. Canal digital: errores por versión/plataforma y cruce con reclamos 'Problema con app'

- Tasa de error por platform: Android=2.30%, Linux=2.29%, MacOS=2.30%, Windows=2.31%, iOS=2.30%
- Tasa de error por channel: Android App=2.30%, Desktop Web=2.30%, Mobile Web=2.30%, iOS App=2.29%
- Error por app_version (500 versiones): rango 2.00%–2.67%; varianza obs/esperada=1.00 (≈1 = ninguna versión es peor)
- Reclamo con error digital del cliente en los 7 días previos, por subcategoría: Atención en sucursal=1.16% (n=11,892), Calidad de servicio=1.15% (n=11,886), Cargo no reconocido=1.07% (n=12,297), Cobro indebido=1.02% (n=12,194), Problema con app=1.00% (n=12,128), nan=0.81% (n=6,698)
