"""The synthetic demo athlete must show the three behaviours the models are built for (run after build_db.py --demo):
an illness alert before onset, no illness alert during the hard training block, and no alert during the logged trip.

    uv run python tests/test_demo_scenarios.py
"""

from pathlib import Path

import duckdb

DB = Path(__file__).resolve().parents[1] / "demo" / "demo.duckdb"
TRAINING_BLOCK = ("2024-03-11", "2024-03-12")
TRIP = ("2024-06-21", "2024-06-22")


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    risk = dict(con.execute("SELECT strftime(date, '%Y-%m-%d'), illness_risk FROM daily_risk").fetchall())
    onset = con.execute("SELECT strftime(onset_date, '%Y-%m-%d') FROM illness_log").fetchone()[0]
    first_alert = min(d for d, r in risk.items() if r == "alert")
    assert first_alert < onset, f"illness alert {first_alert} should come before onset {onset}"
    assert all(risk[d] != "alert" for d in TRAINING_BLOCK), "the training block must not raise an illness alert"
    assert all(risk[d] != "alert" for d in TRIP), "the logged trip must not raise an illness alert"
    print(f"OK: first alert {first_alert}, onset {onset}; training block and trip stay below alert")


if __name__ == "__main__":
    main()
