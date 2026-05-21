from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import bindparam, text

import App


st.set_page_config(page_title="Device Utilization", page_icon=":bar_chart:", layout="wide")
App.apply_global_styles()
start_date, end_date = App.render_sidebar()
engine = App.engine

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Device Utilization",
        "Track device usage, stockout pressure, and refill volume to decide whether high-demand cabinets need more than one daily refill.",
        kicker="Operations",
    )
else:
    st.header("Device Utilization")
    st.caption("Track usage, stockouts, and refill volume by device.")


DEFAULT_CATHLAB_DEVICES = ["SJSCATHL8", "SJSCATHL12"]
REFILL_PATTERN = r"restock|refill|\bload\b|replenish"
REFILL_EXCLUDE_PATTERN = r"cancel|unload|empty|outdate"
USAGE_PATTERN = r"remove|dispense|vend|deduct|withdraw|issue|administer"
USAGE_EXCLUDE_PATTERN = r"return|refill|restock|load|unload|verify|count|cancel|waste|outdate|expire"
STOCKOUT_PATTERN = r"stock\s*out|stockout"


def _date_bounds(start, end):
    start_ts = pd.Timestamp(start)
    end_exclusive = pd.Timestamp(end) + pd.Timedelta(days=1)
    return start_ts, end_exclusive


def _clean_device_name(value):
    return str(value or "").strip().upper()


@st.cache_data(ttl=300)
def load_device_candidates():
    frames = []
    with engine.connect() as conn:
        queries = [
            """
            SELECT DISTINCT device AS device
            FROM events
            WHERE device IS NOT NULL
            """,
            """
            SELECT DISTINCT destination AS device
            FROM pharmacy_orders
            WHERE destination IS NOT NULL
            """,
            """
            SELECT DISTINCT station_name AS device
            FROM audit_transaction_detail_rc
            WHERE station_name IS NOT NULL
            """,
            """
            SELECT DISTINCT device AS device
            FROM device_inventory
            WHERE device IS NOT NULL
            """,
        ]
        for sql in queries:
            try:
                frames.append(pd.read_sql(text(sql), conn))
            except Exception:
                continue
    if not frames:
        return []
    devices = pd.concat(frames, ignore_index=True)["device"].dropna().map(_clean_device_name)
    return sorted({device for device in devices if device})


