"""Renders the README findings figures to reports/figures/*.png.

Privacy rule for every published figure: no calendar dates on the axes. Time is shown as days relative to an event,
month of year, or not at all.

    uv run python src/make_figures.py            # from your own database (data/garmin.duckdb)
    uv run python src/make_figures.py --demo     # from the synthetic demo database
"""

import argparse
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "figures"
ANALYSIS = ROOT / "sql" / "analysis"

# dataviz reference palette (light mode); categorical slots in fixed order, status colours reserved for status
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
CRITICAL, WARNING, GOOD = "#d03b3b", "#fab219", "#0ca30c"

plt.rcParams.update({
    "font.family": "sans-serif", "font.size": 10, "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.titlesize": 11, "axes.titlecolor": INK,
    "axes.titlelocation": "left", "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb", "savefig.dpi": 160,
})


def save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote reports/figures/{name}.png")


def f1_event_study(con) -> None:
    df = con.sql((ANALYSIS / "a4_illness_event_study.sql").read_text()).df()
    if df.empty:
        print("skip f1: no logged illness")
        return
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.2), sharex=True)
    for ax, col, title, unit in ((axes[0], "rhr_rise_bpm", "Resting heart rate vs baseline", "bpm"),
                                 (axes[1], "hrv_change_pct", "Overnight HRV vs baseline", "%")):
        alert = df[df.share_alert > 0]
        for d in alert.day_offset:
            ax.axvspan(d - 0.5, d + 0.5, color=CRITICAL, alpha=0.12, lw=0)
        ax.axvline(0, color=INK2, lw=1)
        ax.axhline(0, color=AXIS, lw=1)
        ax.plot(df.day_offset, df[col], color=SLOTS[0], lw=2, marker="o", ms=5, mec="#fcfcfb", mew=1.5)
        ax.set_title(title, pad=18)
        ax.set_ylabel(unit)
        ax.set_xlabel("days from symptom onset")
        ax.set_xticks(range(-7, 8))
        # labels above the plot area so they never sit on the data
        ax.text(0.2, 1.01, "symptoms start", transform=ax.get_xaxis_transform(), ha="left", fontsize=8, color=INK2)
        if not alert.empty:
            ax.text(alert.day_offset.mean(), 1.01, "red alert", transform=ax.get_xaxis_transform(), ha="center",
                    fontsize=8, color=CRITICAL)
    save(fig, "f1_illness_event_study")


def f2_model_comparison(con) -> None:
    sql = (ANALYSIS / "a5_model_comparison.sql").read_text()
    # full history; only models whose input covers the whole period are comparable here
    con.execute("SET VARIABLE since = (SELECT MIN(date) + INTERVAL 28 DAY FROM daily_wellness)")
    full = con.sql(sql).df()
    full = full[~full.model.str.contains("overnight_hr|sleep_window_hr")]
    con.execute("RESET VARIABLE since")
    common = con.sql(sql).df()
    fixed = common[common.model == "NightSignal overnight_hr / expanding / red"].assign(
        model="NightSignal, original 00:00-06:59 window (intraday period only)")
    rows = pd.concat([full, fixed])
    names = {"NightSignal garmin_rhr / expanding / red": "NightSignal rules on daily RHR (selected)",
             "NightSignal garmin_rhr / rolling28 / red": "NightSignal, 28-night rolling baseline",
             "HRV 7d < SWC (Plews)": "HRV 7-day mean below SWC",
             "Ours: RHR ≥3 × 3 nights": "Our first rule (RHR ≥3 bpm × 3 nights)",
             "Ours: 2+ signals z ≥ 1.5": "Our first z-score model"}
    rows["label"] = rows.model.map(names).fillna(rows.model)
    rows = rows[rows.label.isin(list(names.values()) + list(fixed.model))].sort_values("unexplained_per_30d")
    fig, ax = plt.subplots(figsize=(8, 3.2))
    colors = [SLOTS[0] if "selected" in l else MUTED for l in rows.label]
    ax.barh(rows.label, rows.unexplained_per_30d, color=colors, height=0.55)
    for y, (v, lead) in enumerate(zip(rows.unexplained_per_30d, rows.lead_days)):
        ax.text(v + 0.02, y, f"{v:.2f}   (caught {lead:.0f} d early)", va="center", fontsize=8, color=INK2)
    ax.set_xlabel("unexplained false alarms per 30 healthy days (lower is better)")
    ax.set_title("Alert rules compared on the same data")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, rows.unexplained_per_30d.max() * 1.6)
    save(fig, "f2_model_comparison")


