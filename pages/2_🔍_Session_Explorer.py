import streamlit as st
import pandas as pd
import numpy as np
from App import load_data, seconds_to_mmss 

st.set_page_config(page_title="Session Explorer", page_icon="🔍", layout="wide")
st.header("🔍 Unified Session Explorer")
st.caption("Analyzing chronological work blocks across Pyxis and Pharmacy systems.")

# ----------------------------------------------------
# Independent Date Filter
# ----------------------------------------------------

c1, c2 = st.columns(2)

start_date = c1.date_input("Start Date")
end_date = c2.date_input("End Date")

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

df_events, _, df_pharm, _, _ = load_data(start_date, end_date)

# ----------------------------
# Data Unification
# ----------------------------
px = df_events[['user_name', 'dt', 'device', 'event_type', 'med_desc', 'qty', 'pk']].copy() if not df_events.empty else pd.DataFrame()
if not px.empty:
    px['source'] = 'Pyxis'

ph = df_pharm[['user_name', 'dt', 'destination', 'priority', 'med_desc', 'qty', 'pk']].copy() if not df_pharm.empty else pd.DataFrame()
if not ph.empty:
    ph.rename(columns={'priority': 'event_type', 'destination': 'device'}, inplace=True)
    ph['source'] = 'Pharmacy'

combined = pd.concat([px, ph], ignore_index=True)

if combined.empty:
    st.warning("No unified data available.")
    st.stop()

combined['dt'] = pd.to_datetime(combined['dt'])
combined.sort_values(['user_name', 'dt'], inplace=True)

# ----------------------------
# Session Logic
# ----------------------------
combined['prev_user'] = combined['user_name'].shift()
combined['prev_device'] = combined['device'].shift()
combined['prev_dt'] = combined['dt'].shift()

combined['gap'] = (combined['dt'] - combined['prev_dt']).dt.total_seconds().fillna(0)

combined['is_new_session'] = np.where(
    (combined['user_name'] != combined['prev_user']) |
    (combined['device'] != combined['prev_device']) |
    (combined['gap'] > 1200),
    1, 0
)

combined['session_id'] = combined['is_new_session'].cumsum()

# ----------------------------
# Aggregate Sessions
# ----------------------------
sessions = combined.groupby('session_id').agg({
    'user_name': 'first',
    'device': 'first',
    'source': 'first',
    'dt': ['min', 'max'],
    'pk': 'count'
}).reset_index()

sessions.columns = ['session_id', 'User', 'Device', 'Source', 'Start', 'End', 'Tx Count']

sessions['Duration'] = (sessions['End'] - sessions['Start']).dt.total_seconds()
sessions['Duration'] = np.where(sessions['Duration'] < 10, 30, sessions['Duration'])

sessions = sessions.sort_values(['User', 'Start'])
sessions['Next Start'] = sessions.groupby('User')['Start'].shift(-1)
sessions['Walk Time'] = (sessions['Next Start'] - sessions['End']).dt.total_seconds()

# ----------------------------
# Filters
# ----------------------------
c1, c2, c3 = st.columns(3)

all_users = sorted(sessions['User'].dropna().unique())
sel_u = c1.multiselect("Filter User", all_users, key="u_sess_uni")

min_dur = c2.number_input("Min Duration (sec)", 0, 3600, 0)

sel_source = c3.multiselect(
    "Filter Source",
    ["Pyxis", "Pharmacy"],
    default=["Pyxis", "Pharmacy"]
)

view = sessions.copy()

if sel_u:
    view = view[view['User'].isin(sel_u)]

if min_dur:
    view = view[view['Duration'] >= min_dur]

if sel_source:
    view = view[view['Source'].isin(sel_source)]

# ----------------------------
# Single User Metrics
# ----------------------------
if len(sel_u) == 1:
    st.divider()

    total_active = view['Duration'].sum()
    pyxis_time = view[view['Source'] == 'Pyxis']['Duration'].sum()
    pharm_time = view[view['Source'] == 'Pharmacy']['Duration'].sum()

    top_device = view['Device'].mode()[0] if not view.empty else "N/A"

    st.subheader(f"🧠 Shift Analysis: {sel_u[0].title()}")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Active Time", seconds_to_mmss(total_active))
    k2.metric("Pyxis Time", seconds_to_mmss(pyxis_time))
    k3.metric("Pharmacy Time", seconds_to_mmss(pharm_time))
    k4.metric("Most Visited", top_device)

    st.divider()

# ----------------------------
# Display Table
# ----------------------------
disp = view.copy().reset_index(drop=True)

disp['Date'] = disp['Start'].dt.strftime('%m/%d/%y')
disp['Start_Time'] = disp['Start'].dt.strftime('%H:%M:%S')
disp['End_Time'] = disp['End'].dt.strftime('%H:%M:%S')
disp['Duration_Str'] = disp['Duration'].apply(seconds_to_mmss)

disp['Walk_Disp'] = disp['Walk Time'].apply(
    lambda x: seconds_to_mmss(x) if pd.notnull(x) and x >= 0 else "-"
)

st.caption("👆 Click a row to see the exact transactions within that work block.")

event = st.dataframe(
    disp[['session_id', 'User', 'Date', 'Source', 'Device',
          'Start_Time', 'End_Time', 'Tx Count',
          'Duration_Str', 'Walk_Disp']],
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
    hide_index=True
)

# ----------------------------
# Drilldown
# ----------------------------
if len(event.selection.rows) > 0:
    idx = event.selection.rows[0]
    sel_id = disp.iloc[idx]['session_id']

    details = combined[combined['session_id'] == sel_id].sort_values('dt')

    st.divider()
    st.subheader(f"🔬 Session Details: {details['device'].iloc[0]}")

    st.dataframe(
        details[['dt', 'source', 'event_type', 'med_desc', 'qty']],
        use_container_width=True,
        column_config={
            "dt": st.column_config.DatetimeColumn("Time", format="HH:mm:ss")
        }
    )

# ----------------------------
# Developer Debug
# ----------------------------
st.divider()

with st.expander("🛠️ Developer Debug: Raw Event Stream", expanded=False):

    if not sel_u:
        st.info("Select at least one user to view raw debug data.")
    else:
        st.write(f"Debugging data for: **{sel_u}**")

        debug_view = combined[combined['user_name'].isin(sel_u)].sort_values('dt')

        if debug_view.empty:
            st.warning("No raw events found for selected user.")
        else:
            st.write(f"Total Raw Events Found: {len(debug_view)}")
            st.write(debug_view['source'].value_counts())

            st.dataframe(
                debug_view[['dt', 'source', 'device',
                            'event_type', 'med_desc', 'qty']],
                use_container_width=True
            )
