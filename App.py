###############################################
# RXTRACK: EXECUTIVE DASHBOARD (FINAL DEBUG)
# Fixes: Slider Defaults & Date Range Visibility
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
    # Default return if connection fails initially
    if not conn: return datetime.today().date(), datetime.today().date()
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT MIN(dt), MAX(dt) FROM events")
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        # If DB has data, return the real dates
        if result and result[0] and result[1]:
            # Convert timestamp to date object
            min_d = result[0] if isinstance(result[0], datetime) else pd.to_datetime(result[0])
            max_d = result[1] if isinstance(result[1], datetime) else pd.to_datetime(result[1])
            return min_d.date(), max_d.date()
            
    except Exception as e:
        # If query fails, show error so we know why slider is broken
        st.sidebar.error(f"Date Error: {e}")
        if conn: conn.close()
    
    # Fallback only if DB is truly empty
    return datetime.today().date(), datetime.today().date()

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
        if col not in df.columns:
            df[col] = None

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df["resolution_dt"] = pd.to_datetime(df["resolution_dt"], errors="coerce")
    
    df = df.dropna(subset=["dt"]) 

    for c in ["qty", "beginning_qty", "ending_qty", "discrepancy_qty"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    df["dt"] = df["dt"].astype(str)
    df["resolution_dt"] = df["resolution_dt"].astype(str).replace('NaT', None)
    
    df["pk"] = df.apply(lambda r: generate_pk(r), axis=1)
    
    return df[required_cols + ["pk"]]

def insert_batch(df):
    conn = get_db_connection()
    if not conn: return
    cur = conn.cursor()
    
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
#            ANALYTICS LOGIC
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
        
        df = df.sort_values(['user_name', 'dt'])
        
        # Look Ahead/Behind logic
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['next_device'] = df.groupby('user_name')['device'].shift(-1)
        
        df['prev_dt'] = df.groupby('user_name')['dt'].shift(1)
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        
        df['duration_seconds'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        df['walk_seconds'] = (df['dt'] - df['prev_dt']).dt.total_seconds()
        
        # Machine Time
        df['machine_time_sec'] = np.where(
            (df['device'] == df['next_device']) & (df['duration_seconds'] < 600), 
            df['duration_seconds'], 
            0
        )
        
        # Walk Time
        df['walk_time_sec'] = np.where(
            (df['device'] != df['prev_device']) & (df['walk_seconds'] < 1200),
            df['walk_seconds'],
            0
        )

        # Session ID
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
        df['Date'] = df['dt'].dt.date
        df['Hour'] = df['dt'].dt.hour
    
    return df

###########################################################
#                 DASHBOARD UI
###########################################################

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=50)
    st.title("RxTrack Executive")
    
    # --- DB HEALTH CHECK ---
    min_db, max_db = get_db_date_range()
    
    # Get row count for key generation
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events")
        total_rows = cur.fetchone()[0]
        cur.close()
        conn.close()
    except:
        total_rows = 0
        
    with st.expander("💾 Database Stats", expanded=True):
        st.write(f"**Rows:** {total_rows:,}")
        st.write(f"**Earliest:** {min_db}") # Shows start date
        st.write(f"**Latest:** {max_db}")   # Shows end date
    
    st.divider()
    
    st.markdown("### 📅 Date Range")
    
    # FIX: Default to FULL range so user sees new data immediately
    date_range = st.slider(
        "Select Range", 
        min_value=min_db, 
        max_value=max_db,
        value=(min_db, max_db), # Set handles to full width
        format="MM/DD/YY",
        key=f"slider_{min_db}_{max_db}_{total_rows}"
    )
    start_date, end_date = date_range
    
    st.divider()
    
    uploaded = st.file_uploader("Upload Daily Report", type=["csv","xlsx"])
    if uploaded:
        try:
            if uploaded.name.endswith(".xlsx"):
                preview = pd.read_excel(uploaded, header=None, nrows=50)
            else:
                preview = pd.read_csv(uploaded, header=None, nrows=50)
            
            header_row_idx = None
            for idx, row in preview.iterrows():
                row_str = str(row.values).lower()
                if "username" in row_str and "device" in row_str:
                    header_row_idx = idx
                    break
            
            if header_row_idx is None:
                st.error("❌ Could not find headers (UserName/Device) in first 50 rows.")
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
tab_over, tab_compliance, tab_stock, tab_effic, tab_drill = st.tabs([
    "📊 Overview & Speed",
    "🛡️ Compliance", 
    "🚨 Stockouts", 
    "⚡ Efficiency", 
    "🔍 Drill Down"
])

# --- TAB 1: OVERVIEW ---
with tab_over:
    st.markdown("### ⏱️ Operational Speed Analysis")
    session_stats = df.groupby('session_id').agg(total_machine_time=('machine_time_sec', 'sum'))
    avg_machine_time = session_stats['total_machine_time'].mean()
    avg_walk_time = df[df['walk_time_sec'] > 0]['walk_time_sec'].mean()
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Transactions", f"{len(df):,}")
    c2.metric("Avg Session", f"{seconds_to_mmss(avg_machine_time)}")
    c3.metric("Avg Walk", f"{seconds_to_mmss(avg_walk_time)}")
    c4.metric("Active Techs", df['user_name'].nunique())
    
    st.divider()
    
    st.markdown("#### 🐢 Slowest Meds (Machine Time)")
    med_speed = df[df['machine_time_sec'] > 0].groupby('med_desc')['machine_time_sec'].mean().reset_index()
    med_counts = df['med_desc'].value_counts()
    med_speed = med_speed[med_speed['med_desc'].isin(med_counts[med_counts > 5].index)]
    top_slow = med_speed.sort_values('machine_time_sec', ascending=False).head(10)
    
    fig_slow = px.bar(top_slow, x='machine_time_sec', y='med_desc', orientation='h',
                      title="Slowest Meds to Process (Avg Seconds)", color='machine_time_sec', color_continuous_scale='RdYlGn_r')
    fig_slow.update_layout(yaxis={'categoryorder':'total ascending'})
    st.plotly_chart(fig_slow)

# --- TAB 2: COMPLIANCE ---
with tab_compliance:
    st.markdown("### 🛡️ Count Integrity & Accuracy")
    
    if 'discrepancy_qty' in df.columns:
        disc_df = df[df['discrepancy_qty'] != 0].copy()
    else:
        disc_df = pd.DataFrame()
    
    c1, c2 = st.columns(2)
    c1.metric("Total Count Errors", len(disc_df))
    if not disc_df.empty:
        c2.metric("Net Variance", int(disc_df['discrepancy_qty'].sum()))
    else:
        c2.metric("Net Variance", 0)
    
    st.divider()
    
    if not disc_df.empty:
        c_left, c_right = st.columns(2)
        with c_left:
            user_errors = disc_df.groupby('user_name').agg(Errors=('pk', 'count'), Net_Variance=('discrepancy_qty', 'sum')).reset_index().sort_values('Errors', ascending=False).head(10)
            fig_user = px.bar(user_errors, x='Errors', y='user_name', orientation='h', title="Top Users (Discrepancies)", color='Net_Variance', color_continuous_scale='RdBu')
            fig_user.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig_user, use_container_width=True)
        with c_right:
            med_errors = disc_df.groupby('med_desc')['pk'].count().reset_index(name='Count').sort_values('Count', ascending=False).head(10)
            fig_med = px.bar(med_errors, x='Count', y='med_desc', orientation='h', title="Top Meds (Errors)")
            fig_med.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig_med, use_container_width=True)
        st.dataframe(disc_df[['Timestamp', 'user_name', 'device', 'med_desc', 'discrepancy_qty', 'discrepancy_reason']], use_container_width=True, hide_index=True)
    elif 'discrepancy_qty' in df.columns:
        st.success("✅ No discrepancies found!")

