from datetime import time, timedelta
import re

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

import App


st.set_page_config(page_title="Position KPIs", page_icon="📏", layout="wide")
App.apply_global_styles()

start_date, end_date = App.render_sidebar()
App.require_management_access("Position KPIs")

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Position KPIs",
        "Baseline Pyxis and pharmacy workflow duration by scheduled position window, then show how each tech/day deviates from the normal range.",
        kicker="Performance",
    )
else:
    st.header("Position KPIs")
    st.caption("Baseline Pyxis and pharmacy workflow duration by position window and compare tech/day performance.")


DEFAULT_POSITIONS = [
    {"position": "0500 Tech Refills", "start": "05:00", "end": "13:30", "staff_keywords": "0500", "device_keywords": ""},
    {"position": "0600 Tech Refills", "start": "06:00", "end": "14:30", "staff_keywords": "0600", "device_keywords": ""},
    {"position": "PHP Refills", "start": "14:30", "end": "23:00", "staff_keywords": "PHP", "device_keywords": ""},
    {"position": "OR Refills", "start": "14:30", "end": "23:00", "staff_keywords": "OR", "device_keywords": ""},
]


def normalize_keywords(value):
    return [part.strip().upper() for part in str(value or "").split(",") if part.strip()]


def normalize_match_text(value):
    text = str(value or "").upper()
    time_compact = re.sub(r"\b([0-2]?\d):([0-5]\d)\b", lambda m: f"{int(m.group(1)):02d}{m.group(2)}", text)
    return f"{text} {time_compact}"


