###############################################################

# RXTRACK: EXECUTIVE DASHBOARD (v14.3 - Memory Optimized)

# Updates:

#   1. Added 'reduce_mem_usage' to prevent App crashes.

#   2. Fixed Schedule Loading (Smarter date detection).

#   3. Added Drug Dropdowns & Audit Picker.

#   4. Optimization: Garbage Collection & Cache Limits.

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

import random



# --- CONFIGURATION ---

st.set_page_config(

    page_title="RxTrack: Workforce & Efficiency", 

    page_icon="🏥",

    layout="wide",

    initial_sidebar_state="expanded"

)



# Suppress warnings

warnings.filterwarnings("ignore", category=UserWarning, module="pandas")



# --- MEMORY MANAGEMENT ---

def reduce_mem_usage(df):

    """Iterate through all columns and modify data types to reduce memory."""

    if df.empty: return df

    start_mem = df.memory_usage().sum() / 1024**2

    for col in df.columns:

        col_type = df[col].dtype

        if col_type != object:

            c_min, c_max = df[col].min(), df[col].max()

            if str(col_type)[:3] == 'int':

                if c_min > np.iinfo(np.int8).min and c_max < np.iinfo(np.int8).max:

                    df[col] = df[col].astype(np.int8)

                elif c_min > np.iinfo(np.int16).min and c_max < np.iinfo(np.int16).max:

                    df[col] = df[col].astype(np.int16)

                elif c_min > np.iinfo(np.int32).min and c_max < np.iinfo(np.int32).max:

                    df[col] = df[col].astype(np.int32)

            else:

                if c_min > np.finfo(np.float16).min and c_max < np.finfo(np.float16).max:

                    df[col] = df[col].astype(np.float32)

                else:

                    df[col] = df[col].astype(np.float32)

        else:

            # Convert text to category if low cardinality (high repeats)

            num_unique = len(df[col].unique())

            num_total = len(df[col])

            if num_total > 0 and num_unique / num_total < 0.5:

                df[col] = df[col].astype('category')

    return df



# --- CONSTANTS ---

NARC_TERMS = [

    "OXYCODONE", "MORPHINE", "FENTANYL", "HYDROMORPHONE", "HYDROCODONE", 

    "LORAZEPAM", "MIDAZOLAM", "DIAZEPAM", "ALPRAZOLAM", "CODEINE", 

    "METHADONE", "KETAMINE", "TRAMADOL", "ZOLPIDEM", "PHENOBARBITAL", 

    "BUPRENORPHINE", "LACOSAMIDE", "VIMPAT", "PREGABALIN", "LYRICA"

]

ADMIN_USERS = ['emily', 'joe', 'krista']

NAME_MAPPINGS = {

    "phi": "ali", "ho": "ali", "rebekah": "bekah", "nugent": "kathy", 

    "kathleen": "kathy", "spain": "dee", "deloris": "dee", 

    "jabusch": "dan", "daniel": "dan", "nicholas": "nick"     

}

AMBIGUOUS_NAMES = ["melissa", "emily", "sarah", "megan", "erin", "kyle", "jessica", "andy", "heather", "michelle", "taylor"]



# --- DATABASE HELPERS ---

@contextlib.contextmanager

def db_cursor():

    conn = None

    try:

        conn = psycopg2.connect(st.secrets["neon"]["db_url"])

        cur = conn.cursor()

        yield conn, cur

    except Exception as e:

        st.error(f"❌ Database Error: {e}")

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



def run_query(query, params=None):

    try:

        with db_cursor() as (conn, cur):

            return pd.read_sql(query, conn, params=params)

    except Exception:

        return pd.DataFrame()



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



# --- DATA CLEANING ---

def clean_dataframe(df):

    df = df.copy()

    colmap = {

        "UserName": "user_name", "Device": "device", "MedID": "med_id", "MedDescription": "med_desc", 

        "TransactionType": "event_type", "TransactionDateTime": "dt", "Quantity": "qty", 

        "Beg": "beginning_qty", "End": "ending_qty", "DiscrepancyQuantity": "discrepancy_qty", 

        "DiscrepancyReason": "discrepancy_reason", "ResolutionDatetime": "resolution_dt"

    }

    df.rename(columns=colmap, inplace=True)

    required = ["user_name", "device", "med_id", "med_desc", "event_type", "dt", "qty"]

    for col in required:

        if col not in df.columns: df[col] = None

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")

    df.dropna(subset=["dt"], inplace=True)

    for c in ["qty", "discrepancy_qty"]:

        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype('float32')

    df["dt"] = df["dt"].astype(str)

    df["pk"] = df.apply(generate_pk, axis=1)

    return df



