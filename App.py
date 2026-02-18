###############################################################
# RXTRACK: EXECUTIVE DASHBOARD (v13.4 - Stability Fixes)
# Architecture: Quad-Table Strategy + Attendance + Pricing
# Updates:
#   1. Fixed Sidebar Indentation & Duplicate Logic.
#   2. Implemented Day/Week/Range Date Filters.
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
import io
import warnings

from sqlalchemy import create_engine

# 1. Define the engine at the top level of App.py
# Replace 'your_db_url' with your actual Neon/PostgreSQL connection string
DB_URL = "postgresql://neondb_owner:npg_2ZRmDGgU9Vzb@ep-orange-frost-ad1fturl-pooler.c-2.us-east-1.aws.neon.tech/neondb?" 
engine = create_engine(DB_URL)

# --- CONFIGURATION ---
st.set_page_config(
    page_title="RxTrack: Workforce & Efficiency", 
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Suppress DB/Pandas warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pandas")

# --- INITIALIZE VARIABLES (Prevents NameError) ---
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

# Nickname Mappings
NAME_MAPPINGS = {
    "phi": "ali", "ho": "ali", "rebekah": "bekah",
    "nugent": "kathy", "kathleen": "kathy",
    "spain": "dee", "deloris": "dee",
    "jabusch": "dan", "daniel": "dan",
    "nicholas": "nick"     
}

AMBIGUOUS_NAMES = [
    "melissa", "emily", "sarah", "megan", "erin", "kyle", 
    "jessica", "andy", "heather", "michelle", "taylor"
]

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
        );""",
        """CREATE TABLE IF NOT EXISTS attendance_punches (
            pk TEXT PRIMARY KEY, raw_name TEXT, dt_date DATE, start_dt TIMESTAMP, end_dt TIMESTAMP
        );""",
        """CREATE TABLE IF NOT EXISTS inventory_audit (
            pk TEXT PRIMARY KEY, med_id TEXT, med_desc TEXT, med_class TEXT, 
            unit_cost FLOAT, qty_on_hand FLOAT, min_lvl FLOAT, max_lvl FLOAT
        );""",
        """CREATE TABLE IF NOT EXISTS inventory_detailed (
            pk TEXT PRIMARY KEY, station TEXT, med_id TEXT, med_desc TEXT, 
            unit_cost FLOAT, current_count FLOAT, pocket_location TEXT
        );"""
    ]
    with db_cursor() as (conn, cur):
        for sql in schemas:
            cur.execute(sql)
        conn.commit()

def run_query(query, params=None):
    """Executes a SELECT query and returns a pandas DataFrame."""
    try:
        with db_cursor() as (conn, cur):
            return pd.read_sql(query, conn, params=params)
    except Exception:
        return pd.DataFrame()

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
    # Handle nulls or non-strings
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
            # Split and check if first name part actually exists
            first_name_parts = parts[1].strip().split(" ")
            if first_name_parts and len(first_name_parts[0]) > 0:
                first_name = first_name_parts[0]
            
            # Prevent 'index out of range' by checking string length
            if len(last_name_part) > 0: 
                last_initial = last_name_part[0]
    else:
        parts = s.split(" ")
        if len(parts) > 0:
            first_name = parts[0]
            # Verify second part exists and has at least one character
            if len(parts) > 1 and len(parts[1]) > 0: 
                last_initial = parts[1][0]
    
    for key, val in NAME_MAPPINGS.items():
        if key in first_name: 
            first_name = val
            break
            
    if first_name in AMBIGUOUS_NAMES and last_initial:
        return f"{first_name} {last_initial}"
    return first_name

def parse_shift_start(date_obj, shift_str):
    if not shift_str or pd.isna(shift_str): return None
    s = str(shift_str).lower().strip()
    m_time = re.search(r'(\d{1,2}):(\d{2})', s)
    if m_time:
        h, m = int(m_time.group(1)), int(m_time.group(2))
        if 'p' in s and h < 12: h += 12
        if 'a' in s and h == 12: h = 0
        try: return pd.to_datetime(f"{date_obj} {h:02d}:{m:02d}")
        except: return None
    m_ampm = re.search(r'(\d{1,2})\s*([ap])', s)
    if m_ampm:
        h = int(m_ampm.group(1))
        ampm = m_ampm.group(2)
        if ampm == 'p' and h < 12: h += 12
        if ampm == 'a' and h == 12: h = 0
        try: return pd.to_datetime(f"{date_obj} {h:02d}:00")
        except: return None
    m_mil = re.search(r'(\d{4})', s)
    if m_mil:
        val = int(m_mil.group(1))
        if 0 <= val <= 2400:
            h, m = divmod(val, 100)
            try: return pd.to_datetime(f"{date_obj} {h:02d}:{m:02d}")
            except: return None
    return None

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
    pattern_element = r'^(.*?) \((.*?)\)'
    extracted = df['raw_element'].astype(str).str.extract(pattern_element)
    df['location'] = extracted[0].str.strip()
    df['med_id'] = extracted[1].str.strip()
    df.dropna(subset=['med_id'], inplace=True)
    if 'qty_col' not in df.columns:
        pattern_qty = r':\s*(\d+)$' 
        df['qty_col'] = df['raw_element'].astype(str).str.extract(pattern_qty)[0]
    df['qty_extracted'] = pd.to_numeric(df['qty_col'], errors='coerce')
    df.sort_values(['user_name', 'device', 'med_id', 'dt'], inplace=True)
    df['time_gap'] = df.groupby(['user_name', 'device', 'med_id'])['dt'].diff().dt.total_seconds().fillna(999)
    df['group_id'] = (df['time_gap'] > 120).astype(int).cumsum()
    df['is_min'] = df['activity_category'].str.contains('Min', case=False, na=False)
    df['is_max'] = df['activity_category'].str.contains('Max', case=False, na=False)
    df['is_std'] = df['activity_category'].str.contains('Standard Stock', case=False, na=False)
    df['min_qty'] = np.where(df['is_min'], df['qty_extracted'], np.nan)
    df['max_qty'] = np.where(df['is_max'], df['qty_extracted'], np.nan)
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
    long_df = df.melt(id_vars=['Date', 'Day'], var_name='col_header', value_name='raw_entry')
    long_df.dropna(subset=['raw_entry'], inplace=True)
    long_df = long_df[~long_df['raw_entry'].astype(str).str.lower().isin(['x', 'nan', '', ' '])]
    
    processed_rows = []
    for _, row in long_df.iterrows():
        raw = str(row['raw_entry']).strip()
        header = str(row['col_header']).strip()
        dt = pd.to_datetime(row['Date'], errors='coerce').date()
        day_name = row['Day']
        
        if re.search(r'\(\d', raw): 
            parts = [p.strip() + ')' for p in raw.split(')') if '(' in p]
        else:
            parts = [p.strip() for p in raw.split('\n') if p.strip()]
        
        for part in parts:
            if not part or part == ')': continue
            override_time = None
            m_range = re.search(r'\(?(\d{4})\s*-\s*\d{4}\)?', part)
            if m_range:
                override_time = m_range.group(1)
                clean_part = part.replace(m_range.group(0), '')
            else:
                m_single = re.search(r'\((\d{4})\)', part)
                if m_single:
                    override_time = m_single.group(1)
                    clean_part = part.replace(m_single.group(0), '')
                else:
                    m_short = re.search(r'\((\d{1,2})\s*-\s*\d{1,2}\)', part)
                    if m_short:
                        override_time = m_short.group(1)
                        clean_part = part.replace(m_short.group(0), '')
                    else:
                        clean_part = part
            clean_part = clean_part.replace('()', '').strip()
            if clean_part.endswith(','): clean_part = clean_part[:-1]
            assignment_type = "Shift"
            note = ""
            lower_part = clean_part.lower()
            if 'trn' in lower_part or 'training' in lower_part:
                assignment_type = "Training"
                clean_part = re.split(r'\s(?:trn|training)\s?', clean_part, flags=re.IGNORECASE)[0].strip()
            elif any(x in lower_part for x in ['pto', 'off', 'sick']):
                assignment_type = "PTO"
            final_shift_str = override_time if override_time else header
            row_str = f"{dt}|{clean_part}|{final_shift_str}"
            pk = hashlib.sha256(row_str.encode()).hexdigest()
            processed_rows.append({'pk': pk, 'dt': dt, 'day_name': day_name, 'staff_name': clean_part.title(), 'shift_type': final_shift_str, 'assignment_type': assignment_type, 'raw_entry': part, 'note': note})
    return pd.DataFrame(processed_rows)

def clean_attendance_file(file_obj):
    file_obj.seek(0)
    content = file_obj.read().decode('utf-8', errors='ignore')
    lines = content.splitlines()
    data = []
    name_pat = re.compile(r'Employee:\s*([A-Za-z\-,\s\.]+?)(?="|",|",Date)')
    date_pat = re.compile(r'Date:\s*(\d{1,2}/\d{1,2}/\d{4})')
    time_pat = re.compile(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2})')
    for line in lines:
        if "Employee:" not in line or "Date:" not in line: continue
        m_name = name_pat.search(line)
        name = m_name.group(1).strip() if m_name else None
        m_date = date_pat.search(line)
        date_str = m_date.group(1) if m_date else None
        times = time_pat.findall(line)
        start_time = times[0] if len(times) > 0 else None
        end_time = times[1] if len(times) > 1 else None
        if name and date_str and start_time:
            data.append({"raw_name": name, "dt_date": pd.to_datetime(date_str).date(), "start_dt": start_time, "end_dt": end_time})
    df = pd.DataFrame(data)
    if not df.empty: df["pk"] = df.apply(generate_pk, axis=1)
    return df

def clean_inventory_file(df):
    df = df.copy()
    colmap = {"MedID": "med_id", "MedDescription": "med_desc", "MedClass": "med_class", "UnitCost": "unit_cost", "CurrentCount": "qty_on_hand", "CurrentMin": "min_lvl", "CurrentMax": "max_lvl"}
    df.rename(columns=colmap, inplace=True)
    for c in ["med_id", "med_desc", "unit_cost", "qty_on_hand", "min_lvl", "max_lvl"]:
        if c not in df.columns: df[c] = None
    if df['unit_cost'].dtype == object:
        df['unit_cost'] = df['unit_cost'].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False)
    df['unit_cost'] = pd.to_numeric(df['unit_cost'], errors='coerce').fillna(0)
    df['qty_on_hand'] = pd.to_numeric(df['qty_on_hand'], errors='coerce').fillna(0)
    df['pk'] = df.apply(lambda x: str(x['med_id']), axis=1)
    return df[['pk', 'med_id', 'med_desc', 'med_class', 'unit_cost', 'qty_on_hand', 'min_lvl', 'max_lvl']]

def clean_detailed_inventory(df):
    df = df.copy()
    colmap = {
        "StationName": "station", 
        "SourceSystem": "source_system",
        "MedID": "med_id", 
        "MedDescription": "med_desc", 
        "UnitCost": "unit_cost", 
        "CurrentCount": "current_count", 
        "DrawerSubdrawerPocket": "pocket_location"
    }
    df.rename(columns=colmap, inplace=True)
    required = ["station", "source_system", "med_id", "med_desc", "unit_cost", "current_count", "pocket_location"]
    for c in required:
        if c not in df.columns: df[c] = None
    if df['unit_cost'].dtype == object:
        df['unit_cost'] = df['unit_cost'].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False)
    df['unit_cost'] = pd.to_numeric(df['unit_cost'], errors='coerce').fillna(0)
    df['current_count'] = pd.to_numeric(df['current_count'], errors='coerce').fillna(0)
    df['row_sig'] = df['station'].astype(str) + df['med_id'].astype(str) + df['pocket_location'].astype(str)
    df['pk'] = df['row_sig'].apply(lambda x: hashlib.sha256(x.encode()).hexdigest())
    return df[required + ['pk']]

# --- DATA LOADERS (CACHED) ---
@st.cache_data(ttl=300)
def load_data(start_date, end_date):
    queries = {
        "events": """
            SELECT e.user_name, e.device, e.med_id, e.med_desc, e.event_type, e.dt, e.qty, 
                   e.discrepancy_qty, e.discrepancy_reason, c.cost_per_unit, e.pk 
            FROM events e LEFT JOIN med_costs c ON e.med_id = c.med_id
            WHERE e.dt::date BETWEEN %s AND %s
        """,
        "config": """
            SELECT pk, dt, user_name, device, med_id, location, action_type, activity_category, 
                   min_qty, max_qty, is_standard 
            FROM config_events WHERE dt::date BETWEEN %s AND %s
        """,
        "pharm": """
            SELECT pk, queue_id, priority, dt, med_id, med_desc, destination, user_name, qty
            FROM pharmacy_orders WHERE dt::date BETWEEN %s AND %s
        """,
        "schedule": """
            SELECT pk, dt, day_name, staff_name, shift_type, assignment_type, note
            FROM staff_schedule WHERE dt BETWEEN %s AND %s
        """,
        "attendance": """
            SELECT pk, raw_name, dt_date, start_dt, end_dt
            FROM attendance_punches WHERE dt_date BETWEEN %s AND %s
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

    df = results["events"]
    if not df.empty:
        df["cost_per_unit"] = df["cost_per_unit"].fillna(0).astype('float32')
        df["qty"] = df["qty"].fillna(0).astype('float32')
        df = df[~df['med_desc'].astype(str).str.contains(r'Drw|Pkt|Cubic', regex=True, case=False, na=False)]
        df.sort_values(['user_name', 'dt'], inplace=True)
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['duration'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        df['gap_prev'] = (df['dt'] - df.groupby('user_name')['dt'].shift(1)).dt.total_seconds().fillna(0)
        df['machine_time_sec'] = np.where((df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['duration'] < 600), df['duration'], 0)
        df['is_new_session'] = np.where((df['user_name'] != df['user_name'].shift(1)) | (df['device'] != df['prev_device']) | (df['gap_prev'] > 1200), 1, 0)
        df['session_id'] = df['is_new_session'].cumsum()
        df.drop(columns=['next_dt', 'is_new_session', 'gap_prev'], inplace=True, errors='ignore')

    if not results["pharm"].empty:
        results["pharm"] = results["pharm"][~results["pharm"]['destination'].astype(str).str.contains('BATCH PICK', case=False, na=False)]

    return df, results["config"], results["pharm"], results["schedule"], results["attendance"]

def get_stats_range():
    sql = """
        WITH all_dates AS (
            SELECT dt::date as d FROM events WHERE dt IS NOT NULL
            UNION ALL
            SELECT dt::date as d FROM pharmacy_orders WHERE dt IS NOT NULL
            UNION ALL
            SELECT dt as d FROM staff_schedule WHERE dt IS NOT NULL
            UNION ALL
            SELECT dt_date as d FROM attendance_punches WHERE dt_date IS NOT NULL
        )
        SELECT 
            (SELECT COUNT(*) FROM events),
            (SELECT COUNT(*) FROM pharmacy_orders),
            (SELECT COUNT(*) FROM staff_schedule),
            (SELECT COUNT(*) FROM attendance_punches),
            MIN(d), MAX(d) 
        FROM all_dates
    """
    with db_cursor() as (conn, cur):
        cur.execute(sql)
        row = cur.fetchone()
        if row and row[4] and row[5]:
            return (row[0] or 0), (row[1] or 0), (row[2] or 0), (row[3] or 0), row[4], row[5]
    return 0, 0, 0, 0, date.today(), date.today()

def get_present_dates(min_dt, max_dt):
    sql = """
        SELECT DISTINCT dt::date FROM events WHERE dt IS NOT NULL
        UNION
        SELECT DISTINCT dt::date FROM pharmacy_orders WHERE dt IS NOT NULL
    """
    df = run_query(sql)
    if not df.empty:
        col_name = df.columns[0]
        df[col_name] = pd.to_datetime(df[col_name], errors='coerce')
        return set(df[col_name].dt.date.dropna())
    return set()

# --- MAIN APP LOGIC ---
init_db()

# 1. Define your internal pages (those not yet moved to the /pages folder)
PAGES = [
    "📊 Overview", "🎓 Student Project", "🏆 Shift Leaderboard", "🚀 Process Mining", "🛡️ Compliance", "📥 Pends Analyzer", "🚚 Load/Unload", "⚡ Efficiency", "🏥 Pharmacy Workflow", "🔄 Return Reconciliation", "⚖️ Tech Comparison", 
    "📈 Tech Progression", "📅 Attendance"
]

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=60)
    st.title("RxTrack v13.4")
    st.caption("Pharmacy Workflow Intelligence")
    
    st.markdown("### 🧭 Navigation")
    selected_page = st.radio("Go to:", PAGES, label_visibility="collapsed")
    st.divider()
    
    # Get database stats for the sidebar metrics
    n_events, n_pharm, n_sched, n_att, min_db, max_db = get_stats_range()

    # --- PERSISTENT DATE LOGIC (Anchored in Session State) ---
    if 'start_date' not in st.session_state:
        st.session_state.start_date = max_db - timedelta(days=14)
    if 'end_date' not in st.session_state:
        st.session_state.end_date = max_db

    st.markdown("### 📅 Analysis Window")
    filter_mode = st.radio("Filter Mode", ["Range", "Week", "Day"], horizontal=True, label_visibility="collapsed", key="sidebar_filter")

    if filter_mode == "Range":
        date_range = st.slider(
            "Select Range:", 
            min_value=min_db, 
            max_value=max_db, 
            value=(st.session_state.start_date, st.session_state.end_date), 
            format="MM/DD/YY"
        )
        st.session_state.start_date, st.session_state.end_date = date_range
    
    elif filter_mode == "Week":
        week_start = st.date_input("Select Week Start:", value=st.session_state.start_date, min_value=min_db, max_value=max_db)
        st.session_state.start_date = week_start
        st.session_state.end_date = week_start + timedelta(days=6)
    
    else: # Day Mode
        single_day = st.date_input("Select Day:", value=st.session_state.start_date, min_value=min_db, max_value=max_db)
        st.session_state.start_date = single_day
        st.session_state.end_date = single_day

    # Finalize variables for data loading
    start_date, end_date = st.session_state.start_date, st.session_state.end_date

    # --- DATABASE STATUS (Expander) ---
    with st.expander("💾 Database Status", expanded=False):
        c1, c2 = st.columns(2)
        c1.metric("Pyxis Events", f"{n_events:,}")
        c2.metric("Pharm Orders", f"{n_pharm:,}")
        c3, c4 = st.columns(2)
        c3.metric("Sched. Shifts", f"{n_sched:,}")
        c4.metric("Time Punches", f"{n_att:,}")
        
        # Heatmap Calendar Grid
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
    
    # --- UNIVERSAL DATA INGEST ---
    st.subheader("📤 Ingest Data")
    u_type = st.selectbox("File Type:", [
        "Daily Transaction Report", "Device Activity Log (Pends)", 
        "Inventory Audit (Prices)", "Inventory Audit (Detailed RC)", "Staff Schedule", "Attendance Tracking"
    ])
    uploaded = st.file_uploader(f"Upload {u_type}", type=["csv", "xlsx"])
    if uploaded and st.button(f"Process {u_type}"):
        try:
            # 1. Load raw file
            if uploaded.name.endswith('.xlsx'):
                raw = pd.read_excel(uploaded)
            else:
                try:
                    raw = pd.read_csv(uploaded)
                except UnicodeDecodeError:
                    uploaded.seek(0)
                    raw = pd.read_csv(uploaded, encoding='latin1')

            # 2. Route to correct SQL Table
            clean = None 

            if u_type == "Daily Transaction Report":
                clean = clean_dataframe(raw)
                sql = """INSERT INTO events (pk, user_name, device, med_id, med_desc, event_type, dt, qty, 
                         beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason, resolution_dt) 
                         VALUES (%(pk)s, %(user_name)s, %(device)s, %(med_id)s, %(med_desc)s, %(event_type)s, 
                         %(dt)s, %(qty)s, %(beginning_qty)s, %(ending_qty)s, %(discrepancy_qty)s, 
                         %(discrepancy_reason)s, %(resolution_dt)s) ON CONFLICT (pk) DO NOTHING;"""
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Events")

            elif u_type == "Device Activity Log (Pends)":
                clean = clean_activity_log(raw)
                sql = """INSERT INTO config_events (pk, dt, user_name, device, med_id, location, 
                         action_type, activity_category, min_qty, max_qty, is_standard) 
                         VALUES (%(pk)s, %(dt)s, %(user_name)s, %(device)s, %(med_id)s, %(location)s, 
                         %(action_type)s, %(activity_category)s, %(min_qty)s, %(max_qty)s, %(is_standard)s) 
                         ON CONFLICT (pk) DO NOTHING;"""
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Config")

            elif u_type == "Pharmacy Workflow Report":
                clean = clean_pharmacy_report(raw)
                sql = """INSERT INTO pharmacy_orders (pk, queue_id, priority, dt, med_id, med_desc, 
                         destination, user_name, qty) VALUES (%(pk)s, %(queue_id)s, %(priority)s, 
                         %(dt)s, %(med_id)s, %(med_desc)s, %(destination)s, %(user_name)s, %(qty)s) 
                         ON CONFLICT (pk) DO NOTHING;"""
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Pharmacy Orders")

            elif u_type == "Inventory Audit (Prices)":
                clean = clean_inventory_file(raw)
                sql_costs = """INSERT INTO med_costs (med_id, cost_per_unit) VALUES (%(med_id)s, %(unit_cost)s) 
                               ON CONFLICT (med_id) DO UPDATE SET cost_per_unit = EXCLUDED.cost_per_unit;"""
                execute_statement(sql_costs, clean.to_dict("records"), batch=True, table_name="Cost Updates")

            elif u_type == "Staff Schedule":
                clean = clean_schedule_data(raw)
                sql = """INSERT INTO staff_schedule (pk, dt, day_name, staff_name, shift_type, 
                         assignment_type, raw_entry, note) VALUES (%(pk)s, %(dt)s, %(day_name)s, 
                         %(staff_name)s, %(shift_type)s, %(assignment_type)s, %(raw_entry)s, 
                         %(note)s) ON CONFLICT (pk) DO NOTHING;"""
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Schedule")

            elif u_type == "Attendance Tracking":
                clean = clean_attendance_file(uploaded)
                sql = """INSERT INTO attendance_punches (pk, raw_name, dt_date, start_dt, end_dt) 
                         VALUES (%(pk)s, %(raw_name)s, %(dt_date)s, %(start_dt)s, %(end_dt)s) 
                         ON CONFLICT (pk) DO NOTHING;"""
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Attendance")
            
            # 3. Success & Refresh
            if clean is not None:
                st.cache_data.clear()
                st.success(f"Successfully uploaded {len(clean)} records!")
                st.rerun()
            else:
                st.warning("File type logic not yet implemented for this selection.")

        except Exception as e:
            st.error(f"Processing Error: {e}")

 
