-- Digital-banking sessions per customer (G2 get_digital_sessions, M3 "login or new device shortly
-- before the charge"). first_seen_device: the customer's first session with this channel,
-- platform and app version. Anonymous events (no customer_id) are left out.
WITH s AS (
    SELECT customer_id, session_id,
           min(ts)                                       AS started_at,
           max(ts)                                       AS ended_at,
           any_value(channel)                            AS channel,
           any_value(platform)                           AS platform,
           any_value(app_version)                        AS app_version,
           any_value(ip_country)                         AS ip_country,
           count(*)                                      AS n_events,
           bool_or(event_type = 'Login')                 AS has_login,
           bool_or(event_type = 'Error')                 AS has_error,
           bool_or(event_category = 'Transaction')       AS has_transaction_page
    FROM silver.digital_events
    WHERE customer_id IS NOT NULL
    GROUP BY 1, 2
)
SELECT *,
       ROW_NUMBER() OVER (PARTITION BY customer_id, channel, COALESCE(platform, ''), COALESCE(app_version, '')
                          ORDER BY started_at, session_id) = 1 AS first_seen_device
FROM s
