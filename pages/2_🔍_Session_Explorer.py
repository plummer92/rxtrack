import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import re
import json
from datetime import time
from sqlalchemy import text
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(page_title="Session Explorer", page_icon="🔍", layout="wide")
App.apply_global_styles()

load_data = App.load_data
seconds_to_mmss = App.seconds_to_mmss
render_sidebar = App.render_sidebar
normalize_name = App.normalize_name
engine = App.engine


@st.cache_data(ttl=300)
def load_shift_audit_profiles():
    try:
        App.init_db()
        sql = text("""
            SELECT profile_name, page_name, shifts_json, selected_names_json, view_scope, active
            FROM shift_audit_profiles
            WHERE page_name = 'shift_work_map' AND active = TRUE
            ORDER BY profile_name
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn)
        if df.empty:
            return df
        df["shifts"] = df["shifts_json"].apply(lambda x: json.loads(x) if pd.notna(x) and str(x).strip() else [])
        df["selected_names"] = df["selected_names_json"].apply(lambda x: json.loads(x) if pd.notna(x) and str(x).strip() else [])
        return df
    except Exception:
        return pd.DataFrame(columns=["profile_name", "page_name", "shifts", "selected_names", "view_scope", "active"])


def save_shift_audit_profile(profile_name, shifts, selected_names, view_scope):
    try:
        App.init_db()
        sql = text("""
            INSERT INTO shift_audit_profiles
                (profile_name, page_name, shifts_json, selected_names_json, view_scope, active, updated_at)
            VALUES
                (:profile_name, 'shift_work_map', :shifts_json, :selected_names_json, :view_scope, TRUE, NOW())
            ON CONFLICT (profile_name) DO UPDATE SET
                shifts_json = EXCLUDED.shifts_json,
                selected_names_json = EXCLUDED.selected_names_json,
                view_scope = EXCLUDED.view_scope,
                active = TRUE,
                updated_at = NOW()
        """)
        with engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "profile_name": profile_name.strip(),
                    "shifts_json": json.dumps(shifts),
                    "selected_names_json": json.dumps(selected_names),
                    "view_scope": view_scope,
                },
            )
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Could not save audit profile: {e}")
        return False


def summarize_shift_audit(active_sessions, active_work_keys, training_count):
    total_active_sec = active_sessions["duration_sec"].sum()
    total_walk_sec = active_sessions["walk_sec"].sum()
    long_gap_count = int(active_sessions["long_gap_flag"].sum())
    return {
        "staff_on_shift": len(active_work_keys),
        "sessions": len(active_sessions),
        "active_sec": total_active_sec,
        "walk_sec": total_walk_sec,
        "long_gap_count": long_gap_count,
        "training_count": training_count,
    }


@st.cache_data(ttl=300)
def load_inventory_verification_events(start_date, end_date):
    """Load Pyxis inventory verification rows with count-before/count-after fields."""
    try:
        sql = text("""
            SELECT
                pk, dt, user_name, device, med_id, med_desc, event_type, qty,
                beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason
            FROM events
            WHERE dt::date BETWEEN :start_date AND :end_date
              AND (
                    event_type ILIKE '%verify%'
                 OR event_type ILIKE '%verified%'
                 OR event_type ILIKE '%inventory%'
                 OR event_type ILIKE '%count%'
              )
            ORDER BY dt
        """)
        with engine.connect() as conn:
            df = pd.read_sql(
                sql,
                conn,
                params={"start_date": str(start_date), "end_date": str(end_date)},
            )
        if df.empty:
            return df

        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["device"] = df["device"].fillna("Unknown").astype(str).str.strip()
        df["event_type"] = df["event_type"].fillna("").astype(str).str.strip()
        df["med_desc"] = df["med_desc"].fillna("").astype(str).str.strip()
        for col in ["qty", "beginning_qty", "ending_qty", "discrepancy_qty"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["dt"])
    except Exception as e:
        st.error(f"[load_inventory_verification_events] {e}")
        return pd.DataFrame()


def filter_by_time_window(df, start_t, end_t):
    if df.empty:
        return df
    start_minutes = start_t.hour * 60 + start_t.minute
    end_minutes = end_t.hour * 60 + end_t.minute
    event_minutes = df["dt"].dt.hour * 60 + df["dt"].dt.minute
    if start_minutes <= end_minutes:
        return df[(event_minutes >= start_minutes) & (event_minutes <= end_minutes)].copy()
    return df[(event_minutes >= start_minutes) | (event_minutes <= end_minutes)].copy()


def add_inventory_change_flags(df):
    if df.empty:
        return df
    work = df.copy()
    has_begin_end = work["beginning_qty"].notna() & work["ending_qty"].notna()
    begin_end_changed = has_begin_end & work["beginning_qty"].ne(work["ending_qty"])
    discrepancy_changed = work["discrepancy_qty"].fillna(0).ne(0)
    work["count_changed"] = begin_end_changed | discrepancy_changed
    work["change_amount"] = np.where(
        has_begin_end,
        work["ending_qty"].fillna(0) - work["beginning_qty"].fillna(0),
        work["discrepancy_qty"].fillna(0),
    )
    return work


@st.cache_data(ttl=300)
def load_same_med_device_history(device, med_id, selected_dt):
    """Load prior inventory verification events for the same Pyxis device and med_id."""
    try:
        sql = text("""
            SELECT
                pk, dt::timestamp AS dt, user_name, device, med_id, med_desc, event_type, qty,
                beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason
            FROM events
            WHERE device = :device
              AND med_id = :med_id
              AND dt::timestamp <= CAST(:selected_dt AS timestamp)
              AND (
                    event_type ILIKE '%verify%'
                 OR event_type ILIKE '%verified%'
                 OR event_type ILIKE '%inventory%'
                 OR event_type ILIKE '%count%'
              )
              AND event_type NOT ILIKE '%empty%'
              AND event_type NOT ILIKE '%return bin%'
              AND event_type NOT ILIKE '%refill%'
            ORDER BY dt::timestamp DESC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(
                sql,
                conn,
                params={
                    "device": str(device),
                    "med_id": str(med_id),
                    "selected_dt": selected_dt,
                },
            )
        if df.empty:
            return df
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["event_type"] = df["event_type"].fillna("").astype(str).str.strip()
        df["med_desc"] = df["med_desc"].fillna("").astype(str).str.strip()
        for col in ["qty", "beginning_qty", "ending_qty", "discrepancy_qty"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return add_inventory_change_flags(df.dropna(subset=["dt"]))
    except Exception as e:
        st.error(f"[load_same_med_device_history] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_current_pocket_locations(device, med_id):
    """Find the current pocket location for a device/med pair when inventory detail exists."""
    try:
        sql = text("""
            SELECT 'Detailed inventory' AS source_name, station AS device, pocket_location, current_count
            FROM inventory_detailed
            WHERE station = :device AND med_id = :med_id
            UNION ALL
            SELECT 'Device inventory' AS source_name, device, pocket_location, current_quantity AS current_count
            FROM device_inventory
            WHERE device = :device AND med_id = :med_id
            ORDER BY source_name, pocket_location
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"device": str(device), "med_id": str(med_id)})
        if df.empty:
            return df
        df["pocket_location"] = df["pocket_location"].fillna("").astype(str).str.strip()
        df["current_count"] = pd.to_numeric(df["current_count"], errors="coerce")
        return df
    except Exception as e:
        st.error(f"[load_current_pocket_locations] {e}")
        return pd.DataFrame()

start_date, end_date = render_sidebar()
App.require_management_access("Session Explorer")
if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Unified Session Explorer",
        "Analyze chronological work blocks across Pyxis and pharmacy systems in the same navigation shell as the overview.",
        kicker="Performance",
    )
    _debug_event("Session Explorer", "shared_intro_loaded")
    _debug_panel("Session Explorer", intro_mode="shared")
else:
    st.header("🔍 Unified Session Explorer")
    st.caption("Analyze chronological work blocks across Pyxis and pharmacy systems.")
    _debug_event("Session Explorer", "fallback_header_used")
    _debug_panel("Session Explorer", intro_mode="fallback")

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADERS
# ─────────────────────────────────────────────────────────────────────────────

with st.spinner("Loading session data..."):
    df_events, _, df_pharm, _, _ = load_data(start_date, end_date)

# ----------------------------
# Data Unification
# ----------------------------
px_df = df_events[['user_name', 'dt', 'device', 'event_type', 'med_desc', 'qty', 'pk']].copy() if not df_events.empty else pd.DataFrame()
if not px_df.empty:
    px_df['source'] = 'Pyxis'

ph = df_pharm[['user_name', 'dt', 'destination', 'priority', 'med_desc', 'qty', 'pk']].copy() if not df_pharm.empty else pd.DataFrame()
if not ph.empty:
    ph.rename(columns={'priority': 'event_type', 'destination': 'device'}, inplace=True)
    ph['source'] = 'Pharmacy'

combined = pd.concat([px_df, ph], ignore_index=True)

if combined.empty:
    st.warning("No unified data available.")
    st.stop()

combined['dt'] = pd.to_datetime(combined['dt'])
combined.sort_values(['user_name', 'dt'], inplace=True)

def pharmacy_work_label(event_values, start_dt):
    values = [str(value or "").strip() for value in event_values if str(value or "").strip()]
    joined = " ".join(values).lower()
    if "pyxis" in joined and "pull" in joined:
        if pd.notna(start_dt) and int(start_dt.hour) < 7:
            return "0400 Pyxis Pull"
        return "Pyxis Pull"
    if values:
        return pd.Series(values).mode().iloc[0]
    return "Pharmacy Work"


def destination_group_label(destinations):
    unique_destinations = sorted({str(value or "").strip() for value in destinations if str(value or "").strip()})
    if not unique_destinations:
        return "Unknown destination"
    if len(unique_destinations) == 1:
        return unique_destinations[0]
    preview = ", ".join(unique_destinations[:3])
    if len(unique_destinations) > 3:
        return f"{preview} + {len(unique_destinations) - 3} more"
    return preview

# ----------------------------
# Session Logic
# ----------------------------
combined['prev_user'] = combined['user_name'].shift()
combined['session_work_key'] = np.where(
    combined['source'].eq('Pharmacy'),
    'Pharmacy',
    'Pyxis|' + combined['device'].fillna('').astype(str),
)
combined['prev_work_key'] = combined['session_work_key'].shift()
combined['prev_dt'] = combined['dt'].shift()

combined['gap'] = (combined['dt'] - combined['prev_dt']).dt.total_seconds().fillna(0)

combined['is_new_session'] = np.where(
    (combined['user_name'] != combined['prev_user']) |
    (combined['session_work_key'] != combined['prev_work_key']) |
    (combined['gap'] > 1200),
    1, 0
)

combined['session_id'] = combined['is_new_session'].cumsum()

# ----------------------------
# Aggregate Sessions
# ----------------------------
sessions = combined.groupby('session_id').agg(
    User=('user_name', 'first'),
    Device=('device', 'first'),
    Work_Key=('session_work_key', 'first'),
    Source=('source', 'first'),
    Primary_Event=('event_type', 'first'),
    Primary_Med=('med_desc', 'first'),
    Start=('dt', 'min'),
    End=('dt', 'max'),
    Destinations=('device', 'nunique'),
    Tx_Count=('pk', 'count'),
).reset_index()
sessions.rename(columns={
    'Work_Key': 'Work Key',
    'Primary_Event': 'Primary Event',
    'Primary_Med': 'Primary Med',
    'Tx_Count': 'Tx Count',
}, inplace=True)
sessions['Source Type'] = sessions['Source']
session_labels = combined.groupby('session_id').apply(
    lambda group: pd.Series({
        'Display Source': pharmacy_work_label(group['event_type'], group['dt'].min())
        if group['source'].iloc[0] == 'Pharmacy'
        else group['source'].iloc[0],
        'Display Device': destination_group_label(group['device']),
    })
).reset_index()
sessions = sessions.merge(session_labels, on='session_id', how='left')
sessions.loc[sessions['Source'].eq('Pharmacy'), 'Source'] = sessions.loc[sessions['Source'].eq('Pharmacy'), 'Display Source']
sessions.loc[sessions['Work Key'].eq('Pharmacy'), 'Device'] = sessions.loc[sessions['Work Key'].eq('Pharmacy'), 'Display Device']

sessions['Duration'] = (sessions['End'] - sessions['Start']).dt.total_seconds()
sessions['Duration'] = np.where(sessions['Duration'] < 10, 30, sessions['Duration'])

sessions = sessions.sort_values(['User', 'Start'])
sessions['Next Start'] = sessions.groupby('User')['Start'].shift(-1)
sessions['Walk Time'] = (sessions['Next Start'] - sessions['End']).dt.total_seconds()

# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════

tab1, tab2, tab3, tab4 = st.tabs(["Session View", "Shift Timeline", "Shift Work Map", "Inventory Accuracy"])

WORK_TYPE_ORDER = [
    "Carousel / 0400 Pull",
    "Pyxis Outdates",
    "Returns / Carousel Putaway",
    "Pyxis Maintenance",
]

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — SESSION VIEW (existing)
# ─────────────────────────────────────────────────────────────────────────────

with tab1:
    # ----------------------------
    # Filters
    # ----------------------------
    c0, c1, c2, c3 = st.columns([1.2, 1.4, 1, 1.4])

    available_session_dates = sorted(sessions["Start"].dt.date.dropna().unique())
    if available_session_dates:
        default_session_date = end_date if end_date in available_session_dates else available_session_dates[-1]
        if st.session_state.get("session_view_date") not in available_session_dates:
            st.session_state.session_view_date = default_session_date
        selected_session_date = c0.selectbox(
            "Session Date",
            options=available_session_dates,
            index=available_session_dates.index(st.session_state.session_view_date),
            key="session_view_date",
            format_func=lambda value: value.strftime("%m/%d/%Y (%A)"),
        )
        sessions_for_day = sessions[sessions["Start"].dt.date == selected_session_date].copy()
    else:
        selected_session_date = None
        sessions_for_day = sessions.copy()
        c0.info("No session dates found.")

    all_users = sorted(sessions_for_day['User'].dropna().unique())
    if "u_sess_uni" in st.session_state:
        st.session_state.u_sess_uni = [user for user in st.session_state.u_sess_uni if user in all_users]
    sel_u = c1.multiselect("Filter User", all_users, key="u_sess_uni")

    min_dur = c2.number_input("Min Duration (sec)", 0, 3600, 0)

    all_sources = sorted(sessions_for_day['Source'].dropna().unique())
    if "session_view_sources" in st.session_state:
        st.session_state.session_view_sources = [
            source for source in st.session_state.session_view_sources if source in all_sources
        ]
    sel_source = c3.multiselect(
        "Filter Source",
        all_sources,
        default=all_sources,
        key="session_view_sources",
    )

    view = sessions_for_day.copy()

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
        pyxis_time = view[view['Source Type'] == 'Pyxis']['Duration'].sum()
        pharm_time = view[view['Source Type'] == 'Pharmacy']['Duration'].sum()

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
              'Start_Time', 'End_Time', 'Destinations', 'Tx Count',
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
            details[['dt', 'source', 'device', 'event_type', 'med_desc', 'qty']],
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

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — SHIFT TIMELINE
# ─────────────────────────────────────────────────────────────────────────────

with tab4:
    st.subheader("Inventory Accuracy by Delivery Run")
    st.caption(
        "Compare how many verified inventory checks each delivery tech completed and how often the count had to be changed."
    )

    f1, f2, f3 = st.columns([1, 1, 2])
    run_start = f1.time_input("Run start", value=time(5, 0), key="inventory_run_start")
    run_end = f2.time_input("Run end", value=time(8, 0), key="inventory_run_end")

    inv_events = add_inventory_change_flags(load_inventory_verification_events(start_date, end_date))
    inv_events = filter_by_time_window(inv_events, run_start, run_end)

    if inv_events.empty:
        st.info("No inventory verification events were found for the selected date range and run window.")
    else:
        all_inventory_users = sorted(inv_events["user_name"].dropna().unique().tolist())
        selected_inventory_users = f3.multiselect(
            "Technicians",
            all_inventory_users,
            default=all_inventory_users,
            key="inventory_accuracy_users",
        )

        accuracy_view = inv_events.copy()
        if selected_inventory_users:
            accuracy_view = accuracy_view[accuracy_view["user_name"].isin(selected_inventory_users)]

        if accuracy_view.empty:
            st.info("No inventory checks match the selected technicians.")
        else:
            summary = (
                accuracy_view.groupby("user_name")
                .agg(
                    verified_checks=("pk", "count"),
                    changed_counts=("count_changed", "sum"),
                    active_days=("dt", lambda s: s.dt.date.nunique()),
                    first_check=("dt", "min"),
                    last_check=("dt", "max"),
                    devices=("device", lambda s: ", ".join(sorted(set(s.dropna().astype(str))))),
                )
                .reset_index()
            )
            summary["changed_counts"] = summary["changed_counts"].astype(int)
            summary["changed_count_pct"] = np.where(
                summary["verified_checks"].gt(0),
                summary["changed_counts"] / summary["verified_checks"] * 100,
                0,
            )
            summary = summary.sort_values(["changed_count_pct", "verified_checks"], ascending=[False, False])

            total_checks = int(summary["verified_checks"].sum())
            total_changed = int(summary["changed_counts"].sum())
            overall_pct = (total_changed / total_checks * 100) if total_checks else 0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Verified Checks", f"{total_checks:,}")
            m2.metric("Counts Changed", f"{total_changed:,}")
            m3.metric("Changed Count %", f"{overall_pct:.1f}%")
            m4.metric("Technicians", f"{summary['user_name'].nunique():,}")

            st.caption(
                "Changed Count % = verified inventory checks where the beginning and ending count differed, "
                "or the discrepancy quantity was non-zero."
            )

            chart_df = summary.rename(
                columns={
                    "user_name": "Technician",
                    "verified_checks": "Verified Checks",
                    "changed_counts": "Counts Changed",
                    "changed_count_pct": "Changed Count %",
                }
            )
            fig = px.bar(
                chart_df,
                x="Technician",
                y="Changed Count %",
                text=chart_df["Changed Count %"].map(lambda v: f"{v:.1f}%"),
                hover_data=["Verified Checks", "Counts Changed", "active_days"],
                title="Inventory Counts Changed During Delivery Run",
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(yaxis_ticksuffix="%", yaxis_title="Changed Count %", xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)

            summary_display = summary.rename(
                columns={
                    "user_name": "Technician",
                    "verified_checks": "Verified Checks",
                    "changed_counts": "Counts Changed",
                    "changed_count_pct": "Changed Count %",
                    "active_days": "Active Days",
                    "first_check": "First Check",
                    "last_check": "Last Check",
                    "devices": "Devices",
                }
            )
            st.caption("Click a technician row to inspect the exact inventory checks behind their percentage.")
            summary_event = st.dataframe(
                summary_display,
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "Changed Count %": st.column_config.NumberColumn("Changed Count %", format="%.1f%%"),
                    "First Check": st.column_config.DatetimeColumn("First Check", format="MM/DD/YY HH:mm"),
                    "Last Check": st.column_config.DatetimeColumn("Last Check", format="MM/DD/YY HH:mm"),
                },
            )

            st.divider()
            selected_tech = None
            if len(summary_event.selection.rows) > 0:
                selected_tech = summary_display.iloc[summary_event.selection.rows[0]]["Technician"]

            if selected_tech:
                st.subheader(f"Inventory Check Details: {selected_tech}")
                detail_view = accuracy_view[accuracy_view["user_name"].eq(selected_tech)].copy()
            else:
                st.subheader("Inventory Check Details")
                detail_view = accuracy_view.copy()

            detail_view = detail_view.sort_values(["user_name", "dt"]).copy()
            detail_view["Count Changed"] = detail_view["count_changed"].map({True: "Yes", False: "No"})
            detail_display = detail_view[
                [
                    "dt",
                    "user_name",
                    "device",
                    "event_type",
                    "med_desc",
                    "beginning_qty",
                    "ending_qty",
                    "discrepancy_qty",
                    "change_amount",
                    "Count Changed",
                ]
            ].rename(
                columns={
                    "dt": "Time",
                    "user_name": "Technician",
                    "device": "Device",
                    "event_type": "Event Type",
                    "med_desc": "Medication",
                    "beginning_qty": "Beginning Qty",
                    "ending_qty": "Ending Qty",
                    "discrepancy_qty": "Discrepancy Qty",
                    "change_amount": "Change Amount",
                }
            )
            st.caption("Click an inventory check row to see earlier verify-inventory checks for that same device + medication.")
            detail_event = st.dataframe(
                detail_display,
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "Time": st.column_config.DatetimeColumn("Time", format="MM/DD/YY HH:mm:ss"),
                },
            )

            if len(detail_event.selection.rows) > 0:
                selected_detail = detail_view.iloc[detail_event.selection.rows[0]]
                selected_device = selected_detail["device"]
                selected_med_id = selected_detail["med_id"]
                selected_dt = selected_detail["dt"]
                selected_med = selected_detail["med_desc"]
                selected_pk = selected_detail["pk"]

                st.divider()
                st.subheader("Verify Inventory Paper Trail")
                st.caption(
                    "This focuses on prior verify-inventory/count checks for the same device + medication. Routine refills, "
                    "empty return bin events, and other non-count transactions are excluded."
                )

                pocket_locations = load_current_pocket_locations(selected_device, selected_med_id)
                h1, h2, h3 = st.columns(3)
                h1.metric("Device", str(selected_device))
                h2.metric("Med ID", str(selected_med_id))
                h3.metric("Selected Time", pd.to_datetime(selected_dt).strftime("%m/%d/%y %H:%M"))
                st.write(f"**Medication:** {selected_med}")

                if not pocket_locations.empty:
                    st.dataframe(
                        pocket_locations.rename(
                            columns={
                                "source_name": "Source",
                                "device": "Device",
                                "pocket_location": "Current Pocket",
                                "current_count": "Current Count",
                            }
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.info("No current pocket location was found for this device + medication in the inventory detail tables.")

                history = load_same_med_device_history(selected_device, selected_med_id, selected_dt)
                if history.empty:
                    st.info("No previous verify-inventory checks were found for this same device + medication.")
                else:
                    prior_history = history[history["dt"].lt(selected_dt)].copy()
                    user_summary = (
                        prior_history.groupby("user_name")
                        .agg(
                            prior_checks=("pk", "count"),
                            changed_counts=("count_changed", "sum"),
                            first_seen=("dt", "min"),
                            last_seen=("dt", "max"),
                        )
                        .reset_index()
                        if not prior_history.empty
                        else pd.DataFrame(columns=["user_name", "prior_checks", "changed_counts", "first_seen", "last_seen"])
                    )
                    if not user_summary.empty:
                        user_summary["changed_counts"] = user_summary["changed_counts"].astype(int)
                        user_summary = user_summary.sort_values("last_seen", ascending=False)
                    st.dataframe(
                        user_summary.rename(
                            columns={
                                "user_name": "Previous User",
                                "prior_checks": "Prior Checks",
                                "changed_counts": "Changed Counts",
                                "first_seen": "First Seen",
                                "last_seen": "Last Seen",
                            }
                        ),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "First Seen": st.column_config.DatetimeColumn("First Seen", format="MM/DD/YY HH:mm"),
                            "Last Seen": st.column_config.DatetimeColumn("Last Seen", format="MM/DD/YY HH:mm"),
                        },
                    )

                    history_display = history.copy()
                    history_display["Count Changed"] = history_display["count_changed"].map({True: "Yes", False: "No"})
                    history_display["Selected Row"] = history_display["pk"].eq(selected_pk).map({True: "Selected", False: ""})
                    st.dataframe(
                        history_display[
                            [
                                "Selected Row",
                                "dt",
                                "user_name",
                                "event_type",
                                "beginning_qty",
                                "ending_qty",
                                "discrepancy_qty",
                                "change_amount",
                                "Count Changed",
                                "discrepancy_reason",
                            ]
                        ].rename(
                            columns={
                                "dt": "Time",
                                "user_name": "User",
                                "event_type": "Event Type",
                                "beginning_qty": "Beginning Qty",
                                "ending_qty": "Ending Qty",
                                "discrepancy_qty": "Discrepancy Qty",
                                "change_amount": "Change Amount",
                                "discrepancy_reason": "Reason",
                            }
                        ),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Time": st.column_config.DatetimeColumn("Time", format="MM/DD/YY HH:mm:ss"),
                        },
                    )


@st.cache_data(ttl=300)
def load_shift_schedule(sel_date):
    """Load staff_schedule rows for a given date."""
    try:
        sql = text("""
            SELECT staff_name, shift_type, assignment_type, note
            FROM staff_schedule
            WHERE dt::date = :d
            ORDER BY shift_type, staff_name
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": str(sel_date)})
        df["staff_name"] = df["staff_name"].fillna("").astype(str).str.strip()
        df["shift_type"] = df["shift_type"].fillna("").astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"[load_shift_schedule] {e}")
        return pd.DataFrame(columns=["staff_name", "shift_type", "assignment_type", "note"])


@st.cache_data(ttl=300)
def load_day_events(sel_date):
    """Load all Pyxis events for a given date."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type, qty
            FROM events
            WHERE dt::date = :d
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": str(sel_date)})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["device"] = df["device"].fillna("Unknown").astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"[load_day_events] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_day_pharmacy(sel_date):
    """Load all pharmacy orders for a given date."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, destination, priority, med_desc, qty
            FROM pharmacy_orders
            WHERE dt::date = :d
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"d": str(sel_date)})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["destination"] = df["destination"].fillna("Unknown").astype(str).str.strip()
        df["priority"] = df["priority"].fillna("Unknown").astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"[load_day_pharmacy] {e}")
        return pd.DataFrame()


