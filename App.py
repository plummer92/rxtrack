###############################################################
# RXTRACK: EXECUTIVE DASHBOARD (v13.6 - SQLAlchemy & Stability)
# Architecture: Quad-Table Strategy + Attendance + Pricing
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
# Replace with your actual Neon/PostgreSQL connection string
DB_URL = "postgresql://neondb_owner:npg_2ZRmDGgU9Vzb@ep-orange-frost-ad1fturl-pooler.c-2.us-east-1.aws.neon.tech/neondb?" 
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
NARC_TERMS = [
    "OXYCODONE", "MORPHINE", "FENTANYL", "HYDROMORPHONE", "HYDROCODONE", 
    "LORAZEPAM", "MIDAZOLAM", "DIAZEPAM", "ALPRAZOLAM", "CODEINE", 
    "METHADONE", "KETAMINE", "TRAMADOL", "ZOLPIDEM", "PHENOBARBITAL", 
    "BUPRENORPHINE", "LACOSAMIDE", "VIMPAT", "PREGABALIN", "LYRICA", 
    "CHLORDIAZEPOXIDE", "LIBRIUM", "CLONAZEPAM", "KLONOPIN"
]
ADMIN_USERS = ['emily', 'joe', 'krista']
NAME_MAPPINGS = {
    "phi": "ali", "ho": "ali", "rebekah": "bekah",
    "nugent": "kathy", "kathleen": "kathy",
    "spain": "dee", "deloris": "dee",
    "jabusch": "dan", "daniel": "dan",
    "nicholas": "nick"     
}
AMBIGUOUS_NAMES = ["melissa", "emily", "sarah", "megan", "erin", "kyle", "jessica", "andy", "heather", "michelle", "taylor"]

# --- DATABASE HELPERS ---
@contextlib.contextmanager
def db_cursor():
    """Context manager for INSERT/UPDATE batch operations using raw psycopg2."""
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

def execute_statement(sql, params, batch=False, table_name="Data"):
    """Executes INSERT/UPDATE statements."""
    try:
        with db_cursor() as (conn, cur):
            if batch:
                execute_batch(cur, sql, params, page_size=2000)
            else:
                cur.execute(sql, params)
            conn.commit()
            st.toast(f"✅ Processed {len(params)} records for {table_name}!", icon="💾")
    except Exception as e:
        st.error(f"⚠️ Error executing {table_name}: {e}")

def run_query(query, params=None):
    """Executes a SELECT query using the SQLAlchemy engine to avoid warnings."""
    try:
        return pd.read_sql(query, engine, params=params)
    except Exception as e:
        st.error(f"Query Error: {e}")
        return pd.DataFrame()

# --- UTILITY FUNCTIONS ---
def normalize_name(full_name):
    """Robust name normalization to prevent 'string index out of range' errors."""
    if not full_name or pd.isna(full_name): 
        return "unknown"
    s = str(full_name).strip().lower()
    if not s or s == ",": 
        return "unknown"
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

# --- DATA LOADERS (SQLAlchemy Powered) ---
@st.cache_data(ttl=300)
def load_data(start_date, end_date):
    """Loads all core dataframes using the shared SQLAlchemy engine."""
    queries = {
        "events": "SELECT e.*, c.cost_per_unit FROM events e LEFT JOIN med_costs c ON e.med_id = c.med_id WHERE e.dt::date BETWEEN %s AND %s",
        "config": "SELECT * FROM config_events WHERE dt::date BETWEEN %s AND %s",
        "pharm": "SELECT * FROM pharmacy_orders WHERE dt::date BETWEEN %s AND %s",
        "schedule": "SELECT * FROM staff_schedule WHERE dt BETWEEN %s AND %s",
        "attendance": "SELECT * FROM attendance_punches WHERE dt_date BETWEEN %s AND %s"
    }
    
    results = {}
    for key, sql in queries.items():
        results[key] = run_query(sql, params=(start_date, end_date))
        if not results[key].empty and 'dt' in results[key].columns:
            results[key]["dt"] = pd.to_datetime(results[key]["dt"])

    # Process Events with Copy to avoid SettingWithCopyWarning
    df = results["events"].copy()
    if not df.empty:
        df["cost_per_unit"] = pd.to_numeric(df["cost_per_unit"], errors='coerce').fillna(0).astype('float32')
        df["qty"] = pd.to_numeric(df["qty"], errors='coerce').fillna(0).astype('float32')
        df = df[~df['med_desc'].astype(str).str.contains(r'Drw|Pkt|Cubic', case=False, na=False)].copy()
        
        df.sort_values(['user_name', 'dt'], inplace=True)
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['duration'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        df['gap_prev'] = (df['dt'] - df.groupby('user_name')['dt'].shift(1)).dt.total_seconds().fillna(0)
        
        # Calculate machine time
        df['machine_time_sec'] = np.where((df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['duration'] < 600), df['duration'], 0)
        df['is_new_session'] = np.where((df['user_name'] != df['user_name'].shift(1)) | (df['device'] != df['prev_device']) | (df['gap_prev'] > 1200), 1, 0)
        df['session_id'] = df['is_new_session'].cumsum()

    pharm = results["pharm"].copy()
    if not pharm.empty:
        pharm = pharm[~pharm['destination'].astype(str).str.contains('BATCH PICK', case=False, na=False)].copy()

    return df, results["config"], pharm, results["schedule"], results["attendance"]

# --- PAGE ROUTING ---
PAGES = ["📊 Overview", "🎓 Student Project", "🏆 Shift Leaderboard", "🚀 Process Mining", "🛡️ Compliance", "🏥 Pharmacy Workflow", "📅 Attendance"]

with st.sidebar:
    st.title("RxTrack v13.6")
    selected_page = st.radio("Navigation", PAGES)
    
    # Persistent Date Filters
    st.divider()
    st.subheader("📅 Analysis Window")
    # Date logic simplified for brevity
    start_date = st.date_input("Start Date", value=date.today() - timedelta(days=14))
    end_date = st.date_input("End Date", value=date.today())

# --- EXECUTE DATA LOAD ---
df_events, df_config, df_pharm, df_sched, df_att = load_data(start_date, end_date)

# --- PAGE CONTENT ---
if selected_page == "📊 Overview":
    st.header("📊 Executive Summary")
    if not df_events.empty:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Transactions", f"{len(df_events):,}")
        c2.metric("Active Techs", df_events['user_name'].nunique())
        c3.metric("Discrepancies", int(df_events['discrepancy_qty'].ne(0).sum()))
        
        # Use width='stretch' for modern Streamlit compatibility
        st.subheader("💊 Top Medications by Volume")
        top_meds = df_events['med_desc'].value_counts().head(10).reset_index()
        fig = px.bar(top_meds, x='count', y='med_desc', orientation='h', color='count')
        st.plotly_chart(fig, width='stretch')

elif selected_page == "🏥 Pharmacy Workflow":
    st.header("🏥 Pharmacy Workflow")
    st.info("Direct access to Carousel and Pyxis replenishment data.")
    if not df_pharm.empty:
        st.dataframe(df_pharm, width='stretch')

# ... (Additional pages would follow same logic using width='stretch') ...
