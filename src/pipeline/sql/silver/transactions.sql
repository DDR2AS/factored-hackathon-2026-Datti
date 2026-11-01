-- Transacciones: tipadas, normalizadas y enriquecidas con reglas documentadas del generador.
--  * process_date = fecha contable (corte a las 06:00): usarla como "fecha del movimiento", no ts::DATE.
--  * amount_usd se recalcula con las tasas fijas que usa el dataset (el original es nulo en 57%).
--  * fraud_risk_tier: fraud_score > umbral => fraude confirmado en el 100% de los casos.
--  * Las banderas dq_ marcan defectos conocidos; no se eliminan filas.
WITH src AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY _ingested_at DESC, _source_file DESC) AS _rn
    FROM bronze.transactions
),
merchant_map AS (  -- cada comercio tiene una sola categoría (regla determinista del generador)
    SELECT merchant_name, mode(merchant_category) AS merchant_category
    FROM bronze.transactions
    WHERE merchant_name IS NOT NULL AND merchant_category IS NOT NULL
    GROUP BY 1
),
t AS (
    SELECT
        s.transaction_id, s.customer_id, s.product_id,
        TRY_CAST(s.transaction_date AS TIMESTAMP) AS ts,
        TRY_CAST(s.process_date AS DATE)          AS process_date,
        s.transaction_type,
        TRY_CAST(s.amount AS DOUBLE)              AS amount,
        s.currency,
        TRY_CAST(s.amount_usd AS DOUBLE)          AS amount_usd_reported,
        s.channel, s.branch_id, s.merchant_name,
        COALESCE(s.merchant_category, s.transaction_category, m.merchant_category) AS merchant_category,
        s.merchant_category IS NULL AND s.merchant_name IS NOT NULL                AS dq_merchant_category_imputed,
        CASE WHEN s.transaction_country = 'Mexico' THEN 'México' ELSE s.transaction_country END AS transaction_country,
        s.transaction_city,
        s.transaction_status                      AS status,
        s.response_code,
        s.is_fraud = 'True'                       AS is_fraud,
        TRY_CAST(s.fraud_score AS DOUBLE)         AS fraud_score,
        regexp_extract(s._source_file, '([^/]+)$', 1) AS _source_file,
        s._ingested_at
    FROM src s
    LEFT JOIN merchant_map m USING (merchant_name)
    WHERE s._rn = 1
)
SELECT
    t.transaction_id, t.customer_id, t.product_id,
    p.product_type, p.card_last4,
    t.ts, t.process_date, t.transaction_type, t.amount, t.currency,
    CASE t.currency WHEN 'USD' THEN t.amount
                    WHEN 'COP' THEN t.amount / {{usd_rate_COP}}
                    WHEN 'ARS' THEN t.amount / {{usd_rate_ARS}} END AS amount_usd,
    t.amount_usd_reported,
    t.channel, t.branch_id, t.merchant_name, t.merchant_category,
    t.transaction_country, t.transaction_city,
    t.status, t.response_code,
    CASE t.response_code WHEN '00' THEN 'Aprobada'
                         WHEN '05' THEN 'No autorizada por el emisor'
                         WHEN '14' THEN 'Número de tarjeta inválido'
                         WHEN '51' THEN 'Fondos insuficientes'
                         WHEN '54' THEN 'Tarjeta vencida' END AS response_description,
    t.is_fraud, t.fraud_score,
    CASE WHEN t.fraud_score > {{fraud_score_high}} THEN 'alto'
         WHEN t.fraud_score IS NULL THEN 'sin_score'
         ELSE 'bajo' END AS fraud_risk_tier,
    -- banderas de calidad
    t.amount_usd_reported IS NULL AS dq_amount_usd_imputed,
    t.dq_merchant_category_imputed,
    t.response_code IS NULL AS dq_response_code_missing,
    COALESCE((t.response_code = '00') <> (t.status = 'Approved'), FALSE) AS dq_code_status_mismatch,
    COALESCE(t.status IN ('Pending', 'Reversed') AND t.response_code <> '00', FALSE) AS dq_decline_code_on_non_declined,
    COALESCE((t.response_code IN ('14', '54') AND NOT p.is_card)
          OR (t.response_code = '51' AND t.transaction_type = 'Deposit'), FALSE) AS dq_code_semantics_inconsistent,
    COALESCE(t.process_date < p.opening_date, FALSE) AS dq_before_product_open,
    COALESCE(p.is_card AND t.process_date > p.expiration_date, FALSE) AS dq_after_card_expiry,
    COALESCE(c.customer_status IN ('Inactive', 'Closed'), FALSE) AS dq_customer_not_active,
    COALESCE(t.process_date < CAST(c.registration_date AS DATE), FALSE) AS dq_before_customer_registration,
    COALESCE(t.transaction_country <> c.country, FALSE) AS dq_country_differs_from_customer,
    t._source_file, t._ingested_at
FROM t
LEFT JOIN silver.products p ON p.product_id = t.product_id
LEFT JOIN silver.customers c ON c.customer_id = t.customer_id
