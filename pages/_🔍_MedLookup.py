import streamlit as st
import pandas as pd
from App import engine, normalize_name

st.set_page_config(page_title="Med Audit Trail", page_icon="🔍", layout="wide")

st.header("🔍 Advanced Medication Audit")
st.caption("Deep-dive into specific medication history with technician and device filtering.")

# 1. Search Entry
med_query = st.text_input("Enter Med Description (e.g., Heparin):", "heparin 5000")

if med_query:
    # Pull global history for this med
    query = f"SELECT * FROM events WHERE med_desc ILIKE '%%{med_query}%%' ORDER BY dt DESC"
    
    with st.spinner("Scanning database..."):
        df_raw = pd.read_sql(query, engine)

    if not df_raw.empty:
        # Standardize Names
        df_raw['tech_name'] = df_raw['user_name'].apply(normalize_name)
        
        # --- SIDEBAR FILTERS ---
        st.sidebar.header("🎯 Refine Results")
        
        selected_devices = st.sidebar.multiselect(
            "Filter by Device:", 
            options=sorted(df_raw['device'].unique()),
            default=sorted(df_raw['device'].unique())
        )
        
        selected_techs = st.sidebar.multiselect(
            "Filter by Technician:", 
            options=sorted(df_raw['tech_name'].unique()),
            default=sorted(df_raw['tech_name'].unique())
        )
        
        selected_events = st.sidebar.multiselect(
            "Filter by Event Type:", 
            options=sorted(df_raw['event_type'].unique()),
            default=sorted(df_raw['event_type'].unique())
        )

        # Apply Filters
        df_filtered = df_raw[
            (df_raw['device'].isin(selected_devices)) &
            (df_raw['tech_name'].isin(selected_techs)) &
            (df_raw['event_type'].isin(selected_events))
        ].copy()

        # --- INVENTORY INTEGRITY LOGIC ---
        # Compare current 'Beginning' with previous 'Ending'
        df_filtered['prev_ending'] = df_filtered.groupby(['device', 'med_id'])['ending_qty'].shift(-1)
        df_filtered['count_gap'] = df_filtered['beginning_qty'] - df_filtered['prev_ending']

        # 2. Results Metrics
        c1, c2, c3 = st.columns(3)
        c1.metric("Matches Found", len(df_filtered))
        c2.metric("Unique Devices", df_filtered['device'].nunique())
        c3.metric("Detected Gaps", int(df_filtered['count_gap'].fillna(0).ne(0).sum()))

        # 3. Data Table
        st.subheader("📋 Audit Timeline")
        st.dataframe(
            df_filtered[['dt', 'tech_name', 'device', 'event_type', 'qty', 'beginning_qty', 'ending_qty', 'count_gap']],
            width='stretch',
            column_config={
                "dt": st.column_config.DatetimeColumn("Time", format="MM/DD HH:mm:ss"),
                "count_gap": st.column_config.NumberColumn("Inventory Gap", format="%.0f"),
                "qty": "Action Qty"
            }
        )
        
        # 4. Gap Alert
        gaps = df_filtered[df_filtered['count_gap'].fillna(0) != 0]
        if not gaps.empty:
            st.error("🚨 Inventory Gaps Detected! The following technicians found a different count than what the system expected.")
            st.dataframe(gaps[['dt', 'tech_name', 'device', 'count_gap']], width='stretch')
            
    else:
        st.warning(f"No records found for '{med_query}'.")
