"""The published author export may only contain allowlisted columns, empty free-text notes and week-aligned dates.

    uv run python tests/test_public_export.py
"""

import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from export_public import ALLOWED_COLUMNS  # noqa: E402

EXPORT = ROOT / "public" / "author_daily_risk.csv"


def main() -> None:
    with open(EXPORT) as f:
        reader = csv.DictReader(f)
        cols, rows = reader.fieldnames, list(reader)
    extra = set(cols) - set(ALLOWED_COLUMNS) - {"context_notes"}
    assert not extra, f"columns outside the allowlist: {extra}"
    assert all(not r["context_notes"] for r in rows), "free-text notes must be empty"
    days = [date.fromisoformat(r["date"]) for r in rows]
    assert all((b - a).days == 1 for a, b in zip(days, days[1:])), "one row per consecutive day"
    print(f"OK: {len(cols)} allowlisted columns, {len(rows)} rows, notes empty")


if __name__ == "__main__":
    main()
