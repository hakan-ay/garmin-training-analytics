"""Risk explainer: for a selected day, shows illness and overload risk, the evidence that PROVES it,
and the published methods each piece of evidence is based on.

All risk logic lives in sql/09_daily_risk.sql; this file only reads and explains.

    uv run streamlit run app/dashboard.py
    GARMIN_DB=some/other.duckdb uv run streamlit run app/dashboard.py   # any database built by src/build_db.py

Datasets (switchable in the page, or with ?dataset=author|demo|local):
  author  the author's own data as published by src/export_public.py (dates shifted, notes removed)
  demo    synthetic athlete with designed scenarios; its database is built automatically on first use
  local   your own database with real dates (only when data/garmin.duckdb exists, i.e. never on the hosted demo)
"""

import os
import sys
import threading
from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import build_db  # noqa: E402

REAL_DB = ROOT / "data" / "garmin.duckdb"
DEMO_DB = ROOT / "demo" / "demo.duckdb"
AUTHOR_RISK = ROOT / "public" / "author_daily_risk.csv"
AUTHOR_COMPARISON = ROOT / "public" / "author_model_comparison.csv"
COMPARISON_SQL = ROOT / "sql" / "analysis" / "a5_model_comparison.sql"

st.set_page_config(page_title="Risk Dashboard", page_icon="❤️", layout="wide")


def demo_db_is_complete() -> bool:
    try:
        with duckdb.connect(str(DEMO_DB), read_only=True) as con:
            return con.execute("SELECT COUNT(*) FROM daily_risk").fetchone()[0] > 0
    except duckdb.Error:
        return False


@st.cache_resource(show_spinner="Building the demo database (first visit only)…")
def build_demo_db() -> Path:
    """Builds the synthetic demo database once per server process.

    Several visitors (and the host's health check) can hit a fresh deployment at the same time, and DuckDB allows
    only one writer per file. So the database is built under a unique temporary name and moved into place only when
    complete: DEMO_DB either does not exist or is a finished database, never a half-written one.
    """
    if DEMO_DB.exists() and not demo_db_is_complete():
        DEMO_DB.unlink()  # left behind by an older build that crashed half-way
    if not DEMO_DB.exists():
        tmp = DEMO_DB.with_name(f"demo.{os.getpid()}.{threading.get_ident()}.building.duckdb")
        build_db.build(raw_dir=ROOT / "demo" / "raw", labels_dir=ROOT / "demo", db_path=tmp, export_dir=None)
        os.replace(tmp, DEMO_DB)
    return DEMO_DB


def available_datasets() -> dict[str, str]:
    """Dataset key → label, in display order. 'local' only exists on a machine with its own database."""
    datasets = {}
    if os.getenv("GARMIN_DB") or REAL_DB.exists():
        datasets["local"] = "My data (local)"
    if AUTHOR_RISK.exists():
        datasets["author"] = "Author's data"
    datasets["demo"] = "Demo scenarios (synthetic)"
    return datasets


# ---------- theme (dataviz reference palette, light and dark steps chosen separately) ----------
DARK = (st.context.theme.type or "light") == "dark"
C = {
    "surface": "#1a1a19" if DARK else "#fcfcfb",
    "ink": "#f0efec" if DARK else "#0b0b0b",
    "ink2": "#c3c2b7" if DARK else "#52514e",
    "muted": "#898781",
    "grid": "#2c2c2a" if DARK else "#e1e0d9",
    "axis": "#383835" if DARK else "#c3c2b7",
    "series": "#3987e5" if DARK else "#2a78d6",   # signal line (slot 1)
    "load": "#d95926" if DARK else "#eb6834",     # training load (slot 2)
    "band": "rgba(137,135,129,0.18)",             # personal normal range
    "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
}
STATUS = {  # status always comes with an icon + label, never colour alone
    "alert": ("critical", "▲", "High risk"),
    "watch": ("warning", "◆", "Watch"),
    "normal": ("good", "✓", "Normal"),
    "no_baseline": ("muted", "–", "No baseline yet"),
    "high": ("critical", "▲", "High risk"),
    "elevated": ("warning", "◆", "Elevated"),
    "insufficient_load": ("muted", "–", "Not enough training data"),
}
CONTEXT_LABELS = {"heat": "heat", "travel": "travel", "race_recovery": "post-race recovery", "alcohol": "alcohol",
                  "late_night": "late night", "altitude": "altitude", "race": "race", "vaccine": "vaccination",
                  "stress": "high stress"}
