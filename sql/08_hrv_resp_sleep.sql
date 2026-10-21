-- Secondary signals supporting NightSignal, each based on a published method.
--
-- HRV (Plews et al. 2012, Eur J Appl Physiol; Plews et al. 2013 Sports Med; AIS HRV best-practice 2023):
--   lnRMSSD is used (the distribution is skewed; the log normalises it). Daily values are noisy, so the 7-day rolling mean
--   is compared with the personal normal range; range = baseline mean ± SWC, SWC = 0.5 × baseline SD (common practice, Buchheit 2014).
--   At least 3 valid measurements per week are required (Plews thesis). The 7-day CV(lnRMSSD) is reported as well.
--   Garmin's "overnight HRV" is the mean RMSSD of 5-minute windows during sleep.
-- Respiration (Natarajan, Su & Heneghan 2020, npj Digit Med; Fitbit follow-up study): respiration rate rises during illness;
--   flag at +3 breaths/min above the personal normal (median of the last 28 nights). Garmin reports whole numbers.
-- Sleep (Prather et al. 2015, SLEEP 38:1353): ≤6 hours of sleep per night over a week → ~4x risk of catching a cold.
--   Used as a risk modifier, not as an alert.

CREATE OR REPLACE TABLE secondary_signals AS
WITH ln AS (
    SELECT date, hrv_ms, LN(hrv_ms) AS ln_rmssd, resp_avg, sleep_hours
    FROM daily_wellness
),
hrv AS (
    SELECT *,
        AVG(ln_rmssd)    OVER w7  AS ln_rmssd_7d,
        COUNT(ln_rmssd)  OVER w7  AS n_hrv_7d,
        STDDEV_SAMP(ln_rmssd) OVER w7 / NULLIF(AVG(ln_rmssd) OVER w7, 0) * 100 AS cv_ln_rmssd_7d,
        -- baseline: the 28 days before the 7-day window (so the window does not contaminate the baseline)
        AVG(ln_rmssd)    OVER base AS ln_rmssd_base,
        STDDEV_SAMP(ln_rmssd) OVER base AS ln_rmssd_base_sd,
        COUNT(ln_rmssd)  OVER base AS n_hrv_base,
        MEDIAN(resp_avg) OVER r28  AS resp_base,
        AVG(sleep_hours) OVER w7   AS sleep_7d,
        COUNT(sleep_hours) OVER w7 AS n_sleep_7d
    FROM ln
    WINDOW w7   AS (ORDER BY date RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW),
           base AS (ORDER BY date RANGE BETWEEN INTERVAL 34 DAY PRECEDING AND INTERVAL 7 DAY PRECEDING),
           r28  AS (ORDER BY date RANGE BETWEEN INTERVAL 28 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING)
)
SELECT
    date, hrv_ms, ln_rmssd, ln_rmssd_7d, n_hrv_7d, cv_ln_rmssd_7d,
    ln_rmssd_base, 0.5 * ln_rmssd_base_sd AS swc,
    ln_rmssd_base - 0.5 * ln_rmssd_base_sd AS hrv_normal_low,
    ln_rmssd_base + 0.5 * ln_rmssd_base_sd AS hrv_normal_high,
    -- 7-day mean below the normal range: impaired recovery / systemic stress
    n_hrv_7d >= 3 AND n_hrv_base >= 14
      AND ln_rmssd_7d < ln_rmssd_base - 0.5 * ln_rmssd_base_sd                AS hrv_below_normal,
    resp_avg, resp_base,
    COALESCE(resp_avg >= resp_base + 3, false)                                AS resp_elevated,
    sleep_7d,
    COALESCE(n_sleep_7d >= 5 AND sleep_7d <= 6, false)                        AS sleep_debt
FROM hrv
ORDER BY date;
