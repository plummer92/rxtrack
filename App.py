###############################################
# RXTRACK: EXECUTIVE DASHBOARD (FINAL STABLE)
# Fixes: TypeError on Date Slider (String vs Date)
###############################################

import streamlit as st
import pandas as pd
import numpy as np
import hashlib
import psycopg2
from psycopg2.extras import execute_batch
import plotly.express as px
from datetime import datetime, timedelta, date

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
    .missing-card { background-color: #ffebee; padding: 10px; border-radius: 5px; border-left: 5px solid #FF4B4B; margin-bottom: 10px;}
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
    """Bulletproof converter to ensure we always get a python date object"""
    if val is None: 
        return datetime.today().date()
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime): 
        return val.date()
    if isinstance(val, pd.Timestamp):
        return val.date()
    # Fallback for strings
    try:
        return pd.to_datetime(val).date()
    except:
        return datetime.today().date()

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
    """Gets date range and detects MISSING dates for the sidebar"""
    conn = get_db_connection()
    if not conn: return 0, datetime.today().date(), datetime.today().date(), []
    
    try:
        cur = conn.cursor()
        
        # 1. Get Range & Count
        cur.execute("SELECT MIN(dt), MAX(dt), COUNT(*) FROM events")
        result = cur.fetchone()
        
        # Use safe converter on results to prevent TypeErrors
        min_dt = safe_to_date(result[0]) if result else datetime.today().date()
        max_dt = safe_to_date(result[1]) if result else datetime.today().date()
        total_rows = result[2] if result else 0
        
        # 2. Find Gaps (Dates with 0 records)
        missing_dates = []
        if total_rows > 0:
            # Generate all dates in range
            delta = (max_dt - min_dt).days
            if delta < 0: delta = 0
            
            all_dates = {min_dt + timedelta(days=x) for x in range(delta + 1)}
            
            # Get actual dates present
            cur.execute("SELECT DISTINCT DATE(dt) FROM events")
            present_dates = {safe_to_date(row[0]) for row in cur.fetchall()}
            
            # Compare sets
            missing_dates = sorted(list(all_dates - present_dates))
            
        cur.close()
        conn.close()
        
        return total_rows, min_dt, max_dt, missing_dates
        
    except Exception as e:
        if conn: conn.close()
        # Fallback to safe defaults on error
        return 0, datetime.today().date(), datetime.today().date(), []

###########################################################
#                 DATA CLEANING
###########################################################
def generate_pk(row):
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