def clean_schedule_data(df):

    df = df.copy()

    date_col = next((c for c in df.columns if "date" in str(c).lower()), None)

    if date_col: df.rename(columns={date_col: 'Date'}, inplace=True)

    elif len(df.columns) > 1: df.rename(columns={df.columns[1]: 'Date'}, inplace=True)

    

    df = df.dropna(subset=['Date'])

    df = df[pd.to_datetime(df['Date'], errors='coerce').notna()] # valid dates only

    

    id_vars = [c for c in df.columns if "date" in str(c).lower() or "day" in str(c).lower()]

    if not id_vars: id_vars = ['Date']

    

    long_df = df.melt(id_vars=id_vars, var_name='col_header', value_name='raw_entry')

    long_df.dropna(subset=['raw_entry'], inplace=True)

    long_df = long_df[~long_df['raw_entry'].astype(str).str.lower().isin(['x', 'nan', '', ' '])]

    

    processed_rows = []

    for _, row in long_df.iterrows():

        raw = str(row['raw_entry']).strip()

        try: dt = pd.to_datetime(row[id_vars[0]]).date()

        except: continue

        day_name = row.get('Day', dt.strftime('%A'))

        

        parts = [p.strip() + ')' for p in raw.split(')') if '(' in p] if re.search(r'\(\d', raw) else [p.strip() for p in raw.split('\n') if p.strip()]

        

        for part in parts:

            if not part or part == ')': continue

            override_time = None

            m_range = re.search(r'\(?(\d{4})\s*-\s*\d{4}\)?', part)

            if m_range: override_time = m_range.group(1); clean_part = part.replace(m_range.group(0), '')

            else: clean_part = part

            

            clean_part = clean_part.replace('()', '').strip()

            if clean_part.endswith(','): clean_part = clean_part[:-1]

            

            assignment_type = "Shift"

            if 'trn' in clean_part.lower(): assignment_type = "Training"

            elif any(x in clean_part.lower() for x in ['pto', 'off', 'sick']): assignment_type = "PTO"

            

            final_shift_str = override_time if override_time else "Scheduled"

            pk = hashlib.sha256(f"{dt}|{clean_part}|{final_shift_str}".encode()).hexdigest()

            processed_rows.append({'pk': pk, 'dt': dt, 'day_name': day_name, 'staff_name': clean_part.title(), 'shift_type': final_shift_str, 'assignment_type': assignment_type, 'raw_entry': part, 'note': ""})

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

        if "Employee:" not in line: continue

        m_name = name_pat.search(line)

        m_date = date_pat.search(line)

        times = time_pat.findall(line)

        if m_name and m_date and len(times) > 0:

            data.append({"raw_name": m_name.group(1).strip(), "dt_date": pd.to_datetime(m_date.group(1)).date(), "start_dt": times[0], "end_dt": times[1] if len(times)>1 else None})

    df = pd.DataFrame(data)

    if not df.empty: df["pk"] = df.apply(generate_pk, axis=1)

    return df



def clean_audit_file(df, filename):

    df = df.copy()

    audit_date = date.today()

    tech_name = "Unknown"

    m = re.search(r'(\d{1,2})[\.\-](\d{1,2})[\.\-](\d{2,4})', filename)

    if m:

        try:

            mo, d, y = m.groups()

            audit_date = date(int(f"20{y}") if len(y)==2 else int(y), int(mo), int(d))

            tech_name = normalize_name(filename[:m.start()].strip())

        except: pass

    

    col_map = {}

    for c in df.columns:

        cl = c.lower()

        if 'category' in cl: col_map[c] = 'category'

        elif 'question' in cl: col_map[c] = 'question'

        elif 'result' in cl: col_map[c] = 'result'

        elif 'comment' in cl: col_map[c] = 'note'

    df.rename(columns=col_map, inplace=True)

    

    for req in ['category', 'question', 'result']: 

        if req not in df.columns: df[req] = "Unknown"

    

    df['points_earned'] = df['result'].astype(str).str.lower().isin(['yes','pass','compliant','1','ok']).astype(int)

    df['points_possible'] = 1.0

    df['audit_dt'] = audit_date

    df['technician'] = tech_name

    df['pk'] = df.apply(lambda x: hashlib.sha256(f"{x['audit_dt']}|{x['technician']}|{x['question']}".encode()).hexdigest(), axis=1)

    return df[['pk', 'audit_dt', 'technician', 'category', 'question', 'result', 'points_earned', 'points_possible', 'note']]



