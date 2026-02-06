import streamlit as st
import pandas as pd
import numpy as np
# Import shared logic and the admin list from your Hub
from App import load_data, normalize_name, parse_shift_start, ADMIN_USERS 

st.header("⏰ Tardiness Tracker")

if 'start_date' not in st.session_state:
    st.info("👈 Please select a date range on the **Home** page first.")
else:
    # 1. Load data using anchored dates
    df_events, df_config, df_pharm, df_sched, df_att = load_data(
        st.session_state.start_date, 
        st.session_state.end_date
    )
    
    if not df_sched.empty and not df_att.empty:
        # 2. Match names and dates
        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)
        df_att['match_key'] = df_att['raw_name'].apply(normalize_name)
        df_sched['date_obj'] = pd.to_datetime(df_sched['dt']).dt.date
        df_att['date_obj'] = pd.to_datetime(df_att['dt_date']).dt.date
        
        # 3. Filter out Krista, Joe, and Emily
        df_sched = df_sched[~df_sched['match_key'].isin(ADMIN_USERS)]
        
        merged = pd.merge(df_sched, df_att, on=['match_key', 'date_obj'], how='inner')
        
        if not merged.empty:
            # 4. Calculate Times and Delays
            merged['actual_clock_in'] = pd.to_datetime(merged['start_dt'], errors='coerce')
            merged['scheduled_start'] = merged.apply(
                lambda x: parse_shift_start(x['date_obj'], x['shift_type']), axis=1
            )
            
            # Remove errors and calculate delay in minutes
            merged = merged.dropna(subset=['actual_clock_in', 'scheduled_start'])
            merged['delay_min'] = (merged['actual_clock_in'] - merged['scheduled_start']).dt.total_seconds() / 60
            
            # 5. Filter for true tardies (e.g., > 5 minutes late)
            tardies = merged[merged['delay_min'] > 5].sort_values('delay_min', ascending=False)
            
            # 6. Formatting for the UI
            tardies['Clock In'] = tardies['actual_clock_in'].dt.strftime('%H:%M')
            tardies['Scheduled'] = tardies['scheduled_start'].dt.strftime('%H:%M')
            tardies['Late By'] = tardies['delay_min'].apply(lambda x: f"{int(x)} min")
            
            st.metric("Total Tardies Found", len(tardies))
            
            # Displaying the clean table
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
            st.success("🎉 No tardies found for this period.")
