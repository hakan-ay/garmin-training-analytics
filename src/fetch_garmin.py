"""Fetches activities and daily wellness data from Garmin Connect and writes them as JSON under data/raw/.

Authentication: uses the token cache under ~/.garminconnect (override with GARMINTOKENS).
This script never asks for or stores a password.

Usage:
    uv run python src/fetch_garmin.py --since 2024-01-01
"""

import argparse
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from garminconnect import Garmin

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
TOKEN_DIR = os.path.expanduser(os.getenv("GARMINTOKENS", "~/.garminconnect"))


def login() -> Garmin:
    client = Garmin()
    client.login(TOKEN_DIR)
    return client


def fetch_activities(client: Garmin, page_size: int = 100) -> list[dict]:
    activities, start = [], 0
    while True:
        page = client.get_activities(start, page_size)
        if not page:
            break
        activities.extend(page)
        start += page_size
        time.sleep(0.5)
    return activities


def fetch_wellness(client: Garmin, since: date, existing: dict[str, dict]) -> list[dict]:
    """Fetches wellness for every day from `since` to today. Days fetched before are not downloaded again;
    only the last 3 days are refreshed (Garmin can sync a night late)."""
    rows = []
    today = date.today()
    refresh_from = today - timedelta(days=3)
    for i in range((today - since).days + 1):
        day = today - timedelta(days=i)
        d = day.isoformat()
        if d in existing and day < refresh_from and not any(k.endswith("_error") for k in existing[d]):
            rows.append(existing[d])
            continue
        row = {"date": d}
        try:
            row["sleep"] = client.get_sleep_data(d)
        except Exception as e:  # one failing day must not stop the whole fetch
            row["sleep_error"] = str(e)
        try:
            row["hrv"] = client.get_hrv_data(d)
        except Exception as e:
            row["hrv_error"] = str(e)
        try:
            row["rhr"] = client.get_rhr_day(d)
        except Exception as e:
            row["rhr_error"] = str(e)
        rows.append(row)
        time.sleep(0.3)
    return rows


def fetch_intraday(client: Garmin, since: date, existing: dict[str, dict]) -> list[dict]:
    """Intraday heart rate (2 min) and step (15 min) series for NightSignal. Garmin keeps these only for a limited
    number of months, so run this regularly; empty days are stored too so they are not requested on every run."""
    rows = []
    today = date.today()
    refresh_from = today - timedelta(days=3)
    for i in range((today - since).days + 1):
        day = today - timedelta(days=i)
        d = day.isoformat()
        if d in existing and day < refresh_from and "error" not in existing[d]:
            rows.append(existing[d])
            continue
        row = {"date": d}
        try:
            hr = client.get_heart_rates(d) or {}
            row["start_gmt"] = hr.get("startTimestampGMT")
            row["start_local"] = hr.get("startTimestampLocal")
            row["hr"] = [v for v in (hr.get("heartRateValues") or []) if v and v[1]]
            row["steps"] = [[s["startGMT"], s["steps"]] for s in (client.get_steps_data(d) or [])]
        except Exception as e:
            row["error"] = str(e)
        rows.append(row)
        time.sleep(0.3)
    return rows


def fetch_profile(client: Garmin) -> dict:
    """Sex and heart rate zones (HRmax) for Banister TRIMP. Fields such as date of birth are not stored."""
    profile = client.get_user_profile() or {}
    return {
        "sex": (profile.get("userData") or {}).get("gender"),
        "hr_zones": client.connectapi("/biometric-service/heartRateZones"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=date.fromisoformat, default=date.today() - timedelta(days=90),
                        help="start date for wellness data, YYYY-MM-DD")
    parser.add_argument("--intraday-since", type=date.fromisoformat, default=date(2026, 6, 1),
                        help="start date for intraday heart rate / steps (Garmin does not keep older data)")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = login()

    activities = fetch_activities(client)
    (RAW_DIR / "activities.json").write_text(json.dumps(activities, indent=1))
    print(f"activities: {len(activities)}")

    wellness_path = RAW_DIR / "wellness.json"
    existing = {r["date"]: r for r in json.loads(wellness_path.read_text())} if wellness_path.exists() else {}
    wellness = fetch_wellness(client, args.since, existing)
    wellness_path.write_text(json.dumps(wellness, indent=1))
    print(f"wellness days: {len(wellness)}")

    intraday_path = RAW_DIR / "intraday.json"
    existing = {r["date"]: r for r in json.loads(intraday_path.read_text())} if intraday_path.exists() else {}
    intraday = fetch_intraday(client, args.intraday_since, existing)
    intraday_path.write_text(json.dumps(intraday))
    print(f"intraday days: {len(intraday)} (with HR: {sum(1 for r in intraday if r.get('hr'))})")

    (RAW_DIR / "profile.json").write_text(json.dumps(fetch_profile(client), indent=1))


if __name__ == "__main__":
    main()