# --- DATA LOADERS (MEMORY OPTIMIZED) ---

@st.cache_data(ttl=300, max_entries=1)

def load_data(start_date, end_date):

    queries = {

        "events": "SELECT e.user_name, e.device, e.med_id, e.med_desc, e.event_type, e.dt, e.qty, e.discrepancy_qty, c.cost_per_unit FROM events e LEFT JOIN med_costs c ON e.med_id = c.med_id WHERE e.dt::date BETWEEN %s AND %s",

        "pharm": "SELECT pk, priority, dt, med_id, med_desc, destination, user_name, qty FROM pharmacy_orders WHERE dt::date BETWEEN %s AND %s",

        "schedule": "SELECT pk, dt, staff_name, shift_type, assignment_type FROM staff_schedule WHERE dt BETWEEN %s AND %s",

        "attendance": "SELECT pk, raw_name, dt_date, start_dt, end_dt FROM attendance_punches WHERE dt_date BETWEEN %s AND %s",

        "audits": "SELECT pk, audit_dt, technician, category, question, result, points_earned, points_possible, note FROM tech_audits WHERE audit_dt BETWEEN %s AND %s"

    }

    results = {}

    with db_cursor() as (conn, cur):

        for key, sql in queries.items():

            try:

                df = pd.read_sql(sql, conn, params=(start_date, end_date))

                if 'dt' in df.columns: df["dt"] = pd.to_datetime(df["dt"])

                results[key] = reduce_mem_usage(df)

            except Exception: results[key] = pd.DataFrame()

            

    # Process Events

    df = results["events"]

    if not df.empty:

        df["cost_per_unit"] = df["cost_per_unit"].fillna(0)

        df = df[~df['med_desc'].astype(str).str.contains(r'Drw|Pkt|Cubic', regex=True, case=False, na=False)]

        df.sort_values(['user_name', 'dt'], inplace=True)

        # Vectorized Session ID (Faster)

        df['time_gap'] = df.groupby('user_name')['dt'].diff().dt.total_seconds().fillna(0)

        df['device_change'] = df['device'] != df.groupby('user_name')['device'].shift(1)

        df['is_new_session'] = (df['time_gap'] > 1200) | (df['device_change'])

        df['session_id'] = df['is_new_session'].cumsum()

        

        # Calculate Machine Time

        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)

        df['dur'] = (df['next_dt'] - df['dt']).dt.total_seconds().fillna(0)

        # Only count time if next transaction is same device and < 10 mins

        df['machine_time_sec'] = np.where((df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['dur'] < 600), df['dur'], 0)

        df.drop(columns=['time_gap', 'device_change', 'is_new_session', 'next_dt', 'dur'], inplace=True)



    # Force garbage collection

    gc.collect()

    return df, results.get("config", pd.DataFrame()), results["pharm"], results["schedule"], results["attendance"], results["audits"]



def get_stats_range():

    sql = "SELECT (SELECT COUNT(*) FROM events), (SELECT COUNT(*) FROM pharmacy_orders), (SELECT COUNT(*) FROM staff_schedule), (SELECT COUNT(*) FROM attendance_punches), MIN(dt::date), MAX(dt::date) FROM events"

    with db_cursor() as (conn, cur):

        cur.execute(sql)

        row = cur.fetchone()

        return (row[0] or 0), (row[1] or 0), (row[2] or 0), (row[3] or 0), (row[4] or date.today()), (row[5] or date.today())



