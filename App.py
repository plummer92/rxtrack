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
import os


engine = create_engine(
    st.secrets["neon"]["db_url"],
    pool_pre_ping=True,
    pool_recycle=300
)

_DEFAULT_ADMIN_USERS = {"emily", "joe", "krista"}

@st.cache_data(ttl=300)
def load_admin_users():
    """Load admin usernames from DB. Falls back to defaults if table is empty or unavailable."""
    try:
        from sqlalchemy import text as _t
        with engine.connect() as conn:
            result = conn.execute(_t("SELECT username FROM admin_users"))
            users = {row[0].strip().lower() for row in result if row[0]}
        return users if users else _DEFAULT_ADMIN_USERS
    except Exception:
        return _DEFAULT_ADMIN_USERS

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
        def _sql_safe(value):
            # Convert pandas / numpy missing values to true SQL NULLs before psycopg2 sees them.
            if pd.isna(value):
                return None
            return value

        def _normalize_params(payload):
            if batch:
                return [
                    {k: _sql_safe(v) for k, v in row.items()}
                    for row in payload
                ]
            if isinstance(payload, dict):
                return {k: _sql_safe(v) for k, v in payload.items()}
            return payload

        params = _normalize_params(params)
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
        );""",
        """CREATE TABLE IF NOT EXISTS admin_users (
            username TEXT PRIMARY KEY,
            display_name TEXT,
            added_at TIMESTAMP DEFAULT NOW()
        );""",
        """CREATE TABLE IF NOT EXISTS daily_ops (
            id SERIAL PRIMARY KEY,
            task TEXT NOT NULL,
            category TEXT,
            priority TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'Not Started',
            due_date DATE,
            created_at TIMESTAMP DEFAULT NOW(),
            notes TEXT
        );""",
        """CREATE TABLE IF NOT EXISTS follow_ups (
            id SERIAL PRIMARY KEY,
            item TEXT NOT NULL,
            action_taken TEXT,
            follow_up_date DATE,
            status TEXT DEFAULT 'Pending',
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );""",
        """CREATE TABLE IF NOT EXISTS recurring_tasks (
            id SERIAL PRIMARY KEY,
            task TEXT NOT NULL,
            category TEXT,
            priority TEXT DEFAULT 'Medium',
            recurrence TEXT DEFAULT 'Daily',
            days_of_week TEXT,
            active BOOLEAN DEFAULT TRUE,
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );""",
        """ALTER TABLE daily_ops ADD COLUMN IF NOT EXISTS recurring_task_id INTEGER;"""
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
    if not shift_str or pd.isna(shift_str):
        return None

    s = str(shift_str).lower().strip()

    # Format: 7:00a / 07:30pm
    m_time = re.search(r'(\d{1,2}):(\d{2})', s)
    if m_time:
        h, m = int(m_time.group(1)), int(m_time.group(2))
        if 'p' in s and h < 12:
            h += 12
        if 'a' in s and h == 12:
            h = 0
        try:
            return pd.to_datetime(f"{date_obj} {h:02d}:{m:02d}")
        except:
            return None

    # Format: 7a / 7p
    m_ampm = re.search(r'(\d{1,2})\s*([ap])', s)
    if m_ampm:
        h = int(m_ampm.group(1))
        ampm = m_ampm.group(2)
        if ampm == 'p' and h < 12:
            h += 12
        if ampm == 'a' and h == 12:
            h = 0
        try:
            return pd.to_datetime(f"{date_obj} {h:02d}:00")
        except:
            return None

    # Military time: 0700 / 1500
    m_mil = re.search(r'(\d{4})', s)
    if m_mil:
        val = int(m_mil.group(1))
        if 0 <= val <= 2400:
            h, m = divmod(val, 100)
            try:
                return pd.to_datetime(f"{date_obj} {h:02d}:{m:02d}")
            except:
                return None

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
    # Leave timestamps as Python datetimes for psycopg2 and convert missing values to SQL NULL.
    df["discrepancy_reason"] = df["discrepancy_reason"].where(pd.notna(df["discrepancy_reason"]), None)
    df["resolution_dt"] = df["resolution_dt"].where(pd.notna(df["resolution_dt"]), None)
    df = df.astype(object)
    df = df.where(pd.notna(df), None)
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

    # 🔒 GLOBAL NONE GUARD (CRITICAL)
        df["user_name"] = (
            df["user_name"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
            .replace(["None", "none", ""], "unknown")
        )
    
        df["med_desc"] = df["med_desc"].fillna("unknown").astype(str)
        df["device"] = df["device"].fillna("unknown").astype(str)
    
        # Numeric stability
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


def apply_global_styles():
    st.markdown("""
        <style>
        [data-testid="stSidebarNav"] { display: none; }
        .block-container { padding-top: 1.35rem; padding-bottom: 2rem; }
        [data-testid="stSidebar"] {
            background:
                radial-gradient(circle at top right, rgba(34, 197, 94, 0.12), transparent 24%),
                linear-gradient(180deg, #0f172a 0%, #111827 45%, #17212f 100%);
            border-right: 1px solid rgba(148, 163, 184, 0.16);
        }
        [data-testid="stSidebar"] * {
            color: #e5eef8;
        }
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] .stRadio label,
        [data-testid="stSidebar"] .stSelectbox label,
        [data-testid="stSidebar"] .stDateInput label,
        [data-testid="stSidebar"] .stSlider label,
        [data-testid="stSidebar"] .stFileUploader label,
        [data-testid="stSidebar"] .stCaptionContainer,
        [data-testid="stSidebar"] small {
            color: #dbe7f3 !important;
        }
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] a,
        [data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] {
            color: #f8fafc !important;
        }
        [data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] {
            border-radius: 12px;
            padding: 0.2rem 0.35rem;
        }
        [data-testid="stSidebar"] [data-testid="stPageLink-NavLink"]:hover {
            background: rgba(148, 163, 184, 0.12);
        }
        [data-testid="stSidebar"] .stSelectbox > div > div,
        [data-testid="stSidebar"] .stDateInput > div > div,
        [data-testid="stSidebar"] .stFileUploader > div,
        [data-testid="stSidebar"] .stMultiSelect > div > div {
            background: rgba(15, 23, 42, 0.72);
            border-color: rgba(148, 163, 184, 0.28);
            color: #f8fafc;
        }
        .rx-shell {
            background: linear-gradient(135deg, #0f172a 0%, #1f2937 100%);
            color: white;
            padding: 16px 18px;
            border-radius: 16px;
            margin-bottom: 14px;
            box-shadow: 0 12px 28px rgba(15, 23, 42, 0.16);
        }
        .rx-shell h2 {
            margin: 0 0 4px 0;
            font-size: 1.25rem;
            font-weight: 800;
        }
        .rx-shell p {
            margin: 0;
            color: rgba(255,255,255,0.78);
            font-size: 0.88rem;
            line-height: 1.35;
        }
        .rx-nav-label {
            font-size: 0.72rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #93c5fd;
            margin: 14px 0 6px 0;
        }
        </style>
    """, unsafe_allow_html=True)


def render_page_links():
    st.markdown('<div class="rx-nav-label">Core</div>', unsafe_allow_html=True)
    st.page_link("App.py", label="Overview Hub", icon="🏠")
    st.page_link("pages/🧪_Workflow_Experiments.py", label="Workflow Experiments", icon="🧪")
    st.page_link("pages/🧭_Pilot_Monitor.py", label="Pilot Monitor", icon="🧭")
    st.page_link("pages/⚖️_Workload_Capacity_Simulator.py", label="Capacity Simulator", icon="⚖️")

    st.markdown('<div class="rx-nav-label">Operations</div>', unsafe_allow_html=True)
    st.page_link("pages/🏥_Pharmacy_Workflow.py", label="Pharmacy Workflow", icon="🏥")
    st.page_link("pages/🔄_Return_Reconciliation.py", label="Return Reconciliation", icon="🔄")
    st.page_link("pages/🗑️_Return_Bin_Tracker.py", label="Return Bin Tracker", icon="🗑️")
    st.page_link("pages/🎯_Daily_Command.py", label="Daily Command", icon="🎯")

    st.markdown('<div class="rx-nav-label">Performance</div>', unsafe_allow_html=True)
    st.page_link("pages/1_⏰_Tardies.py", label="Tardies", icon="⏰")
    st.page_link("pages/2_🔍_Session_Explorer.py", label="Session Explorer", icon="🔍")
    st.page_link("pages/📊_Workforce_Intelligence.py", label="Workforce Intelligence", icon="📊")
    st.page_link("pages/📥_Pends_Analyzer.py", label="Pends Analyzer", icon="📥")
    st.page_link("pages/🚨_discrepancy_deep_dive.py", label="Discrepancy Deep Dive", icon="🚨")

    st.markdown('<div class="rx-nav-label">Tools</div>', unsafe_allow_html=True)
    st.page_link("pages/📊_Cycle_Count_Integrity.py", label="Cycle Count Integrity", icon="📊")
    st.page_link("pages/📋_Carousel_Drop_Tracker.py", label="Carousel Drop Tracker", icon="📋")
    st.page_link("pages/_🔍_MedLookup.py", label="Med Lookup", icon="🔍")
    st.page_link("pages/_🧠_RxBrain.py", label="RxBrain", icon="🧠")
    st.page_link("pages/🗄️_db_health.py", label="Database Health", icon="🗄️")
    st.page_link("pages/Admin_Master_Mapping.py", label="Admin & Mapping", icon="⚙️")


# --- SHARED SIDEBAR RENDERER ---
def render_sidebar():
    """Call this at the top of any page to always show the date range sidebar."""
    apply_global_styles()
    n_events, n_pharm, n_sched, n_att, min_db, max_db = get_stats_range()

    if 'start_date' not in st.session_state:
        st.session_state.start_date = max_db - timedelta(days=14)
    if 'end_date' not in st.session_state:
        st.session_state.end_date = max_db

    with st.sidebar:
        st.markdown("""
            <div class="rx-shell">
                <h2>RxTrack</h2>
                <p>Operations, analytics, staffing pilots, and workflow testing in one place.</p>
            </div>
        """, unsafe_allow_html=True)
        render_page_links()

        st.divider()
        st.markdown("### Analysis Window")

        filter_mode = st.radio(
            "Filter Mode",
            ["Range", "Week", "Day"],
            horizontal=True,
            label_visibility="collapsed",
            key="rxtrack_sidebar_filter_mode",
        )

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
            week_start = st.date_input(
                "Select Week Start:",
                value=st.session_state.start_date,
                min_value=min_db,
                max_value=max_db,
            )
            st.session_state.start_date = week_start
            st.session_state.end_date = week_start + timedelta(days=6)

        else:
            single_day = st.date_input(
                "Select Day:",
                value=st.session_state.start_date,
                min_value=min_db,
                max_value=max_db,
            )
            st.session_state.start_date = single_day
            st.session_state.end_date = single_day

        st.divider()

        with st.expander("Database Status", expanded=False):
            c1, c2 = st.columns(2)
            c1.metric("Pyxis Events", f"{n_events:,}")
            c2.metric("Pharm Orders", f"{n_pharm:,}")
            c3, c4 = st.columns(2)
            c3.metric("Sched. Shifts", f"{n_sched:,}")
            c4.metric("Time Punches", f"{n_att:,}")

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

    return st.session_state.start_date, st.session_state.end_date



# --- MAIN APP LOGIC ---
# Only runs when App.py is the active Streamlit entrypoint.
# When other pages import App.py, they should only get the shared helpers above.
_is_main = (__name__ == "__main__")

if _is_main:
    # Apply page config and CSS only when running as main page

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

    ADMIN_USERS = load_admin_users()

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

    # --- MAIN APP LOGIC ---
    init_db()

    # 1. Define your internal pages (those not yet moved to the /pages folder)
    PAGES = [
        "📊 Overview", "🛡️ Compliance", "🚚 Load/Unload",
    ]

    # Use render_sidebar for date range — same as all other pages
    start_date, end_date = render_sidebar()

    with st.sidebar:
        st.markdown("### 🧭 Navigation")
        selected_page = st.radio("Go to:", PAGES, label_visibility="collapsed")
        st.divider()

        # --- UNIVERSAL DATA INGEST ---
        st.subheader("📤 Ingest Data")
        u_type = st.selectbox("File Type:", [
            "Daily Transaction Report", "Device Activity Log (Pends)", "Pharmacy Workflow Report", 
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

                elif u_type == "Inventory Audit (Detailed RC)":
                    clean = clean_detailed_inventory(raw)
                    sql = """INSERT INTO inventory_detailed 
                             (pk, station, med_id, med_desc, unit_cost, current_count, pocket_location)
                             VALUES (%(pk)s, %(station)s, %(med_id)s, %(med_desc)s, 
                                     %(unit_cost)s, %(current_count)s, %(pocket_location)s)
                             ON CONFLICT (pk) DO NOTHING;"""
                    execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Detailed Inventory")

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
            st.caption(f"Date range: {start_date.strftime('%b %d, %Y')} → {end_date.strftime('%b %d, %Y')}")

            # ── Classify event types ──────────────────────────────────────────
            ev = df_events.copy()
            ev["_etype"] = ev["event_type"].astype(str).str.lower().str.strip()

            # Refills: Restock, Refill, Load (not unload/cancel)
            refills  = ev[
                ev["_etype"].str.contains(r"restock|refill|\bload\b|replenish", regex=True, na=False) &
                ~ev["_etype"].str.contains("cancel|unload|empty", na=False)
            ]
            # Outdates: Outdate, Expired, Override
            outdates = ev[
                ev["_etype"].str.contains(r"outdat|expir|override", regex=True, na=False) &
                ~ev["_etype"].str.contains("cancel", na=False)
            ]
            # Unloads: Unload, Empty return bin (not cancelled/eject)
            unloads  = ev[
                ev["_etype"].str.contains(r"unload|empty", regex=True, na=False) &
                ~ev["_etype"].str.contains("cancel|eject", na=False)
            ]
            # Everything except verify (for total tx count)
            real_tx  = ev[~ev["_etype"].str.contains("verify", na=False)]

            session_stats = ev.groupby("session_id").agg(total_time=("machine_time_sec", "sum"))
            avg_time      = session_stats["total_time"].mean()

            # ── Row 1 — Top-line KPIs ─────────────────────────────────────────
            m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
            m1.metric("Total Transactions", f"{len(real_tx):,}")
            m2.metric("Refills / Restocks", f"{len(refills):,}")
            m3.metric("Unloads",            f"{len(unloads):,}")
            m4.metric("Outdates",           f"{len(outdates):,}")
            m5.metric("Active Techs",       df_events["user_name"].nunique())
            m6.metric("Avg Session",        seconds_to_mmss(avg_time))
            m7.metric("Discrepancies",      int(df_events["discrepancy_qty"].ne(0).sum()))

            st.divider()

            # ── Proactive Alerts Panel ────────────────────────────────────────
            with st.expander("🔔 Proactive Alerts", expanded=True):
                alerts = []

                # Stockout risk: meds with ending_qty == 0 in the window
                if "ending_qty" in df_events.columns and "med_desc" in df_events.columns:
                    last_inv = (
                        df_events.sort_values("dt")
                        .groupby(["device", "med_desc"])["ending_qty"]
                        .last()
                        .reset_index()
                    )
                    stockouts = last_inv[last_inv["ending_qty"] == 0]
                    if not stockouts.empty:
                        alerts.append(("🚨", f"**{len(stockouts)} medication(s) at zero inventory** — potential stockout.", "error"))

                # High discrepancy rate: > 2% of transactions
                disc_count = int(df_events["discrepancy_qty"].ne(0).sum())
                total_count = len(real_tx)
                if total_count > 0 and disc_count / total_count > 0.02:
                    pct = disc_count / total_count * 100
                    alerts.append(("⚠️", f"**Discrepancy rate is {pct:.1f}%** ({disc_count}/{total_count} transactions) — above 2% threshold.", "warning"))

                # Tardy alert: load from DB if sched/att data present
                if not df_sched.empty and not df_att.empty:
                    try:
                        _sched = df_sched.copy()
                        _att   = df_att.copy()
                        _sched["match_key"] = _sched["staff_name"].apply(normalize_name)
                        _att["match_key"]   = _att["raw_name"].apply(normalize_name)
                        _sched["date_obj"]  = pd.to_datetime(_sched["dt"]).dt.date
                        _att["date_obj"]    = pd.to_datetime(_att["dt_date"]).dt.date
                        _sched = _sched[~_sched["match_key"].isin(load_admin_users())]
                        _merged = pd.merge(_sched, _att, on=["match_key", "date_obj"], how="inner")
                        _merged["actual"]    = pd.to_datetime(_merged["start_dt"], errors="coerce")
                        _merged["scheduled"] = _merged.apply(lambda x: parse_shift_start(x["date_obj"], x["shift_type"]), axis=1)
                        _merged = _merged.dropna(subset=["actual", "scheduled"])
                        _merged["delay_min"] = (_merged["actual"] - _merged["scheduled"]).dt.total_seconds() / 60
                        tardy_count = int((_merged["delay_min"] > 5).sum())
                        repeat_offenders = (
                            _merged[_merged["delay_min"] > 5]
                            .groupby("staff_name").size()
                        )
                        repeat_count = int((repeat_offenders >= 3).sum())
                        if tardy_count > 0:
                            msg = f"**{tardy_count} tardy clock-in(s)** in this window."
                            if repeat_count > 0:
                                msg += f" {repeat_count} technician(s) with 3+ tardies."
                            alerts.append(("⏰", msg, "warning"))
                    except Exception:
                        pass

                if alerts:
                    for icon, msg, level in alerts:
                        if level == "error":
                            st.error(f"{icon} {msg}")
                        else:
                            st.warning(f"{icon} {msg}")
                else:
                    st.success("✅ No active alerts for this date range.")

            st.divider()

            # ── Daily Activity Bar Chart (Refills + Unloads + Outdates stacked) ──
            st.subheader("📅 Daily Transaction Activity")
            st.caption("Day-by-day breakdown of refills, unloads, and outdates across the selected range.")

            daily_rows = []
            for label, subset in [("Refill / Restock", refills), ("Unload", unloads), ("Outdate", outdates)]:
                if not subset.empty:
                    d = subset.copy()
                    d["_date"] = d["dt"].dt.date
                    counts = d.groupby("_date").size().reset_index(name="count")
                    counts["type"] = label
                    daily_rows.append(counts)

            if daily_rows:
                daily_df = pd.concat(daily_rows).sort_values("_date")
                fig_daily = px.bar(
                    daily_df,
                    x="_date", y="count",
                    color="type",
                    color_discrete_map={
                        "Refill / Restock": "#3b82f6",
                        "Unload":           "#8b5cf6",
                        "Outdate":          "#f97316",
                    },
                    barmode="group",
                    labels={"_date": "", "count": "Transactions", "type": ""},
                    text="count"
                )
                fig_daily.update_traces(textposition="outside", textfont_size=10)
                fig_daily.update_layout(
                    bargap=0.2,
                    bargroupgap=0.05,
                    height=400,
                    xaxis=dict(tickformat="%b %d", tickangle=-30),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
                )
                st.plotly_chart(fig_daily, use_container_width=True)
            else:
                # Only unload-type events in DB — show those as the daily chart
                ev["_date"] = ev["dt"].dt.date
                daily_all = (
                    ev[~ev["_etype"].str.contains("cancel|verify", na=False)]
                    .groupby(["_date", "event_type"])
                    .size()
                    .reset_index(name="count")
                    .sort_values("_date")
                )
                if not daily_all.empty:
                    fig_daily = px.bar(
                        daily_all,
                        x="_date", y="count",
                        color="event_type",
                        barmode="group",
                        labels={"_date": "", "count": "Transactions", "event_type": ""},
                        text="count"
                    )
                    fig_daily.update_traces(textposition="outside", textfont_size=10)
                    fig_daily.update_layout(
                        height=400,
                        xaxis=dict(tickformat="%b %d", tickangle=-30),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
                    )
                    st.plotly_chart(fig_daily, use_container_width=True)
                    st.caption(
                        "💡 Only unload-type events found in this range. "
                        "Upload a Daily Transaction Report to see refills and outdates."
                    )

            st.divider()

            # ── Heavy Hitter Stats — 3 columns ───────────────────────────────
            st.subheader("🏋️ Heavy Hitter Stats")
            col_a, col_b, col_c = st.columns(3)

            # Column A — Top techs by refill volume (fallback to all tx if no refills)
            with col_a:
                st.markdown("**🔝 Top Techs by Refill Volume**")
                source = refills if not refills.empty else real_tx
                label  = "Refills" if not refills.empty else "Transactions"
                top_tech = (
                    source.groupby("user_name")
                    .size()
                    .reset_index(name=label)
                    .sort_values(label, ascending=False)
                    .head(10)
                )
                fig_tech = px.bar(
                    top_tech, x=label, y="user_name",
                    orientation="h",
                    labels={label: label, "user_name": ""},
                    color=label, color_continuous_scale="Blues",
                    text=label
                )
                fig_tech.update_traces(textposition="outside")
                fig_tech.update_layout(
                    yaxis={"categoryorder": "total ascending"},
                    coloraxis_showscale=False,
                    height=380, margin=dict(l=0, r=30, t=10, b=0)
                )
                st.plotly_chart(fig_tech, use_container_width=True)

            # Column B — Busiest devices
            with col_b:
                st.markdown("**🖥️ Busiest Devices**")
                top_dev = (
                    real_tx.groupby("device")
                    .size()
                    .reset_index(name="transactions")
                    .sort_values("transactions", ascending=False)
                    .head(10)
                )
                fig_dev = px.bar(
                    top_dev, x="transactions", y="device",
                    orientation="h",
                    labels={"transactions": "Transactions", "device": ""},
                    color="transactions", color_continuous_scale="Purples",
                    text="transactions"
                )
                fig_dev.update_traces(textposition="outside")
                fig_dev.update_layout(
                    yaxis={"categoryorder": "total ascending"},
                    coloraxis_showscale=False,
                    height=380, margin=dict(l=0, r=30, t=10, b=0)
                )
                st.plotly_chart(fig_dev, use_container_width=True)

            # Column C — Outdates & Unloads by tech (stacked)
            with col_c:
                st.markdown("**📦 Outdates & Unloads by Tech**")
                combined = []
                if not outdates.empty:
                    ot = outdates.groupby("user_name").size().reset_index(name="count")
                    ot["type"] = "Outdate"
                    combined.append(ot)
                if not unloads.empty:
                    ul = unloads.groupby("user_name").size().reset_index(name="count")
                    ul["type"] = "Unload"
                    combined.append(ul)

                if combined:
                    combo_df = pd.concat(combined)
                    # Keep only top 10 techs by total
                    top_techs = (
                        combo_df.groupby("user_name")["count"]
                        .sum()
                        .nlargest(10)
                        .index
                    )
                    combo_df = combo_df[combo_df["user_name"].isin(top_techs)]
                    fig_ou = px.bar(
                        combo_df,
                        x="count", y="user_name",
                        color="type",
                        orientation="h",
                        barmode="stack",
                        labels={"count": "Events", "user_name": "", "type": ""},
                        color_discrete_map={"Outdate": "#f97316", "Unload": "#8b5cf6"},
                        text="count"
                    )
                    fig_ou.update_traces(textposition="inside")
                    fig_ou.update_layout(
                        yaxis={"categoryorder": "total ascending"},
                        height=380, margin=dict(l=0, r=10, t=10, b=0),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02)
                    )
                    st.plotly_chart(fig_ou, use_container_width=True)
                else:
                    st.info("No outdate or unload events in this range.")

        else:
            st.info("No Pyxis event data found for the selected date range. Upload a Daily Transaction Report to get started.")

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

    # 6. LOAD/UNLOAD
    elif selected_page == "🚚 Load/Unload":
        if not df_events.empty:
            loads = df_events[df_events['event_type'].str.contains('load|unload', case=False, na=False)]
            st.dataframe(loads[['dt', 'user_name', 'device', 'event_type', 'med_desc', 'qty']], use_container_width=True)

