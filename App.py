###############################################################
# RXTRACK: EXECUTIVE DASHBOARD (v12.1 - Fixed Excel Import)
# Architecture: Quad-Table Strategy + Attendance Tracking
# Updates:
#   1. Fixed IndentationError in Sidebar.
#   2. Added robust Excel/CSV handling for Schedule uploads.
#   3. Includes 'Attendance Tracking' & 'Tardies' logic.
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
        # Handle "Last, First Middle" format common in HR files
        parts = s.split(",")
        if len(parts) >= 2:
            return parts[1].strip().split(" ")[0]
    return s.split(" ")[0]

def parse_shift_start(date_obj, shift_str):
    """Attempts to parse a start time from shift strings like '7a', '0700', '7:00'."""
    if not shift_str or pd.isna(shift_str): return None
    s = str(shift_str).lower().strip()
    
    # Priority 1: Explicit HH:MM (e.g., 7:00, 14:30)
    m_time = re.search(r'(\d{1,2}):(\d{2})', s)
    if m_time:
        h, m = int(m_time.group(1)), int(m_time.group(2))
        if 'p' in s and h < 12: h += 12
        if 'a' in s and h == 12: h = 0
        try:
            return pd.to_datetime(f"{date_obj} {h:02d}:{m:02d}")
        except: return None
        
    # Priority 2: Hour + AM/PM (e.g., 7a, 7p)
    m_ampm = re.search(r'(\d{1,2})\s*([ap])', s)
    if m_ampm:
        h = int(m_ampm.group(1))
        ampm = m_ampm.group(2)
        if ampm == 'p' and h < 12: h += 12
        if ampm == 'a' and h == 12: h = 0
        try:
            return pd.to_datetime(f"{date_obj} {h:02d}:00")
        except: return None
    
    # Priority 3: Military Integer (e.g., 0700, 1400)
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

def clean_attendance_file(file_obj):
    """Parses the specific Attendance Tracking CSV report format."""
    # Reset file pointer and read
    file_obj.seek(0)
    content = file_obj.read().decode('utf-8', errors='ignore')
    lines = content.splitlines()
    
    data = []
    
    # Regex to extract fields from the semi-structured lines
    # Name: Inside 'Employee:' and followed by '",Date:'
    name_pat = re.compile(r'Employee:\s*([A-Za-z\-,\s\.]+?)(?="|",|",Date)')
    # Date: 'Date: MM/DD/YYYY'
    date_pat = re.compile(r'Date:\s*(\d{1,2}/\d{1,2}/\d{4})')
    # Time: 'MM/DD/YYYY HH:MM' (Find all, take first as start, second as end)
    time_pat = re.compile(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2})')

    for line in lines:
        if "Employee:" not in line or "Date:" not in line:
            continue
            
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
    if not df.empty:
        df["pk"] = df.apply(generate_pk, axis=1)
    return df

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

    # Process Events
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

