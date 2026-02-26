import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from App import load_data, engine, render_sidebar

st.set_page_config(page_title="RxBrain", page_icon="🧠", layout="wide")

start_date, end_date = render_sidebar()

st.header("🧠 RxTrack Intelligence Engine")
st.caption("Scanning data for trends and anomalies.")

df_events, _, _, _, _ = load_data(start_date, end_date)

if df_events.empty:
    st.warning("No data found for selected date range.")
    st.stop()

st.success(f"Loaded {len(df_events):,} events.")

# ----------------------------------------------------
# Stockout Risk — last 24h burn rate
# ----------------------------------------------------
st.subheader("🚨 Imminent Stockout Risk (< 12 hrs)")

last_time = df_events["dt"].max()
recent_24h = df_events[df_events["dt"] > (last_time - pd.Timedelta(hours=24))]

if recent_24h.empty:
    st.info("No activity in the last 24 hours to calculate burn rate.")
else:
    # Pull ending_qty separately since load_data doesn't include it
    with engine.connect() as conn:
        current_inv = pd.read_sql("""
            SELECT device, med_desc, ending_qty
            FROM events
            WHERE dt = (
                SELECT MAX(dt) FROM events e2
                WHERE e2.device = events.device AND e2.med_desc = events.med_desc
            )
        """, conn)

    burn = (
        recent_24h.groupby(["device", "med_desc"])
        .agg(pulled=("qty", "sum"))
        .reset_index()
    )

    burn = burn.merge(current_inv, on=["device", "med_desc"], how="left")

    burn["hrs_left"] = burn["ending_qty"] / (
        (burn["pulled"] / 24).replace(0, np.nan)
    )

    critical = burn[burn["hrs_left"] < 12].sort_values("hrs_left")

    if not critical.empty:
        st.error(f"🚩 {len(critical)} imminent stockout risks found.")
        st.dataframe(critical, use_container_width=True, column_config={
            "hrs_left": st.column_config.NumberColumn("Hours Left", format="%.1f"),
            "pulled": st.column_config.NumberColumn("Units Pulled (24h)", format="%.0f"),
            "ending_qty": st.column_config.NumberColumn("Current Inventory", format="%.0f"),
        })
    else:
        st.success("✅ No immediate stockout risks detected.")

# ----------------------------------------------------
# Top Medications by Pull Volume
# ----------------------------------------------------
st.divider()
st.subheader("📈 Top Medications by Pull Volume")

top_meds = (
    df_events.groupby("med_desc")["qty"]
    .sum()
    .reset_index()
    .sort_values("qty", ascending=False)
    .head(15)
)

fig = px.bar(top_meds, x="qty", y="med_desc", orientation="h",
             color="qty", color_continuous_scale="Reds")
fig.update_layout(yaxis={"categoryorder": "total ascending", "title": ""},
                  xaxis_title="Total Units Pulled", showlegend=False)
st.plotly_chart(fig, use_container_width=True)