@st.cache_data(ttl=300)
def load_device_events(start, end, selected_devices):
    if not selected_devices:
        return pd.DataFrame()
    start_ts, end_exclusive = _date_bounds(start, end)
    params = {
        "start_ts": start_ts,
        "end_exclusive": end_exclusive,
        "devices": [_clean_device_name(d) for d in selected_devices],
    }
    sql = text("""
        SELECT pk, dt::timestamp AS dt, user_name, UPPER(TRIM(device)) AS device, med_id, med_desc,
               event_type, qty, beginning_qty, ending_qty, discrepancy_qty
        FROM events
        WHERE dt::timestamp >= :start_ts AND dt::timestamp < :end_exclusive
          AND UPPER(TRIM(device)) IN :devices
        ORDER BY dt::timestamp
    """).bindparams(bindparam("devices", expanding=True))
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params=params)
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    for col in ["qty", "beginning_qty", "ending_qty", "discrepancy_qty"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["user_name", "device", "med_id", "med_desc", "event_type"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df


@st.cache_data(ttl=300)
def load_device_orders(start, end, selected_devices):
    if not selected_devices:
        return pd.DataFrame()
    start_ts, end_exclusive = _date_bounds(start, end)
    params = {
        "start_ts": start_ts,
        "end_exclusive": end_exclusive,
        "devices": [_clean_device_name(d) for d in selected_devices],
    }
    sql = text("""
        SELECT pk, queue_id, priority, dt::timestamp AS dt, med_id, med_desc,
               UPPER(TRIM(destination)) AS destination, user_name, qty
        FROM pharmacy_orders
        WHERE dt::timestamp >= :start_ts AND dt::timestamp < :end_exclusive
          AND UPPER(TRIM(destination)) IN :devices
        ORDER BY dt::timestamp
    """).bindparams(bindparam("devices", expanding=True))
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params=params)
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    for col in ["queue_id", "priority", "med_id", "med_desc", "destination", "user_name"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df


@st.cache_data(ttl=300)
def load_audit_usage(start, end, selected_devices):
    if not selected_devices:
        return pd.DataFrame()
    start_ts, end_exclusive = _date_bounds(start, end)
    params = {
        "start_ts": start_ts,
        "end_exclusive": end_exclusive,
        "devices": [_clean_device_name(d) for d in selected_devices],
    }
    sql = text("""
        SELECT pk, dt::timestamp AS dt, user_name, user_type,
               UPPER(TRIM(station_name)) AS device, med_id, med_desc,
               transaction_type AS event_type, qty, beginning_qty, ending_qty,
               waste_amount, location, drawer_subdrawer_pocket
        FROM audit_transaction_detail_rc
        WHERE dt::timestamp >= :start_ts AND dt::timestamp < :end_exclusive
          AND UPPER(TRIM(station_name)) IN :devices
          AND (
                transaction_type ILIKE '%vend%'
             OR transaction_type ILIKE '%waste%'
             OR transaction_type ILIKE '%remove%'
             OR transaction_type ILIKE '%dispense%'
             OR transaction_type ILIKE '%withdraw%'
          )
        ORDER BY dt::timestamp
    """).bindparams(bindparam("devices", expanding=True))
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params=params)
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    for col in ["qty", "beginning_qty", "ending_qty", "waste_amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["user_name", "user_type", "device", "med_id", "med_desc", "event_type", "location", "drawer_subdrawer_pocket"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["event_date"] = df["dt"].dt.date
    df["hour"] = df["dt"].dt.hour
    df["daypart"] = df["hour"].apply(lambda h: "AM" if pd.notna(h) and h < 12 else "PM")
    df["abs_qty"] = df["qty"].abs()
    waste_qty = df["waste_amount"].abs()
    df.loc[df["abs_qty"].fillna(0).eq(0) & waste_qty.fillna(0).gt(0), "abs_qty"] = waste_qty
    df["is_usage"] = True
    return df


def classify_events(events):
    if events.empty:
        return events
    df = events.copy()
    etype = df["event_type"].str.lower()
    df["event_date"] = df["dt"].dt.date
    df["hour"] = df["dt"].dt.hour
    df["daypart"] = df["hour"].apply(lambda h: "AM" if pd.notna(h) and h < 12 else "PM")
    df["abs_qty"] = df["qty"].abs()
    df["is_refill"] = etype.str.contains(REFILL_PATTERN, regex=True, na=False) & ~etype.str.contains(REFILL_EXCLUDE_PATTERN, regex=True, na=False)
    df["is_usage"] = etype.str.contains(USAGE_PATTERN, regex=True, na=False) & ~etype.str.contains(USAGE_EXCLUDE_PATTERN, regex=True, na=False)
    df["is_zero_event"] = df["ending_qty"].fillna(1).le(0)
    return df


def classify_orders(orders):
    if orders.empty:
        return orders
    df = orders.copy()
    priority = df["priority"].str.lower()
    df["event_date"] = df["dt"].dt.date
    df["hour"] = df["dt"].dt.hour
    df["daypart"] = df["hour"].apply(lambda h: "AM" if pd.notna(h) and h < 12 else "PM")
    df["abs_qty"] = df["qty"].abs()
    df["is_stockout"] = priority.str.contains(STOCKOUT_PATTERN, regex=True, na=False)
    df["is_pyxis_pull"] = priority.str.contains(r"pyxis\s*pull|pyxis.*pull", regex=True, na=False)
    return df


def pct_change(current, prior):
    if pd.isna(prior) or float(prior) == 0:
        return None
    return (float(current) - float(prior)) / abs(float(prior)) * 100


def metric_delta(current, prior):
    change = pct_change(current, prior)
    if change is None:
        return None
    return f"{change:+.1f}% vs prior window"


def build_daily(events, orders, audit_usage):
    days = pd.date_range(start_date, end_date, freq="D").date
    daily = pd.DataFrame({"event_date": days})
    expected_cols = [
        "refill_rows", "refill_qty", "refill_meds",
        "usage_rows", "usage_qty", "usage_meds",
        "zero_inventory_events", "zero_inventory_meds",
        "stockout_orders", "stockout_qty", "stockout_meds",
        "pyxis_pull_rows", "pyxis_pull_qty",
    ]
    if not events.empty:
        refill = events[events["is_refill"]].groupby("event_date").agg(
            refill_rows=("pk", "count"),
            refill_qty=("abs_qty", "sum"),
            refill_meds=("med_id", "nunique"),
        ).reset_index()
        zero = events[events["is_zero_event"]].groupby("event_date").agg(
            zero_inventory_events=("pk", "count"),
            zero_inventory_meds=("med_id", "nunique"),
        ).reset_index()
        for frame in [refill, zero]:
            daily = daily.merge(frame, on="event_date", how="left")
    if not audit_usage.empty:
        usage = audit_usage.groupby("event_date").agg(
            usage_rows=("pk", "count"),
            usage_qty=("abs_qty", "sum"),
            usage_meds=("med_id", "nunique"),
        ).reset_index()
        daily = daily.merge(usage, on="event_date", how="left")
    if not orders.empty:
        stockout = orders[orders["is_stockout"]].groupby("event_date").agg(
            stockout_orders=("pk", "count"),
            stockout_qty=("abs_qty", "sum"),
            stockout_meds=("med_id", "nunique"),
        ).reset_index()
        pulls = orders[orders["is_pyxis_pull"]].groupby("event_date").agg(
            pyxis_pull_rows=("pk", "count"),
            pyxis_pull_qty=("abs_qty", "sum"),
        ).reset_index()
        for frame in [stockout, pulls]:
            daily = daily.merge(frame, on="event_date", how="left")
    for col in expected_cols:
        if col not in daily.columns:
            daily[col] = 0
    numeric_cols = [col for col in daily.columns if col != "event_date"]
    daily[numeric_cols] = daily[numeric_cols].fillna(0)
    daily["event_date"] = pd.to_datetime(daily["event_date"])
    return daily


def build_prior_comparison(selected_devices):
    selected_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days + 1
    prior_end = pd.Timestamp(start_date) - timedelta(days=1)
    prior_start = prior_end - timedelta(days=selected_days - 1)
    prior_events = classify_events(load_device_events(prior_start.date(), prior_end.date(), selected_devices))
    prior_orders = classify_orders(load_device_orders(prior_start.date(), prior_end.date(), selected_devices))
    prior_usage = load_audit_usage(prior_start.date(), prior_end.date(), selected_devices)
    return prior_start.date(), prior_end.date(), prior_events, prior_orders, prior_usage


def summarize_window(events, orders, audit_usage):
    refills = events[events["is_refill"]] if not events.empty else pd.DataFrame()
    usage = audit_usage if not audit_usage.empty else pd.DataFrame()
    zero = events[events["is_zero_event"]] if not events.empty else pd.DataFrame()
    stockouts = orders[orders["is_stockout"]] if not orders.empty else pd.DataFrame()
    pulls = orders[orders["is_pyxis_pull"]] if not orders.empty else pd.DataFrame()
    return {
        "usage_qty": usage["abs_qty"].sum() if not usage.empty else 0,
        "usage_rows": len(usage),
        "refill_qty": refills["abs_qty"].sum() if not refills.empty else 0,
        "refill_rows": len(refills),
        "zero_inventory_events": len(zero),
        "stockout_orders": len(stockouts),
        "pyxis_pull_qty": pulls["abs_qty"].sum() if not pulls.empty else 0,
        "pyxis_pull_rows": len(pulls),
    }


def build_hourly_usage(audit_usage):
    if audit_usage.empty:
        return pd.DataFrame()
    usage = audit_usage.copy()
    if usage.empty:
        return pd.DataFrame()
    return usage.groupby(["device", "hour"]).agg(
        usage_events=("pk", "count"),
        usage_qty=("abs_qty", "sum"),
        unique_meds=("med_id", "nunique"),
        last_usage=("dt", "max"),
    ).reset_index().sort_values(["device", "hour"])


def build_refill_time_suggestions(events, orders, audit_usage):
    rows = []
    for device in selected_devices:
        dev_events = events[events["device"] == device].copy() if not events.empty else pd.DataFrame()
        dev_orders = orders[orders["destination"] == device].copy() if not orders.empty else pd.DataFrame()
        dev_usage = audit_usage[audit_usage["device"] == device].copy() if not audit_usage.empty else pd.DataFrame()

        usage_hour = pd.DataFrame()
        if not dev_usage.empty:
            usage_hour = dev_usage.groupby("hour").agg(
                usage_events=("pk", "count"),
                usage_qty=("abs_qty", "sum"),
            ).reset_index()

        zero_hour = pd.DataFrame()
        if not dev_events.empty:
            zero_hour = dev_events[dev_events["is_zero_event"]].groupby("hour").agg(
                zero_inventory_events=("pk", "count"),
            ).reset_index()

        stock_hour = pd.DataFrame()
        if not dev_orders.empty:
            stock_hour = dev_orders[dev_orders["is_stockout"]].groupby("hour").agg(
                stockout_orders=("pk", "count"),
            ).reset_index()

        hourly = pd.DataFrame({"hour": list(range(24))})
        for frame in [usage_hour, zero_hour, stock_hour]:
            if not frame.empty:
                hourly = hourly.merge(frame, on="hour", how="left")
        for col in ["usage_events", "usage_qty", "zero_inventory_events", "stockout_orders"]:
            if col not in hourly.columns:
                hourly[col] = 0
        hourly = hourly.fillna(0)
        hourly["pressure_score"] = (
            hourly["usage_qty"].astype(float)
            + hourly["usage_events"].astype(float)
            + hourly["stockout_orders"].astype(float) * 8
        )

        pressure = hourly[hourly["pressure_score"] > 0].copy()
        if pressure.empty:
            verified_zero_total = int(hourly["zero_inventory_events"].sum())
            rows.append({
                "device": device,
                "suggested_second_refill": "No extra refill signal",
                "peak_pressure_hour": "",
                "pressure_score": 0,
                "usage_qty": 0,
                "stockout_orders": 0,
                "verified_zero_events": verified_zero_total,
                "current_refill_pattern": "No refill/load rows found",
                "rationale": "No Audit Detail usage or stockout-order pressure was found. Verified-zero rows are context only.",
            })
            continue

        refill_hours = set()
        refill_times = []
        current_refill_pattern = "No refill/load rows found"
        if not dev_events.empty:
            refills = dev_events[dev_events["is_refill"]].copy()
            if not refills.empty:
                refill_counts = refills.groupby("hour").size().sort_values(ascending=False)
                refill_hours = {int(hour) for hour in refill_counts.index}
                refill_times = [f"{int(hour):02d}:00 ({int(count)})" for hour, count in refill_counts.head(3).items()]
                current_refill_pattern = ", ".join(refill_times)

        pm_pressure = pressure[pressure["hour"] >= 10]
        target_pool = pm_pressure if not pm_pressure.empty else pressure
        if refill_hours:
            target_pool = target_pool[~target_pool["hour"].astype(int).isin(refill_hours)]
        if target_pool.empty:
            peak = pressure.sort_values(["pressure_score", "stockout_orders", "usage_qty"], ascending=False).iloc[0]
            suggested_label = "Already covered"
            rationale_prefix = "The strongest usage/stockout pressure falls inside the current refill pattern"
        else:
            peak = target_pool.sort_values(["pressure_score", "stockout_orders", "usage_qty"], ascending=False).iloc[0]
            suggested_hour = max(int(peak["hour"]) - 1, 0)
            overlaps_refill_window = bool(refill_hours) and any(abs(suggested_hour - hour) <= 1 for hour in refill_hours)
            if overlaps_refill_window:
                suggested_label = "Already covered"
                rationale_prefix = "The suggested hour overlaps the current refill window"
            else:
                suggested_label = f"{suggested_hour:02d}:00"
                rationale_prefix = "Candidate second refill time"

        pressure_reasons = []
        if peak["stockout_orders"] > 0:
            pressure_reasons.append(f"{int(peak['stockout_orders'])} stockout order(s)")
        if peak["usage_qty"] > 0:
            pressure_reasons.append(f"{peak['usage_qty']:.0f} usage qty")
        if not pressure_reasons:
            pressure_reasons.append("usage/stockout pressure")
        verified_zero_total = int(hourly["zero_inventory_events"].sum())

        rows.append({
            "device": device,
            "suggested_second_refill": suggested_label,
            "peak_pressure_hour": f"{int(peak['hour']):02d}:00",
            "pressure_score": peak["pressure_score"],
            "usage_qty": peak["usage_qty"],
            "stockout_orders": peak["stockout_orders"],
            "verified_zero_events": verified_zero_total,
            "current_refill_pattern": current_refill_pattern,
            "rationale": (
                f"{rationale_prefix}: peak usage/stockout pressure around {int(peak['hour']):02d}:00 "
                f"from {', '.join(pressure_reasons)}. Verified-zero rows ({verified_zero_total}) were not scored."
            ),
        })

    return pd.DataFrame(rows)


candidate_devices = load_device_candidates()
default_devices = [device for device in DEFAULT_CATHLAB_DEVICES if device in candidate_devices]
if not default_devices:
    default_devices = [
        device for device in candidate_devices
        if "CATH" in device and (device.endswith("8") or device.endswith("12") or "L8" in device or "L12" in device)
    ][:2]

with st.expander("Device selection", expanded=True):
    c1, c2 = st.columns([1, 2])
    with c1:
        search_text = st.text_input(
            "Filter device dropdown",
            value="",
            help="Optional. Leave blank to show every device in the dropdown.",
            key="device_utilization_search",
        ).strip().upper()
    device_options = [d for d in candidate_devices if not search_text or search_text in d]
    if not device_options and candidate_devices:
        device_options = candidate_devices
        st.warning("No devices matched that filter, so the full device list is shown.")
    with c2:
        selected_devices = st.multiselect(
            "Devices",
            options=device_options,
            default=[d for d in default_devices if d in device_options],
            key="device_utilization_devices",
        )
    st.caption("The dropdown includes every device found in Events, Pharmacy Orders, or Device Inventory.")

if not selected_devices:
    st.warning("Choose at least one device to analyze.")
    st.stop()

events = classify_events(load_device_events(start_date, end_date, selected_devices))
orders = classify_orders(load_device_orders(start_date, end_date, selected_devices))
audit_usage = load_audit_usage(start_date, end_date, selected_devices)
daily = build_daily(events, orders, audit_usage)
prior_start, prior_end, prior_events, prior_orders, prior_usage = build_prior_comparison(selected_devices)
current_summary = summarize_window(events, orders, audit_usage)
prior_summary = summarize_window(prior_events, prior_orders, prior_usage)

if events.empty and orders.empty and audit_usage.empty:
    st.info("No event or pharmacy order activity found for the selected devices and date range.")
    st.stop()

st.markdown(f"**Selected devices:** {', '.join(selected_devices)}")
st.caption(f"Prior comparison window: {prior_start:%m/%d/%Y} through {prior_end:%m/%d/%Y}")

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Usage Qty", f"{current_summary['usage_qty']:,.0f}", delta=metric_delta(current_summary["usage_qty"], prior_summary["usage_qty"]))
m2.metric("Refill Qty", f"{current_summary['refill_qty']:,.0f}", delta=metric_delta(current_summary["refill_qty"], prior_summary["refill_qty"]))
m3.metric("Stockout Orders", f"{current_summary['stockout_orders']:,.0f}", delta=metric_delta(current_summary["stockout_orders"], prior_summary["stockout_orders"]))
m4.metric("Zero Inventory Events", f"{current_summary['zero_inventory_events']:,.0f}", delta=metric_delta(current_summary["zero_inventory_events"], prior_summary["zero_inventory_events"]))
m5.metric("Pyxis Pull Qty", f"{current_summary['pyxis_pull_qty']:,.0f}", delta=metric_delta(current_summary["pyxis_pull_qty"], prior_summary["pyxis_pull_qty"]))

stockout_up = current_summary["stockout_orders"] > prior_summary["stockout_orders"]
refill_up = current_summary["refill_qty"] > prior_summary["refill_qty"]
usage_up = current_summary["usage_qty"] > prior_summary["usage_qty"]
if stockout_up and (refill_up or usage_up):
    st.warning(
        "Twice-daily refill signal: stockout pressure is higher than the prior comparable window, "
        "and either refill volume or usage is also higher."
    )
elif refill_up or usage_up:
    st.info("Demand signal: refill volume or usage is higher than the prior comparable window. Review AM/PM timing before changing cadence.")
else:
    st.success("No clear demand increase versus the prior comparable window for the selected devices.")

tab_trend, tab_usage, tab_refill_times, tab_daypart, tab_stockout, tab_meds, tab_raw = st.tabs([
    "Daily Trend",
    "Usage",
    "Optimal Refill Times",
    "AM / PM Split",
    "Stockout Pressure",
    "Medication Detail",
    "Raw Data",
])

with tab_trend:
    trend_cols = ["usage_qty", "refill_qty", "stockout_orders", "zero_inventory_events", "pyxis_pull_qty"]
    trend = daily.melt("event_date", value_vars=trend_cols, var_name="measure", value_name="value")
    trend["measure"] = trend["measure"].map({
        "usage_qty": "Usage Qty",
        "refill_qty": "Refill Qty",
        "stockout_orders": "Stockout Orders",
        "zero_inventory_events": "Zero Inventory Events",
        "pyxis_pull_qty": "Pyxis Pull Qty",
    })
    st.plotly_chart(px.line(trend, x="event_date", y="value", color="measure", markers=True), width="stretch")
    st.dataframe(
        daily.sort_values("event_date", ascending=False),
        width="stretch",
        hide_index=True,
        column_config={
            "event_date": st.column_config.DateColumn("Date", format="MM/DD/YYYY"),
            "usage_qty": st.column_config.NumberColumn("Usage Qty", format="%.0f"),
            "refill_qty": st.column_config.NumberColumn("Refill Qty", format="%.0f"),
            "stockout_orders": st.column_config.NumberColumn("Stockout Orders", format="%d"),
            "zero_inventory_events": st.column_config.NumberColumn("Zero Inventory Events", format="%d"),
            "pyxis_pull_qty": st.column_config.NumberColumn("Pyxis Pull Qty", format="%.0f"),
        },
    )

with tab_usage:
    usage_events = audit_usage.copy()
    if usage_events.empty:
        st.info("No Audit Transaction Detail vend/waste/remove rows found for the selected devices and dates.")
    else:
        st.caption("Usage comes from Audit Transaction Detail RC, using station name as the device.")
        usage_by_hour = build_hourly_usage(audit_usage)
        usage_by_med = usage_events.groupby(["device", "med_id", "med_desc"]).agg(
            usage_events=("pk", "count"),
            usage_qty=("abs_qty", "sum"),
            first_usage=("dt", "min"),
            last_usage=("dt", "max"),
        ).reset_index().sort_values(["usage_qty", "usage_events"], ascending=False)
        usage_by_day = usage_events.groupby(["device", "event_date"]).agg(
            usage_events=("pk", "count"),
            usage_qty=("abs_qty", "sum"),
            unique_meds=("med_id", "nunique"),
        ).reset_index()
        st.plotly_chart(
            px.bar(usage_by_hour, x="hour", y="usage_qty", color="device", barmode="group"),
            width="stretch",
        )
        u1, u2 = st.columns(2)
        with u1:
            st.subheader("Usage by Medication")
            st.dataframe(usage_by_med, width="stretch", hide_index=True)
        with u2:
            st.subheader("Daily Usage")
            st.dataframe(usage_by_day.sort_values(["event_date", "device"], ascending=[False, True]), width="stretch", hide_index=True)

with tab_refill_times:
    suggestions = build_refill_time_suggestions(events, orders, audit_usage)
    if suggestions.empty:
        st.info("Not enough usage or stockout data to suggest refill timing.")
    else:
        st.caption("Suggestion is one hour before the highest Audit Detail usage or stockout-order pressure outside the current refill window. Verified-zero rows are shown as context only.")
        st.dataframe(
            suggestions,
            width="stretch",
            hide_index=True,
            column_config={
                "device": st.column_config.TextColumn("Device"),
                "suggested_second_refill": st.column_config.TextColumn("Suggested Second Refill"),
                "peak_pressure_hour": st.column_config.TextColumn("Peak Pressure Hour"),
                "pressure_score": st.column_config.NumberColumn("Pressure Score", format="%.1f"),
                "usage_qty": st.column_config.NumberColumn("Usage Qty At Peak", format="%.0f"),
                "stockout_orders": st.column_config.NumberColumn("Stockouts At Peak", format="%.0f"),
                "verified_zero_events": st.column_config.NumberColumn("Verified Zero Rows", format="%.0f"),
                "current_refill_pattern": st.column_config.TextColumn("Current Refill Pattern"),
                "rationale": st.column_config.TextColumn("Why"),
            },
        )
        pressure_detail = []
        if not audit_usage.empty:
            usage_detail = audit_usage.copy()
            usage_detail["pressure_type"] = "Audit usage"
            pressure_detail.append(usage_detail[["dt", "device", "hour", "pressure_type", "med_id", "med_desc", "event_type", "abs_qty", "ending_qty"]])
        if not events.empty:
            pressure_events = events[events["is_zero_event"]].copy()
            pressure_events["pressure_type"] = "Zero inventory"
            pressure_detail.append(pressure_events[["dt", "device", "hour", "pressure_type", "med_id", "med_desc", "event_type", "abs_qty", "ending_qty"]])
        if not orders.empty:
            stock_events = orders[orders["is_stockout"]].copy()
            if not stock_events.empty:
                stock_events = stock_events.rename(columns={"destination": "device"})
                stock_events["pressure_type"] = "Stockout order"
                stock_events["event_type"] = stock_events["priority"]
                stock_events["ending_qty"] = pd.NA
                pressure_detail.append(stock_events[["dt", "device", "hour", "pressure_type", "med_id", "med_desc", "event_type", "abs_qty", "ending_qty"]])
        if pressure_detail:
            detail = pd.concat(pressure_detail, ignore_index=True).sort_values("dt", ascending=False)
            with st.expander("Rows behind the timing suggestion", expanded=False):
                st.dataframe(detail, width="stretch", hide_index=True)

with tab_daypart:
    frames = []
    if not events.empty:
        frames.append(events[events["is_refill"]].groupby(["device", "daypart"]).agg(refill_rows=("pk", "count"), refill_qty=("abs_qty", "sum")).reset_index())
    if not audit_usage.empty:
        frames.append(audit_usage.groupby(["device", "daypart"]).agg(usage_rows=("pk", "count"), usage_qty=("abs_qty", "sum")).reset_index())
    if not orders.empty:
        frames.append(
            orders[orders["is_stockout"]].groupby(["destination", "daypart"]).agg(
                stockout_orders=("pk", "count"),
                stockout_qty=("abs_qty", "sum"),
            ).reset_index().rename(columns={"destination": "device"})
        )
    frames = [frame for frame in frames if not frame.empty]
    if frames:
        daypart = frames[0]
        for frame in frames[1:]:
            daypart = daypart.merge(frame, on=["device", "daypart"], how="outer")
        daypart = daypart.fillna(0)
        value_cols = [col for col in ["usage_qty", "refill_qty", "stockout_orders"] if col in daypart.columns]
        chart_data = daypart.melt(["device", "daypart"], value_vars=value_cols, var_name="measure", value_name="value")
        st.plotly_chart(px.bar(chart_data, x="daypart", y="value", color="measure", facet_col="device", barmode="group"), width="stretch")
        st.dataframe(daypart, width="stretch", hide_index=True)
    else:
        st.info("No refill, usage, or stockout daypart activity found.")

with tab_stockout:
    stockout_orders = orders[orders["is_stockout"]].copy() if not orders.empty else pd.DataFrame()
    zero_events = events[events["is_zero_event"]].copy() if not events.empty else pd.DataFrame()
    if stockout_orders.empty and zero_events.empty:
        st.success("No stockout orders or zero-ending-inventory events found for this window.")
    else:
        if not stockout_orders.empty:
            st.subheader("Stockout Orders")
            stockout_summary = stockout_orders.groupby(["destination", "med_id", "med_desc"]).agg(
                stockout_orders=("pk", "count"),
                stockout_qty=("abs_qty", "sum"),
                first_stockout=("dt", "min"),
                last_stockout=("dt", "max"),
            ).reset_index().sort_values(["stockout_orders", "stockout_qty"], ascending=False)
            st.dataframe(stockout_summary, width="stretch", hide_index=True)
        if not zero_events.empty:
            st.subheader("Zero Ending Inventory Events")
            zero_summary = zero_events.groupby(["device", "med_id", "med_desc"]).agg(
                zero_events=("pk", "count"),
                first_zero=("dt", "min"),
                last_zero=("dt", "max"),
                last_ending_qty=("ending_qty", "last"),
            ).reset_index().sort_values(["zero_events", "last_zero"], ascending=False)
            st.dataframe(zero_summary, width="stretch", hide_index=True)

with tab_meds:
    med_frames = []
    if not events.empty:
        med_frames.append(events[events["is_refill"]].groupby(["device", "med_id", "med_desc"]).agg(refill_rows=("pk", "count"), refill_qty=("abs_qty", "sum"), last_refill=("dt", "max")).reset_index())
    if not audit_usage.empty:
        med_frames.append(audit_usage.groupby(["device", "med_id", "med_desc"]).agg(usage_rows=("pk", "count"), usage_qty=("abs_qty", "sum"), last_usage=("dt", "max")).reset_index())
    if not orders.empty:
        med_frames.append(
            orders[orders["is_stockout"]].groupby(["destination", "med_id", "med_desc"]).agg(
                stockout_orders=("pk", "count"),
                stockout_qty=("abs_qty", "sum"),
                last_stockout_order=("dt", "max"),
            ).reset_index().rename(columns={"destination": "device"})
        )
    med_frames = [frame for frame in med_frames if not frame.empty]
    if med_frames:
        med_detail = med_frames[0]
        for frame in med_frames[1:]:
            med_detail = med_detail.merge(frame, on=["device", "med_id", "med_desc"], how="outer")
        med_detail = med_detail.fillna({
            "refill_rows": 0,
            "refill_qty": 0,
            "usage_rows": 0,
            "usage_qty": 0,
            "stockout_orders": 0,
            "stockout_qty": 0,
        })
        sort_cols = [col for col in ["stockout_orders", "usage_qty", "refill_qty"] if col in med_detail.columns]
        med_detail = med_detail.sort_values(sort_cols, ascending=False)
        st.dataframe(med_detail, width="stretch", hide_index=True)
        st.download_button(
            "Download medication detail",
            data=med_detail.to_csv(index=False).encode("utf-8"),
            file_name="device_utilization_med_detail.csv",
            mime="text/csv",
        )
    else:
        st.info("No medication-level detail found for the selected devices.")

with tab_raw:
    raw_choice = st.segmented_control(
        "Raw table",
        ["Audit Usage", "Inventory Events", "Pharmacy Orders"],
        default="Audit Usage",
        key="device_utilization_raw_choice",
    )
    if raw_choice == "Audit Usage":
        if audit_usage.empty:
            st.info("No Audit Transaction Detail usage rows found for the selected devices.")
        else:
            st.dataframe(audit_usage.sort_values("dt", ascending=False), width="stretch", hide_index=True)
    elif raw_choice == "Inventory Events":
        if events.empty:
            st.info("No inventory events found for the selected devices.")
        else:
            st.dataframe(events.sort_values("dt", ascending=False), width="stretch", hide_index=True)
    else:
        if orders.empty:
            st.info("No pharmacy orders found for the selected devices.")
        else:
            st.dataframe(orders.sort_values("dt", ascending=False), width="stretch", hide_index=True)
