###############################################################
# RXTRACK: EXECUTIVE DASHBOARD (v13.1 - Inhaler Cost Fix)
# Architecture: Quad-Table Strategy + Attendance + Pricing
# Updates:
#   1. Student Project: Added "Smart Logic" for Inhalers/Puffs.
#   2. Fixes massive cost inflation for Flovent/HFA items.
#   3. Retains all previous tabs and functionality.
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

# Nickname Mappings (LOWER CASE)
NAME_MAPPINGS = {
    "phi": "ali",          
    "rebekah": "bekah",
    "nugent": "kathy", "kathleen": "kathy",
    "spain": "dee", "deloris": "dee",
    "jabusch": "dan", "daniel": "dan",
    "nicholas": "nick"     
}

# Names that REQUIRE a last initial to distinguish duplicates
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
        );""",
        """CREATE TABLE IF NOT EXISTS attendance_punches (
            pk TEXT PRIMARY KEY, raw_name TEXT, dt_date DATE, start_dt TIMESTAMP, end_dt TIMESTAMP
        );""",
        """CREATE TABLE IF NOT EXISTS inventory_audit (
            pk TEXT PRIMARY KEY, med_id TEXT, med_desc TEXT, med_class TEXT, 
            unit_cost FLOAT, qty_on_hand FLOAT, min_lvl FLOAT, max_lvl FLOAT
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
    
    first_name = ""
    last_initial = ""
    
    if "," in s:
        parts = s.split(",")
        if len(parts) >= 2:
            last_name_part = parts[0].strip()
            first_name_part = parts[1].strip().split(" ")[0]
            first_name = first_name_part
            if last_name_part:
                last_initial = last_name_part[0]
    else:
        parts = s.split(" ")
        first_name = parts[0]
        if len(parts) > 1:
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
        try:
            return pd.to_datetime(f"{date_obj} {h:02d}:{m:02d}")
        except: return None
        
    m_ampm = re.search(r'(\d{1,2})\s*([ap])', s)
    if m_ampm:
        h = int(m_ampm.group(1))
        ampm = m_ampm.group(2)
        if ampm == 'p' and h < 12: h += 12
        if ampm == 'a' and h == 12: h = 0
        try:
            return pd.to_datetime(f"{date_obj} {h:02d}:00")
        except: return None
    
    m_mil = re.search(r'(\d{4})', s)
    if m_mil:
        val = int(m_mil.group(1))
        if 0 <= val <= 2400:
            h, m = divmod(val, 100)
            try:
                return pd.to_datetime(f"{date_obj} {h:02d}:{m:02d}")
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
            
            processed_rows.append({
                'pk': pk,
                'dt': dt,
                'day_name': day_name,
                'staff_name': clean_part.title(),
                'shift_type': final_shift_str,
                'assignment_type': assignment_type,
                'raw_entry': part,
                'note': note
            })

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
            data.append({
                "raw_name": name,
                "dt_date": pd.to_datetime(date_str).date(),
                "start_dt": start_time,
                "end_dt": end_time
            })
    df = pd.DataFrame(data)
    if not df.empty: df["pk"] = df.apply(generate_pk, axis=1)
    return df

def clean_inventory_file(df):
    df = df.copy()
    colmap = {
        "MedID": "med_id", "MedDescription": "med_desc", "MedClass": "med_class",
        "UnitCost": "unit_cost", "CurrentCount": "qty_on_hand",
        "CurrentMin": "min_lvl", "CurrentMax": "max_lvl"
    }
    df.rename(columns=colmap, inplace=True)
    
    for c in ["med_id", "med_desc", "unit_cost", "qty_on_hand", "min_lvl", "max_lvl"]:
        if c not in df.columns: df[c] = None
        
    if df['unit_cost'].dtype == object:
        df['unit_cost'] = df['unit_cost'].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False)
    
    df['unit_cost'] = pd.to_numeric(df['unit_cost'], errors='coerce').fillna(0)
    df['qty_on_hand'] = pd.to_numeric(df['qty_on_hand'], errors='coerce').fillna(0)
    
    df['pk'] = df.apply(lambda x: str(x['med_id']), axis=1)
    return df[['pk', 'med_id', 'med_desc', 'med_class', 'unit_cost', 'qty_on_hand', 'min_lvl', 'max_lvl']]

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
        df['machine_time_sec'] = np.where(
            (df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['duration'] < 600), 
            df['duration'], 0
        )
        
        df['is_new_session'] = np.where(
            (df['user_name'] != df['user_name'].shift(1)) | 
            (df['device'] != df['prev_device']) |
            (df['gap_prev'] > 1200), 1, 0
        )
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

def
