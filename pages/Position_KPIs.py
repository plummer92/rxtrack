from datetime import time
import re

import pandas as pd
import plotly.express as px
import streamlit as st

import App


st.set_page_config(page_title="Position KPIs", page_icon="📏", layout="wide")
App.apply_global_styles()

start_date, end_date = App.render_sidebar()
App.require_management_access("Position KPIs")

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Position KPIs",
        "Baseline refill duration by position window, then show how each tech/day deviates from the normal range.",
        kicker="Performance",
    )
else:
    st.header("Position KPIs")
    st.caption("Baseline refill duration by position window and compare tech/day performance.")


DEFAULT_POSITIONS = [
    {"position": "0500 Tech Refills", "start": "05:00", "end": "09:00", "staff_keywords": "0500", "device_keywords": ""},
    {"position": "0600 Tech Refills", "start": "06:00", "end": "10:00", "staff_keywords": "0600", "device_keywords": ""},
    {"position": "1430 PHP Refills", "start": "14:30", "end": "18:30", "staff_keywords": "1430", "device_keywords": "PHP"},
    {"position": "1430 OR Refills", "start": "14:30", "end": "18:30", "staff_keywords": "1430", "device_keywords": "OR"},
]


def normalize_keywords(value):
    return [part.strip().upper() for part in str(value or "").split(",") if part.strip()]


