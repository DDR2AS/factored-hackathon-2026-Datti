-- Transactions as the customer sees them on the statement (gateway, M1, M3, G2).
--  * local_date / local_time: calendar date and HH:MM of ts (local time). process_date stays as the
--    accounting date, date(ts - 6 h): don't compare it with what the customer says.
--  * merchant_id: the dataset has none; stable id from the normalised name. Movements without a
--    merchant get a statement descriptor ("RETIRO ATM", "TRANSFERENCIA APP").
--  * is_fraud is a LABEL for M3 training and evaluation; the gateway never serves it.
--  * origin: 'dataset' here; synthetic fee charges and planted evaluation rows carry other values.
--  * card_last4: the card's last 4 digits; for accounts and loans, the last 4 of the product number
--    (what the statement shows), so every charge can be shown as "•••• 1234".
SELECT
    t.transaction_id                                         AS txn_id,
    t.customer_id,
    t.product_id,
    t.product_type,
    COALESCE(t.card_last4, cp.number_last4)                  AS card_last4,
    CAST(t.ts AS DATE)                                       AS local_date,
    strftime(t.ts, '%H:%M')                                  AS local_time,
    t.ts,
    t.process_date,
    t.transaction_type,
    lower(t.transaction_type)                                AS txn_type,
    CAST(NULL AS VARCHAR)                                    AS reversal_of,  -- the dataset links no reversal to a charge
    CAST(round(t.amount, 2) AS DECIMAL(18, 2))               AS amount,
    t.currency,
    CAST(round(t.amount_usd, 2) AS DECIMAL(18, 2))           AS amount_usd,
    CASE WHEN t.merchant_name IS NOT NULL
         THEN 'M-' || upper(regexp_replace(strip_accents(t.merchant_name), '[^A-Za-z0-9]+', '', 'g'))
         ELSE 'D-' || upper(t.transaction_type) || '-' || upper(t.channel) END AS merchant_id,
    CASE WHEN t.merchant_name IS NOT NULL THEN upper(strip_accents(t.merchant_name))
         WHEN t.transaction_type = 'Withdrawal' THEN 'RETIRO ' || upper(t.channel)
         WHEN t.transaction_type = 'Transfer'   THEN 'TRANSFERENCIA ' || upper(t.channel)
         WHEN t.transaction_type = 'Payment'    THEN 'PAGO ' || upper(t.channel)
         WHEN t.transaction_type = 'Deposit'    THEN 'DEPOSITO ' || upper(t.channel)
         ELSE 'AJUSTE' END                                   AS merchant_name,
    CASE WHEN t.merchant_category IS NOT NULL THEN lower(t.merchant_category)
         WHEN t.transaction_type IN ('Withdrawal', 'Deposit') THEN 'cash'
         WHEN t.transaction_type = 'Transfer' THEN 'transfer'
         WHEN t.transaction_type = 'Payment'  THEN 'payment'
         ELSE 'adjustment' END                               AS merchant_category,
    t.channel,
    lower(t.status)                                          AS status,  -- approved | declined | pending | reversed
    t.response_code,
    t.response_description,
    t.fraud_score,
    t.fraud_risk_tier,
    t.is_fraud,
    CASE t.transaction_country WHEN 'México' THEN 'MX' WHEN 'Colombia' THEN 'CO' WHEN 'Argentina' THEN 'AR'
         WHEN 'Brazil' THEN 'BR' WHEN 'USA' THEN 'US' ELSE t.transaction_country END AS txn_country,
    t.transaction_city                                       AS txn_city,
    COALESCE(t.dq_country_differs_from_customer, FALSE)      AS is_foreign,
    NOT (t.dq_code_semantics_inconsistent OR t.dq_decline_code_on_non_declined
         OR (t.status = 'Declined' AND t.dq_response_code_missing)) AS explanation_reliable,
    t.dq_before_product_open,
    t.dq_after_card_expiry,
    t.dq_customer_not_active,
    t.dq_amount_usd_imputed,
    'dataset'                                                AS origin
FROM silver.transactions t
LEFT JOIN gold.customer_products cp ON cp.product_id = t.product_id
