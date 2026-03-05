import streamlit as st
import pandas as pd
import numpy as np
from sqlalchemy import text
from App import load_data, engine, render_sidebar

st.set_page_config(
    page_title="Cycle Count Integrity",
    page_icon="📊",
    layout="wide"
)

start_date, end_date = render_sidebar()

st.header("📊 Cycle Count Integrity Dashboard")
st.caption("Tracking days since last cycle count, user accountability, and carousel location mapping.")

# ----------------------------------------------------
# Load carousel master mapping
# ----------------------------------------------------

@st.cache_data(ttl=3600)
def load_master_mapping():
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text("SELECT med_id, med_desc, carousel_location FROM carousel_master_mapping"))
            df = pd.DataFrame(result.fetchall(), columns=result.keys())
        df["med_id"] = df["med_id"].astype(str).str.strip().str.upper()
        return df
    except Exception as e:
        st.warning(f"⚠️ Could not load carousel mapping: {e}")
        return pd.DataFrame()

# ----------------------------------------------------
# Load ALL pharmacy data (full history needed for cycle counts)
# ----------------------------------------------------

@st.cache_data(ttl=300)
def load_all_pharm():
    try:
        with engine.connect() as conn:
            df = pd.read_sql(
                text("SELECT priority, dt, med_id, med_desc, user_name, qty FROM pharmacy_orders"),
                conn
            )
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["med_id"] = df["med_id"].astype(str).str.strip().str.upper()
        return df
    except Exception as e:
        st.warning(f"⚠️ Could not load pharmacy data: {e}")
        return pd.DataFrame()

df_all_pharm = load_all_pharm()
df_master = load_master_mapping()

if df_all_pharm.empty:
    st.warning("No pharmacy workflow data found.")
    st.stop()

# ----------------------------------------------------
# Identify Cycle Count events (full history)
# ----------------------------------------------------

cycle_counts = df_all_pharm[
    df_all_pharm["priority"].astype(str).str.strip() == "Cycle Count"
].copy()

if cycle_counts.empty:
    st.warning("No cycle count transactions found in database.")
    st.stop()

cycle_counts["cycle_date"] = cycle_counts["dt"].dt.date

# Latest cycle count per med_id — who did it and when
latest_cycle = (
    cycle_counts
    .sort_values("dt")
    .groupby("med_id")
    .last()
    .reset_index()[["med_id", "cycle_date", "user_name"]]
    .rename(columns={"user_name": "cycle_count_user"})
)

# All meds that have EVER been cycle counted
ever_counted_ids = set(cycle_counts["med_id"].unique())

# ----------------------------------------------------
# Return activity within selected date range
# Returns = "Returns" priority only
# Instant returns included via "Returns" — Manual Restock excluded
# ----------------------------------------------------

returns = df_all_pharm[
    df_all_pharm["priority"].astype(str).str.strip() == "Returns"
].copy()

returns["return_date"] = returns["dt"].dt.date

returns = returns[
    (returns["return_date"] >= start_date) &
    (returns["return_date"] <= end_date)
].copy()

if returns.empty:
    st.warning("No return activity found in selected date range.")
    st.stop()

# ----------------------------------------------------
# Merge cycle count data onto returns
# ----------------------------------------------------

tracker = returns.merge(latest_cycle, on="med_id", how="left")

tracker["days_since_cycle"] = (
    pd.to_datetime(tracker["return_date"]) -
    pd.to_datetime(tracker["cycle_date"])
).dt.days

# Correctly flag never counted — based on whether med_id ever appears in cycle counts
tracker["never_cycle_counted"] = ~tracker["med_id"].isin(ever_counted_ids)

# ----------------------------------------------------
# Add carousel location via med_id join
# ----------------------------------------------------

if not df_master.empty:
    tracker = tracker.merge(
        df_master[["med_id", "carousel_location"]],
        on="med_id",
        how="left"
    )
else:
    tracker["carousel_location"] = None

# ----------------------------------------------------
# Executive Metrics
# ----------------------------------------------------

avg_days = tracker["days_since_cycle"].mean()
max_days = tracker["days_since_cycle"].max()
never_counted_count = tracker[tracker["never_cycle_counted"]]["med_id"].nunique()
matched = tracker["carousel_location"].notna().sum()
total = len(tracker)
match_pct = (matched / total * 100) if total > 0 else 0

m1, m2, m3, m4 = st.columns(4)
m1.metric("Avg Days Since Cycle Count", f"{avg_days:.1f}" if pd.notna(avg_days) else "N/A")
m2.metric("Max Days Since Cycle Count", int(max_days) if pd.notna(max_days) else 0)
m3.metric("Meds Never Cycle Counted", never_counted_count)
m4.metric("Carousel Match Rate", f"{match_pct:.1f}%")

st.divider()

# ----------------------------------------------------
# Tabs: Detail view + Never Counted
# ----------------------------------------------------

tab1, tab2 = st.tabs(["🔍 Return Activity", "🚨 Never Cycle Counted"])

with tab1:
    st.subheader("Post-Cycle Return Activity")
    st.caption(f"Returns from {start_date} to {end_date} with last cycle count date per medication.")

    display_cols = [c for c in [
        "med_id", "med_desc", "carousel_location",
        "return_date", "qty", "user_name",
        "cycle_date", "cycle_count_user", "days_since_cycle"
    ] if c in tracker.columns]

    st.dataframe(
        tracker[display_cols].sort_values("days_since_cycle", ascending=False),
        use_container_width=True,
        column_config={
            "days_since_cycle": st.column_config.NumberColumn("Days Since Count", format="%d days"),
            "return_date": st.column_config.DateColumn("Return Date"),
            "cycle_date": st.column_config.DateColumn("Last Cycle Count"),
            "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
        },
        hide_index=True
    )

with tab2:
    st.subheader("Medications Never Cycle Counted")
    st.caption("These medications have been returned during the selected period but have NO cycle count record in the entire database.")

    never_df = tracker[tracker["never_cycle_counted"]].copy()

    if never_df.empty:
        st.success("✅ All returned medications have at least one recorded cycle count.")
    else:
        never_summary = (
            never_df
            .groupby(
                ["med_id", "med_desc", "carousel_location"],
                dropna=False
            )
            .agg(
                total_qty_returned=("qty", "sum"),
                return_occurrences=("med_id", "count")
            )
            .reset_index()
            .sort_values("total_qty_returned", ascending=False)
        )

        st.metric("Distinct Meds Never Counted", len(never_summary))
        st.dataframe(
            never_summary,
            use_container_width=True,
            column_config={
                "total_qty_returned": st.column_config.NumberColumn("Total Qty Returned", format="%.0f"),
                "return_occurrences": st.column_config.NumberColumn("# Return Events", format="%d"),
            },
            hide_index=True
        )

        # Breakdown by carousel location
        if "carousel_location" in never_summary.columns:
            st.divider()
            st.subheader("📍 By Carousel Location")
            loc_summary = (
                never_summary
                .groupby("carousel_location", dropna=False)
                .agg(meds=("med_id", "count"), total_qty=("total_qty_returned", "sum"))
                .reset_index()
                .sort_values("meds", ascending=False)
            )
            st.dataframe(loc_summary, use_container_width=True, hide_index=True)
