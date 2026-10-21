-- Q4: Event study. Day 0 = the first day symptoms were FELT (onset_date in data/illness_log.csv).
-- How far are the signals from their personal baselines from day −7 to day +7? Is there a signal on days −1 to −3?
-- Input: the illness_log table, loaded by build_db.py from data/illness_log.csv (see illness_log.example.csv).
-- Concepts: aligning dates on an event, grouping by day offset.

WITH aligned AS (
    SELECT
        i.onset_date,
        CAST(r.date - i.onset_date AS INTEGER)                  AS day_offset,
        r.ns_rise,
        100 * (EXP(s.ln_rmssd - s.ln_rmssd_base) - 1)          AS hrv_change_pct,
        r.resp_avg - s.resp_base                                AS resp_change,
        r.ns_state,
        r.illness_risk
    FROM illness_log i
    JOIN daily_risk r
      ON r.date BETWEEN i.onset_date - INTERVAL 7 DAY AND i.onset_date + INTERVAL 7 DAY
    JOIN secondary_signals s ON s.date = r.date
)
SELECT
    day_offset,
    COUNT(*)                                                    AS n_illnesses,
    ROUND(AVG(ns_rise), 2)                                      AS rhr_rise_bpm,
    ROUND(AVG(hrv_change_pct), 1)                               AS hrv_change_pct,
    ROUND(AVG(resp_change), 2)                                  AS resp_change,
    ROUND(AVG((illness_risk = 'alert')::INT), 2)                AS share_alert
FROM aligned
GROUP BY day_offset
ORDER BY day_offset;
