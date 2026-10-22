"""External validation of the illness alert on the public Stanford COVID-19 wearables cohort.

Data (not redistributed here; download once, ~380 MB):
  * smartwatch heart rate + steps: https://storage.googleapis.com/gbsc-gcp-project-ipop_public/COVID-19/COVID-19-Wearables.zip
    → unzip *_hr.csv and *_steps.csv into data/external/stanford_covid/COVID-19-Wearables/
  * labels: supplementary tables of Mishra et al., Nature Biomedical Engineering 4:1208 (2020),
    https://static-content.springer.com/esm/art%3A10.1038%2Fs41551-020-00640-6/MediaObjects/41551_2020_640_MOESM3_ESM.xlsx
    → data/external/stanford_covid_supp/tables.xlsx
  Dates in both are shifted by the original authors to protect participants, consistently between data and labels.

The SAME sql/07_nightsignal.sql used for the author's data runs here, one series per participant. Evaluation follows
the event-based reporting of Alavi et al. 2022:
  * per illness: alert in the 7 days before symptom onset (early) or within ±7 days (detected), and the lead time
  * per healthy participant: alert episodes per 30 nights of data (false alarms; there is no confounder log here)

    uv run python src/validate_stanford.py
"""

import argparse
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "external" / "stanford_covid" / "COVID-19-Wearables"
TABLES = ROOT / "data" / "external" / "stanford_covid_supp" / "tables.xlsx"
OUT = ROOT / "reports" / "external_validation"
MIN_CASES = 5   # smallest group size published per day (privacy)


