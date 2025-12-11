###############################################################
# RXTRACK: EXECUTIVE DASHBOARD (IMPROVED v11.0)
# Architecture: Quad-Table Strategy (Events | Config | Pharm | Schedule)
# Improvements:
#   1. Refactored DB Logic: Consolidated connection handling to reduce repetition.
#   2. Centralized Constants: Moved hardcoded lists (Narcotics, Admins) to top.
#   3. UI Enhancements: Cleaner CSS, Toast notifications, Better charts.
#   4. Performance: Optimized Pandas operations and layout rendering.
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
import gc
import re
import contextlib

# --- CONFIGURATION ---
st.set_page_config(
    page_title="RxTrack: Workforce & Efficiency", 
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CONSTANTS ---
NARC_TERMS = [
    "OXYCODONE", "MORPHINE", "FENTANYL", "HYDROMORPHONE", "HYDROCODONE", 
    "LORAZEPAM", "MIDAZOLAM", "DIAZEPAM", "ALPRAZOLAM", "CODEINE", 
    "METHADONE", "KETAMINE", "TRAMADOL", "ZOLPIDEM", "PHENOBARBITAL", 
    "BUPRENORPHINE", "LACOSAMIDE", "VIMPAT", "PREGABALIN", "LYRICA", 
    "CHLORDIAZEPOXIDE", "LIBRIUM", "CLONAZEPAM", "KLONOPIN"
]

ADMIN_USERS = ['emily', 'joe', 'krista']

NAME_MAPPINGS = {
    "phi": "ali", "ho": "ali",
    "rebekah": "bekah",
    "nugent": "kathy", "kathleen": "kathy",
    "spain": "dee", "deloris": "dee",
    "jabusch": "dan", "daniel": "dan"
}

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    .metric-card { 
        background-color: #ffffff; 
        padding: 20px; 
        border-radius: 10px; 
        border-left: 5px solid #4CAF50; 
        box-shadow: 0 4px 6px rgba(0,0,0,0.1); 
        margin-bottom: 10px;
    }
    .metric-card h3 { 
        color: #1f2937; 
        margin: 0; 
        font-size: 26px; 
        font-weight: 700; 
    }
    .metric-card p { 
        color: #6b7280; 
        margin: 0; 
        font-size: 14px; 
        font-weight: 500; 
        text-transform: uppercase; 
        letter-spacing: 0.5px;
    }
    .cal-grid { display: flex; flex-wrap: wrap; gap: 3px; max-width: 100%; margin-top: 10px; }
    .cal-day { 
        width: 12px; height: 12px; 
        border-radius: 2px; 
        background-color: #e5e7eb;
    }
    .cal-present { background-color: #4CAF50; } /* Green */
    .cal-missing { background-color: #F87171; } /* Red */
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        height: 50px; white-space: pre-wrap;
        background-color: #f3f4f6; border-radius: 4px;
        padding-top: 10px; padding-bottom: 10px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #e5e7eb; border-bottom: 2px solid #4CAF50;
    }
    </style>
    """, unsafe_allow_html=True)

# --- DATABASE HELPERS ---
@contextlib.contextmanager
def db_cursor():
    """Context manager for database connections."""
    conn = None
    try:
        conn = psycopg2.connect(st.secrets["neon"]["db_url"])
        cur = conn.cursor()
        yield conn, cur
    except Exception as e:
        st.error(f"❌ Database Connection Error: {e}")
        raise e
    finally:
        if conn:
            conn.close()

def run_query(query, params=None):
    """Executes a SELECT query and returns a pandas DataFrame."""
    try:
        with db_cursor() as (conn, cur):
            return pd.read_sql(query, conn, params=params)
    except Exception:
        return pd.DataFrame()

def execute_statement(sql, params, batch=False, table_name="Data"):
    """Executes INSERT/UPDATE statements."""
    try:
        with db_cursor() as (conn, cur):
            if batch:
                execute_batch(cur, sql, params, page_size=2000)
            else:
                cur.execute(sql, params)
            conn.commit()
            st.toast(f"✅ Successfully processed {len(params)} records for {table_name}!", icon="💾")
    except Exception as e:
        st.error(f"⚠️ Error executing {table_name}: {e}")

def init_db():
    """Initializes tables if they do not exist."""
    schemas = [
        """CREATE TABLE IF NOT EXISTS events (
            pk TEXT PRIMARY KEY, user_name TEXT, device TEXT, med_id TEXT, med_desc TEXT, 
            event_type TEXT, dt TIMESTAMP, qty FLOAT, beginning_qty FLOAT, ending_qty FLOAT, 
            discrepancy_qty FLOAT, discrepancy_reason TEXT, resolution_dt TIMESTAMP
        );""",
        """CREATE TABLE IF NOT EXISTS config_events (
            pk TEXT PRIMARY KEY, dt TIMESTAMP, user_name TEXT, device TEXT, med_id TEXT, 
            location TEXT, action_type TEXT, activity_category TEXT, min_qty FLOAT, max_qty FLOAT, is_standard BOOLEAN
        );""",
        """CREATE TABLE IF NOT EXISTS med_costs (
            med_id TEXT PRIMARY KEY, cost_per_unit FLOAT
        );""",
        """CREATE TABLE IF NOT EXISTS pharmacy_orders (
            pk TEXT PRIMARY KEY, queue_id TEXT, priority TEXT, dt TIMESTAMP, med_id TEXT, 
            med_desc TEXT, destination TEXT, user_name TEXT, qty FLOAT
        );""",
        """CREATE TABLE IF NOT EXISTS staff_schedule (
            pk TEXT PRIMARY KEY, dt DATE, day_name TEXT, staff_name TEXT, 
            shift_type TEXT, assignment_type TEXT, raw_entry TEXT, note TEXT
        );"""
    ]
    with db_cursor() as (conn, cur):
        for sql in schemas:
            cur.execute(sql)
        conn.commit()

# --- UTILITY FUNCTIONS ---
def seconds_to_mmss(seconds):
    if pd.isna(seconds) or seconds < 0: return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0: return f"{h}h {m}m {s}s"
    if m > 0: return f"{m}m {s}s"
    return f"{s}s"

def generate_pk(row):
    subset = [str(x) for x in row.values if pd.notnull(x)]
    row_str = "|".join(subset)
    return hashlib.sha256(row_str.encode()).hexdigest()

def normalize_name(full_name):
    """Normalize user names based on business logic."""
    s = str(full_name).strip().lower()
    for key, val in NAME_MAPPINGS.items():
        if key in s: return val
    
    if "," in s:
        parts = s.split(",")
        if len(parts) >= 2:
            return parts[1].strip().split(" ")[0]
    return s.split(" ")[0]

# --- DATA CLEANING ---
def clean_dataframe(df):
    df = df.copy()
    colmap = {
        "UserName": "user_name", "UserID": "user_id", "Device": "device",
        "MedID": "med_id", "MedDescription": "med_desc", "TransactionType": "event_type",
        "TransactionDateTime": "dt", "Quantity": "qty", "Beg": "beginning_qty", 
        "End": "ending_qty", "DiscrepancyQuantity": "discrepancy_qty", 
        "DiscrepancyReason": "discrepancy_reason", "ResolutionDatetime": "resolution_dt"
    }
    df.rename(columns=colmap, inplace=True)
    
    # Ensure all required columns exist
    required = ["user_name", "device", "med_id", "med_desc", "event_type", "dt", "qty", 
                "beginning_qty", "ending_qty", "discrepancy_qty", "discrepancy_reason", "resolution_dt"]
    for col in required:
        if col not in df.columns: df[col] = None

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df.dropna(subset=["dt"], inplace=True)
    df["resolution_dt"] = pd.to_datetime(df["resolution_dt"], errors="coerce")
    
    for c in ["qty", "discrepancy_qty", "beginning_qty", "ending_qty"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype('float32')

    df["dt"] = df["dt"].astype(str)
    df["resolution_dt"] = df["resolution_dt"].astype(str).replace(['NaT', 'nan', 'None', ''], None)
    df["pk"] = df.apply(generate_pk, axis=1)
    return df[required + ["pk"]]

def clean_activity_log(df):
    df = df.copy()
    df.columns = df.columns.str.strip().str.replace(' ', '')
    df.rename(columns={
        "UserName": "user_name", "Device": "device", "TransactionDateTime": "dt", 
        "Action": "action_type", "ActivityType": "activity_category", "AffectedElement": "raw_element",
        "Amount": "qty_col", "Quantity": "qty_col"
    }, inplace=True)
    
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df.dropna(subset=["dt"], inplace=True)

    # Extract Location and MedID
    pattern_element = r'^(.*?) \((.*?)\)'
    extracted = df['raw_element'].astype(str).str.extract(pattern_element)
    df['location'] = extracted[0].str.strip()
    df['med_id'] = extracted[1].str.strip()
    df.dropna(subset=['med_id'], inplace=True)

    # Extract Quantity
    if 'qty_col' not in df.columns:
        pattern_qty = r':\s*(\d+)$' 
        df['qty_col'] = df['raw_element'].astype(str).str.extract(pattern_qty)[0]
    
    df['qty_extracted'] = pd.to_numeric(df['qty_col'], errors='coerce')
    
    # Session Grouping for Pends
    df.sort_values(['user_name', 'device', 'med_id', 'dt'], inplace=True)
    df['time_gap'] = df.groupby(['user_name', 'device', 'med_id'])['dt'].diff().dt.total_seconds().fillna(999)
    df['group_id'] = (df['time_gap'] > 120).astype(int).cumsum()
    
    # Identify Event Types
    df['is_min'] = df['activity_category'].str.contains('Min', case=False, na=False)
    df['is_max'] = df['activity_category'].str.contains('Max', case=False, na=False)
    df['is_std'] = df['activity_category'].str.contains('Standard Stock', case=False, na=False)
    
    # Aggregate to single config change event
    agg_funcs = {
        'location': 'first', 'dt': 'first', 'action_type': 'first', 'activity_category': 'first',
        'is_std': 'max', 'qty_extracted': 'max' # Simplified logic, adjust if rigorous min/max splitting needed
    }
    
    # More robust Min/Max logic
    df['min_qty'] = np.where(df['is_min'], df['qty_extracted'], np.nan)
    df['max_qty'] = np.where(df['is_max'], df['qty_extracted'], np.nan)
    # If not min or max, assume max (common in single value updates)
    df['max_qty'] = np.where((~df['is_min']) & (~df['is_max']), df['qty_extracted'], df['max_qty'])

    grouped = df.groupby(['user_name', 'device', 'med_id', 'group_id'], as_index=False).agg({
        'min_qty': 'max', 'max_qty': 'max', 'is_std': 'max',
        'location': 'first', 'dt': 'first', 'action_type': 'first', 'activity_category': 'first'
    })

    grouped["dt"] = grouped["dt"].astype(str)
    grouped["pk"] = grouped.apply(generate_pk, axis=1)
    grouped.replace({np.nan: None}, inplace=True)
    
    return grouped.rename(columns={'is_std': 'is_standard'})[['pk', 'dt', 'user_name', 'device', 'med_id', 'location', 'action_type', 'activity_category', 'min_qty', 'max_qty', 'is_standard']]

def clean_pharmacy_report(df):
    df = df.copy()
    colmap = {
        "TranQueueID": "queue_id", "Priority": "priority", "Date / Time": "dt",
        "Item ID": "med_id", "Description": "med_desc", "Destination": "destination",
        "User": "user_name", "Quantity": "qty"
    }
    df.rename(columns=colmap, inplace=True)
    for col in colmap.values():
        if col not in df.columns: df[col] = None
        
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df.dropna(subset=["dt"], inplace=True)
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["dt"] = df["dt"].astype(str)
    df["pk"] = df.apply(generate_pk, axis=1)
    return df[["pk", "queue_id", "priority", "dt", "med_id", "med_desc", "destination", "user_name", "qty"]]

def clean_schedule_data(df):
    df = df.copy()
    if len(df.columns) > 2:
        df.rename(columns={df.columns[1]: 'Date', df.columns[2]: 'Day'}, inplace=True)
    
    df = df.iloc[1:].dropna(subset=['Date'])
    df.drop(columns=[df.columns[0]], errors='ignore', inplace=True)
    
    long_df = df.melt(id_vars=['Date', 'Day'], var_name='shift_type', value_name='raw_entry')
    long_df.dropna(subset=['raw_entry'], inplace=True)
    long_df = long_df[~long_df['raw_entry'].astype(str).str.lower().isin(['x', 'nan', '', ' '])]
    
    def parse_entry(entry):
        entry = str(entry).strip()
        assignment_type, note = "Shift", ""
        name = entry
        lower = entry.lower()
        
        if 'trn' in lower or 'training' in lower:
            name = re.split(r'\s(?:trn|training)\s', entry, flags=re.IGNORECASE)[0].strip()
            assignment_type = "Training"
            note = entry 
        elif any(x in lower for x in ['pto', 'off', 'sick']):
            assignment_type = "PTO"
        elif 'trade' in lower:
            name = re.sub(r'\(?\s*trade\s*\)?', '', entry, flags=re.IGNORECASE).strip()
            note = entry
        
        return pd.Series([name.title(), assignment_type, note])

    long_df[['staff_name', 'assignment_type', 'note']] = long_df['raw_entry'].apply(parse_entry)
    long_df['dt'] = pd.to_datetime(long_df['Date'], errors='coerce').dt.date
    long_df.dropna(subset=['dt'], inplace=True)
    long_df['day_name'] = long_df['Day']
    long_df["pk"] = long_df.apply(generate_pk, axis=1)
    
    return long_df[['pk', 'dt', 'day_name', 'staff_name', 'shift_type', 'assignment_type', 'raw_entry', 'note']]

# --- DATA LOADERS (CACHED) ---
@st.cache_data(ttl=300)
def load_data(start_date, end_date):
    """Loads all necessary data in parallel queries logic."""
    queries = {
        "events": """
            SELECT e.user_name, e.device, e.med_id, e.med_desc, e.event_type, e.dt, e.qty, 
                   e.discrepancy_qty, e.discrepancy_reason, c.cost_per_unit, e.pk 
            FROM events e LEFT JOIN med_costs c ON e.med_id = c.med_id
            WHERE e.dt::date BETWEEN %s AND %s
        """,
        "config": """
            SELECT dt, user_name, device, med_id, location, action_type, activity_category, 
                   min_qty, max_qty, is_standard 
            FROM config_events WHERE dt::date BETWEEN %s AND %s
        """,
        "pharm": """
            SELECT queue_id, priority, dt, med_id, med_desc, destination, user_name, qty
            FROM pharmacy_orders WHERE dt::date BETWEEN %s AND %s
        """,
        "schedule": """
            SELECT dt, day_name, staff_name, shift_type, assignment_type, note
            FROM staff_schedule WHERE dt BETWEEN %s AND %s
        """
    }
    
    results = {}
    params = (start_date, end_date)
    
    with db_cursor() as (conn, cur):
        for key, sql in queries.items():
            try:
                results[key] = pd.read_sql(sql, conn, params=params)
                if not results[key].empty and 'dt' in results[key].columns:
                    results[key]["dt"] = pd.to_datetime(results[key]["dt"])
            except Exception:
                results[key] = pd.DataFrame()

    # Process Events specifically for Sessions
    df = results["events"]
    if not df.empty:
        df["cost_per_unit"] = df["cost_per_unit"].fillna(0).astype('float32')
        df["qty"] = df["qty"].fillna(0).astype('float32')
        # Filter non-med items
        df = df[~df['med_desc'].astype(str).str.contains(r'Drw|Pkt|Cubic', regex=True, case=False, na=False)]
        
        df.sort_values(['user_name', 'dt'], inplace=True)
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['duration'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        
        # Logic: Machine time is valid if next tx is same device & < 10 mins gap
        df['machine_time_sec'] = np.where(
            (df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['duration'] < 600), 
            df['duration'], 0
        )
        df['is_new_session'] = np.where(
            (df['user_name'] != df['user_name'].shift(1)) | (df['device'] != df['prev_device']), 1, 0
        )
        df['session_id'] = df['is_new_session'].cumsum()
        df.drop(columns=['next_dt', 'is_new_session'], inplace=True, errors='ignore')

    return df, results["config"], results["pharm"], results["schedule"]

def get_stats_range():
    """Lightweight query for sidebar stats."""
    sql = """
        SELECT 
            (SELECT COUNT(*) FROM events),
            (SELECT COUNT(*) FROM pharmacy_orders),
            MIN(dt::date), MAX(dt::date) 
        FROM events
    """
    with db_cursor() as (conn, cur):
        cur.execute(sql)
        row = cur.fetchone()
        if row and row[2] and row[3]:
            return row[0] or 0, row[1] or 0, row[2], row[3]
    return 0, 0, date.today(), date.today()

def get_present_dates(min_dt, max_dt):
    """Get dates with data for calendar visualization."""
    sql = """
        SELECT DISTINCT dt::date FROM events WHERE dt IS NOT NULL
        UNION
        SELECT DISTINCT dt::date FROM pharmacy_orders WHERE dt IS NOT NULL
    """
    df = run_query(sql)
    if not df.empty:
        # Force column to datetime to allow .dt accessor
        col_name = df.columns[0]
        df[col_name] = pd.to_datetime(df[col_name], errors='coerce')
        return set(df[col_name].dt.date.dropna())
    return set()

# --- MAIN APP LOGIC ---
init_db() # Ensure DB structure

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=60)
    st.title("RxTrack v11")
    st.caption("Pharmacy Workflow Intelligence")
    
    rows_events, rows_pharm, min_db, max_db = get_stats_range()
    
    with st.expander("💾 Database Status", expanded=True):
        c1, c2 = st.columns(2)
        c1.metric("Pyxis Events", f"{rows_events:,}")
        c2.metric("Pharm Orders", f"{rows_pharm:,}")
        
        # Mini Calendar Visualization
        present_dates = get_present_dates(min_db, max_db)
        if min_db and max_db and min_db <= max_db:
            delta = (max_db - min_db).days
            cal_start = max_db - timedelta(days=90) if delta > 90 else min_db
            
            cal_html = '<div class="cal-grid">'
            curr = cal_start
            while curr <= max_db:
                color = "cal-present" if curr in present_dates else "cal-missing"
                cal_html += f'<div class="cal-day {color}" title="{curr}"></div>'
                curr += timedelta(days=1)
            cal_html += '</div>'
            st.markdown(cal_html, unsafe_allow_html=True)

    st.divider()
    
    # Date Slider
    default_start = max(min_db, max_db - timedelta(days=7)) if min_db < max_db else min_db
    date_range = st.slider("📅 Analysis Window", 
                           min_value=min_db, max_value=max_db, 
                           value=(default_start, max_db), format="MM/DD/YY")
    start_date, end_date = date_range

    st.divider()
    
    # Upload Section
    st.subheader("📤 Ingest Data")
    u_type = st.selectbox("File Type:", [
        "Daily Transaction Report", "Device Activity Log (Pends)", 
        "Financial Price List", "Pharmacy Workflow Report", "Staff Schedule"
    ])
    uploaded = st.file_uploader(f"Upload {u_type}", type=["csv", "xlsx"])
    
    if uploaded and st.button(f"Process {u_type}"):
        try:
            if u_type == "Staff Schedule":
                raw = pd.read_csv(uploaded, header=0)
                clean = clean_schedule_data(raw)
                sql = "INSERT INTO staff_schedule (pk, dt, day_name, staff_name, shift_type, assignment_type, raw_entry, note) VALUES (%(pk)s, %(dt)s, %(day_name)s, %(staff_name)s, %(shift_type)s, %(assignment_type)s, %(raw_entry)s, %(note)s) ON CONFLICT (pk) DO NOTHING;"
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Schedule")
            else:
                # Header detection logic
                preview = pd.read_excel(uploaded, header=None, nrows=20) if uploaded.name.endswith('.xlsx') else pd.read_csv(uploaded, header=None, nrows=20)
                header_idx = None
                for idx, row in preview.iterrows():
                    s = str(row.values).lower()
                    if u_type == "Daily Transaction Report" and "username" in s and "device" in s: header_idx = idx; break
                    if u_type == "Device Activity Log (Pends)" and "affectedelement" in s: header_idx = idx; break
                    if u_type == "Financial Price List" and "cost" in s: header_idx = idx; break
                    if u_type == "Pharmacy Workflow Report" and "tranqueueid" in s: header_idx = idx; break
                
                if header_idx is None:
                    st.error("❌ Could not detect valid header row.")
                else:
                    uploaded.seek(0)
                    raw = pd.read_excel(uploaded, header=header_idx) if uploaded.name.endswith('.xlsx') else pd.read_csv(uploaded, header=header_idx)
                    
                    if u_type == "Daily Transaction Report":
                        clean = clean_dataframe(raw)
                        sql = "INSERT INTO events (pk, user_name, device, med_id, med_desc, event_type, dt, qty, beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason, resolution_dt) VALUES (%(pk)s, %(user_name)s, %(device)s, %(med_id)s, %(med_desc)s, %(event_type)s, %(dt)s, %(qty)s, %(beginning_qty)s, %(ending_qty)s, %(discrepancy_qty)s, %(discrepancy_reason)s, %(resolution_dt)s) ON CONFLICT (pk) DO NOTHING;"
                        execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Events")
                    elif u_type == "Device Activity Log (Pends)":
                        clean = clean_activity_log(raw)
                        sql = "INSERT INTO config_events (pk, dt, user_name, device, med_id, location, action_type, activity_category, min_qty, max_qty, is_standard) VALUES (%(pk)s, %(dt)s, %(user_name)s, %(device)s, %(med_id)s, %(location)s, %(action_type)s, %(activity_category)s, %(min_qty)s, %(max_qty)s, %(is_standard)s) ON CONFLICT (pk) DO NOTHING;"
                        execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Config")
                    elif u_type == "Pharmacy Workflow Report":
                        clean = clean_pharmacy_report(raw)
                        sql = "INSERT INTO pharmacy_orders (pk, queue_id, priority, dt, med_id, med_desc, destination, user_name, qty) VALUES (%(pk)s, %(queue_id)s, %(priority)s, %(dt)s, %(med_id)s, %(med_desc)s, %(destination)s, %(user_name)s, %(qty)s) ON CONFLICT (pk) DO NOTHING;"
                        execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Pharmacy Orders")
            
            st.cache_data.clear()
            st.rerun()

        except Exception as e:
            st.error(f"Processing Error: {e}")

# --- LOAD DATA ---
df_events, df_config, df_pharm, df_sched = load_data(start_date, end_date)

if df_events.empty and df_config.empty and df_pharm.empty:
    st.info("👋 System Idle. Please upload data via the sidebar to begin analysis.")
    st.stop()

# --- TABS ---
tabs = st.tabs([
    "📊 Overview", "🚀 Process Mining", "🛡️ Compliance", "📥 Pends Analyzer", 
    "🚚 Load/Unload", "⚡ Efficiency", "🔍 Session Explorer", "🏥 Pharmacy Workflow", 
    "🔄 Return Reconciliation", "⚖️ Tech Comparison", "📈 Tech Progression", "📅 Attendance"
])

# 1. OVERVIEW
with tabs[0]:
    if not df_events.empty:
        st.markdown("## 🏥 Executive Summary")
        
        # KPI Calculation
        session_stats = df_events.groupby('session_id').agg(total_time=('machine_time_sec', 'sum'))
        avg_time = session_stats['total_time'].mean()
        real_tx = df_events[~df_events['event_type'].str.contains('verify', case=False, na=False)]
        
        c1, c2, c3, c4 = st.columns(4)
        with c1: st.markdown(f'<div class="metric-card"><h3>{len(real_tx):,}</h3><p>Total Transactions</p></div>', unsafe_allow_html=True)
        with c2: st.markdown(f'<div class="metric-card"><h3>{seconds_to_mmss(avg_time)}</h3><p>Avg Session Duration</p></div>', unsafe_allow_html=True)
        with c3: st.markdown(f'<div class="metric-card"><h3>{df_events["user_name"].nunique()}</h3><p>Active Technicians</p></div>', unsafe_allow_html=True)
        with c4: st.markdown(f'<div class="metric-card"><h3>{df_events["discrepancy_qty"].ne(0).sum()}</h3><p>Discrepancies</p></div>', unsafe_allow_html=True)

        st.markdown("---")
        
        col_main, col_side = st.columns([2, 1])
        with col_main:
            st.subheader("🐢 Slowest Medications (Machine Time)")
            med_speed = df_events[df_events['machine_time_sec'] > 0].groupby('med_desc')['machine_time_sec'].mean().reset_index()
            top_slow = med_speed.sort_values('machine_time_sec', ascending=False).head(10)
            
            fig = px.bar(top_slow, x='machine_time_sec', y='med_desc', orientation='h', 
                         text_auto='.0f', color='machine_time_sec', color_continuous_scale='Reds')
            fig.update_layout(yaxis={'categoryorder':'total ascending', 'title': ''}, xaxis_title="Seconds", showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        with col_side:
            st.subheader("Transaction Types")
            type_counts = df_events['event_type'].value_counts().reset_index()
            fig_pie = px.pie(type_counts, names='event_type', values='count', hole=0.4)
            fig_pie.update_layout(showlegend=False)
            st.plotly_chart(fig_pie, use_container_width=True)

# 2. PROCESS MINING
with tabs[1]:
    if not df_events.empty:
        st.markdown("### 🔄 Workflow Visualization")
        c1, c2, c3 = st.columns(3)
        
        users = sorted(df_events['user_name'].dropna().unique())
        devices = sorted(df_events['device'].dropna().unique())
        
        sel_user = c1.multiselect("Filter User", users)
        sel_device = c2.multiselect("Filter Device", devices)
        
        # Filter Logic
        moves = df_events[df_events['device'] != df_events['prev_device']].dropna(subset=['prev_device', 'device'])
        if sel_user: moves = moves[moves['user_name'].isin(sel_user)]
        if sel_device: moves = moves[moves['device'].isin(sel_device) | moves['prev_device'].isin(sel_device)]

        if not moves.empty:
            path_counts = moves.groupby(['prev_device', 'device']).size().reset_index(name='count')
            path_counts = path_counts.sort_values('count', ascending=False).head(30)
            
            # Sankey Diagram
            all_nodes = list(pd.concat([path_counts['prev_device'], path_counts['device']]).unique())
            node_map = {node: i for i, node in enumerate(all_nodes)}
            
            fig = go.Figure(data=[go.Sankey(
                node=dict(pad=15, thickness=20, line=dict(color="black", width=0.5), label=all_nodes, color="#1E90FF"),
                link=dict(source=path_counts['prev_device'].map(node_map), 
                          target=path_counts['device'].map(node_map), 
                          value=path_counts['count'])
            )])
            fig.update_layout(title_text="Top 30 Workflow Paths", height=600)
            st.plotly_chart(fig, use_container_width=True)
            
            st.divider()
            st.markdown("#### 🔥 Activity Heatmap (Device vs Hour)")
            activity = moves.groupby([moves['dt'].dt.hour.rename('Hour'), 'device']).size().reset_index(name='count')
            fig_heat = px.density_heatmap(activity, x='Hour', y='device', z='count', nbinsx=24, color_continuous_scale='Viridis')
            st.plotly_chart(fig_heat, use_container_width=True)
        else:
            st.info("No movement data found for current selection.")

# 3. COMPLIANCE
with tabs[2]:
    if not df_events.empty:
        disc_df = df_events[df_events['discrepancy_qty'] != 0].copy()
        
        c1, c2 = st.columns(2)
        c1.metric("Count Errors", len(disc_df))
        
        if not disc_df.empty:
            disc_df['abs_variance'] = disc_df['discrepancy_qty'].abs() * disc_df['cost_per_unit']
            total_loss = disc_df['abs_variance'].sum()
            c2.metric("Variance Value (Risk)", f"${total_loss:,.2f}")
            
            st.dataframe(
                disc_df[['dt', 'user_name', 'device', 'med_desc', 'discrepancy_qty', 'discrepancy_reason', 'cost_per_unit', 'abs_variance']],
                use_container_width=True,
                column_config={"abs_variance": st.column_config.NumberColumn("Risk Value", format="$%.2f")}
            )
        else:
            st.success("✅ Zero discrepancies found in this period!")

# 4. PENDS ANALYZER
with tabs[3]:
    st.markdown("### 📥 Inventory Configuration")
    if not df_config.empty:
        c1, c2 = st.columns(2)
        u_filter = c1.multiselect("User", sorted(df_config['user_name'].dropna().unique()), key="pend_u")
        d_filter = c2.multiselect("Device", sorted(df_config['device'].dropna().unique()), key="pend_d")
        
        view = df_config.copy()
        if u_filter: view = view[view['user_name'].isin(u_filter)]
        if d_filter: view = view[view['device'].isin(d_filter)]
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Config Changes", len(view))
        m2.metric("Capacity Added", int(view['max_qty'].sum()))
        m3.metric("Meds Touched", view['med_id'].nunique())
        
        st.dataframe(view, use_container_width=True)
    else:
        st.info("No configuration events found.")

# 5. LOAD/UNLOAD
with tabs[4]:
    if not df_events.empty:
        loads = df_events[df_events['event_type'].str.contains('load|unload', case=False, na=False)]
        st.markdown("### 🚚 Load & Unload Activity")
        st.dataframe(loads[['dt', 'user_name', 'device', 'event_type', 'med_desc', 'qty']], use_container_width=True)
    else:
        st.info("No load/unload events found.")

# 6. EFFICIENCY
with tabs[5]:
    if not df_events.empty:
        st.markdown("### 📉 Inefficient Refills")
        effic = df_events.groupby(['device', 'med_desc']).agg(Trips=('pk', 'count'), Avg_Qty=('qty', 'mean')).reset_index()
        # Logic: Many trips but small quantity added per trip
        inefficient = effic[(effic['Trips'] >= 3) & (effic['Avg_Qty'] < 3)].sort_values('Trips', ascending=False).head(20)
        
        fig = px.bar(inefficient, x='Trips', y='med_desc', orientation='h', 
                     title="High Frequency, Low Yield Refills", color='Trips')
        st.plotly_chart(fig, use_container_width=True)

# 7. SESSION EXPLORER
with tabs[6]:
    if not df_events.empty:
        st.header("🔍 Session Explorer")
        sessions = df_events.groupby('session_id').agg({
            'user_name': 'first', 
            'device': 'first', 
            'dt': ['min', 'max'],
            'machine_time_sec': 'sum'
        }).reset_index()
        
        sessions.columns = ['session_id', 'User', 'Device', 'Start', 'End', 'Active Machine Time']
        sessions['Total Duration'] = (sessions['End'] - sessions['Start']).dt.total_seconds()
        
        # Calculate "Walk / Idle" time (Time not spent actively scanning)
        sessions['Walk / Idle Time'] = sessions['Total Duration'] - sessions['Active Machine Time']
        
        # Formatting for display
        display_sessions = sessions.copy()
        display_sessions['Active Machine Time'] = display_sessions['Active Machine Time'].apply(seconds_to_mmss)
        display_sessions['Walk / Idle Time'] = display_sessions['Walk / Idle Time'].apply(seconds_to_mmss)
        
        c1, c2 = st.columns(2)
        users = sorted(sessions['User'].dropna().unique())
        sel_u = c1.multiselect("User", users, key="sess_u")
        min_sec = c2.number_input("Min Duration (sec)", 0, 3600, 60)
        
        filtered_sess = display_sessions[display_sessions['Total Duration'] > min_sec]
        if sel_u: filtered_sess = filtered_sess[filtered_sess['User'].isin(sel_u)]
        
        st.dataframe(filtered_sess, use_container_width=True)
        
        if not filtered_sess.empty:
            sel_id = st.selectbox("Drill into Session ID", filtered_sess['session_id'].unique())
            details = df_events[df_events['session_id'] == sel_id]
            st.write(f"**Session Timeline:** {len(details)} transactions")
            st.dataframe(details[['dt', 'event_type', 'med_desc', 'qty', 'machine_time_sec']], use_container_width=True)

# 8. PHARMACY WORKFLOW
with tabs[7]:
    if not df_pharm.empty:
        st.markdown("### 🏥 Central Pharmacy Workflow")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Orders", len(df_pharm))
        c2.metric("Critical/STAT", len(df_pharm[df_pharm['priority'].str.contains('STAT|Critical', case=False, na=False)]))
        c3.metric("Top Destination", df_pharm['destination'].mode()[0] if not df_pharm.empty else "-")
        
        c_chart1, c_chart2 = st.columns(2)
        with c_chart1:
            prio = df_pharm['priority'].value_counts().reset_index()
            st.plotly_chart(px.pie(prio, names='priority', values='count', hole=0.4, title="Order Priority"), use_container_width=True)
        with c_chart2:
            dest = df_pharm[df_pharm['destination'] != 'Carousel Workflow']['destination'].value_counts().head(10).reset_index()
            st.plotly_chart(px.bar(dest, x='count', y='destination', orientation='h', title="Top Destinations"), use_container_width=True)
            
        st.dataframe(df_pharm, use_container_width=True)

# 9. RECONCILIATION
with tabs[8]:
    st.markdown("### 🔄 Unload vs. Return Reconciliation")
    filter_narc = st.checkbox("Exclude Controlled Substances", value=True)
    
    if not df_events.empty and not df_pharm.empty:
        unloads = df_events[df_events['event_type'].str.contains(r'unload|empty\s*return', case=False, na=False)].copy()
        returns = df_pharm[df_pharm['priority'] == 'Returns'].copy()
        
        if filter_narc:
            pat = '|'.join(NARC_TERMS)
            unloads = unloads[~unloads['med_desc'].str.contains(pat, case=False, na=False)]
            returns = returns[~returns['med_desc'].str.contains(pat, case=False, na=False)]
            
        # Aggregate
        unloads['Date'] = unloads['dt'].dt.date
        unloads['med_clean'] = unloads['med_id'].str.strip().str.upper()
        grp_unload = unloads.groupby(['Date', 'med_clean']).agg({'qty': 'sum', 'med_desc': 'first'}).reset_index()
        
        returns['Date'] = returns['dt'].dt.date
        returns['med_clean'] = returns['med_id'].str.strip().str.upper()
        grp_return = returns.groupby(['Date', 'med_clean']).agg({'qty': 'sum'}).reset_index()
        
        merged = pd.merge(grp_unload, grp_return, on=['Date', 'med_clean'], how='outer', suffixes=('_floor', '_pharm'))
        merged.fillna(0, inplace=True)
        merged['Variance'] = merged['qty_pharm'] - merged['qty_floor']
        
        def status(x):
            if x == 0: return "✅ Match"
            if x < 0: return "❌ Missing in Pharm"
            return "❓ Extra Returned"
            
        merged['Status'] = merged['Variance'].apply(status)
        st.dataframe(merged, use_container_width=True)

# 10. TECH COMPARISON
with tabs[9]:
    st.markdown("### ⚖️ Head-to-Head Comparison")
    if not df_events.empty:
        users = sorted(df_events['user_name'].dropna().unique())
        c1, c2 = st.columns(2)
        u1 = c1.selectbox("User A", users, key="u1")
        u2 = c2.selectbox("User B", users, index=1 if len(users)>1 else 0, key="u2")
        
        # Helper to calc shift metrics
        def get_metrics(user):
            sub = df_events[df_events['user_name'] == user]
            if sub.empty: return 0, 0, 0
            
            valid = sub[~sub['event_type'].str.contains('verify', case=False)]
            tx_count = len(valid)
            avg_speed = valid[valid['machine_time_sec'] > 0]['machine_time_sec'].mean()
            
            hours = (sub['dt'].max() - sub['dt'].min()).total_seconds() / 3600
            rate = tx_count / max(hours, 0.5) # Avoid div/0
            return tx_count, avg_speed, rate
            
        m_a = get_metrics(u1)
        m_b = get_metrics(u2)
        
        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader(u1)
            st.metric("Transactions", m_a[0])
            st.metric("Tx / Hour", f"{m_a[2]:.1f}")
            st.metric("Avg Speed (sec)", f"{m_a[1]:.1f}s")
        with col_b:
            st.subheader(u2)
            st.metric("Transactions", m_b[0], delta=m_b[0]-m_a[0])
            st.metric("Tx / Hour", f"{m_b[2]:.1f}", delta=f"{m_b[2]-m_a[2]:.1f}")
            st.metric("Avg Speed (sec)", f"{m_b[1]:.1f}s", delta=f"{m_b[1]-m_a[1]:.1f}s", delta_color="inverse")

# 11. PROGRESSION
with tabs[10]:
    st.markdown("### 📈 Performance Trend")
    if not df_events.empty:
        c1, c2 = st.columns(2)
        u_sel = c1.selectbox("Select Tech", sorted(df_events['user_name'].dropna().unique()), key="prog_u")
        freq = c2.selectbox("Frequency", ["Daily", "Weekly", "Monthly"], key="prog_f")
        freq_map = {"Daily": "D", "Weekly": "W", "Monthly": "M"}
        
        udf = df_events[df_events['user_name'] == u_sel].copy()
        if not udf.empty:
            udf.set_index('dt', inplace=True)
            
            # Resample Logic
            res = udf.resample(freq_map[freq]).agg({
                'pk': 'count',
                'discrepancy_qty': lambda x: (x != 0).sum(),
                'machine_time_sec': 'mean'
            }).rename(columns={'pk': 'Tx', 'discrepancy_qty': 'Errors', 'machine_time_sec': 'Speed'})
            
            # Active Hours approx
            def calc_hrs(x):
                if len(x) < 2: return 0.5
                return (x.index.max() - x.index.min()).total_seconds() / 3600
            
            hrs = udf['pk'].resample(freq_map[freq]).apply(calc_hrs)
            res['Tx_Per_Hour'] = res['Tx'] / hrs.replace(0, 1)
            
            st.plotly_chart(px.line(res, y='Tx_Per_Hour', title="Productivity (Tx/Hr)", markers=True), use_container_width=True)
            st.plotly_chart(px.line(res, y='Speed', title="Speed (Sec/Tx)", markers=True), use_container_width=True)

# 12. ATTENDANCE
with tabs[11]:
    st.markdown("### 📋 Schedule Reconciliation")
    if not df_sched.empty and not df_events.empty:
        # 1. Normalize Names & Dates
        df_events['match_key'] = df_events['user_name'].apply(normalize_name)
        df_pharm['match_key'] = df_pharm['user_name'].apply(normalize_name)
        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)
        
        # 2. Build Worked Days Set (From both systems)
        worked = pd.concat([
            df_events[['dt', 'match_key', 'user_name']],
            df_pharm[['dt', 'match_key', 'user_name']]
        ])
        
        # Use named aggregation to avoid 'dt' column collision during reset_index
        worked_agg = worked.groupby([worked['dt'].dt.date.rename('date_obj'), 'match_key']).agg(
            actual_name=('user_name', 'first'),
            tx_count=('dt', 'count')
        ).reset_index()
        
        # 3. Merge with Schedule
        df_sched['date_obj'] = df_sched['dt'].dt.date
        merged = pd.merge(df_sched, worked_agg, on=['date_obj', 'match_key'], how='outer')
        
        # 4. Status Logic
        def get_status(row):
            shift = str(row['shift_type']).upper()
            tx = row['tx_count'] if pd.notnull(row['tx_count']) else 0
            
            if row['assignment_type'] == 'PTO' or 'PTO' in shift: return "🌴 PTO"
            if 'IV' in shift: return "✅ Present (IV)" if tx > 0 else "💉 IV Shift (Not Tracked)"
            if pd.isna(row['shift_type']) and tx > 0: return "➕ Unscheduled Pick-up"
            if tx > 0: return "✅ Present"
            return "❌ No Show / No Login"

        merged['Status'] = merged.apply(get_status, axis=1)
        merged['display_name'] = merged['actual_name'].fillna(merged['staff_name']).fillna("Unknown")
        
        # Filter Admins
        merged = merged[~merged['display_name'].str.lower().isin(ADMIN_USERS)]
        
        # Metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("Scheduled", len(merged[merged['shift_type'].notna()]))
        m2.metric("Present", len(merged[merged['Status'].str.contains("Present")]))
        m3.metric("No Shows", len(merged[merged['Status'].str.contains("No Show")]))
        
        # Main Table
        st.dataframe(
            merged[['date_obj', 'display_name', 'shift_type', 'Status', 'note']].sort_values('date_obj', ascending=False),
            use_container_width=True,
            column_config={"date_obj": st.column_config.DateColumn("Date")}
        )
    else:
        st.warning("Requires both Schedule and Event data to run reconciliation.")
