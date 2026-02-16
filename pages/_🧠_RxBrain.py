import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from App import load_data, engine 

st.set_page_config(page_title="RxTrack Brain", page_icon="🧠", layout="wide")

# --- ALWAYS-ON BRAIN SCAN ---
@st.cache_data(ttl=600)
def load_all_time_data():
    # 1. Pull raw data
    df_e = pd.read_sql("SELECT * FROM events", engine)
    df_p = pd.read_sql("SELECT * FROM pharmacy_orders", engine)
    
    # 2. CLEAN ALL NUMERIC COLUMNS (Prevents 'None' and 'Syntax' errors)
    # We must explicitly clean these for the Brain to work correctly
    numeric_cols = ['qty', 'discrepancy_qty', 'beginning_qty', 'ending_qty']
    
    for col in numeric_cols:
        if col in df_e.columns:
            df_e[col] = pd.to_numeric(df_e[col], errors='coerce').fillna(0)
        if col in df_p.columns and col == 'qty':
            df_p[col] = pd.to_numeric(df_p[col], errors='coerce').fillna(0)

    # 3. Ensure Timestamps are actual Datetime objects
    df_e['dt'] = pd.to_datetime(df_e['dt'], errors='coerce')
    df_p['dt'] = pd.to_datetime(df_p['dt'], errors='coerce')
        
    return df_e, df_p

st.header("🧠 RxTrack Intelligence Engine")
st.caption("Scanning global historical data for trends and anomalies.")

# Initialize empty to prevent NameErrors if loading fails
df_events_all = pd.DataFrame()
df_pharm_all = pd.DataFrame()

try:
    df_events_all, df_pharm_all = load_all_time_data()
    
    # 1. Burn Rate Prediction
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

RILL-DOWN ---
        st.divider()
        st.subheader("🎯 Restock Accuracy Auditor (Last Touch)")
        st.caption("👆 Click a row to see the exact raw logs for that specific inventory drift.")

        if not df_events_all.empty and not df_sched_all.empty:
            # 1. Sequence Analysis logic (Keep as is)
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
                # 2. Join with Schedule for Shift data
                errors_with_shift = pd.merge(
                    errors, 
                    df_sched_all[['date_obj', 'match_key', 'shift_type']], 
                    left_on=['prev_date', 'prev_match_key'], 
                    right_on=['date_obj', 'match_key'], 
                    how='left'
                )

                # 3. Interactive Main Table
                # We store the user's selection in 'event_selection'
                event_selection = st.dataframe(
                    errors_with_shift[['dt', 'device', 'med_desc', 'prev_tech', 'shift_type', 'discrepancy_qty', 'user_name']],
                    width='stretch',
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    column_config={
                        "dt": st.column_config.DatetimeColumn("Discovery Time", format="MM/DD HH:mm"),
                        "prev_tech": "Restock Tech",
                        "shift_type": "Shift worked",
                        "user_name": "Discovering Tech",
                        "discrepancy_qty": "Variance"
                    }
                )

                # 4. Drill-Down Logic: The "Micro-Audit"
                if len(event_selection.selection.rows) > 0:
                    idx = event_selection.selection.rows[0]
                    sel_row = errors_with_shift.iloc[idx]
                    
                    st.divider()
                    st.subheader(f"🔬 Audit Trail: {sel_row['med_desc']} at {sel_row['device']}")
                    
                    # Pull the raw sequence of transactions for this specific medication/device
                    # This shows what happened just before the restock and just after
                    raw_trail = df_events_all[
                        (df_events_all['device'] == sel_row['device']) & 
                        (df_events_all['med_id'] == sel_row['med_id'])
                    ].copy()
                    
                    # Filter for events within +/- 1 hour of the discovery to keep it clean
                    start_audit = sel_row['dt'] - pd.Timedelta(hours=4)
                    end_audit = sel_row['dt'] + pd.Timedelta(hours=1)
                    raw_trail = raw_trail[(raw_trail['dt'] >= start_audit) & (raw_trail['dt'] <= end_audit)]

                    st.dataframe(
                        raw_trail[['dt', 'user_name', 'event_type', 'qty', 'beginning_qty', 'ending_qty', 'discrepancy_qty']],
                        width='stretch',
                        hide_index=True,
                        column_config={
                            "dt": st.column_config.DatetimeColumn("Exact Timestamp", format="MM/DD HH:mm:ss"),
                            "qty": "Action Qty",
                            "beginning_qty": "Start Count",
                            "ending_qty": "End Count"
                        }
                    )
                    
                    st.info(f"💡 Investigation Note: {sel_row['prev_tech']} performed a {sel_row['prev_event']} at {sel_row['dt']}. "
                            f"The very next user, {sel_row['user_name']}, identified a discrepancy of {sel_row['discrepancy_qty']}.")
            else:
                st.success("✅ Global memory scan complete: No restock accuracy errors found.")
        
        # Visual Leaderboard
        error_counts = errors['prev_tech'].value_counts().reset_index()
        error_counts.columns = ['Technician', 'Count']
        fig_errors = px.bar(error_counts, x='Count', y='Technician', orientation='h', title="Potential Entry Errors by Tech")
        st.plotly_chart(fig_errors, width='stretch')
    else:
        st.success("✅ No discrepancies found immediately following restocks.")