PAGES = [
    "📊 Overview", "⏰ Tardies", "🚀 Process Mining", "🛡️ Compliance", "📥 Pends Analyzer", 
    "🚚 Load/Unload", "⚡ Efficiency", "🔍 Session Explorer", "🏥 Pharmacy Workflow", 
    "🔄 Return Reconciliation", "⚖️ Tech Comparison", "📈 Tech Progression", "📅 Attendance"
]

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=60)
    st.title("RxTrack v12")
    st.caption("Pharmacy Workflow Intelligence")
    
    st.markdown("### 🧭 Navigation")
    selected_page = st.radio("Go to:", PAGES, label_visibility="collapsed")
    st.divider()
    
    rows_events, rows_pharm, min_db, max_db = get_stats_range()
    
    with st.expander("💾 Database Status", expanded=False):
        c1, c2 = st.columns(2)
        c1.metric("Pyxis Events", f"{rows_events:,}")
        c2.metric("Pharm Orders", f"{rows_pharm:,}")
        
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
    
    default_start = max(min_db, max_db - timedelta(days=7)) if min_db < max_db else min_db
    date_range = st.slider("📅 Analysis Window", 
                           min_value=min_db, max_value=max_db, 
                           value=(default_start, max_db), format="MM/DD/YY")
    start_date, end_date = date_range

    st.divider()
    
    st.subheader("📤 Ingest Data")
    u_type = st.selectbox("File Type:", [
        "Daily Transaction Report", "Device Activity Log (Pends)", 
        "Financial Price List", "Pharmacy Workflow Report", 
        "Staff Schedule", "Attendance Tracking"
    ])
    uploaded = st.file_uploader(f"Upload {u_type}", type=["csv", "xlsx"])
    
    if uploaded and st.button(f"Process {u_type}"):
        try:
            # --- 1. ATTENDANCE TRACKING ---
            if u_type == "Attendance Tracking":
                clean = clean_attendance_file(uploaded)
                if not clean.empty:
                    sql = "INSERT INTO attendance_punches (pk, raw_name, dt_date, start_dt, end_dt) VALUES (%(pk)s, %(raw_name)s, %(dt_date)s, %(start_dt)s, %(end_dt)s) ON CONFLICT (pk) DO NOTHING;"
                    execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Attendance")
                else:
                    st.error("❌ Could not parse any attendance records. Check file format.")

            # --- 2. STAFF SCHEDULE (Enhanced) ---
            elif u_type == "Staff Schedule":
                # Handle Excel vs CSV
                if uploaded.name.endswith('.xlsx'):
                    # Read Excel (reads first sheet by default)
                    raw = pd.read_excel(uploaded)
                else:
                    # Read CSV with encoding fallback
                    try:
                        raw = pd.read_csv(uploaded, header=0)
                    except UnicodeDecodeError:
                        uploaded.seek(0)
                        raw = pd.read_csv(uploaded, header=0, encoding='latin1')
                
                clean = clean_schedule_data(raw)
                sql = "INSERT INTO staff_schedule (pk, dt, day_name, staff_name, shift_type, assignment_type, raw_entry, note) VALUES (%(pk)s, %(dt)s, %(day_name)s, %(staff_name)s, %(shift_type)s, %(assignment_type)s, %(raw_entry)s, %(note)s) ON CONFLICT (pk) DO NOTHING;"
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Schedule")

            # --- 3. OTHER FILES ---
            else:
                # Detect Header Row Dynamically
                if uploaded.name.endswith('.xlsx'):
                    preview = pd.read_excel(uploaded, header=None, nrows=20)
                else:
                    try:
                        preview = pd.read_csv(uploaded, header=None, nrows=20)
                    except UnicodeDecodeError:
                        uploaded.seek(0)
                        preview = pd.read_csv(uploaded, header=None, nrows=20, encoding='latin1')

                header_idx = None
                for idx, row in preview.iterrows():
                    s = str(row.values).lower()
                    if u_type == "Daily Transaction Report" and "username" in s and "device" in s: header_idx = idx; break
                    if u_type == "Device Activity Log (Pends)" and "affectedelement" in s: header_idx = idx; break
                    if u_type == "Financial Price List" and "cost" in s: header_idx = idx; break
                    if u_type == "Pharmacy Workflow Report" and "tranqueueid" in s: header_idx = idx; break
                
                if header_idx is None:
                    st.error("❌ Could not detect valid header row. File might be formatted incorrectly.")
                else:
                    uploaded.seek(0)
                    if uploaded.name.endswith('.xlsx'):
                        raw = pd.read_excel(uploaded, header=header_idx)
                    else:
                        try:
                            raw = pd.read_csv(uploaded, header=header_idx)
                        except UnicodeDecodeError:
                            uploaded.seek(0)
                            raw = pd.read_csv(uploaded, header=header_idx, encoding='latin1')
                    
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
df_events, df_config, df_pharm, df_sched, df_att = load_data(start_date, end_date)

if df_events.empty and df_config.empty and df_pharm.empty and df_sched.empty and df_att.empty:
    st.info("👋 System Idle. Please upload data via the sidebar to begin analysis.")
    st.stop()

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

