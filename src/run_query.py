"""Runs the queries in a .sql file against data/garmin.duckdb and prints the result.

Usage:
    uv run python src/run_query.py sql/analysis/a2_weekly_load_rolling.sql
"""

import sys
from pathlib import Path

import duckdb

DB = Path(__file__).resolve().parents[1] / "data" / "garmin.duckdb"

con = duckdb.connect(str(DB), read_only=True)
for path in sys.argv[1:]:
    rel = con.sql(Path(path).read_text())
    if rel is None:
        print(f"{path}: no query to run")
    else:
        rel.show(max_rows=60, max_width=200)