# --- EXECUTE DATA LOADER ---
# Load data once for use across all App.py logic
try:
    df_events, df_config, df_pharm, df_sched, df_att = load_data(start_date, end_date)
except Exception as e:
    st.error(f"Failed to load data: {e}")
    
   # --- EXECUTE DATA LOADER ---
# This ensures variables are always populated with something (even if empty)
if 'start_date' in locals() and 'end_date' in locals():
    try:
        df_events, df_config, df_pharm, df_sched, df_att = load_data(start_date, end_date)
    except Exception as e:
        st.error(f"Failed to load data: {e}")

# 1. OVERVIEW
if selected_page == "📊 Overview":
    if not df_events.empty:
        st.markdown("## 🏥 Executive Summary")
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

# 15. STUDENT PROJECT
elif selected_page == "🎓 Student Project":
    st.header("🎓 Student Optimization Project")
    st.caption("Tracking the value of inventory returned from Pyxis machines (45-day -> 28-day optimization).")
    
    if not df_events.empty:
        all_users = sorted(df_events['user_name'].dropna().unique())
        selected_students = st.multiselect("Select Project Team (Students)", all_users)
        all_actions = sorted(df_events['event_type'].dropna().unique())
        default_actions = [x for x in all_actions if "UNLOAD" in x.upper() or "EMPTY" in x.upper()]
        selected_actions = st.multiselect("Select Actions", all_actions, default=default_actions)
        
        st.divider()
        st.markdown("#### 🧠 Smart Logic Settings")
        c_set1, c_set2 = st.columns(2)
        with c_set1:
            st.markdown("**1. Inhaler Adjustment (Qty Fix)**")
            adjust_inhalers = st.checkbox("➗ Adjust Inhaler Quantities", value=True, help="Divides quantity by 'Puffs' for items marked as HFA/Puff/Inhaler.")
            puffs_per_unit = st.number_input("Est. Puffs per Inhaler", value=120, min_value=1)
        with c_set2:
            st.markdown("**2. Bulk Pack Adjustment (Price Fix)**")
            adjust_bulk = st.checkbox("💲 Adjust High-Cost Orals (Bulk Price)", value=True, help="If a Tablet/Capsule costs more than the threshold, assume it's a pack price and divide it.")
            cost_threshold = st.number_input("Max Reasonable Pill Cost ($)", value=10.0, min_value=1.0, step=1.0, help="Any tablet costing MORE than this will be treated as a bulk pack.")
            pack_divisor = st.number_input("Est. Pack Size (Divisor)", value=100, min_value=1, help="Divide the high price by this number (e.g. $80 / 100 = $0.80/pill).")

        if selected_students and selected_actions:
            project_df = df_events[
                (df_events['user_name'].isin(selected_students)) & 
                (df_events['event_type'].isin(selected_actions))
            ].copy()
            
            if not project_df.empty:
                # A. QUANTITY ADJUSTMENT (Inhalers)
                inhaler_mask = project_df['med_desc'].str.contains(r'puff|hfa|inhaler|actuation', case=False, na=False)
                if adjust_inhalers:
                    project_df['Adj_Qty'] = np.where((inhaler_mask) & (project_df['qty'] > 5), project_df['qty'] / puffs_per_unit, project_df['qty'])
                    project_df['Qty_Note'] = np.where((inhaler_mask) & (project_df['qty'] > 5), "Adj (Inhaler)", "Raw")
                else:
                    project_df['Adj_Qty'] = project_df['qty']
                    project_df['Qty_Note'] = "Raw"

                # B. COST ADJUSTMENT (Bulk Packs)
                oral_mask = project_df['med_desc'].str.contains(r'tab|cap', case=False, na=False)
                high_cost_mask = project_df['cost_per_unit'] > cost_threshold
                if adjust_bulk:
                    project_df['Adj_Cost'] = np.where((oral_mask) & (high_cost_mask), project_df['cost_per_unit'] / pack_divisor, project_df['cost_per_unit'])
                    project_df['Cost_Note'] = np.where((oral_mask) & (high_cost_mask), f"Adj (Bulk/{pack_divisor})", "Raw")
                else:
                    project_df['Adj_Cost'] = project_df['cost_per_unit']
                    project_df['Cost_Note'] = "Raw"

                # C. FINAL CALCULATION
                project_df['Total Value'] = project_df['Adj_Qty'] * project_df['Adj_Cost']
                
                total_qty_adj = project_df['Adj_Qty'].sum()
                total_value = project_df['Total Value'].sum()
                
                st.divider()
                c1, c2, c3 = st.columns(3)
                c1.metric("Total Items (Adj)", f"{total_qty_adj:,.1f}")
                c2.metric("Total Value Saved", f"${total_value:,.2f}")
                c3.metric("Transactions", len(project_df))
                
                c_chart1, c_chart2 = st.columns(2)
                with c_chart1:
                    st.subheader("🏆 Value by Student")
                    student_stats = project_df.groupby('user_name')['Total Value'].sum().reset_index()
                    fig1 = px.bar(student_stats, x='Total Value', y='user_name', orientation='h', text_auto='$.2f', title="Dollar Value Returned")
                    st.plotly_chart(fig1, use_container_width=True)
                with c_chart2:
                    st.subheader("📍 Machines Optimized")
                    device_stats = project_df.groupby('device')['Adj_Qty'].sum().reset_index().sort_values('Adj_Qty', ascending=False).head(10)
                    fig2 = px.bar(device_stats, x='Adj_Qty', y='device', orientation='h', text_auto='.1f', title="Top 10 Machines (Units Removed)")
                    fig2.update_layout(yaxis={'categoryorder':'total ascending'})
                    st.plotly_chart(fig2, use_container_width=True)
                
                st.subheader("📋 Transaction Details")
                st.caption("Check 'Notes' columns to see where smart logic changed the values.")
                cols_to_show = ['dt', 'user_name', 'device', 'med_desc', 'qty', 'Adj_Qty', 'cost_per_unit', 'Adj_Cost', 'Total Value', 'Qty_Note', 'Cost_Note']
                st.dataframe(project_df[cols_to_show].sort_values('Total Value', ascending=False), use_container_width=True, column_config={
                    "dt": st.column_config.DatetimeColumn("Time", format="MM/DD HH:mm"),
                    "qty": st.column_config.NumberColumn("Qty (Raw)", format="%.0f"),
                    "Adj_Qty": st.column_config.NumberColumn("Qty (Adj)", format="%.1f"),
                    "cost_per_unit": st.column_config.NumberColumn("Cost (Raw)", format="$%.2f"),
                    "Adj_Cost": st.column_config.NumberColumn("Cost (Adj)", format="$%.2f"),
                    "Total Value": st.column_config.NumberColumn("Total Value", format="$%.2f")
                })

                missing_costs = project_df[project_df['Adj_Cost'] == 0].copy()
                if not missing_costs.empty:
                    st.divider()
                    with st.expander(f"⚠️ Missing Prices ({len(missing_costs)} items)", expanded=False):
                        st.warning("These items have $0.00 cost and are NOT included in the total.")
                        st.dataframe(missing_costs[['med_id', 'med_desc', 'qty']].groupby(['med_id', 'med_desc']).sum(), use_container_width=True)
                
                adjusted_items = project_df[(project_df['Qty_Note'] != 'Raw') | (project_df['Cost_Note'] != 'Raw')].copy()
                if not adjusted_items.empty:
                    with st.expander(f"🛠️ Logic Audit: Adjusted Items ({len(adjusted_items)} items)", expanded=False):
                        st.info("These items had their Quantity or Cost adjusted by the Smart Logic.")
                        st.dataframe(adjusted_items[['med_desc', 'qty', 'Adj_Qty', 'cost_per_unit', 'Adj_Cost', 'Qty_Note', 'Cost_Note']], use_container_width=True)
            else:
                st.warning("No transactions found for these students with the selected actions.")
        else:
            st.info("Please select at least one student and one action to begin.")
    else:
        st.warning("Please upload a Daily Transaction Report to use this feature.")

