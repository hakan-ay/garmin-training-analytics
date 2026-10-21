-- Early-warning signals for illness / lowered immunity.
--
-- Idea: when the body starts fighting an infection, overnight resting heart rate and sleeping heart rate rise,
--   HRV drops and respiration rate increases, often 1-3 days before the person feels sick.
-- Method: personal baseline per metric = mean and SD of the previous 28 days
--   (today excluded, so an illness does not contaminate its own baseline). Deviation is measured as a z-score.
-- Confounders: heavy training and high altitude produce the same signal. The single definition of the training
--   confounder is the training_confounder table in 03_training_load.sql (Foster strain + EWMA ACWR).
-- NOTE: this z-score model was our first approach; it is kept for comparison with the published method in 07_nightsignal.sql.

CREATE OR REPLACE TABLE wellness_baseline AS
WITH base AS (
    SELECT
        w.*,
        COALESCE(l.trimp, 0) AS load_yesterday,
        (SELECT COALESCE(SUM(x.trimp), 0) FROM daily_load x
          WHERE x.date BETWEEN w.date - INTERVAL 3 DAY AND w.date - INTERVAL 1 DAY) AS load_last3d,
        COALESCE(tc.training_related, false) AS training_related,
        COUNT(w.resting_hr)  OVER past AS n_baseline_days,
        AVG(w.resting_hr)    OVER past AS rhr_mu,       STDDEV_SAMP(w.resting_hr)   OVER past AS rhr_sd,
        AVG(w.sleep_hr_avg)  OVER past AS shr_mu,       STDDEV_SAMP(w.sleep_hr_avg) OVER past AS shr_sd,
        AVG(w.hrv_ms)        OVER past AS hrv_mu,       STDDEV_SAMP(w.hrv_ms)       OVER past AS hrv_sd,
        AVG(w.resp_avg)      OVER past AS resp_mu,      STDDEV_SAMP(w.resp_avg)     OVER past AS resp_sd
    FROM daily_wellness w
    -- wellness "date" = morning of waking up, so yesterday's training affects that night
    LEFT JOIN daily_load l ON l.date = w.date - INTERVAL 1 DAY
    LEFT JOIN training_confounder tc ON tc.date = w.date
    WINDOW past AS (ORDER BY w.date RANGE BETWEEN INTERVAL 28 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING)
),
z AS (
    SELECT
        *,
        -- floor the SD (e.g. a perfectly stable RHR) so the z-score does not explode
        (resting_hr   - rhr_mu)  / GREATEST(rhr_sd, 1.0)  AS rhr_z,
        (sleep_hr_avg - shr_mu)  / GREATEST(shr_sd, 1.0)  AS sleep_hr_z,
        (hrv_ms       - hrv_mu)  / GREATEST(hrv_sd, 2.0)  AS hrv_z,
        (resp_avg     - resp_mu) / GREATEST(resp_sd, 0.3) AS resp_z
    FROM base
)
SELECT
    *,
    -- Combined "body strain score": heart rate up, HRV down, respiration up = positive.
    -- A missing metric counts as 0 (neutral); no score without at least 14 days of baseline.
    CASE WHEN n_baseline_days >= 14 THEN
        COALESCE(rhr_z, 0) + COALESCE(sleep_hr_z, 0) - COALESCE(hrv_z, 0) + COALESCE(resp_z, 0)
    END                                                         AS strain_score,
    -- How many signals are abnormal at once? One may be noise, 2+ is meaningful.
    (COALESCE(rhr_z, 0)      >=  1.5)::INT
  + (COALESCE(sleep_hr_z, 0) >=  1.5)::INT
  + (COALESCE(hrv_z, 0)      <= -1.5)::INT
  + (COALESCE(resp_z, 0)     >=  1.5)::INT                      AS n_flags
FROM z
ORDER BY date;

-- Alert days: 2+ abnormal signals (or a high combined score). explained_by_training = heavy recent load.
CREATE OR REPLACE VIEW alert_days AS
SELECT
    date, strain_score, n_flags, load_yesterday, load_last3d,
    resting_hr, ROUND(rhr_mu, 1) AS rhr_baseline,
    ROUND(sleep_hr_avg, 1) AS sleep_hr_avg, ROUND(shr_mu, 1) AS sleep_hr_baseline,
    hrv_ms, ROUND(hrv_mu, 1) AS hrv_baseline,
    resp_avg, ROUND(resp_mu, 1) AS resp_baseline,
    training_related AS explained_by_training
FROM wellness_baseline
WHERE n_baseline_days >= 14 AND (n_flags >= 2 OR strain_score >= 5);

-- Group consecutive alert days into one "episode" (gap <= 2 days)
CREATE OR REPLACE VIEW candidate_episodes AS
WITH gaps AS (
    SELECT *, date - LAG(date) OVER (ORDER BY date) AS days_since_prev
    FROM alert_days
),
g AS (
    SELECT *,
        SUM(CASE WHEN days_since_prev <= 3 THEN 0 ELSE 1 END) OVER (ORDER BY date) AS episode_id
    FROM gaps
)
SELECT
    episode_id,
    MIN(date)                           AS first_alert,
    MAX(date)                           AS last_alert,
    COUNT(*)                            AS alert_days,
    ROUND(MAX(strain_score), 1)         AS peak_strain,
    MAX(resting_hr - rhr_baseline)      AS max_rhr_rise,
    MIN(hrv_ms - hrv_baseline)          AS max_hrv_drop,
    BOOL_OR(explained_by_training)      AS training_related,
    -- a one-day spike is usually noise (late night, alcohol, stress); a deviation lasting 2+ days is a strong candidate
    CASE WHEN COUNT(*) >= 2 THEN 'strong' ELSE 'weak' END AS strength
FROM g
GROUP BY episode_id
ORDER BY first_alert;

-- Simple, explainable alternative rule (similar to the Stanford/Snyder lab RHR approach):
-- resting heart rate at least 3 bpm above the personal baseline for 3 consecutive nights.
-- On the logged illness it fired 1-2 days before symptom onset and is far less noisy than the 2-signal rule.
CREATE OR REPLACE VIEW rhr_rule_alerts AS
WITH d AS (
    SELECT date, resting_hr - rhr_mu AS rhr_rise, load_last3d, training_related
    FROM wellness_baseline
    WHERE resting_hr IS NOT NULL AND n_baseline_days >= 14
),
r AS (
    SELECT *,
        MIN(rhr_rise) OVER (ORDER BY date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)                    AS min_rise_3n,
        COUNT(*)      OVER (ORDER BY date RANGE BETWEEN INTERVAL 2 DAY PRECEDING AND CURRENT ROW)      AS nights_in_window
    FROM d
)
SELECT date, ROUND(min_rise_3n, 1) AS min_rise_3n, load_last3d, training_related
FROM r
WHERE min_rise_3n >= 3 AND nights_in_window = 3;   -- all 3 nights must have been measured
