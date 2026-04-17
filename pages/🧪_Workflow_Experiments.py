import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import App


NAME_MAPPINGS = {
    "phi": "ali",
    "ho": "ali",
    "rebekah": "bekah",
    "nugent": "kathy",
    "kathleen": "kathy",
    "spain": "dee",
    "deloris": "dee",
    "jabusch": "dan",
    "daniel": "dan",
    "nicholas": "nick",
}

AMBIGUOUS_NAMES = {
    "melissa",
    "emily",
    "sarah",
    "megan",
    "erin",
    "kyle",
    "jessica",
    "andy",
    "heather",
    "michelle",
    "taylor",
}

SHIFT_BUCKETS = [
    "06:00-08:59",
    "09:00-11:59",
    "12:00-14:59",
    "15:00-17:59",
    "18:00+",
    "Unknown",
]


def normalize_name_local(full_name):
    if not full_name or pd.isna(full_name):
        return "unknown"

    s = str(full_name).strip().lower()
    if not s or s == ",":
        return "unknown"

    first_name = ""
    last_initial = ""

    if "," in s:
        parts = s.split(",")
        if len(parts) >= 2:
            last_name_part = parts[0].strip()
            first_name_parts = parts[1].strip().split(" ")
            if first_name_parts and first_name_parts[0]:
                first_name = first_name_parts[0]
            if last_name_part:
                last_initial = last_name_part[0]
    else:
        parts = s.split(" ")
        if parts:
            first_name = parts[0]
            if len(parts) > 1 and parts[1]:
                last_initial = parts[1][0]

    for key, val in NAME_MAPPINGS.items():
        if key in first_name:
            first_name = val
            break

    if first_name in AMBIGUOUS_NAMES and last_initial:
        return f"{first_name} {last_initial}"
    return first_name or "unknown"


def parse_shift_start_local(date_obj, shift_str):
    if not shift_str or pd.isna(shift_str):
        return pd.NaT

    s = str(shift_str).lower().strip()

    m_time = pd.Series([s]).str.extract(r"(\d{1,2}):(\d{2})")
    if not m_time.empty and pd.notna(m_time.iloc[0, 0]):
        hour = int(m_time.iloc[0, 0])
        minute = int(m_time.iloc[0, 1])
        if "p" in s and hour < 12:
            hour += 12
        if "a" in s and hour == 12:
            hour = 0
        return pd.to_datetime(f"{date_obj} {hour:02d}:{minute:02d}", errors="coerce")

    m_ampm = pd.Series([s]).str.extract(r"(\d{1,2})\s*([ap])")
    if not m_ampm.empty and pd.notna(m_ampm.iloc[0, 0]):
        hour = int(m_ampm.iloc[0, 0])
        ampm = m_ampm.iloc[0, 1]
        if ampm == "p" and hour < 12:
            hour += 12
        if ampm == "a" and hour == 12:
            hour = 0
        return pd.to_datetime(f"{date_obj} {hour:02d}:00", errors="coerce")

    m_mil = pd.Series([s]).str.extract(r"(\d{4})")
    if not m_mil.empty and pd.notna(m_mil.iloc[0, 0]):
        val = int(m_mil.iloc[0, 0])
        if 0 <= val <= 2400:
            hour, minute = divmod(val, 100)
            return pd.to_datetime(f"{date_obj} {hour:02d}:{minute:02d}", errors="coerce")

    return pd.NaT


def to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


def sample_note(df):
    if df.empty:
        return "No records in this slice."
    if len(df) < 30:
        return "Small sample: use directional judgment only."
    if len(df) < 100:
        return "Moderate sample: useful for hypothesis building."
    return "Large sample: good candidate for workflow pilot design."


def add_baseline_comparison(summary_df, metric_col):
    if summary_df.empty:
        return summary_df

    baseline_row = summary_df.sort_values(metric_col, ascending=False).iloc[0]
    baseline_value = baseline_row[metric_col]
    summary_df = summary_df.copy()
    summary_df["vs_best_group_pct"] = np.where(
        baseline_value == 0,
        np.nan,
        ((summary_df[metric_col] - baseline_value) / baseline_value) * 100,
    )
    return summary_df


