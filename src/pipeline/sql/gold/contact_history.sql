-- Prior contacts of each customer (lane rule repeat_complainer, G2 get_prior_contacts): formal
-- complaints ('complaint') and call-center contacts ('inquiry'). Real repeat history, not the
-- is_repeat_complainer flag (a 15% coin flip in the data).
SELECT
    complaint_id                                   AS contact_id,
    customer_id,
    CAST(ts AS DATE)                               AS date,
    ts,
    reception_channel                              AS channel,
    'complaint'                                    AS contact_type,
    CASE subcategory WHEN 'Cargo no reconocido' THEN 'unrecognized_charge' WHEN 'Cobro indebido' THEN 'wrong_fee'
                     WHEN 'Problema con app' THEN 'app' WHEN 'Atención en sucursal' THEN 'branch'
                     WHEN 'Calidad de servicio' THEN 'service' END AS subcategory,
    lower(status)                                  AS status,
    'complaints'                                   AS source
FROM silver.complaints
UNION ALL
SELECT
    interaction_id,
    customer_id,
    CAST(ts AS DATE),
    ts,
    channel,
    'inquiry',
    lower(category),
    CASE WHEN resolved THEN 'resolved' ELSE 'unresolved' END,
    'call_center'
FROM silver.call_center_interactions
