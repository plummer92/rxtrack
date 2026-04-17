import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)
import streamlit as st
import pandas as pd
import numpy as np
import io


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

st.set_page_config(page_title="Workforce Intelligence", layout="wide")
load_data = App.load_data
seconds_to_mmss = App.seconds_to_mmss
render_sidebar = App.render_sidebar

render_sidebar()
if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Workforce Intelligence Scorecard",
        "Executive-level technician performance analytics presented in the unified RxTrack interface.",
        kicker="Performance",
    )
    _debug_event("Workforce Intelligence", "shared_intro_loaded")
    _debug_panel("Workforce Intelligence", intro_mode="shared")
else:
    st.header("📊 Workforce Intelligence Scorecard")
    st.caption("Executive-level technician performance analytics.")
    _debug_event("Workforce Intelligence", "fallback_header_used")
    _debug_panel("Workforce Intelligence", intro_mode="fallback")

if 'start_date' not in st.session_state:
    st.info("👈 Select a date range on Overview first.")

else:
    with st.spinner("Loading workforce data..."):
        df_events, _, df_pharm, df_sched, df_att = load_data(
            st.session_state.start_date,
            st.session_state.end_date
        )

    if df_events.empty and df_pharm.empty:
        st.warning("No activity found for selected window.")
        st.stop()

    # --- Build Combined Activity ---
    px = df_events[['user_name', 'dt', 'device', 'qty']].copy() if not df_events.empty else pd.DataFrame()
    if not px.empty:
        px['source'] = 'Pyxis'

    ph = df_pharm[['user_name', 'dt', 'destination', 'qty']].copy() if not df_pharm.empty else pd.DataFrame()
    if not ph.empty:
        ph.rename(columns={'destination': 'device'}, inplace=True)
        ph['source'] = 'Pharmacy'

    combined = pd.concat([px, ph], ignore_index=True)

    if combined.empty:
        st.warning("No combined data available.")
        st.stop()

    combined['dt'] = pd.to_datetime(combined['dt'])
    combined.sort_values(['user_name', 'dt'], inplace=True)

    # Session logic
    combined['prev_user'] = combined['user_name'].shift()
    combined['prev_dt'] = combined['dt'].shift()
    combined['gap'] = (combined['dt'] - combined['prev_dt']).dt.total_seconds()
    combined['new_session'] = (
        (combined['user_name'] != combined['prev_user']) |
        (combined['gap'] > 1200)
    ).astype(int)

    combined['session_id'] = combined['new_session'].cumsum()

    sessions = combined.groupby(['session_id', 'user_name']).agg(
        start=('dt', 'min'),
        end=('dt', 'max'),
        tx=('qty', 'count')
    ).reset_index()

    sessions['duration'] = (sessions['end'] - sessions['start']).dt.total_seconds()
    sessions['duration'] = np.where(sessions['duration'] < 30, 60, sessions['duration'])

    # Summary
    summary = sessions.groupby('user_name').agg(
        total_active=('duration', 'sum'),
        total_tx=('tx', 'sum'),
        avg_session=('duration', 'mean'),
        sessions=('session_id', 'count')
    ).reset_index()

    summary['tx_per_hour'] = summary['total_tx'] / (summary['total_active'] / 3600)

    # Walk time
    sessions = sessions.sort_values(['user_name', 'start'])
    sessions['next_start'] = sessions.groupby('user_name')['start'].shift(-1)
    sessions['walk_time'] = (sessions['next_start'] - sessions['end']).dt.total_seconds()
    sessions['walk_time'] = sessions['walk_time'].clip(lower=0)

    walk = sessions.groupby('user_name')['walk_time'].sum().reset_index()
    summary = summary.merge(walk, on='user_name', how='left')

    summary['walk_time'] = summary['walk_time'].fillna(0)
    summary['walk_pct'] = summary['walk_time'] / (summary['total_active'] + summary['walk_time'])

    # Tardy logic (wrap in safety check)
    if not df_sched.empty and not df_att.empty:
        from App import parse_shift_start, normalize_name, load_admin_users

        df_sched['match_key'] = df_sched['staff_name'].apply(normalize_name)
        df_att['match_key'] = df_att['raw_name'].apply(normalize_name)
        df_sched['date_obj'] = pd.to_datetime(df_sched['dt']).dt.date
        df_att['date_obj'] = pd.to_datetime(df_att['dt_date']).dt.date

        df_sched = df_sched[~df_sched['match_key'].isin(load_admin_users())]

        merged = pd.merge(df_sched, df_att, on=['match_key', 'date_obj'], how='inner')

        merged['actual'] = pd.to_datetime(merged['start_dt'], errors='coerce')
        merged['scheduled'] = merged.apply(
            lambda x: parse_shift_start(x['date_obj'], x['shift_type']),
            axis=1
        )

        merged = merged.dropna(subset=['actual', 'scheduled'])
        merged['delay_min'] = (
            (merged['actual'] - merged['scheduled']).dt.total_seconds() / 60
        )

        tardies = merged[merged['delay_min'] > 5]
        tardy_counts = tardies.groupby('staff_name').size().reset_index(name='tardies')

        summary = summary.merge(
            tardy_counts,
            left_on='user_name',
            right_on='staff_name',
            how='left'
        )

    summary['tardies'] = summary.get('tardies', pd.Series(0, index=summary.index))
    summary['tardies'] = summary['tardies'].fillna(0)

    # Efficiency score
    summary['efficiency_score'] = (
        (summary['tx_per_hour'] * 0.6) +
        ((1 - summary['walk_pct']) * 30) -
        (summary['tardies'] * 2)
    )

    summary = summary.sort_values('efficiency_score', ascending=False).reset_index(drop=True)

    top_25 = summary['efficiency_score'].quantile(0.75)
    bottom_25 = summary['efficiency_score'].quantile(0.25)

    def tier(x):
        if x >= top_25:
            return "🟢 Top Performer"
        elif x <= bottom_25:
            return "🔴 Coaching Zone"
        return "🟡 Solid"

    summary['Tier'] = summary['efficiency_score'].apply(tier)

    def red_flag(row):
        if row['tardies'] >= 3:
            return "⚠ Frequent Tardies"
        if row['walk_pct'] > 0.35:
            return "⚠ Excessive Idle"
        if row['tx_per_hour'] < summary['tx_per_hour'].mean() * 0.6:
            return "⚠ Low Productivity"
        return ""

    summary['Alert'] = summary.apply(red_flag, axis=1)

    summary['Active Time'] = summary['total_active'].apply(seconds_to_mmss)

    export_cols = ['user_name', 'Active Time', 'tx_per_hour', 'walk_pct',
                   'tardies', 'efficiency_score', 'Tier', 'Alert']
    st.dataframe(
        summary[export_cols],
        use_container_width=True
    )
    st.download_button(
        "⬇️ Export Scorecard to Excel",
        data=to_excel_bytes(summary[export_cols]),
        file_name="workforce_scorecard.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )







