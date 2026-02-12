import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from App import load_data, seconds_to_mmss

st.set_page_config(page_title="Pharmacy Workflow", page_icon="🏥", layout="wide")

# Title and Description
st.header("🏥 Central Pharmacy Workflow & Stockout Intelligence")
st.caption("Analyzing stockout frequency to suggest optimized Par Levels and reduce STAT deliveries.")

if 'start_date' not in st.session_state:
    st.info("👈 Please select a date range on the **Overview** page first.")
else:
    # 1. Load data
    df_events, _, df_pharm, _, _ = load_data(
        st.session_state.start_date, 
        st.session_state.end_date
    )

    if df_pharm.empty:
        st.warning("No Pharmacy Workflow data found for this period.")
    else:
        # --- FILTERS ---
        priorities = sorted(df_pharm['priority'].dropna().unique())
        sel_prio = st.multiselect("Filter Transaction Type (Priority)", priorities)
        
        view_pharm = df_pharm.copy()
        if sel_prio:
            view_pharm = view_pharm[view_pharm['priority'].isin(sel_prio)]

        # --- KEY METRICS ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Orders", len(view_pharm))
        
        # Calculate STAT/Critical counts
        stat_mask = view_pharm['priority'].str.contains('STAT|Critical', case=False, na=False)
        c2.metric("Critical/STAT", len(view_pharm[stat_mask]))
        
        # Calculate Stockout counts
        stockout_mask = view_pharm['priority'].str.contains(r'Stock\s*Out|Stockout', case=False, na=False)
        stockout_only = view_pharm[stockout_mask].copy()
        c3.metric("Stockout Events", len(stockout_only))
        
        top_dest = view_pharm['destination'].mode()[0] if not view_pharm['destination'].empty else "N/A"
        c4.metric("Top Destination", top_dest)

        # --- VISUAL ANALYSIS ---
        st.divider()
        col_chart1, col_chart2 = st.columns(2)
        
        with col_chart1:
            st.subheader("📍 Stockouts by Device")
            if not stockout_only.empty:
                fig_dev = px.bar(stockout_only['destination'].value_counts().reset_index().head(10), 
                                 x='count', y='destination', orientation='h', title="Top 10 Problem Units",
                                 labels={'count': 'Stockout Count', 'destination': 'Unit'}, color='count')
                st.plotly_chart(fig_dev, use_container_width=True)
            else:
                st.info("No stockout data for charts.")

        with col_chart2:
            st.subheader("💊 Top Stockout Medications")
            if not stockout_only.empty:
                fig_med = px.bar(stockout_only['med_desc'].value_counts().reset_index().head(10), 
                                 x='count', y='med_desc', orientation='h', title="Top 10 Problem Meds",
                                 labels={'count': 'Stockout Count', 'med_desc': 'Medication'}, color='count')
                st.plotly_chart(fig_med, use_container_width=True)

        # --- PAR LEVEL RECOMMENDATIONS ---
        st.divider()
        st.subheader("💡 AI Par Level Recommendations")
        st.caption("Calculated based on stockout frequency vs. average refill volume.")
        
        if not stockout_only.empty:
            # Aggregate stockout data
            stockout_agg = stockout_only.groupby(['destination', 'med_id']).agg(
                med_desc=('med_desc', 'first'),
                Stockout_Count=('pk', 'count'),
                Avg_Stockout_Req=('qty', 'mean')
            ).reset_index().rename(columns={'destination': 'device'})

            # Pull refill stats from Pyxis events if available
            if not df_events.empty:
                is_refill = df_events['event_type'].astype(str).str.contains(r'REFILL|LOAD|STOCK|ADD', case=False, na=False)
                refill_stats = df_events[is_refill].groupby(['device', 'med_id'])['qty'].mean().reset_index(name='Avg_Refill_Qty')
                recs = pd.merge(stockout_agg, refill_stats, on=['device', 'med_id'], how='left')
            else:
                recs = stockout_agg.copy()
                recs['Avg_Refill_Qty'] = 0

            recs['Avg_Refill_Qty'] = recs['Avg_Refill_Qty'].fillna(0)
            
            # Smart logic for suggestions
            base_capacity = np.where(recs['Avg_Refill_Qty'] > 0, recs['Avg_Refill_Qty'], recs['Avg_Stockout_Req'])
            recs['Suggested Min'] = np.clip(np.ceil(base_capacity * 1.5), 1, None).astype(int)
            recs['Suggested Max'] = np.ceil(recs['Suggested Min'] * 2.5).astype(int)

            st.dataframe(
                recs[['device', 'med_desc', 'Stockout_Count', 'Suggested Min', 'Suggested Max']].sort_values('Stockout_Count', ascending=False),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "device": "Pyxis Unit",
                    "med_desc": "Medication",
                    "Stockout_Count": "Frequency"
                }
            )
        else:
            st.success("✅ No Stockouts found! Par levels appear optimized for this period.")

        # --- RAW DATA VIEW ---
        with st.expander("📄 View Detailed Workflow Log"):
            st.dataframe(view_pharm, use_container_width=True)


        st.divider()
        st.subheader("🔄 Full Lifecycle: Stockout to Replenishment")
        st.caption("Tracking the delay between a 'Zero' event and the 'Refill' event.")
        
        if not df_events.empty and not df_pharm.empty:
            # 1. Identify "Zero Out" Events from Pyxis
            st.write("Columns found in df_events:", df_events.columns.tolist())
            zeros = df_events[df_events['ending_qty'] == 0].copy()
            zeros = zeros[['dt', 'device', 'med_id', 'med_desc']].rename(columns={'dt': 'Stockout_Time'})
        
            # 2. Identify "Refill" Events from Pyxis
            refills = df_events[df_events['event_type'].str.contains('REFILL|LOAD', case=False, na=False)].copy()
            refills = refills[['dt', 'device', 'med_id', 'qty']].rename(columns={'dt': 'Refill_Time', 'qty': 'Refill_Qty'})
        
            # 3. Join with Workflow (Stockouts)
            # We match by Unit (Device) and Medication (med_id)
            lifecycle = pd.merge(zeros, stockout_only, left_on=['device', 'med_id'], right_on=['destination', 'med_id'], how='inner')
            
            # 4. Find the first refill that happened AFTER the stockout
            # We merge and then filter for Refill_Time > Stockout_Time
            full_path = pd.merge(lifecycle, refills, on=['device', 'med_id'], how='left')
            full_path = full_path[full_path['Refill_Time'] > full_path['Stockout_Time']]
            
            # Get the earliest refill for each stockout
            full_path = full_path.sort_values('Refill_Time').groupby(['device', 'med_id', 'Stockout_Time']).first().reset_index()
        
            # 5. Calculate Delay
            full_path['Replenish_Time_Min'] = (full_path['Refill_Time'] - full_path['Stockout_Time']).dt.total_seconds() / 60
        
            # Display the Full Story
            st.dataframe(
                full_path[['device', 'med_desc_x', 'Stockout_Time', 'dt', 'Refill_Time', 'Replenish_Time_Min']],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "device": "Unit",
                    "med_desc_x": "Medication",
                    "Stockout_Time": "Time Hit Zero",
                    "dt": "Carousel Order Time",
                    "Refill_Time": "Actual Refill",
                    "Replenish_Time_Min": st.column_config.NumberColumn("Total Outage (Min)", format="%d")
                }
            )
            
            avg_outage = full_path['Replenish_Time_Min'].mean()
            st.metric("Average Time Unit Stays Empty", f"{int(avg_outage)} Minutes" if not full_path.empty else "N/A")
