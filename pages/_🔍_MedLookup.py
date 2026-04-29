import streamlit as st
import pandas as pd
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(page_title="Med Audit Trail", page_icon="🔍", layout="wide")
if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.render_sidebar()

engine = App.engine
normalize_name = App.normalize_name


@st.cache_data(ttl=300)
def get_matching_pyxis_events(med_desc, med_id, device, center_dt, hours_window):
    if pd.isna(center_dt):
        return pd.DataFrame()

    start_dt = pd.to_datetime(center_dt) - pd.Timedelta(hours=hours_window)
    end_dt = pd.to_datetime(center_dt) + pd.Timedelta(hours=hours_window)
    med_id = None if pd.isna(med_id) else med_id
    device = None if pd.isna(device) else device

    if device:
        query = """
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type, qty,
                   beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason
            FROM events
            WHERE dt::timestamp BETWEEN %s AND %s
              AND device = %s
              AND (med_desc = %s OR (%s IS NOT NULL AND med_id = %s))
            ORDER BY dt DESC
        """
        params = (start_dt, end_dt, device, med_desc, med_id, med_id)
    else:
        query = """
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type, qty,
                   beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason
            FROM events
            WHERE dt::timestamp BETWEEN %s AND %s
              AND (med_desc = %s OR (%s IS NOT NULL AND med_id = %s))
            ORDER BY dt DESC
        """
        params = (start_dt, end_dt, med_desc, med_id, med_id)

    return pd.read_sql(query, engine, params=params)

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Advanced Medication Audit",
        "Deep-dive into specific medication history with technician and device filtering while keeping the new RxTrack page shell.",
        kicker="Tools",
    )
    _debug_event("Med Lookup", "shared_intro_loaded")
    _debug_panel("Med Lookup", intro_mode="shared")
else:
    st.header("🔍 Advanced Medication Audit")
    st.caption("Deep-dive into specific medication history with technician and device filtering.")
    _debug_event("Med Lookup", "fallback_header_used")
    _debug_panel("Med Lookup", intro_mode="fallback")

# -------------------------------------------------
# 1️⃣ Load Medication List (Cached)
# -------------------------------------------------
@st.cache_data(ttl=300)
def get_med_list():
    query = """
        SELECT DISTINCT med_desc FROM events WHERE med_desc IS NOT NULL
        UNION
        SELECT DISTINCT med_desc FROM pharmacy_orders WHERE med_desc IS NOT NULL
        ORDER BY med_desc
    """
    df = pd.read_sql(query, engine)
    return sorted(df['med_desc'].dropna().unique())

med_list = get_med_list()

if not med_list:
    st.warning("No medications found in database.")
    st.stop()

# -------------------------------------------------
# 2️⃣ Medication Selector
# -------------------------------------------------
selected_med = st.selectbox(
    "Select Medication:",
    options=med_list,
    index=None,
    placeholder="Search or select a medication..."
)

if not selected_med:
    st.info("Select a medication to begin audit analysis.")
    st.stop()

# -------------------------------------------------
# 3️⃣ Pull Data (Secure Query)
# -------------------------------------------------
pyxis_query = """
    SELECT
        pk,
        dt,
        user_name,
        device,
        med_id,
        med_desc,
        event_type,
        qty,
        beginning_qty,
        ending_qty,
        discrepancy_qty,
        discrepancy_reason,
        resolution_dt,
        'Pyxis Event' AS source,
        NULL::TEXT AS queue_id
    FROM events
    WHERE med_desc = %s
    ORDER BY dt DESC
"""

carousel_query = """
    SELECT
        pk,
        dt,
        user_name,
        destination AS device,
        med_id,
        med_desc,
        priority AS event_type,
        qty,
        NULL::FLOAT AS beginning_qty,
        NULL::FLOAT AS ending_qty,
        NULL::FLOAT AS discrepancy_qty,
        NULL::TEXT AS discrepancy_reason,
        NULL::TIMESTAMP AS resolution_dt,
        'Carousel / Pyxis Pull' AS source,
        queue_id
    FROM pharmacy_orders
    WHERE med_desc = %s
    ORDER BY dt DESC
"""