def coerce_time(value):
    if isinstance(value, time):
        return value
    parsed = pd.to_datetime(str(value or "").strip(), errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.time()


def build_work_sessions(events, pharmacy_orders):
    px_cols = ["pk", "dt", "user_name", "device", "event_type", "med_id", "med_desc", "qty"]
    px_df = events[[c for c in px_cols if c in events.columns]].copy() if not events.empty else pd.DataFrame()
    if not px_df.empty:
        px_df["source"] = "Pyxis"

    ph_cols = ["pk", "dt", "user_name", "destination", "priority", "med_id", "med_desc", "qty"]
    ph_df = pharmacy_orders[[c for c in ph_cols if c in pharmacy_orders.columns]].copy() if not pharmacy_orders.empty else pd.DataFrame()
    if not ph_df.empty:
        ph_df = ph_df.rename(columns={"destination": "device", "priority": "event_type"})
        ph_df["source"] = "Pharmacy"

    df = pd.concat([px_df, ph_df], ignore_index=True)
    if df.empty:
        return pd.DataFrame()

    for col in ["pk", "user_name", "device", "event_type", "med_id", "med_desc", "source"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()
    if "qty" not in df.columns:
        df["qty"] = 0
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"]).copy()
    if df.empty:
        return pd.DataFrame()

    df["tech_key"] = df["user_name"].apply(App.normalize_name)
    df["event_date"] = df["dt"].dt.date
    df["device_norm"] = df["device"].str.upper()
    df = df.sort_values(["tech_key", "event_date", "source", "device_norm", "dt"]).reset_index(drop=True)
    df["prev_tech"] = df["tech_key"].shift()
    df["prev_date"] = df["event_date"].shift()
    df["prev_source"] = df["source"].shift()
    df["prev_device"] = df["device_norm"].shift()
    df["prev_dt"] = df["dt"].shift()
    df["gap_sec"] = (df["dt"] - df["prev_dt"]).dt.total_seconds().fillna(0)
    df["new_session"] = (
        (df["tech_key"] != df["prev_tech"])
        | (df["event_date"] != df["prev_date"])
        | (df["source"] != df["prev_source"])
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
            source=("source", "first"),
            device=("device", "first"),
            device_norm=("device_norm", "first"),
            primary_event=("event_type", "first"),
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


@st.cache_data(ttl=300)
def load_position_kpi_data(start_date, end_date):
    params = {"start_date": start_date, "end_date": end_date}
    legacy_event_sql = text("""
        SELECT
            user_name::text AS user_name,
            device::text AS device,
            med_id::text AS med_id,
            med_desc::text AS med_desc,
            event_type::text AS event_type,
            dt::timestamp AS dt,
            qty::float8 AS qty,
            pk::text AS pk
        FROM events
        WHERE dt::date BETWEEN :start_date AND :end_date
    """)
    audit_event_sql = text("""
        SELECT
            user_name::text AS user_name,
            station_name::text AS device,
            med_id::text AS med_id,
            med_desc::text AS med_desc,
            transaction_type::text AS event_type,
            dt::timestamp AS dt,
            qty::float8 AS qty,
            pk::text AS pk
        FROM audit_transaction_detail_rc
        WHERE dt::date BETWEEN :start_date AND :end_date
    """)
    pharmacy_sql = text("""
        SELECT pk, queue_id, priority, dt, med_id, med_desc, destination, user_name, qty
        FROM pharmacy_orders
        WHERE dt::date BETWEEN :start_date AND :end_date
    """)
    schedule_sql = text("""
        SELECT pk, dt, day_name, staff_name, shift_type, assignment_type, note,
               COALESCE(schedule_status, assignment_type, 'Standard') AS schedule_status,
               cell_fill_color
        FROM staff_schedule
        WHERE dt::date BETWEEN :start_date AND :end_date
    """)
    frames = []
    pharmacy = pd.DataFrame()
    schedule = pd.DataFrame()
    with App.engine.connect() as conn:
        try:
            frames.append(pd.read_sql(legacy_event_sql, conn, params=params))
        except Exception as exc:
            st.warning(f"Could not load legacy Pyxis event rows for {start_date} to {end_date}: {exc}")
        try:
            frames.append(pd.read_sql(audit_event_sql, conn, params=params))
        except Exception as exc:
            st.warning(f"Could not load audit Pyxis event rows for {start_date} to {end_date}: {exc}")
        try:
            pharmacy = pd.read_sql(pharmacy_sql, conn, params=params)
        except Exception as exc:
            st.warning(f"Could not load pharmacy rows for {start_date} to {end_date}: {exc}")
        try:
            schedule = pd.read_sql(schedule_sql, conn, params=params)
        except Exception as exc:
            st.warning(f"Could not load schedule rows for {start_date} to {end_date}: {exc}")

    events = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    for df in [events, pharmacy, schedule]:
        if not df.empty and "dt" in df.columns:
            df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    if not pharmacy.empty and "destination" in pharmacy.columns:
        pharmacy = pharmacy[~pharmacy["destination"].astype(str).str.contains("BATCH PICK", case=False, na=False)].copy()
    return events, pharmacy, schedule


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
        .apply(normalize_match_text)
    )
    return sessions


def contains_any_keyword(series, keywords):
    if not keywords:
        return pd.Series(True, index=series.index)
    series = series.fillna("").astype(str).apply(normalize_match_text)
    pattern_parts = [
        rf"(?<![A-Z0-9]){re.escape(keyword)}(?![A-Z0-9])"
        for keyword in keywords
    ]
    pattern = "|".join(pattern_parts)
    return series.str.contains(pattern, case=False, regex=True, na=False)


def summarize_position_match(pos_name, start_time, end_time, staff_keywords, device_keywords, sessions):
    session_time = sessions["start_dt"].dt.time
    if start_time <= end_time:
        time_match = sessions[(session_time >= start_time) & (session_time <= end_time)].copy()
    else:
        time_match = sessions[(session_time >= start_time) | (session_time <= end_time)].copy()

    staff_match = time_match
    if staff_keywords:
        staff_match = staff_match[contains_any_keyword(staff_match["schedule_text"], staff_keywords)].copy()

    device_match = staff_match
    if device_keywords:
        device_match = device_match[contains_any_keyword(device_match["device_norm"], device_keywords)].copy()

    return {
        "position": pos_name,
        "time_window_sessions": len(time_match),
        "after_staff_keyword": len(staff_match),
        "after_device_keyword": len(device_match),
        "matched_staff": staff_match["staff_name"].nunique() if not staff_match.empty else 0,
    }


with st.expander("Position window setup", expanded=False):
    st.caption(
        "Keywords are comma-separated. Staff keywords match schedule text first; device keywords optionally narrow cabinet/destination names."
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
        key="position_kpi_windows_v2",
    )

min_sessions = st.slider("Minimum sessions for baseline", 3, 30, 5, key="position_kpi_min_sessions")
baseline_lookback_days = st.slider(
    "Baseline lookback days",
    7,
    180,
    60,
    key="position_kpi_baseline_lookback_days",
)
effective_start_date = min(start_date, end_date - timedelta(days=baseline_lookback_days - 1))
if effective_start_date < start_date:
    st.caption(
        f"Position KPI baseline is using {effective_start_date:%m/%d/%y} through {end_date:%m/%d/%y} "
        f"so the baseline has enough refill sessions."
    )

with st.spinner("Loading refill sessions..."):
    df_events, df_pharm, df_sched = load_position_kpi_data(effective_start_date, end_date)
    sessions = build_work_sessions(df_events, df_pharm)

if sessions.empty and effective_start_date < start_date:
    with st.spinner("Retrying the selected sidebar window..."):
        retry_events, retry_pharm, retry_sched = load_position_kpi_data(start_date, end_date)
        retry_sessions = build_work_sessions(retry_events, retry_pharm)
    df_events, df_pharm, df_sched = retry_events, retry_pharm, retry_sched
    if not retry_sessions.empty:
        st.warning(
            "The wider KPI baseline did not return workflow rows, so this view is using the selected sidebar "
            "date range instead. Widen the sidebar range if you need a larger baseline."
        )
        effective_start_date = start_date
        sessions = retry_sessions

if sessions.empty:
    row_cols = st.columns(3)
    row_cols[0].metric("Loaded Pyxis Rows", f"{len(df_events):,}")
    row_cols[1].metric("Loaded Pharmacy Rows", f"{len(df_pharm):,}")
    row_cols[2].metric("Loaded Schedule Rows", f"{len(df_sched):,}")
    st.warning("No Pyxis or pharmacy workflow sessions were found in the KPI baseline window.")
    if not df_events.empty and "event_type" in df_events.columns:
        event_counts = (
            df_events["event_type"]
            .fillna("")
            .astype(str)
            .str.strip()
            .replace("", "(blank)")
            .value_counts()
            .head(20)
            .rename_axis("event_type")
            .reset_index(name="rows")
        )
        with st.expander("Loaded event types"):
            st.dataframe(event_counts, width="stretch", hide_index=True)
    if not df_pharm.empty and "priority" in df_pharm.columns:
        pharm_counts = (
            df_pharm["priority"]
            .fillna("")
            .astype(str)
            .str.strip()
            .replace("", "(blank)")
            .value_counts()
            .head(20)
            .rename_axis("priority")
            .reset_index(name="rows")
        )
        with st.expander("Loaded pharmacy priorities"):
            st.dataframe(pharm_counts, width="stretch", hide_index=True)
    st.stop()

sessions = attach_schedule_context(sessions, df_sched)
sessions["has_schedule_match"] = sessions["shift_type"].fillna("").astype(str).str.strip().ne("")

diag_cols = st.columns(4)
diag_cols[0].metric("Loaded Pyxis Rows", f"{len(df_events):,}")
diag_cols[1].metric("Loaded Pharmacy Rows", f"{len(df_pharm):,}")
diag_cols[2].metric("Workflow Sessions", f"{len(sessions):,}")
diag_cols[3].metric("Sessions Linked To Schedule", f"{int(sessions['has_schedule_match'].sum()):,}")

position_frames = []
position_diagnostics = []
for pos in edited_positions.to_dict("records"):
    pos_name = str(pos.get("position") or "").strip()
    start_time = coerce_time(pos.get("start"))
    end_time = coerce_time(pos.get("end"))
    staff_keywords = normalize_keywords(pos.get("staff_keywords"))
    keywords = normalize_keywords(pos.get("device_keywords"))
    if not pos_name or start_time is None or end_time is None:
        continue
    position_diagnostics.append(
        summarize_position_match(pos_name, start_time, end_time, staff_keywords, keywords, sessions)
    )
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
    if position_diagnostics:
        with st.expander("Position matching diagnostics", expanded=True):
            st.dataframe(pd.DataFrame(position_diagnostics), width="stretch", hide_index=True)
    if not df_sched.empty:
        schedule_preview = df_sched.copy()
        schedule_preview["schedule_text"] = (
            schedule_preview[["staff_name", "shift_type", "assignment_type"]]
            .fillna("")
            .astype(str)
            .agg(" ".join, axis=1)
            .apply(normalize_match_text)
        )
        with st.expander("Schedule rows loaded"):
            st.dataframe(
                schedule_preview[["dt", "staff_name", "shift_type", "assignment_type", "schedule_text"]].head(100),
                width="stretch",
                hide_index=True,
            )
    if not sessions.empty:
        unmatched = sessions[~sessions["has_schedule_match"]].copy()
        with st.expander("Workflow sessions not linked to schedule"):
            st.dataframe(
                unmatched[["event_date", "source", "user_name", "tech_key", "device", "start_dt", "primary_event"]].head(100),
                width="stretch",
                hide_index=True,
            )
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
m1.metric("Workflow Sessions", f"{len(kpi):,}")
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
                "source", "primary_event", "duration_minutes", "baseline_median_min", "duration_delta_min", "duration_delta_pct",
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
        file_name="position_kpi_workflow_sessions.csv",
        mime="text/csv",
    )