def build_session_frame(df_events, df_sched):
    if df_events.empty:
        return pd.DataFrame()

    work = df_events.copy()
    work["dt"] = pd.to_datetime(work["dt"], errors="coerce")
    work = work.dropna(subset=["dt", "session_id"]).copy()
    work["qty"] = pd.to_numeric(work["qty"], errors="coerce").fillna(0)
    work["machine_time_sec"] = pd.to_numeric(work["machine_time_sec"], errors="coerce").fillna(0)
    work["user_name"] = work["user_name"].fillna("unknown").astype(str).str.strip()
    work["device"] = work["device"].fillna("unknown").astype(str).str.strip()
    work["date_obj"] = work["dt"].dt.date
    work["match_key"] = work["user_name"].apply(normalize_name_local)

    sessions = work.groupby("session_id").agg(
        user_name=("user_name", "first"),
        match_key=("match_key", "first"),
        device=("device", "first"),
        session_date=("date_obj", "first"),
        start_dt=("dt", "min"),
        end_dt=("dt", "max"),
        tx_count=("pk", "count"),
        qty_total=("qty", "sum"),
        machine_active_sec=("machine_time_sec", "sum"),
    ).reset_index()

    sessions["elapsed_sec"] = (sessions["end_dt"] - sessions["start_dt"]).dt.total_seconds().fillna(0)
    sessions["session_seconds"] = sessions[["machine_active_sec", "elapsed_sec"]].max(axis=1)
    sessions["session_seconds"] = sessions["session_seconds"].clip(lower=30)
    sessions["hour_of_day"] = sessions["start_dt"].dt.hour
    sessions["tx_per_hour"] = np.where(
        sessions["session_seconds"] > 0,
        sessions["tx_count"] / (sessions["session_seconds"] / 3600),
        np.nan,
    )

    if not df_sched.empty:
        sched = df_sched.copy()
        sched["dt"] = pd.to_datetime(sched["dt"], errors="coerce")
        sched = sched.dropna(subset=["dt"]).copy()
        sched["date_obj"] = sched["dt"].dt.date
        sched["match_key"] = sched["staff_name"].apply(normalize_name_local)
        admin_users = load_admin_users()
        sched = sched[~sched["match_key"].isin(admin_users)]
        sched = (
            sched.sort_values(["date_obj", "staff_name"])
            .drop_duplicates(subset=["match_key", "date_obj"], keep="first")
            [["match_key", "date_obj", "staff_name", "shift_type", "assignment_type"]]
        )
        sessions = sessions.merge(
            sched,
            left_on=["match_key", "session_date"],
            right_on=["match_key", "date_obj"],
            how="left",
        )
        sessions.drop(columns=["date_obj"], inplace=True, errors="ignore")

    sessions["shift_type"] = sessions["shift_type"].fillna("Unknown")
    sessions["assignment_type"] = sessions["assignment_type"].fillna("Unknown")
    sessions["staff_name"] = sessions["staff_name"].fillna(sessions["user_name"])

    return sessions


