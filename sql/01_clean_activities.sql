-- Cleaned activity table.
-- Rules (applied in order; the first one that matches becomes exclude_reason):
--   1. Among duplicates with the same start time, the longest record is kept.
--   2. Manually entered records are excluded.
--   3. Records without heart rate data, or with physiologically impossible values, are excluded.
--   4. Per-sport duration / distance / speed thresholds (sport_rules table) are applied.
-- No record is deleted: excluded ones stay in the excluded_activities view with their reason.

CREATE OR REPLACE TABLE activities_all AS
WITH typed AS (
    SELECT
        activity_id,
        CAST(start_time_local AS TIMESTAMP)                    AS start_time,
        sport_type,
        CASE
            WHEN sport_type IN ('cycling', 'road_biking', 'virtual_ride',
                                'indoor_cycling', 'gravel_cycling', 'mountain_biking') THEN 'cycling'
            WHEN sport_type IN ('running', 'treadmill_running', 'trail_running') THEN 'running'
            WHEN sport_type IN ('walking', 'hiking')                            THEN 'walk_hike'
            WHEN sport_type = 'mountaineering'                                  THEN 'mountain'
            WHEN sport_type IN ('strength_training', 'indoor_climbing')         THEN 'strength_climb'
            WHEN sport_type LIKE '%swimming%'                                   THEN 'swimming'
            WHEN sport_type LIKE '%skiing%'                                     THEN 'winter'
            ELSE 'other'
        END                                                    AS sport_group,
        sport_type IN ('virtual_ride', 'treadmill_running', 'indoor_cycling',
                       'strength_training', 'indoor_climbing')  AS is_indoor,
        event_type = 'race'                                    AS is_race,
        NULLIF(distance_m, 0)                                  AS distance_m,
        duration_s,
        moving_duration_s,
        CAST(avg_hr AS INTEGER)                                AS avg_hr,
        CAST(max_hr AS INTEGER)                                AS max_hr,
        elevation_gain_m,
        CAST(calories AS INTEGER)                              AS calories,
        training_load,
        aerobic_te,
        anaerobic_te,
        hr_zone1_s, hr_zone2_s, hr_zone3_s, hr_zone4_s, hr_zone5_s,
        coalesce(is_manual, false)                             AS is_manual,
        -- Duration used for speed: moving_duration for cycling/running (excludes breaks),
        -- total duration otherwise (moving_duration is unreliable for hiking/mountaineering: it can record
        -- 13 min of movement on a 278-minute day)
        CASE WHEN sport_type IN ('cycling', 'road_biking', 'virtual_ride', 'indoor_cycling', 'gravel_cycling',
                                 'mountain_biking', 'running', 'treadmill_running', 'trail_running')
              AND moving_duration_s > 0
             THEN moving_duration_s ELSE duration_s END        AS effective_s
    FROM stg_activities
),
derived AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY start_time ORDER BY duration_s DESC) AS dup_rank,
        distance_m / 1000.0                                    AS distance_km,
        duration_s / 60.0                                      AS duration_min,
        -- km/h (cycling) and min/km (running/walking) are kept separately
        CASE WHEN distance_m > 0
             THEN (distance_m / 1000.0) / (effective_s / 3600.0) END AS speed_kmh,
        CASE WHEN distance_m > 0 AND sport_group IN ('running', 'walk_hike')
             THEN (effective_s / 60.0) / (distance_m / 1000.0) END   AS pace_min_per_km
    FROM typed
)
SELECT
    d.* EXCLUDE (dup_rank),
    CASE
        WHEN d.dup_rank > 1                                         THEN 'duplicate'
        WHEN d.is_manual                                            THEN 'manual_entry'
        WHEN d.avg_hr IS NULL                                       THEN 'no_hr'
        WHEN d.avg_hr NOT BETWEEN 50 AND 200
          OR d.max_hr > 220 OR d.max_hr < d.avg_hr                  THEN 'hr_implausible'
        -- a new sport type must not pass silently; it has to be added to sport_rules
        WHEN r.sport_group IS NULL                                  THEN 'no_rule'
        WHEN d.duration_min < r.min_duration_min                    THEN 'too_short'
        WHEN r.min_distance_km IS NOT NULL
         AND coalesce(d.distance_km, 0) < r.min_distance_km         THEN 'too_little_distance'
        WHEN r.min_speed_kmh IS NOT NULL
         AND d.speed_kmh NOT BETWEEN r.min_speed_kmh AND r.max_speed_kmh THEN 'speed_implausible'
    END                                                        AS exclude_reason
FROM derived d
LEFT JOIN sport_rules r
       ON r.sport_group = d.sport_group AND r.is_indoor = d.is_indoor;

CREATE OR REPLACE VIEW activities AS
SELECT * EXCLUDE (exclude_reason) FROM activities_all WHERE exclude_reason IS NULL;

CREATE OR REPLACE VIEW excluded_activities AS
SELECT activity_id, start_time, sport_type, sport_group, is_indoor, duration_min, distance_km,
       speed_kmh, avg_hr, max_hr, exclude_reason
FROM activities_all WHERE exclude_reason IS NOT NULL;

-- For speed + heart rate analyses such as aerobic efficiency: outdoor endurance activities with distance
CREATE OR REPLACE VIEW endurance_outdoor AS
SELECT *, speed_kmh / avg_hr AS kmh_per_bpm
FROM activities
WHERE sport_group IN ('cycling', 'running') AND NOT is_indoor
  AND distance_km >= CASE sport_group WHEN 'cycling' THEN 10 ELSE 3 END;
