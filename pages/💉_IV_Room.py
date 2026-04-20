import pandas as pd
import plotly.express as px
import streamlit as st

import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)


def to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


st.set_page_config(page_title="IV Room", page_icon="💉", layout="wide")

render_sidebar = App.render_sidebar
load_iv_room_data = App.load_iv_room_data

start_date, end_date = render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "IV Room Workload",
        "Track sterile compounding demand, STAT pressure, technician throughput, and preparation turnaround in the same RxTrack shell.",
        kicker="Operations",
    )
    _debug_event("IV Room", "shared_intro_loaded")
    _debug_panel("IV Room", intro_mode="shared")
else:
    st.header("💉 IV Room Workload")
    st.caption("Track sterile compounding demand, technician throughput, and turnaround time.")
    _debug_event("IV Room", "fallback_header_used")
    _debug_panel("IV Room", intro_mode="fallback")

with st.spinner("Loading IV room workload..."):
    df_iv = load_iv_room_data(start_date, end_date)

if df_iv.empty:
    st.info("No IV room workload found for this date range. Upload an `IV Room Workload` file from the sidebar to get started.")
    st.stop()

work = df_iv.copy()
work["order_date"] = pd.to_datetime(work["order_date"], errors="coerce")
work["order_dt"] = pd.to_datetime(work["order_dt"], errors="coerce")
work["completed_on"] = pd.to_datetime(work["completed_on"], errors="coerce")
work["prepare_tat_minutes"] = pd.to_numeric(work["prepare_tat_minutes"], errors="coerce")
work["num_preparations"] = pd.to_numeric(work["num_preparations"], errors="coerce").fillna(0)
work["priority_name"] = work["priority_name"].fillna("").astype(str).str.strip()
work["prepared_by"] = work["prepared_by"].fillna("Unassigned").astype(str).str.strip()
work["approved_by"] = work["approved_by"].fillna("Unassigned").astype(str).str.strip()

facility_options = sorted(work["facility_name"].dropna().unique().tolist())
selected_facilities = st.multiselect("Facility", facility_options, default=facility_options)

filtered = work.copy()
if selected_facilities:
    filtered = filtered[filtered["facility_name"].isin(selected_facilities)]

if filtered.empty:
    st.warning("No IV room records match the current filters.")
    st.stop()

stat_mask = filtered["priority_name"].str.upper().eq("STAT")
tat_ready = filtered.dropna(subset=["prepare_tat_minutes"]).copy()

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("IV Orders", f"{len(filtered):,}")
m2.metric("Preparations", f"{int(filtered['num_preparations'].sum()):,}")
m3.metric("STAT Orders", f"{int(stat_mask.sum()):,}")
m4.metric("Prepared By Count", f"{filtered['prepared_by'].nunique():,}")
m5.metric(
    "Median Prep TAT",
    f"{tat_ready['prepare_tat_minutes'].median():.1f} min" if not tat_ready.empty else "N/A",
)

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("Daily IV Volume")
    daily = (
        filtered.assign(day=filtered["order_date"].dt.date)
        .groupby("day", as_index=False)
        .agg(
            iv_orders=("pk", "count"),
            preparations=("num_preparations", "sum"),
            stat_orders=("priority_name", lambda s: s.astype(str).str.upper().eq("STAT").sum()),
        )
    )
    fig_daily = px.bar(
        daily,
        x="day",
        y="preparations",
        hover_data=["iv_orders", "stat_orders"],
        labels={"day": "", "preparations": "Preparations"},
        color="preparations",
        color_continuous_scale="Blues",
    )
    fig_daily.update_layout(coloraxis_showscale=False, height=360)
    st.plotly_chart(fig_daily, use_container_width=True)

with col2:
    st.subheader("Order Mix by Hour")
    hourly = filtered.dropna(subset=["order_dt"]).copy()
    if hourly.empty:
        st.info("No parsable order timestamps are available for hourly analysis.")
    else:
        hourly["hour"] = hourly["order_dt"].dt.hour
        hourly_mix = hourly.groupby("hour", as_index=False).agg(
            iv_orders=("pk", "count"),
            preparations=("num_preparations", "sum"),
        )
        fig_hour = px.line(
            hourly_mix,
            x="hour",
            y="preparations",
            markers=True,
            labels={"hour": "Hour of Day", "preparations": "Preparations"},
        )
        fig_hour.update_layout(height=360)
        st.plotly_chart(fig_hour, use_container_width=True)

col3, col4 = st.columns(2)