def build_tardy_frame(df_sched, df_att):
    if df_sched.empty or df_att.empty:
        return pd.DataFrame()

    sched = df_sched.copy()
    att = df_att.copy()

    sched["date_obj"] = pd.to_datetime(sched["dt"], errors="coerce").dt.date
    att["date_obj"] = pd.to_datetime(att["dt_date"], errors="coerce").dt.date
    sched["match_key"] = sched["staff_name"].apply(normalize_name_local)
    att["match_key"] = att["raw_name"].apply(normalize_name_local)

    admin_users = load_admin_users()
    sched = sched[~sched["match_key"].isin(admin_users)].copy()

    merged = pd.merge(sched, att, on=["match_key", "date_obj"], how="inner")
    if merged.empty:
        return pd.DataFrame()

    merged["actual_clock_in"] = pd.to_datetime(merged["start_dt"], errors="coerce")
    merged["scheduled_start"] = merged.apply(
        lambda row: parse_shift_start_local(row["date_obj"], row["shift_type"]),
        axis=1,
    )
    merged = merged.dropna(subset=["actual_clock_in", "scheduled_start"]).copy()
    merged["delay_min"] = (
        merged["actual_clock_in"] - merged["scheduled_start"]
    ).dt.total_seconds() / 60
    merged["tardy_flag"] = (merged["delay_min"] > 5).astype(int)
    merged["scheduled_hour"] = merged["scheduled_start"].dt.hour

    merged["shift_start_bucket"] = pd.cut(
        merged["scheduled_hour"].fillna(-1),
        bins=[-2, 5, 8, 11, 14, 17, 24],
        labels=SHIFT_BUCKETS,
    ).astype(str)
    merged.loc[merged["scheduled_hour"].isna(), "shift_start_bucket"] = "Unknown"

    return merged


def build_pharmacy_frame(df_pharm):
    if df_pharm.empty:
        return pd.DataFrame()

    pharm = df_pharm.copy()
    pharm["dt"] = pd.to_datetime(pharm["dt"], errors="coerce")
    pharm = pharm.dropna(subset=["dt"]).copy()
    pharm["hour"] = pharm["dt"].dt.hour
    pharm["weekday"] = pharm["dt"].dt.day_name()
    pharm["is_weekend"] = np.where(pharm["dt"].dt.weekday >= 5, "Weekend", "Weekday")
    pharm["priority_group"] = np.select(
        [
            pharm["priority"].astype(str).str.contains("STAT|CRITICAL", case=False, na=False),
            pharm["priority"].astype(str).str.contains("STOCK ?OUT", case=False, na=False),
        ],
        ["STAT/Critical", "Stockout"],
        default="Routine/Other",
    )
    return pharm


def build_device_frame(df_events):
    if df_events.empty:
        return pd.DataFrame()

    events = df_events.copy()
    events["dt"] = pd.to_datetime(events["dt"], errors="coerce")
    events = events.dropna(subset=["dt"]).copy()
    events["date_obj"] = events["dt"].dt.date
    events["hour"] = events["dt"].dt.hour
    events["qty"] = pd.to_numeric(events["qty"], errors="coerce").fillna(0)
    events["has_discrepancy"] = events["discrepancy_qty"].fillna(0).ne(0).astype(int)
    return events


def summarize_group(df, group_col, metrics):
    if df.empty or group_col not in df.columns:
        return pd.DataFrame()

    grouped = df.groupby(group_col).agg(**metrics).reset_index()
    grouped = grouped.rename(columns={group_col: "Group"})
    return grouped.sort_values("records", ascending=False)


st.set_page_config(page_title="Workflow Experiments", page_icon="🧪", layout="wide")

load_admin_users = App.load_admin_users
load_data = App.load_data
render_sidebar = App.render_sidebar
seconds_to_mmss = App.seconds_to_mmss

start_date, end_date = render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Workflow Experiments",
        "Read-only hypothesis testing across session efficiency, tardiness, pharmacy rhythm, and device load.",
        kicker="Core",
    )
    App.record_ui_debug_event("Workflow Experiments", "shared_intro_loaded")
    App.render_ui_debugger("Workflow Experiments", intro_mode="shared")
else:
    st.header("🧪 Workflow Experiments")
    st.caption("Read-only hypothesis testing across session efficiency, tardiness, pharmacy rhythm, and device load.")
    App.record_ui_debug_event("Workflow Experiments", "fallback_header_used")
    App.render_ui_debugger("Workflow Experiments", intro_mode="fallback")

with st.spinner("Loading experiment data..."):
    df_events, _, df_pharm, df_sched, df_att = load_data(start_date, end_date)

if df_events.empty and df_pharm.empty and df_sched.empty and df_att.empty:
    st.warning("No data available in the selected window.")
    st.stop()

