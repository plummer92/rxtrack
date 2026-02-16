import streamlit as st
import pandas as pd
import numpy as np
from App import load_data, engine # Now this import will work!

st.set_page_config(page_title="RxTrack Brain", page_icon="🧠", layout="wide")

# --- ALWAYS-ON BRAIN SCAN ---
@st.cache_data(ttl=600)
def load_all_time_data():
    # 1. Pull the raw data using the engine defined in App.py
    df_e = pd.read_sql("SELECT * FROM events", engine)
    df_p = pd.read_sql("SELECT * FROM pharmacy_orders", engine)
    
    # 2. FORCE NUMERIC TYPES (This fixes the 'str' vs 'float' error)
    # We apply this to qty and inventory levels to ensure math works
    for df in [df_e, df_p]:
        if 'qty' in df.columns:
            df['qty'] = pd.to_numeric(df['qty'], errors='coerce').fillna(0)
            
    if not df_e.empty:
        # Standardizing naming and types for Pyxis events
        if 'ending_qty' in df_e.columns:
            df_e['ending_qty'] = pd.to_numeric(df_e['ending_qty'], errors='coerce').fillna(0)
        else:
            df_e['ending_qty'] = np.nan
            
        if 'beginning_qty' in df_e.columns:
            df_e['beginning_qty'] = pd.to_numeric(df_e['beginning_qty'], errors='coerce').fillna(0)
        else:
            df_e['beginning_qty'] = np.nan

    # 3. Ensure Timestamps are actual Datetime objects
    df_e['dt'] = pd.to_datetime(df_e['dt'], errors='coerce')
    df_p['dt'] = pd.to_datetime(df_p['dt'], errors='coerce')
        
    return df_e, df_p

st.header("🧠 RxTrack Intelligence Engine")
st.caption("Scanning global historical data for trends and anomalies.")

try:
    df_events_all, df_pharm_all = load_all_time_data()
    
    # 1. Burn Rate Prediction (Scan the last 24h of the whole DB)
    if not df_events_all.empty:
        last_time = df_events_all['dt'].max()
        recent_24h = df_events_all[df_events_all['dt'] > (last_time - pd.Timedelta(hours=24))].copy()
        
        # Aggregate logic
        burn = recent_24h.dropna(subset=['ending_qty']).groupby(['device', 'med_desc']).agg(
            pulled=('qty', 'sum'),
            current_inv=('ending_qty', 'last')
        ).reset_index()
        
        burn['hrs_left'] = burn['current_inv'] / (burn['pulled'] / 24)
        critical = burn[burn['hrs_left'] < 12].sort_values('hrs_left')
        
        if not critical.empty:
            st.error(f"🚩 High Alert: {len(critical)} imminent stockout risks found in global scan.")
            st.dataframe(critical, width='stretch')

    # 2. Inventory Drift Auditor
    st.divider()
    st.subheader("🕵️ Global Drift Auditor")
    drift_df = df_events_all.dropna(subset=['beginning_qty', 'ending_qty']).copy()
    drift_df['expected'] = drift_df['beginning_qty'] - drift_df['qty']
    mismatch = drift_df[drift_df['ending_qty'] != drift_df['expected']]
    
    if not mismatch.empty:
        st.warning(f"The Brain identified {len(mismatch)} historical count discrepancies.")
        st.dataframe(mismatch[['dt', 'device', 'med_desc', 'qty', 'beginning_qty', 'ending_qty']], width='stretch')

except Exception as e:
    st.error(f"Brain Scan failed: {e}")
    st.info("Ensure the database engine is correctly configured in App.py.")
