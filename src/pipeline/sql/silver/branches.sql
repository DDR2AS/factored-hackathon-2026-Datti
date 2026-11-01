-- Sucursales: tipado y normalización de país.
SELECT
    branch_id, branch_code, branch_name, branch_type, address, city, state,
    CASE WHEN country = 'Mexico' THEN 'México' ELSE country END AS country,
    postal_code, geographic_zone, phone, email,
    TRY_CAST(opening_time AS TIME)        AS opening_time,
    TRY_CAST(closing_time AS TIME)        AS closing_time,
    has_atms = 'True'                     AS has_atms,
    TRY_CAST(atm_count AS INTEGER)        AS atm_count,
    has_teller_windows = 'True'           AS has_teller_windows,
    TRY_CAST(teller_window_count AS INTEGER) AS teller_window_count,
    TRY_CAST(latitude AS DOUBLE)          AS latitude,
    TRY_CAST(longitude AS DOUBLE)         AS longitude,
    TRY_CAST(branch_opening_date AS DATE) AS branch_opening_date,
    branch_status,
    -- coordenadas cerca de (0,0) ("isla nula"): no son una ubicación real en LATAM
    COALESCE(abs(TRY_CAST(latitude AS DOUBLE)) < 1 AND abs(TRY_CAST(longitude AS DOUBLE)) < 1, FALSE) AS dq_geo_null_island,
    _source_file, _ingested_at
FROM bronze.branches
QUALIFY ROW_NUMBER() OVER (PARTITION BY branch_id ORDER BY _ingested_at DESC) = 1
