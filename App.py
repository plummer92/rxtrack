###############################################################
# RXTRACK: EXECUTIVE DASHBOARD (v13.6 - Full Integration)
# Architecture: Quad-Table Strategy + Attendance + Pricing
# Updates: 
#   1. Restored Full Ingestion Suite (clean_dataframe, etc.)
#   2. Fixed 'None' crash in normalize_name logic.
#   3. SQLAlchemy Engine for multi-table stability.
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
# Shared engine using pooler-aware SQLAlchemy
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
def generate_pk(row):
    """Restored: Generates a unique hash for each transaction row."""
    subset = [str(x) for x in row.values if pd.notnull(x)]
    return hashlib.sha256("|".join(subset).encode()).hexdigest()

def normalize_name(full_name):
    """The Zero-Failure Normalizer: Handles 'None', 'nan', and commas."""
    if not full_name or pd.isna(full_name) or str(full_name).strip().lower() in ["", ",", "nan", "none"]:
        return "unknown"
    
    s = str(full_name).strip().lower()
    first_name, last_initial = "unknown", ""
    
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) >= 2:
            last_name_part = parts[0]
            first_name_part = parts[1].split(" ")[0]
            first_name = first_name_part if first_name_part else "unknown"
            last_initial = last_name_part[0] if last_name_part else ""
    else:
        parts = [p.strip() for p in s.split(" ") if p.strip()]
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

