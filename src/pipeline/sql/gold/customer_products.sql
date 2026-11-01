-- Products of each customer (gateway get_cards / profile products, M1 card last 4).
-- Never the full product number: only its last 4 digits. card_id exists only for cards.
SELECT
    p.product_id,
    p.customer_id,
    CASE p.product_type
        WHEN 'Tarjeta Crédito' THEN 'credit_card'   WHEN 'Tarjeta Débito' THEN 'debit_card'
        WHEN 'Cuenta Ahorro' THEN 'savings_account' WHEN 'Cuenta Corriente' THEN 'checking_account'
        WHEN 'Préstamo Personal' THEN 'personal_loan' WHEN 'Préstamo Hipotecario' THEN 'mortgage'
        WHEN 'Inversión' THEN 'investment'          WHEN 'Seguro' THEN 'insurance'
        ELSE 'other' END                                   AS kind,
    p.product_type,
    p.product_type || ' •••• ' || right(p.product_number, 4) AS name,
    p.currency,
    p.card_last4,
    right(p.product_number, 4)                             AS number_last4,
    CASE WHEN p.is_card THEN 'K-' || p.product_id END      AS card_id,
    lower(p.product_status)                                AS status,   -- active | blocked | closed | suspended
    p.is_card,
    p.is_credit,
    p.opening_date,
    p.expiration_date,
    p.is_expired,
    p.has_linked_app,
    p.dq_expired_but_active,
    p.dq_product_number_duplicated
FROM silver.products p
