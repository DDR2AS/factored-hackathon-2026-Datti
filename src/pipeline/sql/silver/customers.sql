-- Clientes: tipado, ingreso normalizado a USD y banderas de calidad.
-- estimated_monthly_income viene en moneda local (MXN/COP/ARS); con tasas fijas los tres países
-- quedan con la misma mediana (~2.3k USD).
WITH c AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY _ingested_at DESC, _source_file DESC) AS _rn
    FROM bronze.customers
)
SELECT
    customer_id,
    document_number, document_type,
    sha256(document_number)                   AS document_hash,  -- para identidad simulada sin exponer el número
    first_name, last_name,
    TRY_CAST(date_of_birth AS DATE)           AS date_of_birth,
    date_diff('year', TRY_CAST(date_of_birth AS DATE), DATE '{{snapshot_date}}') AS age,
    gender,
    lower(trim(email))                        AS email,
    mobile_phone, landline_phone, address, city, state, country, postal_code,
    detected_accent, segment,
    TRY_CAST(credit_score AS INTEGER)         AS credit_score,
    TRY_CAST(estimated_monthly_income AS DOUBLE) AS monthly_income_local,
    CASE country WHEN 'México' THEN 'MXN' WHEN 'Colombia' THEN 'COP' WHEN 'Argentina' THEN 'ARS' END AS income_currency,
    TRY_CAST(estimated_monthly_income AS DOUBLE) / CASE country
        WHEN 'México' THEN {{usd_rate_MXN}} WHEN 'Colombia' THEN {{usd_rate_COP}} WHEN 'Argentina' THEN {{usd_rate_ARS}}
    END                                       AS monthly_income_usd,
    occupation, marital_status, education_level,
    TRY_CAST(registration_date AS TIMESTAMP)  AS registration_date,
    registration_branch_id,
    customer_status,
    TRY_CAST(last_updated AS TIMESTAMP)       AS last_updated,
    accepts_marketing = 'True'                AS accepts_marketing,
    -- banderas de calidad
    COALESCE((country = 'México' AND document_type NOT IN ('CURP', 'INE', 'Pasaporte'))
          OR (country = 'Colombia' AND document_type NOT IN ('CC', 'CE', 'Pasaporte'))
          OR (country = 'Argentina' AND document_type NOT IN ('DNI', 'Pasaporte')), FALSE) AS dq_document_type_invalid_for_country,
    COALESCE(email IS NOT NULL AND COUNT(*) OVER (PARTITION BY lower(trim(email))) > 1, FALSE) AS dq_email_shared,
    COALESCE(registration_branch_id NOT IN (SELECT branch_id FROM bronze.branches), FALSE) AS dq_registration_branch_missing,
    COALESCE(TRY_CAST(last_updated AS TIMESTAMP) > TIMESTAMP '{{snapshot_date}} 23:59:59', FALSE) AS dq_last_updated_future,
    regexp_extract(_source_file, '([^/]+)$', 1) AS _source_file,
    _ingested_at
FROM c
WHERE _rn = 1
