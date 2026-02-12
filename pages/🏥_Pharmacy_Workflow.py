import streamlit as st
import pandas as pd
import plotly.express as px
from App import load_data, seconds_to_mmss # Shared logic

st.set_page_config(page_title="Pharmacy Workflow", page_icon="🏥", layout="wide")
st.header("🏥 Pharmacy Workflow & Stockout Intelligence")

if 'start_date' not in st.session_state:
    st.info("👈 Please select a date range on the **Overview** page first.")
else:
    # 1. Load data
    # We focus on df_pharm (Workflow) and df_events (to see the impact on Pyxis)
    df_events, _, df_pharm, _, _ = load_data(
        st.session_state.start_date, 
        st.session_state.end_date
    )

    if df_pharm.empty:
        st.warning("No Pharmacy Workflow data found for this period.")
    else:
        # 2. Identify Stockouts/Critical Needs
        # Filtering for 'Stockout' or 'Critical' priorities in your report
        stockouts = df_pharm[df_pharm['priority'].str.contains('Stockout|Critical|Stat', case=False, na=False)].copy()
        
        # --- EXECUTIVE SUMMARY ---
        t1, t2, t3 = st.columns(3)
        t1.metric("Total Workflow Orders", len(df_pharm))
        t2.metric("Stockout Events", len(stockouts), delta=f"{len(stockouts)/len(df_pharm)*100:.1f}% of total", delta_color="inverse")
        
        # Calculate Avg Turnaround (if completion data exists)
        t3.metric("Stockout Frequency", f"{len(stockouts) / max(1, (st.session_state.end_date - st.session_state.start_date).days):.1f} / day")

        # --- STOCKOUT ANALYSIS ---
        st.divider()
        col_left, col_right = st.columns(2)

        with col_left:
            st.subheader("💊 Most Frequent Stockout Meds")
            # Identifies which drugs are consistently hitting zero in the Pyxis
            med_counts = stockouts['med_desc'].value_counts().reset_index().head(10)
            med_counts.columns = ['Medication', 'Stockout Count']
            fig_med = px.bar(med_counts, x='Stockout Count', y='Medication', orientation='h', 
                             color='Stockout Count', color_continuous_scale='OrRd')
            st.plotly_chart(fig_med, use_container_width=True)

        with col_right:
            st.subheader("📍 Problematic Devices")
            # Identifies which Pyxis machines are failing par levels most often
            device_counts = stockouts['destination'].value_counts().reset_index().head(10)
            device_counts.columns = ['Device', 'Stockout Count']
            fig_dev = px.bar(device_counts, x='Stockout Count', y='Device', orientation='h', 
                             color='Stockout Count', color_continuous_scale='Blues')
            st.plotly_chart(fig_dev, use_container_width=True)

        # --- DATA DRILL-DOWN ---
        st.divider()
        st.subheader("📋 Stockout Detail Log")
        st.dataframe(
            stockouts[['dt', 'user_name', 'destination', 'med_desc', 'priority', 'qty']], 
            use_container_width=True,
            hide_index=True,
            column_config={
                "dt": st.column_config.DatetimeColumn("Requested At", format="MM/DD HH:mm"),
                "user_name": "Technician",
                "destination": "Pyxis Unit",
                "med_desc": "Medication"
            }
        )

        # 3. Pattern Recognition Logic
        st.info("💡 **Leadership Insight:** If a medication appears on the Stockout chart frequently, consider increasing its 'Par Level' in the Pyxis to reduce 'Stat' delivery tasks.")
