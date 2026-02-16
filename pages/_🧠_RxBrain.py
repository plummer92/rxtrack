import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from App import engine, normalize_name 

st.set_page_config(page_title="RxTrack Brain", page_icon="🧠", layout="wide")

# --- ALWAYS-ON BRAIN SCAN ---
@st.cache_data(ttl=600)
def load_all_time_data():
    # 1. Pull core tables from global engine
    df_e = pd.read_sql("SELECT * FROM events", engine)
    df_p = pd.read_sql("SELECT * FROM pharmacy_orders", engine)
    df_s = pd.read_sql("SELECT * FROM staff_schedule", engine)
    
    # 2. Force numeric types for math
    numeric_cols = ['qty', 'discrepancy_qty', 'beginning_qty', 'ending_qty']
    for col in numeric_cols:
        if col in df_e.columns:
            df_e[col] = pd.to_numeric(df_e[col], errors='coerce').fillna(0)
    
    # 3. Standardize dates and names
    df_e['dt'] = pd.to_datetime(df_e['dt'], errors='coerce')
    df_e['date_only'] = df_e['dt'].dt.date
    df_e['match_key'] = df_e['user_name'].apply(normalize_name)
    
    df_s['date_obj'] = pd.to_datetime(df_s['dt']).dt.date
    df_s['match_key'] = df_s['staff_name'].apply(normalize_name)
        
    return df_e, df_p, df_s

st.header("🧠 RxTrack Intelligence Engine")
st.caption("Scanning global historical data for trends and anomalies.")

# Global variables to prevent NameErrors
df_events_all = pd.DataFrame()
df_pharm_all = pd.DataFrame()
df_sched_all = pd.DataFrame()

# --- EMERGENCY HEARTBEAT DEBUG ---
with st.expander("🛠️ System Heartbeat (Debug Console)", expanded=True):
    try:
        # 1. Test the Raw Connection
        from sqlalchemy import text
        with engine.connect() as conn:
            # Check row counts across all tables
            e_count = conn.execute(text("SELECT COUNT(*) FROM events")).scalar()
            s_count = conn.execute(text("SELECT COUNT(*) FROM staff_schedule")).scalar()
            
            st.write(f"✅ Connection: Active")
            st.write(f"📊 Events in DB: **{e_count}**")
            st.write(f"📅 Schedule Rows: **{s_count}**")

            # 2. Inspect Malformed Usernames
            # This targets the "string index out of range" crash
            st.write("🕵️ Scanning for malformed names...")
            bad_names = pd.read_sql("SELECT DISTINCT user_name FROM events WHERE user_name IS NULL OR user_name = '' OR user_name = ',' LIMIT 5", engine)
            if not bad_names.empty:
                st.error(f"Found {len(bad_names)} malformed usernames that might be crashing the Brain.")
                st.dataframe(bad_names)
            else:
                st.success("No empty/comma-only usernames found in top results.")

    except Exception as db_err:
        st.error(f"❌ Heartbeat Failed: {db_err}")

