-- Q2: Weekly training load (Banister TRIMP), Foster monotony / strain and the EWMA acute:chronic ratio.
-- An ACWR above 1.5 is commonly read as "load increased too quickly" (Williams 2017; predictive value is debated).
-- Concepts: DATE_TRUNC, aggregating a daily spine to weeks, taking the last value of a week with ARG_MAX.

SELECT
    DATE_TRUNC('week', date)::DATE                       AS week,
    ROUND(SUM(trimp))                                    AS weekly_trimp,
    ROUND(ARG_MAX(monotony, date), 2)                    AS monotony_end_of_week,
    ROUND(ARG_MAX(strain, date))                         AS strain_end_of_week,
    ROUND(ARG_MAX(ewma_acwr, date), 2)                   AS ewma_acwr_end_of_week,
    COUNT(*) FILTER (WHERE trimp > 0)                    AS training_days
FROM daily_load
GROUP BY 1
ORDER BY 1;
