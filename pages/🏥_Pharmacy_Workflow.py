import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(page_title="Pharmacy Workflow", page_icon="🏥", layout="wide")
App.apply_global_styles()
load_data = App.load_data
seconds_to_mmss = App.seconds_to_mmss
render_sidebar = App.render_sidebar

render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Central Pharmacy Workflow & Stockout Intelligence",
        "Analyze stockout frequency, destination pressure, and carousel behavior in the same modern shell as the overview hub.",
        kicker="Operations",
    )
    _debug_event("Pharmacy Workflow", "shared_intro_loaded")
    _debug_panel("Pharmacy Workflow", intro_mode="shared")
else:
    st.header("🏥 Central Pharmacy Workflow & Stockout Intelligence")
    st.caption("Analyze stockout frequency, destination pressure, and carousel behavior.")
    _debug_event("Pharmacy Workflow", "fallback_header_used")
    _debug_panel("Pharmacy Workflow", intro_mode="fallback")

if 'start_date' not in st.session_state:
    st.info("👈 Please select a date range on the **Overview** page first.")
else:
    # 1. Load data
    with st.spinner("Loading pharmacy data..."):
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

        # --- CAROUSEL TRANSACTION ANALYZER ---
        st.divider()
        st.subheader("🎡 Carousel Transaction Analyzer")
        st.caption("Deep-dive into central pharmacy workflow codes (e.g., STAT, Stockout, Routine) to find order trends.")

        if not df_pharm.empty:
            # 1. Selection Filter (Using priority/transaction codes from Pharmacy Workflow)
            all_carousel_codes = sorted(df_pharm['priority'].dropna().unique())
            selected_carousel_code = st.selectbox("Select Carousel Transaction Type:", all_carousel_codes)
            
            # 2. Filter the Pharmacy Data
            car_df = df_pharm[df_pharm['priority'] == selected_carousel_code].copy()
            
            if not car_df.empty:
                # 3. Summary Metrics for Carousel Workflow
                m1, m2, m3 = st.columns(3)
                m1.metric(f"Total '{selected_carousel_code}' Orders", len(car_df))
                m2.metric("Primary Destination", car_df['destination'].mode()[0])
                m3.metric("Highest Volume Med", car_df['med_desc'].mode()[0])

                # 4. Destination and Med Trends
                col_tr1, col_tr2 = st.columns(2)
                
                with col_tr1:
                    st.markdown("##### Top Destination Units")
                    dest_counts = car_df['destination'].value_counts().reset_index().head(10)
                    dest_counts.columns = ['Unit', 'Order Count']
                    fig_dest = px.bar(dest_counts, x='Order Count', y='Unit', orientation='h', color='Order Count')
                    st.plotly_chart(fig_dest, width='stretch')

                with col_tr2:
                    st.markdown("##### Heaviest Medications (Carousel Pulls)")
                    car_med_counts = car_df['med_desc'].value_counts().reset_index().head(10)
                    car_med_counts.columns = ['Medication', 'Pull Count']
                    fig_med_car = px.bar(car_med_counts, x='Pull Count', y='Medication', orientation='h', 
                                         color='Pull Count', color_continuous_scale='Bluered')
                    st.plotly_chart(fig_med_car, width='stretch')

                # 5. Hourly Trend: When are these orders hitting the Carousel?
                st.markdown(f"##### Peak Order Times: {selected_carousel_code}")
                car_df['hour'] = car_df['dt'].dt.hour
                hourly_trend = car_df.groupby('hour').size().reset_index(name='Order Volume')
                fig_hour = px.line(hourly_trend, x='hour', y='Order Volume', markers=True, 
                                   labels={'hour': 'Hour of Day (24h)', 'Order Volume': 'Number of Pulls'})
                st.plotly_chart(fig_hour, width='stretch')

                # 6. Carousel Raw Log
                with st.expander(f"📋 View Raw '{selected_carousel_code}' Carousel Log"):
                    st.dataframe(
                        car_df[['dt', 'user_name', 'destination', 'med_desc', 'qty', 'priority']],
                        width='stretch',
                        hide_index=True
                    )
            else:
                st.info(f"No carousel records found for code: {selected_carousel_code}")

        # --- PYXIS TRANSACTION DEEP-DIVE ---
        st.divider()
        st.subheader("🔍 Pyxis Transaction Drill-Down")
        st.caption("Filter and analyze specific action types within the Pyxis events.")

        if not df_events.empty:
            # 1. Setup Filters for Transaction Types
            all_event_types = sorted(df_events['event_type'].dropna().unique())
            
            c_dr1, c_dr2 = st.columns([1, 2])
            with c_dr1:
                selected_types = st.multiselect(
                    "Select Transaction Types:", 
                    all_event_types, 
                    # Default to common replenishment/pull actions for pharmacy focus
                    default=[t for t in all_event_types if any(word in t.upper() for word in ['REFILL', 'LOAD', 'DISPENSE'])]
                )
            
            # 2. Filter the Data
            drill_df = df_events.copy()
            if selected_types:
                drill_df = drill_df[drill_df['event_type'].isin(selected_types)]

            # 3. Visual Breakdown
            with c_dr2:
                if not drill_df.empty:
                    fig_type = px.pie(
                        drill_df, 
                        names='event_type', 
                        title="Distribution of Selected Transactions",
                        hole=0.4
                    )
                    st.plotly_chart(fig_type, width='stretch')

            # 4. Detailed Transaction Table
            st.dataframe(
                drill_df[['dt', 'user_name', 'device', 'med_desc', 'event_type', 'qty']],
                width='stretch',
                hide_index=True,
                column_config={
                    "dt": st.column_config.DatetimeColumn("Timestamp", format="MM/DD HH:mm"),
                    "user_name": "Technician",
                    "device": "Pyxis Unit",
                    "event_type": "Action"
                }
            )
        else:
            st.info("No Pyxis event data available for deep-dive.")

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

        # --- PLACE AT THE VERY END OF THE FILE ---
        st.divider()
        st.subheader("♻️ Outdate Lifecycle & Replenishment")
        st.caption("Tracking the time between an 'Outdate' removal and the next 'Refill/Load'.")

        if not df_events.empty:
            # 1. Identify "Outdate" removals
            outdate_removals = df_events[
                df_events['event_type'].str.contains('OUTDATE|EXPIRED', case=False, na=False)
            ].copy()
            
            if not outdate_removals.empty:
                outdate_removals = outdate_removals[['dt', 'device', 'med_id', 'med_desc', 'user_name']].rename(
                    columns={'dt': 'Removal_Time', 'user_name': 'Removed_By'}
                )

                # 2. Match with the next Refill/Load
                refills = df_events[df_events['event_type'].str.contains('REFILL|LOAD', case=False, na=False)].copy()
                refills = refills[['dt', 'device', 'med_id', 'qty', 'user_name']].rename(
                    columns={'dt': 'Refill_Time', 'qty': 'Refill_Qty', 'user_name': 'Refilled_By'}
                )

                # 3. Pair them up
                outdate_path = pd.merge(outdate_removals, refills, on=['device', 'med_id'], how='inner')
                outdate_path = outdate_path[outdate_path['Refill_Time'] > outdate_path['Removal_Time']]
                
                # Get the first refill after the outdate
                outdate_path = outdate_path.sort_values('Refill_Time').groupby(['device', 'med_id', 'Removal_Time']).first().reset_index()

                # 4. Calculate Turnaround (in Hours)
                outdate_path['Outage_Duration_Hrs'] = (outdate_path['Refill_Time'] - outdate_path['Removal_Time']).dt.total_seconds() / 3600

                # 5. Display Table
                st.dataframe(
                    outdate_path[['device', 'med_desc', 'Removal_Time', 'Refill_Time', 'Outage_Duration_Hrs', 'Removed_By', 'Refilled_By']],
                    width='stretch',
                    hide_index=True,
                    column_config={
                        "Removal_Time": st.column_config.DatetimeColumn("1. Outdate Removed", format="MM/DD HH:mm"),
                        "Refill_Time": st.column_config.DatetimeColumn("2. Replenished", format="MM/DD HH:mm"),
                        "Outage_Duration_Hrs": st.column_config.NumberColumn("Hours Empty", format="%.1f")
                    }
                )

                if not outdate_path.empty:
                    avg_outdate_gap = outdate_path['Outage_Duration_Hrs'].mean()
                    st.metric("Avg Outdate Replenishment Time", f"{avg_outdate_gap:.1f} Hours")
            else:
                st.info("No 'Outdate' transaction types found in the current Pyxis event data.")


        st.divider()
        st.subheader("🔄 Full Lifecycle: Stockout to Replenishment")
        st.caption("Tracking the sequence: Outage Discovery ➔ Carousel Order ➔ Actual Refill.")

        if not df_events.empty and not df_pharm.empty:
            # 1. Start with the Orders (The Carousel Source of Truth)
            orders = stockout_only[['dt', 'destination', 'med_id', 'med_desc', 'qty']].copy()
            orders.rename(columns={'dt': 'Order_Time', 'destination': 'device', 'qty': 'Order_Qty'}, inplace=True)

            # 2. Identify Pyxis Refills/Loads
            refills = df_events[df_events['event_type'].str.contains('REFILL|LOAD|ADD', case=False, na=False)].copy()
            refills = refills[['dt', 'device', 'med_id', 'qty']].rename(columns={'dt': 'Refill_Time', 'qty': 'Refill_Qty'})

            # 3. Match Order to its First Subsequent Refill
            path = pd.merge(orders, refills, on=['device', 'med_id'], how='left')
            path = path[path['Refill_Time'] > path['Order_Time']]
            # Only keep the absolute first refill after the order was placed
            path = path.sort_values('Refill_Time').groupby(['device', 'med_id', 'Order_Time']).first().reset_index()

            # 4. (Optional) Look for the "Discovery" event PRIOR to the order
            # This handles your "Time Triggered" requirement
            outages = df_events[df_events['event_type'].str.contains('Out of Stock|Empty|Zero', case=False, na=False)].copy()
            if not outages.empty:
                outages = outages[['dt', 'device', 'med_id']].rename(columns={'dt': 'Discovery_Time'})
                trigger_match = pd.merge(path, outages, on=['device', 'med_id'], how='left')
                # Only keep discoveries that happened BEFORE the Carousel Order
                trigger_match = trigger_match[trigger_match['Discovery_Time'] < trigger_match['Order_Time']]
                # Take the most recent discovery before that order
                trigger_match = trigger_match.sort_values('Discovery_Time', ascending=False).groupby(['device', 'med_id', 'Order_Time']).first().reset_index()
                
                # Merge the Discovery Time back into our main path
                path = pd.merge(path, trigger_match[['device', 'med_id', 'Order_Time', 'Discovery_Time']], on=['device', 'med_id', 'Order_Time'], how='left')

            # 5. Calculate Turnaround (Pharmacy Efficiency)
            # This is the time from Carousel Pull to Floor Restock
            path['Pharmacy_Turnaround_Min'] = (path['Refill_Time'] - path['Order_Time']).dt.total_seconds() / 60

            # 6. Formatting for the Dashboard
            display_df = path.copy()
            display_df['Discovery'] = display_df['Discovery_Time'].dt.strftime('%m/%d %H:%M') if 'Discovery_Time' in display_df else "Not Found"
            display_df['Order (Carousel)'] = display_df['Order_Time'].dt.strftime('%m/%d %H:%M')
            display_df['Actual Refill'] = display_df['Refill_Time'].dt.strftime('%m/%d %H:%M')
            display_df['Turnaround'] = display_df['Pharmacy_Turnaround_Min'].apply(lambda x: f"{int(x)} min" if pd.notnull(x) else "-")

            if not path.empty:
                avg_turnaround = path['Pharmacy_Turnaround_Min'].mean()
                st.metric("Avg Pharmacy Turnaround (Order to Refill)", f"{int(avg_turnaround)} Minutes")
                
                st.caption("👆 Click a row to see the exact raw logs for that replenishment cycle.")
                
                # 1. Interactive Table
                event = st.dataframe(
                    display_df[['device', 'med_desc', 'Discovery', 'Order (Carousel)', 'Actual Refill', 'Turnaround']],
                    width='stretch', 
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    column_config={
                        "device": "Unit",
                        "med_desc": "Medication",
                        "Discovery": "1. Time Triggered (Hit 0)",
                        "Order (Carousel)": "2. Carousel Order Created",
                        "Actual Refill": "3. Actual Refill Completed"
                    }
                )

                # 2. Drill-Down Logic
                if len(event.selection.rows) > 0:
                    idx = event.selection.rows[0]
                    sel_row = display_df.iloc[idx]
                    
                    st.divider()
                    st.subheader(f"🔬 Audit Trail: {sel_row['med_desc']}")
                    
                    # Pulling the raw data for the specific order and refill
                    raw_order = df_pharm[
                        (df_pharm['dt'] == sel_row['Order_Time']) & 
                        (df_pharm['med_id'] == sel_row['med_id'])
                    ].copy()
                    raw_order['Step'] = "2. Carousel Order"

                    raw_refill = df_events[
                        (df_events['dt'] == sel_row['Refill_Time']) & 
                        (df_events['med_id'] == sel_row['med_id'])
                    ].copy()
                    raw_refill['Step'] = "3. Actual Refill"

                    # Combine and display the mini-log
                    audit_trail = pd.concat([raw_order, raw_refill], ignore_index=True)
                    audit_trail = audit_trail.sort_values('dt')

                    st.dataframe(
                        audit_trail[['dt', 'Step', 'user_name', 'event_type', 'qty']],
                        width='stretch',
                        hide_index=True,
                        column_config={
                            "dt": st.column_config.DatetimeColumn("Exact Timestamp", format="MM/DD HH:mm:ss"),
                            "user_name": "Technician",
                            "event_type": "Action Type"
                        }
                    )
            else:
                st.info("No completed replenishment cycles found in this date range.")