SOURCES = {
    "Alavi 2022": "Alavi et al., Real-time alerting system for COVID-19 and other stress events using wearable data, "
                  "Nature Medicine 28:175-184 (2022). Reference code: github.com/StanfordBioinformatics/wearable-infection",
    "Plews 2012": "Plews et al., Heart rate variability in elite triathletes, is variation in variability the key to "
                  "effective training? Eur J Appl Physiol 112:3729-41 (2012); AIS HRV best-practice (2023)",
    "Natarajan 2020": "Natarajan, Su & Heneghan, Assessment of physiological signs associated with COVID-19 measured "
                      "using wearable devices, npj Digital Medicine 3:156 (2020)",
    "Prather 2015": "Prather et al., Behaviorally assessed sleep and susceptibility to the common cold, SLEEP 38:1353-9 (2015)",
    "Banister 1991": "Banister, Modeling elite athletic performance (TRIMP); Morton, Fitz-Clarke & Banister, "
                     "J Appl Physiol 69:1171-7 (1990)",
    "Foster 1998": "Foster, Monitoring training in athletes with reference to overtraining syndrome, "
                   "Med Sci Sports Exerc 30:1164-8 (1998)",
    "Williams 2017": "Williams et al., Better way to determine the acute:chronic workload ratio? Br J Sports Med 51:209-10 (2017); "
                     "critique: Impellizzeri et al., Int J Sports Physiol Perform (2020)",
}


def cite(key: str) -> str:
    return f"<sup title='{SOURCES[key]}'>[{key}]</sup>"


@st.cache_data
def load_db(db_path: str, db_mtime: float) -> tuple[pd.DataFrame, pd.DataFrame]:  # mtime: refresh after a rebuild
    con = duckdb.connect(db_path, read_only=True)
    risk = con.sql("SELECT * FROM daily_risk ORDER BY date").df()
    comparison = con.sql(COMPARISON_SQL.read_text()).df()
    con.close()
    risk["date"] = pd.to_datetime(risk["date"])
    return risk, comparison


