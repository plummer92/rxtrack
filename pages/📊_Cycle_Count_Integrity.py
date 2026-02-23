import streamlit as st
import pandas as pd
import numpy as np
from App import load_data

st.set_page_config(
    page_title="Cycle Count Integrity",
    page_icon="📊",
    layout="wide"
)

st.header("📊 Cycle Count Day Tracker")
st.caption("Tracking days since last cycle count and time-to-contamination.")

# ----------------------------------------------------
# 1️⃣ Independent Date Filter (Full Historical)
# ----------------------------------------------------

c1, c2 = st.columns(2)
start_date = c1.date_input("Start Date")
end_date = c2.date_input("End Date")

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

# ----------------------------------------------------
# 2️⃣ Load FULL Historical Data (Backdated)
# ----------------------------------------------------

df_events, _, df_pharm, _, df_cycle = load_data(
    pd.to_datetime("2000-01-01"),
    pd.to_datetime("2100-01-01")
)

if df_events.empty and df_pharm.empty:
    st.warning("No historical data found.")
    st.stop()

for df in [df_events, df_pharm, df_cycle]:
    if not df.empty and "dt" in df.columns:
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")

# ----------------------------------------------------
# 3️⃣ Identify Cycle Count Events
# ----------------------------------------------------

cycle_counts = pd.DataFrame()

if not df_cycle.empty:
    cycle_counts = df_cycle.copy()

elif not df_events.empty and "event_type" in df_events.columns:
    cycle_counts = df_events[
        df_events["event_type"].astype(str).str.contains(
            "cycle",
            case=False,
            na=False
        )
    ].copy()

if cycle_counts.empty:
    st.warning("No cycle count events found.")
    st.stop()

cycle_counts["cycle_date"] = cycle_counts["dt"].dt.date

# Get latest cycle count per med_id
latest_cycle = (
    cycle_counts
    .groupby("med_id")["cycle_date"]
    .max()
    .reset_index()
)

# ----------------------------------------------------
# 4️⃣ Identify Return Activity
# ----------------------------------------------------

returns = pd.DataFrame()

if not df_pharm.empty and "event_type" in df_pharm.columns:
    returns = df_pharm[
        df_pharm["event_type"].astype(str).str.contains(
            "return|restock|instant",
            case=False,
            na=False
        )
    ].copy()

returns["return_date"] = returns["dt"].dt.date

# Filter to selected window
returns = returns[
    (returns["return_date"] >= start_date) &
    (returns["return_date"] <= end_date)
]

# ----------------------------------------------------
# 5️⃣ Merge Cycle Count Baseline
# ----------------------------------------------------

tracker = returns.merge(
    latest_cycle,
    on="med_id",
    how="left"
)

tracker["days_since_cycle"] = (
    pd.to_datetime(tracker["return_date"]) -
    pd.to_datetime(tracker["cycle_date"])
).dt.days

# Flag meds with no cycle count history
tracker["never_cycle_counted"] = tracker["cycle_date"].isna()

# ----------------------------------------------------
# 6️⃣ First Post-Cycle Activity
# ----------------------------------------------------

first_after_cycle = (
    tracker.sort_values("return_date")
    .groupby("med_id")
    .first()
    .reset_index()
)

# ----------------------------------------------------
# 7️⃣ Executive Metrics
# ----------------------------------------------------

avg_days = tracker["days_since_cycle"].mean()
max_days = tracker["days_since_cycle"].max()
never_counted = tracker["never_cycle_counted"].sum()

m1, m2, m3 = st.columns(3)
m1.metric("Average Days Since Cycle Count", f"{avg_days:.1f}")
m2.metric("Max Days Since Cycle Count", int(max_days) if not np.isnan(max_days) else 0)
m3.metric("Meds Never Cycle Counted", int(never_counted))

st.divider()

# ----------------------------------------------------
# 8️⃣ Detailed Table
# ----------------------------------------------------

display_cols = [
    "med_id",
    "return_date",
    "cycle_date",
    "days_since_cycle",
    "user_name"
]

st.subheader("🔍 Post-Cycle Return Activity")

st.dataframe(
    tracker[display_cols].sort_values(
        "days_since_cycle",
        ascending=False
    ),
    width="stretch"
)
