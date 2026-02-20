import streamlit as st
import pandas as pd
import numpy as np
from App import load_data, seconds_to_mmss

st.set_page_config(page_title="Return Reconciliation", page_icon="🔄", layout="wide")

st.header("🔄 Closed-Loop Return Integrity")
st.caption("Validating Pyxis unload events against Pharmacy return/restock activity.")

# ----------------------------------------------------
# 1️⃣ Load Data
# ----------------------------------------------------

if 'start_date' not in st.session_state:
    st.info("👈 Select a date range on Overview first.")
    st.stop()

df_events, _, df_pharm, _, _ = load_data(
    st.session_state.start_date,
    st.session_state.end_date
)

if df_events.empty and df_pharm.empty:
    st.warning("No data found for selected dates.")
    st.stop()

# Ensure datetime
if not df_events.empty:
    df_events['dt'] = pd.to_datetime(df_events['dt'], errors='coerce')

if not df_pharm.empty:
    df_pharm['dt'] = pd.to_datetime(df_pharm['dt'], errors='coerce')

# ----------------------------------------------------
# 2️⃣ Identify Workflow Events
# ----------------------------------------------------

pyxis_unload = pd.DataFrame()
pharm_return = pd.DataFrame()

if not df_events.empty:
    pyxis_unload = df_events[
        df_events['event_type'].str.contains(
            "empty|unload|return bin", case=False, na=False
        )
    ].copy()

if not df_pharm.empty:

    # Normalize event column
    pharm_df = df_pharm.copy()

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
    else:
        pharm_return = pd.DataFrame()

# ----------------------------------------------------
# 3️⃣ Normalize Date Window (Daily)
# ----------------------------------------------------

if not pyxis_unload.empty:
    pyxis_unload['date'] = pyxis_unload['dt'].dt.date

if not pharm_return.empty:
    pharm_return['date'] = pharm_return['dt'].dt.date

# ----------------------------------------------------
# 4️⃣ Aggregate Quantities
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
# 5️⃣ Merge + Reconcile
# ----------------------------------------------------

if not pyxis_sum.empty or not pharm_sum.empty:

    recon = pd.merge(
        pyxis_sum,
        pharm_sum,
        on=['med_id', 'med_desc', 'date'],
        how='outer'
    )

    recon[['qty_pyxis', 'qty_pharm']] = recon[['qty_pyxis', 'qty_pharm']].fillna(0)

    recon['difference'] = recon['qty_pyxis'] - recon['qty_pharm']

else:
    recon = pd.DataFrame()

# ----------------------------------------------------
# 6️⃣ Director Metrics
# ----------------------------------------------------

if recon.empty:
    st.warning("No unload/return workflow activity detected.")
    st.stop()

total_unload = recon['qty_pyxis'].sum()
total_return = recon['qty_pharm'].sum()

reconciliation_pct = (
    (min(total_unload, total_return) / total_unload) * 100
    if total_unload > 0 else 100
)

unmatched = recon[recon['difference'] != 0]

c1, c2, c3, c4 = st.columns(4)

c1.metric("Total Pyxis Unload Qty", int(total_unload))
c2.metric("Total Pharmacy Return Qty", int(total_return))
c3.metric("Reconciliation %", f"{reconciliation_pct:.2f}%")
c4.metric("Unmatched Med Days", len(unmatched))

st.divider()

# ----------------------------------------------------
# 7️⃣ Mismatch Table
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
# 8️⃣ Medication Risk Concentration
# ----------------------------------------------------

st.subheader("💊 Medication Variance Concentration")

med_variance = (
    unmatched
    .groupby('med_desc')['difference']
    .sum()
    .reset_index()
    .sort_values('difference', key=abs, ascending=False)
)

if not med_variance.empty:
    st.dataframe(med_variance.head(10), use_container_width=True)
else:
    st.info("No medication-level variance detected.")

# ----------------------------------------------------
# 9️⃣ Time-Based Pattern
# ----------------------------------------------------

st.subheader("📆 Daily Workflow Integrity")

daily = (
    recon
    .groupby('date')[['qty_pyxis', 'qty_pharm']]
    .sum()
    .reset_index()
)

daily['difference'] = daily['qty_pyxis'] - daily['qty_pharm']

st.dataframe(daily.sort_values('date'), use_container_width=True)

# ----------------------------------------------------
# 🔍 Developer Debug (Optional)
# ----------------------------------------------------

with st.expander("🛠 Debug: Raw Event Counts", expanded=False):
    st.write("Pyxis Unload Events:", len(pyxis_unload))
    st.write("Pharmacy Return Events:", len(pharm_return))
