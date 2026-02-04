import streamlit as st
import pandas as pd
import numpy as np
# This line pulls the "Shared Brain" from your main App.py
from App import load_data, normalize_name, parse_shift_start, ADMIN_USERS 

st.header("⏰ Tardiness Tracker")

# 1. Access the shared dates from the Hub (App.py)
if 'start_date' in st.session_state:
    # 2. Load the data using the shared loader
    df_events, df_config, df_pharm, df_sched, df_att = load_data(
        st.session_state.start_date, 
        st.session_state.end_date
    )
    
    if not df_sched.empty and not df_att.empty:
        # 3. Apply the fixed Tardy logic
        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)
        df_att['match_key'] = df_att['raw_name'].apply(normalize_name)
        df_sched['date_obj'] = pd.to_datetime(df_sched['dt']).dt.date
        df_att['date_obj'] = pd.to_datetime(df_att['dt_date']).dt.date
        
        merged = pd.merge(df_sched, df_att, on=['match_key', 'date_obj'], how='inner')
        
        if not merged.empty:
            merged['actual_start_dt'] = pd.to_datetime(merged['start_dt'], errors='coerce')
            merged['scheduled_start_dt'] = merged.apply(lambda x: parse_shift_start(x['date_obj'], x['shift_type']), axis=1)
            merged['scheduled_start_dt'] = pd.to_datetime(merged['scheduled_start_dt'], errors='coerce')
            merged.dropna(subset=['scheduled_start_dt', 'actual_start_dt'], inplace=True)
            
            merged['delay_min'] = (merged['actual_start_dt'] - merged['scheduled_start_dt']).dt.total_seconds() / 60
            tardies = merged[merged['delay_min'] > 5].sort_values('delay_min', ascending=False)
            
            st.metric("Total Tardies Matched", len(tardies))
            st.dataframe(tardies[['date_obj', 'staff_name', 'shift_type', 'delay_min']], use_container_width=True)
