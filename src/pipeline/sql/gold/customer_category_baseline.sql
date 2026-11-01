-- Per customer and merchant category: count, median and p95 in USD (same window as
-- customer_baseline). M3 "amount vs the customer's median and p95 for this category".
WITH base AS (
    SELECT t.customer_id, t.merchant_category,
           CAST(COALESCE(t.amount_usd, CASE WHEN t.currency = 'USD' THEN t.amount END) AS DOUBLE) AS a_usd
    FROM gold.transactions_enriched t
    JOIN gold.customer_profile cp USING (customer_id)
    WHERE t.txn_type IN ('purchase', 'payment')
      AND t.status IN ('approved', 'pending')
      AND t.local_date <= cp.reference_date - INTERVAL {{baseline_gap_days}} DAY
      AND t.local_date >  cp.reference_date - INTERVAL ({{baseline_gap_days}} + {{baseline_window_days}}) DAY
)
SELECT customer_id, merchant_category, count(*) AS n,
       round(median(a_usd), 2) AS median_usd, round(quantile_cont(a_usd, 0.95), 2) AS p95_usd
FROM base
GROUP BY 1, 2
