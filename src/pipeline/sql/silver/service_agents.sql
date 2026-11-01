-- Agentes: tipado, idiomas como banderas (para derivar casos en portugués) y FK rotas marcadas.
SELECT
    agent_id, employee_code, first_name, last_name, email, phone, native_accent,
    CASE WHEN country_of_origin = 'Mexico' THEN 'México' ELSE country_of_origin END AS country_of_origin,
    assigned_branch_id, agent_type, experience_level, languages,
    languages LIKE '%portugués%'                   AS speaks_portuguese,
    languages LIKE '%inglés%'                      AS speaks_english,
    specialty,
    TRY_CAST(hire_date AS DATE)                    AS hire_date,
    TRY_CAST(avg_csat AS DOUBLE)                   AS avg_csat,  -- no coincide con las encuestas reales
    TRY_CAST(total_monthly_interactions AS INTEGER) AS total_monthly_interactions,
    agent_status,
    agent_status = 'Active'                        AS is_active,
    work_shift,
    COALESCE(assigned_branch_id NOT IN (SELECT branch_id FROM bronze.branches), FALSE) AS dq_assigned_branch_missing,
    COUNT(*) OVER (PARTITION BY employee_code) > 1 AS dq_employee_code_duplicated,
    COUNT(*) OVER (PARTITION BY lower(email)) > 1  AS dq_email_duplicated,
    _source_file, _ingested_at
FROM bronze.service_agents
QUALIFY ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY _ingested_at DESC) = 1
