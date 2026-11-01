-- Eventos digitales: tipado y normalización. 24% sin cliente; product_id nunca es del cliente.
SELECT
    e.event_id,
    TRY_CAST(e.event_date AS TIMESTAMP)      AS ts,
    TRY_CAST(e.process_date AS DATE)         AS process_date,
    e.customer_id, e.session_id, e.event_type, e.event_category, e.channel, e.platform, e.browser,
    e.app_version, e.page_url, e.page_title, e.action, e.element_id, e.product_id,
    TRY_CAST(e.event_value AS DOUBLE)        AS event_value,
    TRY_CAST(e.duration_seconds AS DOUBLE)   AS duration_s,
    e.ip_address,
    CASE WHEN e.ip_country = 'Mexico' THEN 'México' ELSE e.ip_country END AS ip_country,
    e.ip_city,
    e.is_mobile = 'True'                     AS is_mobile,
    e.referrer, e.utm_source, e.utm_medium, e.utm_campaign,
    e.customer_id IS NULL                    AS dq_customer_missing,
    COALESCE(e.product_id IS NOT NULL AND p.customer_id IS DISTINCT FROM e.customer_id, FALSE) AS dq_product_not_owned,
    regexp_extract(e._source_file, '([^/]+)$', 1) AS _source_file,
    e._ingested_at
FROM bronze.digital_events e
LEFT JOIN silver.products p ON p.product_id = e.product_id
QUALIFY ROW_NUMBER() OVER (PARTITION BY e.event_id ORDER BY e._ingested_at DESC, e._source_file DESC) = 1
