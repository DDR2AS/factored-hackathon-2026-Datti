-- Usual behaviour of each customer (gateway get_customer_baseline, M3 features, G2): purchases and
-- payments older than {{baseline_gap_days}} days before the reference date, in a
-- {{baseline_window_days}}-day window, so the disputed recent charges never define "normal".
-- Amount statistics in the customer's main currency (rows in that currency) and in USD (all rows).
WITH base AS (
    SELECT t.*, cp.reference_date, cp.currency AS main_currency,
           CAST(COALESCE(t.amount_usd, CASE WHEN t.currency = 'USD' THEN t.amount END) AS DOUBLE) AS a_usd,
           CAST(substr(t.local_time, 1, 2) AS INTEGER) AS hh
    FROM gold.transactions_enriched t
    JOIN gold.customer_profile cp USING (customer_id)
    WHERE t.txn_type IN ('purchase', 'payment')
      AND t.status IN ('approved', 'pending')
      AND t.local_date <= cp.reference_date - INTERVAL {{baseline_gap_days}} DAY
      AND t.local_date >  cp.reference_date - INTERVAL ({{baseline_gap_days}} + {{baseline_window_days}}) DAY
),
cat AS (SELECT customer_id, merchant_category, count(*) AS n FROM base GROUP BY 1, 2),
topcat AS (
    SELECT customer_id, list(merchant_category ORDER BY n DESC, merchant_category)[1:3] AS top_categories
    FROM cat GROUP BY 1
),
mer AS (SELECT customer_id, merchant_id, any_value(merchant_name) AS merchant_name, count(*) AS n FROM base GROUP BY 1, 2),
topmer AS (
    SELECT customer_id,
           list(merchant_name ORDER BY n DESC, merchant_name)[1:3] AS top_merchants,
           list(merchant_id ORDER BY merchant_id)                  AS known_merchant_ids
    FROM mer GROUP BY 1
),
agg AS (
    SELECT customer_id,
           min(local_date)                                                           AS window_from,
           count(*)                                                                  AS n_txns,
           median(CAST(amount AS DOUBLE)) FILTER (WHERE currency = main_currency)    AS median_amount,
           quantile_cont(CAST(amount AS DOUBLE), 0.9) FILTER (WHERE currency = main_currency) AS p90_amount,
           median(a_usd)                                                             AS median_amount_usd,
           quantile_cont(a_usd, 0.95)                                                AS p95_amount_usd,
           quantile_disc(hh, 0.1)                                                    AS usual_hour_from,
           quantile_disc(hh, 0.9)                                                    AS usual_hour_to,
           list(DISTINCT channel ORDER BY channel)                                   AS usual_channels,
           list(DISTINCT txn_country ORDER BY txn_country)                           AS usual_countries
    FROM base GROUP BY 1
)
SELECT
    cp.customer_id,
    agg.window_from,
    CAST(cp.reference_date - INTERVAL {{baseline_gap_days}} DAY AS DATE)            AS window_to,
    COALESCE(agg.n_txns, 0)                                                          AS n_txns,
    cp.currency,
    CAST(round(agg.median_amount, 2) AS DECIMAL(18, 2))                             AS median_amount,
    CAST(round(agg.p90_amount, 2) AS DECIMAL(18, 2))                                AS p90_amount,
    round(agg.median_amount_usd, 2)                                                  AS median_amount_usd,
    round(agg.p95_amount_usd, 2)                                                     AS p95_amount_usd,
    COALESCE(topcat.top_categories, [])                                              AS top_categories,
    COALESCE(topmer.top_merchants, [])                                               AS top_merchants,
    COALESCE(topmer.known_merchant_ids, [])                                          AS known_merchant_ids,
    agg.usual_hour_from,
    agg.usual_hour_to,
    COALESCE(agg.usual_channels, [])                                                 AS usual_channels,
    COALESCE(agg.usual_countries, [])                                                AS usual_countries
FROM gold.customer_profile cp
LEFT JOIN agg    USING (customer_id)
LEFT JOIN topcat USING (customer_id)
LEFT JOIN topmer USING (customer_id)