sessions = build_session_frame(df_events, df_sched)
tardy = build_tardy_frame(df_sched, df_att)
pharm = build_pharmacy_frame(df_pharm)
device_events = build_device_frame(df_events)

st.info(
    "These views are descriptive and read-only. They help us spot strong candidates for pilot workflow changes before we touch operations."
)

tab1, tab2, tab3, tab4 = st.tabs(
    ["Session Efficiency", "Tardiness", "Pharmacy Rhythm", "Device Load"]
)

with tab1:
    st.subheader("Session Efficiency Tests")
    st.caption("Compare session duration and throughput by shift, device, assignment, or hour.")

    if sessions.empty:
        st.warning("No session data found for the selected period.")
    else:
        compare_col = st.selectbox(
            "Compare sessions by",
            ["shift_type", "device", "assignment_type", "hour_of_day", "staff_name"],
            index=0,
            key="sess_compare",
        )
        min_sessions = st.slider("Minimum sessions per group", 1, 50, 5, key="sess_min")

        session_summary = summarize_group(
            sessions,
            compare_col,
            {
                "records": ("session_id", "count"),
                "avg_session_sec": ("session_seconds", "mean"),
                "median_session_sec": ("session_seconds", "median"),
                "avg_tx_per_hour": ("tx_per_hour", "mean"),
                "avg_tx_count": ("tx_count", "mean"),
            },
        )
        session_summary = session_summary[session_summary["records"] >= min_sessions].copy()
        session_summary = add_baseline_comparison(session_summary, "avg_tx_per_hour")

        c1, c2, c3 = st.columns(3)
        c1.metric("Sessions Analyzed", f"{len(sessions):,}")
        c2.metric(
            "Average Session",
            seconds_to_mmss(sessions["session_seconds"].mean() if not sessions.empty else 0),
        )
        c3.metric(
            "Average Throughput",
            f"{sessions['tx_per_hour'].mean():.1f} tx/hr" if not sessions.empty else "0.0 tx/hr",
        )

        if session_summary.empty:
            st.warning("No groups met the current minimum-session threshold.")
        else:
            best_group = session_summary.sort_values("avg_tx_per_hour", ascending=False).iloc[0]
            worst_group = session_summary.sort_values("avg_tx_per_hour", ascending=True).iloc[0]

            st.markdown(
                f"Best observed group: **{best_group['Group']}** at **{best_group['avg_tx_per_hour']:.1f} tx/hr**. "
                f"Weakest observed group: **{worst_group['Group']}** at **{worst_group['avg_tx_per_hour']:.1f} tx/hr**."
            )
            st.caption(sample_note(session_summary))

            fig = px.bar(
                session_summary.sort_values("avg_tx_per_hour", ascending=False),
                x="avg_tx_per_hour",
                y="Group",
                color="avg_session_sec",
                orientation="h",
                labels={
                    "avg_tx_per_hour": "Average Throughput (tx/hr)",
                    "avg_session_sec": "Avg Session Seconds",
                },
                title="Throughput by Comparison Group",
            )
            st.plotly_chart(fig, use_container_width=True)

            display = session_summary.copy()
            display["avg_session"] = display["avg_session_sec"].apply(seconds_to_mmss)
            display["median_session"] = display["median_session_sec"].apply(seconds_to_mmss)
            st.dataframe(
                display[
                    [
                        "Group",
                        "records",
                        "avg_session",
                        "median_session",
                        "avg_tx_per_hour",
                        "avg_tx_count",
                        "vs_best_group_pct",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "records": st.column_config.NumberColumn("Sessions", format="%d"),
                    "avg_tx_per_hour": st.column_config.NumberColumn("Avg Throughput", format="%.2f"),
                    "avg_tx_count": st.column_config.NumberColumn("Avg Tx / Session", format="%.2f"),
                    "vs_best_group_pct": st.column_config.NumberColumn("Vs Best Group %", format="%.1f"),
                },
            )
            st.download_button(
                "Export Session Comparison CSV",
                data=to_csv_bytes(display),
                file_name="workflow_experiment_sessions.csv",
                mime="text/csv",
            )

with tab2:
    st.subheader("Tardiness Tests")
    st.caption("Compare delay rates by shift type, shift start bucket, or assignment.")

    if tardy.empty:
        st.warning("No matched schedule and attendance data found for the selected period.")
    else:
        compare_col = st.selectbox(
            "Compare tardiness by",
            ["shift_type", "shift_start_bucket", "assignment_type", "staff_name"],
            index=1,
            key="tardy_compare",
        )
        tardy_summary = summarize_group(
            tardy,
            compare_col,
            {
                "records": ("match_key", "count"),
                "tardy_rate": ("tardy_flag", "mean"),
                "avg_delay_min": ("delay_min", "mean"),
                "median_delay_min": ("delay_min", "median"),
            },
        )
        tardy_summary["tardy_rate_pct"] = tardy_summary["tardy_rate"] * 100
        tardy_summary = tardy_summary.sort_values(["tardy_rate_pct", "records"], ascending=[False, False])

        c1, c2, c3 = st.columns(3)
        c1.metric("Attendance Matches", f"{len(tardy):,}")
        c2.metric("Overall Tardy Rate", f"{tardy['tardy_flag'].mean() * 100:.1f}%")
        c3.metric("Average Delay", f"{tardy['delay_min'].mean():.1f} min")

        st.caption(sample_note(tardy))

        fig = px.bar(
            tardy_summary,
            x="tardy_rate_pct",
            y="Group",
            color="avg_delay_min",
            orientation="h",
            labels={
                "tardy_rate_pct": "Tardy Rate (%)",
                "avg_delay_min": "Average Delay (min)",
            },
            title="Tardy Rate by Group",
        )
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(
            tardy_summary[
                ["Group", "records", "tardy_rate_pct", "avg_delay_min", "median_delay_min"]
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "records": st.column_config.NumberColumn("Matched Shifts", format="%d"),
                "tardy_rate_pct": st.column_config.NumberColumn("Tardy Rate %", format="%.1f"),
                "avg_delay_min": st.column_config.NumberColumn("Avg Delay (min)", format="%.1f"),
                "median_delay_min": st.column_config.NumberColumn("Median Delay (min)", format="%.1f"),
            },
        )
        st.download_button(
            "Export Tardiness Comparison CSV",
            data=to_csv_bytes(tardy_summary),
            file_name="workflow_experiment_tardiness.csv",
            mime="text/csv",
        )

with tab3:
    st.subheader("Pharmacy Rhythm Tests")
    st.caption("Look for timing patterns that may support queueing or staffing adjustments.")

    if pharm.empty:
        st.warning("No pharmacy workflow records found for the selected period.")
    else:
        compare_col = st.selectbox(
            "Compare pharmacy workload by",
            ["hour", "weekday", "is_weekend", "priority_group", "destination"],
            index=0,
            key="pharm_compare",
        )
        min_records = st.slider("Minimum orders per group", 1, 100, 10, key="pharm_min")

        pharm_summary = summarize_group(
            pharm,
            compare_col,
            {
                "records": ("pk", "count"),
                "avg_qty": ("qty", "mean"),
                "stat_share": ("priority_group", lambda s: (s == "STAT/Critical").mean()),
            },
        )
        pharm_summary = pharm_summary[pharm_summary["records"] >= min_records].copy()
        pharm_summary["stat_share_pct"] = pharm_summary["stat_share"] * 100

        c1, c2, c3 = st.columns(3)
        c1.metric("Orders Analyzed", f"{len(pharm):,}")
        c2.metric("STAT/Critical Share", f"{(pharm['priority_group'] == 'STAT/Critical').mean() * 100:.1f}%")
        c3.metric("Average Qty / Order", f"{pharm['qty'].mean():.2f}")

        if pharm_summary.empty:
            st.warning("No groups met the current minimum-order threshold.")
        else:
            st.caption(sample_note(pharm_summary))

            fig = px.line(
                pharm_summary.sort_values("Group"),
                x="Group",
                y="records",
                markers=True,
                title="Order Volume by Group",
            )
            st.plotly_chart(fig, use_container_width=True)

            fig2 = px.bar(
                pharm_summary.sort_values("stat_share_pct", ascending=False),
                x="stat_share_pct",
                y="Group",
                orientation="h",
                color="records",
                labels={"stat_share_pct": "STAT/Critical Share (%)"},
                title="Urgency Mix by Group",
            )
            st.plotly_chart(fig2, use_container_width=True)

            st.dataframe(
                pharm_summary[
                    ["Group", "records", "avg_qty", "stat_share_pct"]
                ],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "records": st.column_config.NumberColumn("Orders", format="%d"),
                    "avg_qty": st.column_config.NumberColumn("Avg Qty", format="%.2f"),
                    "stat_share_pct": st.column_config.NumberColumn("STAT/Critical %", format="%.1f"),
                },
            )
            st.download_button(
                "Export Pharmacy Rhythm CSV",
                data=to_csv_bytes(pharm_summary),
                file_name="workflow_experiment_pharmacy.csv",
                mime="text/csv",
            )

