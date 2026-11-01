-- Aggregates per merchant (G2 get_merchant_stats). Aggregates only: never another customer's records.
-- fraud_rate uses the dataset label is_fraud; dispute_rate is unknown (complaints are not linked to
-- merchants in the data) and stays null.
WITH t AS (
    SELECT *, CAST(COALESCE(amount_usd, CASE WHEN currency = 'USD' THEN amount END) AS DOUBLE) AS a_usd
    FROM gold.transactions_enriched
    WHERE merchant_id LIKE 'M-%'
)
SELECT
    merchant_id,
    any_value(merchant_name)                                                  AS merchant_name,
    mode(merchant_category)                                                   AS merchant_category,
    count(*)                                                                  AS n_txns,
    count(DISTINCT customer_id)                                               AS n_customers,
    round(avg(CASE WHEN is_fraud THEN 1.0 ELSE 0 END), 5)                     AS fraud_rate,
    round(avg(CASE WHEN fraud_score > {{fraud_score_high}} THEN 1.0 ELSE 0 END), 5) AS high_score_rate,
    round(avg(CASE WHEN status = 'declined' THEN 1.0 ELSE 0 END), 4)          AS decline_rate,
    round(avg(CASE WHEN status = 'reversed' THEN 1.0 ELSE 0 END), 4)          AS reversal_rate,
    CAST(NULL AS DOUBLE)                                                      AS dispute_rate,
    round(median(a_usd), 2)                                                   AS median_amount_usd,
    round(quantile_cont(a_usd, 0.95), 2)                                      AS p95_amount_usd
FROM t
GROUP BY merchant_id
