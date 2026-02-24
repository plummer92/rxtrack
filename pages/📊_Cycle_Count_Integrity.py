import streamlit as st
import pandas as pd
import numpy as np
from sqlalchemy import text
from App import load_data, engine

st.set_page_config(
    page_title="Cycle Count Integrity",
    page_icon="📊",
    layout="wide"
)

st.header("📊 Cycle Count Day Tracker")
st.caption("Tracking days since last cycle count and identifying risk exposure.")

# ----------------------------------------------------
# 1️⃣ Date Filter
# ----------------------------------------------------

col1, col2 = st.columns(2)
start_date = col1.date_input("Start Date")
end_date = col2.date_input("End Date")

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

# ----------------------------------------------------
# 2️⃣ Load Workflow Data
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
# 3️⃣ Cycle Count Events
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
# 4️⃣ Return Activity
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
# 6️⃣ Pull Master Mapping (SQLAlchemy Safe Method)
# ----------------------------------------------------

with engine.connect() as conn:
    result = conn.execute(
        text("""
            SELECT med_id, med_desc, drug_name, carousel_location
            FROM carousel_master_mapping
        """)
    )
    rows = result.fetchall()
    df_master = pd.DataFrame(rows, columns=result.keys())

# Clean formatting
tracker["med_id"] = tracker["med_id"].astype(str).str.strip().str.upper()
df_master["med_id"] = df_master["med_id"].astype(str).str.strip().str.upper()

if "drug_name" in df_master.columns:
    df_master["drug_name"] = df_master["drug_name"].astype(str).str.strip().str.upper()

if "med_desc" in df_master.columns:
    df_master["med_desc"] = df_master["med_desc"].astype(str).str.strip().str.upper()

# ----------------------------------------------------
# 🔎 DEBUG BLOCK
# ----------------------------------------------------

st.divider()
st.subheader("🔎 DEBUG: Key Alignment Check")

st.write("Workflow med_id sample:")
st.write(tracker["med_id"].unique()[:10])

st.write("Master med_id sample:")
st.write(df_master["med_id"].unique()[:10])

if "drug_name" in df_master.columns:
    st.write("Master drug_name sample:")
    st.write(df_master["drug_name"].unique()[:10])

if "med_desc" in df_master.columns:
    st.write("Master med_desc sample:")
    st.write(df_master["med_desc"].unique()[:10])

workflow_ids = set(tracker["med_id"].unique())
master_ids = set(df_master["med_id"].unique())

st.write("Matches on med_id:", len(workflow_ids.intersection(master_ids)))

if "drug_name" in df_master.columns:
    drug_names = set(df_master["drug_name"].unique())
    st.write("Matches on drug_name:", len(workflow_ids.intersection(drug_names)))

if "med_desc" in df_master.columns:
    desc_names = set(df_master["med_desc"].unique())
    st.write("Matches on med_desc:", len(workflow_ids.intersection(desc_names)))

# ----------------------------------------------------
# 7️⃣ TEMPORARY MERGE (TRY med_id FIRST)
# ----------------------------------------------------

tracker = tracker.merge(
    df_master[["med_id", "carousel_location"]],
    on="med_id",
    how="left"
)

# ----------------------------------------------------
# 8️⃣ Executive Metrics
# ----------------------------------------------------

avg_days = tracker["days_since_cycle"].mean()
max_days = tracker["days_since_cycle"].max()
never_counted = tracker["never_cycle_counted"].sum()

m1, m2, m3 = st.columns(3)

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

st.divider()

# ----------------------------------------------------
# 9️⃣ Detailed Table
# ----------------------------------------------------

st.subheader("🔍 Post-Cycle Return Activity")

display_cols = [
    "med_id",
    "med_desc",
    "carousel_location",
    "return_date",
    "cycle_date",
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