with st.spinner("Scanning database..."):
    pyxis_df = pd.read_sql(pyxis_query, engine, params=(selected_med,))
    carousel_df = pd.read_sql(carousel_query, engine, params=(selected_med,))
    df_raw = pd.concat([pyxis_df, carousel_df], ignore_index=True)
    if not df_raw.empty:
        df_raw = df_raw.sort_values("dt", ascending=False)

if df_raw.empty:
    st.warning(f"No records found for '{selected_med}'.")
    st.stop()

# -------------------------------------------------
# 4️⃣ Normalize Technician Names
# -------------------------------------------------
df_raw['tech_name'] = df_raw['user_name'].apply(normalize_name)

# -------------------------------------------------
# 5️⃣ Sidebar Filters
# -------------------------------------------------
st.sidebar.header("🎯 Refine Results")

selected_sources = st.sidebar.multiselect(
    "Filter by Source:",
    options=sorted(df_raw['source'].dropna().unique()),
    default=sorted(df_raw['source'].dropna().unique())
)

selected_devices = st.sidebar.multiselect(
    "Filter by Device / Destination:",
    options=sorted(df_raw['device'].dropna().unique()),
    default=sorted(df_raw['device'].dropna().unique())
)

selected_techs = st.sidebar.multiselect(
    "Filter by Technician:",
    options=sorted(df_raw['tech_name'].dropna().unique()),
    default=sorted(df_raw['tech_name'].dropna().unique())
)

selected_events = st.sidebar.multiselect(
    "Filter by Event / Priority:",
    options=sorted(df_raw['event_type'].dropna().unique()),
    default=sorted(df_raw['event_type'].dropna().unique())
)

# -------------------------------------------------
# 6️⃣ Apply Filters
# -------------------------------------------------
df_filtered = df_raw[
    (df_raw['source'].isin(selected_sources)) &
    (df_raw['device'].isin(selected_devices)) &
    (df_raw['tech_name'].isin(selected_techs)) &
    (df_raw['event_type'].isin(selected_events))
].copy()

if df_filtered.empty:
    st.warning("No records match current filters.")
    st.stop()

# -------------------------------------------------
# 7️⃣ Inventory Integrity Logic
# -------------------------------------------------
df_filtered = df_filtered.sort_values(['device', 'med_id', 'dt'], ascending=[True, True, False])

df_filtered['prev_ending'] = pd.NA
df_filtered['count_gap'] = pd.NA
pyxis_mask = df_filtered['source'].eq('Pyxis Event')
pyxis_events = df_filtered.loc[pyxis_mask].copy()

if not pyxis_events.empty:
    pyxis_events['prev_ending'] = (
        pyxis_events.groupby(['device', 'med_id'])['ending_qty']
        .shift(-1)
    )

    pyxis_events['count_gap'] = (
        pyxis_events['beginning_qty'] - pyxis_events['prev_ending']
    )

    df_filtered.loc[pyxis_events.index, 'prev_ending'] = pyxis_events['prev_ending']
    df_filtered.loc[pyxis_events.index, 'count_gap'] = pyxis_events['count_gap']

df_filtered['prev_ending'] = pd.to_numeric(df_filtered['prev_ending'], errors='coerce')
df_filtered['count_gap'] = pd.to_numeric(df_filtered['count_gap'], errors='coerce')
df_filtered = df_filtered.sort_values("dt", ascending=False)

# -------------------------------------------------
# 8️⃣ Metrics Overview
# -------------------------------------------------
c1, c2, c3, c4 = st.columns(4)

c1.metric("Matches Found", len(df_filtered))
c2.metric("Pyxis Events", int(df_filtered['source'].eq('Pyxis Event').sum()))
c3.metric("Carousel / Pull Rows", int(df_filtered['source'].eq('Carousel / Pyxis Pull').sum()))
c4.metric("Detected Pyxis Gaps", int((df_filtered['count_gap'].notna() & (df_filtered['count_gap'] != 0)).sum()))

