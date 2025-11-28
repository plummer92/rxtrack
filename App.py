###############################################
# RXTRACK: EXECUTIVE DASHBOARD
# Includes Overview, Machine vs. Walk Time, & Drill Down
###############################################

import streamlit as st
import pandas as pd
import numpy as np
import hashlib
import psycopg2
from psycopg2.extras import execute_batch
import plotly.express as px
from datetime import datetime, timedelta

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
    .metric-card { background-color: #f9f9f9; padding: 15px; border-radius: 10px; border-left: 5px solid #4CAF50; }
    .stockout-card { border-left: 5px solid #FF4B4B; }
    .highlight { font-weight: bold; color: #2c3e50; }
    </style>
    """, unsafe_allow_html=True)

###########################################################
#                 HELPER FUNCTIONS
###########################################################
def seconds_to_mmss(seconds):
    """Converts seconds (e.g. 95) to MM:SS format (e.g. 01:35)"""
    if pd.isna(seconds) or seconds < 0:
        return "-"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"

###########################################################
#                 DATABASE CONNECTION
###########################################################
def get_db_connection():
    try:
        return psycopg2.connect(st.secrets["neon"]["db_url"])
    except Exception as e:
        st.error(f"❌ DB Connection Error: {e}")
        return None

def get_db_date_range():
    """Gets the absolute min and max dates from the DB for the slider"""
    conn = get_db_connection()
    if not conn: return datetime.today().date(), datetime.today().date()
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT MIN(dt), MAX(dt) FROM events")
        min_dt, max_dt = cur.fetchone()
        cur.close()
        conn.close()
        
        if min_dt and max_dt:
            return min_dt.date(), max_dt.date()
    except:
        if conn: conn.close()
    
    return datetime.today().date() - timedelta(days=7), datetime.today().date()

###########################################################
#                 DATA CLEANING
###########################################################
def generate_pk(row):
    """Stable unique hash for deduplication."""
    row_str = "|".join(str(v) for v in row.values)
    return hashlib.sha256(row_str.encode()).hexdigest()

def clean_dataframe(df):
    df = df.copy()
    
    colmap = {
        "UserName": "user_name", "UserID": "user_id", "Device": "device",
        "MedID": "med_id", "MedDescription": "med_desc",
        "TransactionType": "event_type", "TransactionDateTime": "dt",
        "Quantity": "qty", "Beg": "beginning_qty", "End": "ending_qty"
    }
    
    df = df.rename(columns=colmap)
    
    required_cols = [
        "user_name", "device", "med_id", "med_desc", 
        "event_type", "dt", "qty", 
        "beginning_qty", "ending_qty"
    ]
    
    for col in required_cols:
        if col not in df.columns:
            df[col] = None

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"]) 

    for c in ["qty", "beginning_qty", "ending_qty"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    df["dt"] = df["dt"].astype(str)
    df["pk"] = df.apply(lambda r: generate_pk(r), axis=1)
    
    return df[required_cols + ["pk"]]

def insert_batch(df):
    conn = get_db_connection()
    if not conn: return
    cur = conn.cursor()
    
    sql = """
        INSERT INTO events (
            pk, user_name, device, med_id, med_desc, 
            event_type, dt, qty, beginning_qty, ending_qty
        )
        VALUES (
            %(pk)s, %(user_name)s, %(device)s, %(med_id)s, %(med_desc)s, 
            %(event_type)s, %(dt)s, %(qty)s, %(beginning_qty)s, %(ending_qty)s
        )
        ON CONFLICT (pk) DO NOTHING;
    """
    
    rows = df.to_dict("records")
    try:
        execute_batch(cur, sql, rows, page_size=2000)
        conn.commit()
        st.success(f"✅ Securely processed {len(rows)} records.")
    except Exception as e:
        st.error(f"Error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

###########################################################
#            ANALYTICS LOGIC (UPDATED)
###########################################################
@st.cache_data(ttl=300)
def load_data(start_date, end_date):
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    
    query = """
        SELECT * FROM events 
        WHERE dt::date BETWEEN %s AND %s
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
        
        # --- ADVANCED TIME LOGIC ---
        df = df.sort_values(['user_name', 'dt'])
        
        # Look Ahead (Next Event) to determine Dwell Time
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['next_device'] = df.groupby('user_name')['device'].shift(-1)
        
        # Look Behind (Previous Event) to determine Walk Time
        df['prev_dt'] = df.groupby('user_name')['dt'].shift(1)
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        
        # Calculate raw durations (in seconds)
        # Duration of CURRENT task (Time until next event)
        df['duration_seconds'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        # Time taken to GET here (Time since last event)
        df['walk_seconds'] = (df['dt'] - df['prev_dt']).dt.total_seconds()
        
        # --- LOGIC SEPARATION ---
        # Machine Time: If next event is SAME device, the duration is "Machine Time"
        # We cap this at 600s (10 min) to filter out lunch breaks/shift ends
        df['machine_time_sec'] = np.where(
            (df['device'] == df['next_device']) & (df['duration_seconds'] < 600), 
            df['duration_seconds'], 
            0
        )
        
        # Walk Time: If prev event was DIFFERENT device, the gap is "Walk Time"
        # Capped at 1200s (20 min)
        df['walk_time_sec'] = np.where(
            (df['device'] != df['prev_device']) & (df['walk_seconds'] < 1200),
            df['walk_seconds'],
            0
        )

        # --- SESSION IDENTIFICATION ---
        # Identify start of a new session (Change in user, device, or long gap)
        df['is_new_session'] = np.where(
            (df['user_name'] != df['user_name'].shift(1)) | 
            (df['device'] != df['prev_device']) |
            (df['walk_seconds'] > 600), 
            1, 0
        )
        df['session_id'] = df['is_new_session'].cumsum()

        # Formatting
        df['Machine Time'] = df['machine_time_sec'].apply(seconds_to_mmss)
        df['Walk Time'] = df['walk_time_sec'].apply(seconds_to_mmss)
        df['Timestamp'] = df['dt'].dt.strftime('%b %d, %I:%M %p')
    
    return df

###########################################################
#                 DASHBOARD UI
###########################################################

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=50)
    st.title("RxTrack Executive")
    
    min_db, max_db = get_db_date_range()
    
    st.markdown("### 📅 Date Range")
    default_start = max(min_db, max_db - timedelta(days=7))
    date_range = st.slider(
        "Select Range", min_value=min_db, max_value=max_db,
        value=(default_start, max_db), format="MM/DD/YY"
    )
    start_date, end_date = date_range
    
    st.divider()
    
    uploaded = st.file_uploader("Upload Daily Report", type=["csv","xlsx"])
    if uploaded:
        try:
            if uploaded.name.endswith(".xlsx"):
                preview = pd.read_excel(uploaded, header=None, nrows=20)
            else:
                preview = pd.read_csv(uploaded, header=None, nrows=20)
            
            header_row_idx = None
            for idx, row in preview.iterrows():
                row_str = str(row.values).lower()
                if "username" in row_str and "device" in row_str:
                    header_row_idx = idx
                    break
            
            if header_row_idx is None:
                st.error("❌ Could not find headers (UserName/Device).")
            else:
                uploaded.seek(0)
                if uploaded.name.endswith(".xlsx"):
                    raw = pd.read_excel(uploaded, header=header_row_idx)
                else:
                    raw = pd.read_csv(uploaded, header=header_row_idx)
                
                clean = clean_dataframe(raw)
                st.success(f"✅ Found {len(clean)} rows.")
                
                if st.button("Process & Save to DB"):
                    insert_batch(clean)
                    st.cache_data.clear()
                    st.rerun()

        except Exception as e:
            st.error(f"Error reading file: {e}")

# Load Data
df = load_data(start_date, end_date)

if df.empty:
    st.info("👋 Ready for data. Upload a Pyxis report to begin.")
    st.stop()

# --- TABS ---
tab_over, tab_stock, tab_effic, tab_drill = st.tabs([
    "📊 Overview & Speed",
    "🚨 Stockout Risk", 
    "⚡ Efficiency", 
    "🔍 Drill Down"
])

# --- TAB 1: OVERVIEW & SPEED ---
with tab_over:
    st.markdown("### ⏱️ Operational Speed Analysis")
    
    # Calculate Session Stats
    # Group by session ID to find total time spent at machine per visit
    session_stats = df.groupby('session_id').agg(
        user=('user_name', 'first'),
        device=('device', 'first'),
        total_machine_time=('machine_time_sec', 'sum'),
        events=('pk', 'count')
    )
    
    avg_machine_time = session_stats['total_machine_time'].mean()
    avg_walk_time = df[df['walk_time_sec'] > 0]['walk_time_sec'].mean()
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Transactions", f"{len(df):,}")
    c2.metric("Avg Session Time", f"{seconds_to_mmss(avg_machine_time)}", help="Avg time spent standing at one machine per visit")
    c3.metric("Avg Walk Time", f"{seconds_to_mmss(avg_walk_time)}", help="Avg time between different machines")
    c4.metric("Active Techs", df['user_name'].nunique())
    
    st.divider()
    
    # Slowest Meds Chart
    st.markdown("#### 🐢 Slowest Meds to Process (Machine Time)")
    st.markdown("Average time spent *after* scanning this med before the next action (at the same machine). High numbers = Hard to handle meds.")
    
    # Filter for machine time only (>0)
    med_speed = df[df['machine_time_sec'] > 0].groupby('med_desc')['machine_time_sec'].mean().reset_index()
    # Filter for significant data (at least appeared 5 times)
    med_counts = df['med_desc'].value_counts()
    med_speed = med_speed[med_speed['med_desc'].isin(med_counts[med_counts > 5].index)]
    
    top_slow = med_speed.sort_values('machine_time_sec', ascending=False).head(10)
    
    fig_slow = px.bar(top_slow, x='machine_time_sec', y='med_desc', orientation='h',
                      title="Top 10 Slowest Meds (Avg Seconds per Transaction)",
                      labels={'machine_time_sec': 'Seconds', 'med_desc': 'Medication'},
                      color='machine_time_sec', color_continuous_scale='RdYlGn_r')
    fig_slow.update_layout(yaxis={'categoryorder':'total ascending'})
    st.plotly_chart(fig_slow, use_container_width=True)

# --- TAB 2: STOCKOUTS ---
with tab_stockout:
    all_refills = df[df['is_refill']].copy()
    all_refills = all_refills.sort_values(['device', 'med_desc', 'dt'])
    
    all_refills['prev_refill_dt'] = all_refills.groupby(['device', 'med_desc'])['dt'].shift(1)
    all_refills['burn_duration'] = all_refills['dt'] - all_refills['prev_refill_dt']
    
    stockouts = all_refills[all_refills['beginning_qty'] == 0].copy()
    
    def format_burn_rate(td):
        if pd.isna(td): return "First Record (N/A)"
        total_seconds = int(td.total_seconds())
        days, rem = divmod(total_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        if days > 0: return f"{days}d {hours}h"
        return f"{hours}h {minutes}m"

    stockouts['Time Since Last Refill'] = stockouts['burn_duration'].apply(format_burn_rate)
    
    st.markdown(f"### ⚠️ {len(stockouts)} Stockout Events")
    
    if not stockouts.empty:
        c_hot, c_list = st.columns([1, 2])
        with c_hot:
            hotspots = stockouts['device'].value_counts().reset_index()
            hotspots.columns = ['Device', 'Count']
            fig = px.bar(hotspots.head(10), x='Count', y='Device', orientation='h', color_discrete_sequence=['#FF4B4B'])
            fig.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig, use_container_width=True)
        with c_list:
            cols = ['Time Since Last Refill', 'Timestamp', 'device', 'med_desc', 'qty', 'user_name']
            st.dataframe(stockouts.sort_values('dt', ascending=False)[cols], use_container_width=True, hide_index=True)
    else:
        st.success("✅ Zero stockouts found.")

# --- TAB 3: EFFICIENCY ---
with tab_effic:
    st.markdown("### 📉 Low-Yield Refill Matrix")
    
    refills = df[df['is_refill']].copy()
    effic_stats = refills.groupby(['device', 'med_desc']).agg(
        Trips=('pk', 'count'),
        Avg_Added=('qty', 'mean'),
        Total_Added=('qty', 'sum')
    ).reset_index()
    
    inefficient = effic_stats[ (effic_stats['Trips'] >= 3) & (effic_stats['Avg_Added'] < 3) ]
    
    if not inefficient.empty:
        fig_bub = px.scatter(inefficient, x='Trips', y='Avg_Added', 
                             size='Total_Added', hover_name='med_desc', color='device',
                             title="Inefficiency Bubbles (Size = Total Stock Added)")
        st.plotly_chart(fig_bub, use_container_width=True)
    else:
        st.success("Refill efficiency looks good.")

# --- TAB 4: DRILL DOWN ---
with tab_drill:
    st.header("🔍 Interactive Data Explorer")
    
    # Filters Row 1
    c1, c2, c3, c4 = st.columns(4)
    
    all_users = sorted([x for x in df['user_name'].unique() if x is not None])
    all_devices = sorted([x for x in df['device'].unique() if x is not None])
    all_meds = sorted([x for x in df['med_desc'].unique() if x is not None])
    all_events = sorted([x for x in df['event_type'].unique() if x is not None])
    
    sel_users = c1.multiselect("Technician", all_users)
    sel_devices = c2.multiselect("Device", all_devices)
    sel_meds = c3.multiselect("Medication", all_meds)
    sel_events = c4.multiselect("Event Type", all_events)
    
    filtered = df.copy()
    
    # Apply Filters
    if sel_users: filtered = filtered[filtered['user_name'].isin(sel_users)]
    if sel_devices: filtered = filtered[filtered['device'].isin(sel_devices)]
    if sel_meds: filtered = filtered[filtered['med_desc'].isin(sel_meds)]
    if sel_events: filtered = filtered[filtered['event_type'].isin(sel_events)]
        
    st.markdown(f"**Showing {len(filtered):,} records**")
    
    display_cols = [
        'Timestamp', 
        'Walk Time',      # Time to get here from previous device
        'Machine Time',   # Time spent doing this task (before next task at same device)
        'user_name', 
        'device', 
        'event_type', 
        'med_desc', 
        'qty', 
        'beginning_qty', 
        'ending_qty'
    ]
    
    valid_cols = [c for c in display_cols if c in filtered.columns]
    
    st.dataframe(
        filtered[valid_cols].sort_values('Timestamp', ascending=False), 
        use_container_width=True, 
        hide_index=True
    )