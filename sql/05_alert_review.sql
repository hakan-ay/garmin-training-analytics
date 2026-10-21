-- Alert validation: every RHR alert episode gets a real-life label.
--   true_positive : the alert falls between 7 days before illness onset and the end of the illness
--   training      : heavy training load in the preceding days
--   <context>     : overlaps an event in context_log (heat, travel, post-race...)
--   unexplained   : none of the above
-- Also per illness: how many days ahead was it flagged (lead time), or was it missed?

CREATE OR REPLACE VIEW alert_episodes AS
WITH gaps AS (
    SELECT *, date - LAG(date) OVER (ORDER BY date) AS days_since_prev
    FROM rhr_rule_alerts
),
g AS (
    SELECT *, SUM(CASE WHEN days_since_prev <= 3 THEN 0 ELSE 1 END) OVER (ORDER BY date) AS episode_id
    FROM gaps
)
SELECT
    episode_id,
    MIN(date)              AS first_alert,
    MAX(date)              AS last_alert,
    COUNT(*)               AS alert_nights,
    MAX(min_rise_3n)       AS peak_rhr_rise,
    BOOL_OR(training_related) AS training_related
FROM g
GROUP BY episode_id;

CREATE OR REPLACE VIEW alert_review AS
SELECT
    e.*,
    CASE
        WHEN i.onset_date IS NOT NULL THEN 'true_positive'
        WHEN e.training_related       THEN 'training'
        WHEN c.kind IS NOT NULL       THEN c.kind
        ELSE 'unexplained'
    END                                        AS label,
    i.onset_date,
    i.onset_date - e.first_alert               AS lead_days,
    COALESCE(i.notes, c.notes)                 AS notes
FROM alert_episodes e
LEFT JOIN illness_log i
       ON e.first_alert <= i.end_date AND e.last_alert >= i.onset_date - INTERVAL 7 DAY
LEFT JOIN context_log c
       ON e.first_alert <= c.end_date AND e.last_alert >= c.start_date
-- keep one row when an episode overlaps several context events
QUALIFY ROW_NUMBER() OVER (PARTITION BY e.episode_id ORDER BY c.start_date) = 1
ORDER BY e.first_alert;

-- Per illness: did the model catch it, and how many days ahead?
CREATE OR REPLACE VIEW illness_detection AS
SELECT
    i.onset_date,
    i.end_date,
    MIN(a.date)                                AS first_alert,
    i.onset_date - MIN(a.date)                 AS lead_days,
    MIN(a.date) IS NOT NULL                    AS detected
FROM illness_log i
LEFT JOIN rhr_rule_alerts a
       ON a.date BETWEEN i.onset_date - INTERVAL 7 DAY AND i.end_date
GROUP BY i.onset_date, i.end_date;