def build_unified_day_sessions(df_events_day, df_pharm_day):
    px_df = df_events_day[["pk", "dt", "user_name", "device", "event_type", "med_desc", "qty", "match_key"]].copy() if not df_events_day.empty else pd.DataFrame()
    if not px_df.empty:
        px_df["source"] = "Pyxis"

    ph_df = df_pharm_day[["pk", "dt", "user_name", "destination", "priority", "med_desc", "qty"]].copy() if not df_pharm_day.empty else pd.DataFrame()
    if not ph_df.empty:
        ph_df = ph_df.rename(columns={"destination": "device", "priority": "event_type"})
        ph_df["source"] = "Pharmacy"
        ph_df["match_key"] = ph_df["user_name"].apply(normalize_name)

    day_combined = pd.concat([px_df, ph_df], ignore_index=True)
    if day_combined.empty:
        return pd.DataFrame(), pd.DataFrame()

    day_combined["dt"] = pd.to_datetime(day_combined["dt"], errors="coerce")
    day_combined = day_combined.dropna(subset=["dt"]).sort_values(["match_key", "dt"]).reset_index(drop=True)

    day_combined["prev_match_key"] = day_combined["match_key"].shift()
    day_combined["prev_device"] = day_combined["device"].shift()
    day_combined["prev_dt"] = day_combined["dt"].shift()
    day_combined["gap"] = (day_combined["dt"] - day_combined["prev_dt"]).dt.total_seconds().fillna(0)
    day_combined["is_new_session"] = np.where(
        (day_combined["match_key"] != day_combined["prev_match_key"]) |
        (day_combined["device"] != day_combined["prev_device"]) |
        (day_combined["gap"] > 1200),
        1, 0
    )
    day_combined["session_id"] = day_combined["is_new_session"].cumsum()

    grouped = (
        day_combined.groupby("session_id")
        .agg(
            tech_key=("match_key", "first"),
            user_name=("user_name", "first"),
            device=("device", "first"),
            source=("source", "first"),
            primary_event=("event_type", "first"),
            primary_med=("med_desc", "first"),
            start=("dt", "min"),
            end=("dt", "max"),
            tx_count=("pk", "count"),
        )
        .reset_index()
    )
    grouped["duration_sec"] = (grouped["end"] - grouped["start"]).dt.total_seconds()
    grouped["duration_sec"] = np.where(grouped["duration_sec"] < 10, 30, grouped["duration_sec"])
    grouped = grouped.sort_values(["tech_key", "start"]).reset_index(drop=True)
    grouped["next_start"] = grouped.groupby("tech_key")["start"].shift(-1)
    grouped["walk_sec"] = (grouped["next_start"] - grouped["end"]).dt.total_seconds()
    grouped["walk_sec"] = grouped["walk_sec"].where(grouped["walk_sec"].gt(0), 0).fillna(0)
    return day_combined, grouped


