import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from App import engine

st.set_page_config(page_title="RxBrain", page_icon="🧠", layout="wide")

st.header("🧠 RxTrack Intelligence Engine")
st.caption("Learning from all accumulated data to surface trends and risks.")

# ----------------------------------------------------
# Load all historical data (this is intentional —
# RxBrain learns from everything, not a date window)
# ----------------------------------------------------

@st.cache_data(ttl=600)
def load_all_data():
    with engine.connect() as conn:
        df_e = pd.read_sql("""
            SELECT user_name, device, med_id, med_desc, event_type,
                   dt, qty, beginning_qty, ending_qty, discrepancy_qty
            FROM events
            ORDER BY dt DESC
        """, conn)

        df_p = pd.read_sql("""
            SELECT user_name, med_id, med_desc, destination, priority, dt, qty
            FROM pharmacy_orders
            ORDER BY dt DESC
        """, conn)

    if not df_e.empty:
        df_e["dt"] = pd.to_datetime(df_e["dt"], errors="coerce")
        df_e["user_name"] = df_e["user_name"].fillna("unknown").astype(str).str.strip()

    if not df_p.empty:
        df_p["dt"] = pd.to_datetime(df_p["dt"], errors="coerce")

    return df_e, df_p

with st.spinner("Loading all historical data..."):
    df_events, df_pharm = load_all_data()

if df_events.empty:
    st.warning("No historical data found.")
    st.stop()

date_min = df_events["dt"].min().date()
date_max = df_events["dt"].max().date()
st.caption(f"📚 Analyzing **{len(df_events):,}** events from **{date_min}** to **{date_max}**")

st.divider()

# Pre-compute time columns used across multiple tabs
df_events["hour"] = df_events["dt"].dt.hour
df_events["day_of_week"] = df_events["dt"].dt.day_name()
df_events["month"] = df_events["dt"].dt.to_period("M").astype(str)

tab1, tab2, tab3, tab4 = st.tabs([
    "🚨 Stockout Risk",
    "📈 Usage Trends",
    "👤 Technician Patterns",
    "⚠️ Anomalies"
])

# ----------------------------------------------------
# TAB 1: Stockout Risk
# Based on recent 24h burn vs current inventory
# ----------------------------------------------------
with tab1:
    st.subheader("Imminent Stockout Risk")
    st.caption("Burn rate from last 24 hours compared to current ending inventory.")

    last_time = df_events["dt"].max()
    recent_24h = df_events[df_events["dt"] > (last_time - pd.Timedelta(hours=24))]

    if recent_24h.empty:
        st.info("No activity in the last 24 hours.")
    else:
        burn = (
            recent_24h.groupby(["device", "med_desc"])
            .agg(pulled=("qty", "sum"))
            .reset_index()
        )

        # Get most recent ending_qty per device/med
        latest_inv = (
            df_events.sort_values("dt")
            .groupby(["device", "med_desc"])["ending_qty"]
            .last()
            .reset_index()
            .rename(columns={"ending_qty": "current_inv"})
        )

        burn = burn.merge(latest_inv, on=["device", "med_desc"], how="left")
        burn["hourly_burn"] = burn["pulled"] / 24
        burn["hrs_left"] = burn["current_inv"] / burn["hourly_burn"].replace(0, np.nan)

        critical = burn[burn["hrs_left"] < 12].sort_values("hrs_left")
        warning = burn[(burn["hrs_left"] >= 12) & (burn["hrs_left"] < 48)].sort_values("hrs_left")

        c1, c2, c3 = st.columns(3)
        c1.metric("Critical (< 12 hrs)", len(critical), delta_color="inverse")
        c2.metric("Warning (12–48 hrs)", len(warning), delta_color="inverse")
        c3.metric("Meds Monitored", len(burn))

        if not critical.empty:
            st.error("🚩 Critical Stockout Risk")
            st.dataframe(critical[["device", "med_desc", "current_inv", "pulled", "hrs_left"]],
                use_container_width=True,
                column_config={
                    "hrs_left": st.column_config.NumberColumn("Hours Left", format="%.1f"),
                    "pulled": st.column_config.NumberColumn("24h Burn", format="%.0f"),
                    "current_inv": st.column_config.NumberColumn("Current Inv", format="%.0f"),
                })

        if not warning.empty:
            st.warning("⚠️ Approaching Low Stock")
            st.dataframe(warning[["device", "med_desc", "current_inv", "pulled", "hrs_left"]],
                use_container_width=True,
                column_config={
                    "hrs_left": st.column_config.NumberColumn("Hours Left", format="%.1f"),
                    "pulled": st.column_config.NumberColumn("24h Burn", format="%.0f"),
                    "current_inv": st.column_config.NumberColumn("Current Inv", format="%.0f"),
                })

        if critical.empty and warning.empty:
            st.success("✅ No stockout risks detected.")

