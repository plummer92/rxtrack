###############################################
# RXTRACK: STEWARDSHIP & DRILL DOWN EDITION
# Includes Smart File Loader & MM:SS Time Formatting
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
        return "00:00"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"

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

###########################################################
#                 DATA CLEANING
###########################################################
def generate_pk(row):
    """Stable unique hash for deduplication."""
    row_str = "|".join(str(v) for v in row.values)
    return hashlib.sha256(row_str.encode()).hexdigest()

def clean_dataframe(df):
    df = df.copy()
    
    # Map YOUR specific CSV headers to Database Columns
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
    
    # Ensure columns exist
    for col in required_cols:
        if col not in df.columns:
            df[col] = None

    # Parse Dates
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"]) 

    # Numeric Cleanup
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
    df = pd.read_sql(query, conn, params=(start_date, end_date))
    
    if not df.empty:
        df["dt"] = pd.to_datetime(df["dt"])
        df["is_refill"] = df["event_type"].str.lower().str.contains("refill|load", na=False)
        
        # --- CALCULATE GAP TIMES FOR MM:SS DISPLAY ---
        df = df.sort_values(['user_name', 'dt'])
        
        # Shift time by 1 to compare current row with previous row
        df['prev_time'] = df.groupby('user_name')['dt'].shift(1)
        
        # Calculate difference in seconds
        df['gap_seconds'] = (df['dt'] - df['prev_time']).dt.total_seconds()
        
        # Apply the MM:SS formatter helper function
        df['Time Since Last (MM:SS)'] = df['gap_seconds'].apply(seconds_to_mmss)
        
        # Create a pretty timestamp for display
        df['Timestamp'] = df['dt'].dt.strftime('%b %d, %I:%M %p')
    
    return df

###########################################################
#                 DASHBOARD UI
###########################################################

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=50)
    st.title("RxTrack Executive")
    
    # Date Filter
    end_val = datetime.today().date()
    start_val = end_val - timedelta(days=7)
    
    c1, c2 = st.columns(2)
    start_date = c1.date_input("Start", start_val)
    end_date = c2.date_input("End", end_val)
    
    st.divider()
    
    # --- SMART FILE LOADER ---
    uploaded = st.file_uploader("Upload Daily Report", type=["csv","xlsx"])
    if uploaded:
        try:
            # 1. Peek at the file to find headers
            if uploaded.name.endswith(".xlsx"):
                preview = pd.read_excel(uploaded, header=None, nrows=20)
            else:
                preview = pd.read_csv(uploaded, header=None, nrows=20)
            
            # 2. Find row with 'UserName' and 'Device'
            header_row_idx = None
            for idx, row in preview.iterrows():
                row_str = str(row.values).lower()
                if "username" in row_str and "device" in row_str:
                    header_row_idx = idx
                    break
            
            if header_row_idx is None:
                st.error("❌ Could not find headers (UserName/Device) in first 20 rows.")
            else:
                uploaded.seek(0)
                if uploaded.name.endswith(".xlsx"):
                    raw = pd.read_excel(uploaded, header=header_row_idx)
                else:
                    raw = pd.read_csv(uploaded, header=header_row_idx)
                
                clean = clean_dataframe(raw)
                st.success(f"✅ Headers found at row {header_row_idx+1}. {len(clean)} rows ready.")
                
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
tab_stockout, tab_effic, tab_drill = st.tabs([
    "🚨 Stockout Risk", 
    "⚡ Par Level Efficiency", 
    "🔍 Data Explorer (Drill Down)"
])

# --- TAB 1: STOCKOUTS ---
with tab_stockout:
    # Logic: Only care about refills where start count was 0
    refills = df[df['is_refill']].copy()
    stockouts = refills[refills['beginning_qty'] == 0]
    
    st.markdown(f"### ⚠️ {len(stockouts)} Stockout Events Detected")
    st.markdown("Beginning Count was 0. Potential patient care delay.")
    
    if not stockouts.empty:
        c_hot, c_list = st.columns([1, 2])
        with c_hot:
            hotspots = stockouts['device'].value_counts().reset_index()
            hotspots.columns = ['Device', 'Count']
            fig = px.bar(hotspots.head(10), x='Count', y='Device', orientation='h', color_discrete_sequence=['#FF4B4B'])
            fig.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig, use_container_width=True)
        with c_list:
            st.dataframe(stockouts[['Timestamp', 'device', 'med_desc', 'user_name']].sort_values('Timestamp', ascending=False), use_container_width=True)
    else:
        st.success("Zero stockouts found in this period.")

# --- TAB 2: EFFICIENCY ---
with tab_effic:
    st.markdown("### 📉 Low-Yield Refill Matrix")
    st.markdown("High effort (many trips) vs. Low yield (adding few meds).")
    
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

# --- TAB 3: DATA EXPLORER (DRILL DOWN) ---
with tab_drill:
    st.header("🔍 Interactive Line-by-Line Analysis")
    
    # 1. FILTERS
    c1, c2, c3 = st.columns(3)
    
    # Get unique sorted lists for dropdowns (handle None values safely)
    all_users = sorted([x for x in df['user_name'].unique() if x is not None])
    all_devices = sorted([x for x in df['device'].unique() if x is not None])
    all_meds = sorted([x for x in df['med_desc'].unique() if x is not None])
    
    sel_users = c1.multiselect("Filter Technician", all_users)
    sel_devices = c2.multiselect("Filter Device", all_devices)
    sel_meds = c3.multiselect("Filter Medication", all_meds)
    
    # 2. APPLY FILTERS
    filtered = df.copy()
    if sel_users:
        filtered = filtered[filtered['user_name'].isin(sel_users)]
    if sel_devices:
        filtered = filtered[filtered['device'].isin(sel_devices)]
    if sel_meds:
        filtered = filtered[filtered['med_desc'].isin(sel_meds)]
        
    st.markdown(f"**Showing {len(filtered):,} records**")
    
    # 3. DISPLAY TABLE
    # Select just the columns we want to show for clarity
    display_cols = [
        'Timestamp', 
        'Time Since Last (MM:SS)', 
        'user_name', 
        'device', 
        'event_type', 
        'med_desc', 
        'qty', 
        'beginning_qty', 
        'ending_qty'
    ]
    
    # Ensure display columns actually exist in dataframe before showing
    valid_cols = [c for c in display_cols if c in filtered.columns]
    
    # Sort so mostly recently activity is at top
    st.dataframe(
        filtered[valid_cols].sort_values('Timestamp', ascending=False),
        use_container_width=True,
        hide_index=True
    )