with tab4:
    st.subheader("Device Load Tests")
    st.caption("Compare event volume, discrepancy rate, and unique-user spread by device.")

    if device_events.empty:
        st.warning("No device event data found for the selected period.")
    else:
        compare_col = st.selectbox(
            "Compare device load by",
            ["device", "hour", "date_obj", "user_name"],
            index=0,
            key="device_compare",
        )
        min_records = st.slider("Minimum events per group", 1, 250, 25, key="device_min")

        device_summary = summarize_group(
            device_events,
            compare_col,
            {
                "records": ("pk", "count"),
                "unique_users": ("user_name", "nunique"),
                "avg_qty": ("qty", "mean"),
                "discrepancy_rate": ("has_discrepancy", "mean"),
            },
        )
        device_summary = device_summary[device_summary["records"] >= min_records].copy()
        device_summary["discrepancy_rate_pct"] = device_summary["discrepancy_rate"] * 100

        c1, c2, c3 = st.columns(3)
        c1.metric("Events Analyzed", f"{len(device_events):,}")
        c2.metric("Unique Devices", f"{device_events['device'].nunique():,}")
        c3.metric(
            "Overall Discrepancy Rate",
            f"{device_events['has_discrepancy'].mean() * 100:.2f}%",
        )

        if device_summary.empty:
            st.warning("No groups met the current minimum-event threshold.")
        else:
            st.caption(sample_note(device_summary))

            fig = px.bar(
                device_summary.sort_values("records", ascending=False).head(20),
                x="records",
                y="Group",
                color="discrepancy_rate_pct",
                orientation="h",
                labels={
                    "records": "Event Count",
                    "discrepancy_rate_pct": "Discrepancy Rate (%)",
                },
                title="Highest-Volume Groups",
            )
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(
                device_summary[
                    ["Group", "records", "unique_users", "avg_qty", "discrepancy_rate_pct"]
                ],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "records": st.column_config.NumberColumn("Events", format="%d"),
                    "unique_users": st.column_config.NumberColumn("Unique Users", format="%d"),
                    "avg_qty": st.column_config.NumberColumn("Avg Qty", format="%.2f"),
                    "discrepancy_rate_pct": st.column_config.NumberColumn("Discrepancy Rate %", format="%.2f"),
                },
            )
            st.download_button(
                "Export Device Load CSV",
                data=to_csv_bytes(device_summary),
                file_name="workflow_experiment_device_load.csv",
                mime="text/csv",
            )

st.divider()

with st.expander("Experiment Design Notes", expanded=False):
    st.markdown(
        """
        - This page is descriptive only and does not apply statistical significance testing yet.
        - Session comparisons use existing `session_id` values from the shared loader so behavior stays aligned with the rest of the app.
        - Tardiness uses the same practical 5-minute threshold already used elsewhere in the project.
        - Exports are raw comparison tables so we can review ideas together before making workflow changes.
        """
    )