# --- TAB 3: STOCKOUTS ---
with tab_stock:
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

# --- TAB 4: EFFICIENCY ---
with tab_effic:
    c_left, c_right = st.columns([2, 1])
    with c_left:
        st.markdown("### 📉 Inefficient Refill Candidates")
        refills = df[df['is_refill']].copy()
        mask_exclude = refills['med_desc'].str.lower().str.contains('keys|cassette', na=False)
        refills = refills[~mask_exclude]
        
        effic_stats = refills.groupby(['device', 'med_desc']).agg(
            Trips=('pk', 'count'), Avg_Added=('qty', 'mean')
        ).reset_index()
        
        inefficient = effic_stats[ (effic_stats['Trips'] >= 3) & (effic_stats['Avg_Added'] < 3) ]
        inefficient = inefficient.sort_values('Trips', ascending=False).head(15)
        
        if not inefficient.empty:
            fig_bar = px.bar(inefficient, x='Trips', y='med_desc', orientation='h', color='Avg_Added', color_continuous_scale='OrRd')
            fig_bar.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig_bar, use_container_width=True)
            
            st.markdown("#### 🕵️ Med Detective")
            med_list = inefficient['med_desc'].unique().tolist()
            selected_med = st.selectbox("Select Med:", ["Select..."] + med_list)
            
            if selected_med != "Select...":
                med_df = refills[refills['med_desc'] == selected_med]
                c_dev, c_day = st.columns(2)
                with c_dev:
                    med_breakdown = med_df.groupby('device').agg(Trips=('pk', 'count')).reset_index().sort_values('Trips', ascending=False)
                    fig_bd = px.bar(med_breakdown, x='Trips', y='device', orientation='h', title=f"Where?", color_discrete_sequence=['#FF4B4B'])
                    fig_bd.update_layout(yaxis={'categoryorder':'total ascending'})
                    st.plotly_chart(fig_bd, use_container_width=True)
                with c_day:
                    daily_trend = med_df.groupby('Date').agg(Trips=('pk', 'count')).reset_index()
                    fig_trend = px.bar(daily_trend, x='Date', y='Trips', title=f"When?", color_discrete_sequence=['#3366CC'])
                    st.plotly_chart(fig_trend, use_container_width=True)
                
                # NEW: WHO Analysis
                c_who, c_log = st.columns([1, 2])
                with c_who:
                    who_stats = med_df.groupby('user_name').agg(Trips=('pk', 'count')).reset_index().sort_values('Trips', ascending=False).head(10)
                    fig_who = px.bar(who_stats, x='Trips', y='user_name', orientation='h', title="Who?", color_discrete_sequence=['#00CC96'])
                    fig_who.update_layout(yaxis={'categoryorder':'total ascending'})
                    st.plotly_chart(fig_who, use_container_width=True)
                with c_log:
                    st.markdown("**Exact Delivery Times**")
                    st.dataframe(med_df[['Timestamp', 'user_name', 'device', 'qty']].sort_values('Timestamp', ascending=False), use_container_width=True, hide_index=True)
        else:
            st.success("Refill efficiency looks good.")
    
    with c_right:
        st.markdown("### 🚦 Traffic Analysis")
        traffic_df = df[~df['med_desc'].str.lower().str.contains('keys|cassette', na=False)].copy()
        traffic_df = traffic_df[~traffic_df['device'].str.lower().str.contains('pharm', na=False)]
        
        device_visits = traffic_df.groupby('device')['session_id'].nunique().reset_index(name='Visits').sort_values('Visits', ascending=False).head(10)
        fig_dev = px.bar(device_visits, x='Visits', y='device', orientation='h', title="Most Visited")
        fig_dev.update_layout(yaxis={'categoryorder':'total ascending'})
        st.plotly_chart(fig_dev, use_container_width=True)
        
        hourly = traffic_df.groupby(['device', 'Hour'])['session_id'].nunique().reset_index(name='Visits')
        fig_heat = px.density_heatmap(hourly, x='Hour', y='device', z='Visits', title="Heatmap", color_continuous_scale='Viridis')
        st.plotly_chart(fig_heat, use_container_width=True)

