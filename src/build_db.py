"""data/raw/*.json -> data/garmin.duckdb  (or demo/raw -> demo/demo.duckdb with --demo)

Python only flattens the JSON into tables (staging). All cleaning and modelling lives in the files under sql/.
Personal / location fields (names, GPS, locationName, owner*) are never loaded into staging.
"""

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"


def stg_activities(raw_dir: Path) -> pd.DataFrame:
    raw = json.loads((raw_dir / "activities.json").read_text())
    rows = [
        {
            "activity_id": a["activityId"],
            "start_time_local": a.get("startTimeLocal"),
            "start_time_gmt": a.get("startTimeGMT"),
            "sport_type": a["activityType"]["typeKey"],
            "event_type": (a.get("eventType") or {}).get("typeKey"),
            "distance_m": a.get("distance"),
            "duration_s": a.get("duration"),
            "moving_duration_s": a.get("movingDuration"),
            "avg_hr": a.get("averageHR"),
            "max_hr": a.get("maxHR"),
            "avg_speed_mps": a.get("averageSpeed"),
            "elevation_gain_m": a.get("elevationGain"),
            "calories": a.get("calories"),
            "training_load": a.get("activityTrainingLoad"),
            "aerobic_te": a.get("aerobicTrainingEffect"),
            "anaerobic_te": a.get("anaerobicTrainingEffect"),
            "is_manual": a.get("manualActivity"),
            **{f"hr_zone{i}_s": a.get(f"hrTimeInZone_{i}") for i in range(1, 6)},
        }
        for a in raw
    ]
    return pd.DataFrame(rows)


def stg_wellness(raw_dir: Path) -> pd.DataFrame:
    raw = json.loads((raw_dir / "wellness.json").read_text())
    rows = []
    for r in raw:
        sleep = r.get("sleep") or {}
        dto = sleep.get("dailySleepDTO") or {}
        hrv = (r.get("hrv") or {}).get("hrvSummary") or {}
        rhr_list = (((r.get("rhr") or {}).get("allMetrics") or {}).get("metricsMap") or {}).get(
            "WELLNESS_RESTING_HEART_RATE"
        ) or [{}]
        sleep_hr = [p["value"] for p in (sleep.get("sleepHeartRate") or []) if p.get("value")]
        baseline = hrv.get("baseline") or {}
        rows.append(
            {
                "date": r["date"],
                # Garmin "Local" timestamps encode local wall-clock time as if it were epoch time
                "sleep_start_local": pd.Timestamp(dto["sleepStartTimestampLocal"], unit="ms")
                if dto.get("sleepStartTimestampLocal") else None,
                "sleep_end_local": pd.Timestamp(dto["sleepEndTimestampLocal"], unit="ms")
                if dto.get("sleepEndTimestampLocal") else None,
                "sleep_s": dto.get("sleepTimeSeconds"),
                "deep_sleep_s": dto.get("deepSleepSeconds"),
                "rem_sleep_s": dto.get("remSleepSeconds"),
                "awake_count": dto.get("awakeCount"),
                "sleep_score": ((dto.get("sleepScores") or {}).get("overall") or {}).get("value"),
                "avg_sleep_stress": dto.get("avgSleepStress"),
                "resp_avg": dto.get("averageRespirationValue"),
                "resp_max": dto.get("highestRespirationValue"),
                # Sleeping heart rate: Garmin provides a per-minute series; the nightly mean and minimum are less noisy than RHR
                "sleep_hr_avg": sum(sleep_hr) / len(sleep_hr) if sleep_hr else None,
                "sleep_hr_min": min(sleep_hr) if sleep_hr else None,
                "hrv_last_night_ms": hrv.get("lastNightAvg"),
                "hrv_weekly_avg_ms": hrv.get("weeklyAvg"),
                "hrv_baseline_low_ms": baseline.get("balancedLow"),
                "hrv_baseline_high_ms": baseline.get("balancedUpper"),
                "hrv_status": hrv.get("status"),
                "resting_hr": rhr_list[0].get("value"),
                "body_battery_change": sleep.get("bodyBatteryChange"),
            }
        )
    return pd.DataFrame(rows)


