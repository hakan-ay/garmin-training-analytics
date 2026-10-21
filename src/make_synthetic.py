"""Generates a synthetic, fully fictional athlete in Garmin's raw JSON format, for the public demo and CI.

The data runs through exactly the same pipeline as real data (src/build_db.py --demo). It contains the patterns
the models are built to separate:
  * an illness: resting HR +5/+6 bpm and HRV −20% for 3 nights BEFORE the logged onset, worse during the illness
  * a hard training block: large daily loads that raise resting HR, a confounder that must not become an illness alert
  * a travel episode with one very short night, logged in the context log
  * seasonal sport mix, day-to-day noise and nights without the watch

    uv run python src/make_synthetic.py      # writes demo/raw/*.json, demo/illness_log.csv, demo/context_log.csv
"""

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo"
START, DAYS = date(2024, 1, 1), 274
INTRADAY_DAYS = 100                      # Garmin keeps intraday data for a limited time; mimic that
HR_MAX, HR_REST_SETTING = 190, 52
ILLNESS_ONSET, ILLNESS_DAYS = date(2024, 8, 19), 6
TRAINING_BLOCK = (date(2024, 3, 9), date(2024, 3, 14))
TRAVEL = (date(2024, 6, 20), date(2024, 6, 22))

rng = np.random.default_rng(42)


def ms(dt: datetime) -> int:
    """Naive datetime → epoch milliseconds, the way Garmin encodes its *Local timestamps."""
    return int((dt - datetime(1970, 1, 1)).total_seconds() * 1000)


def plan_day(d: date) -> list[tuple[str, float, float]]:
    """Returns planned sessions as (sport_type, duration_min, intensity 0..1) with a seasonal mix."""
    if TRAINING_BLOCK[0] <= d <= TRAINING_BLOCK[1]:
        return [("road_biking", rng.uniform(200, 320), rng.uniform(0.62, 0.7))]
    if ILLNESS_ONSET - timedelta(days=1) <= d <= ILLNESS_ONSET + timedelta(days=ILLNESS_DAYS + 2):
        return []
    if TRAVEL[0] <= d <= TRAVEL[1]:
        return [("walking", rng.uniform(40, 90), 0.2)]
    m, wd = d.month, d.weekday()
    sessions = []
    if m in (1, 2):                                      # winter: indoor rides and ski weekends
        if wd in (1, 3):
            sessions.append(("virtual_ride", rng.uniform(45, 75), rng.uniform(0.5, 0.65)))
        if wd == 5 and rng.random() < 0.6:
            sessions.append(("resort_skiing", rng.uniform(180, 300), 0.35))
    elif m in (3, 4, 5, 9):                              # spring / autumn: road cycling and running
        if wd in (1, 4):
            sessions.append(("road_biking", rng.uniform(60, 120), rng.uniform(0.5, 0.65)))
        if wd == 6:
            sessions.append(("road_biking", rng.uniform(150, 260), rng.uniform(0.5, 0.62)))
        if wd == 2:
            sessions.append(("running", rng.uniform(35, 60), rng.uniform(0.55, 0.7)))
    else:                                                # summer: strength, swims, hikes
        if wd in (0, 3):
            sessions.append(("strength_training", rng.uniform(45, 75), 0.3))
        if wd == 5:
            sessions.append(("open_water_swimming", rng.uniform(30, 50), 0.4))
        if wd == 6:
            sessions.append(("hiking", rng.uniform(120, 240), 0.3))
        if wd == 2:
            sessions.append(("running", rng.uniform(30, 50), rng.uniform(0.55, 0.68)))
    return [s for s in sessions if rng.random() > 0.15]  # skipped sessions


SPEED_KMH = {"road_biking": 26, "virtual_ride": 28, "running": 11, "walking": 4.8, "hiking": 3.8,
             "open_water_swimming": 2.4}


