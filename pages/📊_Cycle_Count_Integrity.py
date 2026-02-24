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
st.caption("Tracking days since last cycle count and post-cycle return activity.")


# ----------------------------------------------------
# 1️⃣ Independent Date Filter
# ----------------------------------------------------

c1, c2 = st.columns(2)
start_date = c1.date_input("Start Date")
end_date = c2.date_input("End Date")

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()


# ----------------------------------------------------
# 2️⃣ Load FULL Historical Data
# ----------------------------------------------------

df_events, _, df_pharm, _, _ = load_data(
    pd.to_datetime("2000-01-01"),
    pd.to_datetime("2100-01-01")
)

if df_pharm.empty:
    st.warning("No Pharmacy Workflow data found.")
    st.stop()

df_pharm = df_pharm.copy()
df_pharm["dt"] = pd.to_datetime(df_pharm["dt"], errors="coerce")

with st.expander("🔍 Distinct Destination Values"):
    st.write(df_pharm["destination"].dropna().unique()[:50])


# ----------------------------------------------------
# 3️⃣ Identify Cycle Counts (priority)
# ----------------------------------------------------

cycle_counts = df_pharm[
    df_pharm["priority"].astype(str).str.contains(
        "cycle",
        case=False,
        na=False
    )
].copy()

if cycle_counts.empty:
    st.warning("No cycle count transactions found.")
    st.stop()

cycle_counts["cycle_date"] = cycle_counts["dt"].dt.date

latest_cycle = (
    cycle_counts
    .groupby("med_id")["cycle_date"]
    .max()
    .reset_index()
)


# ----------------------------------------------------
# 4️⃣ Identify Returns / Restocks
# ----------------------------------------------------

returns = df_pharm[
    df_pharm["priority"].astype(str).str.contains(
        "return|restock|instant",
        case=False,
        na=False
    )
].copy()

if returns.empty:
    st.warning("No return activity found.")
    st.stop()

returns["return_date"] = returns["dt"].dt.date

returns = returns[
    (returns["return_date"] >= start_date) &
    (returns["return_date"] <= end_date)
].copy()

if returns.empty:
    st.warning("No return activity in selected date range.")
    st.stop()


# ----------------------------------------------------
# 5️⃣ Merge Cycle Baseline
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

tracker["never_cycle_counted"] = tracker["cycle_date"].isna()


# ----------------------------------------------------
# 6️⃣ Executive Metrics
# ----------------------------------------------------

avg_days = tracker["days_since_cycle"].mean()
max_days = tracker["days_since_cycle"].max()
never_counted = tracker["never_cycle_counted"].sum()

m1, m2, m3 = st.columns(3)
m1.metric("Average Days Since Cycle Count",
          f"{avg_days:.1f}" if not np.isnan(avg_days) else "N/A")

m2.metric("Max Days Since Cycle Count",
          int(max_days) if not np.isnan(max_days) else 0)

m3.metric("Meds Never Cycle Counted", int(never_counted))

st.divider()


# ----------------------------------------------------
# 7️⃣ Detailed Table
# ----------------------------------------------------

st.subheader("🔍 Post-Cycle Return Activity")

st.dataframe(
    tracker[[
        "med_id",
        "med_desc",
        "return_date",
        "cycle_date",
        "days_since_cycle",
        "user_name",
        "qty"
    ]]
    .sort_values("days_since_cycle", ascending=False),
    width="stretch"
)
