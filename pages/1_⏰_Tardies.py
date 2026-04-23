import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import timedelta
# Importing shared logic and admin list from your Hub
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(page_title="Tardy Analytics", page_icon="⏰", layout="wide")
render_sidebar = App.render_sidebar
load_data = App.load_data
normalize_name = App.normalize_name
parse_shift_start = App.parse_shift_start
load_admin_users = App.load_admin_users

render_sidebar()
if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Tardiness & Attendance Analytics",
        "Identify delay patterns, frequent offenders, and shift reliability without leaving the shared RxTrack shell.",
        kicker="Performance",
    )
    _debug_event("Tardies", "shared_intro_loaded")
    _debug_panel("Tardies", intro_mode="shared")
else:
    st.header("⏰ Tardiness & Attendance Analytics")
    _debug_event("Tardies", "fallback_header_used")
    _debug_panel("Tardies", intro_mode="fallback")

# Leadership Filter: Define significant tardiness
grace_period = st.sidebar.slider(
    "Define Grace Period (Minutes)", 
    0, 30, 5, 
    help="Delays fewer than these minutes are hidden as 'noise'."
)

if 'start_date' not in st.session_state:
    st.info("👈 Please select a date range on the **Overview** page first.")
else:
    # 1. Load data using anchored sidebar dates
    with st.spinner("Loading attendance data..."):
        _, _, _, df_sched, df_att = load_data(
            st.session_state.start_date,
            st.session_state.end_date
        )
    
    if not df_sched.empty and not df_att.empty:
        # 2. Data Preparation and Admin Exclusion
        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)
        df_att['match_key'] = df_att['raw_name'].apply(normalize_name)
        df_sched['date_obj'] = pd.to_datetime(df_sched['dt']).dt.date
        df_att['date_obj'] = pd.to_datetime(df_att['dt_date']).dt.date
        if 'schedule_status' not in df_sched.columns:
            df_sched['schedule_status'] = df_sched.get('assignment_type', 'Standard')
        df_sched['schedule_status'] = (
            df_sched['schedule_status']
            .fillna(df_sched.get('assignment_type', 'Standard'))
            .astype(str)
            .replace({"": "Standard"})
        )
        
        # Filter out Joe, Krista, and Emily based on the App.py list
        df_sched = df_sched[~df_sched['match_key'].isin(load_admin_users())]
        status_counts = (
            df_sched.groupby('schedule_status')
            .size()
            .reset_index(name='scheduled_shifts')
            .sort_values('scheduled_shifts', ascending=False)
        )

        with st.sidebar:
            status_options = sorted(df_sched['schedule_status'].dropna().unique())
            selected_statuses = st.multiselect(
                "Schedule Status",
                status_options,
                default=status_options,
                help="Separate normal shifts from trades, adjustments, open shifts, and incentive pay."
            )

        if selected_statuses:
            df_sched = df_sched[df_sched['schedule_status'].isin(selected_statuses)].copy()
        
        # 3. Smart Shift Matching (Handles 1000 weekend vs 1300 weekday)
        merged = pd.merge(df_sched, df_att, on=['match_key', 'date_obj'], how='inner')
        
        if not merged.empty:
            merged['actual_clock_in'] = pd.to_datetime(merged['start_dt'], errors='coerce')
            # parse_shift_start reads the specific shift string for that exact date
            merged['scheduled_start'] = merged.apply(
                lambda x: parse_shift_start(x['date_obj'], x['shift_type']), axis=1
            )
            
            merged = merged.dropna(subset=['actual_clock_in', 'scheduled_start'])
            merged['delay_min'] = (merged['actual_clock_in'] - merged['scheduled_start']).dt.total_seconds() / 60
            
            # Filter for true tardies > grace period
            tardies = merged[merged['delay_min'] > grace_period].sort_values('delay_min', ascending=False).copy()
            
            # Formatting for display
            tardies['Clock In'] = tardies['actual_clock_in'].dt.strftime('%H:%M')
            tardies['Scheduled'] = tardies['scheduled_start'].dt.strftime('%H:%M')
            tardies['Late By'] = tardies['delay_min'].apply(lambda x: f"{int(x)} min")

            # --- METRICS OVERVIEW ---
            trade_ct = int((df_sched['schedule_status'] == 'Trade').sum())
            adjustment_ct = int((df_sched['schedule_status'] == 'Adjustment').sum())
            open_ct = int((df_sched['schedule_status'] == 'Open Shift').sum())
            incentive_ct = int((df_sched['schedule_status'] == 'Incentive Pay').sum())

            m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
            m1.metric(f"Technicians > {grace_period}m Late", len(tardies))
            m2.metric("Avg Delay", f"{int(tardies['delay_min'].mean()) if not tardies.empty else 0} min")
            m3.metric("Most Impacted Shift", tardies['shift_type'].mode()[0] if not tardies.empty else "None")
            m4.metric("Trades", trade_ct)
            m5.metric("Adjustments", adjustment_ct)
            m6.metric("Open Shifts", open_ct)
            m7.metric("Incentive Pay", incentive_ct)

            # --- ANALYTICS TABS ---
            tab1, tab2, tab3, tab4 = st.tabs(["📋 Tardy Log", "📊 Pattern Analysis", "🔍 Individual Audit", "Schedule Exceptions"])

            with tab1:
                st.dataframe(
                    tardies[['date_obj', 'staff_name', 'shift_type', 'schedule_status', 'Scheduled', 'Clock In', 'Late By']],
                    use_container_width=True, hide_index=True
                )

            with tab2:
                col_left, col_right = st.columns(2)
                with col_left:
                    st.subheader("Frequent Offenders")
                    tech_counts = tardies['staff_name'].value_counts().reset_index()
                    fig_tech = px.bar(tech_counts, x='count', y='staff_name', orientation='h', 
                                     title="Tardies per Technician", color='count', color_continuous_scale='Reds')
                    st.plotly_chart(fig_tech, use_container_width=True)

                with col_right:
                    st.subheader("Problematic Shifts")
                    # Spot if 1000 weekend starts have higher failure rates
                    shift_counts = tardies['shift_type'].value_counts().reset_index()
                    fig_shift = px.bar(shift_counts, x='count', y='shift_type', orientation='h', 
                                      title="Tardies by Shift Type", color='count')
                    st.plotly_chart(fig_shift, use_container_width=True)

                st.subheader("Schedule Status Mix")
                if not status_counts.empty:
                    fig_status = px.bar(
                        status_counts,
                        x='scheduled_shifts',
                        y='schedule_status',
                        orientation='h',
                        color='schedule_status',
                        title="Scheduled Shifts by Status"
                    )
                    st.plotly_chart(fig_status, use_container_width=True)

            with tab3:
                selected_tech = st.selectbox("Select Technician:", ["All"] + sorted(list(tardies['staff_name'].unique())))
                view_df = tardies if selected_tech == "All" else tardies[tardies['staff_name'] == selected_tech]
                
                st.write(f"Showing **{len(view_df)}** incidents for {selected_tech}")
                st.dataframe(
                    view_df[['date_obj', 'shift_type', 'schedule_status', 'Scheduled', 'Clock In', 'Late By', 'delay_min']],
                    use_container_width=True, hide_index=True,
                    column_config={"delay_min": st.column_config.NumberColumn("Minutes Late", format="%d")}
                )

            with tab4:
                exception_df = df_sched[df_sched['schedule_status'].ne('Standard')].copy()
                if exception_df.empty:
                    st.success("No trade, adjustment, open-shift, or incentive-pay schedule rows in this window.")
                else:
                    display_cols = ['date_obj', 'staff_name', 'shift_type', 'schedule_status', 'assignment_type', 'note']
                    st.dataframe(
                        exception_df[[c for c in display_cols if c in exception_df.columns]],
                        use_container_width=True,
                        hide_index=True
                    )
        else:
            st.success("🎉 All staff arrived within the grace period for this window.")
    else:
        st.warning("Please ensure Schedule and Attendance files are uploaded to the Hub.")