def get_present_dates(min_dt, max_dt):

    df = run_query("SELECT DISTINCT dt::date FROM events WHERE dt IS NOT NULL UNION SELECT DISTINCT dt::date FROM pharmacy_orders WHERE dt IS NOT NULL")

    if not df.empty:

        df[df.columns[0]] = pd.to_datetime(df[df.columns[0]], errors='coerce')

        return set(df[df.columns[0]].dt.date.dropna())

    return set()



# --- MAIN APP LOGIC ---

init_db()



PAGES = ["📊 Overview", "🎓 Student Project", "🏆 Shift Leaderboard", "📝 Manager Tools", "⏰ Tardies", "🚀 Process Mining", "🛡️ Compliance", "🚚 Load/Unload", "⚡ Efficiency", "🔍 Session Explorer", "🏥 Pharmacy Workflow", "🔄 Return Reconciliation", "⚖️ Tech Comparison", "📈 Tech Progression", "📅 Attendance"]



with st.sidebar:

    st.image("https://img.icons8.com/color/96/caduceus.png", width=60)

    st.title("RxTrack v14.3")

    st.caption("Manager's Edition (Optimized)")

    selected_page = st.radio("Go to:", PAGES, label_visibility="collapsed")

    st.divider()

    

    n_events, n_pharm, n_sched, n_att, min_db, max_db = get_stats_range()

    

    # Date Picker

    filter_mode = st.radio("Filter Mode", ["Range", "Week", "Day"], horizontal=True, label_visibility="collapsed")

    if filter_mode == "Range":

        d_range = st.slider("Range:", min_value=min_db, max_value=max_db, value=(max(min_db, max_db-timedelta(days=14)), max_db), format="MM/DD/YY")

        start_date, end_date = d_range

    elif filter_mode == "Week":

        ws = st.date_input("Week of:", value=max_db-timedelta(days=7), min_value=min_db, max_value=max_db)

        start_date, end_date = ws, ws + timedelta(days=6)

    else:

        sd = st.date_input("Day:", value=max_db, min_value=min_db, max_value=max_db)

        start_date, end_date = sd, sd



    st.divider()

    u_type = st.selectbox("📥 Import Data", ["Daily Transaction Report", "Pharmacy Workflow Report", "Staff Schedule", "Attendance Tracking", "Technician Audit"])

    uploaded = st.file_uploader(f"Upload {u_type}", type=["csv", "xlsx"])

    if uploaded and st.button("Process File"):

        try:

            if u_type == "Attendance Tracking":

                clean = clean_attendance_file(uploaded)

                sql = "INSERT INTO attendance_punches (pk, raw_name, dt_date, start_dt, end_dt) VALUES (%(pk)s, %(raw_name)s, %(dt_date)s, %(start_dt)s, %(end_dt)s) ON CONFLICT (pk) DO NOTHING;"

                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Attendance")

            elif u_type == "Technician Audit":

                clean = clean_audit_file(pd.read_csv(uploaded), uploaded.name)

                sql = "INSERT INTO tech_audits (pk, audit_dt, technician, category, question, result, points_earned, points_possible, note) VALUES (%(pk)s, %(audit_dt)s, %(technician)s, %(category)s, %(question)s, %(result)s, %(points_earned)s, %(points_possible)s, %(note)s) ON CONFLICT (pk) DO UPDATE SET result=EXCLUDED.result, points_earned=EXCLUDED.points_earned, note=EXCLUDED.note;"

                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Audits")

            elif u_type == "Staff Schedule":

                raw = pd.read_excel(uploaded) if uploaded.name.endswith('.xlsx') else pd.read_csv(uploaded, header=0, encoding='latin1')

                clean = clean_schedule_data(raw)

                sql = "INSERT INTO staff_schedule (pk, dt, day_name, staff_name, shift_type, assignment_type, raw_entry, note) VALUES (%(pk)s, %(dt)s, %(day_name)s, %(staff_name)s, %(shift_type)s, %(assignment_type)s, %(raw_entry)s, %(note)s) ON CONFLICT (pk) DO NOTHING;"

                execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Schedule")

            elif u_type == "Daily Transaction Report":

                raw = pd.read_csv(uploaded, header=None, nrows=20, encoding='latin1')

                h_idx = next((i for i, r in raw.iterrows() if "username" in str(r.values).lower() and "device" in str(r.values).lower()), None)

                if h_idx is not None:

                    uploaded.seek(0)

                    clean = clean_dataframe(pd.read_csv(uploaded, header=h_idx, encoding='latin1'))

                    sql = "INSERT INTO events (pk, user_name, device, med_id, med_desc, event_type, dt, qty, beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason, resolution_dt) VALUES (%(pk)s, %(user_name)s, %(device)s, %(med_id)s, %(med_desc)s, %(event_type)s, %(dt)s, %(qty)s, %(beginning_qty)s, %(ending_qty)s, %(discrepancy_qty)s, %(discrepancy_reason)s, %(resolution_dt)s) ON CONFLICT (pk) DO NOTHING;"

                    execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Events")

            elif u_type == "Pharmacy Workflow Report":

                raw = pd.read_csv(uploaded, header=None, nrows=20, encoding='latin1')

                h_idx = next((i for i, r in raw.iterrows() if "tranqueueid" in str(r.values).lower()), None)

                if h_idx is not None:

                    uploaded.seek(0)

                    clean = clean_pharmacy_report(pd.read_csv(uploaded, header=h_idx, encoding='latin1'))

                    sql = "INSERT INTO pharmacy_orders (pk, queue_id, priority, dt, med_id, med_desc, destination, user_name, qty) VALUES (%(pk)s, %(queue_id)s, %(priority)s, %(dt)s, %(med_id)s, %(med_desc)s, %(destination)s, %(user_name)s, %(qty)s) ON CONFLICT (pk) DO NOTHING;"

                    execute_statement(sql, clean.to_dict("records"), batch=True, table_name="Pharmacy Orders")

            

            st.cache_data.clear()

            st.rerun()

        except Exception as e: st.error(f"Error: {e}")