# 14. SHIFT LEADERBOARD
elif selected_page == "🏆 Shift Leaderboard":
    st.header("🏆 Shift Performance Leaderboard")
    st.caption("Identify top performers and high-volume staff by shift type.")
    
    if not df_sched.empty:
        activity_data = []
        if not df_events.empty:
            df_events['match_key'] = df_events['user_name'].apply(normalize_name)
            pyxis_counts = df_events.groupby([df_events['dt'].dt.date, 'match_key']).size().reset_index(name='pyxis_tx')
            pyxis_counts.columns = ['date_obj', 'match_key', 'pyxis_tx']
            activity_data.append(pyxis_counts)
        if not df_pharm.empty:
            df_pharm['match_key'] = df_pharm['user_name'].apply(normalize_name)
            pharm_counts = df_pharm.groupby([df_pharm['dt'].dt.date, 'match_key']).size().reset_index(name='pharm_tx')
            pharm_counts.columns = ['date_obj', 'match_key', 'pharm_tx']
            activity_data.append(pharm_counts)
            
        if activity_data:
            total_activity = activity_data[0]
            for df in activity_data[1:]:
                total_activity = pd.merge(total_activity, df, on=['date_obj', 'match_key'], how='outer').fillna(0)
            total_activity['total_tx'] = total_activity.get('pyxis_tx', 0) + total_activity.get('pharm_tx', 0)
        else:
            total_activity = pd.DataFrame(columns=['date_obj', 'match_key', 'total_tx'])

        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)
        df_sched['date_obj'] = pd.to_datetime(df_sched['dt']).dt.date
        merged = pd.merge(df_sched, total_activity, on=['date_obj', 'match_key'], how='left')
        merged['total_tx'] = merged['total_tx'].fillna(0)
        
        valid_shifts = [s for s in merged['shift_type'].unique() if s and str(s).lower() not in ['x', 'nan', 'pto', 'off']]
        sel_shift = st.selectbox("Select Shift Type", sorted(valid_shifts))
        shift_data = merged[merged['shift_type'] == sel_shift]
        
        if not shift_data.empty:
            stats = shift_data.groupby('staff_name').agg(
                shifts_worked=('pk', 'count'),
                total_transactions=('total_tx', 'sum'),
                avg_tx_per_shift=('total_tx', 'mean')
            ).reset_index()
            stats = stats[stats['shifts_worked'] > 0]
            
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("📊 Most Shifts Worked")
                top_volume = stats.sort_values('shifts_worked', ascending=False).head(10)
                fig_vol = px.bar(top_volume, x='shifts_worked', y='staff_name', orientation='h', title=f"Who works '{sel_shift}' the most?", text_auto=True)
                fig_vol.update_layout(yaxis={'categoryorder':'total ascending', 'title': ''}, xaxis_title="Shifts Count")
                st.plotly_chart(fig_vol, use_container_width=True)
            with c2:
                st.subheader("⚡ Efficiency (Tx per Shift)")
                top_eff = stats[stats['shifts_worked'] >= 3].sort_values('avg_tx_per_shift', ascending=False).head(10)
                if top_eff.empty: top_eff = stats.sort_values('avg_tx_per_shift', ascending=False).head(10)
                fig_eff = px.bar(top_eff, x='avg_tx_per_shift', y='staff_name', orientation='h', title=f"Most Productive on '{sel_shift}'", text_auto='.0f', color='avg_tx_per_shift')
                fig_eff.update_layout(yaxis={'categoryorder':'total ascending', 'title': ''}, xaxis_title="Avg Transactions")
                st.plotly_chart(fig_eff, use_container_width=True)
            st.divider()
            st.write("Detailed Stats", stats.sort_values('shifts_worked', ascending=False))
        else:
            st.info(f"No data found for shift type: {sel_shift}")
    else:
        st.warning("Please upload a Schedule file to use this feature.")

