import pandas as pd
import plotly.express as px
import streamlit as st

import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)


st.set_page_config(page_title="IV Overnight Optimizer", page_icon="🌙", layout="wide")

render_sidebar = App.render_sidebar
load_orders = App.load_overnight_cartfill_orders
load_context = App.load_overnight_cartfill_context

start_date, end_date = render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "IV Overnight Optimizer",
        "Focus the overnight model on SJS Cleanroom cartfills, measure demand against a four-hour prep window, and surface long-hold waste risk.",
        kicker="Operations",
    )
    _debug_event("IV Overnight Optimizer", "shared_intro_loaded")
    _debug_panel("IV Overnight Optimizer", intro_mode="shared")
else:
    st.header("🌙 IV Overnight Optimizer")
    st.caption("Focus overnight cleanroom cartfills on staffing pressure and waste prevention.")
    _debug_event("IV Overnight Optimizer", "fallback_header_used")
    _debug_panel("IV Overnight Optimizer", intro_mode="fallback")

with st.spinner("Loading overnight cartfill model..."):
    df_orders = load_orders(start_date, end_date)
    df_windows, df_staffing = load_context()

if df_orders.empty:
    st.info("No overnight cartfill model data found for this date range. Upload an `IV Overnight Cartfill Model` workbook from the sidebar to get started.")
    st.stop()

orders = df_orders.copy()
for col in ["ready_for_dispense_dt", "admin_given_dt", "prepared_dt", "required_start_dt", "event_date"]:
    if col in orders.columns:
        orders[col] = pd.to_datetime(orders[col], errors="coerce")

orders["pharmacy"] = orders["pharmacy"].fillna("Unknown").astype(str).str.strip()
orders["prep_or_dispense_user"] = orders["prep_or_dispense_user"].fillna("Unknown").astype(str).str.strip()
orders["order_medication"] = orders["order_medication"].fillna("Unknown").astype(str).str.strip()
orders["is_sjs_cleanroom"] = orders["is_sjs_cleanroom"].fillna(False)
orders["prep_lead_hours"] = pd.to_numeric(orders["prep_lead_hours"], errors="coerce")
orders["hold_hours"] = pd.to_numeric(orders["hold_hours"], errors="coerce")

pharmacy_options = sorted(orders["pharmacy"].dropna().unique().tolist())
default_pharmacy = ["SJS Cleanroom"] if "SJS Cleanroom" in pharmacy_options else pharmacy_options
selected_pharmacies = st.multiselect("Pharmacy", pharmacy_options, default=default_pharmacy)

filtered = orders.copy()
if selected_pharmacies:
    filtered = filtered[filtered["pharmacy"].isin(selected_pharmacies)]

if filtered.empty:
    st.warning("No overnight cartfill records match the current filters.")
    st.stop()

cleanroom = filtered[filtered["is_sjs_cleanroom"]].copy()
focus = cleanroom if not cleanroom.empty else filtered.copy()

focus["due_dt"] = focus["ready_for_dispense_dt"].fillna(focus["admin_given_dt"])
focus["required_start_dt"] = focus["required_start_dt"].fillna(focus["due_dt"] - pd.Timedelta(hours=4))
focus["prep_lead_hours"] = focus["prep_lead_hours"].where(focus["prep_lead_hours"].notna(), (focus["due_dt"] - focus["prepared_dt"]).dt.total_seconds() / 3600)
focus["hold_hours"] = focus["hold_hours"].where(focus["hold_hours"].notna(), (focus["admin_given_dt"] - focus["prepared_dt"]).dt.total_seconds() / 3600)

due_ready = focus.dropna(subset=["due_dt"]).copy()
prep_ready = focus.dropna(subset=["prepared_dt", "due_dt"]).copy()
hold_ready = focus.dropna(subset=["prepared_dt", "admin_given_dt"]).copy()

compressed = prep_ready[prep_ready["prep_lead_hours"] < 4].copy()
long_hold = hold_ready[hold_ready["hold_hours"] > 8].copy()

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Focused Orders", f"{len(focus):,}")
m2.metric("Cleanroom Orders", f"{int(focus['is_sjs_cleanroom'].sum()):,}")
m3.metric("Orders Under 4h Lead", f"{len(compressed):,}")
m4.metric("Median Prep Lead", f"{prep_ready['prep_lead_hours'].median():.1f} h" if not prep_ready.empty else "N/A")
m5.metric("Long Holds >8h", f"{len(long_hold):,}")

st.caption("Current assumptions: scheduled due time comes from `Ready for Dispense`, required cleanroom start is `due time - 4 hours`, and holds over 8 hours are shown as a waste-risk proxy.")

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("When IV Work Needed To Start")
    if due_ready.empty:
        st.info("No due timestamps are available in this filter window.")
    else:
        start_profile = due_ready.copy()
        start_profile["required_hour"] = start_profile["required_start_dt"].dt.hour
        start_mix = start_profile.groupby("required_hour", as_index=False).agg(
            orders=("pk", "count"),
        )
        fig_start = px.bar(
            start_mix,
            x="required_hour",
            y="orders",
            labels={"required_hour": "Hour Cleanroom Should Start", "orders": "Orders"},
            color="orders",
            color_continuous_scale="Blues",
        )
        fig_start.update_layout(coloraxis_showscale=False, height=360)
        st.plotly_chart(fig_start, width="stretch")

