-- NightSignal inputs. 07_nightsignal.sql reads only these two tables, so the same algorithm can also run on
-- Stanford's sample data and be tested against the original code (tests/test_nightsignal.py).
--
-- ns_minutes       : per-minute (2-minute here) heart rate + step count at that time, local time
-- ns_nightly_extra : series already summarised per night (proxies for periods without intraday data)

CREATE OR REPLACE TABLE ns_minutes AS
SELECT
    'overnight_hr'                    AS series,
    h.ts_local,
    h.hr,
    -- Garmin reports steps in 15-minute buckets; each heart rate sample takes the step count of its bucket
    s.steps
FROM stg_intraday_hr h
LEFT JOIN stg_intraday_steps s
       ON s.bucket_start_local = time_bucket(INTERVAL 15 MINUTE, h.ts_local);

-- Proxy series: Garmin's daily resting heart rate (available for the full history). It is not NightSignal's definition
-- (Garmin RHR = the lowest 30-minute average of the day), so it is kept as a separate series and never mixed.
CREATE OR REPLACE TABLE ns_nightly_extra AS
SELECT 'garmin_rhr' AS series, date, resting_hr AS avg_hr
FROM daily_wellness
WHERE resting_hr IS NOT NULL;

-- Our adaptation: 'sleep_window_hr'. NightSignal uses a fixed 00:00-06:59 window; for a late sleeper a large part of
-- that window is spent awake. Here the same definition (mean step-free heart rate, truncated to an integer) is applied
-- to the ACTUAL sleep window detected by Garmin. The remaining steps (baseline, thresholds, 2 nights) are unchanged.
INSERT INTO ns_nightly_extra
SELECT 'sleep_window_hr', w.date, FLOOR(AVG(h.hr))::INTEGER
FROM daily_wellness w
JOIN stg_intraday_hr h ON h.ts_local BETWEEN w.sleep_start_local AND w.sleep_end_local
LEFT JOIN stg_intraday_steps s ON s.bucket_start_local = time_bucket(INTERVAL 15 MINUTE, h.ts_local)
WHERE s.steps = 0
GROUP BY w.date;