# 3. PROCESS MINING
elif selected_page == "🚀 Process Mining":
    if not df_events.empty:
        st.markdown("### 🔄 Workflow Visualization")
        c1, c2, c3 = st.columns(3)
        users = sorted(df_events['user_name'].dropna().unique())
        devices = sorted(df_events['device'].dropna().unique())
        sel_user = c1.multiselect("Filter User", users)
        sel_device = c2.multiselect("Filter Device", devices)
        
        moves = df_events[df_events['device'] != df_events['prev_device']].dropna(subset=['prev_device', 'device'])
        if sel_user: moves = moves[moves['user_name'].isin(sel_user)]
        if sel_device: moves = moves[moves['device'].isin(sel_device) | moves['prev_device'].isin(sel_device)]

        if not moves.empty:
            path_counts = moves.groupby(['prev_device', 'device']).size().reset_index(name='count')
            path_counts = path_counts.sort_values('count', ascending=False).head(30)
            all_nodes = list(pd.concat([path_counts['prev_device'], path_counts['device']]).unique())
            node_map = {node: i for i, node in enumerate(all_nodes)}
            fig = go.Figure(data=[go.Sankey(
                node=dict(pad=15, thickness=20, line=dict(color="black", width=0.5), label=all_nodes, color="#1E90FF"),
                link=dict(source=path_counts['prev_device'].map(node_map), target=path_counts['device'].map(node_map), value=path_counts['count'])
            )])
            fig.update_layout(title_text="Top 30 Workflow Paths", height=600)
            st.plotly_chart(fig, use_container_width=True)
            st.divider()
            activity = moves.groupby([moves['dt'].dt.hour.rename('Hour'), 'device']).size().reset_index(name='count')
            fig_heat = px.density_heatmap(activity, x='Hour', y='device', z='count', nbinsx=24, color_continuous_scale='Viridis')
            st.plotly_chart(fig_heat, use_container_width=True)