with col2:
    st.subheader("Due Hour Profile")
    if due_ready.empty:
        st.info("No due timestamps are available in this filter window.")
    else:
        due_profile = due_ready.copy()
        due_profile["due_hour"] = due_profile["due_dt"].dt.hour
        due_mix = due_profile.groupby("due_hour", as_index=False).agg(
            orders=("pk", "count"),
        )
        fig_due = px.line(
            due_mix,
            x="due_hour",
            y="orders",
            markers=True,
            labels={"due_hour": "First Dose Due Hour", "orders": "Orders"},
        )
        fig_due.update_layout(height=360)
        st.plotly_chart(fig_due, use_container_width=True)

col3, col4 = st.columns(2)

with col3:
    st.subheader("Highest Pressure Medications")
    med_pressure = (
        focus.groupby("order_medication", as_index=False)
        .agg(
            orders=("pk", "count"),
            median_prep_lead=("prep_lead_hours", "median"),
            median_hold=("hold_hours", "median"),
        )
        .sort_values(["orders", "median_prep_lead"], ascending=[False, True])
        .head(15)
    )
    fig_meds = px.bar(
        med_pressure.sort_values("orders"),
        x="orders",
        y="order_medication",
        orientation="h",
        hover_data=["median_prep_lead", "median_hold"],
        labels={"orders": "Orders", "order_medication": ""},
        color="orders",
        color_continuous_scale="Tealgrn",
    )
    fig_meds.update_layout(coloraxis_showscale=False, height=420)
    st.plotly_chart(fig_meds, use_container_width=True)

with col4:
    st.subheader("Preparation Load By User")
    user_load = (
        focus.groupby("prep_or_dispense_user", as_index=False)
        .agg(
            orders=("pk", "count"),
            median_prep_lead=("prep_lead_hours", "median"),
            median_hold=("hold_hours", "median"),
        )
        .sort_values("orders", ascending=False)
        .head(15)
    )
    fig_users = px.bar(
        user_load.sort_values("orders"),
        x="orders",
        y="prep_or_dispense_user",
        orientation="h",
        hover_data=["median_prep_lead", "median_hold"],
        labels={"orders": "Orders", "prep_or_dispense_user": ""},
        color="orders",
        color_continuous_scale="Greens",
    )
    fig_users.update_layout(coloraxis_showscale=False, height=420)
    st.plotly_chart(fig_users, use_container_width=True)

ctx_col1, ctx_col2 = st.columns(2)

with ctx_col1:
    st.subheader("Configured SJS IV Cartfill Windows")
    if df_windows.empty:
        st.info("No cartfill timing rows are stored yet from the workbook.")
    else:
        cleanroom_windows = df_windows[df_windows["pharmacy"].fillna("").astype(str).str.contains("Cleanroom", case=False, na=False)].copy()
        if cleanroom_windows.empty:
            st.info("No cleanroom-specific timing rows were found in the workbook context.")
        else:
            st.dataframe(
                cleanroom_windows[["cartfill_name", "time_processed_raw", "doses_due", "pharmacy"]],
                width="stretch",
                hide_index=True,
            )

with ctx_col2:
    st.subheader("Staffing Model Snapshot")
    if df_staffing.empty:
        st.info("No staffing model rows are stored yet from the workbook.")
    else:
        staffing = df_staffing.copy()
        staffing["schedule_date"] = pd.to_datetime(staffing["schedule_date"], errors="coerce")
        if staffing["schedule_date"].notna().any():
            staffing = staffing[staffing["schedule_date"].dt.date.between(start_date, end_date)].copy()
        overnight_staff = staffing[
            staffing["shift_name"].fillna("").astype(str).str.contains("IV|Central|Narc|Pyxis|Pack", case=False, na=False)
        ].copy()
        if overnight_staff.empty:
            st.info("No IV-adjacent staffing rows match the selected date range.")
        else:
            st.dataframe(
                overnight_staff[["schedule_date", "day_name", "shift_name", "assigned_staff"]],
                width="stretch",
                hide_index=True,
            )

st.divider()

alert_col1, alert_col2 = st.columns(2)

with alert_col1:
    st.subheader("Orders Inside The 4-Hour Prep Window")
    if compressed.empty:
        st.success("No orders with less than 4 hours of prep lead are in the current filter window.")
    else:
        st.dataframe(
            compressed[[
                "order_id", "order_medication", "pharmacy", "prepared_dt",
                "due_dt", "prep_lead_hours", "prep_or_dispense_user",
            ]].sort_values("prep_lead_hours"),
            width="stretch",
            hide_index=True,
            column_config={
                "prepared_dt": st.column_config.DatetimeColumn("Prepared", format="MM/DD/YY HH:mm"),
                "due_dt": st.column_config.DatetimeColumn("Due", format="MM/DD/YY HH:mm"),
                "prep_lead_hours": st.column_config.NumberColumn("Prep Lead (h)", format="%.2f"),
            },
        )

with alert_col2:
    st.subheader("Potential Waste Hold List")
    if long_hold.empty:
        st.success("No orders exceeded the long-hold threshold in the current filter window.")
    else:
        st.dataframe(
            long_hold[[
                "order_id", "order_medication", "pharmacy", "prepared_dt",
                "admin_given_dt", "hold_hours", "prep_or_dispense_user",
            ]].sort_values("hold_hours", ascending=False),
            width="stretch",
            hide_index=True,
            column_config={
                "prepared_dt": st.column_config.DatetimeColumn("Prepared", format="MM/DD/YY HH:mm"),
                "admin_given_dt": st.column_config.DatetimeColumn("Admin Given", format="MM/DD/YY HH:mm"),
                "hold_hours": st.column_config.NumberColumn("Hold Hours", format="%.2f"),
            },
        )
