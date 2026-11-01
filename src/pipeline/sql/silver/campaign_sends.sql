-- Envíos de campañas: tipado y cumplimiento (consentimiento y segmento objetivo).
WITH s AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY send_id ORDER BY _ingested_at DESC, _source_file DESC) AS _rn
    FROM bronze.campaign_sends
)
SELECT
    s.send_id,
    TRY_CAST(s.send_date AS TIMESTAMP)       AS ts,
    TRY_CAST(s.process_date AS DATE)         AS process_date,
    s.campaign_id, s.customer_id, s.send_channel, s.template_used, s.subject, s.send_status,
    s.was_delivered = 'True'                 AS delivered,
    s.was_opened = 'True'                    AS opened,
    TRY_CAST(s.open_date AS TIMESTAMP)       AS opened_at,
    s.was_clicked = 'True'                   AS clicked,
    TRY_CAST(s.click_date AS TIMESTAMP)      AS clicked_at,
    TRY_CAST(TRY_CAST(s.click_count AS DOUBLE) AS INTEGER) AS click_count,
    s.had_conversion = 'True'                AS converted,
    TRY_CAST(s.conversion_date AS TIMESTAMP) AS converted_at,
    TRY_CAST(s.conversion_value AS DOUBLE)   AS conversion_value,
    s.open_device,
    CASE WHEN s.open_country = 'Mexico' THEN 'México' ELSE s.open_country END AS open_country,
    s.failure_reason,
    TRY_CAST(s.send_cost AS DOUBLE)          AS send_cost,
    COALESCE(NOT c.accepts_marketing, FALSE) AS dq_sent_without_consent,
    COALESCE(mc.target_segment IS NOT NULL AND mc.target_segment <> c.segment, FALSE) AS dq_outside_target_segment,
    COALESCE(s.subject LIKE '%en nan!%', FALSE) AS dq_subject_nan,  -- "¡Oferta especial en nan!"
    regexp_extract(s._source_file, '([^/]+)$', 1) AS _source_file,
    s._ingested_at
FROM s
LEFT JOIN silver.customers c USING (customer_id)
LEFT JOIN silver.marketing_campaigns mc USING (campaign_id)
WHERE s._rn = 1