def f3_sleep_window(con) -> None:
    df = con.sql("""
        SELECT date,
               EXTRACT(EPOCH FROM LEAST(sleep_end_local, date::TIMESTAMP + INTERVAL 7 HOUR)
                                - GREATEST(sleep_start_local, date::TIMESTAMP)) / 3600 AS hours_asleep_in_window
        FROM daily_wellness
        WHERE sleep_start_local IS NOT NULL
    """).df()
    df["share"] = (df.hours_asleep_in_window.clip(lower=0, upper=7) / 7 * 100)
    fig, ax = plt.subplots(figsize=(7, 3))
    ax.hist(df.share, bins=range(0, 105, 10), color=SLOTS[0], rwidth=0.9)
    ax.axvline(df.share.median(), color=INK2, lw=1)
    ax.text(df.share.median(), 1.01, f"median {df.share.median():.0f}%", transform=ax.get_xaxis_transform(),
            ha="center", fontsize=8, color=INK2)
    ax.set_xlabel("share of NightSignal's fixed 00:00-06:59 window actually spent asleep (%)")
    ax.set_ylabel("nights")
    ax.set_title("A fixed clock window misses a late sleeper's night", pad=16)
    save(fig, "f3_sleep_window_coverage")


def f4_seasonal_mix(con) -> None:
    df = con.sql("""
        SELECT MONTH(start_time) AS m,
               CASE WHEN sport_group IN ('mountain', 'other') THEN 'other' ELSE sport_group END AS sport,
               SUM(duration_min) / 60 AS hours
        FROM activities GROUP BY 1, 2
    """).df()
    order = ["cycling", "running", "walk_hike", "strength_climb", "swimming", "winter", "other"]
    pivot = df.pivot_table(index="m", columns="sport", values="hours", aggfunc="sum").reindex(range(1, 13)).fillna(0)
    share = pivot.div(pivot.sum(axis=1).replace(0, 1), axis=0) * 100
    fig, ax = plt.subplots(figsize=(9, 3.4))
    bottom = pd.Series(0.0, index=share.index)
    for i, sport in enumerate(s for s in order if s in share.columns):
        ax.bar(share.index, share[sport], bottom=bottom, color=SLOTS[i], width=0.7,
               edgecolor="#fcfcfb", linewidth=1, label=sport.replace("_", " / "))
        bottom += share[sport]
    ax.set_xticks(range(1, 13), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_ylabel("% of training hours")
    ax.set_title("Sport mix by month of year")
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=7, fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    save(fig, "f4_seasonal_sport_mix")


def f6_load_recovery(con) -> None:
    df = con.sql((ANALYSIS / "a3_load_vs_next_day_recovery.sql").read_text()).df()
    labels = df.yesterday_load.str.split(": ").str[1]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3))
    for ax, col, title, unit in ((axes[0], "avg_rhr_rise_bpm", "Next-morning resting HR vs baseline", "bpm"),
                                 (axes[1], "avg_hrv_change_pct", "Next-morning HRV vs baseline", "%")):
        ax.bar(labels, df[col], color=SLOTS[1], width=0.55)
        ax.axhline(0, color=AXIS, lw=1)
        for x, v in enumerate(df[col]):
            ax.text(x, v, f"{v:+.1f}", ha="center", va="bottom" if v >= 0 else "top", fontsize=8, color=INK2)
        ax.set_title(title)
        ax.set_ylabel(unit)
        ax.set_xlabel("yesterday's training load (personal tertiles)")
        ax.grid(axis="x", visible=False)
    save(fig, "f6_load_vs_next_day_recovery")


def f7_external_validation(con) -> None:
    """Aggregates from src/validate_stanford.py; skipped when the external validation has not been run."""
    path = ROOT / "reports" / "external_validation" / "stanford_event_study.csv"
    if not path.exists():
        print("skip f7: run src/validate_stanford.py first")
        return
    ev = pd.read_csv(path)
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.2), sharex=True)
    ax = axes[0]
    ax.fill_between(ev.day, ev.q1_rise_bpm, ev.q3_rise_bpm, color=MUTED, alpha=0.2, lw=0)
    ax.plot(ev.day, ev.median_rise_bpm, color=SLOTS[0], lw=2, marker="o", ms=4, mec="#fcfcfb", mew=1)
    ax.axhline(0, color=AXIS, lw=1)
    ax.set_title("Nightly heart rate above baseline", pad=18)
    ax.set_ylabel("bpm (median, IQR)")
    ax = axes[1]
    ax.bar(ev.day, ev.share_red_alert * 100, color=CRITICAL, width=0.75)
    ax.set_title("Share of cases in red alert", pad=18)
    ax.set_ylabel("%")
    ax.grid(axis="x", visible=False)
    for ax in axes:
        ax.axvline(0, color=INK2, lw=1)
        ax.text(0.5, 1.01, "symptoms start", transform=ax.get_xaxis_transform(), ha="left", fontsize=8, color=INK2)
        ax.set_xlabel("days from symptom onset")
    fig.suptitle("External cohort: COVID-19 cases aligned on symptom onset (Stanford, Mishra et al. 2020)",
                 x=0.01, ha="left", fontsize=9, color=INK2, y=1.04)
    save(fig, "f7_external_validation")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    db = ROOT / ("demo/demo.duckdb" if args.demo else "data/garmin.duckdb")
    con = duckdb.connect(str(db), read_only=True)
    for f in (f1_event_study, f2_model_comparison, f3_sleep_window, f4_seasonal_mix, f6_load_recovery,
              f7_external_validation):
        f(con)
    con.close()


if __name__ == "__main__":
    main()