with col3:
    st.subheader("Highest-Volume Compounds")
    top_drugs = (
        filtered.groupby("drug_name", as_index=False)
        .agg(
            iv_orders=("pk", "count"),
            preparations=("num_preparations", "sum"),
        )
        .sort_values(["preparations", "iv_orders"], ascending=False)
        .head(15)
    )
    fig_drugs = px.bar(
        top_drugs.sort_values("preparations"),
        x="preparations",
        y="drug_name",
        orientation="h",
        hover_data=["iv_orders"],
        labels={"preparations": "Preparations", "drug_name": ""},
        color="preparations",
        color_continuous_scale="Tealgrn",
    )
    fig_drugs.update_layout(coloraxis_showscale=False, height=420)
    st.plotly_chart(fig_drugs, use_container_width=True)

with col4:
    st.subheader("Technician Preparation Load")
    tech_load = (
        filtered.groupby("prepared_by", as_index=False)
        .agg(
            iv_orders=("pk", "count"),
            preparations=("num_preparations", "sum"),
            median_tat=("prepare_tat_minutes", "median"),
        )
        .sort_values(["preparations", "iv_orders"], ascending=False)
        .head(15)
    )
    fig_tech = px.bar(
        tech_load.sort_values("preparations"),
        x="preparations",
        y="prepared_by",
        orientation="h",
        hover_data=["iv_orders", "median_tat"],
        labels={"preparations": "Preparations", "prepared_by": ""},
        color="preparations",
        color_continuous_scale="Greens",
    )
    fig_tech.update_layout(coloraxis_showscale=False, height=420)
    st.plotly_chart(fig_tech, use_container_width=True)

st.divider()

stat_col, tat_col = st.columns(2)

with stat_col:
    st.subheader("Priority Mix")
    priority_mix = (
        filtered.assign(priority_bucket=filtered["priority_name"].replace({"": "Routine"}))
        .groupby("priority_bucket", as_index=False)
        .agg(iv_orders=("pk", "count"), preparations=("num_preparations", "sum"))
        .sort_values("preparations", ascending=False)
    )
    fig_priority = px.pie(
        priority_mix,
        names="priority_bucket",
        values="preparations",
        hole=0.45,
    )
    fig_priority.update_layout(height=360)
    st.plotly_chart(fig_priority, use_container_width=True)

with tat_col:
    st.subheader("TAT by Technician")
    if tat_ready.empty:
        st.info("`Prepare TAT Minutes` is only populated on a small share of records in this export, so TAT benchmarking is limited right now.")
    else:
        tat_by_tech = (
            tat_ready.groupby("prepared_by", as_index=False)
            .agg(
                tat_records=("pk", "count"),
                median_tat=("prepare_tat_minutes", "median"),
                p90_tat=("prepare_tat_minutes", lambda s: s.quantile(0.90)),
            )
            .sort_values(["tat_records", "median_tat"], ascending=[False, True])
            .head(15)
        )
        fig_tat = px.scatter(
            tat_by_tech,
            x="tat_records",
            y="median_tat",
            size="p90_tat",
            hover_name="prepared_by",
            labels={"tat_records": "TAT Records", "median_tat": "Median TAT (min)", "p90_tat": "P90 TAT"},
        )
        fig_tat.update_layout(height=360)
        st.plotly_chart(fig_tat, use_container_width=True)

st.subheader("IV Room Summary Table")
summary = (
    filtered.groupby("prepared_by", as_index=False)
    .agg(
        iv_orders=("pk", "count"),
        preparations=("num_preparations", "sum"),
        stat_orders=("priority_name", lambda s: s.astype(str).str.upper().eq("STAT").sum()),
        median_tat=("prepare_tat_minutes", "median"),
    )
    .sort_values(["preparations", "iv_orders"], ascending=False)
)
st.dataframe(
    summary,
    width="stretch",
    hide_index=True,
    column_config={
        "preparations": st.column_config.NumberColumn("Preparations", format="%.0f"),
        "median_tat": st.column_config.NumberColumn("Median TAT (min)", format="%.1f"),
    },
)

with st.expander("Raw IV Room Log"):
    raw_cols = [
        "facility_name",
        "order_lot_number",
        "compound_type",
        "num_preparations",
        "drug_name",
        "order_dt",
        "completed_on",
        "priority_name",
        "prepare_tat_minutes",
        "prepared_by",
        "approved_by",
        "secondary_approved_by",
    ]
    st.dataframe(
        filtered[raw_cols].sort_values("order_dt", ascending=False),
        width="stretch",
        hide_index=True,
    )
    st.download_button(
        "Export IV Room CSV",
        data=to_csv_bytes(filtered[raw_cols]),
        file_name="iv_room_workload.csv",
        mime="text/csv",
    )