@st.cache_data
def load_author(csv_mtime: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    risk = pd.read_csv(AUTHOR_RISK, parse_dates=["date"])
    return risk, pd.read_csv(AUTHOR_COMPARISON)


def load(dataset: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if dataset == "author":
        return load_author(AUTHOR_RISK.stat().st_mtime)
    db = build_demo_db() if dataset == "demo" else Path(os.getenv("GARMIN_DB") or REAL_DB)
    return load_db(str(db), db.stat().st_mtime)


# ---------- evidence sentences ----------
def illness_reasons(r: pd.Series) -> list[str]:
    out = []
    if pd.notna(r.ns_night_hr):
        if r.ns_rise >= 3:
            nights = f", **{r.ns_candidate_nights:.0f} nights** in a row" if r.ns_candidate_nights >= 2 else ""
            out.append(f"**Resting heart rate {r.ns_night_hr:.0f} bpm**: **{r.ns_rise:+.0f} bpm** above the personal "
                       f"baseline ({r.ns_baseline:.0f}, median of the whole history){nights}. "
                       f"Thresholds: +3 yellow, +4 red, 2 nights in a row {cite('Alavi 2022')}")
        else:
            out.append(f"Resting heart rate {r.ns_night_hr:.0f} bpm, close to the baseline ({r.ns_baseline:.0f}) "
                       f"{cite('Alavi 2022')}")
    else:
        out.append("No resting heart rate for this night (watch not worn).")
    if pd.notna(r.ln_rmssd_7d):
        hrv7 = np.exp(r.ln_rmssd_7d)
        low = np.exp(r.hrv_normal_low) if pd.notna(r.hrv_normal_low) else None
        if r.hrv_below_normal:
            out.append(f"**7-day HRV mean {hrv7:.0f} ms**: below the personal normal range (lower bound {low:.0f} ms), "
                       f"the body is under stress {cite('Plews 2012')}")
        elif low is not None:
            out.append(f"7-day HRV mean {hrv7:.0f} ms, within the normal range {cite('Plews 2012')}")
    if r.resp_elevated:
        out.append(f"**Respiration rate {r.resp_avg:.0f}/min**: at least 3 above the normal ({r.resp_base:.0f}) "
                   f"{cite('Natarajan 2020')}")
    if r.sleep_debt:
        out.append(f"Average sleep over the last 7 nights **{r.sleep_7d:.1f} h**: ≤6 hours of sleep is linked to a "
                   f"~4x higher risk of catching a cold {cite('Prather 2015')}")
    # confounders: explain why the risk is an alert or only "watch"
    if r.training_related:
        why = (f"yesterday's weekly strain {r.strain_yesterday:.0f} (your 90th percentile is {r.strain_p90:.0f})"
               if pd.notna(r.strain_p90) and pd.notna(r.strain_yesterday) and r.strain_yesterday > r.strain_p90
               else f"acute:chronic load ratio {r.ewma_acwr_yesterday:.2f}")
        out.append(f"**High training load**: {why}. The heart rate rise may be recovery {cite('Foster 1998')}")
    if pd.notna(r.context_kind):
        notes = f" ({r.context_notes})" if pd.notna(r.context_notes) else ""
        out.append(f"Logged context: **{CONTEXT_LABELS.get(r.context_kind, r.context_kind)}**{notes}, "
                   "which can leave the same physiological signature.")
    if r.illness_risk == "alert":
        out.append("No unusual training load and no logged travel / heat: **the deviation is not explained by training.**")
    return out


def injury_reasons(r: pd.Series) -> list[str]:
    if r.injury_risk == "insufficient_load":
        return ["Training load in recent weeks is too low to compute a ratio."]
    out = [f"Acute (7-day) / chronic (28-day) load ratio **{r.ewma_acwr:.2f}**, exponentially weighted "
           f"{cite('Williams 2017')}"]
    if r.ewma_acwr > 1.5:
        out.append("Load increased **more than 1.5x** your usual level: the zone associated with injury risk.")
    elif r.ewma_acwr > 1.3:
        out.append("Load increased 1.3-1.5x: progress carefully.")
    if pd.notna(r.strain) and pd.notna(r.strain_p90) and r.strain > r.strain_p90:
        out.append(f"Weekly **strain {r.strain:.0f}**: above your personal 90th percentile ({r.strain_p90:.0f}), "
                   f"load is both high and monotonous {cite('Foster 1998')}")
    if r.hrv_below_normal:
        out.append(f"7-day HRV mean below the normal range: recovery is not complete {cite('Plews 2012')}")
    if r.injury_risk == "normal":
        out.append("Load progression and recovery are in line with your usual level.")
    return out


def status_tile(title: str, level: str, reasons: list[str]) -> None:
    key, icon, label = STATUS[level]
    with st.container(border=True):
        st.markdown(f"<div style='color:{C['ink2']};font-size:0.9rem'>{title}</div>"
                    f"<div style='display:flex;align-items:center;gap:10px;margin:4px 0 8px'>"
                    f"<span style='background:{C[key]};color:#fff;border-radius:6px;padding:2px 10px;"
                    f"font-weight:600'>{icon}</span>"
                    f"<span style='font-size:1.6rem;font-weight:600;color:{C['ink']}'>{label}</span></div>",
                    unsafe_allow_html=True)
        st.markdown("\n".join(f"- {x}" for x in reasons), unsafe_allow_html=True)


# ---------- chart helpers ----------
def base_layout(fig: go.Figure, title: str, height: int = 230) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=14, color=C["ink"]), x=0, xanchor="left"),
        height=height, margin=dict(l=8, r=8, t=36, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",   # blend into the page background
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=C["ink2"], size=12),
        hovermode="x unified", showlegend=False,
        hoverlabel=dict(bgcolor=C["surface"], font_color=C["ink"], bordercolor=C["axis"]),
    )
    fig.update_xaxes(showgrid=False, linecolor=C["axis"], tickfont_color=C["muted"])
    fig.update_yaxes(gridcolor=C["grid"], gridwidth=1, zeroline=False, tickfont_color=C["muted"])
    return fig


def runs(dates: pd.Series) -> list[tuple]:
    """Turns consecutive days into (start, end) ranges for shading."""
    out, start, prev = [], None, None
    for d in sorted(dates):
        if prev is not None and d - prev <= timedelta(days=1):
            prev = d
            continue
        if start is not None:
            out.append((start, prev))
        start = prev = d
    if start is not None:
        out.append((start, prev))
    return out


