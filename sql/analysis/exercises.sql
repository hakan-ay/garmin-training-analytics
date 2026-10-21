-- EXERCISES: write the solutions yourself. To run:
--   uv run python src/run_query.py sql/analysis/exercises.sql

-- E1: Aerobic efficiency (cycling).
--   Question: has my heart rate at the same speed dropped over time?
--   Hint: filter sport_group = 'cycling' AND NOT is_indoor AND distance_km > 20 (or use the endurance_outdoor view).
--         For each activity compute "efficiency = speed_kmh / avg_hr".
--         Average it per quarter with DATE_TRUNC('quarter', start_time).
--         Bonus: a 5-ride rolling mean with AVG(...) OVER (ORDER BY start_time ROWS BETWEEN 4 PRECEDING AND CURRENT ROW).

-- SELECT ...


-- E2: Best training day / time.
--   Question: on which day of the week and at which time of day (morning / midday / evening) do I do my longest sessions?
--   Hint: DAYNAME(start_time), HOUR(start_time) and CASE for time-of-day buckets.
--         Then rank per sport with RANK() OVER (PARTITION BY sport_group ORDER BY AVG(duration_min) DESC).

-- SELECT ...
