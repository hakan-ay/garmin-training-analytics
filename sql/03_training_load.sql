-- Training load, using published formulas.
--
-- Banister TRIMP (Banister 1991; Morton, Fitz-Clarke & Banister 1990):
--   TRIMP = duration_min × ΔHR × a·e^(b·ΔHR),  ΔHR = (avgHR − HRrest) / (HRmax − HRrest)
--   male a=0.64 b=1.92, female a=0.86 b=1.67 (weighting derived from the blood lactate response curve)
-- Edwards TRIMP (Edwards 1993): Σ zone_min × weight(1..5), zones at 50/60/70/80/90 % of HRmax.
--   Only valid for sports whose Garmin zones use the HR_MAX method (running zones are lactate-threshold based).
-- Foster monotony / strain (Foster 1998, MSSE 30:1164):
--   monotony = mean / SD of daily load over 7 days (rest days count as 0),  strain = weekly load × monotony
-- EWMA acute:chronic ratio (Williams et al. 2017, BJSM): λ = 2/(N+1), N_acute = 7, N_chronic = 28.
--   EWMA is used because the rolling ACWR is criticised for mathematical coupling (Lolli 2019; Impellizzeri 2020);
--   the ability of ACWR to predict injury is still debated in the literature.

CREATE OR REPLACE TABLE activity_trimp AS
WITH p AS (
    SELECT
        hr_max,
        hr_rest_setting,
        CASE WHEN sex = 'FEMALE' THEN 0.86 ELSE 0.64 END AS a,
        CASE WHEN sex = 'FEMALE' THEN 1.67 ELSE 1.92 END AS b,
        hrmax_zone_sports
    FROM stg_profile
),
x AS (
    SELECT
        act.activity_id, act.start_time, act.sport_type, act.sport_group,
        act.duration_min, act.avg_hr,
        p.hr_max,
        -- HRrest: that morning's measured resting heart rate; otherwise the value from the Garmin profile
        COALESCE(w.resting_hr, p.hr_rest_setting)                       AS hr_rest,
        p.a, p.b,
        act.hr_zone1_s, act.hr_zone2_s, act.hr_zone3_s, act.hr_zone4_s, act.hr_zone5_s,
        -- Edwards weights are meaningless when running zones are lactate-threshold based
        NOT (act.sport_group = 'running' AND NOT list_contains(p.hrmax_zone_sports, 'RUNNING')) AS edwards_valid
    FROM activities act
    CROSS JOIN p
    LEFT JOIN daily_wellness w ON w.date = act.start_time::DATE
)
SELECT
    activity_id, start_time, sport_type, sport_group, duration_min, avg_hr, hr_rest, hr_max,
    LEAST(GREATEST((avg_hr - hr_rest) / (hr_max - hr_rest), 0), 1)         AS delta_hr,
    duration_min * LEAST(GREATEST((avg_hr - hr_rest) / (hr_max - hr_rest), 0), 1)
        * a * EXP(b * LEAST(GREATEST((avg_hr - hr_rest) / (hr_max - hr_rest), 0), 1))
                                                                         AS trimp_banister,
    CASE WHEN edwards_valid THEN
        (COALESCE(hr_zone1_s, 0) * 1 + COALESCE(hr_zone2_s, 0) * 2 + COALESCE(hr_zone3_s, 0) * 3
       + COALESCE(hr_zone4_s, 0) * 4 + COALESCE(hr_zone5_s, 0) * 5) / 60.0
    END                                                                  AS trimp_edwards
FROM x;

-- Daily series: rest days are included with 0 load (the monotony calculation requires this)
CREATE OR REPLACE TABLE daily_load AS
WITH RECURSIVE spine AS (
    SELECT UNNEST(generate_series(
        (SELECT MIN(start_time)::DATE FROM activities),
        (SELECT GREATEST(MAX(start_time)::DATE, MAX(w.date)) FROM activities, daily_wellness w),
        INTERVAL 1 DAY))::DATE AS date
),
daily AS (
    SELECT s.date, COALESCE(SUM(t.trimp_banister), 0) AS trimp
    FROM spine s
    LEFT JOIN activity_trimp t ON t.start_time::DATE = s.date
    GROUP BY s.date
),
foster AS (
    SELECT
        date, trimp,
        SUM(trimp)         OVER w7 AS weekly_load,
        AVG(trimp)         OVER w7 AS mean_7d,
        STDDEV_POP(trimp)  OVER w7 AS sd_7d,
        ROW_NUMBER() OVER (ORDER BY date) AS n
    FROM daily
    WINDOW w7 AS (ORDER BY date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
),
ewma AS (
    -- Recursive EWMA: today = λ·load + (1−λ)·yesterday; initialised with the first day's load
    SELECT n, date, trimp, trimp AS acute, trimp AS chronic FROM foster WHERE n = 1
    UNION ALL
    SELECT f.n, f.date, f.trimp,
           (2.0 / 8)  * f.trimp + (1 - 2.0 / 8)  * e.acute,
           (2.0 / 29) * f.trimp + (1 - 2.0 / 29) * e.chronic
    FROM foster f JOIN ewma e ON f.n = e.n + 1
)
SELECT
    f.date, f.trimp, f.weekly_load,
    CASE WHEN f.sd_7d > 0 THEN f.mean_7d / f.sd_7d END                   AS monotony,
    CASE WHEN f.sd_7d > 0 THEN f.weekly_load * f.mean_7d / f.sd_7d END   AS strain,
    e.acute  AS ewma_acute,
    e.chronic AS ewma_chronic,
    -- the chronic component has not settled during the first 28 days
    CASE WHEN f.n > 28 AND e.chronic > 0 THEN e.acute / e.chronic END    AS ewma_acwr
FROM foster f
JOIN ewma e USING (n)
ORDER BY f.date;

-- Training confounder (single definition). The wellness date is the morning of waking up, so yesterday's load is used.
--   Personal threshold: the 90th percentile of strain up to that day (no look-ahead; needs at least 28 days of history).
CREATE OR REPLACE TABLE training_confounder AS
WITH d AS (
    SELECT
        date + INTERVAL 1 DAY AS morning,
        strain, ewma_acwr,
        QUANTILE_CONT(strain, 0.9) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS strain_p90,
        COUNT(strain) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)             AS n_hist
    FROM daily_load
)
SELECT
    morning::DATE                                              AS date,
    strain                                                     AS strain_yesterday,
    strain_p90,
    ewma_acwr                                                  AS ewma_acwr_yesterday,
    COALESCE(n_hist >= 28 AND strain > strain_p90, false)
      OR COALESCE(ewma_acwr > 1.5, false)                      AS training_related
FROM d;
