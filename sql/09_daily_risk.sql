-- Daily risk table: the dashboard's single data source.
-- Each day stores the risk level + the fields that PROVE that level, so the UI answers "why?" without new calculations.
--
-- Illness risk (primary signal: NightSignal, Alavi et al. 2022, 07_nightsignal.sql)
--   alert  : NightSignal red alert (2 consecutive nights ≥ baseline+4) AND not explained by training / a context event
--   watch  : NightSignal yellow alert, OR red with a confounder, OR respiration ≥ normal+3 (Natarajan 2020)
--   HRV is shown as evidence but never alerts on its own: Plews' 7-day HRV method was built for training adaptation /
--   overreaching and can stay low for weeks (as an illness alert it produced 64 "watch" days per year).
--   normal : otherwise
--   Series and baseline were chosen with a5_model_comparison.sql (false-alarm rate on healthy days over the full history):
--   NightSignal rules (original expanding median, +4 bpm, 2 nights) applied to Garmin's daily resting heart rate.
--   The original 00:00-06:59 window performed worst because, for a late sleeper, much of it is spent awake.
-- Overload / injury risk (EWMA ACWR, Williams 2017; Foster 1998 strain; Plews 2012 HRV; files 03 and 08)
--   high > 1.5 ACWR; elevated > 1.3 ACWR, strain above the personal 90th percentile, or 7-day HRV below the normal range.
--   The ability of ACWR to predict injury is debated in the literature.

CREATE OR REPLACE TABLE daily_risk AS
WITH config AS (
    -- chosen from the a5_model_comparison.sql results (README "Model comparison")
    SELECT 'garmin_rhr' AS ns_series, 'expanding' AS ns_baseline_kind
),
ns_base AS (
    SELECT n.*,
        n.date - CAST(ROW_NUMBER() OVER (PARTITION BY n.series, n.baseline_kind, n.yellow_candidate
                                         ORDER BY n.date) AS INTEGER) AS island
    FROM nightsignal n, config c
    WHERE n.baseline_kind = c.ns_baseline_kind AND n.series = c.ns_series
),
ns AS (
    -- number of consecutive candidate nights (gaps-and-islands), used in the evidence sentence
    SELECT *,
        CASE WHEN yellow_candidate
             THEN ROW_NUMBER() OVER (PARTITION BY series, baseline_kind, yellow_candidate, island ORDER BY date)
             ELSE 0 END AS candidate_nights
    FROM ns_base
),
ns_pick AS (SELECT * FROM ns)
SELECT
    w.date,
    -- NightSignal evidence
    n.series                                       AS ns_series,
    n.avg_hr                                       AS ns_night_hr,
    n.baseline                                     AS ns_baseline,
    n.rise                                         AS ns_rise,
    COALESCE(n.candidate_nights, 0)                AS ns_candidate_nights,
    COALESCE(n.state, 0)                           AS ns_state,
    -- raw signals and personal z-scores (signal contribution chart; 04_illness_signals.sql)
    b.resting_hr, b.rhr_mu AS rhr_baseline, b.rhr_sd,
    b.sleep_hr_avg, b.shr_mu AS sleep_hr_baseline, b.shr_sd,
    b.hrv_ms, b.hrv_mu AS hrv_baseline, b.hrv_sd,
    b.resp_avg, b.resp_mu AS resp_baseline_mean, b.resp_sd,
    b.rhr_z, b.sleep_hr_z, b.hrv_z, b.resp_z,
    b.sleep_score,
    -- HRV / respiration / sleep (08_hrv_resp_sleep.sql)
    s.ln_rmssd, s.ln_rmssd_7d, s.hrv_normal_low, s.hrv_normal_high, s.cv_ln_rmssd_7d,
    COALESCE(s.hrv_below_normal, false)            AS hrv_below_normal,
    s.resp_base, COALESCE(s.resp_elevated, false)  AS resp_elevated,
    s.sleep_7d, COALESCE(s.sleep_debt, false)      AS sleep_debt,
    b.sleep_hours,
    -- load and confounders (03_training_load.sql)
    COALESCE(l.trimp, 0)                           AS trimp_today,
    l.weekly_load, l.monotony, l.strain, l.ewma_acute, l.ewma_chronic, l.ewma_acwr,
    tc.strain_yesterday, tc.strain_p90, tc.ewma_acwr_yesterday,
    COALESCE(tc.training_related, false)           AS training_related,
    c.kind                                         AS context_kind,
    c.notes                                        AS context_notes,
    i.onset_date IS NOT NULL                       AS is_sick_logged,
    COALESCE(tc.training_related, false) OR c.kind IS NOT NULL AS has_confounder,
    CASE
        WHEN n.date IS NULL                                                   THEN 'no_baseline'
        WHEN n.state = 2 AND NOT (COALESCE(tc.training_related, false) OR c.kind IS NOT NULL) THEN 'alert'
        WHEN n.state >= 1 OR s.resp_elevated                                  THEN 'watch'
        ELSE 'normal'
    END                                            AS illness_risk,
    CASE
        WHEN l.ewma_acwr IS NULL OR l.ewma_chronic < 5                        THEN 'insufficient_load'
        WHEN l.ewma_acwr > 1.5                                                THEN 'high'
        WHEN l.ewma_acwr > 1.3 OR l.strain > tc.strain_p90 OR s.hrv_below_normal THEN 'elevated'
        ELSE 'normal'
    END                                            AS injury_risk
FROM daily_wellness w
LEFT JOIN ns_pick n              ON n.date = w.date
LEFT JOIN wellness_baseline b    ON b.date = w.date
LEFT JOIN secondary_signals s    ON s.date = w.date
LEFT JOIN daily_load l           ON l.date = w.date
LEFT JOIN training_confounder tc ON tc.date = w.date
LEFT JOIN context_log c ON w.date BETWEEN c.start_date AND c.end_date
LEFT JOIN illness_log i ON w.date BETWEEN i.onset_date AND i.end_date
QUALIFY ROW_NUMBER() OVER (PARTITION BY w.date ORDER BY c.start_date) = 1
ORDER BY w.date;
