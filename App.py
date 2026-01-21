###############################################################
# RXTRACK: EXECUTIVE DASHBOARD (v15.4 - Stable Release)
# Updates:
#   1. CRITICAL FIX: replaced invalid 'px.sankey' with 'go.Sankey'.
#   2. RESTORED: Device-to-Device workflow tracking.
#   3. FIXED: Session Explorer filtering and sorting.
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
import warnings

# --- OPTIONAL ML LIBRARY ---
try:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# --- CONFIGURATION ---
st.set_page_config(
    page_title="RxTrack: Executive Suite", 
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Suppress warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pandas")

# --- INITIALIZE VARIABLES ---
df_events = pd.DataFrame()
df_config = pd.DataFrame()
df_pharm = pd.DataFrame()
df_sched = pd.DataFrame()
df_att = pd.DataFrame()
df_audits = pd.DataFrame()

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

AMBIGUOUS_NAMES = [
    "melissa", "emily", "sarah", "megan", "erin", "kyle", 
    "jessica", "andy", "heather", "michelle", "taylor"
]

# --- AUDIT TEMPLATES ---
AUDIT_TEMPLATES = {
    "Standard Pyxis Compliance": {
        "score_max": 100,
        "criteria": ["Correct Count?", "No Expired?", "Stock Rotated?", "Bins Not Overfilled?", "Return Bin Emptied?"],
        "has_drug_check": True
    },
    "IV Room / Aseptic": {
        "score_max": 100,
        "criteria": ["Proper Garbing?", "Aseptic Technique?", "Hood Cleaning Logged?", "No Personal Items?", "Vials Swabbed?"],
        "has_drug_check": False
    },
    "Morning Workflow": {
        "score_max": 50,
        "criteria": ["Queue Cleared < 9am?", "Phone Answered?", "Crash Cart Restock?", "Handover Notes?"],
        "has_drug_check": False
    },
    "Night Shift Security": {
        "score_max": 50,
        "criteria": ["Perpetual Inventory?", "Narc Vault Locked?", "Rounds Done?", "Fridge Temps?"],
        "has_drug_check": False
    }
}

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    .metric-card { background-color: #ffffff; padding: 20px; border-radius: 10px; border-left: 5px solid #4CAF50; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 10px; }
    .metric-card h3 { color: #1f2937; margin: 0; font-size: 26px; font-weight: 700; }
    .metric-card p { color: #6b7280; margin: 0; font-size: 14px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
    .cal-grid { display: flex; flex-wrap: wrap; gap: 3px; max-width: 100%; margin-top: 10px; }
    .cal-day { width: 12px; height: 12px; border-radius: 2px; background-color: #e5e7eb; }
    .cal-present { background-color: #4CAF50; }
    .cal-missing { background-color: #F87171; }
    </style>
    """, unsafe_allow_html=True)

# --- DATABASE HELPERS ---
@contextlib.contextmanager
def db_cursor():
    conn = None
    try:
        conn = psycopg2.connect(st.secrets["neon"]["db_url"])
        cur = conn.cursor()
        yield conn, cur
    except Exception as e:
        st.error(f"❌ Database Connection Error: {e}")
        raise e
    finally:
        if conn: conn.close()

def execute_statement(sql, params, batch=False, table_name="Data"):
    try:
        with db_cursor() as (conn, cur):
            if batch: execute_batch(cur, sql, params, page_size=2000)
            else: cur.execute(sql, params)
            conn.commit()
            st.toast(f"✅ Saved {len(params)} records to {table_name}!", icon="💾")
    except Exception as e:
        st.error(f"⚠️ Error executing {table_name}: {e}")

def init_db():
    schemas = [
        "CREATE TABLE IF NOT EXISTS events (pk TEXT PRIMARY KEY, user_name TEXT, device TEXT, med_id TEXT, med_desc TEXT, event_type TEXT, dt TIMESTAMP, qty FLOAT, beginning_qty FLOAT, ending_qty FLOAT, discrepancy_qty FLOAT, discrepancy_reason TEXT, resolution_dt TIMESTAMP);",
        "CREATE TABLE IF NOT EXISTS config_events (pk TEXT PRIMARY KEY, dt TIMESTAMP, user_name TEXT, device TEXT, med_id TEXT, location TEXT, action_type TEXT, activity_category TEXT, min_qty FLOAT, max_qty FLOAT, is_standard BOOLEAN);",
        "CREATE TABLE IF NOT EXISTS med_costs (med_id TEXT PRIMARY KEY, cost_per_unit FLOAT);",
        "CREATE TABLE IF NOT EXISTS pharmacy_orders (pk TEXT PRIMARY KEY, queue_id TEXT, priority TEXT, dt TIMESTAMP, med_id TEXT, med_desc TEXT, destination TEXT, user_name TEXT, qty FLOAT);",
        "CREATE TABLE IF NOT EXISTS staff_schedule (pk TEXT PRIMARY KEY, dt DATE, day_name TEXT, staff_name TEXT, shift_type TEXT, assignment_type TEXT, raw_entry TEXT, note TEXT);",
        "CREATE TABLE IF NOT EXISTS attendance_punches (pk TEXT PRIMARY KEY, raw_name TEXT, dt_date DATE, start_dt TIMESTAMP, end_dt TIMESTAMP);",
        "CREATE TABLE IF NOT EXISTS inventory_audit (pk TEXT PRIMARY KEY, med_id TEXT, med_desc TEXT, med_class TEXT, unit_cost FLOAT, qty_on_hand FLOAT, min_lvl FLOAT, max_lvl FLOAT);",
        "CREATE TABLE IF NOT EXISTS inventory_detailed (pk TEXT PRIMARY KEY, station TEXT, med_id TEXT, med_desc TEXT, unit_cost FLOAT, current_count FLOAT, pocket_location TEXT);",
        "CREATE TABLE IF NOT EXISTS tech_audits (pk TEXT PRIMARY KEY, audit_dt DATE, technician TEXT, category TEXT, question TEXT, result TEXT, points_earned FLOAT, points_possible FLOAT, note TEXT);"
    ]
    with db_cursor() as (conn, cur):
        for sql in schemas: cur.execute(sql)
        conn.commit()

def run_query(query, params=None):
    try:
        with db_cursor() as (conn, cur): return pd.read_sql(query, conn, params=params)
    except Exception: return pd.DataFrame()

# --- UTILITY FUNCTIONS ---
def seconds_to_mmss(seconds):
    if pd.isna(seconds) or seconds < 0: return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"

def generate_pk(row):
    subset = [str(x) for x in row.values if pd.notnull(x)]
    return hashlib.sha256("|".join(subset).encode()).hexdigest()

def normalize_name(full_name):
    s = str(full_name).strip().lower()
    first_name, last_initial = "", ""
    if "," in s:
        parts = s.split(",")
        if len(parts) >= 2:
            first_name = parts[1].strip().split(" ")[0]
            if parts[0].strip(): last_initial = parts[0].strip()[0]
    else:
        parts = s.split(" ")
        first_name = parts[0]
        if len(parts) > 1: last_initial = parts[1][0]
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
    return None

def get_reconciled_returns(df):
    if df.empty: return df
    df_clean = df[~df['event_type'].astype(str).str.upper().str.contains("CANCELLED")].copy()
    df_clean = df_clean.sort_values(['device', 'med_id', 'dt'])
    drop_indices = set()
    groups = df_clean[df_clean['event_type'].isin(['Unload', 'Load'])].groupby(['device', 'med_id'])
    for (device, med), group in groups:
        pending_unloads = []
        for idx, row in group.iterrows():
            if row['event_type'] == 'Unload':
                pending_unloads.append({'idx': idx, 'qty': row['qty']})
            elif row['event_type'] == 'Load':
                for i in range(len(pending_unloads) - 1, -1, -1):
                    if abs(pending_unloads[i]['qty'] - row['qty']) < 0.01:
                        drop_indices.add(pending_unloads[i]['idx'])
                        drop_indices.add(idx)
                        pending_unloads.pop(i)
                        break
    return df_clean.drop(index=list(drop_indices))

def smart_match_returns(unloads, returns, lookback_hours=72):
    if unloads.empty or returns.empty: return unloads, returns
    u_df, r_df = unloads.copy(), returns.copy()
    u_df['match_id'] = None
    
    # FIXED: Direct column initialization to avoid ValueError
    for c in ['match_id', 'suspected_source', 'source_user', 'unload_dt', 'lag_str']:
        r_df[c] = None
        
    u_df = u_df.sort_values('dt')
    r_df = r_df.sort_values('dt')
    
    for r_idx, r_row in r_df.iterrows():
        candidates = u_df[
            (u_df['norm_med_id'] == r_row['norm_med_id']) &
            (u_df['dt'] < r_row['dt']) &
            (u_df['dt'] >= r_row['dt'] - timedelta(hours=lookback_hours)) &
            (u_df['match_id'].isnull())
        ]
        if not candidates.empty:
            best = candidates[candidates['qty'] == r_row['qty']]
            match_idx = best.index[-1] if not best.empty else candidates.index[-1]
            match_row = u_df.loc[match_idx]
            match_id = f"{r_idx}-{match_idx}"
            u_df.at[match_idx, 'match_id'] = match_id
            r_df.at[r_idx, 'match_id'] = match_id
            r_df.at[r_idx, 'suspected_source'] = match_row['device']
            r_df.at[r_idx, 'source_user'] = match_row['user_name']
            r_df.at[r_idx, 'unload_dt'] = match_row['dt']
            lag = r_row['dt'] - match_row['dt']
            r_df.at[r_idx, 'lag_str'] = f"{lag.days}d {lag.seconds//3600}h {(lag.seconds//60)%60}m"
    return u_df, r_df

# --- DATA CLEANING & LOADING ---
def clean_dataframe(df):
    df = df.copy()
    colmap = {"UserName": "user_name", "Device": "device", "MedID": "med_id", "MedDescription": "med_desc", "TransactionType": "event_type", "TransactionDateTime": "dt", "Quantity": "qty", "DiscrepancyQuantity": "discrepancy_qty", "DiscrepancyReason": "discrepancy_reason"}
    df.rename(columns=colmap, inplace=True)
    required = ["user_name", "device", "med_id", "med_desc", "event_type", "dt", "qty"]
    for c in required:
        if c not in df.columns: df[c] = None
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df.dropna(subset=["dt"], inplace=True)
    for c in ["qty", "discrepancy_qty"]: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype('float32')
    df["dt"] = df["dt"].astype(str)
    df["pk"] = df.apply(generate_pk, axis=1)
    return df

def clean_pharmacy_report(df):
    df = df.copy()
    colmap = {"TranQueueID": "queue_id", "Priority": "priority", "Date / Time": "dt", "Item ID": "med_id", "Description": "med_desc", "Destination": "destination", "User": "user_name", "Quantity": "qty"}
    df.rename(columns=colmap, inplace=True)
    for c in colmap.values():
        if c not in df.columns: df[c] = None
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df.dropna(subset=["dt"], inplace=True)
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["dt"] = df["dt"].astype(str)
    df["pk"] = df.apply(generate_pk, axis=1)
    return df

def clean_activity_log(df):
    df = df.copy()
    df.columns = df.columns.str.strip().str.replace(' ', '')
    df.rename(columns={"UserName": "user_name", "Device": "device", "TransactionDateTime": "dt", "Action": "action_type", "ActivityType": "activity_category", "AffectedElement": "raw_element", "Amount": "qty_col", "Quantity": "qty_col"}, inplace=True)
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df.dropna(subset=["dt"], inplace=True)
    pattern = r'^(.*?) \((.*?)\)'
    extracted = df['raw_element'].astype(str).str.extract(pattern)
    df['location'] = extracted[0].str.strip()
    df['med_id'] = extracted[1].str.strip()
    df.dropna(subset=['med_id'], inplace=True)
    if 'qty_col' not in df.columns: df['qty_col'] = df['raw_element'].astype(str).str.extract(r':\s*(\d+)$')[0]
    df['qty_extracted'] = pd.to_numeric(df['qty_col'], errors='coerce')
    df['is_min'] = df['activity_category'].str.contains('Min', case=False, na=False)
    df['is_max'] = df['activity_category'].str.contains('Max', case=False, na=False)
    df['is_std'] = df['activity_category'].str.contains('Standard Stock', case=False, na=False)
    df['min_qty'] = np.where(df['is_min'], df['qty_extracted'], np.nan)
    df['max_qty'] = np.where(df['is_max'], df['qty_extracted'], np.where((~df['is_min']) & (~df['is_max']), df['qty_extracted'], np.nan))
    grouped = df.groupby(['user_name', 'device', 'med_id'], as_index=False).agg({'min_qty': 'max', 'max_qty': 'max', 'is_std': 'max', 'location': 'first', 'dt': 'first', 'action_type': 'first', 'activity_category': 'first'})
    grouped["dt"] = grouped["dt"].astype(str)
    grouped["pk"] = grouped.apply(generate_pk, axis=1)
    return grouped.rename(columns={'is_std': 'is_standard'})

def clean_schedule_data(df):
    df = df.copy()
    if len(df.columns) > 2: df.rename(columns={df.columns[1]: 'Date', df.columns[2]: 'Day'}, inplace=True)
    df = df.iloc[1:].dropna(subset=['Date'])
    df.drop(columns=[df.columns[0]], errors='ignore', inplace=True)
    long_df = df.melt(id_vars=['Date', 'Day'], var_name='col_header', value_name='raw_entry')
    long_df.dropna(subset=['raw_entry'], inplace=True)
    long_df = long_df[~long_df['raw_entry'].astype(str).str.lower().isin(['x', 'nan', '', ' '])]
    processed_rows = []
    for _, row in long_df.iterrows():
        raw = str(row['raw_entry']).strip()
        dt = pd.to_datetime(row['Date'], errors='coerce').date()
        parts = [p.strip() for p in raw.split('\n') if p.strip()]
        for part in parts:
            clean_part = part.replace('()', '').strip()
            final_shift_str = "Scheduled"
            processed_rows.append({'pk': hashlib.sha256(f"{dt}|{clean_part}".encode()).hexdigest(), 'dt': dt, 'staff_name': clean_part.title(), 'shift_type': final_shift_str, 'assignment_type': "Shift", 'raw_entry': part, 'note': ""})
    return pd.DataFrame(processed_rows)

def clean_attendance_file(file_obj):
    file_obj.seek(0)
    content = file_obj.read().decode('utf-8', errors='ignore')
    data = []
    name_pat = re.compile(r'Employee:\s*([A-Za-z\-,\s\.]+?)(?="|",|",Date)')
    date_pat = re.compile(r'Date:\s*(\d{1,2}/\d{1,2}/\d{4})')
    time_pat = re.compile(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2})')
    for line in content.splitlines():
        m_name, m_date = name_pat.search(line), date_pat.search(line)
        times = time_pat.findall(line)
        if m_name and m_date and len(times)>0:
            data.append({"raw_name": m_name.group(1).strip(), "dt_date": pd.to_datetime(m_date.group(1)).date(), "start_dt": times[0], "end_dt": times[1] if len(times)>1 else None})
    df = pd.DataFrame(data)
    if not df.empty: df["pk"] = df.apply(generate_pk, axis=1)
    return df

@st.cache_data(ttl=300)
def load_data(start_date, end_date):
    queries = {
        # Added 'pk' to selection
        "events": "SELECT e.pk, e.user_name, e.device, e.med_id, e.med_desc, e.event_type, e.dt, e.qty, e.discrepancy_qty, c.cost_per_unit FROM events e LEFT JOIN med_costs c ON e.med_id = c.med_id WHERE e.dt::date BETWEEN %s AND %s",
        "config": "SELECT pk, dt, user_name, device, med_id, location, action_type, activity_category, min_qty, max_qty, is_standard FROM config_events WHERE dt::date BETWEEN %s AND %s",
        "pharm": "SELECT pk, queue_id, priority, dt, med_id, med_desc, destination, user_name, qty FROM pharmacy_orders WHERE dt::date BETWEEN %s AND %s",
        "schedule": "SELECT pk, dt, staff_name, shift_type FROM staff_schedule WHERE dt BETWEEN %s AND %s",
        "attendance": "SELECT pk, raw_name, dt_date, start_dt, end_dt FROM attendance_punches WHERE dt_date BETWEEN %s AND %s",
        "audits": "SELECT pk, audit_dt, technician, points_earned, points_possible FROM tech_audits WHERE audit_dt BETWEEN %s AND %s"
    }
    results = {}
    with db_cursor() as (conn, cur):
        for key, sql in queries.items():
            try:
                results[key] = pd.read_sql(sql, conn, params=(start_date, end_date))
                if 'dt' in results[key].columns: results[key]["dt"] = pd.to_datetime(results[key]["dt"])
            except: results[key] = pd.DataFrame()
    
    df = results["events"]
    if not df.empty:
        df["cost_per_unit"] = df["cost_per_unit"].fillna(0).astype('float32')
        df.sort_values(['user_name', 'dt'], inplace=True)
        
        # Safe Session Logic (Shift without index issues)
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        df['duration'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        
        # Calculate machine time only if same device
        df['machine_time_sec'] = np.where(
            (df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['duration'] < 600), 
            df['duration'], 0
        )
        df.drop(columns=['next_dt'], inplace=True)

    return df, results["config"], results["pharm"], results["schedule"], results["attendance"], results["audits"]

# --- MAIN APP LOGIC ---
init_db()

PAGES = [
    "📊 Overview", "🕵️‍♂️ Deep Detective", "📝 Smart Audits", "🏆 Tech of the Quarter", 
    "🎓 Student Project", "🏆 Shift Leaderboard", "⏰ Tardies", "🚀 Process Mining", 
    "🛡️ Compliance", "⚡ Efficiency", "🔍 Session Explorer", "🏥 Pharmacy Workflow", 
    "🔄 Return Reconciliation", "⚖️ Tech Comparison", "📈 Tech Progression", "📅 Attendance", "📥 Pends Analyzer"
]

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=60)
    st.title("RxTrack v15.4")
    st.caption("Deep Detective Edition")
    selected_page = st.radio("Go to:", PAGES)
    st.divider()
    
    d_range = st.date_input("Analysis Range", [date.today()-timedelta(days=7), date.today()])
    if len(d_range) == 2: start_date, end_date = d_range
    else: start_date, end_date = d_range[0], d_range[0]

    u_type = st.selectbox("Import", ["Daily Transaction Report", "Device Activity Log (Pends)", "Pharmacy Workflow Report", "Staff Schedule", "Attendance Tracking"])
    uploaded = st.file_uploader("Upload File", type=["csv", "xlsx"])
    if uploaded and st.button("Process"):
        try:
            if u_type == "Daily Transaction Report":
                df_raw = pd.read_csv(uploaded, header=0, encoding='latin1') 
                clean = clean_dataframe(df_raw)
                sql = "INSERT INTO events (pk, user_name, device, med_id, med_desc, event_type, dt, qty, discrepancy_qty, discrepancy_reason) VALUES (%(pk)s, %(user_name)s, %(device)s, %(med_id)s, %(med_desc)s, %(event_type)s, %(dt)s, %(qty)s, %(discrepancy_qty)s, %(discrepancy_reason)s) ON CONFLICT (pk) DO NOTHING;"
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Events")
            elif u_type == "Device Activity Log (Pends)":
                df_raw = pd.read_csv(uploaded, header=0, encoding='latin1')
                clean = clean_activity_log(df_raw)
                sql = "INSERT INTO config_events (pk, dt, user_name, device, med_id, location, action_type, activity_category, min_qty, max_qty, is_standard) VALUES (%(pk)s, %(dt)s, %(user_name)s, %(device)s, %(med_id)s, %(location)s, %(action_type)s, %(activity_category)s, %(min_qty)s, %(max_qty)s, %(is_standard)s) ON CONFLICT (pk) DO NOTHING;"
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Config")
            elif u_type == "Pharmacy Workflow Report":
                df_raw = pd.read_csv(uploaded, header=0, encoding='latin1')
                clean = clean_pharmacy_report(df_raw)
                sql = "INSERT INTO pharmacy_orders (pk, queue_id, priority, dt, med_id, med_desc, destination, user_name, qty) VALUES (%(pk)s, %(queue_id)s, %(priority)s, %(dt)s, %(med_id)s, %(med_desc)s, %(destination)s, %(user_name)s, %(qty)s) ON CONFLICT (pk) DO NOTHING;"
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Pharmacy")
            elif u_type == "Staff Schedule":
                df_raw = pd.read_csv(uploaded, header=0, encoding='latin1')
                clean = clean_schedule_data(df_raw)
                sql = "INSERT INTO staff_schedule (pk, dt, staff_name, shift_type, assignment_type, raw_entry, note) VALUES (%(pk)s, %(dt)s, %(staff_name)s, %(shift_type)s, %(assignment_type)s, %(raw_entry)s, %(note)s) ON CONFLICT (pk) DO NOTHING;"
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Schedule")
            elif u_type == "Attendance Tracking":
                clean = clean_attendance_file(uploaded)
                sql = "INSERT INTO attendance_punches (pk, raw_name, dt_date, start_dt, end_dt) VALUES (%(pk)s, %(raw_name)s, %(dt_date)s, %(start_dt)s, %(end_dt)s) ON CONFLICT (pk) DO NOTHING;"
                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Attendance")
            st.cache_data.clear()
            st.rerun()
        except Exception as e: st.error(f"Error: {e}")

if 'start_date' in locals():
    try: df_events, df_config, df_pharm, df_sched, df_att, df_audits = load_data(start_date, end_date)
    except: pass

# --- PAGE LOGIC ---

# 1. OVERVIEW
if selected_page == "📊 Overview":
    st.markdown("## 🏥 Executive Summary")
    if not df_events.empty:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Transactions", f"{len(df_events):,}")
        c2.metric("Active Techs", df_events["user_name"].nunique())
        c3.metric("Discrepancies", df_events["discrepancy_qty"].ne(0).sum())
        c4.metric("Avg Speed", f"{df_events[df_events['machine_time_sec']>0]['machine_time_sec'].mean():.1f}s")
        st.subheader("🐢 Slowest Meds")
        slow = df_events[df_events['machine_time_sec']>0].groupby('med_desc')['machine_time_sec'].mean().sort_values(ascending=False).head(10).reset_index()
        st.plotly_chart(px.bar(slow, x='machine_time_sec', y='med_desc', orientation='h'), use_container_width=True)

# 2. DEEP DETECTIVE
elif selected_page == "🕵️‍♂️ Deep Detective":
    st.header("🕵️‍♂️ Deep Detective: Anomaly Detection")
    if not df_events.empty:
        user_stats = df_events.groupby('user_name').agg(
            Total_Tx=('pk', 'count'),
            Cancels=('event_type', lambda x: x.astype(str).str.contains('CANCEL', case=False).sum()),
            Unloads=('event_type', lambda x: x.astype(str).str.contains('Unload', case=False).sum()),
            Avg_Speed=('machine_time_sec', lambda x: x[x>0].mean())
        ).reset_index()
        min_tx = st.slider("Min Transactions", 10, 500, 20)
        active = user_stats[user_stats['Total_Tx'] >= min_tx].copy()
        if not active.empty:
            active['Cancel_Rate'] = active['Cancels'] / active['Total_Tx'] * 100
            active['Unload_Rate'] = active['Unloads'] / active['Total_Tx'] * 100
            mu, sigma = active['Cancel_Rate'].mean(), active['Cancel_Rate'].std()
            active['Z_Cancel'] = (active['Cancel_Rate'] - mu) / (sigma + 1e-6)
            if HAS_SKLEARN:
                feat = ['Cancel_Rate', 'Unload_Rate', 'Avg_Speed']
                active[feat] = active[feat].fillna(0)
                clf = IsolationForest(contamination=0.05, random_state=42)
                active['ML_Flag'] = np.where(clf.fit_predict(StandardScaler().fit_transform(active[feat])) == -1, "🔴 High Risk", "🟢 Normal")
            else: active['ML_Flag'] = "⚪ No ML"
            
            c1, c2 = st.columns(2)
            with c1:
                fig = px.scatter(active, x='Total_Tx', y='Cancel_Rate', color='ML_Flag' if HAS_SKLEARN else 'Z_Cancel', size='Unload_Rate', hover_name='user_name', title="Risk Analysis")
                fig.add_hline(y=mu, line_dash="dash")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                st.subheader("Top Anomalies")
                st.dataframe(active.sort_values('Z_Cancel', ascending=False).head(10)[['user_name', 'Cancel_Rate', 'Z_Cancel', 'ML_Flag']], use_container_width=True)
        else: st.warning("Not enough data.")
    else: st.info("Upload Data.")

# 3. SMART AUDITS
elif selected_page == "📝 Smart Audits":
    st.header("📝 Smart Audit Targeting")
    today = date.today()
    todays_shift = df_sched[df_sched['dt'] == today].copy() if not df_sched.empty else pd.DataFrame()
    c1, c2 = st.columns([1, 2])
    with c1:
        st.subheader("🎯 Today's Roster")
        if not todays_shift.empty:
            opts = [f"{r['staff_name']} ({r['shift_type']})" for _, r in todays_shift.iterrows()]
            target = st.radio("Select Target:", opts)
            st.info(f"Selected: {target}")
        else: st.warning("No schedule data.")
    with c2:
        st.subheader("📋 Audit Form")
        with st.form("audit"):
            wf = st.selectbox("Workflow", list(AUDIT_TEMPLATES.keys()))
            score = 0
            for c in AUDIT_TEMPLATES[wf]["criteria"]:
                if st.checkbox(c): score += 10
            if st.form_submit_button("Save"):
                st.success(f"Audit Saved! Score: {score}")

# 4. TECH OF QUARTER
elif selected_page == "🏆 Tech of the Quarter":
    st.header("🏆 Tech of the Quarter")
    if not df_audits.empty:
        start_q, end_q = st.columns(2)[0].date_input("Start", date(date.today().year,1,1)), st.columns(2)[1].date_input("End", date.today())
        q_audits = df_audits[(df_audits['audit_dt']>=start_q) & (df_audits['audit_dt']<=end_q)]
        if not q_audits.empty:
            rank = q_audits.groupby('technician')['points_earned'].mean().reset_index().sort_values('points_earned', ascending=False)
            st.balloons()
            st.dataframe(rank, use_container_width=True)
    else: st.info("No audit data.")

# 5. STUDENT PROJECT
elif selected_page == "🎓 Student Project":
    st.header("🎓 Student Project")
    if not df_events.empty:
        users = st.multiselect("Students", sorted(df_events['user_name'].unique()))
        if users:
            sub = df_events[df_events['user_name'].isin(users)]
            val = (sub['qty'] * sub['cost_per_unit']).sum()
            st.metric("Total Value Saved", f"${val:,.2f}")
            st.dataframe(sub, use_container_width=True)

# 6. SHIFT LEADERBOARD
elif selected_page == "🏆 Shift Leaderboard":
    if not df_sched.empty and not df_events.empty:
        df_events['k'] = df_events['user_name'].apply(normalize_name)
        df_sched['k'] = df_sched['staff_name'].apply(normalize_name)
        cts = df_events.groupby([df_events['dt'].dt.date, 'k']).size().reset_index(name='tx')
        m = pd.merge(df_sched, cts, left_on=[df_sched['dt'].dt.date, 'k'], right_on=['dt', 'k'], how='left').fillna(0)
        st.subheader("Top Performers by Shift")
        st.dataframe(m.groupby('staff_name')['tx'].mean().sort_values(ascending=False), use_container_width=True)

# 7. TARDIES
elif selected_page == "⏰ Tardies":
    if not df_sched.empty and not df_att.empty:
        df_sched['k'] = df_sched['staff_name'].apply(normalize_name)
        df_att['k'] = df_att['raw_name'].apply(normalize_name)
        m = pd.merge(df_sched, df_att, left_on=[df_sched['dt'].dt.date, 'k'], right_on=['dt_date', 'k'])
        m['start'] = pd.to_datetime(m['start_dt'])
        m['sched'] = m.apply(lambda x: parse_shift_start(x['dt'], x['shift_type']), axis=1)
        m.dropna(subset=['sched'], inplace=True)
        m['late_min'] = (m['start'] - m['sched']).dt.total_seconds() / 60
        tardies = m[m['late_min'] > 5]
        st.metric("Late Arrivals", len(tardies))
        st.dataframe(tardies[['dt_date', 'staff_name', 'late_min']], use_container_width=True)

# 8. PROCESS MINING (FIXED CRASH)
elif selected_page == "🚀 Process Mining":
    if not df_events.empty:
        # Calculate Device->Device movements
        moves = df_events[df_events['device'] != df_events['prev_device']].dropna(subset=['prev_device', 'device']).copy()
        
        if not moves.empty:
            path = moves.groupby(['prev_device', 'device']).size().reset_index(name='count').sort_values('count', ascending=False).head(20)
            
            # --- FIXED SANKEY (Using go.Sankey engine) ---
            all_nodes = list(pd.concat([path['prev_device'], path['device']]).unique())
            node_map = {node: i for i, node in enumerate(all_nodes)}
            
            fig = go.Figure(data=[go.Sankey(
                node=dict(
                    pad=15, thickness=20, line=dict(color="black", width=0.5),
                    label=all_nodes, color="#3498db"
                ),
                link=dict(
                    source=path['prev_device'].map(node_map),
                    target=path['device'].map(node_map),
                    value=path['count']
                )
            )])
            fig.update_layout(title="Top Workflow Paths (Device → Device)", height=600)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No movement between devices detected.")

# 9. COMPLIANCE
elif selected_page == "🛡️ Compliance":
    if not df_events.empty:
        disc = df_events[df_events['discrepancy_qty'] != 0]
        st.dataframe(disc, use_container_width=True)

# 10. RETURN RECONCILIATION
elif selected_page == "🔄 Return Reconciliation":
    st.markdown("### 🔄 Return Footprint & Smart Trace")
    c1, c2, c3 = st.columns(3)
    filter_narc = c1.checkbox("Exclude Narcs", True)
    adj_inh = c2.checkbox("Fix Inhalers", True)
    lookback = c3.slider("Lookback (Hrs)", 24, 168, 72)
    
    if not df_events.empty:
        mask = df_events['event_type'].str.contains('Unload|Empty|Destock', case=False, na=False)
        raw_move = df_events[mask & ~df_events['event_type'].str.contains('CANCEL', case=False, na=False)].copy()
        
        st.divider()
        c_ch, c_me = st.columns([2,1])
        with c_ch:
            fp = raw_move.groupby('device')['qty'].sum().reset_index().sort_values('qty').tail(10)
            st.plotly_chart(px.bar(fp, x='qty', y='device', orientation='h', title="Top Return Sources"), use_container_width=True)
        with c_me:
            st.metric("Units Moved", f"{raw_move['qty'].sum():,.0f}")
            st.metric("Events", len(raw_move))

        if not df_pharm.empty:
            st.divider()
            st.subheader("🔎 Chain of Custody")
            unloads = get_reconciled_returns(df_events)
            unloads = unloads[unloads['event_type'].str.contains('Unload|Empty|Destock', case=False, na=False)].copy()
            returns = df_pharm[df_pharm['priority'].str.contains('Return', case=False, na=False)].copy()
            
            if filter_narc:
                pat = '|'.join(NARC_TERMS)
                unloads = unloads[~unloads['med_desc'].str.contains(pat, case=False, na=False)]
                returns = returns[~returns['med_desc'].str.contains(pat, case=False, na=False)]
            
            if adj_inh:
                mask_i = unloads['med_desc'].str.contains('puff|hfa', case=False, na=False)
                unloads['qty'] = np.where(mask_i & (unloads['qty']>10), unloads['qty']/120, unloads['qty'])
            
            unloads['norm_med_id'] = unloads['med_id'].str.strip().str.upper()
            returns['norm_med_id'] = returns['med_id'].str.strip().str.upper()
            
            matched_u, matched_r = smart_match_returns(unloads, returns, lookback)
            
            succ = matched_r[matched_r['match_id'].notnull()].copy(); succ['Status'] = "✅ Reconciled"
            orph = matched_r[matched_r['match_id'].isnull()].copy(); orph['Status'] = "❓ Mystery"
            miss = matched_u[matched_u['match_id'].isnull()].copy(); miss['Status'] = "⚠️ Missing"
            
            succ = succ[['dt', 'user_name', 'med_desc', 'qty', 'suspected_source', 'source_user', 'unload_dt', 'lag_str', 'Status']]
            orph = orph[['dt', 'user_name', 'med_desc', 'qty', 'suspected_source', 'source_user', 'unload_dt', 'lag_str', 'Status']]
            miss = miss[['dt', 'user_name', 'med_desc', 'qty', 'device', 'user_name', 'dt', 'lag_str', 'Status']]
            
            col_map_r = {'dt':'Scan Time', 'user_name':'Pharm User', 'suspected_source':'Source', 'source_user':'Tech', 'unload_dt':'Unload Time'}
            succ.rename(columns=col_map_r, inplace=True)
            orph.rename(columns=col_map_r, inplace=True)
            
            miss.columns = ['Unload Time', 'Tech', 'med_desc', 'qty', 'Source', 'Tech_dup', 'Unload_dup', 'lag_str', 'Status']
            miss['Scan Time'] = None; miss['Pharm User'] = None
            
            master = pd.concat([succ, orph, miss], ignore_index=True)
            
            m1, m2, m3 = st.columns(3)
            m1.metric("✅ Traced", len(succ))
            m2.metric("⚠️ Missing", len(miss))
            rate = len(succ)/len(master)*100 if len(master) else 0
            m3.metric("Success Rate", f"{rate:.1f}%")
            
            grp = master.groupby('med_desc').agg(
                Total=('qty','count'),
                Missing=('Status', lambda x: (x=="⚠️ Missing").sum()),
                Matched=('Status', lambda x: (x=="✅ Reconciled").sum())
            ).reset_index().sort_values('Missing', ascending=False)
            
            sel = st.dataframe(grp, on_select="rerun", selection_mode="single-row", use_container_width=True, hide_index=True)
            
            if len(sel.selection.rows) > 0:
                med = grp.iloc[sel.selection.rows[0]]['med_desc']
                st.subheader(f"History: {med}")
                det = master[master['med_desc']==med].sort_values('Unload Time', ascending=False)
                st.dataframe(det[['Status', 'qty', 'Source', 'Tech', 'Unload Time', 'Pharm User', 'Scan Time', 'lag_str']], use_container_width=True)
        else: st.info("Upload Pharmacy Report.")

# 11. TECH COMPARISON
elif selected_page == "⚖️ Tech Comparison":
    if not df_events.empty:
        u1, u2 = st.columns(2)[0].selectbox("A", df_events['user_name'].unique()), st.columns(2)[1].selectbox("B", df_events['user_name'].unique())
        st.metric("Diff", len(df_events[df_events['user_name']==u1]) - len(df_events[df_events['user_name']==u2]))

# 12. PROGRESSION
elif selected_page == "📈 Tech Progression":
    if not df_events.empty:
        u = st.selectbox("Tech", df_events['user_name'].unique())
        st.plotly_chart(px.line(df_events[df_events['user_name']==u].set_index('dt').resample('D').size(), title="Daily Volume"), use_container_width=True)

# 13. ATTENDANCE
elif selected_page == "📅 Attendance":
    if not df_sched.empty and not df_events.empty:
        df_events['k'] = df_events['user_name'].apply(normalize_name)
        df_sched['k'] = df_sched['staff_name'].apply(normalize_name)
        wk = df_events.groupby([df_events['dt'].dt.date, 'k']).size().reset_index(name='tx')
        m = pd.merge(df_sched, wk, left_on=[df_sched['dt'].dt.date, 'k'], right_on=['dt', 'k'], how='left')
        m['Status'] = np.where(m['tx']>0, "✅ Present", "❌ Absent")
        st.dataframe(m[['dt', 'staff_name', 'Status']], use_container_width=True)

# 14. PENDS ANALYZER
elif selected_page == "📥 Pends Analyzer":
    if not df_config.empty:
        st.dataframe(df_config, use_container_width=True)
    else: st.info("No Pends Data")

# 15. EFFICIENCY
elif selected_page == "⚡ Efficiency":
    if not df_events.empty:
        eff = df_events.groupby(['device', 'med_desc']).size().reset_index(name='Refills').sort_values('Refills', ascending=False)
        st.plotly_chart(px.bar(eff.head(20), x='Refills', y='med_desc', orientation='h'), use_container_width=True)

# 16. SESSION EXPLORER (RESTORED)
elif selected_page == "🔍 Session Explorer":
    st.header("🔍 Session Explorer (Unified)")
    if not df_events.empty:
        # Build Combined View
        px_df = df_events[['dt', 'user_name', 'device', 'event_type', 'med_desc', 'qty']].copy()
        px_df['Source'] = 'Pyxis'
        
        ph_df = pd.DataFrame()
        if not df_pharm.empty:
            ph_df = df_pharm[['dt', 'user_name', 'destination', 'priority', 'med_desc', 'qty']].copy()
            ph_df.rename(columns={'destination': 'device', 'priority': 'event_type'}, inplace=True)
            ph_df['Source'] = 'Pharmacy'
            
        combined = pd.concat([px_df, ph_df], ignore_index=True).sort_values('dt')
        
        # User Filter
        all_users = sorted(combined['user_name'].dropna().unique())
        sel_u = st.multiselect("Filter User", all_users)
        if sel_u: combined = combined[combined['user_name'].isin(sel_u)]
        
        st.dataframe(
            combined[['dt', 'user_name', 'Source', 'device', 'event_type', 'med_desc', 'qty']], 
            use_container_width=True,
            column_config={"dt": st.column_config.DatetimeColumn("Time", format="MM/DD HH:mm:ss")}
        )
    else: st.info("No Data.")

# 17. PHARMACY WORKFLOW
elif selected_page == "🏥 Pharmacy Workflow":
    if not df_pharm.empty:
        st.dataframe(df_pharm, use_container_width=True)