# 2. TARDIES
elif selected_page == "⏰ Tardies":
    st.header("⏰ Tardiness Tracker")
    st.caption("Matches 'Scheduled Start' with 'Actual Clock-in'. Automatically ignores consecutive shifts and admins.")
    
    if not df_sched.empty and not df_att.empty:
        # 1. Normalize Names for Matching
        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)
        df_att['match_key'] = df_att['raw_name'].apply(normalize_name)
        
        # 2. Ensure Date Types Match
        df_sched['date_obj'] = pd.to_datetime(df_sched['dt']).dt.date
        df_att['date_obj'] = pd.to_datetime(df_att['dt_date']).dt.date
        
        # 3. Merge Schedule and Attendance (Many-to-Many)
        merged = pd.merge(df_sched, df_att, on=['match_key', 'date_obj'], how='inner')
        
        if not merged.empty:
            # 4. Parse Scheduled Times
            merged['scheduled_start_dt'] = merged.apply(lambda x: parse_shift_start(x['date_obj'], x['shift_type']), axis=1)
            merged.dropna(subset=['scheduled_start_dt'], inplace=True)
            
            # 5. Smart Matching: Find best match for each schedule row
            merged['actual_start_dt'] = pd.to_datetime(merged['start_dt'])
            merged['actual_end_dt'] = pd.to_datetime(merged['end_dt'])
            
            # Calculate time difference magnitude
            merged['diff_abs'] = (merged['actual_start_dt'] - merged['scheduled_start_dt']).abs()
            
            # Sort by smallest difference and keep the best match for each Scheduled Shift
            best_matches = merged.sort_values('diff_abs').drop_duplicates(subset=['pk_x'])
            
            final_df = best_matches.copy()
            
            # 6. Calculate Delay
            final_df['delay_min'] = (final_df['actual_start_dt'] - final_df['scheduled_start_dt']).dt.total_seconds() / 60
            
            # 7. Consecutive Shift Logic (The "Double" Fix)
            final_df.sort_values(['match_key', 'actual_start_dt'], inplace=True)
            final_df['prev_end'] = final_df.groupby('match_key')['actual_end_dt'].shift(1)
            final_df['gap_min'] = (final_df['actual_start_dt'] - final_df['prev_end']).dt.total_seconds() / 60
            final_df['is_double'] = np.where((final_df['gap_min'].notnull()) & (final_df['gap_min'] < 30), True, False)

            # 8. Filter Results
            grace_period = st.slider("Grace Period (minutes)", 0, 15, 5)
            
            # Filter: Delay > Grace AND Not a Double Shift AND Not Admin
            tardies = final_df[
                (final_df['delay_min'] > grace_period) & 
                (final_df['is_double'] == False) &
                (~final_df['match_key'].isin(ADMIN_USERS))
            ].copy()
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Shifts Matched", len(final_df))
            c2.metric("Doubles/Splits Excluded", final_df['is_double'].sum())
            c3.metric("True Late Arrivals", len(tardies), delta_color="inverse")
            
            if not tardies.empty:
                tardies = tardies.sort_values('delay_min', ascending=False)
                tardies['Late By'] = tardies['delay_min'].apply(lambda x: f"{int(x)} min")
                tardies['Scheduled'] = tardies['scheduled_start_dt'].dt.strftime('%H:%M')
                tardies['Actual'] = tardies['actual_start_dt'].dt.strftime('%H:%M')
                
                st.subheader("🚩 Late List")
                st.dataframe(
                    tardies[['date_obj', 'staff_name', 'shift_type', 'Scheduled', 'Actual', 'Late By']],
                    use_container_width=True,
                    column_config={"date_obj": "Date"}
                )
            else:
                st.success("🎉 No tardies found (after excluding doubles and admins)!")
        else:
            st.warning("No valid matches found between Schedule and Attendance.")
    else:
        st.info("Please upload both 'Staff Schedule' and 'Attendance Tracking' files.")
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
            st.dataframe(
                disc_df[['dt', 'user_name', 'device', 'med_desc', 'discrepancy_qty', 'discrepancy_reason', 'cost_per_unit', 'abs_variance']],
                use_container_width=True,
                column_config={"abs_variance": st.column_config.NumberColumn("Risk Value", format="$%.2f")}
            )
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

