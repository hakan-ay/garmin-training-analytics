-- Per-sport filter rules. To change a threshold, edit only this table; 01_clean_activities.sql reads from it.
--   min_duration_min : records shorter than this are excluded
--   min_distance_km  : NULL = no distance required (strength, skiing...)
--   min/max_speed_kmh: NULL = no speed check (indoor, skiing: lift time distorts speed)
-- To think in pace instead of speed for running/walking: 60 / speed_kmh = min/km.

CREATE OR REPLACE TABLE sport_rules AS
SELECT * FROM (VALUES
    -- sport_group,    indoor, min_dur, min_km, min_kmh, max_kmh
    ('cycling',        false,  10,      2.0,    8.0,     55.0),   -- road rides + short city rides (real load)
    ('cycling',        true,   15,      NULL,   NULL,    NULL),   -- Zwift/ROUVY: virtual speed is unreliable
    ('running',        false,  10,      1.0,    6.0,     24.0),   -- 10:00 - 2:30 min/km
    ('running',        true,   10,      1.0,    6.0,     24.0),   -- treadmill
    ('walk_hike',      false,  15,      1.0,    1.5,     8.0),
    ('mountain',       false,  30,      NULL,   NULL,    NULL),   -- mountaineering: GPS distance is meaningless on vertical terrain
    ('strength_climb', true,   10,      NULL,   NULL,    NULL),
    ('swimming',       false,  10,      NULL,   NULL,    NULL),   -- GPS distance is unreliable in open water
    ('winter',         false,  30,      NULL,   NULL,    NULL),
    ('other',          false,  10,      NULL,   NULL,    NULL)
) AS t(sport_group, is_indoor, min_duration_min, min_distance_km, min_speed_kmh, max_speed_kmh);
