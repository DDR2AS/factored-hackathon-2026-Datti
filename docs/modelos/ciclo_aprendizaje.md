# Ciclo de aprendizaje (una vuelta de demostración)

Generado por `python scripts/learning_loop.py` (`src/eval/learning_loop.py`). **Decisiones de analista SIMULADAS** con la verdad sembrada de los escenarios; etiquetas marcadas `synthetic: true`.

- Escenarios nuevos (semilla 777, distinta de `make eval`): 240.
- Etiquetas escritas: 464 ({'decision': 49, 'lane': 203, 'match': 163, 'recommendation': 49}) en `data/labels/` → `gold.labels` con `make pipeline`.
- Consultas etiquetadas para M1 (cargos confirmados por el cliente): 107.

| | M1 actual | Candidato con etiquetas |
|---|---|---|
| Versión | `m1-lgbm-1` | (no promovido) |
| Top-1 (prueba congelada) | 75.52% | 75.34% |
| Confirmaciones equivocadas | 0.35% | 0.88% |
| Confirmación directa | 23.89% | 30.66% |
| Detección de cargo inexistente | 96.63% | 91.81% |

**Decisión:** se mantiene el modelo actual. Regla: promote only if wrong confirmations <= current AND top-1 >= current, on the same frozen test queries.

En producción el mismo paso corre como trabajo de entrenamiento (SageMaker, pago por segundo) con las decisiones reales de la consola (`labels_emitted`), y M2 se reentrena igual con las correcciones de intención.
