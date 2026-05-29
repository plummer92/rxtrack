import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

import App


st.set_page_config(page_title="Zero Verify Watch", page_icon="🔎", layout="wide")
App.apply_global_styles()
start_date, end_date = App.render_sidebar()
engine = App.engine

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Zero Verify Watch",
        "Find meds that keep being verified at zero so you can tune Pyxis min/max levels before stockouts become routine.",
        kicker="Inventory Tuning",
    )
else:
    st.header("Zero Verify Watch")
    st.caption("Find meds repeatedly counted at zero and review the refill trail behind them.")


@st.cache_data(ttl=300)
def load_current_device_inventory():
    sql = text("""
        SELECT DISTINCT ON (UPPER(TRIM(device)), UPPER(TRIM(med_id)))
            UPPER(TRIM(device)) AS device,
            UPPER(TRIM(med_id)) AS med_id,
            med_desc,
            current_quantity,
            min_qty,
            max_qty,
            pocket_location,
            status,
            snapshot_dt
        FROM device_inventory
        WHERE device IS NOT NULL
          AND med_id IS NOT NULL
        ORDER BY UPPER(TRIM(device)), UPPER(TRIM(med_id)), snapshot_dt DESC NULLS LAST
    """)
    try:
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    for col in ["current_quantity", "min_qty", "max_qty"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["snapshot_dt"] = pd.to_datetime(df["snapshot_dt"], errors="coerce")
    return df


def attach_inventory_settings(summary):
    if summary.empty:
        return summary
    inventory = load_current_device_inventory()
    if inventory.empty:
        return summary
    work = summary.copy()
    work["device_key"] = work["device"].fillna("").astype(str).str.strip().str.upper()
    work["med_id_key"] = work["med_id"].fillna("").astype(str).str.strip().str.upper()
    inventory = inventory.rename(columns={"device": "device_key", "med_id": "med_id_key"})
    merged = work.merge(
        inventory[
            [
                "device_key", "med_id_key", "current_quantity", "min_qty", "max_qty",
                "pocket_location", "status", "snapshot_dt",
            ]
        ],
        on=["device_key", "med_id_key"],
        how="left",
    )
    merged.drop(columns=["device_key", "med_id_key"], inplace=True, errors="ignore")
    return merged


def add_review_signals(summary):
    if summary.empty:
        return summary
    out = summary.copy()
    out["zero_verifies"] = pd.to_numeric(out["zero_verifies"], errors="coerce").fillna(0)
    if "avg_hours_since_refill" not in out.columns:
        out["avg_hours_since_refill"] = pd.NA
    out["avg_hours_since_refill"] = pd.to_numeric(out["avg_hours_since_refill"], errors="coerce")
    out["review_priority"] = "Watch"
    out.loc[out["zero_verifies"].ge(3), "review_priority"] = "Review min/max"
    out.loc[
        out["zero_verifies"].ge(5) | out["avg_hours_since_refill"].between(0, 24, inclusive="both"),
        "review_priority",
    ] = "High priority"
    out["suggested_action"] = "Review recent usage and current min/max"
    out.loc[out["review_priority"].eq("High priority"), "suggested_action"] = (
        "Consider raising min/max or adding a refill check"
    )
    return out


run_analysis = st.checkbox(
    "Run Zero Verify Analysis",
    value=False,
    help="Runs the heavier refill-history lookup for the selected date range.",
)

if not run_analysis:
    st.info("Choose a date range, then run the analysis when you want the heavier refill-history lookup.")
    st.stop()

with st.spinner("Running zero-verify refill analysis..."):
    zero_verify_events = App.load_zero_verify_refill_gaps(start_date, end_date)
    zero_verify_summary = App.summarize_zero_verify_refill_gaps(zero_verify_events)
    zero_verify_summary = add_review_signals(attach_inventory_settings(zero_verify_summary))

if zero_verify_events.empty:
    st.success("No Verify Inventory transactions with quantity 0 were found in this date range.")
    st.stop()

avg_zero_refill_gap = zero_verify_events["hours_since_refill"].dropna().mean()

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Zero Verify Events", f"{len(zero_verify_events):,}")
k2.metric("Med/Device Pairs", f"{len(zero_verify_summary):,}")
k3.metric("Meds Hit Zero", f"{zero_verify_events['med_id'].nunique():,}")
k4.metric("Devices With Zero", f"{zero_verify_events['device'].nunique():,}")
k5.metric("Avg Refill To Zero", f"{avg_zero_refill_gap:.1f}h" if pd.notna(avg_zero_refill_gap) else "-")

priority_options = ["High priority", "Review min/max", "Watch"]
selected_priorities = st.multiselect(
    "Priority",
    priority_options,
    default=[p for p in priority_options if p in set(zero_verify_summary["review_priority"])],
)
filtered_summary = zero_verify_summary[
    zero_verify_summary["review_priority"].isin(selected_priorities)
].copy() if selected_priorities else zero_verify_summary.copy()

if not filtered_summary.empty:
    priority_counts = (
        filtered_summary["review_priority"]
        .value_counts()
        .rename_axis("priority")
        .reset_index(name="pairs")
    )
    fig = px.bar(
        priority_counts,
        x="pairs",
        y="priority",
        orientation="h",
        color="priority",
        color_discrete_map={
            "High priority": "#ef4444",
            "Review min/max": "#f59e0b",
            "Watch": "#3b82f6",
        },
    )
    fig.update_layout(height=240, margin=dict(l=0, r=10, t=10, b=0), showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Min/Max Review Worklist")
st.dataframe(
    filtered_summary,
    use_container_width=True,
    hide_index=True,
    column_config={
        "review_priority": st.column_config.TextColumn("Priority"),
        "suggested_action": st.column_config.TextColumn("Suggested Action"),
        "device": st.column_config.TextColumn("Device"),
        "med_id": st.column_config.TextColumn("Med ID"),
        "med_desc": st.column_config.TextColumn("Medication"),
        "zero_verifies": st.column_config.NumberColumn("Zero Verifies", format="%d"),
        "min_qty": st.column_config.NumberColumn("Current Min", format="%.0f"),
        "max_qty": st.column_config.NumberColumn("Current Max", format="%.0f"),
        "current_quantity": st.column_config.NumberColumn("Current Qty", format="%.0f"),
        "pocket_location": st.column_config.TextColumn("Pocket"),
        "first_seen": st.column_config.DatetimeColumn("First Seen", format="MM/DD/YY HH:mm"),
        "last_seen": st.column_config.DatetimeColumn("Last Seen", format="MM/DD/YY HH:mm"),
        "avg_hours_since_refill": st.column_config.NumberColumn("Avg Hours Since Refill", format="%.1f"),
        "median_hours_since_refill": st.column_config.NumberColumn("Median Hours Since Refill", format="%.1f"),
        "last_prior_refill": st.column_config.DatetimeColumn("Last Prior Refill", format="MM/DD/YY HH:mm"),
        "verify_users": st.column_config.TextColumn("Verify Users"),
        "prior_refill_users": st.column_config.TextColumn("Prior Refill Users"),
    },
)

st.subheader("Event Trail")
detail_cols = [
    "dt", "user_name", "device", "med_id", "med_desc", "qty",
    "prior_refill_dt", "prior_refill_user", "prior_refill_event_type",
    "prior_refill_qty", "hours_since_refill",
]
st.dataframe(
    zero_verify_events[[col for col in detail_cols if col in zero_verify_events.columns]],
    use_container_width=True,
    hide_index=True,
    column_config={
        "dt": st.column_config.DatetimeColumn("Zero Verify Time", format="MM/DD/YY HH:mm"),
        "user_name": st.column_config.TextColumn("Verify User"),
        "prior_refill_dt": st.column_config.DatetimeColumn("Prior Refill Time", format="MM/DD/YY HH:mm"),
        "prior_refill_user": st.column_config.TextColumn("Prior Refill User"),
        "prior_refill_qty": st.column_config.NumberColumn("Prior Refill Qty", format="%.0f"),
        "hours_since_refill": st.column_config.NumberColumn("Hours Since Refill", format="%.1f"),
    },
)
