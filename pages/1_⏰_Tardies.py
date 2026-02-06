import streamlit as st
import pandas as pd
import numpy as np
from datetime import timedelta
# Importing shared logic from your Hub
from App import load_data, normalize_name, parse_shift_start, ADMIN_USERS 

st.header("⏰ Tardiness Tracker")

# Use a slider so you can adjust the definition of "late" on the fly
grace_period = st.slider("Define Grace Period (Minutes)", 0, 30, 5, help="Technicians late by FEWER than these minutes will be hidden.")

if 'start_date' not in st.session_state:
    st.info("👈 Please select a date range on the **Home** page first.")
else:
    # 1. Load data using anchored dates
    df_events, df_config, df_pharm, df_sched, df_att = load_data(
        st.session_state.start_date, 
        st.session_state.end_date
    )
    
    if not df_sched.empty and not df_att.empty:
        # 2. Prep data for matching
        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)
        df_att['match_key'] = df_att['raw_name'].apply(normalize_name)
        df_sched['date_obj'] = pd.to_datetime(df_sched['dt']).dt.date
        df_att['date_obj'] = pd.to_datetime(df_att['dt_date']).dt.date
        
        # 3. Filter out Krista, Joe, and Emily
        df_sched = df_sched[~df_sched['match_key'].isin(ADMIN_USERS)]
        
        merged = pd.merge(df_sched, df_att, on=['match_key', 'date_obj'], how='inner')
        
        if not merged.empty:
            # 4. Calculate Scheduled vs Actual Clock-In
            merged['actual_clock_in'] = pd.to_datetime(merged['start_dt'], errors='coerce')
            merged['scheduled_start'] = merged.apply(
                lambda x: parse_shift_start(x['date_obj'], x['shift_type']), axis=1
            )
            
            # Clean up Nones and calculate delay
            merged = merged.dropna(subset=['actual_clock_in', 'scheduled_start'])
            merged['delay_min'] = (merged['actual_clock_in'] - merged['scheduled_start']).dt.total_seconds() / 60
            
            # 5. Apply the Grace Period Filter
            # This hides the "noise" you mentioned
            tardies = merged[merged['delay_min'] > grace_period].sort_values('delay_min', ascending=False)
            
            # 6. Formatting for leadership review
            tardies['Clock In'] = tardies['actual_clock_in'].dt.strftime('%H:%M')
            tardies['Scheduled'] = tardies['scheduled_start'].dt.strftime('%H:%M')
            tardies['Late By'] = tardies['delay_min'].apply(lambda x: f"{int(x)} min")
            
            st.metric(f"Technicians > {grace_period}m Late", len(tardies))
            
            st.dataframe(
                tardies[['date_obj', 'staff_name', 'shift_type', 'Scheduled', 'Clock In', 'Late By']], 
                use_container_width=True,
                hide_index=True,
                column_config={
                    "date_obj": "Date",
                    "staff_name": "Technician",
                    "shift_type": "Shift"
                }
            )
        else:
            st.success("🎉 All staff arrived within the grace period.")