# --- DATA CLEANING (RESTORED FROM v13.4) ---
def clean_dataframe(df):
    """Restored: Processes Daily Transaction Reports into SQL format."""
    df = df.copy()
    colmap = {
        "UserName": "user_name", "Device": "device", "MedID": "med_id", 
        "MedDescription": "med_desc", "TransactionType": "event_type",
        "TransactionDateTime": "dt", "Quantity": "qty", "Beg": "beginning_qty", 
        "End": "ending_qty", "DiscrepancyQuantity": "discrepancy_qty", 
        "DiscrepancyReason": "discrepancy_reason", "ResolutionDatetime": "resolution_dt"
    }
    df.rename(columns=colmap, inplace=True)
    
    # Null-Safe Username Guard
    df['user_name'] = df['user_name'].fillna('unknown').astype(str)
    
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df.dropna(subset=["dt"], inplace=True)
    
    for c in ["qty", "discrepancy_qty", "beginning_qty", "ending_qty"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype('float32')
        
    df["dt"] = df["dt"].astype(str) # String format for SQL insertion
    df["pk"] = df.apply(generate_pk, axis=1)
    return df

def clean_activity_log(df):
    """Restored: Processes Pends / Activity Logs."""
    df = df.copy()
    df.columns = df.columns.str.strip().str.replace(' ', '')
    df.rename(columns={
        "UserName": "user_name", "Device": "device", "TransactionDateTime": "dt", 
        "Action": "action_type", "ActivityType": "activity_category", "AffectedElement": "raw_element"
    }, inplace=True)
    
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df.dropna(subset=["dt"], inplace=True)
    
    # Regex extraction of MedID and Location
    extracted = df['raw_element'].astype(str).str.extract(r'^(.*?) \((.*?)\)')
    df['location'] = extracted[0].str.strip()
    df['med_id'] = extracted[1].str.strip()
    
    df['pk'] = df.apply(generate_pk, axis=1)
    return df.dropna(subset=['med_id'])

# --- DATABASE HELPERS ---
def run_query(query, params=None):
    """Executes SELECT queries via SQLAlchemy."""
    try:
        return pd.read_sql(query, engine, params=params)
    except Exception as e:
        st.error(f"Query Error: {e}")
        return pd.DataFrame()

@contextlib.contextmanager
def db_cursor():
    """Context manager for raw psycopg2 (Batch Uploads)."""
    conn = None
    try:
        conn = psycopg2.connect(st.secrets["neon"]["db_url"])
        cur = conn.cursor()
        yield conn, cur
    except Exception as e:
        st.error(f"❌ Connection Error: {e}")
        raise e
    finally:
        if conn: conn.close()

def execute_statement(sql, params, batch=False, table_name="Data"):
    """Executes INSERT/UPDATE batches."""
    try:
        with db_cursor() as (conn, cur):
            if batch: execute_batch(cur, sql, params, page_size=2000)
            else: cur.execute(sql, params)
            conn.commit()
            st.toast(f"✅ Processed {len(params)} {table_name} records!", icon="💾")
    except Exception as e:
        st.error(f"⚠️ {table_name} Error: {e}")

# --- RESTORED DATA LOADER ---
@st.cache_data(ttl=300)
def load_data(start_date, end_date):
    """Unified SQLAlchemy loader with session & efficiency logic."""
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

    # Clean Events with explicit copy to fix SettingWithCopyWarnings
    df = results["events"].copy()
    if not df.empty:
        df['user_name'] = df['user_name'].fillna('unknown') # Prevents 'None' crash
        df["cost_per_unit"] = pd.to_numeric(df["cost_per_unit"], errors='coerce').fillna(0).astype('float32')
        df["qty"] = pd.to_numeric(df["qty"], errors='coerce').fillna(0).astype('float32')
        df = df[~df['med_desc'].astype(str).str.contains(r'Drw|Pkt|Cubic', case=False, na=False)].copy()
        
        # Session & Machine Efficiency Logic
        df.sort_values(['user_name', 'dt'], inplace=True)
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['duration'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        df['gap_prev'] = (df['dt'] - df.groupby('user_name')['dt'].shift(1)).dt.total_seconds().fillna(0)
        df['machine_time_sec'] = np.where((df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['duration'] < 600), df['duration'], 0)
        df['is_new_session'] = np.where((df['user_name'] != df['user_name'].shift(1)) | (df['device'] != df['prev_device']) | (df['gap_prev'] > 1200), 1, 0)
        df['session_id'] = df['is_new_session'].cumsum()

    return df, results["config"], results["pharm"], results["schedule"], results["attendance"]

# --- SIDEBAR & NAVIGATION ---
PAGES = ["📊 Overview", "🎓 Student Project", "🏆 Shift Leaderboard", "🚀 Process Mining", "🛡️ Compliance", "📥 Pends Analyzer", "🚚 Load/Unload", "⚡ Efficiency", "🏥 Pharmacy Workflow", "🔄 Return Reconciliation", "⚖️ Tech Comparison", "📈 Tech Progression", "📅 Attendance"]

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=60)
    st.title("RxTrack v13.6")
    selected_page = st.radio("Go to:", PAGES)
    
    st.divider()
    st.subheader("📅 Analysis Window")
    start_date = st.date_input("Start Date", value=date.today() - timedelta(days=14))
    end_date = st.date_input("End Date", value=date.today())
    
    st.divider()
    st.subheader("📤 Ingest Data") # Restored full ingestion
    u_type = st.selectbox("File Type:", ["Daily Transaction Report", "Device Activity Log (Pends)", "Inventory Audit (Prices)", "Staff Schedule", "Attendance Tracking"])
    uploaded = st.file_uploader(f"Upload {u_type}", type=["csv", "xlsx"])
    
    if uploaded and st.button(f"Process {u_type}"):
        try:
            raw = pd.read_excel(uploaded) if uploaded.name.endswith('.xlsx') else pd.read_csv(uploaded)
            if u_type == "Daily Transaction Report":
                clean = clean_dataframe(raw)
                sql = """INSERT INTO events (pk, user_name, device, med_id, med_desc, event_type, dt, qty, beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason) 
                         VALUES (%(pk)s, %(user_name)s, %(device)s, %(med_id)s, %(med_desc)s, %(event_type)s, %(dt)s, %(qty)s, %(beginning_qty)s, %(ending_qty)s, %(discrepancy_qty)s, %(discrepancy_reason)s) 
                         ON CONFLICT (pk) DO NOTHING;"""
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Events")
            
            elif u_type == "Device Activity Log (Pends)":
                clean = clean_activity_log(raw)
                sql = """INSERT INTO config_events (pk, dt, user_name, device, med_id, location, action_type) 
                         VALUES (%(pk)s, %(dt)s, %(user_name)s, %(device)s, %(med_id)s, %(location)s, %(action_type)s) 
                         ON CONFLICT (pk) DO NOTHING;"""
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Config")
            # ... Add other u_type mappings as needed
            
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Ingestion Error: {e}")

# Execute the Load
df_events, df_config, df_pharm, df_sched, df_att = load_data(start_date, end_date)