def classify_work_type(source, device, event_type):
    src = str(source or "").lower()
    dev = str(device or "").lower()
    evt = str(event_type or "").lower()

    if src == "pharmacy":
        return "Carousel / 0400 Pull"
    if re.search(r"outdate", evt):
        return "Pyxis Outdates"
    if re.search(r"carousel|cubic|pack|central", dev):
        return "Returns / Carousel Putaway"
    return "Pyxis Maintenance"


with tab2:
    st.subheader("🕐 Follow the Shift")
    st.caption(
        "Select a date and shift to see every Pyxis transaction those techs performed, in chronological order."
    )

    # ── Date picker ──────────────────────────────────────────────────────────
    shift_date = st.date_input(
        "Date",
        value=end_date,
        key="shift_timeline_date"
    )

    with st.spinner("Loading schedule and events..."):
        df_sched_day  = load_shift_schedule(shift_date)
        df_events_day = load_day_events(shift_date)

    if df_sched_day.empty:
        st.warning("No schedule found for this date. Make sure schedule data has been uploaded.")
        st.stop()

    # ── Normalize names ───────────────────────────────────────────────────────
    df_sched_day["match_key"] = df_sched_day["staff_name"].apply(normalize_name)

    if not df_events_day.empty:
        df_events_day["match_key"] = df_events_day["user_name"].apply(normalize_name)

    # ── Shift selector ────────────────────────────────────────────────────────
    available_shifts = sorted(df_sched_day["shift_type"].dropna().replace("", pd.NA).dropna().unique())

    if not available_shifts:
        st.warning("No shift types found for this date.")
        st.stop()

    col_sh, col_nm = st.columns([1, 2])

    with col_sh:
        sel_shifts = st.multiselect(
            "Shift(s)",
            options=available_shifts,
            default=available_shifts[:1] if available_shifts else [],
            key="shift_timeline_shifts"
        )

    # ── Staff on selected shifts ──────────────────────────────────────────────
    if not sel_shifts:
        st.info("Select at least one shift to continue.")
        st.stop()

    sched_filtered = df_sched_day[df_sched_day["shift_type"].isin(sel_shifts)].copy()

    # Build display-name → match_key mapping for the name picker
    name_options = (
        sched_filtered[["staff_name", "match_key"]]
        .drop_duplicates("staff_name")
        .sort_values("staff_name")
    )

    with col_nm:
        sel_names = st.multiselect(
            "Staff (leave blank for all on shift)",
            options=name_options["staff_name"].tolist(),
            key="shift_timeline_names"
        )

    # If user picked specific names, narrow the match_keys
    if sel_names:
        active_keys = set(
            name_options[name_options["staff_name"].isin(sel_names)]["match_key"]
        )
    else:
        active_keys = set(sched_filtered["match_key"].unique())

    # ── KPI row: who is on shift ──────────────────────────────────────────────
    st.divider()

    shift_roster = sched_filtered[["staff_name", "shift_type", "assignment_type", "note"]].drop_duplicates()
    if sel_names:
        shift_roster = shift_roster[shift_roster["staff_name"].isin(sel_names)]

    k1, k2, k3 = st.columns(3)
    k1.metric("Staff on Shift", len(shift_roster))
    k2.metric("Shift(s) Selected", ", ".join(sel_shifts))
    k3.metric("Date", shift_date.strftime("%A, %b %d %Y"))

    with st.expander("📋 Shift Roster", expanded=False):
        st.dataframe(
            shift_roster.rename(columns={
                "staff_name":       "Name",
                "shift_type":       "Shift",
                "assignment_type":  "Assignment",
                "note":             "Note",
            }),
            use_container_width=True,
            hide_index=True,
        )

    st.divider()

    # ── Filter events to scheduled staff ─────────────────────────────────────
    if df_events_day.empty:
        st.info("No Pyxis events found for this date.")
        st.stop()

    timeline = df_events_day[df_events_day["match_key"].isin(active_keys)].copy()

    if timeline.empty:
        st.warning(
            "No Pyxis events found for the selected shift staff on this date. "
            "Name matching may not have linked schedule names to Pyxis usernames."
        )

        # Debug helper — show who we tried to match
        with st.expander("🛠️ Name Match Debug", expanded=True):
            st.write("**Match keys searched:**", sorted(active_keys))
            st.write("**Match keys found in events:**",
                     sorted(df_events_day["match_key"].unique().tolist()))
        st.stop()

    timeline = timeline.sort_values("dt").reset_index(drop=True)

    # Attach display name (first match_key → staff_name mapping)
    key_to_name = (
        sched_filtered[["match_key", "staff_name"]]
        .drop_duplicates("match_key")
        .set_index("match_key")["staff_name"]
        .to_dict()
    )
    timeline["tech_display"] = timeline["match_key"].map(key_to_name).fillna(timeline["user_name"])

    # ── Summary metrics ───────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Transactions", f"{len(timeline):,}")
    m2.metric("Techs Active",       timeline["match_key"].nunique())
    m3.metric("Devices Touched",    timeline["device"].nunique())
    m4.metric("Time Span",
        f"{timeline['dt'].min().strftime('%H:%M')} – {timeline['dt'].max().strftime('%H:%M')}"
        if not timeline.empty else "—"
    )

    # ── Timeline chart (events per tech over time) ────────────────────────────
    if timeline["dt"].notna().sum() > 0:
        chart_df = timeline.copy()
        chart_df["hour"] = chart_df["dt"].dt.floor("30min")
        heat = (
            chart_df.groupby(["tech_display", "hour"])
            .size()
            .reset_index(name="tx_count")
        )

        fig = px.bar(
            heat,
            x="hour", y="tx_count",
            color="tech_display",
            labels={"hour": "", "tx_count": "Transactions", "tech_display": "Tech"},
            title="Transaction Activity by Technician (30-min buckets)",
            barmode="stack",
        )
        fig.update_layout(
            height=320,
            xaxis=dict(tickformat="%H:%M", tickangle=-30),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=0, r=0, t=50, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── Chronological event table ─────────────────────────────────────────────
    st.subheader("📋 Chronological Event Log")

    # Optional: filter down to a single tech
    all_tech_displays = sorted(timeline["tech_display"].unique())
    drill_tech = st.selectbox(
        "Focus on one technician (optional)",
        options=["— All —"] + all_tech_displays,
        key="shift_timeline_drill"
    )

    log_view = timeline if drill_tech == "— All —" else timeline[timeline["tech_display"] == drill_tech]

    st.dataframe(
        log_view[["dt", "tech_display", "device", "event_type", "med_desc", "qty"]].rename(columns={
            "dt":           "Time",
            "tech_display": "Technician",
            "device":       "Device",
            "event_type":   "Event Type",
            "med_desc":     "Medication",
            "qty":          "Qty",
        }),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Time": st.column_config.DatetimeColumn("Time", format="HH:mm:ss"),
            "Qty":  st.column_config.NumberColumn("Qty", format="%.0f"),
        }
    )

    st.caption(f"{len(log_view):,} events shown.")