# 8. SESSION EXPLORER
elif selected_page == "🔍 Session Explorer":
    st.header("🔍 Session Explorer")
    st.caption("Unified view of Pyxis and Pharmacy Workflow sessions.")

    if df_events.empty and df_pharm.empty:
        st.info("No data available.")
    else:
        if not df_events.empty:
            px = df_events[['user_name', 'dt', 'device', 'event_type', 'med_desc', 'qty', 'pk']].copy()
            px['source'] = 'Pyxis'
        else:
            px = pd.DataFrame(columns=['user_name', 'dt', 'device', 'event_type', 'med_desc', 'qty', 'pk', 'source'])

        if not df_pharm.empty:
            ph = df_pharm[['user_name', 'dt', 'destination', 'priority', 'med_desc', 'qty', 'pk']].copy()
            ph.rename(columns={'priority': 'event_type', 'destination': 'target_dest'}, inplace=True)
            ph['device'] = 'Pharmacy Workflow' 
            ph['source'] = 'Pharmacy'
        else:
            ph = pd.DataFrame(columns=['user_name', 'dt', 'device', 'event_type', 'med_desc', 'qty', 'pk', 'source'])

        combined = pd.concat([px, ph], ignore_index=True)
        combined['dt'] = pd.to_datetime(combined['dt'])
        combined.sort_values(['user_name', 'dt'], inplace=True)
        
        combined['prev_user'] = combined['user_name'].shift(1)
        combined['prev_device'] = combined['device'].shift(1)
        combined['prev_dt'] = combined['dt'].shift(1)
        combined['gap'] = (combined['dt'] - combined['prev_dt']).dt.total_seconds().fillna(0)
        
        combined['is_new_session'] = np.where(
            (combined['user_name'] != combined['prev_user']) | 
            (combined['device'] != combined['prev_device']) | 
            (combined['gap'] > 1200), 1, 0
        )
        combined['session_id'] = combined['is_new_session'].cumsum()

        sessions = combined.groupby('session_id').agg({
            'user_name': 'first', 'device': 'first', 'source': 'first',
            'dt': ['min', 'max'], 'pk': 'count'
        }).reset_index()
        sessions.columns = ['session_id', 'User', 'Device', 'Source', 'Start', 'End', 'Tx Count']
        
        sessions['Duration'] = (sessions['End'] - sessions['Start']).dt.total_seconds()
        sessions['Duration'] = np.where(sessions['Duration'] < 10, 30, sessions['Duration'])
        
        sessions = sessions.sort_values(['User', 'Start'])
        sessions['Next Start'] = sessions.groupby('User')['Start'].shift(-1)
        sessions['Walk Time'] = (sessions['Next Start'] - sessions['End']).dt.total_seconds()
        
        c1, c2, c3 = st.columns(3)
        all_users = sorted(sessions['User'].dropna().unique())
        sel_u = c1.multiselect("Filter User", all_users, key="u_sess_uni")
        min_dur = c2.number_input("Min Duration (sec)", 0, 3600, 0)
        sel_source = c3.multiselect("Filter Source", ["Pyxis", "Pharmacy"], default=["Pyxis", "Pharmacy"])
        
        view = sessions.copy()
        if sel_u: view = view[view['User'].isin(sel_u)]
        if min_dur: view = view[view['Duration'] >= min_dur]
        if sel_source: view = view[view['Source'].isin(sel_source)]
        
        if len(sel_u) == 1:
            st.divider()
            total_active = view['Duration'].sum()
            pyxis_time = view[view['Source'] == 'Pyxis']['Duration'].sum()
            pharm_time = view[view['Source'] == 'Pharmacy']['Duration'].sum()
            top_device = view['Device'].mode()[0] if not view.empty else "N/A"
            
            st.subheader(f"🧠 Shift Analysis: {sel_u[0].title()}")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Total Active Time", seconds_to_mmss(total_active))
            k2.metric("Pyxis Time", seconds_to_mmss(pyxis_time))
            k3.metric("Pharmacy Time", seconds_to_mmss(pharm_time))
            k4.metric("Most Visited", top_device)
            st.divider()

        disp = view.copy().reset_index(drop=True)
        disp['Duration_Str'] = disp['Duration'].apply(seconds_to_mmss)
        disp['Walk Time'] = disp['Walk Time'].apply(seconds_to_mmss)
        disp['Start'] = disp['Start'].dt.strftime('%H:%M:%S')
        disp['End'] = disp['End'].dt.strftime('%H:%M:%S')
        
        st.caption("👆 Click a row to see the exact transactions.")
        event = st.dataframe(
            disp[['session_id', 'User', 'Source', 'Device', 'Start', 'End', 'Tx Count', 'Duration_Str', 'Walk Time']],
            use_container_width=True,
            on_select="rerun", selection_mode="single-row", hide_index=True,
            column_config={"Duration_Str": "Duration"}
        )
        
        if len(event.selection.rows) > 0:
            idx = event.selection.rows[0]
            sel_id = disp.iloc[idx]['session_id']
            details = combined[combined['session_id'] == sel_id].sort_values('dt').copy()
            st.divider()
            st.subheader(f"🔬 Timeline: {details['device'].iloc[0]}")
            st.dataframe(
                details[['dt', 'source', 'event_type', 'med_desc', 'qty']],
                use_container_width=True,
                column_config={"dt": st.column_config.DatetimeColumn("Time", format="HH:mm:ss")}
            )

