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
# 2️⃣ Load FULL Historical Data (Backdated)
# ----------------------------------------------------

df_events, _, df_pharm, _, _ = load_data(
    pd.to_datetime("2000-01-01"),
    pd.to_datetime("2100-01-01")
)

if df_pharm.empty:
    st.warning("No Pharmacy Workflow data found.")
    st.stop()

# 🔍 DEBUG: Inspect Pharmacy Workflow Schema
with st.expander("🔍 Pharmacy Workflow Columns (DEBUG)"):
    st.write(df_pharm.columns)
    st.write(df_pharm.head())


# ----------------------------------------------------
# 3️⃣ Detect Datetime Column Dynamically
# ----------------------------------------------------

def detect_datetime_column(df):
    possible_cols = [
        "dt",
        "transaction_datetime",
        "transaction_date",
        "event_datetime",
        "timestamp",
        "date"
    ]
    for col in possible_cols:
        if col in df.columns:
            return col
    return None

datetime_col = detect_datetime_column(df_pharm)

if datetime_col is None:
    st.error("No datetime column found in Pharmacy Workflow data.")
    st.stop()

df_pharm = df_pharm.copy()
df_pharm[datetime_col] = pd.to_datetime(
    df_pharm[datetime_col],
    errors="coerce"
)


# ----------------------------------------------------
# 4️⃣ Identify Cycle Counts (priority OR transaction_type)
# ----------------------------------------------------

cycle_counts = pd.DataFrame()

if "transaction_type" in df_pharm.columns:
    cycle_counts = df_pharm[
        df_pharm["transaction_type"].astype(str).str.contains(
            "cycle",
            case=False,
            na=False
        )
    ].copy()

elif "priority" in df_pharm.columns:
    cycle_counts = df_pharm[
        df_pharm["priority"].astype(str).str.contains(
            "cycle",
            case=False,
            na=False
        )
    ].copy()

else:
    st.error("No transaction_type or priority column found.")
    st.stop()

if cycle_counts.empty:
    st.warning("No cycle count transactions found.")
    st.stop()


# ----------------------------------------------------
# 5️⃣ Identify Return Activity
# ----------------------------------------------------

returns = df_pharm[
    df_pharm["transaction_type"].astype(str).str.contains(
        "return|restock|instant",
        case=False,
        na=False
    )
].copy()

if returns.empty:
    st.warning("No return activity found.")
    st.stop()

returns["return_date"] = returns[datetime_col].dt.date

# Apply page date filter ONLY here
returns = returns[
    (returns["return_date"] >= start_date) &
    (returns["return_date"] <= end_date)
].copy()

if returns.empty:
    st.warning("No return activity in selected date range.")
    st.stop()


# ----------------------------------------------------
# 6️⃣ Merge Cycle Count Baseline
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
# 7️⃣ Executive Metrics
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
# 8️⃣ Detailed Table
# ----------------------------------------------------

st.subheader("🔍 Post-Cycle Return Activity")

display_cols = [
    "med_id",
    "return_date",
    "cycle_date",
    "days_since_cycle",
    "user_name"
]

available_cols = [col for col in display_cols if col in tracker.columns]

st.dataframe(
    tracker[available_cols]
        .sort_values("days_since_cycle", ascending=False),
    width="stretch"
)