def load_labels(tables: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Illness episodes (participant, kind, onset) and the participants the authors list as potentially healthy."""
    t3 = pd.read_excel(tables, sheet_name="SuppTable3_Fig2a_COVID-19", header=3)
    t4 = pd.read_excel(tables, sheet_name="SuppTable4_Fig2a_Illnesses", header=4)
    t29 = pd.read_excel(tables, sheet_name="SuppTable29_AlarmFreqComparison", header=2)

    episodes = []
    for _, row in t3[t3.Category == "COVID-19"].iterrows():
        symptoms = [pd.Timestamp(d) for d in pd.to_datetime(eval_dates(row.Symptom_dates))]
        diagnosis = [pd.Timestamp(d) for d in pd.to_datetime(eval_dates(row.covid_diagnosis_dates))]
        diag = diagnosis[0] if diagnosis else None
        # COVID onset = the symptom date closest before the diagnosis (within 3 weeks); asymptomatic → diagnosis date
        near = [s for s in symptoms if diag is not None and diag - pd.Timedelta(days=21) <= s <= diag + pd.Timedelta(days=7)]
        onset = min(near, key=lambda s: abs((diag - s).days)) if near else diag
        if onset is not None:
            episodes.append({"participant": row.ParticipantID, "kind": "COVID-19", "onset": onset,
                             "symptomatic": bool(near)})
    other_ids = set(t3[t3.Category.str.startswith("Other Illness")].ParticipantID)
    for _, row in t4.dropna(subset=["shift_sick_start"]).iterrows():
        if row.ParticipantID in other_ids:
            episodes.append({"participant": row.ParticipantID, "kind": "other illness",
                             "onset": pd.Timestamp(row.shift_sick_start), "symptomatic": True})
    healthy = t29[t29.caseCategory == "potential.healthy"][["ParticipantID"]].rename(
        columns={"ParticipantID": "participant"})
    return pd.DataFrame(episodes), healthy


def eval_dates(cell) -> list:
    """The table stores lists like "[Timestamp('2026-07-14 00:00:00'), NaT]" as text."""
    if not isinstance(cell, str):
        return []
    return [p.split("'")[1] for p in cell.split("Timestamp(")[1:]]


def run_nightsignal(con: duckdb.DuckDBPyConnection, data: Path) -> None:
    # minute-level heart rate (mean of the 5-second samples) with that minute's step count; only 00:00-06:59 is used
    con.execute(f"""
        CREATE TABLE ns_minutes AS
        WITH hr AS (
            SELECT "user" AS series, date_trunc('minute', datetime) AS ts_local, AVG(heartrate) AS hr
            FROM read_csv('{data}/*_hr.csv', columns = {{'user': 'VARCHAR', 'datetime': 'TIMESTAMP', 'heartrate': 'DOUBLE'}},
                          header = true)
            WHERE HOUR(datetime) < 7
            GROUP BY ALL
        ),
        st AS (
            SELECT "user" AS series, datetime AS ts_local, steps
            FROM read_csv('{data}/*_steps.csv', columns = {{'user': 'VARCHAR', 'datetime': 'TIMESTAMP', 'steps': 'DOUBLE'}},
                          header = true)
            WHERE HOUR(datetime) < 7
        )
        SELECT hr.series, hr.ts_local, hr.hr, st.steps
        FROM hr JOIN st USING (series, ts_local)
    """)
    con.execute("CREATE TABLE ns_nightly_extra (series VARCHAR, date DATE, avg_hr INTEGER)")
    con.execute((ROOT / "sql" / "07_nightsignal.sql").read_text())


def evaluate(con, episodes: pd.DataFrame, healthy: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    con.register("episodes", episodes)
    con.register("healthy", healthy)
    per_episode = con.sql("""
        WITH rules AS (
            SELECT * FROM (VALUES ('red', 2), ('yellow+red', 1)) AS t(alert_rule, min_state)
        ),
        ev AS (
            SELECT e.participant, e.kind, e.symptomatic, e.onset::DATE AS onset, n.baseline_kind, r.alert_rule,
                   COUNT(*) FILTER (WHERE n.date BETWEEN e.onset::DATE - 21 AND e.onset::DATE + 7)        AS nights_with_data,
                   MIN(n.date) FILTER (WHERE n.state >= r.min_state
                                         AND n.date BETWEEN e.onset::DATE - 7 AND e.onset::DATE + 7)       AS first_alert
            FROM episodes e
            CROSS JOIN rules r
            JOIN nightsignal n ON n.series = e.participant
            GROUP BY ALL
        )
        SELECT *, onset - first_alert AS lead_days
        FROM ev
        WHERE nights_with_data >= 14          -- enough nights around the illness to judge the alert
    """).df()
    false_alarms = con.sql("""
        WITH rules AS (SELECT * FROM (VALUES ('red', 2), ('yellow+red', 1)) AS t(alert_rule, min_state)),
        alerts AS (
            SELECT n.series, n.baseline_kind, r.alert_rule, n.date,
                   n.date - LAG(n.date) OVER (PARTITION BY n.series, n.baseline_kind, r.alert_rule ORDER BY n.date) AS gap
            FROM nightsignal n JOIN healthy h ON h.participant = n.series CROSS JOIN rules r
            WHERE n.state >= r.min_state
        ),
        episodes AS (
            SELECT baseline_kind, alert_rule, COUNT(*) FILTER (WHERE gap IS NULL OR gap > 3) AS n_episodes
            FROM alerts GROUP BY ALL
        ),
        nights AS (
            SELECT n.baseline_kind, COUNT(*) AS n_nights, COUNT(DISTINCT n.series) AS n_people
            FROM nightsignal n JOIN healthy h ON h.participant = n.series WHERE NOT n.is_imputed GROUP BY ALL
        )
        SELECT e.baseline_kind, e.alert_rule, n.n_people, n.n_nights, e.n_episodes,
               ROUND(30.0 * e.n_episodes / n.n_nights, 2) AS false_alarms_per_30_nights
        FROM episodes e JOIN nights n USING (baseline_kind)
        ORDER BY 1, 2
    """).df()
    return per_episode, false_alarms


def event_study(con, episodes: pd.DataFrame) -> pd.DataFrame:
    """Aggregate only: per day relative to COVID-19 onset, the median rise of nightly heart rate over the NightSignal
    baseline, its interquartile range and the share of cases in red alert. Days with fewer than MIN_CASES cases are
    dropped so that no single participant's values can be read off the published file."""
    con.register("covid", episodes[episodes.kind == "COVID-19"])
    return con.sql(f"""
        SELECT CAST(n.date - c.onset::DATE AS INTEGER)                       AS day,
               COUNT(*)                                                        AS cases,
               MEDIAN(n.rise)                                                  AS median_rise_bpm,
               QUANTILE_CONT(n.rise, 0.25)                                     AS q1_rise_bpm,
               QUANTILE_CONT(n.rise, 0.75)                                     AS q3_rise_bpm,
               ROUND(AVG((n.state = 2)::INT), 3)                               AS share_red_alert
        FROM covid c
        JOIN nightsignal n ON n.series = c.participant AND n.baseline_kind = 'expanding' AND NOT n.is_imputed
        WHERE n.date BETWEEN c.onset::DATE - 21 AND c.onset::DATE + 14
        GROUP BY 1
        HAVING COUNT(*) >= {MIN_CASES}
        ORDER BY 1
    """).df()


def summarise(per_episode: pd.DataFrame) -> pd.DataFrame:
    g = per_episode.groupby(["kind", "baseline_kind", "alert_rule"])
    return pd.DataFrame({
        "illnesses": g.size(),
        "detected_±7d": g.first_alert.apply(lambda s: s.notna().sum()),
        "early_(before_onset)": g.lead_days.apply(lambda s: (s > 0).sum()),
        "median_lead_days": g.lead_days.median(),
    }).reset_index()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--tables", type=Path, default=TABLES)
    args = parser.parse_args()
    episodes, healthy = load_labels(args.tables)
    con = duckdb.connect()
    run_nightsignal(con, args.data)
    per_episode, false_alarms = evaluate(con, episodes, healthy)
    summary = summarise(per_episode)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "stanford_detection.csv", index=False)
    false_alarms.to_csv(OUT / "stanford_false_alarms.csv", index=False)
    event_study(con, episodes).to_csv(OUT / "stanford_event_study.csv", index=False)
    print(f"participants with nightly data: {con.execute('SELECT COUNT(DISTINCT series) FROM nightsignal').fetchone()[0]}")
    print(f"labelled illnesses: {len(episodes)}, evaluable (≥14 nights around onset): "
          f"{per_episode[['participant', 'onset']].drop_duplicates().shape[0]}")
    print(summary.to_string(index=False))
    print(false_alarms.to_string(index=False))


if __name__ == "__main__":
    main()
