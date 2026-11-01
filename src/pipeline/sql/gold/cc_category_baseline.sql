-- Baseline por motivo de contacto: volumen, resolución, escalamiento, tiempos, satisfacción y costo.
-- est_handle_minutes imputa la duración faltante (14%) con la media del motivo.
-- est_cost_usd usa un SUPUESTO de costo por minuto de agente (config.AGENT_COST_PER_MINUTE_USD).
WITH agg AS (
    SELECT
        category,
        COUNT(*)                                          AS contacts,
        AVG(CASE WHEN resolved THEN 1.0 ELSE 0 END)       AS fcr_rate,
        AVG(CASE WHEN escalated THEN 1.0 ELSE 0 END)      AS escalation_rate,
        AVG(CASE WHEN requires_followup THEN 1.0 ELSE 0 END) AS followup_rate,
        median(duration_s)                                AS median_duration_s,
        AVG(duration_s)                                   AS avg_duration_s,
        median(wait_s)                                    AS median_wait_s,
        AVG(duration_s) * COUNT(*) / 60.0                 AS est_handle_minutes
    FROM silver.call_center_interactions
    GROUP BY category
),
sat AS (
    SELECT
        i.category,
        AVG(CASE WHEN s.survey_type = 'CSAT' THEN s.score END) AS csat_avg,
        AVG(CASE WHEN s.survey_type = 'NPS' THEN s.score END)  AS nps_score_avg,
        COUNT(*)                                               AS surveys
    FROM silver.satisfaction_surveys s
    JOIN silver.call_center_interactions i ON i.interaction_id = s.interaction_id
    GROUP BY i.category
)
SELECT
    agg.*,
    agg.contacts / SUM(agg.contacts) OVER ()                        AS contact_share,
    agg.est_handle_minutes * {{agent_cost_per_minute_usd}}          AS est_cost_usd,
    {{agent_cost_per_minute_usd}}                                   AS assumed_cost_per_minute_usd,
    sat.csat_avg, sat.nps_score_avg, sat.surveys
FROM agg
LEFT JOIN sat ON sat.category = agg.category
ORDER BY contacts DESC