def coerce_time(value):
    if isinstance(value, time):
        return value
    parsed = pd.to_datetime(str(value or "").strip(), errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.time()


def sessionize_refills(events):
    if events.empty:
        return pd.DataFrame()

    refill_pattern = r"restock|refill|\bload\b|replenish"
    exclude_pattern = r"cancel|unload|empty|outdate|expire"
    df = events.copy()
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"]).copy()
    for col in ["event_type", "user_name", "device", "med_id", "med_desc"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()
    if "qty" not in df.columns:
        df["qty"] = 0
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    event_text = df["event_type"].str.lower()
    df = df[
        event_text.str.contains(refill_pattern, regex=True, na=False)
        & ~event_text.str.contains(exclude_pattern, regex=True, na=False)
    ].copy()
    if df.empty:
        return pd.DataFrame()

    df["tech_key"] = df["user_name"].apply(App.normalize_name)
    df["event_date"] = df["dt"].dt.date
    df["device_norm"] = df["device"].str.upper()
    df = df.sort_values(["tech_key", "event_date", "device_norm", "dt"]).reset_index(drop=True)
    df["prev_tech"] = df["tech_key"].shift()
    df["prev_date"] = df["event_date"].shift()
    df["prev_device"] = df["device_norm"].shift()
    df["prev_dt"] = df["dt"].shift()
    df["gap_sec"] = (df["dt"] - df["prev_dt"]).dt.total_seconds().fillna(0)
    df["new_session"] = (
        (df["tech_key"] != df["prev_tech"])
        | (df["event_date"] != df["prev_date"])
        | (df["device_norm"] != df["prev_device"])
        | (df["gap_sec"] > 20 * 60)
    )
    df["session_id"] = df["new_session"].cumsum()

    sessions = (
        df.groupby("session_id", as_index=False)
        .agg(
            event_date=("event_date", "first"),
            user_name=("user_name", "first"),
            tech_key=("tech_key", "first"),
            device=("device", "first"),
            device_norm=("device_norm", "first"),
            start_dt=("dt", "min"),
            end_dt=("dt", "max"),
            refill_events=("dt", "count"),
            refill_qty=("qty", lambda s: s.abs().sum()),
            meds=("med_id", "nunique"),
        )
    )
    sessions["duration_minutes"] = (
        (sessions["end_dt"] - sessions["start_dt"]).dt.total_seconds().clip(lower=60) / 60
    )
    sessions["events_per_minute"] = sessions["refill_events"] / sessions["duration_minutes"].replace(0, pd.NA)
    return sessions


def attach_schedule_context(sessions, schedule):
    sessions = sessions.copy()
    if schedule.empty:
        sessions["staff_name"] = sessions["user_name"]
        sessions["shift_type"] = ""
        sessions["assignment_type"] = ""
        sessions["schedule_text"] = sessions["user_name"].fillna("").astype(str).str.upper()
        return sessions

    sched = schedule.copy()
    sched["event_date"] = pd.to_datetime(sched["dt"], errors="coerce").dt.date
    sched["tech_key"] = sched["staff_name"].apply(App.normalize_name)
    sched = sched[["event_date", "tech_key", "staff_name", "shift_type", "assignment_type"]].drop_duplicates()
    sessions = sessions.merge(sched, on=["event_date", "tech_key"], how="left")
    sessions["staff_name"] = sessions["staff_name"].fillna(sessions["user_name"])
    for col in ["shift_type", "assignment_type"]:
        sessions[col] = sessions[col].fillna("")
    sessions["schedule_text"] = (
        sessions[["staff_name", "shift_type", "assignment_type"]]
        .fillna("")
        .astype(str)
        .agg(" ".join, axis=1)
        .str.upper()
    )
    return sessions


def contains_any_keyword(series, keywords):
    if not keywords:
        return pd.Series(True, index=series.index)
    pattern = "|".join(re.escape(keyword) for keyword in keywords)
    return series.str.contains(pattern, case=False, regex=True, na=False)


with st.expander("Position window setup", expanded=False):
    st.caption(
        "Keywords are comma-separated. Staff keywords match schedule text; device keywords match cabinet names."
    )
    edited_positions = st.data_editor(
        pd.DataFrame(DEFAULT_POSITIONS),
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        column_config={
            "position": st.column_config.TextColumn("Position"),
            "start": st.column_config.TextColumn("Start (HH:MM)"),
            "end": st.column_config.TextColumn("End (HH:MM)"),
            "staff_keywords": st.column_config.TextColumn("Staff/assignment keywords"),
            "device_keywords": st.column_config.TextColumn("Device keywords"),
        },
        key="position_kpi_windows",
    )

min_sessions = st.slider("Minimum sessions for baseline", 3, 30, 5, key="position_kpi_min_sessions")

with st.spinner("Loading refill sessions..."):
    df_events, _, _, df_sched, _ = App.load_data(start_date, end_date)
    sessions = sessionize_refills(df_events)

if sessions.empty:
    st.warning("No refill/load sessions were found in the selected date range.")
    st.stop()

sessions = attach_schedule_context(sessions, df_sched)

position_frames = []
for pos in edited_positions.to_dict("records"):
    pos_name = str(pos.get("position") or "").strip()
    start_time = coerce_time(pos.get("start"))
    end_time = coerce_time(pos.get("end"))
    staff_keywords = normalize_keywords(pos.get("staff_keywords"))
    keywords = normalize_keywords(pos.get("device_keywords"))
    if not pos_name or start_time is None or end_time is None:
        continue
    temp = sessions.copy()
    session_time = temp["start_dt"].dt.time
    if start_time <= end_time:
        temp = temp[(session_time >= start_time) & (session_time <= end_time)].copy()
    else:
        temp = temp[(session_time >= start_time) | (session_time <= end_time)].copy()
    if staff_keywords:
        temp = temp[contains_any_keyword(temp["schedule_text"], staff_keywords)].copy()
    if keywords:
        temp = temp[contains_any_keyword(temp["device_norm"], keywords)].copy()
    if temp.empty:
        continue
    temp["position"] = pos_name
    position_frames.append(temp)

if not position_frames:
    st.warning("No refill sessions matched the current position windows/keywords.")
    st.stop()

kpi = pd.concat(position_frames, ignore_index=True)
kpi = kpi.drop_duplicates(subset=["position", "session_id"]).copy()

baseline = (
    kpi.groupby("position", as_index=False)
    .agg(
        baseline_sessions=("session_id", "count"),
        baseline_median_min=("duration_minutes", "median"),
        baseline_p75_min=("duration_minutes", lambda s: s.quantile(0.75)),
        baseline_events=("refill_events", "median"),
        baseline_qty=("refill_qty", "median"),
    )
)
baseline = baseline[baseline["baseline_sessions"] >= min_sessions].copy()
if baseline.empty:
    st.warning("No positions met the minimum-session baseline threshold. Lower the minimum or widen the date range.")
    st.stop()

kpi = kpi.merge(baseline, on="position", how="inner")
kpi["duration_delta_min"] = kpi["duration_minutes"] - kpi["baseline_median_min"]
kpi["duration_delta_pct"] = kpi["duration_delta_min"] / kpi["baseline_median_min"].replace(0, pd.NA) * 100
kpi["kpi_status"] = "Within baseline"
kpi.loc[kpi["duration_minutes"] > kpi["baseline_p75_min"], "kpi_status"] = "Slower than baseline"
kpi.loc[kpi["duration_minutes"] < (kpi["baseline_median_min"] * 0.75), "kpi_status"] = "Faster than baseline"

m1, m2, m3, m4 = st.columns(4)
m1.metric("Refill Sessions", f"{len(kpi):,}")
m2.metric("Positions Baselined", f"{baseline['position'].nunique():,}")
m3.metric("Slower Sessions", f"{int((kpi['kpi_status'] == 'Slower than baseline').sum()):,}")
m4.metric("Techs Measured", f"{kpi['staff_name'].nunique():,}")

st.subheader("Position Baselines")
baseline_view = baseline.copy()
baseline_view["baseline_median_time"] = baseline_view["baseline_median_min"].apply(lambda v: App.seconds_to_mmss(v * 60))
baseline_view["baseline_p75_time"] = baseline_view["baseline_p75_min"].apply(lambda v: App.seconds_to_mmss(v * 60))
st.dataframe(
    baseline_view[
        ["position", "baseline_sessions", "baseline_median_time", "baseline_p75_time", "baseline_events", "baseline_qty"]
    ],
    width="stretch",
    hide_index=True,
    column_config={
        "position": st.column_config.TextColumn("Position"),
        "baseline_sessions": st.column_config.NumberColumn("Sessions", format="%d"),
        "baseline_events": st.column_config.NumberColumn("Median Events", format="%.0f"),
        "baseline_qty": st.column_config.NumberColumn("Median Qty", format="%.0f"),
    },
)

trend_tab, tech_tab, detail_tab = st.tabs(["Position Trend", "Tech Deviation", "Session Detail"])

with trend_tab:
    daily = (
        kpi.groupby(["event_date", "position"], as_index=False)
        .agg(
            sessions=("session_id", "count"),
            median_minutes=("duration_minutes", "median"),
            refill_events=("refill_events", "sum"),
            refill_qty=("refill_qty", "sum"),
        )
    )
    fig = px.line(
        daily,
        x="event_date",
        y="median_minutes",
        color="position",
        markers=True,
        labels={"event_date": "Date", "median_minutes": "Median Refill Minutes", "position": "Position"},
    )
    fig.update_layout(height=380)
    st.plotly_chart(fig, width="stretch")

with tech_tab:
    tech_summary = (
        kpi.groupby(["position", "staff_name"], as_index=False)
        .agg(
            sessions=("session_id", "count"),
            median_minutes=("duration_minutes", "median"),
            avg_delta_min=("duration_delta_min", "mean"),
            slower_sessions=("kpi_status", lambda s: int((s == "Slower than baseline").sum())),
            refill_events=("refill_events", "sum"),
            refill_qty=("refill_qty", "sum"),
        )
        .sort_values(["position", "avg_delta_min"], ascending=[True, False])
    )
    st.dataframe(
        tech_summary,
        width="stretch",
        hide_index=True,
        column_config={
            "sessions": st.column_config.NumberColumn("Sessions", format="%d"),
            "median_minutes": st.column_config.NumberColumn("Median Min", format="%.1f"),
            "avg_delta_min": st.column_config.NumberColumn("Avg Delta Min", format="%.1f"),
            "slower_sessions": st.column_config.NumberColumn("Slower Sessions", format="%d"),
            "refill_events": st.column_config.NumberColumn("Events", format="%d"),
            "refill_qty": st.column_config.NumberColumn("Qty", format="%.0f"),
        },
    )

with detail_tab:
    detail = kpi.sort_values(["event_date", "position", "start_dt"], ascending=[False, True, True]).copy()
    detail["start_dt"] = pd.to_datetime(detail["start_dt"], errors="coerce")
    detail["end_dt"] = pd.to_datetime(detail["end_dt"], errors="coerce")
    st.dataframe(
        detail[
            [
                "event_date", "position", "staff_name", "shift_type", "device", "start_dt", "end_dt",
                "duration_minutes", "baseline_median_min", "duration_delta_min", "duration_delta_pct",
                "refill_events", "refill_qty", "meds", "kpi_status",
            ]
        ],
        width="stretch",
        hide_index=True,
        column_config={
            "start_dt": st.column_config.DatetimeColumn("Start", format="MM/DD/YY HH:mm"),
            "end_dt": st.column_config.DatetimeColumn("End", format="MM/DD/YY HH:mm"),
            "duration_minutes": st.column_config.NumberColumn("Duration Min", format="%.1f"),
            "baseline_median_min": st.column_config.NumberColumn("Baseline Min", format="%.1f"),
            "duration_delta_min": st.column_config.NumberColumn("Delta Min", format="%.1f"),
            "duration_delta_pct": st.column_config.NumberColumn("Delta %", format="%.0f%%"),
            "refill_events": st.column_config.NumberColumn("Events", format="%d"),
            "refill_qty": st.column_config.NumberColumn("Qty", format="%.0f"),
        },
    )
    st.download_button(
        "Download position KPI session detail CSV",
        data=detail.to_csv(index=False).encode("utf-8"),
        file_name="position_kpi_refill_sessions.csv",
        mime="text/csv",
    )
