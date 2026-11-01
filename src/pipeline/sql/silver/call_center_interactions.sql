-- Contactos del call center: tipado, una sola columna de motivo y banderas de calidad.
--  * contact_reason es idéntico a reason_category (100%): se conserva solo "category".
--  * process_date = fecha contable (corte a las 08:00): usarla para agregados diarios.
--  * mentioned_products son IDs aleatorios: casi nunca existen y nunca son del cliente.
WITH i AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY interaction_id ORDER BY _ingested_at DESC, _source_file DESC) AS _rn
    FROM bronze.call_center_interactions
),
mentioned AS (
    SELECT x.interaction_id, bool_or(p.customer_id = x.customer_id) AS any_owned
    FROM (
        SELECT interaction_id, customer_id, trim(unnest(string_split(mentioned_products, ','))) AS product_id
        FROM i WHERE _rn = 1 AND mentioned_products IS NOT NULL
    ) x
    LEFT JOIN silver.products p ON p.product_id = x.product_id
    GROUP BY 1
)
SELECT
    i.interaction_id, i.customer_id, i.agent_id,
    TRY_CAST(i.interaction_date AS TIMESTAMP)  AS ts,
    TRY_CAST(i.process_date AS DATE)           AS process_date,
    isodow(TRY_CAST(i.process_date AS DATE))   AS weekday_num,  -- 1 = lunes ... 7 = domingo
    i.interaction_type, i.channel,
    i.reason_category                          AS category,
    TRY_CAST(i.duration_seconds AS DOUBLE)     AS duration_s,
    TRY_CAST(i.wait_time_seconds AS DOUBLE)    AS wait_s,
    i.was_resolved = 'True'                    AS resolved,
    i.requires_followup = 'True'               AS requires_followup,
    i.was_escalated = 'True'                   AS escalated,
    i.detected_sentiment                       AS sentiment,
    TRY_CAST(i.sentiment_score AS DOUBLE)      AS sentiment_score,
    i.customer_detected_accent, i.agent_used_accent,
    i.mentioned_products,
    i.has_transcript = 'True'                  AS has_transcript,
    i.has_recording = 'True'                   AS has_recording,
    i.contact_reason IS DISTINCT FROM i.reason_category AS dq_contact_reason_differs,
    i.duration_seconds IS NULL                 AS dq_duration_missing,
    i.wait_time_seconds IS NULL                AS dq_wait_missing,
    COALESCE(i.mentioned_products IS NOT NULL AND NOT COALESCE(m.any_owned, FALSE), FALSE) AS dq_mentioned_products_not_owned,
    regexp_extract(i._source_file, '([^/]+)$', 1) AS _source_file,
    i._ingested_at
FROM i
LEFT JOIN mentioned m USING (interaction_id)
WHERE i._rn = 1
