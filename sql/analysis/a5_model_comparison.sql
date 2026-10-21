-- Q5: Model comparison, in the reporting format of Alavi et al. 2022.
-- Per model: was the illness detected, how many days ahead (lead time), how many false-alarm episodes per 30 healthy days,
-- and how many of those false alarms are explained by a known confounder (training / context log).
-- For a fair comparison all models are measured over the common period (from the first day with intraday heart rate).
-- WARNING: illness_log may contain very few illnesses. This table shows how methods behave; it does not rank them.

-- Override the period with: SET VARIABLE since = DATE '...';  (src/make_figures.py does this for the full history)
WITH params AS (
    SELECT COALESCE(getvariable('since'), (SELECT MIN(date) FROM nightsignal WHERE series = 'overnight_hr')) AS since
),
alarm_days AS (
    SELECT 'NightSignal ' || series || ' / ' || baseline_kind || ' / yellow+red' AS model, date
    FROM nightsignal WHERE state >= 1
    UNION ALL
    SELECT 'NightSignal ' || series || ' / ' || baseline_kind || ' / red', date
    FROM nightsignal WHERE state = 2
    UNION ALL
    SELECT 'Ours: RHR ≥3 × 3 nights', date FROM rhr_rule_alerts
    UNION ALL
    SELECT 'Ours: 2+ signals z ≥ 1.5', date FROM alert_days
    UNION ALL
    SELECT 'HRV 7d < SWC (Plews)', date FROM secondary_signals WHERE hrv_below_normal
    UNION ALL
    SELECT 'Respiration ≥ normal+3 (Natarajan)', date FROM secondary_signals WHERE resp_elevated
),
scoped AS (
    SELECT a.* FROM alarm_days a, params p WHERE a.date >= p.since
),
models AS (
    -- models that never alert still appear in the table
    SELECT DISTINCT model FROM alarm_days
),
gaps AS (
    SELECT *, date - LAG(date) OVER (PARTITION BY model ORDER BY date) AS gap FROM scoped
),
episodes AS (
    SELECT model, MIN(date) AS first_alert, MAX(date) AS last_alert
    FROM (SELECT *, SUM(CASE WHEN gap <= 3 THEN 0 ELSE 1 END) OVER (PARTITION BY model ORDER BY date) AS ep FROM gaps)
    GROUP BY model, ep
),
labeled AS (
    SELECT e.*,
        i.onset_date,
        i.onset_date IS NOT NULL AS hits_illness,
        -- confounder: a training confounder or context event on any day of the episode
        EXISTS (SELECT 1 FROM training_confounder t
                WHERE t.date BETWEEN e.first_alert AND e.last_alert AND t.training_related)
        OR EXISTS (SELECT 1 FROM context_log c
                   WHERE e.first_alert <= c.end_date AND e.last_alert >= c.start_date) AS explained
    FROM episodes e
    LEFT JOIN illness_log i
           ON e.first_alert <= i.end_date AND e.last_alert >= i.onset_date - INTERVAL 7 DAY
),
healthy_days AS (
    -- number of days in the common period outside illness windows (onset−7 … end)
    SELECT COUNT(*) AS n FROM daily_wellness w, params p
    WHERE w.date >= p.since
      AND NOT EXISTS (SELECT 1 FROM illness_log i
                      WHERE w.date BETWEEN i.onset_date - INTERVAL 7 DAY AND i.end_date)
)
SELECT
    m.model,
    COALESCE(BOOL_OR(l.hits_illness), false)                           AS detected,
    MAX(l.onset_date - l.first_alert) FILTER (WHERE l.hits_illness)    AS lead_days,
    COUNT(*) FILTER (WHERE l.first_alert IS NOT NULL AND NOT l.hits_illness)                  AS false_episodes,
    COUNT(*) FILTER (WHERE l.first_alert IS NOT NULL AND NOT l.hits_illness AND l.explained)  AS explained_by_confounder,
    ROUND(30.0 * COUNT(*) FILTER (WHERE l.first_alert IS NOT NULL AND NOT l.hits_illness AND NOT l.explained)
          / ANY_VALUE(h.n), 2)                                         AS unexplained_per_30d
FROM models m
LEFT JOIN labeled l USING (model)
CROSS JOIN healthy_days h
GROUP BY m.model
ORDER BY detected DESC, unexplained_per_30d, lead_days DESC;
