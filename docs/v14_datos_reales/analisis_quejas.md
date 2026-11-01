# Análisis de quejas y reclamos: clasificación completa y reglas de decisión

Población completa (sin muestreo): 67,095 reclamos formales (`complaints`), 117,021 contactos de tipo Queja
dentro de 686,296 contactos (`call_center_interactions`), 4,425,008 transacciones. Periodo: 2023-06-17 a 2026-06-17.

## 1. Qué entra: reclamos formales por subcategoría y tipo de caso

| Subcategoría | Total | Queja | Reclamo con dinero | Solicitud | Sugerencia |
|---|---|---|---|---|---|
| Cargo no reconocido | 13,580 | 8,239 | 3,335 | 1,329 | 677 |
| Cobro indebido | 13,553 | 8,171 | 3,357 | 1,367 | 658 |
| Problema con app | 13,407 | 8,120 | 3,312 | 1,335 | 640 |
| Atención en sucursal | 13,361 | 7,969 | 3,355 | 1,372 | 665 |
| Calidad de servicio | 13,194 | 7,953 | 3,239 | 1,358 | 644 |
| **Total** | **67,095** | **40,452 (60%)** | **16,598 (25%)** | **6,761 (10%)** | **3,284 (5%)** |

Lectura: las 5 subcategorías pesan igual (20% cada una) y el tipo de caso se reparte igual dentro de cada una.
El 25% son "Claim" (piden dinero); el 10% son solicitudes y el 5% sugerencias, que no requieren investigación.

## 2. Por dónde entran

| Canal | Reclamos | % | Nota |
|---|---|---|---|
| Call Center | 33,761 | 50.3% | La mitad nace en una llamada: es el canal que el chat puede desviar |
| Email | 13,323 | 19.9% | Texto libre: candidato natural al clasificador |
| Web | 9,884 | 14.7% | |
| App | 6,727 | 10.0% | |
| Sucursal | 2,683 | 4.0% | |
| Regulador | 717 | 1.1% | Siempre humano |

Los contactos de tipo Queja al call center (117,021) entran 85% por teléfono (99,554), 4% email, 3.8% app,
3.4% web chat, 3.3% WhatsApp, 0.5% web. La resolución es idéntica en todos los canales (43–44%): el canal no cambia el resultado, el proceso sí.

## 3. Evidencia con la que llegan (base de las reglas de completitud)

| Subcategoría | Con monto | Con producto | Producto de otro cliente | Con sucursal |
|---|---|---|---|---|
| Cargo no reconocido | 4,500 (33%) | 9,001 (66%) | 9,001 (100% de los que traen producto) | 3,904 (29%) |
| Cobro indebido | 4,457 (33%) | 9,019 (67%) | 9,019 (100%) | 3,918 (29%) |
| Problema con app | 4,352 (32%) | 8,905 (66%) | 8,905 (100%) | 3,802 (28%) |
| Atención en sucursal | 4,223 (32%) | 8,808 (66%) | 8,808 (100%) | 3,816 (29%) |
| Calidad de servicio | 4,219 (32%) | 8,837 (67%) | 8,837 (100%) | 3,738 (28%) |

Montos reclamados (en USD, solo los 20,711 que traen monto): mediana 14 USD, p75 ~285 USD, máximo ~5,000 USD; la
distribución es idéntica en las 5 subcategorías (el monto es aleatorio en el generador). 985 reclamos de cargo superan 500 USD.

Conclusión: hoy ningún reclamo llega completo. El sistema debe **construir** la evidencia: buscar el cargo en las
transacciones del cliente y verificar que sea suyo, pedir sucursal y fecha cuando corresponde.

## 4. Criterios de derivación a humano (conteos exactos)

| Subcategoría | Vía regulador | Reincidente | Prioridad crítica | Monto > 500 USD | Con compensación | Cualquier criterio |
|---|---|---|---|---|---|---|
| Cargo no reconocido | 156 | 1,992 | 644 | 985 | 995 | 3,432 (25%) |
| Cobro indebido | 140 | 2,064 | 686 | 980 | 945 | 3,540 (26%) |
| Problema con app | 130 | 2,047 | 682 | 919 | 894 | 3,467 (26%) |
| Atención en sucursal | 142 | 2,020 | 691 | 957 | 935 | 3,480 (26%) |
| Calidad de servicio | 149 | 1,963 | 652 | 882 | 872 | 3,334 (25%) |
| **Total** | **717** | **10,086** | **3,355** | **4,723** | **4,641** | **17,253 (26%)** |

Con las reglas "regulador, reincidente, crítico o monto > 500 USD", el 26% de los reclamos iría a humano de entrada.
Esto fija la mezcla de rutas: **ruta C ≈ 25%**; el 75% restante se reparte entre A (resolver con datos) y B (caso con promesa).

## 5. Estado de gestión hoy

