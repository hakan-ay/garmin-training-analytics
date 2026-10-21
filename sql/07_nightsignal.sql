-- NightSignal (Alavi et al., "Real-time alerting system for COVID-19 and other stress events using wearable data",
-- Nature Medicine 28, 175-184 (2022)). Reference implementation: github.com/StanfordBioinformatics/wearable-infection
-- nightsignal.py (Apache-2.0). Every step below is an exact SQL equivalent of that code:
--   1. Nightly RHR: mean of step-free heart rate samples between 00:00 and 06:59, truncated to an integer (int()).
--   2. A single missing night is filled with the (truncated) mean of its two neighbours.
--   3. Baseline: median of all nightly means up to and INCLUDING that night, truncated to an integer.
--   4. Candidate: mean >= baseline + 3 → yellow candidate; >= baseline + 4 → red candidate.
--   5. Alert: 2 consecutive candidate nights → alert on the second night (red first; a red day is not also yellow).
-- Our addition (not in the original): baseline_kind = 'rolling28' → baseline = median of the last 28 nights
--   (to follow seasonal RHR drift over a long history). Which works better is measured in analysis a5.
-- Input: ns_minutes, ns_nightly_extra (06_nightsignal_input.sql).

CREATE OR REPLACE TABLE nightsignal AS
WITH nightly_raw AS (
    SELECT series, ts_local::DATE AS date, FLOOR(AVG(hr))::INTEGER AS avg_hr
    FROM ns_minutes
    WHERE HOUR(ts_local) < 7 AND steps = 0 AND hr IS NOT NULL
    GROUP BY 1, 2
    UNION ALL
    SELECT series, date, avg_hr FROM ns_nightly_extra
),
-- Step 2: the original code fills day X when X-1 and X+1 exist, but because of its loop bounds
-- it also requires X+2 or X-2 to exist (the first/last list element is skipped).
imputed AS (
    SELECT a.series, a.date + INTERVAL 1 DAY AS date, FLOOR((a.avg_hr + b.avg_hr) / 2.0)::INTEGER AS avg_hr
    FROM nightly_raw a
    JOIN nightly_raw b ON b.series = a.series AND b.date = a.date + INTERVAL 2 DAY
    WHERE NOT EXISTS (SELECT 1 FROM nightly_raw m WHERE m.series = a.series AND m.date = a.date + INTERVAL 1 DAY)
      AND (EXISTS (SELECT 1 FROM nightly_raw c WHERE c.series = a.series AND c.date = a.date + INTERVAL 3 DAY)
        OR EXISTS (SELECT 1 FROM nightly_raw c WHERE c.series = a.series AND c.date = a.date - INTERVAL 1 DAY))
),
nightly AS (
    SELECT series, date::DATE AS date, avg_hr, false AS is_imputed FROM nightly_raw
    UNION ALL
    SELECT series, date::DATE, avg_hr, true FROM imputed
),
baselines AS (
    SELECT series, date, avg_hr, is_imputed, 'expanding' AS baseline_kind,
           FLOOR(MEDIAN(avg_hr) OVER (PARTITION BY series ORDER BY date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))::INTEGER AS baseline
    FROM nightly
    UNION ALL
    SELECT series, date, avg_hr, is_imputed, 'rolling28',
           FLOOR(MEDIAN(avg_hr) OVER (PARTITION BY series ORDER BY date
                 RANGE BETWEEN INTERVAL 27 DAY PRECEDING AND CURRENT ROW))::INTEGER
    FROM nightly
),
candidates AS (
    SELECT *,
        avg_hr >= baseline + 4 AS red_candidate,
        avg_hr >= baseline + 3 AS yellow_candidate
    FROM baselines
),
alerts AS (
    SELECT c.*,
        c.red_candidate AND COALESCE(p.red_candidate, false)       AS red_alert,
        c.yellow_candidate AND COALESCE(p.yellow_candidate, false) AS yellow_pair
    FROM candidates c
    LEFT JOIN candidates p
           ON p.series = c.series AND p.baseline_kind = c.baseline_kind AND p.date = c.date - INTERVAL 1 DAY
)
SELECT
    series, baseline_kind, date, avg_hr, is_imputed, baseline,
    avg_hr - baseline                                             AS rise,
    red_candidate, yellow_candidate,
    CASE WHEN red_alert THEN 2 WHEN yellow_pair THEN 1 ELSE 0 END AS state   -- 0 green, 1 yellow, 2 red
FROM alerts
ORDER BY series, baseline_kind, date;
