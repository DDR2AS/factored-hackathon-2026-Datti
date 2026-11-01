# Análisis v1.4 sobre el dataset completo de los organizadores

Copia de los reportes del repo personal de Arturo ([Arturo-GA/Hackathon-Factored](https://github.com/Arturo-GA/Hackathon-Factored), carpeta `docs/`), hechos sobre los **datos reales** (686.296 contactos, 67.095 reclamos, 4.425.008 transacciones, 2023-06-17 a 2026-06-17), sin muestreo salvo donde se indica. Solo contienen estadísticas agregadas (los IDs y correos se enmascararon con `analysis/sanitize_docs.py`). Los scripts que reproducen cada cifra están en ese repo (`analysis/`, `analysis/pass3/`).

Estos son los únicos números del proyecto que vienen del dataset real; todo lo que dice `synthetic_replica` (pipeline, modelos y evaluación de esta rama) se calculó sobre la réplica sintética (D15).

| Archivo | Contenido |
|---|---|
| [hallazgos.md](hallazgos.md) | Conclusiones: reglas del generador, señal disponible, defectos y qué implica para la solución |
| [analisis_quejas.md](analisis_quejas.md) | Reclamos: volúmenes, tiempos, montos, productos ajenos, casos que necesitan una persona |
| [hallazgos_detalle.md](hallazgos_detalle.md) | 38 hallazgos con evidencia, cada uno reproducido por un script y desafiado por otro |
| [dq_report.md](dq_report.md) | 194 contratos de calidad sobre silver: 159 PASS, 35 WARN, 0 FAIL |
| [data_profile.md](data_profile.md) | Perfil columna por columna de las 13 tablas (base de la réplica sintética) |
| [tratamiento_datos.md](tratamiento_datos.md), [signal_checks.md](signal_checks.md), [deep_signal.md](deep_signal.md) | Tratamiento de datos y búsqueda de señal |
