import streamlit as st
import pandas as pd
import numpy as np
from App import load_data, seconds_to_mmss # Import shared logic

st.set_page_config(page_title="Session Explorer", page_icon="🔍", layout="wide")
st.header("🔍 Unified Session Explorer")
st.caption("Analyzing continuous work blocks across Pyxis and Pharmacy Workflow.")

if 'start_date' not in st.session_state:
    st.info("👈 Please select a date range on the **Overview** page first.")
else:
    # 1. Load data using shared session state
    df_events, _, df_pharm, _, _ = load_data(
        st.session_state.start_date, 
        st.session_state.end_date
    )

    if df_events.empty and df_pharm.empty:
        st.warning("No activity found for the selected dates.")
    else:
        # 2. Unify Pyxis and Pharmacy Activity
        if not df_events.empty:
            px_df = df_events[['user_name', 'dt', 'device', 'event_type', 'med_desc', 'qty', 'pk']].copy()
            px_df['source'] = 'Pyxis'
        else:
            px_df = pd.DataFrame()

        if not df_pharm.empty:
            ph_df = df_pharm[['user_name', 'dt', 'destination', 'priority', 'med_desc', 'qty', 'pk']].copy()
            ph_df.rename(columns={'priority': 'event_type', 'destination': 'device'}, inplace=True)
            ph_df['source'] = 'Pharmacy'
        else:
            ph_df = pd.DataFrame()

        combined = pd.concat([px_df, ph_df], ignore_index=True)
        combined['dt'] = pd.to_datetime(combined['dt'])
        combined.sort_values(['user_name', 'dt'], inplace=True)

        # 3. Session Logic (20-minute gap threshold)
        combined['prev_user'] = combined['user_name'].shift(1)
        combined['prev_dt'] = combined['dt'].shift(1)
        combined['gap'] = (combined['dt'] - combined['prev_dt']).dt.total_seconds().fillna(0)
        
        combined['is_new_session'] = np.where(
            (combined['user_name'] != combined['prev_user']) | (combined['gap'] > 1200), 1, 0
        )
        combined['session_id'] = combined['is_new_session'].cumsum()

        # 4. Aggregate Sessions
        sessions = combined.groupby('session_id').agg({
            'user_name': 'first',
            'dt': ['min', 'max'],
            'pk': 'count',
            'device': 'nunique'
        }).reset_index()
        
        sessions.columns = ['session_id', 'User', 'Start', 'End', 'Transactions', 'Stations Visited']
        sessions['Duration'] = (sessions['End'] - sessions['Start']).dt.total_seconds()
        
        # 5. User Deep Dive Filter
        all_users = sorted(sessions['User'].unique())
        sel_u = st.selectbox("Select Technician to Analyze:", all_users)
        
        u_sessions = sessions[sessions['User'] == sel_u].sort_values('Start', ascending=False)

        # --- SESSION METRICS ---
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Sessions", len(u_sessions))
        c2.metric("Avg Session Time", seconds_to_mmss(u_sessions['Duration'].mean()))
        c3.metric("Total Transactions", u_sessions['Transactions'].sum())

        st.divider()
        st.subheader(f"Timeline for {sel_u}")
        
        # Display session list
        u_display = u_sessions.copy()
        u_display['Duration'] = u_display['Duration'].apply(seconds_to_mmss)
        u_display['Start'] = u_display['Start'].dt.strftime('%m/%d %H:%M')
        
        event = st.dataframe(
            u_display, 
            use_container_width=True, 
            on_select="rerun", 
            selection_mode="single-row",
            hide_index=True
        )

        # 6. Drill-down into specific session details
        if len(event.selection.rows) > 0:
            idx = event.selection.rows[0]
            sess_id = u_display.iloc[idx]['session_id']
            details = combined[combined['session_id'] == sess_id].sort_values('dt')
            
            st.markdown(f"### 🔬 Session Details: {sess_id}")
            st.dataframe(
                details[['dt', 'source', 'device', 'event_type', 'med_desc', 'qty']], 
                use_container_width=True,
                column_config={"dt": st.column_config.DatetimeColumn("Time", format="HH:mm:ss")}
            )