# 9. PHARMACY WORKFLOW
elif selected_page == "🏥 Pharmacy Workflow":
    if not df_pharm.empty:
        st.markdown("### 🏥 Central Pharmacy Workflow")
        c_filter1, c_filter2 = st.columns(2)
        priorities = sorted(df_pharm['priority'].dropna().unique())
        sel_prio = c_filter1.multiselect("Filter Transaction Type (Priority)", priorities)
        
        view_pharm = df_pharm.copy()
        if sel_prio: view_pharm = view_pharm[view_pharm['priority'].isin(sel_prio)]
            
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Orders", len(view_pharm))
        c2.metric("Critical/STAT", len(view_pharm[view_pharm['priority'].str.contains('STAT|Critical', case=False, na=False)]))
        top_dest = view_pharm['destination'].mode()[0] if not view_pharm['destination'].empty else "N/A"
        c3.metric("Top Destination", top_dest)
        
        st.dataframe(view_pharm, use_container_width=True)
        
        st.divider()
        st.subheader("💡 Par Level Recommendations")
        stockout_only = df_pharm[df_pharm['priority'].str.contains(r'Stock\s*Out|Stockout', case=False, na=False)].copy()
        
        if not stockout_only.empty:
            stockout_agg = stockout_only.groupby(['destination', 'med_id']).agg(
                med_desc=('med_desc', 'first'),
                Stockout_Count=('pk', 'count'),
                Avg_Stockout_Req=('qty', 'mean'),
            ).reset_index().rename(columns={'destination': 'device'})

            if not df_events.empty:
                is_refill = df_events['event_type'].astype(str).str.contains(r'REFILL|LOAD|STOCK|ADD', case=False, na=False)
                refill_stats = df_events[is_refill].groupby(['device', 'med_id'])['qty'].mean().reset_index(name='Avg_Refill_Qty')
                recs = pd.merge(stockout_agg, refill_stats, on=['device', 'med_id'], how='left')
            else:
                recs = stockout_agg.copy()
                recs['Avg_Refill_Qty'] = 0

            recs['Avg_Refill_Qty'] = recs['Avg_Refill_Qty'].fillna(0)
            base_capacity = np.where(recs['Avg_Refill_Qty'] > 0, recs['Avg_Refill_Qty'], recs['Avg_Stockout_Req'])
            recs['Suggested Min'] = np.clip(np.ceil(base_capacity * 1.5), 1, None)
            recs['Suggested Max'] = np.ceil(recs['Suggested Min'] * 2.5)
            
            st.dataframe(recs[['device', 'med_desc', 'Stockout_Count', 'Suggested Min', 'Suggested Max']], use_container_width=True)
        else:
            st.success("No Stockouts found! Par levels look good.")

# 10. RETURN RECONCILIATION
elif selected_page == "🔄 Return Reconciliation":
    st.markdown("### 🔄 Unload vs. Return Reconciliation")
    filter_narc = st.checkbox("Exclude Controlled Substances", value=True)
    if not df_events.empty and not df_pharm.empty:
        unloads = df_events[df_events['event_type'].str.contains(r'unload|empty\s*return', case=False, na=False)].copy()
        returns = df_pharm[df_pharm['priority'] == 'Returns'].copy()
        
        if filter_narc:
            pat = '|'.join(NARC_TERMS)
            unloads = unloads[~unloads['med_desc'].str.contains(pat, case=False, na=False)]
            returns = returns[~returns['med_desc'].str.contains(pat, case=False, na=False)]
            
        unloads['Date'] = unloads['dt'].dt.date
        grp_unload = unloads.groupby(['Date', unloads['med_id'].str.strip().str.upper()]).agg({'qty': 'sum', 'med_desc': 'first'}).reset_index()
        returns['Date'] = returns['dt'].dt.date
        grp_return = returns.groupby(['Date', returns['med_id'].str.strip().str.upper()]).agg({'qty': 'sum'}).reset_index()
        
        merged = pd.merge(grp_unload, grp_return, on=['Date', 'med_id'], how='outer', suffixes=('_floor', '_pharm'))
        merged.fillna(0, inplace=True)
        merged['Variance'] = merged['qty_pharm'] - merged['qty_floor']
        st.dataframe(merged, use_container_width=True)

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
        
        st.dataframe(
            merged[['date_obj', 'staff_name', 'shift_type', 'Status']].sort_values('date_obj', ascending=False),
            use_container_width=True,
            column_config={"date_obj": st.column_config.DateColumn("Date")}
        )