# --- TAB 5: DRILL DOWN ---
with tab_drill:
    st.header("🔍 Interactive Data Explorer")
    c1, c2, c3, c4 = st.columns(4)
    sel_users = c1.multiselect("Technician", sorted([x for x in df['user_name'].unique() if x is not None]))
    sel_devices = c2.multiselect("Device", sorted([x for x in df['device'].unique() if x is not None]))
    sel_meds = c3.multiselect("Medication", sorted([x for x in df['med_desc'].unique() if x is not None]))
    sel_events = c4.multiselect("Event Type", sorted([x for x in df['event_type'].unique() if x is not None]))
    
    filtered = df.copy()
    if sel_users: filtered = filtered[filtered['user_name'].isin(sel_users)]
    if sel_devices: filtered = filtered[filtered['device'].isin(sel_devices)]
    if sel_meds: filtered = filtered[filtered['med_desc'].isin(sel_meds)]
    if sel_events: filtered = filtered[filtered['event_type'].isin(sel_events)]
        
    st.markdown(f"**Showing {len(filtered):,} records**")
    cols = ['Timestamp', 'Walk Time', 'Machine Time', 'user_name', 'device', 'event_type', 'med_desc', 'qty', 'discrepancy_qty']
    valid_cols = [c for c in cols if c in filtered.columns]
    st.dataframe(filtered[valid_cols].sort_values('Timestamp', ascending=False), use_container_width=True, hide_index=True)