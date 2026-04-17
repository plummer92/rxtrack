import streamlit as st
import pandas as pd
import App

st.set_page_config(page_title="Med Audit Trail", page_icon="🔍", layout="wide")
if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.render_sidebar()

engine = App.engine
normalize_name = App.normalize_name

App.render_page_intro(
    "Advanced Medication Audit",
    "Deep-dive into specific medication history with technician and device filtering while keeping the new RxTrack page shell.",
    kicker="Tools",
)

# -------------------------------------------------
# 1️⃣ Load Medication List (Cached)
# -------------------------------------------------
@st.cache_data(ttl=300)
def get_med_list():
    query = "SELECT DISTINCT med_desc FROM events ORDER BY med_desc"
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
query = """
    SELECT *
    FROM events
    WHERE med_desc = %s
    ORDER BY dt DESC
"""

with st.spinner("Scanning database..."):
    df_raw = pd.read_sql(query, engine, params=(selected_med,))

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

selected_devices = st.sidebar.multiselect(
    "Filter by Device:",
    options=sorted(df_raw['device'].dropna().unique()),
    default=sorted(df_raw['device'].dropna().unique())
)

selected_techs = st.sidebar.multiselect(
    "Filter by Technician:",
    options=sorted(df_raw['tech_name'].dropna().unique()),
    default=sorted(df_raw['tech_name'].dropna().unique())
)

selected_events = st.sidebar.multiselect(
    "Filter by Event Type:",
    options=sorted(df_raw['event_type'].dropna().unique()),
    default=sorted(df_raw['event_type'].dropna().unique())
)

# -------------------------------------------------
# 6️⃣ Apply Filters
# -------------------------------------------------
df_filtered = df_raw[
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

df_filtered['prev_ending'] = (
    df_filtered.groupby(['device', 'med_id'])['ending_qty']
    .shift(-1)
)

df_filtered['count_gap'] = (
    df_filtered['beginning_qty'] - df_filtered['prev_ending']
)

df_filtered['count_gap'] = df_filtered['count_gap'].fillna(0)

# -------------------------------------------------
# 8️⃣ Metrics Overview
# -------------------------------------------------
c1, c2, c3 = st.columns(3)

c1.metric("Matches Found", len(df_filtered))
c2.metric("Unique Devices", df_filtered['device'].nunique())
c3.metric("Detected Gaps", int((df_filtered['count_gap'] != 0).sum()))

# -------------------------------------------------
# 9️⃣ Timeline Display
# -------------------------------------------------
st.subheader("📋 Audit Timeline")

st.dataframe(
    df_filtered[['dt', 'tech_name', 'device', 'event_type',
                 'qty', 'beginning_qty', 'ending_qty', 'count_gap']],
    use_container_width=True,
    column_config={
        "dt": st.column_config.DatetimeColumn("Time", format="MM/DD HH:mm:ss"),
        "qty": st.column_config.NumberColumn("Action Qty", format="%.0f"),
        "count_gap": st.column_config.NumberColumn("Inventory Gap", format="%.0f"),
        "beginning_qty": "Beginning Count",
        "ending_qty": "Ending Count"
    }
)

# -------------------------------------------------
# 🔴 Gap Alert Section
# -------------------------------------------------
gaps = df_filtered[df_filtered['count_gap'] != 0]

if not gaps.empty:
    st.error("🚨 Inventory Gaps Detected — Possible Count Integrity Issues")

    st.dataframe(
        gaps[['dt', 'tech_name', 'device', 'count_gap']],
        use_container_width=True
    )

    st.caption(
        "A non-zero gap indicates the beginning count did not match the previous ending count "
        "for that medication in that device."
    )
else:
    st.success("✅ No inventory discrepancies detected for selected filters.")
