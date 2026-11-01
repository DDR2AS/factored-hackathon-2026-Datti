-- Campañas de marketing: tipado y normalización de país.
SELECT
    campaign_id, campaign_name, description, campaign_type, campaign_objective, promoted_product, target_segment,
    CASE WHEN target_country = 'Mexico' THEN 'México' ELSE target_country END AS target_country,
    TRY_CAST(start_date AS DATE)                  AS start_date,
    TRY_CAST(end_date AS DATE)                    AS end_date,
    TRY_CAST(budget AS DOUBLE)                    AS budget,
    campaign_status,
    TRY_CAST(expected_conversion_rate AS DOUBLE)  AS expected_conversion_rate,
    COALESCE(TRY_CAST(end_date AS DATE) < TRY_CAST(start_date AS DATE), FALSE) AS dq_end_before_start,
    _source_file, _ingested_at
FROM bronze.marketing_campaigns
QUALIFY ROW_NUMBER() OVER (PARTITION BY campaign_id ORDER BY _ingested_at DESC) = 1