def clean_cost_dataframe(df):
    df = df.copy()
    df.columns = df.columns.str.strip().str.lower()
    
    id_col = next((c for c in df.columns if "id" in c or "med" in c), None)
    cost_col = next((c for c in df.columns if "cost" in c or "price" in c or "avg" in c), None)
    
    if not id_col or not cost_col:
        return None
    
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
    
    # --- 1. DATA GAP DETECTOR ---
    # We use safe_to_date inside get_db_stats now
    total_rows, min_db, max_db, missing_dates = get_db_stats()
    
    with st.expander("💾 Database Coverage", expanded=True):
        st.write(f"**Records:** {total_rows:,}")
        st.write(f"**Range:** {min_db} to {max_db}")
        
        if missing_dates:
            st.markdown(f"""
            <div class="missing-card">
            ⚠️ <b>Missing Data ({len(missing_dates)} Days)</b><br>
            <i>No records found for:</i><br>
            {", ".join([d.strftime('%b %d') for d in missing_dates[:5]])}
            {f"...and {len(missing_dates)-5} more" if len(missing_dates) > 5 else ""}
            </div>
            """, unsafe_allow_html=True)
        else:
            st.success("✅ Complete Daily Coverage")
    
    st.divider()
    
    # --- 2. DATE SLIDER ---
    # Now min_db and max_db are guaranteed to be Date objects, not strings
    default_start = max(min_db, max_db - timedelta(days=7))
    
    date_range = st.slider(
        "Select Analysis Range", 
        min_value=min_db, 
        max_value=max_db,
        value=(min_db, max_db),
        format="MM/DD/YY",
        key=f"slider_{min_db}_{max_db}_{total_rows}"
    )
    start_date, end_date = date_range
    
    st.divider()
    
    # --- 3. SMART FILE LOADER (DUAL MODE) ---
    uploaded = st.file_uploader("Upload File (Daily Report OR Price List)", type=["csv","xlsx"])
    if uploaded:
        try:
            # 1. READ RAW HEADER (First 50 rows)
            if uploaded.name.endswith(".xlsx"):
                preview = pd.read_excel(uploaded, header=None, nrows=50)
            else:
                preview = pd.read_csv(uploaded, header=None, nrows=50)
            
            # 2. IDENTIFY FILE TYPE
            file_type = "unknown"
            header_row_idx = 0
            
            for idx, row in preview.iterrows():
                row_str = str(row.values).lower()
                if "username" in row_str and "device" in row_str:
                    file_type = "daily_report"
                    header_row_idx = idx
                    break
                if ("med" in row_str or "id" in row_str) and ("cost" in row_str or "price" in row_str) and "username" not in row_str:
                    file_type = "cost_list"
                    header_row_idx = idx
                    break
            
            if file_type == "unknown":
                st.error("❌ Unknown file type. Could not find 'UserName' (Report) or 'Cost' (Financials).")
            else:
                uploaded.seek(0)
                if uploaded.name.endswith(".xlsx"):
                    raw = pd.read_excel(uploaded, header=header_row_idx)
                else:
                    raw = pd.read_csv(uploaded, header=header_row_idx)
                
                if file_type == "daily_report":
                    clean = clean_dataframe(raw)
                    st.info(f"📄 Detected Daily Report ({len(clean)} rows)")
                    if st.button("Save Report to DB"):
                        insert_batch(clean, "events")
                        st.cache_data.clear()
                        st.rerun()
                        
                elif file_type == "cost_list":
                    clean = clean_cost_dataframe(raw)
                    if clean is not None:
                        st.info(f"💰 Detected Price List ({len(clean)} meds)")
                        if st.button("Update Financials"):
                            insert_batch(clean, "med_costs")
                            st.cache_data.clear()
                            st.success("✅ Prices updated!")
                    else:
                        st.error("Could not map 'MedID' and 'Cost' columns.")

        except Exception as e:
            st.error(f"Error reading file: {e}")

# Load Data
df = load_data(start_date, end_date)

if df.empty:
    st.info("👋 Ready for data. Upload a Pyxis report to begin.")
    st.stop()

# --- TABS ---
tab_over, tab_compliance, tab_stock, tab_effic, tab_drill = st.tabs([
    "📊 Overview",
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
    else: disc_df = pd.DataFrame()
    
    if not disc_df.empty:
        disc_df['variance_cost'] = disc_df['discrepancy_qty'] * disc_df['cost_per_unit']
        total_loss = disc_df['variance_cost'].sum()
    else: total_loss = 0
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Count Errors", len(disc_df))
    if not disc_df.empty:
        c2.metric("Net Variance Qty", int(disc_df['discrepancy_qty'].sum()))
        c3.metric("Net Financial Variance", f"${total_loss:,.2f}", delta_color="inverse")
    else:
        c2.metric("Net Variance", 0)
        c3.metric("Financial Impact", "$0.00")
    
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
        st.dataframe(disc_df[['Timestamp', 'user_name', 'device', 'med_desc', 'discrepancy_qty', 'cost_per_unit', 'variance_cost']], use_container_width=True, hide_index=True)
    elif 'discrepancy_qty' in df.columns:
        st.success("✅ No discrepancies found!")

# --- TAB 3: STOCKOUTS ---
with tab_stock:
    all_refills = df[df['is_refill']].copy()
    all_refills = all_refills.sort_values(['device', 'med_desc', 'dt'])
    all_refills['prev_refill_dt'] = all_refills.groupby(['device', 'med_desc'])['dt'].shift(1)
    all_refills['burn_duration'] = all_refills['dt'] - all_refills['prev_refill_dt']
    stockouts = all_refills[all_refills['beginning_qty'] == 0].copy()
    stockouts['Time Since Last Refill'] = stockouts['burn_duration'].apply(lambda x: f"{int(x.total_seconds()//3600)}h {int((x.total_seconds()%3600)//60)}m" if pd.notnull(x) else "N/A")
    
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
    cols = ['Timestamp', 'Walk Time', 'Machine Time', 'user_name', 'device', 'event_type', 'med_desc', 'qty', 'discrepancy_qty', 'cost_per_unit']
    valid_cols = [c for c in cols if c in filtered.columns]
    st.dataframe(filtered[valid_cols].sort_values('Timestamp', ascending=False), use_container_width=True, hide_index=True)