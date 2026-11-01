-- SYNTHETIC fee schedule (plan v2 section 8: "fee_schedule: synthetic, labelled as such").
-- The dataset has no fees table and no fee transactions. Amounts are a team ASSUMPTION per product
-- type and currency; one row per product and fee. A fee charge links to its row by merchant_id.
WITH fees(product_type, currency, fee_code, merchant_id, description, amount, frequency) AS (
    VALUES
    ('Tarjeta Crédito',  'USD', 'HANDLING', 'FEE-HANDLING', 'Cuota de manejo mensual',                   9.90, 'monthly'),
    ('Tarjeta Crédito',  'COP', 'HANDLING', 'FEE-HANDLING', 'Cuota de manejo mensual',               38900.00, 'monthly'),
    ('Tarjeta Crédito',  'ARS', 'HANDLING', 'FEE-HANDLING', 'Cuota de manejo mensual',                3500.00, 'monthly'),
    ('Tarjeta Débito',   'USD', 'CARD',     'FEE-CARD',     'Comisión tarjeta de débito',                1.50, 'monthly'),
    ('Tarjeta Débito',   'COP', 'CARD',     'FEE-CARD',     'Comisión tarjeta de débito',             5900.00, 'monthly'),
    ('Tarjeta Débito',   'ARS', 'CARD',     'FEE-CARD',     'Comisión tarjeta de débito',              520.00, 'monthly'),
    ('Cuenta Ahorro',    'USD', 'MAINT',    'FEE-MAINT',    'Comisión mantenimiento cuenta',             2.50, 'monthly'),
    ('Cuenta Ahorro',    'COP', 'MAINT',    'FEE-MAINT',    'Comisión mantenimiento cuenta',          9900.00, 'monthly'),
    ('Cuenta Ahorro',    'ARS', 'MAINT',    'FEE-MAINT',    'Comisión mantenimiento cuenta',           890.00, 'monthly'),
    ('Cuenta Corriente', 'USD', 'MAINT',    'FEE-MAINT',    'Comisión mantenimiento cuenta corriente',   5.00, 'monthly'),
    ('Cuenta Corriente', 'COP', 'MAINT',    'FEE-MAINT',    'Comisión mantenimiento cuenta corriente', 19900.00, 'monthly'),
    ('Cuenta Corriente', 'ARS', 'MAINT',    'FEE-MAINT',    'Comisión mantenimiento cuenta corriente',  1790.00, 'monthly')
)
SELECT
    p.product_id,
    p.customer_id,
    cp.name                                   AS product_name,
    p.currency,
    f.fee_code,
    f.merchant_id,
    f.description,
    CAST(f.amount AS DECIMAL(18, 2))          AS amount,
    f.frequency,
    TRUE                                      AS synthetic
FROM silver.products p
JOIN fees f ON f.product_type = p.product_type AND f.currency = p.currency
JOIN gold.customer_products cp ON cp.product_id = p.product_id
WHERE p.product_status IN ('Active', 'Blocked')
