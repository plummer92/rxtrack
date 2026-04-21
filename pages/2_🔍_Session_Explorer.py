import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import re
from sqlalchemy import text
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(page_title="Session Explorer", page_icon="🔍", layout="wide")

load_data = App.load_data
seconds_to_mmss = App.seconds_to_mmss
render_sidebar = App.render_sidebar
normalize_name = App.normalize_name
engine = App.engine

start_date, end_date = render_sidebar()
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
    'event_type': 'first',
    'med_desc': 'first',
    'dt': ['min', 'max'],
    'pk': 'count'
}).reset_index()

sessions.columns = ['session_id', 'User', 'Device', 'Source', 'Primary Event', 'Primary Med', 'Start', 'End', 'Tx Count']

sessions['Duration'] = (sessions['End'] - sessions['Start']).dt.total_seconds()
sessions['Duration'] = np.where(sessions['Duration'] < 10, 30, sessions['Duration'])

sessions = sessions.sort_values(['User', 'Start'])
sessions['Next Start'] = sessions.groupby('User')['Start'].shift(-1)
sessions['Walk Time'] = (sessions['Next Start'] - sessions['End']).dt.total_seconds()

# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════

tab1, tab2, tab3 = st.tabs(["🔍 Session View", "🕐 Shift Timeline", "🧭 Shift Work Map"])

WORK_TYPE_ORDER = [
    "Carousel / 0400 Pull",
    "Pyxis Delivery",
    "Returns / Carousel Putaway",
    "Other Cabinet Work",
]

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — SESSION VIEW (existing)
# ─────────────────────────────────────────────────────────────────────────────

with tab1:
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

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — SHIFT TIMELINE
# ─────────────────────────────────────────────────────────────────────────────

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
    if re.search(r"stockout|return|outdate|unload", evt):
        return "Returns / Carousel Putaway"
    if re.search(r"refill|restock|load|replenish", evt):
        return "Pyxis Delivery"
    if re.search(r"cycle count|blind count|physical count", evt):
        return "Other Cabinet Work"
    if re.search(r"carousel|cubic|pack|central", dev):
        return "Returns / Carousel Putaway"
    return "Other Cabinet Work"


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
        "Map scheduled shift coverage to the three biggest daily phases you described: carousel / 0400 pull first, Pyxis delivery second, then returns back into the carousel."
    )

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

    wc1, wc2 = st.columns([1, 2])

    with wc1:
        selected_work_shifts = st.multiselect(
            "Shift(s)",
            options=work_shifts,
            default=work_shifts[:1] if work_shifts else [],
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
            key="shift_work_map_names",
        )

    if selected_work_names:
        active_work_keys = set(
            work_name_options[work_name_options["staff_name"].isin(selected_work_names)]["match_key"]
        )
    else:
        active_work_keys = set(work_sched_filtered["match_key"].unique())

    day_stream, day_sessions = build_unified_day_sessions(df_events_work, df_pharm_work)
    if day_stream.empty or day_sessions.empty:
        st.warning("No Pyxis or pharmacy events were found for this date.")
        st.stop()

    active_sessions = day_sessions[day_sessions["tech_key"].isin(active_work_keys)].copy()
    if active_sessions.empty:
        st.warning("No work sessions matched the selected shift staff on this date.")
        st.stop()

    work_key_to_name = (
        work_sched_filtered[["match_key", "staff_name"]]
        .drop_duplicates("match_key")
        .set_index("match_key")["staff_name"]
        .to_dict()
    )
    active_sessions["tech_display"] = active_sessions["tech_key"].map(work_key_to_name).fillna(active_sessions["user_name"])
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

    total_active_sec = active_sessions["duration_sec"].sum()
    total_walk_sec = active_sessions["walk_sec"].sum()
    long_gap_count = int(active_sessions["long_gap_flag"].sum())

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Staff on Shift", len(active_work_keys))
    m2.metric("Sessions", f"{len(active_sessions):,}")
    m3.metric("Active Work Time", seconds_to_mmss(total_active_sec))
    m4.metric("Walk Time", seconds_to_mmss(total_walk_sec))
    m5.metric("Long Gaps >20m", long_gap_count)

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
        st.dataframe(
            work_type_summary[["work_type", "sessions", "techs", "active_time", "walk_time"]].rename(columns={
                "work_type": "Work Type",
                "sessions": "Sessions",
                "techs": "Techs",
                "active_time": "Active Time",
                "walk_time": "Walk Time",
            }),
            use_container_width=True,
            hide_index=True,
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

