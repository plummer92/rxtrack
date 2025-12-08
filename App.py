###############################################################
# RXTRACK: EXECUTIVE DASHBOARD (INTEGRATED v10.0)
# Architecture: Quad-Table Strategy (Events | Config | Pharm | Schedule)
# New Features:
#   1. Staff Schedule Parser (Handling "Ragged" CSVs)
#   2. Attendance Audit Tab (Comparing Scheduled vs. Actual Work)
#   3. Full integration of previous logic.
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
import re  # Required for schedule parsing

# Page Config
st.set_page_config(
    page_title="RxTrack: Workforce & Efficiency", 
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
    <style>
    .metric-card { background-color: #f0f2f6; padding: 20px; border-radius: 12px; border-left: 6px solid #4CAF50; color: #31333F; box-shadow: 2px 2px 5px rgba(0,0,0,0.1); }
    .metric-card h3 { color: #31333F; margin: 0; font-size: 28px; font-weight: 700; }
    .metric-card p { color: #666; margin: 0; font-size: 14px; font-weight: 500; }
    .cal-grid { display: flex; flex-wrap: wrap; gap: 2px; max-width: 100%; }
    .cal-day { width: 18px; height: 18px; border-radius: 2px; font-size: 8px; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; }
    .cal-present { background-color: #4CAF50; }
    .cal-missing { background-color: #FF4B4B; }
    .cal-empty { background-color: #e0e0e0; }
    </style>
    """, unsafe_allow_html=True)

###########################################################
#                 HELPER FUNCTIONS
###########################################################
def seconds_to_mmss(seconds):
    if pd.isna(seconds) or seconds < 0: return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    else:
        return f"{s}s"

def safe_to_date(val):
    if val is None: return datetime.today().date()
    try: return pd.to_datetime(val).date()
    except: return datetime.today().date()

def generate_pk(row):
    subset = [str(x) for x in row.values if pd.notnull(x)]
    row_str = "|".join(subset)
    return hashlib.sha256(row_str.encode()).hexdigest()

###########################################################
#                 DATABASE CONNECTION
###########################################################
@st.cache_resource
def get_db_connection():
    try:
        return psycopg2.connect(st.secrets["neon"]["db_url"])
    except Exception as e:
        st.error(f"❌ DB Connection Error: {e}")
        return None

def get_db_stats():
    """ Calculates date range across primary tables. """
    conn = get_db_connection()
    if not conn: return 0, 0, datetime.today().date(), datetime.today().date(), set()
    
    try:
        cur = conn.cursor()
        # Get Range
        cur.execute("""
            SELECT MIN(dt), MAX(dt) FROM (
                SELECT dt::timestamp as dt FROM events
                UNION ALL
                SELECT dt::timestamp as dt FROM pharmacy_orders
            ) as combined
        """)
        range_result = cur.fetchone()
        min_dt = safe_to_date(range_result[0]) if range_result and range_result[0] else datetime.today().date()
        max_dt = safe_to_date(range_result[1]) if range_result and range_result[1] else datetime.today().date()
        
        # Get Counts
        cur.execute("SELECT COUNT(*) FROM events")
        rows_events = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM pharmacy_orders")
        rows_pharm = cur.fetchone()[0]
        
        # Calendar Heatmap Data
        present_dates = set()
        if rows_events > 0:
            cur.execute("SELECT DISTINCT DATE(dt) FROM events")
            present_dates = {safe_to_date(row[0]) for row in cur.fetchall()}
            
        cur.close()
        return rows_events, rows_pharm, min_dt, max_dt, present_dates
        
    except Exception as e:
        return 0, 0, datetime.today().date(), datetime.today().date(), set()

###########################################################
#                 DATA CLEANING & PROCESSING
###########################################################
def clean_dataframe(df):
    """ Cleans Daily Transaction Report (Pyxis Events) """
    df = df.copy()
    colmap = {
        "UserName": "user_name", "UserID": "user_id", "Device": "device",
        "MedID": "med_id", "MedDescription": "med_desc",
        "TransactionType": "event_type", "TransactionDateTime": "dt",
        "Quantity": "qty", "Beg": "beginning_qty", "End": "ending_qty",
        "DiscrepancyQuantity": "discrepancy_qty", 
        "DiscrepancyReason": "discrepancy_reason",
        "ResolutionDatetime": "resolution_dt"
    }
    df = df.rename(columns=colmap)
    required = ["user_name", "device", "med_id", "med_desc", "event_type", "dt", "qty", "beginning_qty", "ending_qty", "discrepancy_qty", "discrepancy_reason", "resolution_dt"]
    for col in required:
        if col not in df.columns: df[col] = None

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"]) 
    df["resolution_dt"] = pd.to_datetime(df["resolution_dt"], errors="coerce")

    for c in ["qty", "discrepancy_qty"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype('float32')

    df["dt"] = df["dt"].astype(str)
    df["resolution_dt"] = df["resolution_dt"].astype(str).replace(['NaT', 'nan', 'None', ''], None)
    df["pk"] = df.apply(lambda r: generate_pk(r), axis=1)
    
    return df[required + ["pk"]]

def clean_activity_log(df):
    """ Cleans Device Activity Log (Config Pends) """
    df = df.copy()
    df.columns = df.columns.str.strip().str.replace(' ', '')
    df = df.rename(columns={
        "UserName": "user_name", "Device": "device", "TransactionDateTime": "dt", 
        "Action": "action_type", "ActivityType": "activity_category", "AffectedElement": "raw_element",
        "Amount": "qty_col", "Quantity": "qty_col"
    })
    
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"])
    
    pattern_element = r'^(.*?) \((.*?)\)'
    extracted_elem = df['raw_element'].astype(str).str.extract(pattern_element)
    df['location'] = extracted_elem[0].str.strip()
    df['med_id'] = extracted_elem[1].str.strip()
    df = df.dropna(subset=['med_id'])

    if 'qty_col' in df.columns:
        df['qty_extracted'] = pd.to_numeric(df['qty_col'], errors='coerce')
    else:
        pattern_qty = r':\s*(\d+)$' 
        df['qty_extracted'] = df['raw_element'].astype(str).str.extract(pattern_qty)[0]
        df['qty_extracted'] = pd.to_numeric(df['qty_extracted'], errors='coerce')
    
    # Deduplication Logic
    df = df.sort_values(['user_name', 'device', 'med_id', 'dt'])
    df['prev_dt'] = df.groupby(['user_name', 'device', 'med_id'])['dt'].shift(1)
    df['time_gap'] = (df['dt'] - df['prev_dt']).dt.total_seconds().fillna(999)
    df['group_id'] = (df['time_gap'] > 120).astype(int).cumsum()
    
    df['is_min_event'] = df['activity_category'].astype(str).str.contains('Min', case=False, na=False)
    df['is_max_event'] = df['activity_category'].astype(str).str.contains('Max', case=False, na=False)
    df['is_standard'] = df['activity_category'].astype(str).str.contains('Standard Stock', case=False, na=False)

    df['temp_min'] = np.where(df['is_min_event'], df['qty_extracted'], np.nan)
    df['temp_max'] = np.where(df['is_max_event'], df['qty_extracted'], np.nan)
    df['temp_max'] = np.where((~df['is_min_event']) & (~df['is_max_event']), df['qty_extracted'], df['temp_max'])

    grouped = df.groupby(['user_name', 'device', 'med_id', 'group_id'], as_index=False).agg({
        'temp_min': 'max', 'temp_max': 'max', 'is_standard': 'max',
        'location': 'first', 'dt': 'first', 'action_type': 'first', 'activity_category': 'first'
    })
    
    grouped['min_qty'] = grouped['temp_min']
    grouped['max_qty'] = grouped['temp_max']
    grouped['is_standard'] = grouped['is_standard'].fillna(False)
    
    grouped["dt"] = grouped["dt"].astype(str)
    grouped["pk"] = grouped.apply(lambda r: generate_pk(r), axis=1)
    grouped = grouped.replace({np.nan: None})
    
    return grouped[['pk', 'dt', 'user_name', 'device', 'med_id', 'location', 'action_type', 'activity_category', 'min_qty', 'max_qty', 'is_standard']]

def clean_pharmacy_report(df):
    """ Cleans TransactionDetailReport (Central Pharmacy) """
    df = df.copy()
    colmap = {
        "TranQueueID": "queue_id", "Priority": "priority", "Date / Time": "dt",
        "Item ID": "med_id", "Description": "med_desc", "Destination": "destination",
        "User": "user_name", "Quantity": "qty"
    }
    df = df.rename(columns=colmap)
    for col in colmap.values():
        if col not in df.columns: df[col] = None
        
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"])
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    
    df["dt"] = df["dt"].astype(str)
    df["pk"] = df.apply(lambda r: generate_pk(r), axis=1)
    
    return df[["pk", "queue_id", "priority", "dt", "med_id", "med_desc", "destination", "user_name", "qty"]]

def clean_cost_dataframe(df):
    """ Cleans Financial Price List """
    df = df.copy()
    df.columns = df.columns.str.strip().str.lower()
    id_col = next((c for c in df.columns if "id" in c or "med" in c), None)
    cost_col = next((c for c in df.columns if "cost" in c or "price" in c or "avg" in c), None)
    if not id_col or not cost_col: return None
    df = df[[id_col, cost_col]].rename(columns={id_col: "med_id", cost_col: "cost_per_unit"})
    df["cost_per_unit"] = df["cost_per_unit"].astype(str).str.replace('$', '', regex=False)
    df["cost_per_unit"] = pd.to_numeric(df["cost_per_unit"], errors="coerce").fillna(0)
    df = df.dropna(subset=["med_id"])
    return df

def clean_schedule_data(df):
    """
    Parses the 'Pharmacy Tech Schedule' CSV (Ragged Format).
    """
    df = df.copy()
    
    # 1. Handle Header Offset
    # If the file has "WEEKDAYS" in row 0, the real header structure spans rows.
    # We rename columns based on position: Col 1 is Date, Col 2 is Day
    if len(df.columns) > 2:
        df = df.rename(columns={df.columns[1]: 'Date', df.columns[2]: 'Day'})
    
    # Drop the "WEEKENDS" row (usually index 0) and the first column label
    df = df.iloc[1:].dropna(subset=['Date'])
    df = df.drop(columns=[df.columns[0]], errors='ignore')
    
    # 2. Melt (Unpivot)
    long_df = df.melt(
        id_vars=['Date', 'Day'],
        var_name='shift_type',
        value_name='raw_entry'
    )
    
    # 3. Clean Rows
    long_df = long_df.dropna(subset=['raw_entry'])
    long_df = long_df[~long_df['raw_entry'].astype(str).str.lower().isin(['x', 'nan', '', ' '])]
    
    # 4. Parsing Logic
    def parse_entry(entry):
        entry = str(entry).strip()
        assignment_type = "Shift"
        note = ""
        name = entry
        
        # Check for Training
        if 'trn' in entry.lower() or 'training' in entry.lower():
            parts = re.split(r'\s(?:trn|training)\s', entry, flags=re.IGNORECASE)
            name = parts[0].strip()
            assignment_type = "Training"
            note = entry 
        # Check for PTO
        elif any(x in entry.lower() for x in ['pto', 'off', 'sick']):
            name = entry
            assignment_type = "PTO"
        
        return pd.Series([name.title(), assignment_type, note])

    long_df[['staff_name', 'assignment_type', 'note']] = long_df['raw_entry'].apply(parse_entry)
    
    # 5. Final Formatting
    long_df['dt'] = pd.to_datetime(long_df['Date'], errors='coerce').dt.date
    long_df = long_df.dropna(subset=['dt'])
    long_df['day_name'] = long_df['Day']
    
    # Generate PK
    long_df["pk"] = long_df.apply(lambda r: generate_pk(r), axis=1)
    
    return long_df[['pk', 'dt', 'day_name', 'staff_name', 'shift_type', 'assignment_type', 'raw_entry', 'note']]

def insert_batch(df, table_name):
    conn = get_db_connection()
    if not conn: return
    cur = conn.cursor()
    
    sql = ""
    if table_name == "events":
        sql = """INSERT INTO events (pk, user_name, device, med_id, med_desc, event_type, dt, qty, beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason, resolution_dt) VALUES (%(pk)s, %(user_name)s, %(device)s, %(med_id)s, %(med_desc)s, %(event_type)s, %(dt)s, %(qty)s, %(beginning_qty)s, %(ending_qty)s, %(discrepancy_qty)s, %(discrepancy_reason)s, %(resolution_dt)s) ON CONFLICT (pk) DO NOTHING;"""
    elif table_name == "config_events":
        sql = """INSERT INTO config_events (pk, dt, user_name, device, med_id, location, action_type, activity_category, min_qty, max_qty, is_standard) VALUES (%(pk)s, %(dt)s, %(user_name)s, %(device)s, %(med_id)s, %(location)s, %(action_type)s, %(activity_category)s, %(min_qty)s, %(max_qty)s, %(is_standard)s) ON CONFLICT (pk) DO NOTHING;"""
    elif table_name == "med_costs":
        sql = """INSERT INTO med_costs (med_id, cost_per_unit) VALUES (%(med_id)s, %(cost_per_unit)s) ON CONFLICT (med_id) DO UPDATE SET cost_per_unit = EXCLUDED.cost_per_unit;"""
    elif table_name == "pharmacy_orders":
        sql = """INSERT INTO pharmacy_orders (pk, queue_id, priority, dt, med_id, med_desc, destination, user_name, qty) VALUES (%(pk)s, %(queue_id)s, %(priority)s, %(dt)s, %(med_id)s, %(med_desc)s, %(destination)s, %(user_name)s, %(qty)s) ON CONFLICT (pk) DO NOTHING;"""
    elif table_name == "staff_schedule":
        sql = """INSERT INTO staff_schedule (pk, dt, day_name, staff_name, shift_type, assignment_type, raw_entry, note) VALUES (%(pk)s, %(dt)s, %(day_name)s, %(staff_name)s, %(shift_type)s, %(assignment_type)s, %(raw_entry)s, %(note)s) ON CONFLICT (pk) DO NOTHING;"""
        
    rows = df.to_dict("records")
    try:
        execute_batch(cur, sql, rows, page_size=2000)
        conn.commit()
        st.success(f"✅ Processed {len(rows)} records into '{table_name}'.")
    except Exception as e:
        st.error(f"Error: {e}")
        conn.rollback()
    finally:
        cur.close()

###########################################################
#                 ANALYTICS LOGIC
###########################################################
@st.cache_data(ttl=300)
def load_events_data(start_date, end_date):
    """ Loads EVENTS table. """
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    
    query = """
        SELECT e.user_name, e.device, e.med_id, e.med_desc, e.event_type, e.dt, e.qty, e.discrepancy_qty, e.discrepancy_reason, c.cost_per_unit, e.pk 
        FROM events e LEFT JOIN med_costs c ON e.med_id = c.med_id
        WHERE e.dt::date BETWEEN %s AND %s
    """
    try:
        df = pd.read_sql(query, conn, params=(start_date, end_date))
    except:
        return pd.DataFrame()
    
    if not df.empty:
        df["dt"] = pd.to_datetime(df["dt"])
        df["cost_per_unit"] = df["cost_per_unit"].fillna(0).astype('float32')
        df["qty"] = df["qty"].fillna(0).astype('float32')
        
        # Cleaning
        df = df[~df['med_desc'].astype(str).str.contains(r'Drw|Pkt|Cubic', regex=True, case=False, na=False)]
        
        # Time Logic
        df = df.sort_values(['user_name', 'dt'])
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['duration_seconds'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        
        df['machine_time_sec'] = np.where((df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['duration_seconds'] < 600), df['duration_seconds'], 0)
        df['is_new_session'] = np.where((df['user_name'] != df['user_name'].shift(1)) | (df['device'] != df['prev_device']), 1, 0)
        df['session_id'] = df['is_new_session'].cumsum()

        df['Timestamp'] = df['dt'].dt.strftime('%b %d, %H:%M:%S')
        df['Date'] = df['dt'].dt.date
        df['Hour'] = df['dt'].dt.hour
        
        df.drop(columns=['next_dt', 'is_new_session'], inplace=True, errors='ignore')
        gc.collect() 
        
    return df

@st.cache_data(ttl=300)
def load_config_data(start_date, end_date):
    """ Loads CONFIG table. """
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    query = """
        SELECT dt, user_name, device, med_id, location, action_type, activity_category, min_qty, max_qty, is_standard 
        FROM config_events WHERE dt::date BETWEEN %s AND %s
    """
    try:
        df = pd.read_sql(query, conn, params=(start_date, end_date))
    except:
        return pd.DataFrame()
    if not df.empty:
        df["dt"] = pd.to_datetime(df["dt"])
        df['Timestamp'] = df['dt'].dt.strftime('%b %d, %H:%M:%S')
        df['Standard Stock'] = df['is_standard'].apply(lambda x: "☑️ Yes" if x else "☐ No")
        df['Activity'] = df['action_type'] + " (" + df['activity_category'] + ")"
        df['Hour'] = df['dt'].dt.hour
    return df

@st.cache_data(ttl=300)
def load_pharmacy_data(start_date, end_date):
    """ Loads PHARMACY_ORDERS table. """
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    query = """
        SELECT queue_id, priority, dt, med_id, med_desc, destination, user_name, qty
        FROM pharmacy_orders 
        WHERE dt::date BETWEEN %s AND %s
    """
    try:
        df = pd.read_sql(query, conn, params=(start_date, end_date))
    except:
        return pd.DataFrame()
    if not df.empty:
        df["dt"] = pd.to_datetime(df["dt"])
        df['Timestamp'] = df['dt'].dt.strftime('%b %d, %H:%M:%S')
        df['qty'] = pd.to_numeric(df['qty'], errors='coerce').fillna(0)
        df['destination'] = df['destination'].fillna('Carousel Workflow').replace('', 'Carousel Workflow')
    return df

@st.cache_data(ttl=300)
def load_schedule_data(start_date, end_date):
    """ Loads STAFF_SCHEDULE table. """
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    query = """
        SELECT dt, day_name, staff_name, shift_type, assignment_type, note
        FROM staff_schedule 
        WHERE dt BETWEEN %s AND %s
    """
    try:
        df = pd.read_sql(query, conn, params=(start_date, end_date))
        if not df.empty:
             df["dt"] = pd.to_datetime(df["dt"])
        return df
    except:
        return pd.DataFrame()

###########################################################
#                 DASHBOARD UI
###########################################################

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=50)
    st.title("SJS St. Johns Pharmacy")
    
    rows_events, rows_pharm, min_db, max_db, present_dates = get_db_stats()
    
    with st.expander("💾 Database Status", expanded=True):
        st.write(f"**Pyxis Events:** {rows_events:,}")
        st.write(f"**Pharmacy Orders:** {rows_pharm:,}")
        
        delta = (max_db - min_db).days
        calendar_start = max_db - timedelta(days=90) if delta > 90 else min_db
        calendar_html = '<div class="cal-grid">'
        current_day = calendar_start
        while current_day <= max_db:
            color_class = "cal-present" if current_day in present_dates else "cal-missing"
            calendar_html += f'<div class="cal-day {color_class}" title="{current_day}">{current_day.day}</div>'
            current_day += timedelta(days=1)
        calendar_html += '</div>'
        st.markdown(calendar_html, unsafe_allow_html=True)
    
    st.divider()
    
    if min_db < max_db:
        default_start = max(min_db, max_db - timedelta(days=7))
        date_range = st.slider(
            "Select Range", 
            min_value=min_db, 
            max_value=max_db, 
            value=(default_start, max_db), 
            format="MM/DD/YY",
            key=f"main_date_slider_{rows_events}_{rows_pharm}"
        )
        start_date, end_date = date_range
    else:
        start_date, end_date = min_db, max_db
        
    st.divider()
    
    st.subheader("📤 Data Upload")
    upload_type = st.selectbox("Select File Type:", 
        ["Daily Transaction Report", "Device Activity Log (Pends)", "Financial Price List", "Pharmacy Workflow Report", "Staff Schedule"])
    
    uploaded = st.file_uploader(f"Upload {upload_type}", type=["csv","xlsx"])
    
    if uploaded:
        if st.button(f"Process {upload_type}"):
            try:
                # HEADER DETECTION LOGIC
                if upload_type == "Staff Schedule":
                     # Schedule format is fixed, header is row 0
                     raw = pd.read_csv(uploaded, header=0)
                     clean = clean_schedule_data(raw)
                     insert_batch(clean, "staff_schedule")
                     st.cache_data.clear()
                     st.rerun()
                else:
                    # Dynamic Header Search for other files
                    if uploaded.name.endswith(".xlsx"): preview = pd.read_excel(uploaded, header=None, nrows=50)
                    else: preview = pd.read_csv(uploaded, header=None, nrows=50)
                    header_row_idx = None
                    
                    for idx, row in preview.iterrows():
                        row_str = str(row.values).lower()
                        if upload_type == "Daily Transaction Report" and "username" in row_str and "device" in row_str and "affectedelement" not in row_str: header_row_idx = idx; break
                        if upload_type == "Device Activity Log (Pends)" and "affectedelement" in row_str: header_row_idx = idx; break
                        if upload_type == "Financial Price List" and ("med" in row_str or "id" in row_str) and "cost" in row_str: header_row_idx = idx; break
                        if upload_type == "Pharmacy Workflow Report" and "tranqueueid" in row_str: header_row_idx = idx; break
                    
                    if header_row_idx is None: 
                        st.error("❌ Header not found.")
                    else:
                        uploaded.seek(0)
                        if uploaded.name.endswith(".xlsx"): raw = pd.read_excel(uploaded, header=header_row_idx)
                        else: raw = pd.read_csv(uploaded, header=header_row_idx)
                        
                        if upload_type == "Daily Transaction Report":
                            clean = clean_dataframe(raw)
                            insert_batch(clean, "events")
                        elif upload_type == "Device Activity Log (Pends)":
                            clean = clean_activity_log(raw)
                            insert_batch(clean, "config_events")
                        elif upload_type == "Financial Price List":
                            clean = clean_cost_dataframe(raw)
                            insert_batch(clean, "med_costs")
                        elif upload_type == "Pharmacy Workflow Report":
                            clean = clean_pharmacy_report(raw)
                            insert_batch(clean, "pharmacy_orders")
                        
                        st.cache_data.clear()
                        st.rerun()
            except Exception as e: st.error(f"Error: {e}")

# Load ALL datasets
df = load_events_data(start_date, end_date)
df_config = load_config_data(start_date, end_date)
df_pharm = load_pharmacy_data(start_date, end_date)
df_sched = load_schedule_data(start_date, end_date)

if df.empty and df_config.empty and df_pharm.empty and df_sched.empty:
    st.info("👋 Ready for data. Upload files to begin.")
    st.stop()

# --- TABS ---
tab_over, tab_mine, tab_comp, tab_pends, tab_loads, tab_effic, tab_drill, tab_pharm, tab_recon, tab_compare, tab_progress, tab_attend = st.tabs([
    "📊 Overview", "🚀 Process Mining", "🛡️ Compliance", "📥 Pends Analyzer", "🚚 Load/Unload", "⚡ Efficiency", "🔍 Session Explorer", "🏥 Pharmacy Workflow", "🔄 Return Reconciliation", "⚖️ Tech Comparison", "📈 Tech Progression", "📅 Attendance"
])

# --- TAB 1: OVERVIEW ---
with tab_over:
    if not df.empty:
        st.markdown("## 🏥 Executive Summary")
        session_stats = df.groupby('session_id').agg(total_machine_time=('machine_time_sec', 'sum'))
        avg_machine_time = session_stats['total_machine_time'].mean()
        
        real_transactions = df[~df['event_type'].astype(str).str.contains('verify', case=False, na=False)]
        total_tx = len(real_transactions)
        active_techs = df['user_name'].nunique()
        
        c1, c2, c3 = st.columns(3)
        with c1: st.markdown(f"""<div class="metric-card"><h3>{total_tx:,}</h3><p>Total Transactions</p></div>""", unsafe_allow_html=True)
        with c2: st.markdown(f"""<div class="metric-card"><h3>{seconds_to_mmss(avg_machine_time)}</h3><p>Avg Session Duration</p></div>""", unsafe_allow_html=True)
        with c3: st.markdown(f"""<div class="metric-card"><h3>{active_techs}</h3><p>Active Technicians</p></div>""", unsafe_allow_html=True)
        
        st.markdown("---")
        st.subheader("🐢 Slowest Medications (Machine Time)")
        med_speed = df[df['machine_time_sec'] > 0].groupby('med_desc')['machine_time_sec'].mean().reset_index()
        top_slow = med_speed.sort_values('machine_time_sec', ascending=False).head(10)
        
        fig = px.bar(top_slow, x='machine_time_sec', y='med_desc', orientation='h', title="Avg Seconds per Transaction", text_auto='.0f', color='machine_time_sec', color_continuous_scale='Reds')
        fig.update_layout(yaxis={'categoryorder':'total ascending', 'title': ''}, xaxis={'title': 'Seconds'}, showlegend=False, height=500)
        st.plotly_chart(fig, use_container_width=True)

# --- TAB 2: PROCESS MINING ---
with tab_mine:
    if not df.empty:
        st.markdown("### 🔄 Workflow Visualization")
        available_dates = sorted(df['dt'].dt.date.unique(), reverse=True)
        
        c_pm1, c_pm2, c_pm3 = st.columns(3)
        all_pm_users = sorted([x for x in df['user_name'].unique() if x is not None])
        all_pm_devices = sorted([x for x in df['device'].unique() if x is not None])
        
        sel_pm_user = c_pm1.multiselect("Filter User", all_pm_users, key="pm_user_filter")
        sel_pm_device = c_pm2.multiselect("Filter Device", all_pm_devices, key="pm_device_filter")
        sel_pm_date = c_pm3.selectbox("Filter Shift Date", options=["All"] + [d.strftime('%Y-%m-%d') for d in available_dates], key="pm_date_filter")
        
        moves = df[df['device'] != df['prev_device']].dropna(subset=['prev_device', 'device']).copy()
        
        if sel_pm_user: moves = moves[moves['user_name'].isin(sel_pm_user)]
        if sel_pm_device: moves = moves[moves['device'].isin(sel_pm_device) | moves['prev_device'].isin(sel_pm_device)]
        if sel_pm_date != "All": moves = moves[moves['dt'].dt.date.astype(str) == sel_pm_date]
        
        if not moves.empty:
            path_counts = moves.groupby(['prev_device', 'device']).size().reset_index(name='count')
            path_counts = path_counts.sort_values('count', ascending=False).head(30)
            
            all_nodes = list(pd.concat([path_counts['prev_device'], path_counts['device']]).unique())
            node_map = {node: i for i, node in enumerate(all_nodes)}
            path_counts['source'] = path_counts['prev_device'].map(node_map)
            path_counts['target'] = path_counts['device'].map(node_map)
            
            fig_sankey = go.Figure(data=[go.Sankey(
                node=dict(pad=15, thickness=20, line=dict(color="black", width=0.5), label=all_nodes, color="#1E90FF"),
                link=dict(source=path_counts['source'], target=path_counts['target'], value=path_counts['count'], color='rgba(200, 200, 200, 0.3)')
            )])
            fig_sankey.update_layout(title_text="Top 30 Workflow Routes", font_size=10, height=500)
            st.plotly_chart(fig_sankey, use_container_width=True)
            
            st.divider()
            st.markdown("#### 🔥 Activity Heatmap (Device vs Hour)")
            activity = moves.groupby([moves['dt'].dt.hour.rename('Hour'), 'device']).size().reset_index(name='count')
            fig_heat = px.density_heatmap(activity, x='Hour', y='device', z='count', nbinsx=24, color_continuous_scale='Viridis')
            st.plotly_chart(fig_heat, use_container_width=True)

# --- TAB 3: COMPLIANCE ---
with tab_comp:
    if not df.empty:
        disc_df = df[df['discrepancy_qty'] != 0].copy() if 'discrepancy_qty' in df.columns else pd.DataFrame()
        c1, c2 = st.columns(2)
        c1.metric("Count Errors", len(disc_df))
        if not disc_df.empty:
            disc_df['abs_variance'] = disc_df['discrepancy_qty'].abs() * disc_df['cost_per_unit']
            total_loss = disc_df['abs_variance'].sum()
            c2.metric("Variance Value (Risk)", f"${total_loss:,.2f}")
            st.dataframe(disc_df[['dt', 'user_name', 'device', 'med_desc', 'discrepancy_qty', 'cost_per_unit', 'abs_variance']], use_container_width=True)

# --- TAB 4: PENDS ANALYZER ---
with tab_pends:
    st.markdown("### 📥 Inventory Configuration")
    if not df_config.empty:
        c_f1, c_f2 = st.columns(2)
        filter_user = c_f1.multiselect("Filter Tech", sorted([x for x in df_config['user_name'].unique() if x is not None]))
        filter_device = c_f2.multiselect("Filter Device", sorted([x for x in df_config['device'].unique() if x is not None]))
        
        pends_view = df_config.copy()
        if filter_user: pends_view = pends_view[pends_view['user_name'].isin(filter_user)]
        if filter_device: pends_view = pends_view[pends_view['device'].isin(filter_device)]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Config Changes", f"{len(pends_view):,}")
        c2.metric("Capacity Added", f"{int(pends_view['max_qty'].sum()):,}")
        c3.metric("Unique Meds Touched", pends_view['med_id'].nunique())
        
        st.divider()
        c_chart1, c_chart2 = st.columns(2)
        with c_chart1:
            top_meds = pends_view['med_id'].value_counts().head(10).reset_index()
            top_meds.columns = ['Med ID', 'Count']
            st.plotly_chart(px.bar(top_meds, x='Count', y='Med ID', orientation='h'), use_container_width=True)
        with c_chart2:
            top_dev = pends_view['device'].value_counts().head(10).reset_index()
            top_dev.columns = ['Device', 'Count']
            st.plotly_chart(px.bar(top_dev, x='Count', y='Device', orientation='h'), use_container_width=True)
    else:
        st.info("No Config Data found.")

# --- TAB 5: LOADS ---
with tab_loads:
    loads_df = df[df['event_type'].astype(str).str.lower().str.contains('load|unload')].copy() if not df.empty else pd.DataFrame()
    if not loads_df.empty:
        st.markdown("### 🚚 Load & Unload Events")
        st.dataframe(loads_df[['dt', 'user_name', 'device', 'event_type', 'med_desc', 'qty']], use_container_width=True)

# --- TAB 6: EFFICIENCY ---
with tab_effic:
    if not df.empty:
        st.markdown("### 📉 Inefficient Refills")
        effic_stats = df.groupby(['device', 'med_desc']).agg(Trips=('pk', 'count'), Avg_Added=('qty', 'mean')).reset_index()
        inefficient = effic_stats[(effic_stats['Trips'] >= 3) & (effic_stats['Avg_Added'] < 3)].sort_values('Trips', ascending=False).head(15)
        st.plotly_chart(px.bar(inefficient, x='Trips', y='med_desc', orientation='h', title="High Effort, Low Yield"))

# --- TAB 7: SESSION EXPLORER ---
with tab_drill:
    if not df.empty:
        st.header("🔍 Session Explorer")
        session_metrics = df.groupby('session_id').agg({'user_name': 'first', 'device': 'first', 'dt': ['min', 'max']}).reset_index()
        session_metrics.columns = ['session_id', 'User', 'Device', 'Start Time', 'End Time']
        session_metrics['dwell_seconds'] = (session_metrics['End Time'] - session_metrics['Start Time']).dt.total_seconds()
        
        with st.expander("⏳ Session Filters", expanded=True):
            c_fill1, c_fill2 = st.columns(2)
            sel_users = c_fill1.multiselect("Filter User", sorted([x for x in session_metrics['User'].unique() if x is not None]))
            dwell_range = c_fill2.slider("Dwell Time (sec)", 0, 3600, (0, 3600), step=10)
        
        if sel_users: session_metrics = session_metrics[session_metrics['User'].isin(sel_users)]
        valid_sessions = session_metrics[(session_metrics['dwell_seconds'] >= dwell_range[0]) & (session_metrics['dwell_seconds'] <= dwell_range[1])]['session_id']
        
        detailed_view = df[df['session_id'].isin(valid_sessions)].copy()
        detailed_view = detailed_view.merge(session_metrics[['session_id', 'dwell_seconds']], on='session_id', how='left')
        detailed_view['Dwell Time'] = detailed_view['dwell_seconds'].apply(seconds_to_mmss)
        
        st.dataframe(detailed_view[['user_name', 'device', 'dt', 'event_type', 'med_desc', 'qty', 'Dwell Time']], use_container_width=True)

# --- TAB 8: PHARMACY WORKFLOW ---
with tab_pharm:
    if not df_pharm.empty:
        st.markdown("### 🏥 Central Pharmacy Workflow")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Orders", f"{len(df_pharm):,}")
        critical_count = len(df_pharm[df_pharm['priority'].astype(str).str.contains('STAT|Critical', case=False, na=False)])
        c2.metric("Critical/STAT Orders", critical_count)
        top_dest = df_pharm['destination'].mode()[0] if not df_pharm.empty else "N/A"
        c3.metric("Top Destination", top_dest)
        
        c_chart1, c_chart2 = st.columns(2)
        with c_chart1:
            prio_counts = df_pharm['priority'].value_counts().reset_index()
            prio_counts.columns = ['Priority', 'Count']
            st.plotly_chart(px.pie(prio_counts, names='Priority', values='Count', hole=0.4), use_container_width=True)
        with c_chart2:
            chart_df = df_pharm[df_pharm['destination'] != 'Carousel Workflow']
            dest_counts = chart_df['destination'].value_counts().head(10).reset_index()
            dest_counts.columns = ['Destination', 'Orders']
            st.plotly_chart(px.bar(dest_counts, x='Orders', y='Destination', orientation='h'), use_container_width=True)
            
        st.dataframe(df_pharm[['dt', 'queue_id', 'priority', 'med_desc', 'destination', 'user_name', 'qty']].sort_values('dt', ascending=False), use_container_width=True)

# --- TAB 9: RECONCILIATION ---
with tab_recon:
    st.markdown("### 🔄 Unload vs. Return Reconciliation")
    filter_controlled = st.checkbox("Exclude Controlled Substances", value=True)

    if not df.empty and not df_pharm.empty:
        unloads = df[df['event_type'].astype(str).str.contains(r'unload|empty\s*return', case=False, na=False)].copy()
        returns = df_pharm[df_pharm['priority'] == 'Returns'].copy()

        if filter_controlled:
            narc_terms = ["OXYCODONE", "MORPHINE", "FENTANYL", "HYDROMORPHONE", "HYDROCODONE", "LORAZEPAM", "MIDAZOLAM", "DIAZEPAM", "ALPRAZOLAM", "CODEINE", "METHADONE", "KETAMINE", "TRAMADOL", "ZOLPIDEM", "PHENOBARBITAL", "BUPRENORPHINE", "LACOSAMIDE", "VIMPAT", "PREGABALIN", "LYRICA", "CHLORDIAZEPOXIDE", "LIBRIUM", "CLONAZEPAM", "KLONOPIN"]
            pattern = '|'.join(narc_terms)
            unloads = unloads[~unloads['med_desc'].astype(str).str.contains(pattern, case=False, na=False)]
            returns = returns[~returns['med_desc'].astype(str).str.contains(pattern, case=False, na=False)]

        unloads['Date'] = unloads['dt'].dt.date
        unloads['med_id_clean'] = unloads['med_id'].astype(str).str.strip().str.upper()
        unloads_agg = unloads.groupby(['Date', 'med_id_clean']).agg({'qty': 'sum', 'med_desc': 'first', 'dt': 'min'}).reset_index().rename(columns={'qty': 'qty_floor', 'dt': 'floor_time'})

        returns['Date'] = returns['dt'].dt.date
        returns['med_id_clean'] = returns['med_id'].astype(str).str.strip().str.upper()
        returns_agg = returns.groupby(['Date', 'med_id_clean']).agg({'qty': 'sum', 'dt': 'min', 'med_desc': 'first'}).reset_index().rename(columns={'qty': 'qty_returned', 'dt': 'pharm_time'})

        comparison = pd.merge(unloads_agg, returns_agg, on=['Date', 'med_id_clean'], how='outer', suffixes=('_floor', '_pharm'))
        comparison['med_desc'] = comparison['med_desc_floor'].combine_first(comparison['med_desc_pharm'])
        comparison['qty_floor'] = comparison['qty_floor'].fillna(0)
        comparison['qty_returned'] = comparison['qty_returned'].fillna(0)
        comparison['Variance'] = comparison['qty_returned'] - comparison['qty_floor']
        
        def get_status(row):
            if row['Variance'] == 0: return "✅ Match"
            if row['Variance'] < 0: return "❌ Missing Items"
            return "❓ Extra Returned"
        
        comparison['Status'] = comparison.apply(get_status, axis=1)
        st.dataframe(comparison[['Date', 'med_desc', 'qty_floor', 'qty_returned', 'Variance', 'Status']], use_container_width=True)

# --- TAB 10: TECH COMPARISON ---
with tab_compare:
    st.markdown("### ⚖️ Technician Performance Comparison")
    if not df.empty:
        unique_users = sorted([u for u in df['user_name'].unique() if u is not None])
        c1, c2 = st.columns(2)
        
        with c1:
            user_a = st.selectbox("Select User A", unique_users, key="user_a")
            dates_a = sorted(df[df['user_name'] == user_a]['dt'].dt.date.unique(), reverse=True)
            date_a = st.selectbox("Select Date A", dates_a, key="date_a") if dates_a else None

        with c2:
            default_idx_b = 1 if len(unique_users) > 1 else 0
            user_b = st.selectbox("Select User B", unique_users, index=default_idx_b, key="user_b")
            dates_b = sorted(df[df['user_name'] == user_b]['dt'].dt.date.unique(), reverse=True)
            date_b = st.selectbox("Select Date B", dates_b, key="date_b") if dates_b else None
        
        if date_a and date_b:
            df_a = df[(df['user_name'] == user_a) & (df['dt'].dt.date == date_a)].copy()
            df_b = df[(df['user_name'] == user_b) & (df['dt'].dt.date == date_b)].copy()
            
            def get_shift_metrics(sub_df):
                tx_count = len(sub_df[~sub_df['event_type'].astype(str).str.contains('verify', case=False, na=False)])
                avg_mach = sub_df[sub_df['machine_time_sec'] > 0]['machine_time_sec'].mean()
                return tx_count, 0 if pd.isna(avg_mach) else avg_mach

            m_a = get_shift_metrics(df_a)
            m_b = get_shift_metrics(df_b)
            
            st.divider()
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**{user_a}**")
                st.metric("Transactions", m_a[0])
                st.metric("Avg Machine Time", f"{m_a[1]:.1f}s")
            with col_b:
                st.markdown(f"**{user_b}**")
                st.metric("Transactions", m_b[0], delta=m_b[0]-m_a[0])
                st.metric("Avg Machine Time", f"{m_b[1]:.1f}s", delta=f"{m_b[1]-m_a[1]:.1f}s", delta_color="inverse")

# --- TAB 11: PROGRESSION ---
with tab_progress:
    st.markdown("### 📈 Individual Technician Progression")
    if not df.empty:
        c_prog1, c_prog2 = st.columns(2)
        unique_users = sorted([u for u in df['user_name'].unique() if u is not None])
        selected_user = c_prog1.selectbox("Select Technician", unique_users, key="prog_user_select")
        time_freq = c_prog2.selectbox("Time Aggregation", ["Daily", "Weekly", "Monthly"], key="prog_freq_select")
        freq_map = {"Daily": "D", "Weekly": "W", "Monthly": "M"}
        
        user_df = df[df['user_name'] == selected_user].copy()
        if not user_df.empty:
            user_df.set_index('dt', inplace=True)
            is_valid = ~user_df['event_type'].astype(str).str.contains('verify', case=False, na=False)
            user_df['is_valid_tx'] = is_valid.astype(int)
            user_df['is_error'] = ((user_df['discrepancy_qty'] != 0) & (is_valid)).astype(int)
            
            stats_over_time = user_df.resample(freq_map[time_freq]).agg({
                'is_valid_tx': 'sum',
                'is_error': 'sum',
                'machine_time_sec': 'mean'
            }).fillna(0).rename(columns={'is_valid_tx': 'Transactions', 'is_error': 'Errors', 'machine_time_sec': 'Speed'})
            
            st.plotly_chart(px.line(stats_over_time, x=stats_over_time.index, y='Speed', title="Speed Trend (Lower is Better)", markers=True), use_container_width=True)
            st.plotly_chart(px.bar(stats_over_time, x=stats_over_time.index, y='Transactions', title="Volume Trend"), use_container_width=True)
            
            st.subheader("🔍 Discrepancy Drill-Down")
            error_rows = user_df[user_df['is_error'] == 1].reset_index()
            if not error_rows.empty:
                st.dataframe(error_rows[['dt', 'device', 'med_desc', 'event_type', 'qty', 'discrepancy_qty', 'discrepancy_reason']], use_container_width=True)
            else:
                st.success("No discrepancies found.")

# --- TAB 12: ATTENDANCE AUDIT ---
with tab_attend:
    st.markdown("### 📋 Schedule vs. Reality Audit")
    
    if not df_sched.empty:
        if not df.empty:
            # Prepare Event Data
            df['clean_user'] = df['user_name'].astype(str).str.title()
            worked_days = df.groupby([df['dt'].dt.date, 'clean_user']).size().reset_index(name='tx_count')
            worked_days.columns = ['date_obj', 'user_name_link', 'tx_count']
            
            # Prepare Schedule Data
            df_sched['date_obj'] = df_sched['dt'].dt.date
            
            # Merge
            audit = pd.merge(
                df_sched, 
                worked_days, 
                left_on=['date_obj', 'staff_name'], 
                right_on=['date_obj', 'user_name_link'], 
                how='outer'
            )
            
            audit['staff_name'] = audit['staff_name'].fillna(audit['user_name_link'])
            audit['shift_type'] = audit['shift_type'].fillna("-")
            audit['tx_count'] = audit['tx_count'].fillna(0)
            
            def get_attendance_status(row):
                if row['shift_type'] != "-" and row['tx_count'] == 0:
                    if row['assignment_type'] == 'PTO': return "🌴 PTO"
                    return "❌ No Show / No Login"
                if row['shift_type'] == "-" and row['tx_count'] > 0:
                    return "➕ Unscheduled Pick-up"
                if row['shift_type'] != "-" and row['tx_count'] > 0:
                    return "✅ Present"
                return "Unknown"

            audit['Status'] = audit.apply(get_attendance_status, axis=1)
            
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Scheduled", len(audit[audit['shift_type'] != "-"]))
            m2.metric("Present", len(audit[audit['Status'] == "✅ Present"]))
            m3.metric("Unscheduled", len(audit[audit['Status'] == "➕ Unscheduled Pick-up"]))
            m4.metric("No Shows", len(audit[audit['Status'] == "❌ No Show / No Login"]))
            
            st.divider()
            
            filter_status = st.multiselect("Filter Status", options=["❌ No Show / No Login", "➕ Unscheduled Pick-up", "✅ Present", "🌴 PTO"], default=["❌ No Show / No Login", "➕ Unscheduled Pick-up"])
            view_df = audit.copy()
            if filter_status: view_df = view_df[view_df['Status'].isin(filter_status)]
                
            st.dataframe(view_df[['date_obj', 'staff_name', 'shift_type', 'Status', 'tx_count', 'note']].sort_values('date_obj', ascending=False), use_container_width=True)
        else:
            st.warning("Schedule loaded, but no Event data found to compare.")
            st.dataframe(df_sched)
    else:
        st.info("No Schedule Data found. Upload 'Staff Schedule' CSV.")