def shade_events(fig: go.Figure, win: pd.DataFrame, selected) -> None:
    for a, b in runs(win.loc[win.illness_risk == "alert", "date"]):
        fig.add_vrect(x0=a - timedelta(hours=12), x1=b + timedelta(hours=12), fillcolor=C["critical"],
                      opacity=0.14, line_width=0, layer="below")
    for a, b in runs(win.loc[win.is_sick_logged, "date"]):
        fig.add_vrect(x0=a - timedelta(hours=12), x1=b + timedelta(hours=12), fillcolor=C["muted"],
                      opacity=0.16, line_width=0, layer="below",
                      annotation_text="sick (logged)", annotation_position="top left",
                      annotation_font=dict(size=10, color=C["ink2"]))
    fig.add_vline(x=selected, line_width=1, line_color=C["ink2"])


def nightsignal_chart(win: pd.DataFrame, selected) -> go.Figure:
    """Resting heart rate + NightSignal baseline and +3 / +4 thresholds; candidate nights marked in threshold colour."""
    fig = go.Figure()
    for offset, color, label in ((4, C["critical"], "red threshold (+4)"), (3, C["warning"], "yellow threshold (+3)")):
        fig.add_trace(go.Scatter(x=win.date, y=win.ns_baseline + offset, mode="lines", line_shape="hv",
                                 line=dict(color=color, width=1), hovertemplate=f"{label} %{{y}}<extra></extra>"))
    fig.add_trace(go.Scatter(x=win.date, y=win.ns_baseline, mode="lines", line_shape="hv",
                             line=dict(color=C["muted"], width=1), hovertemplate="baseline %{y}<extra></extra>"))
    fig.add_trace(go.Scatter(x=win.date, y=win.ns_night_hr, mode="lines+markers", connectgaps=False,
                             line=dict(color=C["series"], width=2),
                             marker=dict(size=6, color=C["series"], line=dict(width=2, color=C["surface"])),
                             hovertemplate="resting HR %{y} bpm<extra></extra>"))
    for lo, hi, color in ((3, 4, C["warning"]), (4, 99, C["critical"])):
        hit = win[(win.ns_rise >= lo) & (win.ns_rise < hi)]
        fig.add_trace(go.Scatter(x=hit.date, y=hit.ns_night_hr, mode="markers", hoverinfo="skip",
                                 marker=dict(size=10, color=color, line=dict(width=2, color=C["surface"]))))
    shade_events(fig, win, selected)
    return base_layout(fig, "Resting heart rate: NightSignal baseline and thresholds", height=260)