def activity(d: date, sport: str, dur_min: float, intensity: float, rhr: float, aid: int) -> tuple[dict, float]:
    avg_hr = rhr + intensity * (HR_MAX - rhr) + rng.normal(0, 3)
    start = datetime.combine(d, datetime.min.time()) + timedelta(hours=float(rng.uniform(9, 18)))
    x = (avg_hr - rhr) / (HR_MAX - rhr)
    trimp = dur_min * x * 0.64 * np.exp(1.92 * x)
    # time in zones around the average heart rate (zone floors at 50/60/70/80/90 % of HRmax)
    pct = avg_hr / HR_MAX
    centre = int(np.clip((pct - 0.5) * 10, 0, 4))
    weights = np.array([0.4 ** abs(i - centre) for i in range(5)])
    zones = weights / weights.sum() * dur_min * 60
    distance = SPEED_KMH.get(sport, 0) * dur_min / 60 * 1000 * rng.uniform(0.9, 1.1)
    return {
        "activityId": aid,
        "startTimeLocal": start.strftime("%Y-%m-%d %H:%M:%S"),
        "startTimeGMT": (start - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "activityType": {"typeKey": sport},
        "eventType": {"typeKey": "uncategorized"},
        "distance": round(distance, 1),
        "duration": round(dur_min * 60, 1),
        "movingDuration": round(dur_min * 60 * 0.93, 1),
        "averageHR": round(avg_hr),
        "maxHR": round(min(HR_MAX, avg_hr + rng.uniform(15, 30))),
        "averageSpeed": round(distance / (dur_min * 60), 3) if distance else None,
        "elevationGain": round(distance / 1000 * rng.uniform(5, 20), 1) if sport in ("road_biking", "hiking") else 0,
        "calories": round(dur_min * (4 + 10 * intensity)),
        "activityTrainingLoad": round(trimp * 1.1, 1),
        "aerobicTrainingEffect": round(min(5, 1 + 4 * intensity), 1),
        "anaerobicTrainingEffect": round(rng.uniform(0, 1.5), 1),
        "manualActivity": False,
        **{f"hrTimeInZone_{i + 1}": round(float(z), 1) for i, z in enumerate(zones)},
    }, trimp


def illness_effect(d: date) -> tuple[float, float, float]:
    """(resting HR bpm, lnRMSSD change, respiration change) for the injected illness."""
    k = (d - ILLNESS_ONSET).days
    if k in (-3, -2, -1):
        return 5 + (k == -1), -0.22, 0.6
    if 0 <= k < ILLNESS_DAYS:
        return 7 - k, -0.3 + 0.04 * k, 1.5
    return 0, 0, 0


def main() -> None:
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    activities, wellness, intraday = [], [], []
    trimp_yesterday, aid = 0.0, 900000000
    for i in range(DAYS):
        d = START + timedelta(days=i)
        season = 1.5 * np.sin(2 * np.pi * (i - 200) / 365)          # slow seasonal drift of resting HR
        ill_rhr, ill_hrv, ill_resp = illness_effect(d)
        travel = TRAVEL[0] <= d <= TRAVEL[1]
        rhr = HR_REST_SETTING + season + 0.012 * trimp_yesterday + ill_rhr + (6 if travel else 0) + rng.normal(0, 1.2)
        ln_rmssd = np.log(64) - 0.0012 * trimp_yesterday + ill_hrv - (0.15 if travel else 0) + rng.normal(0, 0.1)
        resp = 14 + ill_resp + rng.normal(0, 0.4)
        bed = datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(hours=float(rng.normal(23.4, 0.6)))
        sleep_h = 3.0 if d == TRAVEL[0] + timedelta(days=1) else float(np.clip(rng.normal(7.3, 0.7), 5, 9.5))
        wake = bed + timedelta(hours=sleep_h)
        worn = rng.random() > 0.05 and not (ILLNESS_ONSET + timedelta(days=20) <= d <= ILLNESS_ONSET + timedelta(days=22))

        sessions = plan_day(d)
        day_trimp = 0.0
        for sport, dur, intensity in sessions:
            aid += 1
            a, t = activity(d, sport, dur, intensity, rhr, aid)
            activities.append(a)
            day_trimp += t
        trimp_yesterday = day_trimp

        if not worn:
            wellness.append({"date": d.isoformat()})
            continue
        sleep_hr_series = [{"value": int(round(rhr + 3 + rng.normal(0, 2))),
                            "startGMT": ms(bed + timedelta(minutes=2 * j))} for j in range(int(sleep_h * 30))]
        wellness.append({
            "date": d.isoformat(),
            "sleep": {
                "dailySleepDTO": {
                    "sleepStartTimestampLocal": ms(bed), "sleepEndTimestampLocal": ms(wake),
                    "sleepTimeSeconds": int(sleep_h * 3600), "deepSleepSeconds": int(sleep_h * 3600 * 0.18),
                    "remSleepSeconds": int(sleep_h * 3600 * 0.22), "awakeCount": int(rng.integers(0, 3)),
                    "sleepScores": {"overall": {"value": int(np.clip(85 - 4 * ill_rhr - 8 * (sleep_h < 5)
                                                                     + rng.normal(0, 5), 20, 99))}},
                    "avgSleepStress": round(float(15 + 3 * ill_rhr + rng.normal(0, 4)), 1),
                    "averageRespirationValue": round(resp), "highestRespirationValue": round(resp + 4),
                },
                "sleepHeartRate": sleep_hr_series if i >= DAYS - INTRADAY_DAYS else [],
                "bodyBatteryChange": int(rng.integers(30, 70)),
            },
            "hrv": {"hrvSummary": {"lastNightAvg": int(round(np.exp(ln_rmssd))), "weeklyAvg": 64,
                                   "baseline": {"balancedLow": 56, "balancedUpper": 74}, "status": "BALANCED"}},
            "rhr": {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": [{"value": int(round(rhr))}]}}},
        })

        if i >= DAYS - INTRADAY_DAYS:
            day0 = datetime.combine(d, datetime.min.time())
            hr, steps = [], []
            for j in range(720):                                     # 2-minute samples over the local day
                t = day0 + timedelta(minutes=2 * j)
                asleep = t < wake or t >= bed + timedelta(days=1)
                hr.append([ms(t - timedelta(hours=1)), int(round((rhr + 3 if asleep else 75) + rng.normal(0, 3)))])
            for j in range(96):                                      # 15-minute step buckets
                t = day0 + timedelta(minutes=15 * j)
                asleep = t < wake or t >= bed + timedelta(days=1)
                steps.append([(t - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.0"),
                              0 if asleep or rng.random() < 0.4 else int(rng.integers(20, 900))])
            intraday.append({"date": d.isoformat(),
                             "start_gmt": (day0 - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.0"),
                             "start_local": day0.strftime("%Y-%m-%dT%H:%M:%S.0"), "hr": hr, "steps": steps})

    (OUT / "raw" / "activities.json").write_text(json.dumps(activities[::-1]))
    (OUT / "raw" / "wellness.json").write_text(json.dumps(wellness[::-1]))
    (OUT / "raw" / "intraday.json").write_text(json.dumps(intraday[::-1]))
    (OUT / "raw" / "profile.json").write_text(json.dumps({"sex": "MALE", "hr_zones": [
        {"trainingMethod": "HR_MAX", "sport": sport, "maxHeartRateUsed": HR_MAX, "restingHeartRateUsed": HR_REST_SETTING}
        for sport in ("DEFAULT", "CYCLING", "RUNNING")]}))
    with open(OUT / "illness_log.csv", "w", newline="") as f:
        csv.writer(f).writerows([["onset_date", "end_date", "kind", "notes"],
                                 [ILLNESS_ONSET, ILLNESS_ONSET + timedelta(days=ILLNESS_DAYS), "cold",
                                  "synthetic illness: sore throat, then fever"]])
    with open(OUT / "context_log.csv", "w", newline="") as f:
        csv.writer(f).writerows([["start_date", "end_date", "kind", "notes"],
                                 [TRAVEL[0], TRAVEL[1], "travel", "synthetic trip: flight and one 3-hour night"]])
    print(f"activities: {len(activities)}, days: {len(wellness)}, intraday days: {len(intraday)}")


if __name__ == "__main__":
    main()
