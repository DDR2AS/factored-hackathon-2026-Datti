-- Server-side profile of each customer (session, G1 context, gateway get_customer_profile).
-- Data minimisation: first name only; no document, e-mail, phone or address (plan v2 section 8).
-- reference_date = the customer's latest transaction date on or before the data cut-off: "today"
-- for relative dates said in the chat ("el 12", "ayer").
WITH tx AS (
    SELECT customer_id,
           max(CAST(ts AS DATE)) FILTER (WHERE CAST(ts AS DATE) <= DATE '{{snapshot_date}}') AS reference_date,
           count(*)                                                                         AS n_txns
    FROM silver.transactions
    GROUP BY 1
),
prod AS (
    SELECT customer_id,
           mode(currency)                                         AS main_currency,
           count(*)                                               AS n_products,
           count(*) FILTER (WHERE product_status = 'Active')      AS n_active_products,
           count(*) FILTER (WHERE is_card)                        AS n_cards
    FROM silver.products
    GROUP BY 1
)
SELECT
    c.customer_id,
    c.first_name                                                   AS display_name,
    CASE c.country WHEN 'México' THEN 'MX' WHEN 'Colombia' THEN 'CO' WHEN 'Argentina' THEN 'AR' END AS country,
    'es'                                                           AS language,  -- no Portuguese in the dataset
    CASE c.country WHEN 'México' THEN 'es-MX' WHEN 'Colombia' THEN 'es-CO' ELSE 'es-AR' END AS locale,
    CASE c.country WHEN 'México' THEN 'America/Mexico_City' WHEN 'Colombia' THEN 'America/Bogota'
         ELSE 'America/Argentina/Buenos_Aires' END                 AS time_zone,
    COALESCE(prod.main_currency,
             CASE c.country WHEN 'Colombia' THEN 'COP' WHEN 'Argentina' THEN 'ARS' ELSE 'USD' END) AS currency,
    c.segment,
    c.customer_status,
    COALESCE(tx.reference_date, DATE '{{snapshot_date}}')         AS reference_date,
    COALESCE(prod.n_products, 0)                                   AS n_products,
    COALESCE(prod.n_active_products, 0)                            AS n_active_products,
    COALESCE(prod.n_cards, 0)                                      AS n_cards,
    COALESCE(tx.n_txns, 0)                                         AS n_txns,
    c.age,
    CAST(c.registration_date AS DATE)                              AS registration_date
FROM silver.customers c
LEFT JOIN tx   USING (customer_id)
LEFT JOIN prod USING (customer_id)
