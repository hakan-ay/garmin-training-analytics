"""Checks that sql/07_nightsignal.sql produces exactly the same alerts as Stanford's original nightsignal.py.

Reference data and code: github.com/StanfordBioinformatics/wearable-infection (Apache-2.0).
    1. Download the sample Fitbit file (P682517-Fitbit-rhr.csv).
    2. Run the original code: python nightsignal.py --device=Fitbit --restinghr=P682517-Fitbit-rhr.csv → NS-signals.json
    3. uv run python tests/test_nightsignal.py P682517-Fitbit-rhr.csv NS-signals.json
"""

import json
import sys
from pathlib import Path

import duckdb

SQL = Path(__file__).resolve().parents[1] / "sql" / "07_nightsignal.sql"


def our_alerts(csv_path: str) -> dict[str, int]:
    con = duckdb.connect()
    # Fitbit file: Datetime, HR_Value, f0_, ST_Value (per-minute heart rate + steps)
    con.execute("""
        CREATE TABLE ns_minutes AS
        SELECT 'test' AS series, CAST(Datetime AS TIMESTAMP) AS ts_local,
               CAST(HR_Value AS DOUBLE) AS hr, CAST(ST_Value AS DOUBLE) AS steps
        FROM read_csv(?, header = true, all_varchar = true)
    """, [csv_path])
    con.execute("CREATE TABLE ns_nightly_extra (series VARCHAR, date DATE, avg_hr INTEGER)")
    con.execute(SQL.read_text())
    rows = con.execute("""
        SELECT strftime(date, '%Y-%m-%d'), state FROM nightsignal
        WHERE baseline_kind = 'expanding'
    """).fetchall()
    return dict(rows)


def reference_alerts(json_path: str) -> dict[str, int]:
    signals = json.loads(Path(json_path).read_text())["nightsignal"]
    return {s["date"][:10]: int(s["val"]) for s in signals}


def main() -> None:
    ours, ref = our_alerts(sys.argv[1]), reference_alerts(sys.argv[2])
    nights_match = set(ours) == set(ref)
    mismatches = {d: (ours.get(d), ref.get(d)) for d in set(ours) | set(ref) if ours.get(d) != ref.get(d)}
    print(f"nights      ours: {len(ours)}  reference: {len(ref)}  same set: {nights_match}")
    print(f"alert days  ours: {sum(v > 0 for v in ours.values())}  reference: {sum(v > 0 for v in ref.values())}")
    if mismatches:
        for d in sorted(mismatches):
            print(f"  DIFF {d}: ours={mismatches[d][0]} reference={mismatches[d][1]}")
        sys.exit(1)
    print("OK: every night and alert state is identical")


if __name__ == "__main__":
    main()
