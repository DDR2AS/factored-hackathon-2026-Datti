-- Historical complaint times per complaint type (promise clock, evaluation baseline B0).
-- complaint_type uses the v2 names; 'all' is every complaint.
WITH k AS (
    SELECT *,
           CASE subcategory WHEN 'Cargo no reconocido' THEN 'unrecognized_charge' WHEN 'Cobro indebido' THEN 'wrong_fee'
                            WHEN 'Problema con app' THEN 'app' WHEN 'Atención en sucursal' THEN 'branch'
                            WHEN 'Calidad de servicio' THEN 'service' ELSE 'other' END AS complaint_type,
           date_diff('minute', ts, first_response_at) / 60.0 AS first_response_hours
    FROM silver.complaints
)
SELECT
    COALESCE(complaint_type, 'all')                                      AS complaint_type,
    count(*)                                                             AS complaints,
    median(resolution_days)                                              AS median_resolution_days,
    quantile_cont(resolution_days, 0.9)                                  AS p90_resolution_days,
    round(avg(CASE WHEN sla_breached THEN 1.0 ELSE 0 END), 4)            AS sla_breach_rate,
    round(median(first_response_hours), 1)                               AS median_first_response_hours,
    round(quantile_cont(first_response_hours, 0.9), 1)                   AS p90_first_response_hours,
    count(*) FILTER (WHERE assigned_agent_id IS NULL)                    AS never_assigned,
    round(avg(CASE WHEN claimed_amount IS NOT NULL THEN 1.0 ELSE 0 END), 4) AS with_amount_rate,
    round(avg(CASE WHEN is_open THEN 1.0 ELSE 0 END), 4)                 AS open_rate,
    round(avg(CASE WHEN affected_product_id IS NULL THEN NULL
                   WHEN dq_affected_product_not_owned THEN 1.0 ELSE 0 END), 4) AS affected_product_foreign_rate
FROM k
GROUP BY ROLLUP (complaint_type)
