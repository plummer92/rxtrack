import streamlit as st
import pandas as pd
import numpy as np
from App import load_data, engine # Assuming engine is defined in App.py

st.set_page_config(page_title="RxTrack Brain", page_icon="🧠", layout="wide")

# --- DATA INGESTION (BYPASSING FILTERS) ---
@st.cache_data(ttl=600)
def load_brain_memory():
    # We pull the last 90 days of data regardless of the sidebar
    query_events = "SELECT * FROM events"
    query_pharm = "SELECT * FROM pharmacy_orders"
    
    # Using SQLAlchemy engine for clean ingestion
    df_e = pd.read_sql(query_events, engine)
    df_p = pd.read_sql(query_pharm, engine)
    
    # Standardize columns to prevent KeyError
    if 'ending_qty' not in df_e.columns:
        df_e['ending_qty'] = np.nan
    if 'beginning_qty' not in df_e.columns:
        df_e['beginning_qty'] = np.nan
        
    return df_e, df_p

df_events_all, df_pharm_all = load_brain_memory()

st.header("🧠 RxTrack Intelligence Engine")
st.caption("Scanning the full repository (Global Memory) for anomalies and trends.")

# --- THE PREDICTIVE BRAIN (SCANNING ALL TIME) ---
if not df_events_all.empty:
    st.subheader("🔮 Predictive Burn-Rate Analysis")
    
    # 1. Calculate Burn Rate based on the most recent 24h in the WHOLE database
    last_timestamp = df_events_all['dt'].max()
    brain_24h = df_events_all[df_events_all['dt'] > (last_timestamp - pd.Timedelta(hours=24))].copy()
    
    # Fix the KeyError by ensuring we drop NaNs for the calculation
    predictive_df = brain_24h.dropna(subset=['ending_qty'])
    
    if not predictive_df.empty:
        burn_rate = predictive_df.groupby(['device', 'med_id', 'med_desc']).agg(
            total_pulled=('qty', 'sum'),
            current_inv=('ending_qty', 'last')
        ).reset_index()

        burn_rate['hourly_rate'] = burn_rate['total_pulled'] / 24
        burn_rate['est_hours_left'] = burn_rate['current_inv'] / burn_rate['hourly_rate']

        # Only show items empty in < 12 hours
        alerts = burn_rate[burn_rate['est_hours_left'] < 12].sort_values('est_hours_left')
        
        if not alerts.empty:
            st.error(f"⚠️ The Brain has identified {len(alerts)} imminent stockout risks.")
            st.dataframe(alerts, width='stretch')
        else:
            st.success("Brain Scan Complete: No imminent stockouts detected in the next 12 hours.")

# --- BRAIN LOGIC 2: THE "9 PULL" DRIFT ---
st.divider()
st.subheader("🕵️ Inventory Drift Auditor")
st.caption("Scanning for transactions where the bin count didn't drop by the pulled amount.")

# Use the full history to find count errors
drift_df = df_events_all.dropna(subset=['beginning_qty', 'ending_qty']).copy()
drift_df['expected_ending'] = drift_df['beginning_qty'] - drift_df['qty']
discrepancies = drift_df[drift_df['ending_qty'] != drift_df['expected_ending']]

if not discrepancies.empty:
    st.warning(f"The Brain found {len(discrepancies)} count anomalies in historical data.")
    st.dataframe(discrepancies[['dt', 'user_name', 'device', 'med_desc', 'qty', 'beginning_qty', 'ending_qty']], width='stretch')
