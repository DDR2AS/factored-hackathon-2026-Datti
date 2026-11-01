-- Encuestas: tipado y coherencia con el contacto evaluado.
-- El puntaje está determinado por si el contacto se resolvió (CSAT 3 vs 2; NPS 6 vs 3).
WITH s AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY survey_id ORDER BY _ingested_at DESC, _source_file DESC) AS _rn
    FROM bronze.satisfaction_surveys
)
SELECT
    s.survey_id,
    TRY_CAST(s.survey_date AS TIMESTAMP)     AS ts,
    TRY_CAST(s.process_date AS DATE)         AS process_date,  -- copia la fecha contable del contacto
    s.interaction_id, s.customer_id, s.agent_id,
    s.survey_type, s.send_channel,
    TRY_CAST(TRY_CAST(s.main_score AS DOUBLE) AS INTEGER) AS score,
    s.nps_category,
    s.question_1_text, TRY_CAST(TRY_CAST(s.question_1_response AS DOUBLE) AS INTEGER) AS question_1_response,
    s.question_2_text, TRY_CAST(TRY_CAST(s.question_2_response AS DOUBLE) AS INTEGER) AS question_2_response,
    s.question_3_text, TRY_CAST(TRY_CAST(s.question_3_response AS DOUBLE) AS INTEGER) AS question_3_response,
    s.open_comments, s.comment_sentiment,
    TRY_CAST(s.response_time_hours AS DOUBLE)   AS response_time_hours,
    TRY_CAST(s.campaign_response_rate AS DOUBLE) AS campaign_response_rate,
    -- NPS: categoría esperada según el puntaje (0-6 detractor, 7-8 pasivo, 9-10 promotor)
    COALESCE(s.survey_type = 'NPS' AND s.nps_category IS DISTINCT FROM
        CASE WHEN TRY_CAST(s.main_score AS DOUBLE) <= 6 THEN 'Detractor'
             WHEN TRY_CAST(s.main_score AS DOUBLE) <= 8 THEN 'Passive' ELSE 'Promoter' END, FALSE) AS dq_nps_category_mismatch,
    COALESCE(s.customer_id IS DISTINCT FROM i.customer_id, TRUE) AS dq_customer_differs_from_interaction,
    COALESCE(s.agent_id IS DISTINCT FROM i.agent_id, TRUE)       AS dq_agent_differs_from_interaction,
    regexp_extract(s._source_file, '([^/]+)$', 1) AS _source_file,
    s._ingested_at
FROM s
LEFT JOIN silver.call_center_interactions i USING (interaction_id)
WHERE s._rn = 1
