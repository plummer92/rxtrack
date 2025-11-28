###############################################
# RXTRACK: STEWARDSHIP EDITION
# Focus: Stockouts, Low-Efficiency Refills, and Inventory Health
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
    page_title="RxTrack: Stewardship & Efficiency", 
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for Executive Dashboard
st.markdown("""
    <style>
    .metric-card { background-color: #f9f9f9; padding: 15px; border-radius: 10px; border-left: 5px solid #4CAF50; }
    .stockout-card { border-left: 5px solid #FF4B4B; }
    </style>
    """, unsafe_allow_html=True)

###########################################################
#                 DATABASE CONNECTION
###########################################################
@st.cache_resource
def get_db_connection():
    try:
        # Uses st.secrets for safety
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
    # Based on the file you shared
    colmap = {
        "UserName": "user_name",
        "UserID": "user_id",
        "Device": "device",
        "MedID": "med_id",
        "MedDescription": "med_desc",
        "TransactionType": "event_type",
        "TransactionDateTime": "dt",
        "Quantity": "qty",
        "Beg": "beginning_qty",
        "End": "ending_qty"
    }
    
    df = df.rename(columns=colmap)
    
    # Keep only columns we need for the database
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
    df = df.dropna(subset=["dt"]) # Drop summary rows at bottom of CSV

    # Numeric Cleanup
    for c in ["qty", "beginning_qty", "ending_qty"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    df["dt"] = df["dt"].astype(str)
    df["pk"] = df.apply(lambda r: generate_pk(r), axis=1)
    
    return df[required_cols + ["pk"]]

###########################################################
#                 DB INSERT
###########################################################
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
#            ANALYTICS LOGIC (STEWARDSHIP)
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
        # Filter purely for Refill/Load events for the stewardship metrics
        df["is_refill"] = df["event_type"].str.lower().str.contains("refill|load", na=False)
    
    return df

###########################################################
#                 DASHBOARD UI
###########################################################

# Sidebar
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
    
    # File Upload
    uploaded = st.file_uploader("Upload Daily Report", type=["csv","xlsx"])
    if uploaded:
        if uploaded.name.endswith(".xlsx"):
            raw = pd.read_excel(uploaded)
        else:
            raw = pd.read_csv(uploaded)
        
        # Clean & Save
        clean = clean_dataframe(raw)
        if st.button("Process & Save to DB"):
            insert_batch(clean)
            st.cache_data.clear()

# Load Data
df = load_data(start_date, end_date)

if df.empty:
    st.info("👋 Ready for data. Upload a Pyxis report to begin.")
    st.stop()

# --- HEADER: The "Hard Data" Numbers ---
st.markdown("## 🏥 Stewardship & Operational Efficiency")

# Filter to just refills for these metrics
refills = df[df['is_refill']].copy()

# Metric 1: Stockouts (Beginning Qty was 0)
stockouts = refills[refills['beginning_qty'] == 0]
num_stockouts = len(stockouts)

# Metric 2: Micro-Refills (Adding only 1 item)
micro_refills = refills[refills['qty'] == 1]
num_micros = len(micro_refills)

# Metric 3: Total Volume
total_volume = refills['qty'].sum()

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.markdown(f"""
    <div class="metric-card stockout-card">
        <h3>🚨 Stockouts</h3>
        <h1>{num_stockouts:,}</h1>
        <p>Bin was empty upon arrival</p>
    </div>
    """, unsafe_allow_html=True)

with c2:
    st.markdown(f"""
    <div class="metric-card">
        <h3>📉 Micro-Refills</h3>
        <h1>{num_micros:,}</h1>
        <p>Trips to add just 1 unit</p>
    </div>
    """, unsafe_allow_html=True)

with c3:
    st.metric("Total Units Restocked", f"{total_volume:,.0f}")
    
with c4:
    st.metric("Unique Meds Refilled", f"{refills['med_desc'].nunique():,}")

st.markdown("---")

# --- TABS ---
tab_stockout, tab_effic, tab_staff, tab_raw = st.tabs([
    "🚨 Stockout Risk (Safety)", 
    "⚡ Par Level Efficiency (Labor)", 
    "🧑‍⚕️ Staff Workflow",
    "💾 Raw Data"
])

# --- TAB 1: STOCKOUTS (Safety Argument) ---
with tab_stockout:
    st.markdown("### ⚠️ Supply Chain Gaps (Stockouts)")
    st.markdown("These events occurred where the **Beginning Count was 0**. This indicates a potential delay in patient care.")
    
    if not stockouts.empty:
        # Group by Device to see hotspots
        hotspots = stockouts['device'].value_counts().reset_index()
        hotspots.columns = ['Device', 'Stockouts']
        
        col_a, col_b = st.columns([1, 2])
        
        with col_a:
            st.dataframe(hotspots, use_container_width=True, hide_index=True)
            
        with col_b:
            fig = px.bar(hotspots.head(10), x='Stockouts', y='Device', orientation='h', 
                         title="Top 10 Devices with Stockouts", color_discrete_sequence=['#FF4B4B'])
            fig.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig, use_container_width=True)
            
        st.markdown("#### Detailed Stockout Log")
        st.dataframe(stockouts[['dt', 'device', 'med_desc', 'user_name', 'qty']].sort_values('dt', ascending=False), use_container_width=True)
    else:
        st.success("✅ Zero stockouts detected in this period!")

# --- TAB 2: EFFICIENCY (Financial Argument) ---
with tab_effic:
    st.markdown("### 📉 Par Level Optimization Candidates")
    st.markdown("""
    **The Problem:** High frequency of refills with low quantity suggests Par Levels are too low.
    **The Fix:** Increase Max Par for these items to reduce refill trips.
    """)
    
    # Logic: Group by Med, Calculate Avg Qty Added and Frequency
    effic_stats = refills.groupby(['device', 'med_desc']).agg(
        refill_events=('pk', 'count'),
        avg_qty_added=('qty', 'mean'),
        total_qty=('qty', 'sum')
    ).reset_index()
    
    # Filter for high effort (lots of visits) but low yield (small adds)
    # Example: Visited > 3 times, but avg add is < 3
    inefficient = effic_stats[ (effic_stats['refill_events'] >= 3) & (effic_stats['avg_qty_added'] < 3) ]
    
    if not inefficient.empty:
        fig_bub = px.scatter(inefficient, x='refill_events', y='avg_qty_added', 
                             size='total_qty', hover_name='med_desc', color='device',
                             title="Inefficiency Matrix: High Trips vs. Low Quantity",
                             labels={'refill_events':'Number of Trips', 'avg_qty_added':'Avg Qty Added'})
        st.plotly_chart(fig_bub, use_container_width=True)
        
        st.dataframe(inefficient.sort_values('refill_events', ascending=False), use_container_width=True)
    else:
        st.success("Refill efficiency looks good.")

# --- TAB 3: STAFF WORKFLOW ---
with tab_staff:
    st.markdown("### 🧑‍⚕️ Technician Activity Volume")
    
    tech_vol = df.groupby('user_name')['pk'].count().reset_index(name='transactions')
    tech_refills = refills.groupby('user_name')['qty'].sum().reset_index(name='units_stocked')
    
    merged = pd.merge(tech_vol, tech_refills, on='user_name', how='left').fillna(0)
    
    fig_dual = px.bar(merged, x='user_name', y=['transactions', 'units_stocked'], 
                      barmode='group', title="Activity vs. Volume Moved")
    st.plotly_chart(fig_dual, use_container_width=True)

# --- TAB 4: RAW DATA ---
with tab_raw:
    st.dataframe(df.sort_values('dt', ascending=False), use_container_width=True)