def stg_intraday(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flattens the intraday heart rate (2 min) and step (15 min) series into tables, converted to local time.
    The local-time offset is the difference between the day's GMT and local start (handles daylight saving automatically)."""
    path = raw_dir / "intraday.json"
    hr_rows, step_rows = [], []
    if path.exists():
        for r in json.loads(path.read_text()):
            if not r.get("start_gmt") or not r.get("hr"):
                continue
            offset = pd.Timestamp(r["start_local"]) - pd.Timestamp(r["start_gmt"])
            for ts_ms, hr in r["hr"]:
                hr_rows.append({"ts_local": pd.Timestamp(ts_ms, unit="ms") + offset, "hr": hr})
            for start_gmt, steps in r.get("steps") or []:
                step_rows.append({"bucket_start_local": pd.Timestamp(start_gmt) + offset, "steps": steps})
    hr = pd.DataFrame(hr_rows, columns=["ts_local", "hr"]).drop_duplicates("ts_local")
    steps = pd.DataFrame(step_rows, columns=["bucket_start_local", "steps"]).drop_duplicates("bucket_start_local")
    return hr, steps


def stg_profile(raw_dir: Path) -> pd.DataFrame:
    path = raw_dir / "profile.json"
    p = json.loads(path.read_text()) if path.exists() else {}
    default = next((z for z in p.get("hr_zones") or [] if z.get("sport") == "DEFAULT"), {})
    return pd.DataFrame([{
        "sex": p.get("sex"),
        "hr_max": default.get("maxHeartRateUsed"),
        "hr_rest_setting": default.get("restingHeartRateUsed"),
        # sports whose zones are %HRmax based (Edwards TRIMP is only valid for these)
        "hrmax_zone_sports": [z["sport"] for z in p.get("hr_zones") or [] if z.get("trainingMethod") == "HR_MAX"],
    }])


def load_label_csv(con: duckdb.DuckDBPyConnection, labels_dir: Path, name: str, columns: dict[str, str]) -> None:
    """Loads a private label file (gitignored) into a table; creates an empty table when the file is missing so the pipeline never breaks."""
    path = labels_dir / f"{name}.csv"
    cols = ", ".join(f"{c} {t}" for c, t in columns.items())
    con.execute(f"CREATE TABLE {name} ({cols})")
    if path.exists():
        con.execute(f"INSERT INTO {name} SELECT * FROM read_csv(?, header = true, columns = ?)",
                    [str(path), columns])


def build(raw_dir: Path, labels_dir: Path, db_path: Path, export_dir: Path | None) -> None:
    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    con.register("stg_activities_df", stg_activities(raw_dir))
    con.register("stg_wellness_df", stg_wellness(raw_dir))
    con.execute("CREATE TABLE stg_activities AS SELECT * FROM stg_activities_df")
    con.execute("CREATE TABLE stg_wellness AS SELECT * FROM stg_wellness_df")
    hr_df, steps_df = stg_intraday(raw_dir)
    con.register("stg_hr_df", hr_df)
    con.register("stg_steps_df", steps_df)
    con.register("stg_profile_df", stg_profile(raw_dir))
    con.execute("CREATE TABLE stg_intraday_hr AS SELECT * FROM stg_hr_df")
    con.execute("CREATE TABLE stg_intraday_steps AS SELECT * FROM stg_steps_df")
    con.execute("CREATE TABLE stg_profile AS SELECT * FROM stg_profile_df")

    load_label_csv(con, labels_dir, "illness_log",
                   {"onset_date": "DATE", "end_date": "DATE", "kind": "VARCHAR", "notes": "VARCHAR"})
    load_label_csv(con, labels_dir, "context_log",
                   {"start_date": "DATE", "end_date": "DATE", "kind": "VARCHAR", "notes": "VARCHAR"})

    for f in sorted(SQL_DIR.glob("0*_*.sql")):
        con.execute(f.read_text())
        print(f"ran {f.name}")

    for t in ("activities", "daily_wellness", "excluded_activities"):
        print(t, con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])

    if export_dir is not None:
        # Anonymous output (no names or locations, but health metrics: kept out of git)
        export_dir.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY activities TO '{export_dir / 'activities.csv'}' (HEADER)")
        con.execute(f"COPY daily_wellness TO '{export_dir / 'daily_wellness.csv'}' (HEADER)")
    con.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="build demo/demo.duckdb from the synthetic data in demo/")
    args = parser.parse_args()
    if args.demo:
        build(ROOT / "demo" / "raw", ROOT / "demo", ROOT / "demo" / "demo.duckdb", export_dir=None)
    else:
        build(ROOT / "data" / "raw", ROOT / "data", ROOT / "data" / "garmin.duckdb", ROOT / "data" / "processed")


if __name__ == "__main__":
    main()
