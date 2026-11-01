-- Tipos de cambio diarios (12 pares entre USD/MXN/COP/ARS).
-- Ojo: amount_usd de transacciones NO usa esta tabla (usa tasas fijas); se conserva como referencia.
SELECT
    TRY_CAST(date AS DATE)            AS rate_date,
    source_currency,
    target_currency,
    TRY_CAST(exchange_rate AS DOUBLE) AS exchange_rate,
    TRY_CAST(buy_rate AS DOUBLE)      AS buy_rate,
    TRY_CAST(sell_rate AS DOUBLE)     AS sell_rate,
    source                            AS rate_source,
    COALESCE(NOT (TRY_CAST(buy_rate AS DOUBLE) <= TRY_CAST(exchange_rate AS DOUBLE)
                  AND TRY_CAST(exchange_rate AS DOUBLE) <= TRY_CAST(sell_rate AS DOUBLE)), FALSE) AS dq_spread_inconsistent,
    _source_file, _ingested_at
FROM bronze.daily_exchange_rates
QUALIFY ROW_NUMBER() OVER (PARTITION BY date, source_currency, target_currency ORDER BY _ingested_at DESC) = 1
