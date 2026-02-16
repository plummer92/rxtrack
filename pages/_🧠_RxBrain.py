import streamlit as st
import pandas as pd
from App import load_data, seconds_to_mmss

st.set_page_config(page_title="RxTrack Brain", page_icon="🧠", layout="wide")
st.header("🧠 RxTrack Intelligence Engine")
st.caption("Scanning workflows for anomalies, drift, and efficiency gaps.")

if 'start_date' not in st.session_state:
    st.info("👈 Please select a date range on the Home page.")
else:
    df_events, _, df_pharm, _, _ = load_data(st.session_state.start_date, st.session_state.end_date)

    if not df_events.empty and not df_pharm.empty:
        st.subheader("🚩 Today's Priority Flags")
        
        # --- BRAIN LOGIC 1: THE LONG WALK (LATENCY) ---
        # Identifying replenishment delays over 2 hours
        orders = df_pharm[df_pharm['priority'].str.contains('Stockout|Critical', case=False, na=False)].copy()
        refills = df_events[df_events['event_type'].str.contains('REFILL|LOAD', case=False, na=False)].copy()
        
        latency = pd.merge(orders, refills, on=['destination', 'med_id'], suffixes=('_order', '_refill'))
        latency['gap'] = (latency['dt_refill'] - latency['dt_order']).dt.total_seconds() / 60
        long_walks = latency[latency['gap'] > 120] # 2-hour threshold

        # --- BRAIN LOGIC 2: INVENTORY DRIFT ---
        # Logic to find if a pull didn't decrease inventory as expected
        # (Requires beginning_qty and ending_qty columns to be active)
        
        # --- BRAIN LOGIC 3: REPETITIVE PULLS ---
        # Finding same med pulled for same unit > 3 times in 4 hours
        df_pharm['hour_block'] = df_pharm['dt'].dt.floor('4H')
        repeats = df_pharm.groupby(['hour_block', 'destination', 'med_id']).size().reset_index(name='count')
        inefficient_batches = repeats[repeats['count'] > 3]

        # --- DISPLAY ALERTS ---
        col1, col2 = st.columns(2)
        
        with col1:
            st.error(f"⚠️ {len(long_walks)} Delivery Latency Alerts")
            if not long_walks.empty:
                st.dataframe(long_walks[['med_desc_order', 'destination', 'gap']], width='stretch')
        
        with col2:
            st.warning(f"📉 {len(inefficient_batches)} Batching Inefficiencies")
            if not inefficient_batches.empty:
                st.dataframe(inefficient_batches[['destination', 'count']], width='stretch')

    else:
        st.info("Waiting for data stream to analyze...")