# 4. COMPLIANCE
elif selected_page == "🛡️ Compliance":
    if not df_events.empty:
        disc_df = df_events[df_events['discrepancy_qty'] != 0].copy()
        c1, c2 = st.columns(2)
        c1.metric("Count Errors", len(disc_df))
        if not disc_df.empty:
            disc_df['abs_variance'] = disc_df['discrepancy_qty'].abs() * disc_df['cost_per_unit']
            total_loss = disc_df['abs_variance'].sum()
            c2.metric("Variance Value (Risk)", f"${total_loss:,.2f}")
            st.dataframe(disc_df[['dt', 'user_name', 'device', 'med_desc', 'discrepancy_qty', 'discrepancy_reason', 'cost_per_unit', 'abs_variance']], use_container_width=True, column_config={"abs_variance": st.column_config.NumberColumn("Risk Value", format="$%.2f")})
        else:
            st.success("✅ Zero discrepancies found!")

# 5. PENDS ANALYZER
elif selected_page == "📥 Pends Analyzer":
    st.markdown("### 📥 Inventory Configuration")
    if not df_config.empty:
        c1, c2 = st.columns(2)
        u_filter = c1.multiselect("User", sorted(df_config['user_name'].dropna().unique()), key="pend_u")
        d_filter = c2.multiselect("Device", sorted(df_config['device'].dropna().unique()), key="pend_d")
        view = df_config.copy()
        if u_filter: view = view[view['user_name'].isin(u_filter)]
        if d_filter: view = view[view['device'].isin(d_filter)]
        st.dataframe(view, use_container_width=True)

