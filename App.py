###############################################
# RXTRACK: EXECUTIVE DASHBOARD (SORTING FIX)
# Architecture: Dual-Table Strategy (Events vs Config)
# Fixes: Timestamps now sort chronologically (using datetime objects) 
#        while displaying in formatted military time.
###############################################

import streamlit as st
import pandas as pd
import numpy as np
import hashlib
import psycopg2
from psycopg2.extras import execute_batch
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, date
import gc  # Added for memory management

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
    return f"{m:02d}:{s:02d}"

def safe_to_date(val):
    if val is None: return datetime.today().date()
    try: return pd.to_datetime(val).date()
    except: return datetime.today().date()

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
        # Only fetch dates if row count is manageable to save memory
        if total_rows > 0:
            cur.execute("SELECT DISTINCT DATE(dt) FROM events")
            present_dates = {safe_to_date(row[0]) for row in cur.fetchall()}
            
        cur.close()
        return total_rows, min_dt, max_dt, present_dates
        
    except Exception as e:
        return 0, datetime.today().date(), datetime.today().date(), set()

###########################################################
#                 DATA CLEANING & PROCESSING
###########################################################
def generate_pk(row):
    subset = [str(x) for x in row.values if pd.notnull(x)]
    row_str = "|".join(subset)
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
    
    required = ["user_name", "device", "med_id", "med_desc", "event_type", "dt", "qty", "beginning_qty", "ending_qty", "discrepancy_qty", "discrepancy_reason", "resolution_dt"]
    for col in required:
        if col not in df.columns: df[col] = None

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"]) 

    # OPTIMIZATION: Downcast numeric types to save RAM
    for c in ["qty", "discrepancy_qty"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype('float32')

    df["dt"] = df["dt"].astype(str)
    df["resolution_dt"] = df["resolution_dt"].astype(str).replace('NaT', None)
    df["pk"] = df.apply(lambda r: generate_pk(r), axis=1)
    
    return df[required + ["pk"]]

def clean_activity_log(df):
    df = df.copy()
    df.columns = df.columns.str.strip().str.replace(' ', '')
    df = df.rename(columns={"UserName": "user_name", "Device": "device", "TransactionDateTime": "dt", "Action": "action_type", "ActivityType": "activity_category", "AffectedElement": "raw_element"})
    
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt"])
    
    pattern = r'^(.*?) \((.*?)\):\s*(\d+)$'
    extracted = df['raw_element'].astype(str).str.extract(pattern)
    df['location'] = extracted[0].str.strip()
    df['med_id'] = extracted[1].str.strip()
    df['qty_extracted'] = pd.to_numeric(extracted[2], errors='coerce').fillna(0)
    df = df.dropna(subset=['med_id'])

    # Deduplication
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
    
    grouped['min_qty'] = grouped['temp_min'].fillna(0)
    grouped['max_qty'] = grouped['temp_max'].fillna(0)
    grouped['is_standard'] = grouped['is_standard'].fillna(False)
    
    grouped["dt"] = grouped["dt"].astype(str)
    grouped["pk"] = grouped.apply(lambda r: generate_pk(r), axis=1)
    
    return grouped[['pk', 'dt', 'user_name', 'device', 'med_id', 'location', 'action_type', 'activity_category', 'min_qty', 'max_qty', 'is_standard']]

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

def insert_batch(df, table_name):
    conn = get_db_connection()
    if not conn: return
    cur = conn.cursor()
    
    if table_name == "events":
        sql = """INSERT INTO events (pk, user_name, device, med_id, med_desc, event_type, dt, qty, beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason, resolution_dt) VALUES (%(pk)s, %(user_name)s, %(device)s, %(med_id)s, %(med_desc)s, %(event_type)s, %(dt)s, %(qty)s, %(beginning_qty)s, %(ending_qty)s, %(discrepancy_qty)s, %(discrepancy_reason)s, %(resolution_dt)s) ON CONFLICT (pk) DO NOTHING;"""
    elif table_name == "config_events":
        sql = """INSERT INTO config_events (pk, dt, user_name, device, med_id, location, action_type, activity_category, min_qty, max_qty, is_standard) VALUES (%(pk)s, %(dt)s, %(user_name)s, %(device)s, %(med_id)s, %(location)s, %(action_type)s, %(activity_category)s, %(min_qty)s, %(max_qty)s, %(is_standard)s) ON CONFLICT (pk) DO NOTHING;"""
    elif table_name == "med_costs":
        sql = """INSERT INTO med_costs (med_id, cost_per_unit) VALUES (%(med_id)s, %(cost_per_unit)s) ON CONFLICT (med_id) DO UPDATE SET cost_per_unit = EXCLUDED.cost_per_unit;"""
        
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
#             ANALYTICS LOGIC (OPTIMIZED)
###########################################################
@st.cache_data(ttl=300)
def load_events_data(start_date, end_date):
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    
    # OPTIMIZATION: Select ONLY necessary columns
    query = """
        SELECT e.user_name, e.device, e.med_id, e.med_desc, e.event_type, e.dt, e.qty, e.discrepancy_qty, c.cost_per_unit, e.pk 
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
        
        # Time Logic
        df = df.sort_values(['user_name', 'dt'])
        df['next_dt'] = df.groupby('user_name')['dt'].shift(-1)
        df['duration_seconds'] = (df['next_dt'] - df['dt']).dt.total_seconds()
        df['prev_device'] = df.groupby('user_name')['device'].shift(1)
        
        # Vectorized calculations (faster than apply)
        df['machine_time_sec'] = np.where((df['device'] == df.groupby('user_name')['device'].shift(-1)) & (df['duration_seconds'] < 600), df['duration_seconds'], 0)
        df['is_new_session'] = np.where((df['user_name'] != df['user_name'].shift(1)) | (df['device'] != df['prev_device']), 1, 0)
        df['session_id'] = df['is_new_session'].cumsum()

        # Military Time Format preserved
        df['Timestamp'] = df['dt'].dt.strftime('%b %d, %H:%M:%S')
        df['Date'] = df['dt'].dt.date
        df['Hour'] = df['dt'].dt.hour
        
        # Calculate Process Mining Paths
        df['path_taken'] = df['prev_device'].fillna('Start') + " ➡️ " + df['device']
        # Calculate gap minutes
        df['gap_minutes'] = (df['dt'] - df.groupby('user_name')['dt'].shift(1)).dt.total_seconds() / 60
        
        # Clean up temp columns to save RAM
        df.drop(columns=['next_dt', 'is_new_session'], inplace=True, errors='ignore')
        gc.collect() # Force garbage collection
        
    return df

@st.cache_data(ttl=300)
def load_config_data(start_date, end_date):
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    
    # OPTIMIZATION: Select specific columns
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

###########################################################
#                 DASHBOARD UI
###########################################################

with st.sidebar:
    st.image("https://img.icons8.com/color/96/caduceus.png", width=50)
    st.title("RxTrack Executive")
    total_rows, min_db, max_db, present_dates = get_db_stats()
    with st.expander("💾 Coverage Calendar", expanded=True):
        st.write(f"**Records:** {total_rows:,}")
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
    default_start = max(min_db, max_db - timedelta(days=7))
    date_range = st.slider("Select Range", min_value=min_db, max_value=max_db, value=(min_db, max_db), format="MM/DD/YY", key=f"slider_{min_db}_{max_db}_{total_rows}")
    start_date, end_date = date_range
    st.divider()
    
    st.subheader("📤 Data Upload")
    upload_type = st.selectbox("Select File Type:", ["Daily Transaction Report", "Device Activity Log (Pends)", "Financial Price List"])
    uploaded = st.file_uploader(f"Upload {upload_type}", type=["csv","xlsx"])
    
    if uploaded:
        if st.button(f"Process {upload_type}"):
            try:
                if uploaded.name.endswith(".xlsx"): preview = pd.read_excel(uploaded, header=None, nrows=50)
                else: preview = pd.read_csv(uploaded, header=None, nrows=50)
                header_row_idx = None
                
                # Heuristic Header Finder
                for idx, row in preview.iterrows():
                    row_str = str(row.values).lower()
                    if upload_type == "Daily Transaction Report" and "username" in row_str and "device" in row_str and "affectedelement" not in row_str: header_row_idx = idx; break
                    if upload_type == "Device Activity Log (Pends)" and "affectedelement" in row_str: header_row_idx = idx; break
                    if upload_type == "Financial Price List" and ("med" in row_str or "id" in row_str) and "cost" in row_str: header_row_idx = idx; break
                
                if header_row_idx is None: st.error("❌ Header not found.")
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
                    st.cache_data.clear()
                    st.rerun()
            except Exception as e: st.error(f"Error: {e}")

# Load BOTH datasets
df = load_events_data(start_date, end_date)
df_config = load_config_data(start_date, end_date)

if df.empty and df_config.empty:
    st.info("👋 Ready for data.")
    st.stop()

# --- TABS ---
tab_over, tab_mine, tab_comp, tab_pends, tab_loads, tab_effic, tab_drill = st.tabs([
    "📊 Overview", "🚀 Process Mining", "🛡️ Compliance", "📥 Pends Analyzer", "🚚 Loads", "⚡ Efficiency", "🔍 Drill Down"
])

# --- TAB 1: OVERVIEW ---
with tab_over:
    if not df.empty:
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
        top_slow = med_speed.sort_values('machine_time_sec', ascending=False).head(10)
        st.plotly_chart(px.bar(top_slow, x='machine_time_sec', y='med_desc', orientation='h', title="Slowest Meds (Avg Sec)"))

# --- TAB 2: PROCESS MINING (SANKEY RESTORED & OPTIMIZED) ---
with tab_mine:
    if not df.empty:
        st.markdown("### 🔄 Workflow Visualization")
        
        # Filter for movements between DIFFERENT devices only
        moves = df[df['device'] != df['prev_device']].dropna(subset=['prev_device', 'device']).copy()
        
        if not moves.empty:
            # Count the frequency of each path
            path_counts = moves.groupby(['prev_device', 'device']).size().reset_index(name='count')
            
            # CRITICAL OPTIMIZATION: Limit to Top 50 Paths to prevent crash
            path_counts = path_counts.sort_values('count', ascending=False).head(50)
            
            # Create a list of all unique nodes (devices/locations) FROM THE SUBSET
            all_nodes = list(pd.concat([path_counts['prev_device'], path_counts['device']]).unique())
            node_map = {node: i for i, node in enumerate(all_nodes)}
            
            # Map source/target to indices
            path_counts['source_idx'] = path_counts['prev_device'].map(node_map)
            path_counts['target_idx'] = path_counts['device'].map(node_map)
            
            # Build the Sankey Figure
            fig_sankey = go.Figure(data=[go.Sankey(
                node=dict(
                    pad=15,
                    thickness=20,
                    line=dict(color="black", width=0.5),
                    label=all_nodes,
                    color="#1E90FF"  # Dodger Blue for better node contrast
                ),
                link=dict(
                    source=path_counts['source_idx'],
                    target=path_counts['target_idx'],
                    value=path_counts['count'],
                    # VISIBILITY FIX: Changed from black (0,0,0) to light gray (200,200,200)
                    # This ensures links are visible in Dark Mode
                    color='rgba(200, 200, 200, 0.3)' 
                )
            )])
            
            fig_sankey.update_layout(title_text=f"Technician Movement Flow (Top {len(path_counts)} Paths)", font_size=10, height=600)
            st.plotly_chart(fig_sankey, use_container_width=True)
            
            with st.expander("View Raw Path Data"):
                 st.dataframe(path_counts.sort_values('count', ascending=False), use_container_width=True)
        else:
            st.info("Not enough movement data to generate a flow chart.")

# --- TAB 3: COMPLIANCE ---
with tab_comp:
    if not df.empty:
        disc_df = df[df['discrepancy_qty'] != 0].copy() if 'discrepancy_qty' in df.columns else pd.DataFrame()
        
        c1, c2 = st.columns(2)
        c1.metric("Count Errors", len(disc_df))
        
        # Financial Logic
        if not disc_df.empty:
            disc_df['abs_variance'] = disc_df['discrepancy_qty'].abs() * disc_df['cost_per_unit']
            total_loss = disc_df['abs_variance'].sum()
            c2.metric("Variance Value (Risk)", f"${total_loss:,.2f}")
            
            st.divider()
            
            # Financial Trend Chart
            st.markdown("#### 💸 Cost of Variance Over Time")
            daily_loss = disc_df.groupby('Date')['abs_variance'].sum().reset_index()
            fig_fin = px.bar(daily_loss, x='Date', y='abs_variance', title="Daily Financial Risk (Absolute Variance)")
            st.plotly_chart(fig_fin, use_container_width=True)
            
            st.markdown("#### 📝 Discrepancy Details")
            st.dataframe(
                disc_df[['dt', 'user_name', 'device', 'med_desc', 'discrepancy_qty', 'cost_per_unit', 'abs_variance']], 
                column_config={
                    "dt": st.column_config.DatetimeColumn(
                        "Timestamp",
                        format="MMM DD, HH:mm:ss"
                    ),
                    "cost_per_unit": st.column_config.NumberColumn("Cost/Unit", format="$%.2f"),
                    "abs_variance": st.column_config.NumberColumn("Variance Value", format="$%.2f")
                },
                use_container_width=True
            )

# --- TAB 4: PENDS ANALYZER ---
with tab_pends:
    st.markdown("### 📥 Inventory Configuration (Analyzer)")
    if not df_config.empty:
        c_f1, c_f2 = st.columns(2)
        filter_user = c_f1.multiselect("Filter Tech", sorted([x for x in df_config['user_name'].unique() if x is not None]))
        filter_device = c_f2.multiselect("Filter Device", sorted([x for x in df_config['device'].unique() if x is not None]))
        
        pends_view = df_config.copy()
        if filter_user: pends_view = pends_view[pends_view['user_name'].isin(filter_user)]
        if filter_device: pends_view = pends_view[pends_view['device'].isin(filter_device)]
        
        # KPIs
        c1, c2, c3 = st.columns(3)
        c1.metric("Config Changes", f"{len(pends_view):,}")
        c2.metric("Capacity Added", f"{int(pends_view['max_qty'].sum()):,}")
        c3.metric("Unique Meds Touched", pends_view['med_id'].nunique())
        
        st.divider()
        
        # 1. Top Meds Chart
        c_chart1, c_chart2 = st.columns(2)
        with c_chart1:
            st.markdown("#### 💊 Top Configured Meds")
            top_meds = pends_view['med_id'].value_counts().head(10).reset_index()
            top_meds.columns = ['Med ID', 'Count']
            st.plotly_chart(px.bar(top_meds, x='Count', y='Med ID', orientation='h'), use_container_width=True)
            
        # 2. Top Devices Chart
        with c_chart2:
            st.markdown("#### 📟 High Traffic Devices (Pends)")
            top_dev = pends_view['device'].value_counts().head(10).reset_index()
            top_dev.columns = ['Device', 'Count']
            st.plotly_chart(px.bar(top_dev, x='Count', y='Device', orientation='h'), use_container_width=True)
            
        # 3. Time of Day Heatmap/Bar
        st.markdown("#### 🕒 Configuration Activity by Hour")
        hourly_counts = pends_view.groupby('Hour').size().reset_index(name='Events')
        fig_time = px.bar(hourly_counts, x='Hour', y='Events', title="When are pends happening?", 
                          labels={'Hour': 'Hour of Day (24h)'})
        fig_time.update_layout(xaxis=dict(tickmode='linear', dtick=1))
        st.plotly_chart(fig_time, use_container_width=True)

        with st.expander("See Raw Config Data"):
            st.dataframe(
                pends_view[['dt', 'user_name', 'device', 'location', 'med_id', 'min_qty', 'max_qty', 'Standard Stock', 'Activity']], 
                column_config={
                    "dt": st.column_config.DatetimeColumn(
                        "Timestamp",
                        format="MMM DD, HH:mm:ss"
                    )
                },
                use_container_width=True, hide_index=True
            )
    else:
        st.info("No Config Data found in this date range. Upload 'Device Activity Log'.")

# --- TAB 5: LOADS (RESTOCK) ---
with tab_loads:
    loads_df = df[df['event_type'].astype(str).str.lower().str.contains('load|unload|refill')].copy() if not df.empty else pd.DataFrame()
    if not loads_df.empty:
        st.markdown("### 🚚 Stock Movement")
        st.dataframe(
            loads_df[['dt', 'user_name', 'device', 'event_type', 'med_desc', 'qty']], 
            column_config={
                "dt": st.column_config.DatetimeColumn(
                    "Timestamp",
                    format="MMM DD, HH:mm:ss"
                )
            },
            use_container_width=True
        )

# --- TAB 6: EFFICIENCY ---
with tab_effic:
    if not df.empty:
        st.markdown("### 📉 Inefficient Refills")
        effic_stats = df.groupby(['device', 'med_desc']).agg(Trips=('pk', 'count'), Avg_Added=('qty', 'mean')).reset_index()
        inefficient = effic_stats[(effic_stats['Trips'] >= 3) & (effic_stats['Avg_Added'] < 3)].sort_values('Trips', ascending=False).head(15)
        st.plotly_chart(px.bar(inefficient, x='Trips', y='med_desc', orientation='h', title="High Effort, Low Yield"))

# --- TAB 7: DRILL DOWN ---
with tab_drill:
    if not df.empty:
        st.header("🔍 Transaction Explorer")
        st.dataframe(
            df[['dt', 'user_name', 'device', 'event_type', 'med_desc', 'qty']].sort_values('dt', ascending=False), 
            column_config={
                "dt": st.column_config.DatetimeColumn(
                    "Timestamp",
                    format="MMM DD, HH:mm:ss"
                )
            },
            use_container_width=True, hide_index=True
        )