# ----------------------------------------------------
# TAB 2: Usage Trends
# Top meds, busiest devices, usage over time
# ----------------------------------------------------
with tab2:
    st.subheader("Medication Usage Trends")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Top 15 Medications by Total Volume**")
        top_meds = (
            df_events.groupby("med_desc")["qty"]
            .sum().reset_index()
            .sort_values("qty", ascending=False)
            .head(15)
        )
        fig = px.bar(top_meds, x="qty", y="med_desc", orientation="h",
                     color="qty", color_continuous_scale="Blues")
        fig.update_layout(yaxis={"categoryorder": "total ascending", "title": ""},
                          xaxis_title="Total Units", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("**Top 15 Busiest Devices**")
        top_devices = (
            df_events.groupby("device")["qty"]
            .sum().reset_index()
            .sort_values("qty", ascending=False)
            .head(15)
        )
        fig2 = px.bar(top_devices, x="qty", y="device", orientation="h",
                      color="qty", color_continuous_scale="Greens")
        fig2.update_layout(yaxis={"categoryorder": "total ascending", "title": ""},
                           xaxis_title="Total Units", showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)

    st.markdown("**Monthly Pull Volume Over Time**")
    monthly = df_events.groupby("month")["qty"].sum().reset_index()
    fig3 = px.line(monthly, x="month", y="qty", markers=True)
    fig3.update_layout(xaxis_title="Month", yaxis_title="Total Units Pulled")
    st.plotly_chart(fig3, use_container_width=True)

# ----------------------------------------------------
# TAB 3: Technician Patterns
# Who pulls what, when, from where
# ----------------------------------------------------
with tab3:
    st.subheader("Technician Activity Patterns")

    tech_summary = (
        df_events.groupby("user_name")
        .agg(
            total_tx=("qty", "count"),
            total_qty=("qty", "sum"),
            devices_used=("device", "nunique"),
            meds_handled=("med_desc", "nunique"),
        )
        .reset_index()
        .sort_values("total_tx", ascending=False)
    )

    st.dataframe(tech_summary, use_container_width=True, column_config={
        "total_tx": st.column_config.NumberColumn("Total Transactions"),
        "total_qty": st.column_config.NumberColumn("Total Units"),
        "devices_used": st.column_config.NumberColumn("Devices Used"),
        "meds_handled": st.column_config.NumberColumn("Unique Meds"),
    })

    st.markdown("**Hourly Activity Heatmap (All Time)**")
    heatmap = df_events.groupby(["day_of_week", "hour"]).size().reset_index(name="count")
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    fig4 = px.density_heatmap(heatmap, x="hour", y="day_of_week",
                               z="count", category_orders={"day_of_week": day_order},
                               color_continuous_scale="Viridis")
    fig4.update_layout(xaxis_title="Hour of Day", yaxis_title="")
    st.plotly_chart(fig4, use_container_width=True)

# ----------------------------------------------------
# TAB 4: Anomalies
# Discrepancies, count gaps, unusual activity
# ----------------------------------------------------
with tab4:
    st.subheader("Anomaly Detection")

    # Discrepancies
    disc = df_events[df_events["discrepancy_qty"] != 0].copy()
    st.metric("Total Historical Discrepancies", len(disc))

    if not disc.empty:
        disc_by_user = (
            disc.groupby("user_name")["discrepancy_qty"]
            .agg(count="count", total_variance=lambda x: x.abs().sum())
            .reset_index()
            .sort_values("count", ascending=False)
        )
        st.markdown("**Discrepancies by Technician**")
        st.dataframe(disc_by_user, use_container_width=True)

        disc_by_med = (
            disc.groupby("med_desc")["discrepancy_qty"]
            .agg(count="count", total_variance=lambda x: x.abs().sum())
            .reset_index()
            .sort_values("count", ascending=False)
            .head(15)
        )
        st.markdown("**Most Problematic Medications**")
        st.dataframe(disc_by_med, use_container_width=True)

    # Unusual after-hours activity (before 6am or after 10pm)
    st.divider()
    after_hours = df_events[
        (df_events["hour"] < 6) | (df_events["hour"] >= 22)
    ].copy()

    st.metric("After-Hours Transactions (before 6am / after 10pm)", len(after_hours))

    if not after_hours.empty:
        ah_summary = (
            after_hours.groupby("user_name")
            .agg(tx=("qty", "count"), meds=("med_desc", "nunique"))
            .reset_index()
            .sort_values("tx", ascending=False)
        )
        st.dataframe(ah_summary, use_container_width=True)
