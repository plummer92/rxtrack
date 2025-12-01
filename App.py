###############################################
# RXTRACK: EXECUTIVE DASHBOARD (FINAL STABLE)
# Features: Daily Reports, Financials, & Activity Logs
# Fixes: Clean Columns for Pends (Min/Max/Std Stock)
###############################################

import streamlit as st
import pandas as pd
import numpy as np
import hashlib
import psycopg2
from psycopg2.extras import execute_batch
import plotly.express as px
from datetime import datetime, timedelta, date
import re

# Page Config
st.set_page_config(
    page_title="RxTrack: Efficiency Dashboard", 
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
    <style>
    .metric-card { background-color: #f0f2f6; padding: 15px; border-radius: 10px; border-left: 5px solid #4CAF50; color: #31333F; }
    .metric-card h3 { color: #31333F; margin: 0; }
    .metric-card p { color: #31333F; margin: 0; }
    .missing-card { background-color: #ffebee; padding: 10px; border-radius: 5px; border-left: 5px solid #FF4B4B; margin-bottom: 10px; color: #b71c1c; }
    .cal-grid { display: flex; flex-wrap: wrap; gap: 2px; max-width: 100%; }
    .cal-day { width: 18px; height: 18px; border-radius: 2px; font-size: 8px; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; }
    .cal-present { background-color: #4CAF50; }
    .cal-missing { background-color: #FF4B4B; }
    .cal-empty { background-color: #e0e0e0; }
    .highlight { font-weight: bold; color: #2c3e50; }
    </style>
    """, unsafe_allow_html=True)

###########################################################
#                 HELPER FUNCTIONS
###########################################################
def seconds_to_mmss(seconds):
    if pd.isna(seconds) or seconds < 0: return "-"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"

def safe_to_date(val):
    if val is None: return datetime.today().date()
    if isinstance(val, date) and not isinstance(val, datetime): return val
    if isinstance(val, datetime): return val.date()
    if isinstance(val, pd.Timestamp): return val.date()
    try: return pd.to_datetime(val).date()
    except: return datetime.today().date()

###########################################################
#                 DATABASE CONNECTION
###########################################################
def get_db_connection():
    try:
        return psycopg2.connect(st.secrets["neon"]["db_url"])
    except Exception as e:
        st.error(f"❌ DB Connection Error: {e}")
        return None

def get_db_stats():
    conn = get_db_connection()
    if not conn: return 0, datetime.today().date(), datetime.today().date(), set()
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT MIN(dt), MAX(dt), COUNT(*) FROM events")
        result = cur.fetchone()
        
        min_dt = safe_to_date(result[0]) if result else datetime.today().date()
        max_dt = safe_to_date(result[1]) if result else datetime.today().date()
        total_rows = result[2] if result else 0
        
        present_dates = set()
        if total_rows > 0:
            cur.execute("SELECT DISTINCT DATE(dt) FROM events")
            present_dates = {safe_to_date(row[0]) for row in cur.fetchall()}
            
        cur.close()
        conn.close()
        return total_rows, min_dt, max_dt, present_dates
        
    except Exception as e:
        if conn: conn.close()
        return 0, datetime.today().date(), datetime.today().date(), set()

###########################################################
#                 DATA CLEANING
###########################################################
def generate_pk(row):
    subset = [str(x) for x in row.values if pd.notnull(x)]
    row_str = "|".join(subset)
    return hashlib.sha256(row_str.encode()).hexdigest()

def clean_dataframe(df):
    """Standard Daily Transaction Report"""
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
    required_cols = [
        "user_name", "device", "med_id", "med_desc", 
        "event_type", "dt", "qty", 
        "beginning_qty", "ending_qty",
        "discrepancy_qty", "discrepancy_reason", "resolution_dt"
    ]
    for col in required_cols:
        if col not in df.columns: df[col] = None

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df["resolution_dt"] = pd.to_datetime(df["resolution_dt"], errors="coerce")
    df = df.dropna(subset=["dt"]) 

    for c in ["qty", "beginning_qty", "ending_qty", "discrepancy_qty"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    df["dt"] = df["dt"].astype(str)
    df["resolution_dt"] = df["resolution_dt"].astype(str).replace('NaT', None)
    
    df["pk"] = df.apply(lambda r: generate_pk(r), axis=1)
    return df[required_cols + ["pk"]]

def clean_activity_log(df):
    """Activity Log (Pends/Adds) - Merges Min/Max rows"""
    df = df.copy()
    df.columns = df.columns.str.strip().str.replace(' ', '')
    
    df = df.rename(columns={
        "UserName": "user_name",
        "Device": "device",
        "TransactionDateTime": "dt",
        "Action": "action_type", 
        "ActivityType": "activity_category",
        "AffectedElement": "raw_element"
    })
    
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"])
    
    # Extract Med ID
    pattern = r'\((.*?)\)'
    df['med_id'] = df['raw_element'].astype(str).str.extract(pattern)[0]
    df = df.dropna(subset=['med_id'])

    # Extract Qty (Capacity/Max)
    # Looks for ": 6" at end of string
    qty_pattern = r':\s*(\d+)$'
    df['qty_extracted'] = df['raw_element'].astype(str).str.extract(qty_pattern)[0]
    df['qty_extracted'] = pd.to_numeric(df['qty_extracted'], errors='coerce').fillna(0)

    # Create grouping key (Round to minute to catch split-second Min/Max entries)
    df['dedup_time'] = df['dt'].dt.floor('Min')

    # --- SMART MERGE LOGIC ---
    # We want to merge "Min: 2" and "Max: 6" rows into one.
    # We assume the largest number for a given Med/Time is the MAX capacity.
    # We assume the smallest non-zero is MIN (simplified for display).
    
    grouped = df.groupby(['dedup_time', 'user_name', 'device', 'med_id', 'action_type', 'activity_category'], as_index=False).agg({
        'qty_extracted': ['max', 'min'],  # Capture both Max (Capacity) and Min
        'raw_element': 'first',           # Keep description
        'dt': 'first'                     # Keep original timestamp
    })
    
    # Flatten columns
    grouped.columns = ['dedup_time', 'user_name', 'device', 'med_id', 'action_type', 'activity_category', 'max_qty', 'min_qty', 'raw_element', 'dt']
    
    # Logic for Standard Stock Checkbox
    # If activity_category contains "Standard Stock", we mark it.
    grouped['is_standard'] = grouped['activity_category'].astype(str).str.contains('Standard Stock', case=False, na=False)
    grouped['std_stock_display'] = grouped['is_standard'].apply(lambda x: "☑️ Yes" if x else "☐ No")

    # Create Clean Med Name (remove ID)
    grouped['med_clean'] = grouped['raw_element'].str.split(' \(').str[0]

    # Format the Event Type to show details
    # e.g. "Add (Standard Stock) [Max: 6, Min: 2]"
    grouped['event_type'] = (
        grouped['action_type'].astype(str) + " (" + grouped['activity_category'].astype(str) + ")"
    )
    
    # We store MAX qty as the primary Quantity for charts
    grouped['qty'] = grouped['max_qty']
    
    # Packed Description for Storage: We store the metadata here so we can unpack it later
    grouped['med_desc'] = (
        grouped['med_clean'] + 
        " | StdStock: " + grouped['std_stock_display']
    )
    
    grouped['beginning_qty'] = grouped['min_qty'] # Store Min in Beg for storage
    grouped['ending_qty'] = grouped['max_qty']    # Store Max in End for storage
    
    # Standardize for DB
    required_cols = [
        "user_name", "device", "med_id", "med_desc", 
        "event_type", "dt", "qty", 
        "beginning_qty", "ending_qty",
        "discrepancy_qty", "discrepancy_reason", "resolution_dt"
    ]
    
    final_df = grouped.copy()
    for col in required_cols:
        if col not in final_df.columns: final_df[col] = None
    
    # Ensure numeric defaults
    final_df['discrepancy_qty'] = 0
    final_df['discrepancy_reason'] = None
    final_df['resolution_dt'] = None
    
    final_df["dt"] = final_df["dt"].astype(str)
    final_df["pk"] = final_df.apply(lambda r: generate_pk(r), axis=1)
    
    return final_df[required_cols + ["pk"]]

def clean_cost_dataframe(df):
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

def insert_batch(df, table_name="events"):
    conn = get_db_connection()
    if not conn: return
    cur = conn.cursor()
    if table_name == "events":
        sql = """
            INSERT INTO events (
                pk, user_name, device, med_id, med_desc, 
                event_type, dt, qty, beginning_qty, ending_qty,
                discrepancy_qty, discrepancy_reason, resolution_dt
            )
            VALUES (
                %(pk)s, %(user_name)s, %(device)s, %(med_id)s, %(med_desc)s, 
                %(event_type)s, %(dt)s, %(qty)s, %(beginning_qty)s, %(ending_qty)s,
                %(discrepancy_qty)s, %(discrepancy_reason)s, %(resolution_dt)s
            )
            ON CONFLICT (pk) DO NOTHING;
        """
    else:
        sql = """
            INSERT INTO med_costs (med_id, cost_per_unit)
            VALUES (%(med_id)s, %(cost_per_unit)s)
            ON CONFLICT (med_id) DO UPDATE 
            SET cost_per_unit = EXCLUDED.cost_per_unit;
        """
    rows = df.to_dict("records")
    try:
        execute_batch(cur, sql, rows, page_size=2000)
        conn.commit()
        st.success(f"✅ Securely processed {len(rows)} records into '{table_name}'.")
    except Exception as e:
        st.error(f"Error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

###########################################################
#            ANALYTICS LOGIC
###########################################################
@st.cache_data(ttl=300)
def load_data(start_date, end_date):
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    
    query = """
        SELECT e.*, c.cost_per_unit 
        FROM events e
        LEFT JOIN med_costs c ON e.med_id = c.med_id
        WHERE e.dt::date BETWEEN %s AND %s
    """
    try:
        df = pd.read_sql(query, conn, params=(start_date, end_date))
    except Exception as e:
        st.error(f"Query Error: {e}")
        conn.close()
        return pd.DataFrame()
    conn.close()
    
    if not df.empty:
        df["dt"] = pd.to_datetime(df["dt"])
        df["is_refill"] = df["event_type"].str.lower().str.contains("refill|load", na=False)
        if "cost_per_unit" not in df.columns: df["cost_per_unit"] = 0
        df["cost_per_unit"] = df["cost_per_unit"].fillna(0)
        
        df = df.sort_values(['user_name', 'dt'])
        
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['next_device'] = df.groupby('user_name')['device'].shift(-1)
        df['prev_dt'] = df.groupby('user_name')['dt'].shift(1)
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        
        df['duration_seconds'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        df['walk_seconds'] = (df['dt'] - df['prev_dt']).dt.total_seconds()
        df['gap_minutes'] = df['walk_seconds'] / 60
        df['path_taken'] = df['prev_device'].fillna('Start') + " ➡️ " + df['device']
        
        df['machine_time_sec'] = np.where(
            (df['device'] == df['next_device']) & (df['duration_seconds'] < 600), 
            df['duration_seconds'], 0
        )
        df['walk_time_sec'] = np.where(
            (df['device'] != df['prev_device']) & (df['walk_seconds'] < 1200),
            df['walk_seconds'], 0
        )

        df['is_new_session'] = np.where(
            (df['user_name'] != df['user_name'].shift(1)) | 
            (df['device'] != df['prev_device']) |
            (df['walk_seconds'] > 600), 
            1, 0
        )
        df['session_id'] = df['is_new_session'].cumsum()

        df['Machine Time'] = df['machine_time_sec'].apply(seconds_to_mmss)
        df['Walk Time'] = df['walk_time_sec'].apply(seconds_to_mmss)
        df['Timestamp'] = df['dt'].dt.strftime('%b %d, %I:%M:%S %p')
        df['Date'] = df['dt'].dt.date
        df['Hour'] = df['dt'].dt.hour
    
    return df

###########################################################
#                 DASHBOARD UI
###########################################################

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=50)
    st.title("RxTrack Executive")
    
    # --- 1. COVERAGE CALENDAR ---
    total_rows, min_db, max_db, present_dates = get_db_stats()
    
    with st.expander("💾 Coverage Calendar", expanded=True):
        st.write(f"**Records:** {total_rows:,}")
        delta = (max_db - min_db).days
        calendar_start = max_db - timedelta(days=90) if delta > 90 else min_db
        calendar_html = '<div class="cal-grid">'
        current_day = calendar_start
        while current_day <= max_db:
            if current_day in present_dates:
                color_class = "cal-present"
                tooltip = f"{current_day}: Data Found"
            else:
                color_class = "cal-missing"
                tooltip = f"{current_day}: MISSING"
            calendar_html += f'<div class="cal-day {color_class}" title="{tooltip}">{current_day.day}</div>'
            current_day += timedelta(days=1)
        calendar_html += '</div>'
        st.markdown(calendar_html, unsafe_allow_html=True)
        st.caption("Green = Data Found | Red = Missing")
    
    st.divider()
    
    default_start = max(min_db, max_db - timedelta(days=7))
    date_range = st.slider("Select Range", min_value=min_db, max_value=max_db, value=(min_db, max_db), format="MM/DD/YY", key=f"slider_{min_db}_{max_db}_{total_rows}")
    start_date, end_date = date_range
    
    st.divider()
    
    # --- 2. EXPLICIT FILE LOADER ---
    st.subheader("📤 Data Upload")
    upload_type = st.selectbox("Select File Type:", ["Daily Transaction Report", "Device Activity Log (Pends)", "Financial Price List"])
    uploaded = st.file_uploader(f"Upload {upload_type} (CSV/XLSX)", type=["csv","xlsx"])
    
    if uploaded:
        if st.button(f"Process {upload_type}"):
            try:
                if uploaded.name.endswith(".xlsx"): preview = pd.read_excel(uploaded, header=None, nrows=50)
                else: preview = pd.read_csv(uploaded, header=None, nrows=50)
                
                header_row_idx = None
                for idx, row in preview.iterrows():
                    row_str = str(row.values).lower()
                    if upload_type == "Daily Transaction Report":
                        if "username" in row_str and "device" in row_str and "affectedelement" not in row_str:
                            header_row_idx = idx
                            break
                    elif upload_type == "Device Activity Log (Pends)":
                        if "affectedelement" in row_str and "dispensingdevicename" in row_str:
                            header_row_idx = idx
                            break
                    elif upload_type == "Financial Price List":
                        if ("med" in row_str or "id" in row_str) and ("cost" in row_str or "price" in row_str):
                            header_row_idx = idx
                            break
                
                if header_row_idx is None:
                    st.error(f"❌ Could not find expected headers for {upload_type}. Please check the file.")
                else:
                    uploaded.seek(0)
                    if uploaded.name.endswith(".xlsx"): raw = pd.read_excel(uploaded, header=header_row_idx)
                    else: raw = pd.read_csv(uploaded, header=header_row_idx)
                    
                    if upload_type == "Daily Transaction Report":
                        clean = clean_dataframe(raw)
                        insert_batch(clean, "events")
                    elif upload_type == "Device Activity Log (Pends)":
                        clean = clean_activity_log(raw)
                        st.info(f"Deduplicated to {len(clean)} unique transactions.")
                        insert_batch(clean, "events")
                    elif upload_type == "Financial Price List":
                        clean = clean_cost_dataframe(raw)
                        insert_batch(clean, "med_costs")
                    st.cache_data.clear()
                    st.rerun()
            except Exception as e: st.error(f"Error processing file: {e}")

# Load Data
df = load_data(start_date, end_date)
if df.empty:
    st.info("👋 Ready for data.")
    st.stop()

# --- TABS ---
tab_over, tab_mine, tab_comp, tab_pends, tab_loads, tab_effic, tab_drill = st.tabs([
    "📊 Overview",
    "🚀 Process Mining",
    "🛡️ Compliance", 
    "📥 Pends", 
    "🚚 Loads",
    "⚡ Efficiency", 
    "🔍 Drill Down"
])

# --- TAB 1: OVERVIEW ---
with tab_over:
    st.markdown("### ⏱️ Operational Speed Analysis")
    session_stats = df.groupby('session_id').agg(total_machine_time=('machine_time_sec', 'sum'))
    avg_machine_time = session_stats['total_machine_time'].mean()
    c1, c2, c3 = st.columns(3)
    c1.metric("Transactions", f"{len(df):,}")
    c2.metric("Avg Session", f"{seconds_to_mmss(avg_machine_time)}")
    c3.metric("Active Techs", df['user_name'].nunique())
    st.divider()
    st.markdown("#### 🐢 Slowest Meds (Machine Time)")
    med_speed = df[df['machine_time_sec'] > 0].groupby('med_desc')['machine_time_sec'].mean().reset_index()
    med_counts = df['med_desc'].value_counts()
    med_speed = med_speed[med_speed['med_desc'].isin(med_counts[med_counts > 5].index)]
    top_slow = med_speed.sort_values('machine_time_sec', ascending=False).head(10)
    fig_slow = px.bar(top_slow, x='machine_time_sec', y='med_desc', orientation='h', title="Slowest Meds (Avg Sec)", color='machine_time_sec', color_continuous_scale='RdYlGn_r')
    fig_slow.update_layout(yaxis={'categoryorder':'total ascending'})
    st.plotly_chart(fig_slow)

# --- TAB 2: PROCESS MINING ---
with tab_mine:
    st.markdown("### 🗺️ Route & Labor Optimization")
    c_idle, c_route = st.columns(2)
    with c_idle:
        st.markdown("#### 🕵️ Idle Time Detector")
        idle_threshold = st.slider("Define 'Idle' Gap (Minutes)", 10, 60, 20)
        idle_events = df[(df['gap_minutes'] > idle_threshold) & (df['gap_minutes'] < 480)].copy()
        if not idle_events.empty:
            idle_stats = idle_events.groupby('user_name')['gap_minutes'].sum().reset_index().sort_values('gap_minutes', ascending=False).head(10)
            idle_stats['hours'] = idle_stats['gap_minutes'] / 60
            fig_idle = px.bar(idle_stats, x='hours', y='user_name', orientation='h', title=f"Total Idle Hours (Gaps > {idle_threshold}m)", color='hours', color_continuous_scale='Reds')
            fig_idle.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig_idle, use_container_width=True)
        else: st.success("No significant idle gaps found.")
    with c_route:
        st.markdown("#### 🛣️ Common Paths Taken")
        paths = df[df['device'] != df['prev_device']].copy()
        path_stats = paths.groupby('path_taken').agg(Count=('gap_minutes', 'count'), Avg_Min=('gap_minutes', 'mean')).reset_index()
        common_paths = path_stats[path_stats['Count'] > 5].sort_values('Avg_Min', ascending=True).head(10)
        st.dataframe(common_paths.style.format({'Avg_Min': '{:.1f} min'}), use_container_width=True, hide_index=True)
    st.divider()
    st.markdown("#### 📈 New Hire Benchmark")
    users = sorted(df['user_name'].dropna().unique().tolist())
    target_user = st.selectbox("Select New Hire to Benchmark:", ["Select User..."] + users)
    if target_user != "Select User...":
        daily_counts = df.groupby(['Date', 'user_name']).size().reset_index(name='count')
        team_avg = daily_counts.groupby('Date')['count'].mean().reset_index(name='Team Avg')
        user_stats = daily_counts[daily_counts['user_name'] == target_user].rename(columns={'count': 'User Performance'})
        benchmark = pd.merge(team_avg, user_stats[['Date', 'User Performance']], on='Date', how='left').fillna(0)
        fig_bench = px.line(benchmark, x='Date', y=['Team Avg', 'User Performance'], title="Daily Transaction Volume")
        st.plotly_chart(fig_bench, use_container_width=True)

# --- TAB 3: COMPLIANCE ---
with tab_comp:
    st.markdown("### 🛡️ Count Integrity")
    if 'discrepancy_qty' in df.columns:
        disc_df = df[df['discrepancy_qty'] != 0].copy()
    else: disc_df = pd.DataFrame()
    loss = (disc_df['discrepancy_qty'] * disc_df['cost_per_unit']).sum() if not disc_df.empty else 0
    c1, c2 = st.columns(2)
    c1.metric("Count Errors", len(disc_df))
    c2.metric("Financial Variance", f"${loss:,.2f}")
    if not disc_df.empty:
        c_left, c_right = st.columns(2)
        with c_left:
            user_errors = disc_df.groupby('user_name')['pk'].count().reset_index(name='Count').sort_values('Count', ascending=False).head(10)
            fig_user = px.bar(user_errors, x='Count', y='user_name', orientation='h', title="Top Users (Errors)")
            fig_user.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig_user, use_container_width=True)
        st.dataframe(disc_df[['Timestamp', 'user_name', 'device', 'med_desc', 'discrepancy_qty', 'discrepancy_reason']], use_container_width=True)

# --- TAB 4: PENDS (CONFIG) ---
with tab_pends:
    st.markdown("### 📥 Inventory Configuration (Adds & Pends)")
    pends_df = df[df['event_type'].astype(str).str.lower().str.contains('add|pend')].copy()
    c_f1, c_f2, c_f3 = st.columns(3)
    # FIX: Handle None in multiselect sort
    filter_user = c_f1.multiselect("Tech", sorted([x for x in pends_df['user_name'].unique() if x is not None]))
    filter_device = c_f2.multiselect("Device", sorted([x for x in pends_df['device'].unique() if x is not None]))
    filter_med = c_f3.multiselect("Med ID", sorted([x for x in pends_df['med_id'].unique() if x is not None]))
    if filter_user: pends_df = pends_df[pends_df['user_name'].isin(filter_user)]
    if filter_device: pends_df = pends_df[pends_df['device'].isin(filter_device)]
    if filter_med: pends_df = pends_df[pends_df['med_id'].isin(filter_med)]
    
    if not pends_df.empty:
        c1, c2, c3 = st.columns(3)
        c1.metric("Items Added", f"{len(pends_df):,}")
        c2.metric("Total Capacity Added", f"{int(pends_df['qty'].sum()):,}")
        c3.metric("Unique Meds", pends_df['med_id'].nunique())
        st.divider()
        
        # --- DISPLAY FIX: EXTRACT PACKED DATA FOR CLEAN COLUMNS ---
        # Logic: If ' | StdStock: ' is in description, we know it's a packed Activity Log entry
        # We extract the parts back out for the table
        
        # 1. Extract Standard Stock (True/False)
        pends_df['Standard Stock'] = pends_df['med_desc'].astype(str).apply(lambda x: "☑️" if "☑️ Yes" in x else "☐")
        
        # 2. Extract Clean Med Name (Everything before the packed data)
        pends_df['Medication'] = pends_df['med_desc'].astype(str).str.split(' | StdStock:').str[0]
        
        # 3. Rename Columns
        # We stored Min in 'beginning_qty' and Max in 'ending_qty' in clean_activity_log
        pends_df = pends_df.rename(columns={
            'beginning_qty': 'Min',
            'ending_qty': 'Max',
            'user_name': 'User',
            'device': 'Device',
            'event_type': 'Activity'
        })
        
        st.markdown("#### 📝 Detailed Config Log")
        st.dataframe(
            pends_df[['Timestamp', 'User', 'Device', 'Activity', 'Medication', 'Min', 'Max', 'Standard Stock']], 
            use_container_width=True, 
            hide_index=True
        )
    else: st.info("No Pends/Adds found.")

# --- TAB 5: LOADS (RESTOCK) ---
with tab_loads:
    st.markdown("### 🚚 Stock Movement (Loads, Unloads, Refills)")
    loads_df = df[df['event_type'].astype(str).str.lower().str.contains('load|unload|refill')].copy()
    if not loads_df.empty:
        l_c1, l_c2, l_c3 = st.columns(3)
        l_c1.metric("Restock Events", f"{len(loads_df):,}")
        l_c2.metric("Units Moved", f"{int(loads_df['qty'].sum()):,}")
        l_c3.metric("Meds Handled", loads_df['med_id'].nunique())
        st.divider()
        c_chart, c_user = st.columns(2)
        with c_chart:
             type_counts = loads_df['event_type'].value_counts().reset_index()
             type_counts.columns = ['Action', 'Count']
             st.plotly_chart(px.pie(type_counts, names='Action', values='Count', title="Activity Breakdown", hole=0.4), use_container_width=True)
        with c_user:
             top_loaders = loads_df.groupby('user_name')['qty'].sum().reset_index().sort_values('qty', ascending=False).head(10)
             fig_load_user = px.bar(top_loaders, x='qty', y='user_name', orientation='h', title="Top Staff by Volume Moved", color='qty', color_continuous_scale='Blues')
             fig_load_user.update_layout(yaxis={'categoryorder':'total ascending'})
             st.plotly_chart(fig_load_user, use_container_width=True)
        st.dataframe(loads_df[['Timestamp', 'user_name', 'device', 'event_type', 'med_desc', 'qty']], use_container_width=True)
    else: st.info("No Load/Unload activity found.")

# --- TAB 6: EFFICIENCY ---
with tab_effic:
    c_left, c_right = st.columns([2, 1])
    with c_left:
        st.markdown("### 📉 Inefficient Refill Candidates")
        refills = df[df['is_refill']].copy()
        mask_exclude = refills['med_desc'].str.lower().str.contains('keys|cassette', na=False)
        refills = refills[~mask_exclude]
        effic_stats = refills.groupby(['device', 'med_desc']).agg(Trips=('pk', 'count'), Avg_Added=('qty', 'mean')).reset_index()
        inefficient = effic_stats[(effic_stats['Trips'] >= 3) & (effic_stats['Avg_Added'] < 3)].sort_values('Trips', ascending=False).head(15)
        if not inefficient.empty:
            fig_bar = px.bar(inefficient, x='Trips', y='med_desc', orientation='h', color='Avg_Added', color_continuous_scale='OrRd', title="High Effort, Low Yield")
            fig_bar.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig_bar, use_container_width=True)
            meds = inefficient['med_desc'].unique().tolist()
            sel = st.selectbox("Select Med:", ["Select..."] + meds)
            if sel != "Select...":
                med_df = refills[refills['med_desc'] == sel]
                c_a, c_b = st.columns(2)
                with c_a:
                    bd = med_df.groupby('device')['pk'].count().reset_index(name='Trips').sort_values('Trips', ascending=False)
                    st.plotly_chart(px.bar(bd, x='Trips', y='device', orientation='h', title="Where?"), use_container_width=True)
                with c_b:
                    dt = med_df.groupby('Date')['pk'].count().reset_index(name='Trips')
                    st.plotly_chart(px.bar(dt, x='Date', y='Trips', title="When?"), use_container_width=True)
    with c_right:
        traf = df[~df['device'].str.lower().str.contains('pharm', na=False)].copy()
        visits = traf.groupby('device')['session_id'].nunique().reset_index(name='Visits').sort_values('Visits', ascending=False).head(10)
        st.plotly_chart(px.bar(visits, x='Visits', y='device', orientation='h', title="Most Visited"), use_container_width=True)

# --- TAB 7: DRILL DOWN ---
with tab_drill:
    st.header("🔍 Interactive Data Explorer")
    c1, c2, c3, c4 = st.columns(4)
    u = c1.multiselect("Technician", sorted([x for x in df['user_name'].unique() if x is not None]))
    d = c2.multiselect("Device", sorted([x for x in df['device'].unique() if x is not None]))
    m = c3.multiselect("Medication", sorted([x for x in df['med_desc'].unique() if x is not None]))
    e = c4.multiselect("Event Type", sorted([x for x in df['event_type'].unique() if x is not None]))
    filt = df.copy()
    if u: filt = filt[filt['user_name'].isin(u)]
    if d: filt = filt[filt['device'].isin(d)]
    if m: filt = filt[filt['med_desc'].isin(m)]
    if e: filt = filt[filt['event_type'].isin(e)]
    st.markdown(f"**Showing {len(filt):,} records**")
    cols = ['Timestamp', 'Walk Time', 'Machine Time', 'user_name', 'device', 'event_type', 'med_desc', 'qty', 'discrepancy_qty', 'cost_per_unit']
    v_cols = [c for c in cols if c in filt.columns]
    st.dataframe(filt[v_cols].sort_values('Timestamp', ascending=True), use_container_width=True, hide_index=True)
