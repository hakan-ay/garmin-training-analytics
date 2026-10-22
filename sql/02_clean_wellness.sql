-- Daily wellness table: seconds are converted to hours; nights without the watch stay NULL (never averaged in as 0).
-- date = the morning of waking up, so the HRV/RHR on a "date" row measures the previous night.
-- resting_hr is kept only for nights the watch recorded sleep: on days it was not worn overnight Garmin derives
-- "resting HR" from daytime wear (values of 65-85 bpm instead of ~50), which is not a valid input for overnight
-- methods such as NightSignal. The untouched value stays in resting_hr_raw.

CREATE OR REPLACE TABLE daily_wellness AS
SELECT
    CAST(date AS DATE)                        AS date,
    sleep_start_local,
    sleep_end_local,
    NULLIF(sleep_s, 0) / 3600.0               AS sleep_hours,
    NULLIF(deep_sleep_s, 0) / 3600.0          AS deep_sleep_hours,
    NULLIF(rem_sleep_s, 0) / 3600.0           AS rem_sleep_hours,
    CAST(awake_count AS INTEGER)              AS awake_count,
    CAST(sleep_score AS INTEGER)              AS sleep_score,
    CAST(avg_sleep_stress AS DOUBLE)          AS avg_sleep_stress,
    CAST(NULLIF(resp_avg, 0) AS DOUBLE)       AS resp_avg,
    CAST(NULLIF(resp_max, 0) AS DOUBLE)       AS resp_max,
    CAST(sleep_hr_avg AS DOUBLE)              AS sleep_hr_avg,
    CAST(sleep_hr_min AS INTEGER)             AS sleep_hr_min,
    CAST(hrv_last_night_ms AS DOUBLE)         AS hrv_ms,
    CAST(hrv_weekly_avg_ms AS DOUBLE)         AS hrv_weekly_avg_ms,
    CAST(hrv_baseline_low_ms AS DOUBLE)       AS hrv_baseline_low_ms,
    CAST(hrv_baseline_high_ms AS DOUBLE)      AS hrv_baseline_high_ms,
    hrv_status,
    CASE WHEN NULLIF(sleep_s, 0) IS NOT NULL THEN CAST(resting_hr AS INTEGER) END AS resting_hr,
    CAST(resting_hr AS INTEGER)               AS resting_hr_raw,
    CAST(body_battery_change AS INTEGER)      AS body_battery_change
FROM stg_wellness
ORDER BY date;