with tab3:
    st.subheader("🧭 Shift Work Map")
    st.caption(
        "Map scheduled shift coverage to the main daily phases you described: carousel / 0400 pull first, Pyxis machine work in the middle, then returns back into the carousel."
    )

    saved_profiles = load_shift_audit_profiles()

    work_date = st.date_input(
        "Work Map Date",
        value=end_date,
        key="shift_work_map_date"
    )

    with st.spinner("Loading shift work map..."):
        df_sched_work = load_shift_schedule(work_date)
        df_events_work = load_day_events(work_date)
        df_pharm_work = load_day_pharmacy(work_date)

    if df_sched_work.empty:
        st.warning("No schedule found for this date. Upload schedule data first.")
        st.stop()

    df_sched_work["match_key"] = df_sched_work["staff_name"].apply(normalize_name)
    if not df_events_work.empty:
        df_events_work["match_key"] = df_events_work["user_name"].apply(normalize_name)

    work_shifts = sorted(df_sched_work["shift_type"].dropna().replace("", pd.NA).dropna().unique())
    if not work_shifts:
        st.warning("No shift types found for this date.")
        st.stop()

    defaults = {"shifts": work_shifts[:1] if work_shifts else [], "selected_names": [], "view_scope": "Whole Shift Team"}
    if not saved_profiles.empty:
        profile_lookup = saved_profiles.set_index("profile_name").to_dict("index")
        selected_profile = st.selectbox(
            "Saved Audit Profile",
            options=["Manual"] + saved_profiles["profile_name"].tolist(),
            key="shift_work_map_profile",
        )
        if selected_profile != "Manual":
            profile = profile_lookup[selected_profile]
            defaults["shifts"] = [s for s in profile.get("shifts", []) if s in work_shifts] or defaults["shifts"]
            defaults["selected_names"] = profile.get("selected_names", [])
            defaults["view_scope"] = profile.get("view_scope") or "Whole Shift Team"
            if st.session_state.get("_shift_work_map_profile_applied") != selected_profile:
                st.session_state["shift_work_map_shifts"] = defaults["shifts"]
                st.session_state["shift_work_map_names"] = defaults["selected_names"]
                st.session_state["shift_work_map_scope"] = defaults["view_scope"]
                st.session_state["_shift_work_map_profile_applied"] = selected_profile
        else:
            st.session_state["_shift_work_map_profile_applied"] = "Manual"

    wc1, wc2 = st.columns([1, 2])

    with wc1:
        selected_work_shifts = st.multiselect(
            "Shift(s)",
            options=work_shifts,
            default=defaults["shifts"],
            key="shift_work_map_shifts",
        )

    if not selected_work_shifts:
        st.info("Select at least one shift to continue.")
        st.stop()

    work_sched_filtered = df_sched_work[df_sched_work["shift_type"].isin(selected_work_shifts)].copy()
    work_name_options = (
        work_sched_filtered[["staff_name", "match_key"]]
        .drop_duplicates("staff_name")
        .sort_values("staff_name")
    )

    with wc2:
        selected_work_names = st.multiselect(
            "Staff (leave blank for all on shift)",
            options=work_name_options["staff_name"].tolist(),
            default=[n for n in defaults["selected_names"] if n in work_name_options["staff_name"].tolist()],
            key="shift_work_map_names",
        )

    view_scope = st.radio(
        "View Scope",
        options=["Whole Shift Team", "Selected Staff Only"],
        horizontal=True,
        index=0 if defaults["view_scope"] == "Whole Shift Team" else 1,
        key="shift_work_map_scope",
    )

    save_col1, save_col2 = st.columns([2, 1])
    with save_col1:
        profile_name = st.text_input(
            "Save Current Audit As",
            placeholder="Example: 0500 Hardline Audit",
            key="shift_work_map_profile_name",
        )
    with save_col2:
        st.write("")
        st.write("")
        if st.button("Save Audit Profile", key="save_shift_work_profile"):
            if not profile_name.strip():
                st.warning("Enter a profile name before saving.")
            elif save_shift_audit_profile(
                profile_name,
                selected_work_shifts,
                selected_work_names,
                view_scope,
            ):
                st.success(f"Saved audit profile: {profile_name.strip()}")

    if selected_work_names and view_scope == "Selected Staff Only":
        active_work_keys = set(
            work_name_options[work_name_options["staff_name"].isin(selected_work_names)]["match_key"]
        )
    else:
        active_work_keys = set(work_sched_filtered["match_key"].unique())

    day_stream, day_sessions = build_unified_day_sessions(df_events_work, df_pharm_work)
    if day_stream.empty or day_sessions.empty:
        st.warning("No Pyxis or pharmacy events were found for this date.")
        st.stop()

    active_stream = day_stream[day_stream["match_key"].isin(active_work_keys)].copy()
    active_sessions = day_sessions[day_sessions["tech_key"].isin(active_work_keys)].copy()
    if active_sessions.empty:
        st.warning("No work sessions matched the selected shift staff on this date.")
        st.stop()

    work_key_to_name = (
        work_sched_filtered[["match_key", "staff_name", "assignment_type"]]
        .drop_duplicates("match_key")
        .set_index("match_key")
    )
    key_to_name = work_key_to_name["staff_name"].to_dict()
    key_to_assignment = work_key_to_name["assignment_type"].fillna("").astype(str).to_dict()
    active_sessions["assignment_type"] = active_sessions["tech_key"].map(key_to_assignment).fillna("")
    active_sessions["tech_display"] = active_sessions["tech_key"].map(key_to_name).fillna(active_sessions["user_name"])
    active_sessions["tech_display"] = np.where(
        active_sessions["assignment_type"].str.lower().eq("training"),
        active_sessions["tech_display"] + " (Training)",
        active_sessions["tech_display"],
    )
    active_sessions["work_type"] = active_sessions.apply(
        lambda row: classify_work_type(row["source"], row["device"], row["primary_event"]),
        axis=1
    )
    active_sessions["work_type"] = pd.Categorical(
        active_sessions["work_type"],
        categories=WORK_TYPE_ORDER,
        ordered=True,
    )
    active_sessions["long_gap_flag"] = active_sessions["walk_sec"] > 1200

    training_count = int(work_sched_filtered["assignment_type"].fillna("").astype(str).str.lower().eq("training").sum())
    summary = summarize_shift_audit(active_sessions, active_work_keys, training_count)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Staff on Shift", summary["staff_on_shift"])
    m2.metric("Sessions", f"{summary['sessions']:,}")
    m3.metric("Active Work Time", seconds_to_mmss(summary["active_sec"]))
    m4.metric("Walk Time", seconds_to_mmss(summary["walk_sec"]))
    m5.metric("Training Staff", summary["training_count"])

    st.caption(
        "Use `Whole Shift Team` to keep the regular tech and any trainee together in one shift view. "
        "Switch to `Selected Staff Only` when you want to isolate one person."
    )

    if not saved_profiles.empty:
        audit_rows = []
        for profile in saved_profiles.itertuples(index=False):
            profile_shifts = [s for s in profile.shifts if s in work_shifts]
            if not profile_shifts:
                continue
            profile_sched = df_sched_work[df_sched_work["shift_type"].isin(profile_shifts)].copy()
            if profile.view_scope == "Selected Staff Only" and profile.selected_names:
                profile_keys = set(
                    profile_sched[profile_sched["staff_name"].isin(profile.selected_names)]["match_key"].unique()
                )
            else:
                profile_keys = set(profile_sched["match_key"].unique())
            profile_sessions = day_sessions[day_sessions["tech_key"].isin(profile_keys)].copy()
            if profile_sessions.empty:
                continue
            profile_sessions["work_type"] = profile_sessions.apply(
                lambda row: classify_work_type(row["source"], row["device"], row["primary_event"]),
                axis=1
            )
            profile_sessions["long_gap_flag"] = profile_sessions["walk_sec"] > 1200
            profile_training_count = int(
                profile_sched["assignment_type"].fillna("").astype(str).str.lower().eq("training").sum()
            )
            profile_summary = summarize_shift_audit(profile_sessions, profile_keys, profile_training_count)
            audit_rows.append(
                {
                    "Audit Profile": profile.profile_name,
                    "Shift(s)": ", ".join(profile_shifts),
                    "Scope": profile.view_scope,
                    "Staff on Shift": profile_summary["staff_on_shift"],
                    "Sessions": profile_summary["sessions"],
                    "Active Time": seconds_to_mmss(profile_summary["active_sec"]),
                    "Walk Time": seconds_to_mmss(profile_summary["walk_sec"]),
                    "Long Gaps >20m": profile_summary["long_gap_count"],
                    "Training Staff": profile_summary["training_count"],
                }
            )

        if audit_rows:
            st.divider()
            st.subheader("Saved Audit Runs for This Date")
            st.caption("These hardlined audit profiles run automatically against the selected date using the saved shift rules.")
            st.dataframe(pd.DataFrame(audit_rows), use_container_width=True, hide_index=True)

    roster = work_sched_filtered[["staff_name", "shift_type", "assignment_type", "note"]].drop_duplicates()
    if selected_work_names:
        roster = roster[roster["staff_name"].isin(selected_work_names)]

    with st.expander("📋 Shift Roster", expanded=False):
        st.dataframe(
            roster.rename(columns={
                "staff_name": "Name",
                "shift_type": "Shift",
                "assignment_type": "Assignment",
                "note": "Note",
            }),
            use_container_width=True,
            hide_index=True,
        )

    tech_summary = (
        active_sessions.groupby("tech_display", as_index=False)
        .agg(
            sessions=("session_id", "count"),
            active_sec=("duration_sec", "sum"),
            walk_sec=("walk_sec", "sum"),
            devices=("device", "nunique"),
        )
        .sort_values("active_sec", ascending=False)
    )
    dominant_work = (
        active_sessions.groupby(["tech_display", "work_type"])
        .agg(active_sec=("duration_sec", "sum"))
        .reset_index()
        .sort_values(["tech_display", "active_sec", "work_type"], ascending=[True, False, True])
        .drop_duplicates("tech_display")
        .rename(columns={"work_type": "dominant_work_type"})
    )
    tech_summary = tech_summary.merge(dominant_work[["tech_display", "dominant_work_type"]], on="tech_display", how="left")
    tech_summary["active_time"] = tech_summary["active_sec"].apply(seconds_to_mmss)
    tech_summary["walk_time"] = tech_summary["walk_sec"].apply(seconds_to_mmss)

    work_type_summary = (
        active_sessions.groupby("work_type", as_index=False)
        .agg(
            sessions=("session_id", "count"),
            active_sec=("duration_sec", "sum"),
            walk_sec=("walk_sec", "sum"),
            techs=("tech_display", "nunique"),
        )
        .sort_values("work_type")
    )
    work_type_summary["active_time"] = work_type_summary["active_sec"].apply(seconds_to_mmss)
    work_type_summary["walk_time"] = work_type_summary["walk_sec"].apply(seconds_to_mmss)

    st.divider()
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Time by Technician")
        st.dataframe(
            tech_summary[["tech_display", "sessions", "devices", "active_time", "walk_time", "dominant_work_type"]].rename(columns={
                "tech_display": "Technician",
                "sessions": "Sessions",
                "devices": "Devices",
                "active_time": "Active Time",
                "walk_time": "Walk Time",
                "dominant_work_type": "Dominant Work Type",
            }),
            use_container_width=True,
            hide_index=True,
        )

    with c2:
        st.subheader("Time by Work Type")
        work_type_event = st.dataframe(
            work_type_summary[["work_type", "sessions", "techs", "active_time", "walk_time"]].rename(columns={
                "work_type": "Work Type",
                "sessions": "Sessions",
                "techs": "Techs",
                "active_time": "Active Time",
                "walk_time": "Walk Time",
            }),
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

    chart1, chart2 = st.columns(2)
    with chart1:
        fig_tech = px.bar(
            tech_summary,
            x="tech_display",
            y=["active_sec", "walk_sec"],
            barmode="stack",
            labels={"tech_display": "", "value": "Seconds", "variable": "Time Type"},
        )
        fig_tech.update_layout(height=360, xaxis_tickangle=-25)
        st.plotly_chart(fig_tech, use_container_width=True)

    with chart2:
        fig_work = px.bar(
            work_type_summary.sort_values("active_sec"),
            x="active_sec",
            y="work_type",
            orientation="h",
            labels={"active_sec": "Active Seconds", "work_type": ""},
            color="active_sec",
            color_continuous_scale="Blues",
        )
        fig_work.update_layout(height=360, coloraxis_showscale=False)
        st.plotly_chart(fig_work, use_container_width=True)

    st.divider()
    st.subheader("Session Classification Detail")
    active_sessions["active_time"] = active_sessions["duration_sec"].apply(seconds_to_mmss)
    active_sessions["walk_time"] = active_sessions["walk_sec"].apply(seconds_to_mmss)
    st.dataframe(
        active_sessions[[
            "tech_display", "source", "device", "primary_event", "primary_med",
            "start", "end", "tx_count", "active_time", "walk_time", "work_type"
        ]].rename(columns={
            "tech_display": "Technician",
            "source": "Source",
            "device": "Device",
            "primary_event": "Primary Event",
            "primary_med": "Primary Med",
            "start": "Start",
            "end": "End",
            "tx_count": "Tx Count",
            "active_time": "Active Time",
            "walk_time": "Walk Time",
            "work_type": "Work Type",
        }),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Start": st.column_config.DatetimeColumn("Start", format="HH:mm:ss"),
            "End": st.column_config.DatetimeColumn("End", format="HH:mm:ss"),
        }
    )

    if len(work_type_event.selection.rows) > 0:
        selected_idx = work_type_event.selection.rows[0]
        selected_work_type = work_type_summary.iloc[selected_idx]["work_type"]
        session_ids = active_sessions.loc[active_sessions["work_type"] == selected_work_type, "session_id"].tolist()
        tx_view = active_stream[active_stream["session_id"].isin(session_ids)].copy().sort_values("dt")
        tx_view["tech_display"] = tx_view["match_key"].map(key_to_name).fillna(tx_view["user_name"])
        tx_view["tech_display"] = np.where(
            tx_view["match_key"].map(key_to_assignment).fillna("").astype(str).str.lower().eq("training"),
            tx_view["tech_display"] + " (Training)",
            tx_view["tech_display"],
        )

        st.divider()
        st.subheader(f"🔎 Transaction Drilldown: {selected_work_type}")
        st.caption("Clicking a work type above filters to the exact raw transactions that created those sessions.")
        st.dataframe(
            tx_view[["dt", "tech_display", "source", "device", "event_type", "med_desc", "qty"]].rename(columns={
                "dt": "Time",
                "tech_display": "Technician",
                "source": "Source",
                "device": "Device",
                "event_type": "Event Type",
                "med_desc": "Medication",
                "qty": "Qty",
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Time": st.column_config.DatetimeColumn("Time", format="HH:mm:ss"),
            }
        )

