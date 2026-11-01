-- B0 historical baseline (plan v2 section 11): what the bank does today, from the data.
-- One row per metric; the evaluation report and its page read it.
WITH k AS (
    SELECT *, date_diff('minute', ts, first_response_at) / 60.0 AS first_response_hours FROM silver.complaints
),
q AS (SELECT * FROM silver.call_center_interactions WHERE category = 'Queja'),
sv AS (
    SELECT s.score FROM silver.satisfaction_surveys s
    JOIN silver.call_center_interactions i ON i.interaction_id = s.interaction_id
    WHERE i.category = 'Queja' AND s.survey_type = 'CSAT'
)
SELECT 'complaint_contact_fcr' AS metric, (SELECT avg(CASE WHEN resolved THEN 1.0 ELSE 0 END) FROM q) AS value,
       'ratio' AS unit, (SELECT count(*) FROM q) AS n, 'call_center_interactions' AS source,
       'Contactos con motivo Queja resueltos en el primer contacto' AS description
UNION ALL
SELECT 'complaint_contact_csat', (SELECT avg(score) FROM sv), 'score_1_5', (SELECT count(*) FROM sv),
       'satisfaction_surveys', 'CSAT medio de los contactos por queja'
UNION ALL
SELECT 'median_first_response_hours', (SELECT median(first_response_hours) FROM k), 'hours',
       (SELECT count(first_response_hours) FROM k), 'complaints',
       'Mediana de horas hasta la primera respuesta de un reclamo formal'
UNION ALL
SELECT 'median_resolution_days', (SELECT median(resolution_days) FROM k), 'days',
       (SELECT count(resolution_days) FROM k), 'complaints', 'Mediana de días hasta resolver un reclamo'
UNION ALL
SELECT 'p90_resolution_days', (SELECT quantile_cont(resolution_days, 0.9) FROM k), 'days',
       (SELECT count(resolution_days) FROM k), 'complaints', 'Percentil 90 de días hasta resolver'
UNION ALL
SELECT 'complaints_never_assigned', (SELECT count(*) FROM k WHERE assigned_agent_id IS NULL), 'count',
       (SELECT count(*) FROM k), 'complaints', 'Reclamos que nunca se asignaron a nadie'
UNION ALL
SELECT 'complaints_with_amount_rate', (SELECT avg(CASE WHEN claimed_amount IS NOT NULL THEN 1.0 ELSE 0 END) FROM k),
       'ratio', (SELECT count(*) FROM k), 'complaints', 'Reclamos que llegan con un monto'
UNION ALL
SELECT 'affected_product_foreign_rate',
       (SELECT avg(CASE WHEN dq_affected_product_not_owned THEN 1.0 ELSE 0 END) FROM k WHERE affected_product_id IS NOT NULL),
       'ratio', (SELECT count(affected_product_id) FROM k), 'complaints',
       'Reclamos cuyo producto afectado pertenece a otro cliente'
UNION ALL
SELECT 'money_complaint_share',
       (SELECT avg(CASE WHEN subcategory IN ('Cargo no reconocido', 'Cobro indebido') THEN 1.0 ELSE 0 END) FROM k),
       'ratio', (SELECT count(*) FROM k), 'complaints', 'Reclamos de dinero (cargo no reconocido o cobro indebido)'
UNION ALL
SELECT 'transactional_contact_share',
       (SELECT avg(CASE WHEN category = 'Transaccional' THEN 1.0 ELSE 0 END) FROM silver.call_center_interactions),
       'ratio', (SELECT count(*) FROM silver.call_center_interactions), 'call_center_interactions',
       'Contactos transaccionales (saldo, movimientos)'
UNION ALL
SELECT 'fraud_rule_precision',
       (SELECT avg(CASE WHEN is_fraud THEN 1.0 ELSE 0 END) FROM silver.transactions WHERE fraud_score > {{fraud_score_high}}),
       'ratio', (SELECT count(*) FROM silver.transactions WHERE fraud_score > {{fraud_score_high}}), 'transactions',
       'Precisión de la regla fraud_score > 30'
UNION ALL
SELECT 'fraud_rule_recall',
       (SELECT avg(CASE WHEN fraud_score > {{fraud_score_high}} THEN 1.0 ELSE 0 END) FROM silver.transactions WHERE is_fraud),
       'ratio', (SELECT count(*) FROM silver.transactions WHERE is_fraud), 'transactions',
       'Fraudes que la regla fraud_score > 30 detecta'