# --- LOAD DATA ---

if 'start_date' in locals():

    try: df_events, df_config, df_pharm, df_sched, df_att, df_audits = load_data(start_date, end_date)

    except Exception as e: st.error(f"Load Error: {e}")



# 1. OVERVIEW

if selected_page == "📊 Overview":

    st.markdown("## 🏥 Executive Summary")

    if not df_events.empty:

        c1, c2, c3, c4 = st.columns(4)

        c1.metric("Transactions", f"{len(df_events):,}")

        c2.metric("Active Techs", df_events["user_name"].nunique())

        c3.metric("Discrepancies", df_events["discrepancy_qty"].ne(0).sum())

        

        col_main, col_side = st.columns([2, 1])

        with col_main:

            st.subheader("🐢 Slowest Medications")

            slow = df_events[df_events['machine_time_sec'] > 0].groupby('med_desc')['machine_time_sec'].mean().sort_values(ascending=False).head(10).reset_index()

            st.plotly_chart(px.bar(slow, x='machine_time_sec', y='med_desc', orientation='h', title="Avg Seconds per Med"), use_container_width=True)

        with col_side:

            st.subheader("Activity")

            st.plotly_chart(px.pie(df_events, names='event_type', hole=0.4), use_container_width=True)



# 16. MANAGER TOOLS

