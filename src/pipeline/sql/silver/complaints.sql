-- Reclamos (PQR): tipado, subcategoría imputada y banderas de calidad.
--  * subcategory es una función 1:1 de category: se completa el 10% nulo.
--  * affected_product_id pertenece a OTRO cliente en ~100%: no usarlo sin validar propiedad.
--  * claimed_amount va de 50 a 5000 en cualquier moneda: en COP/ARS no tiene escala real.
WITH k AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY complaint_id ORDER BY _ingested_at DESC, _source_file DESC) AS _rn
    FROM bronze.complaints
),
sub_map AS (
    SELECT category, mode(subcategory) AS subcategory
    FROM bronze.complaints WHERE subcategory IS NOT NULL GROUP BY 1
)
SELECT
    k.complaint_id,
    TRY_CAST(k.creation_date AS TIMESTAMP)   AS ts,
    TRY_CAST(k.process_date AS DATE)         AS process_date,
    k.customer_id, k.case_type, k.category,
    COALESCE(k.subcategory, m.subcategory)   AS subcategory,
    k.subcategory IS NULL                    AS dq_subcategory_imputed,
    k.reception_channel, k.affected_product_id, k.related_branch_id, k.origin_interaction_id,
    k.description,
    TRY_CAST(k.claimed_amount AS DOUBLE)     AS claimed_amount,
    k.currency                               AS claimed_currency,
    TRY_CAST(k.claimed_amount AS DOUBLE) / CASE k.currency WHEN 'USD' THEN 1 WHEN 'COP' THEN {{usd_rate_COP}}
        WHEN 'ARS' THEN {{usd_rate_ARS}} WHEN 'MXN' THEN {{usd_rate_MXN}} END AS claimed_amount_usd,
    k.priority, k.status,
    k.status IN ('Open', 'In Process', 'Escalated') AS is_open,
    k.assigned_agent_id,
    TRY_CAST(k.assignment_date AS TIMESTAMP)       AS assigned_at,
    TRY_CAST(k.first_response_date AS TIMESTAMP)   AS first_response_at,
    TRY_CAST(k.resolution_date AS TIMESTAMP)       AS resolved_at,
    TRY_CAST(k.closing_date AS TIMESTAMP)          AS closed_at,
    k.sla_breached = 'True'                        AS sla_breached,
    TRY_CAST(TRY_CAST(k.resolution_days AS DOUBLE) AS INTEGER) AS resolution_days,
    k.resolution,
    TRY_CAST(k.compensation_granted AS DOUBLE)     AS compensation_granted,
    TRY_CAST(TRY_CAST(k.resolution_satisfaction AS DOUBLE) AS INTEGER) AS resolution_satisfaction,
    k.is_repeat_complainer = 'True'                AS is_repeat_complainer,
    COALESCE(k.affected_product_id IS NOT NULL AND p.customer_id IS DISTINCT FROM k.customer_id, FALSE) AS dq_affected_product_not_owned,
    k.origin_interaction_id IS NULL                AS dq_origin_interaction_missing,
    COALESCE(k.currency IN ('COP', 'ARS'), FALSE)  AS dq_claimed_amount_scale_suspect,
    regexp_extract(k._source_file, '([^/]+)$', 1) AS _source_file,
    k._ingested_at
FROM k
LEFT JOIN sub_map m USING (category)
LEFT JOIN silver.products p ON p.product_id = k.affected_product_id
WHERE k._rn = 1