# -------------------------------------------------
# 9️⃣ Timeline Display
# -------------------------------------------------
st.subheader("📋 Audit Timeline")

timeline_display = df_filtered.reset_index(drop=True)
timeline_cols = ['dt', 'source', 'tech_name', 'device', 'event_type',
                 'qty', 'beginning_qty', 'ending_qty', 'count_gap', 'queue_id']

timeline_event = st.dataframe(
    timeline_display[timeline_cols],
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "dt": st.column_config.DatetimeColumn("Time", format="MM/DD HH:mm:ss"),
        "source": "Source",
        "device": "Device / Destination",
        "event_type": "Event / Priority",
        "qty": st.column_config.NumberColumn("Action Qty", format="%.0f"),
        "count_gap": st.column_config.NumberColumn("Inventory Gap", format="%.0f"),
        "beginning_qty": "Beginning Count",
        "ending_qty": "Ending Count",
        "queue_id": "Queue ID"
    }
)

if len(timeline_event.selection.rows) > 0:
    selected_idx = timeline_event.selection.rows[0]
    selected_row = timeline_display.iloc[selected_idx]

    st.divider()
    st.subheader("Selected Row Pyxis Drilldown")

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Selected Source", selected_row.get("source", ""))
    d2.metric("Device / Destination", selected_row.get("device", ""))
    d3.metric("Action Qty", "" if pd.isna(selected_row.get("qty")) else f"{selected_row.get('qty'):g}")
    d4.metric("Queue ID", selected_row.get("queue_id", "") or "")

    hours_window = st.slider(
        "Pyxis match window around selected row",
        min_value=2,
        max_value=72,
        value=24,
        step=2,
        help="Use a wider window if the carousel pull happened long before the Pyxis load/refill."
    )

    pyxis_detail = get_matching_pyxis_events(
        selected_row.get("med_desc"),
        selected_row.get("med_id"),
        selected_row.get("device"),
        selected_row.get("dt"),
        hours_window,
    )

    if pyxis_detail.empty:
        st.info(
            "No matching Pyxis cabinet events were found for this med/device inside the selected time window. "
            "Try widening the window, or the carousel row may not have a matching cabinet transaction in the uploaded Pyxis data."
        )
    else:
        pyxis_detail["tech_name"] = pyxis_detail["user_name"].apply(normalize_name)
        st.caption(
            "These are Pyxis cabinet events for the same medication and device/destination around the selected timeline row."
        )
        st.dataframe(
            pyxis_detail[[
                "dt", "tech_name", "device", "event_type", "qty",
                "beginning_qty", "ending_qty", "discrepancy_qty", "discrepancy_reason"
            ]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "dt": st.column_config.DatetimeColumn("Time", format="MM/DD HH:mm:ss"),
                "qty": st.column_config.NumberColumn("Action Qty", format="%.0f"),
                "beginning_qty": "Beginning Count",
                "ending_qty": "Ending Count",
                "discrepancy_qty": st.column_config.NumberColumn("Discrepancy Qty", format="%.0f"),
                "discrepancy_reason": "Discrepancy Reason",
            }
        )

# -------------------------------------------------
# 🔴 Gap Alert Section
# -------------------------------------------------
gaps = df_filtered[df_filtered['count_gap'].notna() & (df_filtered['count_gap'] != 0)]

if not gaps.empty:
    st.error("🚨 Inventory Gaps Detected — Possible Count Integrity Issues")

    st.dataframe(
        gaps[['dt', 'tech_name', 'device', 'event_type', 'qty', 'beginning_qty', 'ending_qty', 'count_gap']],
        use_container_width=True
    )

    st.caption(
        "A non-zero gap indicates the beginning count did not match the previous ending count "
        "for that medication in that Pyxis device. Carousel / Pyxis Pull rows are shown for context "
        "but are not included in this gap calculation."
    )
else:
    st.success("✅ No inventory discrepancies detected for selected filters.")