# 6. LOAD/UNLOAD
elif selected_page == "🚚 Load/Unload":
    if not df_events.empty:
        loads = df_events[df_events['event_type'].str.contains('load|unload', case=False, na=False)]
        st.dataframe(loads[['dt', 'user_name', 'device', 'event_type', 'med_desc', 'qty']], use_container_width=True)

# 7. EFFICIENCY
elif selected_page == "⚡ Efficiency":
    if not df_events.empty:
        st.markdown("### 📉 Inefficient Refills")
        effic = df_events.groupby(['device', 'med_desc']).agg(Trips=('pk', 'count'), Avg_Qty=('qty', 'mean')).reset_index()
        inefficient = effic[(effic['Trips'] >= 3) & (effic['Avg_Qty'] < 3)].sort_values('Trips', ascending=False).head(20)
        fig = px.bar(inefficient, x='Trips', y='med_desc', orientation='h', title="High Frequency, Low Yield Refills", color='Trips')
        st.plotly_chart(fig, use_container_width=True)

# 10. RETURN RECONCILIATION
elif selected_page == "🔄 Return Reconciliation":
    st.markdown("### 🔄 Unload vs. Return Reconciliation")
    filter_narc = st.checkbox("Exclude Controlled Substances", value=True)
    if not df_events.empty and not df_pharm.empty:
        raw_unloads = df_events[df_events['event_type'].str.contains(r'unload|empty\s*return', case=False, na=False)].copy()
        raw_returns = df_pharm[df_pharm['priority'] == 'Returns'].copy()
        if filter_narc:
            pat = '|'.join(NARC_TERMS)
            raw_unloads = raw_unloads[~raw_unloads['med_desc'].str.contains(pat, case=False, na=False)]
            raw_returns = raw_returns[~raw_returns['med_desc'].str.contains(pat, case=False, na=False)]
        raw_unloads['norm_med_id'] = raw_unloads['med_id'].str.strip().str.upper()
        raw_unloads['Date'] = raw_unloads['dt'].dt.date
        raw_returns['norm_med_id'] = raw_returns['med_id'].str.strip().str.upper()
        raw_returns['Date'] = raw_returns['dt'].dt.date
        grp_unload = raw_unloads.groupby(['Date', 'norm_med_id']).agg({'qty': 'sum', 'med_desc': 'first', 'event_type': lambda x: ", ".join(sorted(x.astype(str).unique()))}).reset_index()
        grp_return = raw_returns.groupby(['Date', 'norm_med_id']).agg({'qty': 'sum', 'med_desc': 'first'}).reset_index()
        merged = pd.merge(grp_unload, grp_return, on=['Date', 'norm_med_id'], how='outer', suffixes=('_floor', '_pharm'))
        merged['med_desc'] = merged['med_desc_floor'].fillna(merged['med_desc_pharm']).fillna("Unknown Med")
        merged['qty_floor'] = merged['qty_floor'].fillna(0)
        merged['qty_pharm'] = merged['qty_pharm'].fillna(0)
        merged['event_type'] = merged['event_type'].fillna("Manual Pharm Return")
        merged['Variance'] = merged['qty_pharm'] - merged['qty_floor']
        merged['Status'] = np.where(merged['Variance'] == 0, "✅ Matched", "❌ Variance")
        merged.rename(columns={'event_type': 'Floor Action', 'qty_floor': 'Qty Unloaded', 'qty_pharm': 'Qty Returned'}, inplace=True)
        
        total_items = len(merged)
        matched_items = len(merged[merged['Variance'] == 0])
        match_rate = (matched_items / total_items * 100) if total_items > 0 else 0
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Match Rate", f"{match_rate:.1f}%")
        c2.metric("Total Items", total_items)
        c3.metric("Discrepancies", total_items - matched_items, delta_color="inverse")
        c4.metric("Net Variance", f"{merged['Variance'].sum():.0f}")
        
        daily = merged.groupby(['Date', 'Status']).size().unstack(fill_value=0)
        for col in ["✅ Matched", "❌ Variance"]:
            if col not in daily.columns: daily[col] = 0
        fig = go.Figure()
        fig.add_trace(go.Bar(x=daily.index, y=daily['✅ Matched'], name='Matched', marker_color='#4CAF50'))
        fig.add_trace(go.Bar(x=daily.index, y=daily['❌ Variance'], name='Variance', marker_color='#F44336'))
        fig.update_layout(barmode='stack', title="Daily Reconciliation Performance", height=300, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        
        st.divider()
        show_all = st.checkbox("Show Matched Rows (Variance = 0)", value=False)
        display_df = merged.copy()
        if not show_all:
            display_df = display_df[display_df['Variance'] != 0]
        cols_display = ['Status', 'Date', 'med_desc', 'Floor Action', 'Qty Unloaded', 'Qty Returned', 'Variance']
        st.caption("👆 Click a row to drill down into specific timestamps.")
        event = st.dataframe(display_df[cols_display].sort_values(['Date', 'Variance']), use_container_width=True, on_select="rerun", selection_mode="single-row", hide_index=True, column_config={"Date": st.column_config.DateColumn("Date"), "Variance": st.column_config.NumberColumn("Variance", format="%.0f")})
        
        if len(event.selection.rows) > 0:
            selected_index = event.selection.rows[0]
            row = display_df.iloc[selected_index]
            sel_date = row['Date']
            sel_med_id = row['norm_med_id'] 
            sel_med_desc = row['med_desc']
            st.divider()
            st.subheader(f"🔎 Audit Trail: {sel_med_desc}")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("#### 🏥 Floor (Pyxis)")
                floor_details = raw_unloads[(raw_unloads['Date'] == sel_date) & (raw_unloads['norm_med_id'] == sel_med_id)].sort_values('dt')
                if not floor_details.empty:
                    st.dataframe(floor_details[['dt', 'device', 'user_name', 'qty', 'event_type']], use_container_width=True, column_config={"dt": st.column_config.DatetimeColumn("Time", format="HH:mm:ss"), "qty": st.column_config.NumberColumn("Qty", format="%.0f")}, hide_index=True)
                else:
                    st.info("No Pyxis records found.")
            with c2:
                st.markdown("#### 💊 Pharmacy (Carousel/Packager)")
                pharm_details = raw_returns[(raw_returns['Date'] == sel_date) & (raw_returns['norm_med_id'] == sel_med_id)].sort_values('dt')
                if not pharm_details.empty:
                    st.dataframe(pharm_details[['dt', 'destination', 'user_name', 'qty']], use_container_width=True, column_config={"dt": st.column_config.DatetimeColumn("Time", format="HH:mm:ss"), "qty": st.column_config.NumberColumn("Qty", format="%.0f"), "destination": "Location"}, hide_index=True)
                else:
                    st.info("No Pharmacy scan records found.")
    else:
        st.info("Need both Pyxis Transaction Reports and Pharmacy Workflow Reports to perform reconciliation.")

# 11. TECH COMPARISON
elif selected_page == "⚖️ Tech Comparison":
    st.markdown("### ⚖️ Head-to-Head Comparison")
    if not df_events.empty:
        users = sorted(df_events['user_name'].dropna().unique())
        c1, c2 = st.columns(2)
        u1 = c1.selectbox("User A", users, key="u1")
        u2 = c2.selectbox("User B", users, index=1 if len(users)>1 else 0, key="u2")
        def get_metrics(user):
            sub = df_events[df_events['user_name'] == user]
            if sub.empty: return 0, 0, 0
            valid = sub[~sub['event_type'].str.contains('verify', case=False)]
            tx_count = len(valid)
            avg_speed = valid[valid['machine_time_sec'] > 0]['machine_time_sec'].mean()
            hours = (sub['dt'].max() - sub['dt'].min()).total_seconds() / 3600
            rate = tx_count / max(hours, 0.5) 
            return tx_count, avg_speed, rate
        m_a = get_metrics(u1)
        m_b = get_metrics(u2)
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

# 12. PROGRESSION
elif selected_page == "📈 Tech Progression":
    st.markdown("### 📈 Performance Trend")
    if not df_events.empty:
        u_sel = st.selectbox("Select Tech", sorted(df_events['user_name'].dropna().unique()), key="prog_u")
        udf = df_events[df_events['user_name'] == u_sel].copy()
        if not udf.empty:
            udf.set_index('dt', inplace=True)
            res = udf.resample("D").agg({'pk': 'count', 'machine_time_sec': 'mean'}).rename(columns={'pk': 'Tx', 'machine_time_sec': 'Speed'})
            st.plotly_chart(px.line(res, y='Tx', title="Productivity (Tx/Day)", markers=True), use_container_width=True)
            st.plotly_chart(px.line(res, y='Speed', title="Speed (Sec/Tx)", markers=True), use_container_width=True)

# 13. ATTENDANCE
elif selected_page == "📅 Attendance":
    st.markdown("### 📋 Schedule Reconciliation")
    if not df_sched.empty and not df_events.empty:
        df_events['match_key'] = df_events['user_name'].apply(normalize_name)
        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)
        worked = df_events.groupby([df_events['dt'].dt.date.rename('date_obj'), 'match_key']).size().reset_index(name='tx_count')
        df_sched['date_obj'] = df_sched['dt'].dt.date
        merged = pd.merge(df_sched, worked, on=['date_obj', 'match_key'], how='outer')
        def get_status(row):
            shift = str(row['shift_type']).upper()
            tx = row['tx_count'] if pd.notnull(row['tx_count']) else 0
            if 'PTO' in shift or row['assignment_type'] == 'PTO': return "🌴 PTO"
            if tx > 0: return "✅ Present"
            if pd.notna(row['shift_type']): return "❌ No Show / No Login"
            return "➕ Unscheduled"
        merged['Status'] = merged.apply(get_status, axis=1)
        merged = merged[~merged['staff_name'].fillna('Unknown').str.lower().isin(ADMIN_USERS)]
        st.dataframe(merged[['date_obj', 'staff_name', 'shift_type', 'Status']].sort_values('date_obj', ascending=False), use_container_width=True, column_config={"date_obj": st.column_config.DateColumn("Date")})
