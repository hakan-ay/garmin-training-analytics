-- Q1: Sport mix by month, the basis of the seasonal story.
-- Concepts: DATE_TRUNC, CTE, a window function after GROUP BY for the within-month share.

WITH monthly AS (
    SELECT
        DATE_TRUNC('month', start_time)::DATE                     AS month,
        sport_group,
        SUM(duration_min) / 60                                    AS total_h
    FROM activities
    GROUP BY 1, 2
)
SELECT
    month,
    sport_group,
    ROUND(total_h, 1)                                             AS total_h,
    ROUND(100 * total_h / SUM(total_h) OVER (PARTITION BY month), 1) AS pct_of_month
FROM monthly
ORDER BY month, total_h DESC;
