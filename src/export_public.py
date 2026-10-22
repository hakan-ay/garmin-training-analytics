"""Exports the author's own processed data for the public dashboard, with privacy safeguards.

What is published (public/author_daily_risk.csv, public/author_model_comparison.csv):
  * only the columns the dashboard needs (ALLOWED_COLUMNS), no raw Garmin data, no GPS, no activity names,
    no sleep start/end times
  * every date shifted by a whole number of weeks, so weekdays and seasons stay comparable but real calendar
    dates cannot be read off the page
  * free-text context notes removed; only the context kind (travel, heat, ...) remains

The shift is kept out of the repository in data/public_shift.json (gitignored); without it the shift is not
published anywhere. Delete that file to draw a new random shift.

    uv run python src/export_public.py
"""

import json
import random
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "garmin.duckdb"
SHIFT_FILE = ROOT / "data" / "public_shift.json"
OUT = ROOT / "public"
COMPARISON_SQL = ROOT / "sql" / "analysis" / "a5_model_comparison.sql"

# Columns the dashboard reads. Anything not listed here is never published.
ALLOWED_COLUMNS = [
    "date", "illness_risk", "injury_risk",
    "ns_night_hr", "ns_baseline", "ns_rise", "ns_candidate_nights", "ns_state",
    "rhr_z", "sleep_hr_z", "hrv_z", "resp_z",
    "hrv_ms", "ln_rmssd_7d", "hrv_normal_low", "hrv_normal_high", "hrv_below_normal",
    "resp_avg", "resp_base", "resp_baseline_mean", "resp_sd", "resp_elevated",
    "sleep_7d", "sleep_debt",
    "trimp_today", "strain", "strain_p90", "strain_yesterday", "ewma_acwr", "ewma_acwr_yesterday", "training_related",
    "context_kind", "is_sick_logged",
]


def shift_days() -> int:
    """A private, stable shift: a random whole number of weeks between 80 and 130 weeks back."""
    if not SHIFT_FILE.exists():
        SHIFT_FILE.write_text(json.dumps({"shift_days": -7 * random.randint(80, 130)}))
    return json.loads(SHIFT_FILE.read_text())["shift_days"]


def main() -> None:
    shift = shift_days()
    assert shift % 7 == 0, "the shift must be a whole number of weeks (keeps weekdays)"
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)
    cols = ", ".join(c for c in ALLOWED_COLUMNS if c != "date")
    con.execute(f"""
        COPY (
            SELECT (date + INTERVAL ({shift}) DAY)::DATE AS date, {cols}, NULL::VARCHAR AS context_notes
            FROM daily_risk
            ORDER BY date
        ) TO '{OUT / 'author_daily_risk.csv'}' (HEADER)
    """)
    # the comparison table contains no dates; it is computed on the full history like the README figure
    con.execute("SET VARIABLE since = (SELECT MIN(date) + INTERVAL 28 DAY FROM daily_wellness)")
    comparison = con.sql(COMPARISON_SQL.read_text()).df()
    comparison = comparison[~comparison.model.str.contains("overnight_hr|sleep_window_hr")]
    comparison.to_csv(OUT / "author_model_comparison.csv", index=False)
    rows, first, last = con.execute(f"""
        SELECT COUNT(*), MIN(date + INTERVAL ({shift}) DAY)::DATE, MAX(date + INTERVAL ({shift}) DAY)::DATE
        FROM daily_risk
    """).fetchone()
    con.close()
    print(f"wrote public/author_daily_risk.csv ({rows} days, shifted range {first} → {last})")
    print("wrote public/author_model_comparison.csv")


if __name__ == "__main__":
    main()
