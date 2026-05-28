from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import App


st.set_page_config(page_title="Outdate Tracker", page_icon="📦", layout="wide")
App.apply_global_styles()

render_sidebar = App.render_sidebar


@st.cache_data(ttl=300)
def load_outdate_tracker(start_date, end_date):
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date) + timedelta(days=1)
    sql = """
        WITH outdate_events AS (
            SELECT
                e.pk,
                e.dt AS outdate_dt,
                e.user_name,
                e.device,
                e.med_id,
                e.med_desc,
                e.event_type,
                e.qty,
                e.beginning_qty,
                e.ending_qty,
                e.discrepancy_qty,
                c.cost_per_unit
            FROM events e
            LEFT JOIN med_costs c ON UPPER(TRIM(e.med_id)) = UPPER(TRIM(c.med_id))
            WHERE e.dt >= %s AND e.dt < %s
              AND COALESCE(e.event_type, '') ~* 'outdat|expir|override'
              AND COALESCE(e.event_type, '') !~* 'cancel'
        )
        SELECT
            o.pk,
            o.outdate_dt,
            o.user_name AS outdated_by,
            o.device,
            o.med_id,
            o.med_desc,
            o.event_type AS outdate_event_type,
            o.qty,
            o.beginning_qty,
            o.ending_qty,
            o.discrepancy_qty,
            o.cost_per_unit,
            clinical.dt AS last_used_dt,
            clinical.event_type AS last_used_event_type,
            clinical.user_name AS last_used_by,
            clinical.qty AS last_used_qty,
            refill.dt AS last_refill_dt,
            refill.event_type AS last_refill_event_type,
            refill.user_name AS last_refilled_by,
            refill.qty AS last_refill_qty,
            activity.dt AS last_activity_dt,
            activity.event_type AS last_activity_event_type,
            activity.user_name AS last_activity_by
        FROM outdate_events o
        LEFT JOIN LATERAL (
            SELECT p.dt, p.event_type, p.user_name, p.qty
            FROM events p
            WHERE p.dt < o.outdate_dt
              AND UPPER(TRIM(COALESCE(p.device, ''))) = UPPER(TRIM(COALESCE(o.device, '')))
              AND UPPER(TRIM(COALESCE(p.med_id, ''))) = UPPER(TRIM(COALESCE(o.med_id, '')))
              AND COALESCE(p.event_type, '') !~* 'cancel|outdat|expir|override|verify|count|inventory|refill|restock|load|replenish|unload|empty|return|eject'
            ORDER BY p.dt DESC
            LIMIT 1
        ) clinical ON TRUE
        LEFT JOIN LATERAL (
            SELECT p.dt, p.event_type, p.user_name, p.qty
            FROM events p
            WHERE p.dt < o.outdate_dt
              AND UPPER(TRIM(COALESCE(p.device, ''))) = UPPER(TRIM(COALESCE(o.device, '')))
              AND UPPER(TRIM(COALESCE(p.med_id, ''))) = UPPER(TRIM(COALESCE(o.med_id, '')))
              AND COALESCE(p.event_type, '') ~* 'refill|restock|load|replenish'
              AND COALESCE(p.event_type, '') !~* 'cancel|unload|empty'
            ORDER BY p.dt DESC
            LIMIT 1
        ) refill ON TRUE
        LEFT JOIN LATERAL (
            SELECT p.dt, p.event_type, p.user_name
            FROM events p
            WHERE p.dt < o.outdate_dt
              AND UPPER(TRIM(COALESCE(p.device, ''))) = UPPER(TRIM(COALESCE(o.device, '')))
              AND UPPER(TRIM(COALESCE(p.med_id, ''))) = UPPER(TRIM(COALESCE(o.med_id, '')))
              AND COALESCE(p.event_type, '') !~* 'cancel|outdat|expir|override'
            ORDER BY p.dt DESC
            LIMIT 1
        ) activity ON TRUE
        ORDER BY o.outdate_dt DESC, o.device, o.med_desc
    """
    df = App.run_query(sql, params=(start_ts, end_ts))
    if df.empty:
        return df

    for col in ["outdate_dt", "last_used_dt", "last_refill_dt", "last_activity_dt"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in [
        "outdated_by", "device", "med_id", "med_desc", "outdate_event_type",
        "last_used_event_type", "last_used_by", "last_refill_event_type",
        "last_refilled_by", "last_activity_event_type", "last_activity_by",
    ]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    for col in ["qty", "beginning_qty", "ending_qty", "discrepancy_qty", "cost_per_unit", "last_used_qty", "last_refill_qty"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["outdate_qty"] = df["qty"].fillna(0).abs()
    df["estimated_cost"] = df["outdate_qty"] * df["cost_per_unit"].fillna(0)
    df["days_since_used"] = (df["outdate_dt"] - df["last_used_dt"]).dt.total_seconds() / 86400
    df["days_since_refill"] = (df["outdate_dt"] - df["last_refill_dt"]).dt.total_seconds() / 86400
    df["days_since_activity"] = (df["outdate_dt"] - df["last_activity_dt"]).dt.total_seconds() / 86400
    df["usage_status"] = "Prior use found"
    df.loc[df["last_used_dt"].isna(), "usage_status"] = "No prior clinical use found"
    return df


start_date, end_date = render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Outdate Tracker",
        "Review what was outdated, who removed it, and how long the item had gone without documented use.",
        kicker="Operations",
    )
else:
    st.header("Outdate Tracker")
    st.caption("Review what was outdated and days since last documented use.")

with st.spinner("Loading outdate tracker..."):
    outdates = load_outdate_tracker(start_date, end_date)

if outdates.empty:
    st.info("No outdate events were found for the selected date range.")
    st.stop()

total_rows = len(outdates)
unique_meds = outdates["med_id"].replace("", pd.NA).nunique()
unique_devices = outdates["device"].replace("", pd.NA).nunique()
total_qty = outdates["outdate_qty"].sum()
estimated_cost = outdates["estimated_cost"].sum()
median_days = outdates["days_since_used"].dropna().median()
no_prior_use = int(outdates["last_used_dt"].isna().sum())

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Outdate Rows", f"{total_rows:,}")
m2.metric("Unique Meds", f"{unique_meds:,}")
m3.metric("Devices", f"{unique_devices:,}")
m4.metric("Qty Outdated", f"{total_qty:,.0f}")
m5.metric("Median Days Since Used", f"{median_days:.1f}" if pd.notna(median_days) else "-")
m6.metric("No Prior Use Found", f"{no_prior_use:,}")

if estimated_cost > 0:
    st.caption(f"Estimated outdated cost from med_costs: ${estimated_cost:,.2f}")
else:
    st.caption("Estimated cost is blank because matching unit costs were not available for these rows.")

f1, f2, f3, f4 = st.columns([1.2, 1.2, 1, 1.6])
device_options = sorted(outdates["device"].replace("", pd.NA).dropna().unique())
status_options = sorted(outdates["usage_status"].unique())
selected_devices = f1.multiselect("Device", device_options, key="outdate_tracker_device_filter")
selected_status = f2.multiselect("Usage Status", status_options, default=status_options, key="outdate_tracker_status_filter")
min_days = f3.number_input("Min days since used", min_value=0, value=0, step=1)
search = f4.text_input("Med search", key="outdate_tracker_search")

view = outdates.copy()
if selected_devices:
    view = view[view["device"].isin(selected_devices)]
if selected_status:
    view = view[view["usage_status"].isin(selected_status)]
if min_days:
    view = view[view["days_since_used"].fillna(-1).ge(min_days)]
if search:
    mask = (
        view["med_id"].str.contains(search, case=False, na=False)
        | view["med_desc"].str.contains(search, case=False, na=False)
        | view["device"].str.contains(search, case=False, na=False)
    )
    view = view[mask]

st.subheader("Outdate Detail")
st.caption(
    "Days Since Used looks for the last prior non-maintenance transaction for the same med on the same device. "
    "If no prior use is found, compare Last Refill and Last Activity to see whether it was stocked but never documented as used."
)

display_cols = [
    "outdate_dt", "device", "med_id", "med_desc", "outdate_event_type", "outdated_by",
    "outdate_qty", "days_since_used", "last_used_dt", "last_used_event_type", "last_used_by",
    "days_since_refill", "last_refill_dt", "last_refill_event_type", "last_refilled_by",
    "days_since_activity", "last_activity_dt", "last_activity_event_type", "usage_status", "estimated_cost",
]
display = view[display_cols].sort_values(["days_since_used", "outdate_dt"], ascending=[False, False]).copy()
st.dataframe(
    display,
    width="stretch",
    hide_index=True,
    column_config={
        "outdate_dt": st.column_config.DatetimeColumn("Outdated", format="MM/DD/YY HH:mm"),
        "device": st.column_config.TextColumn("Device"),
        "med_id": st.column_config.TextColumn("Med ID"),
        "med_desc": st.column_config.TextColumn("Medication"),
        "outdate_event_type": st.column_config.TextColumn("Outdate Type"),
        "outdated_by": st.column_config.TextColumn("Outdated By"),
        "outdate_qty": st.column_config.NumberColumn("Qty", format="%.0f"),
        "days_since_used": st.column_config.NumberColumn("Days Since Used", format="%.1f"),
        "last_used_dt": st.column_config.DatetimeColumn("Last Used", format="MM/DD/YY HH:mm"),
        "last_used_event_type": st.column_config.TextColumn("Last Use Type"),
        "last_used_by": st.column_config.TextColumn("Last Used By"),
        "days_since_refill": st.column_config.NumberColumn("Days Since Refill", format="%.1f"),
        "last_refill_dt": st.column_config.DatetimeColumn("Last Refill", format="MM/DD/YY HH:mm"),
        "last_refill_event_type": st.column_config.TextColumn("Last Refill Type"),
        "last_refilled_by": st.column_config.TextColumn("Last Refilled By"),
        "days_since_activity": st.column_config.NumberColumn("Days Since Any Activity", format="%.1f"),
        "last_activity_dt": st.column_config.DatetimeColumn("Last Activity", format="MM/DD/YY HH:mm"),
        "last_activity_event_type": st.column_config.TextColumn("Last Activity Type"),
        "usage_status": st.column_config.TextColumn("Usage Status"),
        "estimated_cost": st.column_config.NumberColumn("Est. Cost", format="$%.2f"),
    },
)

st.caption(f"{len(display):,} of {len(outdates):,} outdate rows shown.")

if not display.empty:
    c1, c2 = st.columns([1, 1])
    with c1:
        device_summary = (
            view.groupby("device", dropna=False)
            .agg(
                outdate_rows=("pk", "count"),
                unique_meds=("med_id", "nunique"),
                qty=("outdate_qty", "sum"),
                median_days_since_used=("days_since_used", "median"),
                estimated_cost=("estimated_cost", "sum"),
            )
            .reset_index()
            .sort_values("outdate_rows", ascending=False)
        )
        st.subheader("By Device")
        st.dataframe(device_summary, width="stretch", hide_index=True)

    with c2:
        med_summary = (
            view.groupby(["med_id", "med_desc"], dropna=False)
            .agg(
                outdate_rows=("pk", "count"),
                devices=("device", "nunique"),
                qty=("outdate_qty", "sum"),
                median_days_since_used=("days_since_used", "median"),
                estimated_cost=("estimated_cost", "sum"),
            )
            .reset_index()
            .sort_values(["outdate_rows", "qty"], ascending=[False, False])
        )
        st.subheader("By Medication")
        st.dataframe(med_summary.head(50), width="stretch", hide_index=True)

    if view["days_since_used"].notna().any():
        chart_df = view.dropna(subset=["days_since_used"]).copy()
        chart_df["label"] = chart_df["med_desc"].where(chart_df["med_desc"].ne(""), chart_df["med_id"])
        chart_df["plot_qty"] = chart_df["outdate_qty"].clip(lower=1)
        fig = px.scatter(
            chart_df,
            x="outdate_dt",
            y="days_since_used",
            color="device",
            size="plot_qty",
            hover_data=["med_id", "med_desc", "outdate_qty", "last_used_dt"],
            labels={"outdate_dt": "Outdate Time", "days_since_used": "Days Since Used"},
            title="Outdates by Days Since Last Documented Use",
        )
        fig.update_layout(height=420, legend_title_text="Device")
        st.plotly_chart(fig, width="stretch")

    csv = display.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download outdate tracker CSV",
        data=csv,
        file_name="outdate_tracker.csv",
        mime="text/csv",
    )
