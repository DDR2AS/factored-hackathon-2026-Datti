-- Productos: tipado, atributos derivados (tarjeta, crédito, últimos 4 dígitos) y banderas de calidad.
-- last_transaction_date y current_balance NO se derivan de las transacciones (ver docs/hallazgos.md).
WITH p AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY _ingested_at DESC, _source_file DESC) AS _rn
    FROM bronze.products
)
SELECT
    product_id, customer_id, product_type,
    product_type IN ('Tarjeta Crédito', 'Tarjeta Débito')                         AS is_card,
    product_type IN ('Tarjeta Crédito', 'Préstamo Personal', 'Préstamo Hipotecario') AS is_credit,
    product_number,
    CASE WHEN product_type IN ('Tarjeta Crédito', 'Tarjeta Débito') THEN right(product_number, 4) END AS card_last4,
    currency,
    TRY_CAST(current_balance AS DOUBLE)       AS balance,
    TRY_CAST(credit_limit AS DOUBLE)          AS credit_limit,
    TRY_CAST(current_balance AS DOUBLE) / NULLIF(TRY_CAST(credit_limit AS DOUBLE), 0) AS credit_utilization,
    TRY_CAST(interest_rate AS DOUBLE)         AS interest_rate,
    TRY_CAST(opening_date AS DATE)            AS opening_date,
    TRY_CAST(expiration_date AS DATE)         AS expiration_date,
    COALESCE(TRY_CAST(expiration_date AS DATE) < DATE '{{snapshot_date}}', FALSE) AS is_expired,
    opening_branch_id, product_status, opening_channel,
    has_linked_app = 'True'                   AS has_linked_app,
    TRY_CAST(TRY_CAST(days_past_due AS DOUBLE) AS INTEGER) AS days_past_due,
    TRY_CAST(last_transaction_date AS TIMESTAMP) AS last_transaction_date_reported,
    TRY_CAST(last_updated AS TIMESTAMP)       AS last_updated,
    COALESCE(TRY_CAST(expiration_date AS DATE) < DATE '{{snapshot_date}}' AND product_status = 'Active', FALSE) AS dq_expired_but_active,
    COUNT(*) OVER (PARTITION BY product_number) > 1 AS dq_product_number_duplicated,
    COALESCE(TRY_CAST(last_updated AS TIMESTAMP) > TIMESTAMP '{{snapshot_date}} 23:59:59', FALSE) AS dq_last_updated_future,
    regexp_extract(_source_file, '([^/]+)$', 1) AS _source_file,
    _ingested_at
FROM p
WHERE _rn = 1
