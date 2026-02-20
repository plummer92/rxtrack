import streamlit as st
import pandas as pd
import numpy as np
from App import load_data

st.set_page_config(page_title="Return Reconciliation", page_icon="🔄", layout="wide")

st.header("🔄 Closed-Loop Return Integrity")
st.caption("Validating Pyxis unload workflow against Pharmacy return/restock activity.")

# ----------------------------------------------------
# 1️⃣ Independent Date Filter
# ----------------------------------------------------

c1, c2 = st.columns(2)
start_date = c1.date_input("Start Date")
end_date = c2.date_input("End Date")

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

df_events, _, df_pharm, _, _ = load_data(start_date, end_date)

if df_events.empty and df_pharm.empty:
    st.warning("No data found for selected dates.")
    st.stop()

# Ensure datetime
if not df_events.empty:
    df_events['dt'] = pd.to_datetime(df_events['dt'], errors='coerce')

if not df_pharm.empty:
    df_pharm['dt'] = pd.to_datetime(df_pharm['dt'], errors='coerce')

# ----------------------------------------------------
# 2️⃣ EXCLUDE Patient Cassette
# ----------------------------------------------------

if not df_events.empty and 'device' in df_events.columns:
    df_events = df_events[
        ~df_events['device'].str.contains("cassette", case=False, na=False)
    ]

if not df_pharm.empty and 'destination' in df_pharm.columns:
    df_pharm = df_pharm[
        ~df_pharm['destination'].str.contains("cassette", case=False, na=False)
    ]

# ----------------------------------------------------
# 3️⃣ Identify Workflow Events
# ----------------------------------------------------

# Pyxis unload detection
pyxis_unload = pd.DataFrame()

if not df_events.empty:
    pyxis_unload = df_events[
        df_events['event_type'].astype(str).str.contains(
            "empty|unload|return bin", case=False, na=False
        )
    ].copy()

# Pharmacy return/restock detection
pharm_return = pd.DataFrame()

if not df_pharm.empty:

    pharm_df = df_pharm.copy()

    # Detect correct event column
    if 'event_type' in pharm_df.columns:
        event_col = 'event_type'
    elif 'priority' in pharm_df.columns:
        event_col = 'priority'
    else:
        event_col = None

    if event_col:
        pharm_return = pharm_df[
            pharm_df[event_col].astype(str).str.contains(
                "return|restock|instant", case=False, na=False
            )
        ].copy()

# ----------------------------------------------------
# 4️⃣ Normalize Dates
# ----------------------------------------------------

if not pyxis_unload.empty:
    pyxis_unload['date'] = pyxis_unload['dt'].dt.date

if not pharm_return.empty:
    pharm_return['date'] = pharm_return['dt'].dt.date

# ----------------------------------------------------
# 5️⃣ Aggregate Quantities
# ----------------------------------------------------

pyxis_sum = pd.DataFrame()
pharm_sum = pd.DataFrame()

if not pyxis_unload.empty:
    pyxis_sum = (
        pyxis_unload
        .groupby(['med_id', 'med_desc', 'date'])['qty']
        .sum()
        .reset_index()
        .rename(columns={'qty': 'qty_pyxis'})
    )

if not pharm_return.empty:
    pharm_sum = (
        pharm_return
        .groupby(['med_id', 'med_desc', 'date'])['qty']
        .sum()
        .reset_index()
        .rename(columns={'qty': 'qty_pharm'})
    )

# ----------------------------------------------------
# 6️⃣ Reconciliation Engine
# ----------------------------------------------------

if pyxis_sum.empty and pharm_sum.empty:
    st.warning("No unload/return workflow events found.")
    st.stop()

recon = pd.merge(
    pyxis_sum,
    pharm_sum,
    on=['med_id', 'med_desc', 'date'],
    how='outer'
)

recon[['qty_pyxis', 'qty_pharm']] = recon[['qty_pyxis', 'qty_pharm']].fillna(0)
recon['difference'] = recon['qty_pyxis'] - recon['qty_pharm']

# ----------------------------------------------------
# 7️⃣ Executive Metrics
# ----------------------------------------------------

total_unload = recon['qty_pyxis'].sum()
total_return = recon['qty_pharm'].sum()

reconciliation_pct = (
    (min(total_unload, total_return) / total_unload) * 100
    if total_unload > 0 else 100
)

unmatched = recon[recon['difference'] != 0]

m1, m2, m3, m4 = st.columns(4)

m1.metric("Total Pyxis Unload Qty", int(total_unload))
m2.metric("Total Pharmacy Return Qty", int(total_return))
m3.metric("Reconciliation %", f"{reconciliation_pct:.2f}%")
m4.metric("Unmatched Med-Days", len(unmatched))

st.divider()

# ----------------------------------------------------
# 8️⃣ Variance Table
# ----------------------------------------------------

st.subheader("🚨 Unmatched Workflow Events")

if unmatched.empty:
    st.success("✅ 100% Reconciliation Achieved.")
else:
    st.dataframe(
        unmatched.sort_values('difference', key=abs, ascending=False),
        use_container_width=True
    )

# ----------------------------------------------------
# 9️⃣ Medication Variance Ranking
# ----------------------------------------------------

st.subheader("💊 Medication Variance Concentration")

if not unmatched.empty:
    med_variance = (
        unmatched
        .groupby('med_desc')['difference']
        .sum()
        .reset_index()
        .sort_values('difference', key=abs, ascending=False)
    )
    st.dataframe(med_variance.head(10), use_container_width=True)
else:
    st.info("No medication-level variance detected.")

# ----------------------------------------------------
# 🔍 Debug
# ----------------------------------------------------

with st.expander("🛠 Debug Info", expanded=False):
    st.write("Pyxis unload rows:", len(pyxis_unload))
    st.write("Pharmacy return rows:", len(pharm_return))
