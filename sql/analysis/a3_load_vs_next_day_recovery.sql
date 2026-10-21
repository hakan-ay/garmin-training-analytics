-- Q3: Does a hard training day change the next morning's resting heart rate and HRV?
-- Load buckets are personal tertiles of non-zero daily Banister TRIMP, so "hard" is relative to this athlete.
-- Outcomes are deviations from the personal baselines used by the models:
--   rhr_rise   = resting HR − NightSignal baseline (bpm)
--   hrv_change = overnight lnRMSSD vs its 28-day baseline, in % (Plews)
-- Concepts: date-shifted JOIN (d + 1), NTILE for personal buckets, conditional aggregation.

WITH buckets AS (
    SELECT date, trimp, NTILE(3) OVER (ORDER BY trimp) AS tertile
    FROM daily_load
    WHERE trimp > 0
),
days AS (
    SELECT
        l.date + INTERVAL 1 DAY                                   AS morning,
        CASE WHEN l.trimp = 0 THEN '0: rest'
             WHEN b.tertile = 1 THEN '1: light'
             WHEN b.tertile = 2 THEN '2: moderate'
             ELSE '3: hard' END                                  AS yesterday_load
    FROM daily_load l
    LEFT JOIN buckets b USING (date)
)
SELECT
    d.yesterday_load,
    COUNT(r.ns_rise)                                             AS n_mornings,
    ROUND(AVG(r.ns_rise), 2)                                     AS avg_rhr_rise_bpm,
    ROUND(100 * AVG(EXP(s.ln_rmssd - s.ln_rmssd_base) - 1), 1)   AS avg_hrv_change_pct
FROM days d
JOIN daily_risk r       ON r.date = d.morning
JOIN secondary_signals s ON s.date = d.morning
WHERE r.ns_rise IS NOT NULL
GROUP BY 1
ORDER BY 1;