def hrv_chart(win: pd.DataFrame, selected) -> go.Figure:
    """7-day lnRMSSD mean + personal normal range (baseline ± SWC), shown back-transformed to ms."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=win.date, y=np.exp(win.hrv_normal_high), line=dict(width=0), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=win.date, y=np.exp(win.hrv_normal_low), line=dict(width=0), fill="tonexty",
                             fillcolor=C["band"], hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=win.date, y=win.hrv_ms, mode="markers", hovertemplate="night %{y:.0f} ms<extra></extra>",
                             marker=dict(size=5, color=C["muted"])))
    fig.add_trace(go.Scatter(x=win.date, y=np.exp(win.ln_rmssd_7d), mode="lines", connectgaps=False,
                             line=dict(color=C["series"], width=2), hovertemplate="7-day mean %{y:.0f} ms<extra></extra>"))
    shade_events(fig, win, selected)
    return base_layout(fig, "HRV: 7-day mean and personal normal range", height=260)


def metric_chart(win, col, base, sd, title, unit, selected, sd_floor):
    fig = go.Figure()
    band_sd = win[sd].clip(lower=sd_floor)
    fig.add_trace(go.Scatter(x=win.date, y=win[base] + band_sd, line=dict(width=0), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=win.date, y=win[base] - band_sd, line=dict(width=0), fill="tonexty",
                             fillcolor=C["band"], hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=win.date, y=win[base], line=dict(color=C["muted"], width=1),
                             hovertemplate="baseline %{y:.1f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=win.date, y=win[col], mode="lines+markers", connectgaps=False,
                             line=dict(color=C["series"], width=2),
                             marker=dict(size=6, color=C["series"], line=dict(width=2, color=C["surface"])),
                             hovertemplate=f"%{{y:.1f}} {unit}<extra></extra>"))
    shade_events(fig, win, selected)
    return base_layout(fig, title)


def contribution_chart(r: pd.Series) -> go.Figure:
    # Every signal is turned to the "risk direction": positive = raises risk (sign flipped for HRV)
    items = [("Resting HR", r.rhr_z), ("Sleeping HR", r.sleep_hr_z),
             ("HRV (inverted)", -r.hrv_z if pd.notna(r.hrv_z) else None), ("Respiration", r.resp_z)]
    items = [(k, v) for k, v in items if pd.notna(v)]
    fig = go.Figure(go.Bar(
        y=[k for k, _ in items], x=[v for _, v in items], orientation="h", width=0.5,
        marker=dict(color=[C["critical"] if v > 0 else C["series"] for _, v in items], cornerradius=4),
        text=[f"{v:+.1f}σ" for _, v in items], textposition="outside", textfont_color=C["ink2"],
        hovertemplate="%{y}: %{x:+.2f} SD<extra></extra>",
    ))
    lim = max(3.0, max((abs(v) for _, v in items), default=0) + 1)
    fig.update_xaxes(range=[-lim, lim], zeroline=True, zerolinecolor=C["axis"], zerolinewidth=1)
    return base_layout(fig, "Tonight's signals vs the last 28 days (red = towards higher risk)", height=220)


def line_with_threshold(win, col, title, hover, lines: list[tuple], band=None) -> go.Figure:
    fig = go.Figure()
    if band:
        fig.add_hrect(y0=band[0], y1=band[1], fillcolor=C["good"], opacity=0.08, line_width=0, layer="below",
                      annotation_text=band[2], annotation_position="top left",
                      annotation_font=dict(size=10, color=C["ink2"]))
    for y, color, label in lines:
        if isinstance(y, str):
            fig.add_trace(go.Scatter(x=win.date, y=win[y], mode="lines", line=dict(color=color, width=1),
                                     hovertemplate=f"{label} %{{y:.0f}}<extra></extra>"))
        else:
            fig.add_hline(y=y, line_width=1, line_color=color, annotation_text=label,
                          annotation_position="top right", annotation_font=dict(size=10, color=C["ink2"]))
    fig.add_trace(go.Scatter(x=win.date, y=win[col], mode="lines", line=dict(color=C["series"], width=2),
                             hovertemplate=hover))
    fig.add_vline(x=selected, line_width=1, line_color=C["ink2"])
    return base_layout(fig, title)


# ---------- page ----------
st.title("Illness and overload risk")
st.caption("Every signal is compared with your own history and is based on a published method. "
           "Hover over a bracketed source tag to see the full reference.")
DATASETS = available_datasets()
requested = st.query_params.get("dataset")
dataset = st.segmented_control(
    "Data", list(DATASETS), format_func=DATASETS.get,
    default=requested if requested in DATASETS else next(iter(DATASETS)))
dataset = dataset or next(iter(DATASETS))
st.query_params["dataset"] = dataset
risk, comparison = load(dataset)
if dataset == "author":
    st.info("**The author's own Garmin data** (an n-of-1 self-tracking study, one logged illness). For privacy every "
            "date is shifted by a fixed, undisclosed number of weeks, so weekdays and seasons are preserved but the "
            "calendar dates are not real, and free-text notes are removed.")
elif dataset == "demo":
    st.info("**Synthetic data** generated by `src/make_synthetic.py`, with an injected illness, a training block and "
            "a travel episode. No real person's data is shown.")
else:
    st.info("**Your own data with real dates.** This view only exists locally and is never published.")

valid = risk.loc[risk.illness_risk != "no_baseline", "date"]
c1, c2 = st.columns([1, 1])
# ?date=YYYY-MM-DD links to a specific day (share with a coach, README demo)
try:
    default_day = pd.Timestamp(st.query_params.get("date", valid.max())).date()
except ValueError:
    default_day = valid.max().date()
default_day = min(max(default_day, valid.min().date()), valid.max().date())
with c1:
    selected = pd.Timestamp(st.date_input("Day", value=default_day,
                                          min_value=valid.min().date(), max_value=valid.max().date()))
st.query_params["date"] = selected.date().isoformat()
with c2:
    window = st.segmented_control("Range", ["30 days", "60 days", "90 days", "180 days"], default="60 days")
days = int(window.split()[0]) if window else 60
# Default: only data up to that day, i.e. what the model knew on that day (no hindsight bias)
show_after = st.toggle("Also show the next 7 days (retrospective check)", value=False)

row = risk.loc[risk.date == selected].iloc[0]
win = risk[(risk.date > selected - timedelta(days=days))
           & (risk.date <= selected + timedelta(days=7 if show_after else 0))]

t1, t2 = st.columns(2)
with t1:
    status_tile("Illness / immune risk", row.illness_risk, illness_reasons(row))
with t2:
    status_tile("Overload / injury risk", row.injury_risk, injury_reasons(row))

st.subheader("Evidence: illness signals")
st.caption("The alert rule is NightSignal (Alavi et al. 2022, Nature Medicine): resting heart rate at least 4 bpm above "
           "the median of the whole history on 2 consecutive nights is red, 3 bpm is yellow. Red shading = alert days, "
           "vertical line = selected day.")
g1, g2 = st.columns(2)
with g1:
    st.plotly_chart(nightsignal_chart(win, selected), use_container_width=True)
with g2:
    st.plotly_chart(hrv_chart(win, selected), use_container_width=True)
g3, g4 = st.columns(2)
with g3:
    st.plotly_chart(metric_chart(win, "resp_avg", "resp_baseline_mean", "resp_sd", "Respiration rate", "/min",
                                 selected, 0.3), use_container_width=True)
with g4:
    st.plotly_chart(contribution_chart(row), use_container_width=True)
st.caption("The bottom-right chart is descriptive and never raises an alert: it shows how many standard deviations "
           "each signal is from the last 28 days.")

st.subheader("Evidence: training load and recovery")
st.caption("Load is measured with Banister TRIMP (duration × heart rate reserve × exponential intensity weight). "
           "A heart rate rise after heavy load may be recovery rather than illness; the model separates the two.")
fig = go.Figure(go.Bar(x=win.date, y=win.trimp_today, marker=dict(color=C["load"], cornerradius=4),
                       hovertemplate="TRIMP %{y:.0f}<extra></extra>"))
shade_events(fig, win, selected)
st.plotly_chart(base_layout(fig, "Daily training load (Banister TRIMP)", height=200), use_container_width=True)

l1, l2 = st.columns(2)
with l1:
    if win.ewma_acwr.notna().sum() == 0:
        st.info("Not enough history in this range for the acute:chronic ratio (first 28 days).")
    st.plotly_chart(line_with_threshold(
        win, "ewma_acwr", "Acute:chronic load ratio (EWMA)", "ratio %{y:.2f}<extra></extra>",
        [(1.5, C["critical"], "1.5 high risk")], band=(0.8, 1.3, "0.8-1.3")), use_container_width=True)
with l2:
    st.plotly_chart(line_with_threshold(
        win, "strain", "Weekly strain (Foster)", "strain %{y:.0f}<extra></extra>",
        [("strain_p90", C["critical"], "personal 90th percentile")]), use_container_width=True)
st.caption("The ability of the acute:chronic ratio to predict injury is debated (Impellizzeri 2020): "
           "it shows association, not causation.")

with st.expander("Methods and validation"):
    st.markdown(
        "**Why these methods?** Every signal is based on a peer-reviewed study, and the thresholds are the study's own, "
        "not ours. The SQL implementation of NightSignal was tested against Stanford's original code and produced "
        "identical results (197 nights, 27 alert days, zero differences).\n\n"
        "**Adaptation:** NightSignal's original input is the mean step-free heart rate between 00:00 and 06:59. For a "
        "late sleeper much of that window is spent awake, and it gave the worst results in the comparison, so the same "
        "rules are applied to Garmin's daily resting heart rate.")
    st.caption("Model comparison over the period with intraday heart rate data. WARNING: very few logged illnesses; "
               "lead_days comes from single events, while the false-alarm rate is based on all healthy days.")
    st.dataframe(comparison, hide_index=True, use_container_width=True)
    st.markdown("**Sources**\n\n" + "\n".join(f"- **{k}**: {v}" for k, v in SOURCES.items()))

with st.expander("Table view (selected range)"):
    st.dataframe(win[["date", "illness_risk", "ns_night_hr", "ns_baseline", "ns_rise", "ns_state", "hrv_ms",
                      "hrv_below_normal", "resp_avg", "resp_elevated", "sleep_7d", "trimp_today", "strain",
                      "ewma_acwr", "injury_risk", "context_kind"]],
                 hide_index=True, use_container_width=True)