try:
    df_events_all, df_pharm_all, df_sched_all = load_all_time_data()
    
    # --- 1. BURN RATE PREDICTION ---
    if not df_events_all.empty:
        last_time = df_events_all['dt'].max()
        recent_24h = df_events_all[df_events_all['dt'] > (last_time - pd.Timedelta(hours=24))].copy()
        
        burn = recent_24h.dropna(subset=['ending_qty']).groupby(['device', 'med_desc']).agg(
            pulled=('qty', 'sum'),
            current_inv=('ending_qty', 'last')
        ).reset_index()
        
        burn['hrs_left'] = burn['current_inv'] / (burn['pulled'] / 24)
        critical = burn[burn['hrs_left'] < 12].sort_values('hrs_left')
        
        if not critical.empty:
            st.error(f"🚩 High Alert: {len(critical)} imminent stockout risks found in global scan.")
            st.dataframe(critical, width='stretch')

    # --- 2. GLOBAL DRIFT AUDITOR ---
    st.divider()
    st.subheader("🕵️ Global Drift Auditor")
    drift_df = df_events_all.dropna(subset=['beginning_qty', 'ending_qty']).copy()
    drift_df['expected'] = drift_df['beginning_qty'] - drift_df['qty']
    mismatch = drift_df[drift_df['ending_qty'] != drift_df['expected']]
    
    if not mismatch.empty:
        st.warning(f"Found {len(mismatch)} historical count discrepancies.")
        st.dataframe(mismatch[['dt', 'device', 'med_desc', 'qty', 'beginning_qty', 'ending_qty']], width='stretch')

    # --- 3. RESTOCK ACCURACY AUDITOR (DRILL-DOWN) ---
    st.divider()
    st.subheader("🎯 Restock Accuracy Auditor (Last Touch)")
    st.caption("👆 Click a row to see the exact raw logs for that specific inventory drift.")

    if not df_events_all.empty:
        # Sequence Analysis
        audit_df = df_events_all.sort_values(['device', 'med_id', 'dt']).copy()
        audit_df['prev_tech'] = audit_df.groupby(['device', 'med_id'])['user_name'].shift(1)
        audit_df['prev_event'] = audit_df.groupby(['device', 'med_id'])['event_type'].shift(1)
        audit_df['prev_match_key'] = audit_df.groupby(['device', 'med_id'])['match_key'].shift(1)
        audit_df['prev_date'] = audit_df.groupby(['device', 'med_id'])['date_only'].shift(1)

        error_mask = (
            (audit_df['discrepancy_qty'] != 0) & 
            (audit_df['prev_event'].str.contains('REFILL|LOAD', case=False, na=False))
        )
        errors = audit_df[error_mask].copy()

        if not errors.empty:
            # Join with Schedule for Shift data
            errors_with_shift = pd.merge(
                errors, 
                df_sched_all[['date_obj', 'match_key', 'shift_type']], 
                left_on=['prev_date', 'prev_match_key'], 
                right_on=['date_obj', 'match_key'], 
                how='left'
            )

            # Interactive Selection Table
            event_sel = st.dataframe(
                errors_with_shift[['dt', 'device', 'med_desc', 'prev_tech', 'shift_type', 'discrepancy_qty', 'user_name']],
                width='stretch', hide_index=True, on_select="rerun", selection_mode="single-row",
                column_config={"dt": "Discovery Time", "prev_tech": "Restock Tech", "user_name": "Discovering Tech"}
            )

            if len(event_sel.selection.rows) > 0:
                idx = event_sel.selection.rows[0]
                sel_row = errors_with_shift.iloc[idx]
                st.divider()
                st.subheader(f"🔬 Audit Trail: {sel_row['med_desc']}")
                
                # Show events surrounding the error
                raw_trail = df_events_all[
                    (df_events_all['device'] == sel_row['device']) & (df_events_all['med_id'] == sel_row['med_id'])
                ].copy()
                start_win, end_win = sel_row['dt'] - pd.Timedelta(hours=4), sel_row['dt'] + pd.Timedelta(hours=1)
                st.dataframe(raw_trail[(raw_trail['dt'] >= start_win) & (raw_trail['dt'] <= end_win)], width='stretch')

            # Leaderboard
            col1, col2 = st.columns(2)
            with col1:
                st.plotly_chart(px.bar(errors_with_shift['prev_tech'].value_counts().reset_index(), x='count', y='prev_tech', orientation='h', title="Errors by Tech"), width='stretch')
            with col2:
                st.plotly_chart(px.pie(errors_with_shift['shift_type'].value_counts().reset_index(), names='shift_type', values='count', hole=0.4, title="Errors by Shift"), width='stretch')

except Exception as e:
    st.error(f"Brain Scan failed: {e}")
    # --- DEBUGGING LAB ---
    with st.expander("🛠️ Troubleshooting: View Raw Data Integrity"):
        st.write("Current Database Schema Check:")
        if not df_events_all.empty:
            st.write("Events Table Columns:", df_events_all.columns.tolist())
            st.write("Sample Row:", df_events_all.iloc[0].to_dict())
        else:
            st.warning("Events table is currently empty or failed to load.")
