-- SYNTHETIC monthly fee charges that MATCH gold.fee_schedule: the 1st of each of the last
-- {{fee_months}} months up to the customer's reference date, at 03:00. Same columns as
-- transactions_enriched (origin = 'synthetic_fee'). Lane A "the fee matches the schedule" needs
-- them; planted evaluation scenarios change one of them to test lane B.
WITH months AS (
    SELECT fs.*, cp.reference_date, cp.country,
           CAST(date_trunc('month', cp.reference_date) AS DATE) AS this_month,
           r.k
    FROM gold.fee_schedule fs
    JOIN gold.customer_profile cp USING (customer_id)
    CROSS JOIN range(0, {{fee_months}}) r(k)
    WHERE fs.frequency = 'monthly'
),
dated AS (
    SELECT *, CAST(this_month - to_months(CAST(k AS INTEGER)) AS DATE) AS charge_date FROM months
)
SELECT
    'FEE-' || replace(d.product_id, 'PRD-', '') || '-' || strftime(d.charge_date, '%Y%m') AS txn_id,
    d.customer_id,
    d.product_id,
    cpr.product_type,
    COALESCE(cpr.card_last4, cpr.number_last4)                AS card_last4,
    d.charge_date                                             AS local_date,
    '03:00'                                                   AS local_time,
    CAST(d.charge_date AS TIMESTAMP) + INTERVAL 3 HOUR        AS ts,
    CAST(d.charge_date - INTERVAL 1 DAY AS DATE)              AS process_date,
    'Fee'                                                     AS transaction_type,
    'fee'                                                     AS txn_type,
    CAST(NULL AS VARCHAR)                                     AS reversal_of,
    d.amount,
    d.currency,
    CAST(round(d.amount / CASE d.currency WHEN 'USD' THEN 1 WHEN 'COP' THEN {{usd_rate_COP}}
                                          WHEN 'ARS' THEN {{usd_rate_ARS}} ELSE {{usd_rate_MXN}} END, 2)
         AS DECIMAL(18, 2))                                   AS amount_usd,
    d.merchant_id,
    upper(strip_accents(d.description))                       AS merchant_name,
    'bank_fee'                                                AS merchant_category,
    'Internal'                                                AS channel,
    'approved'                                                AS status,
    '00'                                                      AS response_code,
    'Aprobada'                                                AS response_description,
    0.0                                                       AS fraud_score,
    'bajo'                                                    AS fraud_risk_tier,
    FALSE                                                     AS is_fraud,
    d.country                                                 AS txn_country,
    CAST(NULL AS VARCHAR)                                     AS txn_city,
    FALSE                                                     AS is_foreign,
    TRUE                                                      AS explanation_reliable,
    FALSE                                                     AS dq_before_product_open,
    FALSE                                                     AS dq_after_card_expiry,
    FALSE                                                     AS dq_customer_not_active,
    FALSE                                                     AS dq_amount_usd_imputed,
    'synthetic_fee'                                           AS origin
FROM dated d
JOIN gold.customer_products cpr ON cpr.product_id = d.product_id
WHERE d.charge_date <= d.reference_date
