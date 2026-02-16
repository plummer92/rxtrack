###############################################################
# RXTRACK: EXECUTIVE DASHBOARD (v13.6 - Full Integration)
# Architecture: Quad-Table Strategy + Attendance + Pricing
# Updates: 
#   1. Robust normalize_name to fix Brain Scan crash.
#   2. SQLAlchemy Engine integration for all pages.
#   3. Fixed SettingWithCopyWarnings & width deprecations.
###############################################################

import streamlit as st
import pandas as pd
import numpy as np
import hashlib
import psycopg2
from psycopg2.extras import execute_batch
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, date
import re
import contextlib
import warnings
from sqlalchemy import create_engine

# --- DATABASE CONFIGURATION ---
# Shared engine used by all pages and the RxBrain
# Update this line in App.py
DB_URL = st.secrets["neon"]["db_url"] 
engine = create_engine(DB_URL)

# --- STREAMLIT CONFIGURATION ---
st.set_page_config(
    page_title="RxTrack: Workforce & Efficiency", 
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Suppress DB/Pandas warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pandas")

# --- INITIALIZE VARIABLES ---
df_events = pd.DataFrame()
df_config = pd.DataFrame()
df_pharm = pd.DataFrame()
df_sched = pd.DataFrame()
df_att = pd.DataFrame()

# --- CONSTANTS ---
NARC_TERMS = ["OXYCODONE", "MORPHINE", "FENTANYL", "HYDROMORPHONE", "HYDROCODONE", "LORAZEPAM", "MIDAZOLAM", "DIAZEPAM", "ALPRAZOLAM", "CODEINE", "METHADONE", "KETAMINE", "TRAMADOL", "ZOLPIDEM", "PHENOBARBITAL", "BUPRENORPHINE", "LACOSAMIDE", "VIMPAT", "PREGABALIN", "LYRICA", "CHLORDIAZEPOXIDE", "LIBRIUM", "CLONAZEPAM", "KLONOPIN"]
ADMIN_USERS = ['emily', 'joe', 'krista']
NAME_MAPPINGS = {"phi": "ali", "ho": "ali", "rebekah": "bekah", "nugent": "kathy", "kathleen": "kathy", "spain": "dee", "deloris": "dee", "jabusch": "dan", "daniel": "dan", "nicholas": "nick"}
AMBIGUOUS_NAMES = ["melissa", "emily", "sarah", "megan", "erin", "kyle", "jessica", "andy", "heather", "michelle", "taylor"]

# --- UTILITY FUNCTIONS ---
def normalize_name(full_name):
    """Robust name normalization to prevent 'string index out of range' errors."""
    if not full_name or pd.isna(full_name) or str(full_name).strip() in ["", ",", "nan"]: 
        return "unknown"
    
    s = str(full_name).strip().lower()
    first_name, last_initial = "", ""
    
    if "," in s:
        parts = s.split(",")
        if len(parts) >= 2:
            last_name_part = parts[0].strip()
            first_name_parts = parts[1].strip().split(" ")
            if first_name_parts and len(first_name_parts[0]) > 0:
                first_name = first_name_parts[0]
            if len(last_name_part) > 0: 
                last_initial = last_name_part[0]
    else:
        parts = s.split(" ")
        if len(parts) > 0:
            first_name = parts[0]
            if len(parts) > 1 and len(parts[1]) > 0: 
                last_initial = parts[1][0]
    
    for key, val in NAME_MAPPINGS.items():
        if key in first_name: 
            first_name = val
            break
            
    if first_name in AMBIGUOUS_NAMES and last_initial:
        return f"{first_name} {last_initial}"
    return first_name

def seconds_to_mmss(seconds):
    if pd.isna(seconds) or seconds < 0: return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h > 0 else (f"{m}m {s}s" if m > 0 else f"{s}s")

# --- DATABASE HELPERS ---
def run_query(query, params=None):
    """Executes a SELECT query using SQLAlchemy engine to avoid UserWarnings."""
    try:
        return pd.read_sql(query, engine, params=params)
    except Exception as e:
        st.error(f"Query Error: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=300)
def load_data(start_date, end_date):
    """Unified data loader using the shared SQLAlchemy engine."""
    queries = {
        "events": "SELECT e.*, c.cost_per_unit FROM events e LEFT JOIN med_costs c ON e.med_id = c.med_id WHERE e.dt::date BETWEEN %s AND %s",
        "config": "SELECT * FROM config_events WHERE dt::date BETWEEN %s AND %s",
        "pharm": "SELECT * FROM pharmacy_orders WHERE dt::date BETWEEN %s AND %s",
        "schedule": "SELECT * FROM staff_schedule WHERE dt BETWEEN %s AND %s",
        "attendance": "SELECT * FROM attendance_punches WHERE dt_date BETWEEN %s AND %s"
    }
    
    results = {}
    params = (start_date, end_date)
    for key, sql in queries.items():
        results[key] = run_query(sql, params)
        if not results[key].empty and 'dt' in results[key].columns:
            results[key]["dt"] = pd.to_datetime(results[key]["dt"])

    # Clean Events using explicit copy to fix SettingWithCopyWarnings
    df = results["events"].copy()
    if not df.empty:
        df["cost_per_unit"] = pd.to_numeric(df["cost_per_unit"], errors='coerce').fillna(0).astype('float32')
        df["qty"] = pd.to_numeric(df["qty"], errors='coerce').fillna(0).astype('float32')
        df = df[~df['med_desc'].astype(str).str.contains(r'Drw|Pkt|Cubic', case=False, na=False)].copy()
        
        df.sort_values(['user_name', 'dt'], inplace=True)
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['duration'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        df['gap_prev'] = (df['dt'] - df.groupby('user_name')['dt'].shift(1)).dt.total_seconds().fillna(0)
        df['machine_time_sec'] = np.where((df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['duration'] < 600), df['duration'], 0)
        df['is_new_session'] = np.where((df['user_name'] != df['user_name'].shift(1)) | (df['device'] != df['prev_device']) | (df['gap_prev'] > 1200), 1, 0)
        df['session_id'] = df['is_new_session'].cumsum()

    pharm = results["pharm"].copy()
    if not pharm.empty:
        pharm = pharm[~pharm['destination'].astype(str).str.contains('BATCH PICK', case=False, na=False)].copy()

    return df, results["config"], pharm, results["schedule"], results["attendance"]

# --- MAIN APP ROUTING ---
PAGES = ["📊 Overview", "🎓 Student Project", "🏆 Shift Leaderboard", "🚀 Process Mining", "🛡️ Compliance", "📥 Pends Analyzer", "🚚 Load/Unload", "⚡ Efficiency", "🏥 Pharmacy Workflow", "🔄 Return Reconciliation", "⚖️ Tech Comparison", "📈 Tech Progression", "📅 Attendance"]

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=60)
    st.title("RxTrack v13.6")
    selected_page = st.radio("Go to:", PAGES)
    
    # Persistent Date Logic
    st.divider()
    st.subheader("📅 Analysis Window")
    start_date = st.date_input("Start Date", value=date.today() - timedelta(days=14))
    end_date = st.date_input("End Date", value=date.today())

# Execute the Load
df_events, df_config, df_pharm, df_sched, df_att = load_data(start_date, end_date)

# --- INDIVIDUAL PAGE MODULES ---
if selected_page == "📊 Overview":
    st.header("🏥 Executive Summary")
    if not df_events.empty:
        session_stats = df_events.groupby('session_id').agg(total_time=('machine_time_sec', 'sum'))
        avg_time = session_stats['total_time'].mean()
        real_tx = df_events[~df_events['event_type'].str.contains('verify', case=False, na=False)]
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Transactions", f"{len(real_tx):,}")
        c2.metric("Avg Session", seconds_to_mmss(avg_time))
        c3.metric("Active Techs", df_events["user_name"].nunique())
        c4.metric("Discrepancies", int(df_events["discrepancy_qty"].ne(0).sum()))

        # Top Problem Meds Visualization
        st.subheader("🐢 Slowest Medications (Machine Time)")
        med_speed = df_events[df_events['machine_time_sec'] > 0].groupby('med_desc')['machine_time_sec'].mean().reset_index()
        top_slow = med_speed.sort_values('machine_time_sec', ascending=False).head(10)
        fig = px.bar(top_slow, x='machine_time_sec', y='med_desc', orientation='h', color='machine_time_sec', color_continuous_scale='Reds')
        st.plotly_chart(fig, width='stretch')

elif selected_page == "🎓 Student Project":
    st.header("🎓 Student Optimization Project")
    # ... (Include your existing Student Project code here, ensuring you use width='stretch' for tables) ...

elif selected_page == "🔄 Return Reconciliation":
    st.header("🔄 Unload vs. Return Reconciliation")
    # ... (Include your existing Return Reconciliation code here) ...

# ... (Include all other elif blocks for Compliance, Attendance, etc. as they were in your long file) ...

elif selected_page == "🏥 Pharmacy Workflow":
    st.header("🏥 Central Pharmacy Workflow & Stockout Intelligence")
    # ... (Include your Pharmacy Workflow logic here) ...
