import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from App import engine, normalize_name # Import normalization from main App

st.set_page_config(page_title="RxTrack Brain", page_icon="🧠", layout="wide")

@st.cache_data(ttl=600)
def load_all_time_data():
    # 1. Pull all three tables to enable Shift-Level auditing
    df_e = pd.read_sql("SELECT * FROM events", engine)
    df_p = pd.read_sql("SELECT * FROM pharmacy_orders", engine)
    df_s = pd.read_sql("SELECT * FROM staff_schedule", engine)
    
    # 2. Force numeric types on inventory columns
    numeric_cols = ['qty', 'discrepancy_qty', 'beginning_qty', 'ending_qty']
    for col in numeric_cols:
        if col in df_e.columns:
            df_e[col] = pd.to_numeric(df_e[col], errors='coerce').fillna(0)
    
    # 3. Standardize dates and names for the Global Brain
    df_e['dt'] = pd.to_datetime(df_e['dt'], errors='coerce')
    df_e['date_only'] = df_e['dt'].dt.date
    # Apply the fixed normalize_name to create the link
    df_e['match_key'] = df_e['user_name'].apply(normalize_name)
    
    df_s['date_obj'] = pd.to_datetime(df_s['dt']).dt.date
    df_s['match_key'] = df_s['staff_name'].apply(normalize_name)
        
    return df_e, df_p, df_s

st.header("🧠 RxTrack Intelligence Engine")
st.caption("Scanning global historical data for trends and anomalies.")

try:
    # Pull data
    df_events_all, df_pharm_all, df_sched_all = load_all_time_data()
    
    # --- SECTION 1: BURN RATE & DRIFT (Keep existing logic) ---
    # ... [Your Burn Rate and Global Drift code remains here] ...

    # --- SECTION 2: RESTOCK ACCURACY AUDITOR (DRILL-DOWN) ---
    st.divider()
    st.subheader("🎯 Restock Accuracy Auditor (Last Touch)")
    st.caption("👆 Click a row to see the exact raw logs for that specific inventory drift.")

    if not df_events_all.empty:
        # 1. Sequence Analysis
        audit_df = df_events_all.sort_values(['device', 'med_id', 'dt']).copy()
        audit_df['prev_tech'] = audit_df.groupby(['device', 'med_id'])['user_name'].shift(1)
        audit_df['prev_event'] = audit_df.groupby(['device', 'med_id'])['event_type'].shift(1)
        audit_df['prev_match_key'] = audit_df.groupby(['device', 'med_id'])['match_key'].shift(1)
        audit_df['prev_date'] = audit_df.groupby(['device', 'med_id'])['date_only'].shift(1)

        # Identify Errors
        error_mask = (
            (audit_df['discrepancy_qty'] != 0) & 
            (audit_df['prev_event'].str.contains('REFILL|LOAD', case=False, na=False))
        )
        errors = audit_df[error_mask].copy()

        if not errors.empty:
            # 2. Join with Schedule for Shift Analysis
            errors_with_shift = pd.merge(
                errors, 
                df_sched_all[['date_obj', 'match_key', 'shift_type']], 
                left_on=['prev_date', 'prev_match_key'], 
                right_on=['date_obj', 'match_key'], 
                how='left'
            )

            # 3. Interactive Main Table
            event_selection = st.dataframe(
                errors_with_shift[['dt', 'device', 'med_desc', 'prev_tech', 'shift_type', 'discrepancy_qty', 'user_name']],
                width='stretch', hide_index=True, on_select="rerun", selection_mode="single-row",
                column_config={
                    "dt": st.column_config.DatetimeColumn("Discovery Time", format="MM/DD HH:mm"),
                    "prev_tech": "Restock Tech",
                    "shift_type": "Shift Worked",
                    "user_name": "Discovering Tech",
                    "discrepancy_qty": "Variance"
                }
            )

            # 4. Drill-Down Logic
            if len(event_selection.selection.rows) > 0:
                idx = event_selection.selection.rows[0]
                sel_row = errors_with_shift.iloc[idx]
                
                st.divider()
                st.subheader(f"🔬 Audit Trail: {sel_row['med_desc']} at {sel_row['device']}")
                
                # Show events surrounding the error
                raw_trail = df_events_all[
                    (df_events_all['device'] == sel_row['device']) & 
                    (df_events_all['med_id'] == sel_row['med_id'])
                ].copy()
                
                start_audit = sel_row['dt'] - pd.Timedelta(hours=4)
                end_audit = sel_row['dt'] + pd.Timedelta(hours=1)
                raw_trail = raw_trail[(raw_trail['dt'] >= start_audit) & (raw_trail['dt'] <= end_audit)]

                st.dataframe(
                    raw_trail[['dt', 'user_name', 'event_type', 'qty', 'beginning_qty', 'ending_qty', 'discrepancy_qty']],
                    width='stretch', hide_index=True,
                    column_config={"dt": st.column_config.DatetimeColumn("Time", format="HH:mm:ss")}
                )
                
                st.info(f"💡 Investigation: {sel_row['prev_tech']} restocked at {sel_row['dt']}. Next user {sel_row['user_name']} found {sel_row['discrepancy_qty']} variance.")

            # 5. Visual Leaderboard
            st.divider()
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                tech_errs = errors_with_shift['prev_tech'].value_counts().reset_index()
                st.plotly_chart(px.bar(tech_errs, x='count', y='prev_tech', orientation='h', title="Errors by Technician"))
            with col_b2:
                shift_errs = errors_with_shift['shift_type'].value_counts().reset_index()
                st.plotly_chart(px.pie(shift_errs, names='shift_type', values='count', hole=0.4, title="Errors by Shift Type"))

except Exception as e:
    st.error(f"Brain Scan failed: {e}")