| Subcategoría | Open (sin asignar) | En proceso | Escalado | Resuelto | Cerrado | Rechazado | SLA incumplido |
|---|---|---|---|---|---|---|---|
| Cargo no reconocido | 4,040 | 5,407 | 677 | 2,780 | 551 | 125 | 2,739 |
| Cobro indebido | 4,028 | 5,468 | 700 | 2,738 | 486 | 133 | 2,680 |
| Problema con app | 3,997 | 5,419 | 653 | 2,670 | 528 | 140 | 2,681 |
| Atención en sucursal | 4,021 | 5,360 | 599 | 2,675 | 551 | 155 | 2,724 |
| Calidad de servicio | 4,039 | 5,169 | 692 | 2,649 | 493 | 152 | 2,671 |
| **Total** | **20,125 (30%)** | **26,823 (40%)** | **3,321 (5%)** | **13,512 (20%)** | **2,609 (4%)** | **705 (1%)** | **13,495 (20%)** |

Solo el 24% de los reclamos está resuelto o cerrado. El 30% nunca fue asignado.

## 6. Fraude: números exactos

| Concepto | Valor |
|---|---|
| Transacciones | 4,425,008 |
| Fraudes reales (`is_fraud`) | **4,316** (0.098%; 1,439 por año; 3.9 por día) |
| Fraudes aprobados por el banco | 3,986 (92.4%) |
| Fraudes rechazados | 215 |
| Fraudes pendientes o revertidos | 115 |
| Clientes con al menos un fraude | 4,233 |
| Monto aprobado en fraude | 6,258,630 USD en 3 años |
| Transacciones con `fraud_score > 30` | 2,373, y **las 2,373 son fraude** (precisión 100%) |
| Fraudes detectables con la regla | 2,373 de 4,316 (55%) |
| Fraudes no detectables | 1,943 (45%): score ≤ 30 o nulo, sin ninguna otra señal |
| Fraudes con reclamo "Cargo no reconocido" en 30 días | 0.21% (nivel del azar) |
| Reclamos "Cargo no reconocido" con fraude real previo | 0.21% (igual que sucursal: 0.21%) |

El fraude se reparte igual entre productos (0.10% de las transacciones de cada tipo: ahorro, crédito, cuenta corriente, incluso hipotecas y seguros) y entre canales (POS, ATM, Web, App). No hay patrón: es un sorteo del generador.

Implicación: el fraude y los reclamos de cargo no reconocido **no están conectados** en la data. El fraude se atiende
con la regla del score (a pedido y, opcionalmente, con un aviso proactivo), no con un modelo.

## 7. Reglas de decisión con números

Mezcla objetivo por ruta (sobre reclamos formales; los contactos de "solicitud" y "sugerencia" van a B sin investigación):

| Ruta | Regla | Volumen estimado |
|---|---|---|
| C · Humano ahora | regulador (717) o reincidente (10,086) o prioridad crítica (3,355) o monto > 500 USD (4,723) o cargo con `fraud_score > 30` o queja sobre personas o disputa de la resolución | ≈ 26% (17,253 en 3 años; ≈ 5,750/año; 16/día hábil) |
| A · Se resuelve ahora | cargo encontrado, propio, aprobado y coherente y el cliente lo reconoce al ver el detalle; cobro que coincide con la tarifa publicada; reverso ya aplicado; error de app con guía disponible | ≈ 25–30% (meta; se mide en la evaluación) |
| B · Caso con promesa | el resto: evidencia completa que requiere gestión (fecha prometida: 16 días mediana, 28 días p90) | ≈ 45–50% |

Reglas de completitud (qué se exige antes de abrir un caso):

| Subcategoría | Obligatorio | Verificación |
|---|---|---|
| Cargo no reconocido | transacción identificada (fecha, monto o comercio) | existe, es de un producto del cliente, estado, código, `fraud_risk_tier` |
| Cobro indebido | cargo + motivo (duplicado, tarifa, monto distinto) | existe y es propio; comparación con tarifa sintética |
| Problema con app | pantalla + fecha (+ plataforma) | evento de error del cliente si existe (fixture) |
| Atención en sucursal | sucursal + fecha + qué pasó | la sucursal existe y estaba abierta ese día |
| Calidad de servicio | canal + fecha + qué pasó | contacto previo del cliente en esa fecha si existe |

Vía rápida de fraude (dentro de C): si el cliente dice "me robaron la tarjeta" o el cargo tiene `fraud_score > 30`
→ ofrecer bloqueo de tarjeta con confirmación explícita → abrir "Cargo no reconocido" con evidencia → cola de Fraudes (96 activos), prioridad alta, SLA interno 2 h.

## 8. Contactos de Queja al call center (para el baseline)

| Canal | Quejas | Resueltas | % resuelto | Escaladas | Con seguimiento |
|---|---|---|---|---|---|
| Phone | 99,554 | 43,425 | 43.6% | 9,950 | 62,674 |
| Email | 4,637 | 1,982 | 42.7% | 484 | 2,933 |
| App | 4,399 | 1,939 | 44.1% | 453 | 2,750 |
| Web Chat | 3,946 | 1,717 | 43.5% | 407 | 2,486 |
| WhatsApp | 3,897 | 1,704 | 43.7% | 374 | 2,470 |
| Web | 588 | 254 | 43.2% | 65 | 373 |
| **Total** | **117,021** | **51,021** | **43.6%** | **11,733** | **73,686** |