elif selected_page == "📝 Manager Tools":

    st.header("📝 Manager Tools & Awards")

    tabs = st.tabs(["🎲 Pick Audit Target", "📝 Digital Audit Form", "🏆 Tech of the Quarter"])

    

    with tabs[0]: # PICKER

        st.subheader("🎲 Random Tech Selector")

        if st.button("🎲 Pick Someone", use_container_width=True):

            candidates = []

            if not df_sched.empty:

                working = df_sched[df_sched['dt'].astype(str) == date.today().strftime('%Y-%m-%d')]['staff_name'].unique()

                candidates = [c for c in working if normalize_name(c) not in ADMIN_USERS]

            if not candidates and not df_events.empty:

                candidates = [c for c in df_events['user_name'].unique() if c not in ADMIN_USERS]

            

            if candidates:

                winner = random.choice(candidates)

                st.balloons()

                st.success(f"🎯 TODAY'S TARGET: **{str(winner).upper()}**")

            else: st.warning("No candidates found.")



    with tabs[1]: # AUDIT FORM

        st.subheader("📝 New 5-Drug Audit")

        # DRUG DROPDOWN

        all_techs = sorted(df_events['user_name'].unique()) if not df_events.empty else ["Unknown"]

        drug_opts = [""] + sorted(df_events['med_desc'].dropna().unique()) if not df_events.empty else [""]

        

        with st.form("audit"):

            c1, c2 = st.columns(2)

            sel_tech = c1.selectbox("Technician", all_techs)

            adt = c2.date_input("Date", date.today())

            

            entries = []

            for i in range(1, 6):

                cc1, cc2 = st.columns([2,1])

                dn = cc1.selectbox(f"Drug {i}", drug_opts, key=f"dn{i}")

                dl = cc2.text_input(f"Loc {i}", key=f"dl{i}")

                checks = st.columns(5)

                res = [checks[j].checkbox(l, key=f"c{i}_{j}") for j, l in enumerate(["Drug", "Count", "Date", "Rotate", "Fill"])]

                if dn: entries.append((dn, res))

            

            notes = st.text_area("Notes")

            if st.form_submit_button("💾 Save"):

                recs = []

                check_lbls = ["Correct Drug", "Correct Count", "Correct Date", "Rotated", "Not Overfilled"]

                for drug, res in entries:

                    for idx, passed in enumerate(res):

                        recs.append({

                            "pk": hashlib.sha256(f"{adt}|{sel_tech}|{drug}|{check_lbls[idx]}".encode()).hexdigest(),

                            "audit_dt": adt, "technician": sel_tech, "category": drug, "question": check_lbls[idx],

                            "result": "Pass" if passed else "Fail", "points_earned": 1 if passed else 0, "points_possible": 1, "note": notes

                        })

                if recs:

                    execute_statement("INSERT INTO tech_audits (pk, audit_dt, technician, category, question, result, points_earned, points_possible, note) VALUES (%(pk)s, %(audit_dt)s, %(technician)s, %(category)s, %(question)s, %(result)s, %(points_earned)s, %(points_possible)s, %(note)s) ON CONFLICT (pk) DO NOTHING;", recs, batch=True, table_name="Audit")

                    st.cache_data.clear()



    with tabs[2]: # SCOREBOARD

        if not df_audits.empty and not df_att.empty:

            aud = df_audits.groupby('technician')['points_earned'].sum() / df_audits.groupby('technician')['points_possible'].sum() * 100

            aud = aud.reset_index(name='Audit')

            

            df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)

            df_att['match_key'] = df_att['raw_name'].apply(normalize_name)

            m = pd.merge(df_sched, df_att, on='match_key')

            # Simple tardy count

            tardies = m[pd.to_datetime(m['start_dt']).dt.time > pd.to_datetime("07:05").time()].groupby('match_key').size().reset_index(name='Tardies')

            

            final = pd.merge(aud, tardies, left_on='technician', right_on='match_key', how='outer').fillna(0)

            final['Score'] = (final['Audit'] * 0.7) - (final['Tardies'] * 5)

            st.dataframe(final.sort_values('Score', ascending=False), use_container_width=True)

        else: st.info("Need Audit & Attendance data.")



# 14. SHIFT LEADERBOARD (Optimized)

elif selected_page == "🏆 Shift Leaderboard":

    if not df_sched.empty:

        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)

        df_sched['date_obj'] = pd.to_datetime(df_sched['dt']).dt.date

        

        # Aggregate Events

        if not df_events.empty:

            df_events['match_key'] = df_events['user_name'].apply(normalize_name)

            cts = df_events.groupby([df_events['dt'].dt.date, 'match_key']).size().reset_index(name='tx')

            df = pd.merge(df_sched, cts, left_on=['date_obj', 'match_key'], right_on=['dt', 'match_key'], how='left').fillna(0)

            

            shifts = [s for s in df['shift_type'].unique() if s and 'pto' not in str(s).lower()]

            sel = st.selectbox("Shift", sorted(shifts))

            sub = df[df['shift_type'] == sel].groupby('staff_name')['tx'].mean().sort_values(ascending=False).head(10).reset_index()

            st.plotly_chart(px.bar(sub, x='tx', y='staff_name', orientation='h', title=f"Top Performers: {sel}"), use_container_width=True)
