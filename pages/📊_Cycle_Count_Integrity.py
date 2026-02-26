import streamlit as st
import pandas as pd
import numpy as np
from sqlalchemy import text
from App import load_data, engine, render_sidebar

st.set_page_config(
    page_title="Cycle Count Integrity",
    page_icon="📊",
    layout="wide"
)

start_date, end_date = render_sidebar()

st.header("📊 Cycle Count Integrity Dashboard")
st.caption("Tracking days since last cycle count, user accountability, and carousel location mapping.")

# ----------------------------------------------------
# 🔧 Normalization Function
# ----------------------------------------------------

def normalize(text):
    if pd.isna(text):
        return ""
    return (
        str(text)
        .upper()
        .replace(" ", "")
        .replace("-", "")
        .replace("/", "")
        .replace("(", "")
        .replace(")", "")
        .replace(".", "")
        .strip()
    )

# ----------------------------------------------------
# 1️⃣ Date Filter (from sidebar)
# ----------------------------------------------------




# ----------------------------------------------------
# 2️⃣ Load Pharmacy Workflow Data
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

# ----------------------------------------------------
# 3️⃣ Identify Cycle Count Events
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

# Get latest cycle count per med INCLUDING user
latest_cycle = (
    cycle_counts
    .sort_values("dt")
    .groupby("med_id")
    .last()
    .reset_index()[["med_id", "cycle_date", "user_name"]]
)

latest_cycle = latest_cycle.rename(columns={
    "user_name": "cycle_count_user"
})

# ----------------------------------------------------
# 4️⃣ Identify Return Activity
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
# 5️⃣ Merge Cycle Data
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
# 6️⃣ Load Master Carousel Mapping
# ----------------------------------------------------

with engine.connect() as conn:
    result = conn.execute(
        text("SELECT * FROM carousel_master_mapping")
    )
    rows = result.fetchall()
    df_master = pd.DataFrame(rows, columns=result.keys())

if df_master.empty:
    st.warning("Master carousel mapping table is empty.")
    st.stop()

# ----------------------------------------------------
# 7️⃣ Harmonized Description-Based Join
# ----------------------------------------------------

tracker["join_key"] = tracker["med_desc"].apply(normalize)
df_master["join_key"] = df_master["med_desc"].apply(normalize)

tracker = tracker.merge(
    df_master[["join_key", "carousel_location"]],
    on="join_key",
    how="left"
)

# ----------------------------------------------------
# 8️⃣ Executive Metrics
# ----------------------------------------------------

avg_days = tracker["days_since_cycle"].mean()
max_days = tracker["days_since_cycle"].max()
never_counted = tracker["never_cycle_counted"].sum()
matched = tracker["carousel_location"].notna().sum()
total = len(tracker)

m1, m2, m3, m4 = st.columns(4)

m1.metric(
    "Average Days Since Cycle Count",
    f"{avg_days:.1f}" if not np.isnan(avg_days) else "N/A"
)

m2.metric(
    "Max Days Since Cycle Count",
    int(max_days) if not np.isnan(max_days) else 0
)

m3.metric(
    "Meds Never Cycle Counted",
    int(never_counted)
)

m4.metric(
    "Carousel Match Rate",
    f"{(matched/total*100):.1f}%" if total > 0 else "0%"
)

st.divider()

# ----------------------------------------------------
# 9️⃣ Detailed Post-Cycle Return Table
# ----------------------------------------------------

st.subheader("🔍 Post-Cycle Return Activity")

display_cols = [
    "med_id",
    "med_desc",
    "carousel_location",
    "return_date",
    "cycle_date",
    "cycle_count_user",
    "days_since_cycle",
    "user_name",
    "qty"
]

existing_cols = [col for col in display_cols if col in tracker.columns]

st.dataframe(
    tracker[existing_cols]
    .sort_values("days_since_cycle", ascending=False),
    use_container_width=True
)

# ----------------------------------------------------
# 🔴 Meds Never Cycle Counted Summary
# ----------------------------------------------------

st.divider()
st.subheader("🚨 Medications NEVER Cycle Counted")

never_df = tracker[tracker["never_cycle_counted"] == True].copy()

if never_df.empty:
    st.success("All returned medications have at least one recorded cycle count.")
else:
    never_summary = (
        never_df
        .groupby(["med_id", "med_desc", "carousel_location"], dropna=False)
        .agg(
            total_returns=("qty", "sum"),
            occurrences=("med_id", "count")
        )
        .reset_index()
        .sort_values("total_returns", ascending=False)
    )

    st.dataframe(
        never_summary,
        use_container_width=True
    )
