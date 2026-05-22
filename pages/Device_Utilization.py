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
CLINICAL_USER_PATTERN = r"nurse|anesthesia|respiratory|physician|provider"


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


@st.cache_data(ttl=300)
def load_device_inventory_current(selected_devices):
    if not selected_devices:
        return pd.DataFrame()
    params = {"devices": [_clean_device_name(d) for d in selected_devices]}
    sql = text("""
        SELECT
            UPPER(TRIM(device)) AS device,
            UPPER(TRIM(med_id)) AS med_id,
            med_desc,
            pocket_location,
            status,
            current_quantity,
            min_qty,
            max_qty,
            standard_stock,
            outdate_tracking,
            days_unused,
            snapshot_dt
        FROM device_inventory
        WHERE UPPER(TRIM(device)) IN :devices
    """).bindparams(bindparam("devices", expanding=True))
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params=params)
    if df.empty:
        return df
    for col in ["current_quantity", "min_qty", "max_qty", "days_unused"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["snapshot_dt"] = pd.to_datetime(df["snapshot_dt"], errors="coerce")
    for col in ["device", "med_id", "med_desc", "pocket_location", "status", "standard_stock", "outdate_tracking"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
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


def build_zero_gap_analysis(events, audit_usage):
    if audit_usage.empty:
        return pd.DataFrame()
    event_text = audit_usage["event_type"].fillna("").astype(str)
    removal_mask = event_text.str.contains(r"vend|remove|dispense|withdraw", case=False, regex=True, na=False)
    clinical_zero = audit_usage[
        audit_usage["ending_qty"].fillna(1).le(0)
        & audit_usage["user_type"].str.contains(CLINICAL_USER_PATTERN, case=False, regex=True, na=False)
        & removal_mask
        & audit_usage["abs_qty"].fillna(0).gt(0)
    ].copy()
    if clinical_zero.empty:
        return pd.DataFrame()

    pharmacy_verify_zero = pd.DataFrame()
    pharmacy_refills = pd.DataFrame()
    if not events.empty:
        verify_mask = events["event_type"].str.contains(r"verify|count|inventory", case=False, regex=True, na=False)
        refill_mask = events["is_refill"]
        pharmacy_verify_zero = events[events["ending_qty"].fillna(1).le(0) & verify_mask].copy()
        pharmacy_refills = events[refill_mask].copy()

    rows = []
    for _, zero_row in clinical_zero.sort_values("dt").iterrows():
        verify_matches = pd.DataFrame()
        if not pharmacy_verify_zero.empty:
            verify_matches = pharmacy_verify_zero[
                (pharmacy_verify_zero["device"] == zero_row["device"])
                & (pharmacy_verify_zero["med_id"] == zero_row["med_id"])
                & (pharmacy_verify_zero["dt"] >= zero_row["dt"])
            ].sort_values("dt")
        refill_matches = pd.DataFrame()
        if not pharmacy_refills.empty:
            refill_matches = pharmacy_refills[
                (pharmacy_refills["device"] == zero_row["device"])
                & (pharmacy_refills["med_id"] == zero_row["med_id"])
                & (pharmacy_refills["dt"] >= zero_row["dt"])
            ].sort_values("dt")
        next_verify = verify_matches.iloc[0] if not verify_matches.empty else None
        next_refill = refill_matches.iloc[0] if not refill_matches.empty else None
        verify_gap_hours = None
        refill_gap_hours = None
        if next_verify is not None:
            verify_gap_hours = (next_verify["dt"] - zero_row["dt"]).total_seconds() / 3600.0
        if next_refill is not None:
            refill_gap_hours = (next_refill["dt"] - zero_row["dt"]).total_seconds() / 3600.0
        if next_refill is not None:
            gap_status = "Restocked after clinical zero"
        elif next_verify is not None:
            gap_status = "Verified zero only, no later refill found"
        else:
            gap_status = "No later verify/refill found"
        rows.append({
            "device": zero_row["device"],
            "med_id": zero_row["med_id"],
            "med_desc": zero_row["med_desc"],
            "clinical_zero_time": zero_row["dt"],
            "clinical_user": zero_row["user_name"],
            "clinical_user_type": zero_row["user_type"],
            "clinical_event": zero_row["event_type"],
            "clinical_qty": zero_row["abs_qty"],
            "next_staff_verify_zero": next_verify["dt"] if next_verify is not None else pd.NaT,
            "verify_user": next_verify["user_name"] if next_verify is not None else "",
            "verify_event": next_verify["event_type"] if next_verify is not None else "",
            "verify_gap_hours": verify_gap_hours,
            "next_refill": next_refill["dt"] if next_refill is not None else pd.NaT,
            "refill_user": next_refill["user_name"] if next_refill is not None else "",
            "refill_event": next_refill["event_type"] if next_refill is not None else "",
            "refill_gap_hours": refill_gap_hours,
            "gap_hours": refill_gap_hours,
            "gap_status": gap_status,
        })
    return pd.DataFrame(rows).sort_values(["refill_gap_hours", "verify_gap_hours", "clinical_zero_time"], ascending=[False, False, False])


def _is_blocked_hour(hour, blocked_start, blocked_end):
    if blocked_start == blocked_end:
        return False
    if blocked_start < blocked_end:
        return blocked_start <= hour < blocked_end
    return hour >= blocked_start or hour < blocked_end


def _outside_blocked_hours(hours, blocked_start, blocked_end):
    if not hours:
        return set()
    return {hour for hour in hours if not _is_blocked_hour(int(hour), blocked_start, blocked_end)}


def _suggest_reachable_refill_label(pressure_hour, blocked_start, blocked_end):
    pressure_hour = int(pressure_hour)
    if _is_blocked_hour(pressure_hour, blocked_start, blocked_end):
        pre_hour = max(int(blocked_start) - 1, 0)
        post_hour = int(blocked_end) % 24
        return f"{pre_hour:02d}:00 top-off or {post_hour:02d}:00 post-case"
    return f"{max(pressure_hour - 1, 0):02d}:00"


def _hour_label(hour):
    if pd.isna(hour):
        return ""
    return f"{int(hour) % 24:02d}:00"


def _hours_between(start_hour, end_hour):
    return (int(end_hour) - int(start_hour)) % 24


def _first_hour_from_label(label):
    text_value = str(label or "")
    for token in text_value.replace("/", " ").split():
        if len(token) >= 5 and token[:2].isdigit() and token[2:3] == ":":
            return int(token[:2])
    return None


def _case_window_label(hour, blocked_start, blocked_end):
    if pd.isna(hour):
        return "Unknown"
    hour = int(hour)
    if _is_blocked_hour(hour, blocked_start, blocked_end):
        return "During cases"
    if blocked_start < blocked_end:
        return "Pre-case" if hour < blocked_start else "Post-case"
    return "Open access"


def build_refill_time_suggestions(events, orders, audit_usage, blocked_start, blocked_end):
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

        accessible_pressure = pressure[
            ~pressure["hour"].astype(int).apply(lambda hour: _is_blocked_hour(hour, blocked_start, blocked_end))
        ].copy()
        target_pool = accessible_pressure if not accessible_pressure.empty else pressure
        if refill_hours:
            target_pool = target_pool[~target_pool["hour"].astype(int).isin(refill_hours)]
        if target_pool.empty:
            peak = pressure.sort_values(["pressure_score", "stockout_orders", "usage_qty"], ascending=False).iloc[0]
            suggested_label = "Already covered"
            rationale_prefix = "The strongest usage/stockout pressure falls inside the current refill pattern"
        else:
            peak = target_pool.sort_values(["pressure_score", "stockout_orders", "usage_qty"], ascending=False).iloc[0]
            suggested_label = _suggest_reachable_refill_label(peak["hour"], blocked_start, blocked_end)
            suggested_hours = {
                int(part[:2])
                for part in suggested_label.split()
                if len(part) >= 5 and part[:2].isdigit() and part[2:3] == ":"
            }
            accessible_suggested_hours = _outside_blocked_hours(suggested_hours, blocked_start, blocked_end)
            overlaps_refill_window = bool(refill_hours) and any(
                abs(suggested_hour - refill_hour) <= 1
                for suggested_hour in accessible_suggested_hours
                for refill_hour in refill_hours
            )
            if overlaps_refill_window:
                suggested_label = "Already covered"
                rationale_prefix = "The suggested hour overlaps the current refill window"
            else:
                rationale_prefix = "Candidate reachable refill window"

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
                f"from {', '.join(pressure_reasons)}. Room access limited {int(blocked_start):02d}:00-"
                f"{int(blocked_end):02d}:00. Verified-zero rows ({verified_zero_total}) were not scored."
            ),
        })

    return pd.DataFrame(rows)


def build_refill_workflow_plan(
    suggestions,
    pull_lead_hours,
    overnight_pull_hour,
    prior_night_pull_hour,
    max_staged_hours,
):
    rows = []
    if suggestions.empty:
        return pd.DataFrame()

    blocked_hours = {
        int(overnight_pull_hour - 1) % 24,
        int(overnight_pull_hour) % 24,
        int(overnight_pull_hour + 1) % 24,
    }
    for _, row in suggestions.iterrows():
        suggested = str(row.get("suggested_second_refill") or "")
        delivery_hour = _first_hour_from_label(suggested)
        if delivery_hour is None or suggested in {"Already covered", "No extra refill signal"}:
            rows.append({
                "device": row.get("device"),
                "suggested_delivery": suggested,
                "prep_plan": "No added delivery plan",
                "pull_check_start": "",
                "staged_hours": None,
                "workflow_risk": "No extra refill timing selected",
                "workflow_note": "Timing signal does not currently require an added pull/check/delivery workflow.",
            })
            continue

        same_day_pull_hour = int(delivery_hour - pull_lead_hours) % 24
        same_day_gap = _hours_between(same_day_pull_hour, delivery_hour)
        conflicts_with_big_pull = same_day_pull_hour in blocked_hours
        if conflicts_with_big_pull:
            pull_hour = int(prior_night_pull_hour) % 24
            staged_hours = _hours_between(pull_hour, delivery_hour)
            prep_plan = "Night-before pull/check"
            if staged_hours > max_staged_hours:
                workflow_risk = "Staging gap too long"
                note = (
                    f"{_hour_label(delivery_hour)} delivery would need a prior-night pull around "
                    f"{_hour_label(pull_hour)}, leaving meds staged about {staged_hours:.0f} hours. "
                    f"That is over the {max_staged_hours:.0f} hour review limit."
                )
            else:
                workflow_risk = "Review staging controls"
                note = (
                    f"{_hour_label(delivery_hour)} delivery avoids room access problems, but the pull/check work "
                    f"would likely need to happen around {_hour_label(pull_hour)} before the 04:00 carousel pull. "
                    f"Estimated staged time is about {staged_hours:.0f} hours."
                )
        else:
            pull_hour = same_day_pull_hour
            staged_hours = same_day_gap
            prep_plan = "Same-shift pull/check"
            workflow_risk = "Operationally reachable"
            note = (
                f"{_hour_label(delivery_hour)} delivery can be supported by starting the carousel pull/check "
                f"around {_hour_label(pull_hour)} with about {staged_hours:.0f} hours lead time."
            )

        rows.append({
            "device": row.get("device"),
            "suggested_delivery": _hour_label(delivery_hour),
            "prep_plan": prep_plan,
            "pull_check_start": _hour_label(pull_hour),
            "staged_hours": staged_hours,
            "workflow_risk": workflow_risk,
            "workflow_note": note,
        })

    return pd.DataFrame(rows)


def build_refill_delivery_profile(events):
    if events.empty or "is_refill" not in events.columns:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    refills = events[events["is_refill"]].copy()
    if refills.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    refills["time_minutes"] = refills["dt"].dt.hour.mul(60).add(refills["dt"].dt.minute)
    refills["delivery_hour"] = refills["dt"].dt.hour
    refills["delivery_time"] = refills["dt"].dt.strftime("%H:%M")
    refills["event_day"] = refills["dt"].dt.date

    def avg_clock_time(series):
        clean = pd.to_numeric(series, errors="coerce").dropna()
        if clean.empty:
            return ""
        minutes = int(round(float(clean.mean()))) % (24 * 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    summary = refills.groupby("device").agg(
        refill_events=("pk", "count"),
        refill_days=("event_day", "nunique"),
        average_delivery_time=("time_minutes", avg_clock_time),
        median_delivery_time=("time_minutes", lambda s: avg_clock_time(pd.Series([pd.to_numeric(s, errors="coerce").median()]))),
        earliest_delivery=("delivery_time", "min"),
        latest_delivery=("delivery_time", "max"),
        refill_qty=("abs_qty", "sum"),
        unique_meds=("med_id", "nunique"),
    ).reset_index()

    by_hour = refills.groupby(["device", "delivery_hour"]).agg(
        refill_events=("pk", "count"),
        refill_qty=("abs_qty", "sum"),
        unique_meds=("med_id", "nunique"),
    ).reset_index().sort_values(["device", "delivery_hour"])
    by_hour["delivery_hour_label"] = by_hour["delivery_hour"].apply(_hour_label)

    by_user = refills.groupby(["device", "user_name"]).agg(
        refill_events=("pk", "count"),
        refill_qty=("abs_qty", "sum"),
        unique_meds=("med_id", "nunique"),
        average_delivery_time=("time_minutes", avg_clock_time),
        first_delivery=("dt", "min"),
        last_delivery=("dt", "max"),
    ).reset_index().sort_values(["device", "refill_events", "refill_qty"], ascending=[True, False, False])

    return summary, by_hour, by_user


def build_case_window_summary(events, orders, audit_usage, blocked_start, blocked_end):
    frames = []
    if not audit_usage.empty:
        usage = audit_usage.copy()
        usage["case_window"] = usage["hour"].apply(lambda h: _case_window_label(h, blocked_start, blocked_end))
        frames.append(usage.groupby(["device", "case_window"]).agg(
            usage_events=("pk", "count"),
            usage_qty=("abs_qty", "sum"),
            usage_meds=("med_id", "nunique"),
        ).reset_index())
    if not events.empty:
        refills = events[events["is_refill"]].copy()
        if not refills.empty:
            refills["case_window"] = refills["hour"].apply(lambda h: _case_window_label(h, blocked_start, blocked_end))
            frames.append(refills.groupby(["device", "case_window"]).agg(
                refill_rows=("pk", "count"),
                refill_qty=("abs_qty", "sum"),
            ).reset_index())
    if not orders.empty:
        stock = orders[orders["is_stockout"]].copy()
        if not stock.empty:
            stock["case_window"] = stock["hour"].apply(lambda h: _case_window_label(h, blocked_start, blocked_end))
            frames.append(stock.groupby(["destination", "case_window"]).agg(
                stockout_orders=("pk", "count"),
                stockout_qty=("abs_qty", "sum"),
            ).reset_index().rename(columns={"destination": "device"}))
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    summary = frames[0]
    for frame in frames[1:]:
        summary = summary.merge(frame, on=["device", "case_window"], how="outer")
    return summary.fillna(0)


def build_med_signal_detail(events, orders, audit_usage, inventory, gap_df):
    frames = []
    if not audit_usage.empty:
        frames.append(audit_usage.groupby(["device", "med_id", "med_desc"]).agg(
            usage_events=("pk", "count"),
            usage_qty=("abs_qty", "sum"),
            last_usage=("dt", "max"),
        ).reset_index())
    if not events.empty:
        refills = events[events["is_refill"]].copy()
        if not refills.empty:
            frames.append(refills.groupby(["device", "med_id", "med_desc"]).agg(
                refill_rows=("pk", "count"),
                refill_qty=("abs_qty", "sum"),
                last_refill=("dt", "max"),
            ).reset_index())
    if not orders.empty:
        stock = orders[orders["is_stockout"]].copy()
        if not stock.empty:
            frames.append(stock.groupby(["destination", "med_id", "med_desc"]).agg(
                stockout_orders=("pk", "count"),
                stockout_qty=("abs_qty", "sum"),
                last_stockout_order=("dt", "max"),
            ).reset_index().rename(columns={"destination": "device"}))
    if not gap_df.empty:
        frames.append(gap_df.groupby(["device", "med_id", "med_desc"]).agg(
            clinical_zero_events=("clinical_zero_time", "count"),
            max_zero_gap_hours=("refill_gap_hours", "max"),
            median_zero_gap_hours=("refill_gap_hours", "median"),
        ).reset_index())
    inv_summary = pd.DataFrame()
    if not inventory.empty:
        inv_cols = [
            "device", "med_id", "med_desc", "current_quantity", "min_qty", "max_qty",
            "standard_stock", "days_unused", "pocket_location", "status", "outdate_tracking",
        ]
        inv_summary = inventory.sort_values("snapshot_dt").groupby(["device", "med_id"], as_index=False).tail(1)[inv_cols]
    if not frames:
        if inv_summary.empty:
            return pd.DataFrame()
        detail = inv_summary.copy()
    else:
        detail = frames[0]
        for frame in frames[1:]:
            detail = detail.merge(frame, on=["device", "med_id", "med_desc"], how="outer")
        if not inv_summary.empty:
            detail = detail.merge(inv_summary.drop(columns=["med_desc"]), on=["device", "med_id"], how="left")
    for col in [
        "usage_events", "usage_qty", "refill_rows", "refill_qty", "stockout_orders",
        "stockout_qty", "clinical_zero_events", "max_zero_gap_hours", "median_zero_gap_hours",
        "current_quantity", "min_qty", "max_qty", "days_unused",
    ]:
        if col not in detail.columns:
            detail[col] = 0
        detail[col] = pd.to_numeric(detail[col], errors="coerce").fillna(0)
    detail["problem_score"] = (
        detail["stockout_orders"] * 10
        + detail["clinical_zero_events"] * 6
        + detail["usage_events"]
        + detail["usage_qty"] * 0.2
        + detail["max_zero_gap_hours"].fillna(0).clip(lower=0) * 0.5
    )
    return detail.sort_values(["problem_score", "stockout_orders", "clinical_zero_events", "usage_qty"], ascending=False)


def build_inventory_crosscheck(audit_usage, inventory):
    if inventory.empty:
        return pd.DataFrame()
    inv = inventory.sort_values("snapshot_dt").groupby(["device", "med_id"], as_index=False).tail(1).copy()
    usage_summary = pd.DataFrame(columns=["device", "med_id", "audit_usage_events", "audit_usage_qty", "audit_last_usage"])
    if not audit_usage.empty:
        usage_summary = audit_usage.groupby(["device", "med_id"]).agg(
            audit_usage_events=("pk", "count"),
            audit_usage_qty=("abs_qty", "sum"),
            audit_last_usage=("dt", "max"),
        ).reset_index()
    check = inv.merge(usage_summary, on=["device", "med_id"], how="left")
    check["audit_usage_events"] = pd.to_numeric(check["audit_usage_events"], errors="coerce").fillna(0)
    check["audit_usage_qty"] = pd.to_numeric(check["audit_usage_qty"], errors="coerce").fillna(0)
    check["days_unused"] = pd.to_numeric(check["days_unused"], errors="coerce")

    def status(row):
        has_audit = row["audit_usage_events"] > 0
        days = row["days_unused"]
        recent_inventory = pd.notna(days) and days <= 3
        stale_inventory = pd.notna(days) and days >= 14
        if has_audit and recent_inventory:
            return "Matches recent use"
        if has_audit and stale_inventory:
            return "Inventory days-unused high despite audit use"
        if not has_audit and recent_inventory:
            return "Audit missing recent inventory use"
        if not has_audit and stale_inventory:
            return "Likely unused"
        return "Needs context"

    check["crosscheck_status"] = check.apply(status, axis=1)
    return check.sort_values(["crosscheck_status", "days_unused", "audit_usage_events"], ascending=[True, False, False])


def build_stock_config_review(med_detail):
    if med_detail.empty:
        return pd.DataFrame()
    review = med_detail.copy()

    def config_flag(row):
        flags = []
        if row.get("usage_events", 0) > 0 and str(row.get("standard_stock", "")).upper() == "N":
            flags.append("Active but non-standard")
        if row.get("usage_events", 0) > 0 and row.get("min_qty", 0) <= 0:
            flags.append("Min 0 with usage")
        if row.get("stockout_orders", 0) > 0 and row.get("max_qty", 0) <= row.get("min_qty", 0):
            flags.append("Max not above min")
        if row.get("clinical_zero_events", 0) > 0 and row.get("current_quantity", 0) <= 0:
            flags.append("Current zero after clinical zero")
        return ", ".join(flags) if flags else "No obvious config flag"

    review["config_review"] = review.apply(config_flag, axis=1)
    review["config_score"] = (
        (review["config_review"] != "No obvious config flag").astype(int) * 10
        + review["stockout_orders"] * 5
        + review["usage_events"]
    )
    return review.sort_values(["config_score", "stockout_orders", "usage_events"], ascending=False)


def build_recommendation_summary(selected_devices, current_summary, prior_summary, suggestions, med_detail, gap_df):
    rows = []
    for device in selected_devices:
        device_suggestion = pd.Series(dtype=object)
        if not suggestions.empty:
            matches = suggestions[suggestions["device"] == device]
            if not matches.empty:
                device_suggestion = matches.iloc[0]
        device_meds = med_detail[med_detail["device"] == device] if not med_detail.empty else pd.DataFrame()
        device_gaps = gap_df[gap_df["device"] == device] if not gap_df.empty else pd.DataFrame()
        long_gap_count = int((device_gaps["refill_gap_hours"].fillna(0) >= 4).sum()) if not device_gaps.empty else 0
        stockout_orders = int(device_meds["stockout_orders"].sum()) if not device_meds.empty else 0
        problem_meds = int((device_meds["problem_score"] >= 10).sum()) if not device_meds.empty else 0
        usage_qty = float(device_meds["usage_qty"].sum()) if not device_meds.empty else 0
        suggested = str(device_suggestion.get("suggested_second_refill", "Needs more data") or "Needs more data")
        if stockout_orders > 0 or long_gap_count > 0:
            recommendation = "Trial second refill"
        elif suggested in ["Already covered", "No extra refill signal"]:
            recommendation = "Keep current refill window"
        elif usage_qty > 0 and suggested not in ["Needs more data", ""]:
            recommendation = "Consider targeted top-off"
        else:
            recommendation = "Needs more data"
        rows.append({
            "device": device,
            "recommendation": recommendation,
            "suggested_time": suggested,
            "usage_qty": usage_qty,
            "stockout_orders": stockout_orders,
            "problem_meds": problem_meds,
            "long_zero_gaps": long_gap_count,
            "usage_delta": metric_delta(current_summary["usage_qty"], prior_summary["usage_qty"]),
            "refill_delta": metric_delta(current_summary["refill_qty"], prior_summary["refill_qty"]),
            "why": device_suggestion.get("rationale", "Review usage, stockout, and configuration tabs."),
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

with st.expander("Cath Lab access assumptions", expanded=False):
    a1, a2 = st.columns(2)
    with a1:
        blocked_start = st.number_input(
            "Room limited-access start hour",
            min_value=0,
            max_value=23,
            value=7,
            step=1,
            key="device_utilization_blocked_start",
            help="Default assumes scheduled Cath Lab procedure activity starts around 07:00.",
        )
    with a2:
        blocked_end = st.number_input(
            "Room limited-access end hour",
            min_value=0,
            max_value=23,
            value=17,
            step=1,
            key="device_utilization_blocked_end",
            help="Default assumes the room is easier to access again around 17:00.",
        )

with st.expander("Carousel pull/check workflow assumptions", expanded=False):
    w1, w2, w3, w4 = st.columns(4)
    with w1:
        pull_lead_hours = st.number_input(
            "Pull/check lead time hours",
            min_value=1,
            max_value=12,
            value=2,
            step=1,
            key="device_utilization_pull_lead_hours",
            help="How much time to allow for carousel pull, pharmacist check, and staging before delivery.",
        )
    with w2:
        overnight_pull_hour = st.number_input(
            "Large overnight pull hour",
            min_value=0,
            max_value=23,
            value=4,
            step=1,
            key="device_utilization_overnight_pull_hour",
            help="The hour that is already occupied by the major overnight pull.",
        )
    with w3:
        prior_night_pull_hour = st.number_input(
            "Prior-night pull option",
            min_value=0,
            max_value=23,
            value=22,
            step=1,
            key="device_utilization_prior_night_pull_hour",
            help="Fallback time if the suggested delivery would require pulling during the major overnight pull.",
        )
    with w4:
        max_staged_hours = st.number_input(
            "Review if staged over hours",
            min_value=1,
            max_value=24,
            value=8,
            step=1,
            key="device_utilization_max_staged_hours",
            help="Flags suggested workflows where meds would sit staged longer than this.",
        )

events = classify_events(load_device_events(start_date, end_date, selected_devices))
orders = classify_orders(load_device_orders(start_date, end_date, selected_devices))
audit_usage = load_audit_usage(start_date, end_date, selected_devices)
inventory = load_device_inventory_current(selected_devices)
daily = build_daily(events, orders, audit_usage)
prior_start, prior_end, prior_events, prior_orders, prior_usage = build_prior_comparison(selected_devices)
current_summary = summarize_window(events, orders, audit_usage)
prior_summary = summarize_window(prior_events, prior_orders, prior_usage)
gap_df = build_zero_gap_analysis(events, audit_usage)
suggestions = build_refill_time_suggestions(events, orders, audit_usage, int(blocked_start), int(blocked_end))
workflow_plan = build_refill_workflow_plan(
    suggestions,
    int(pull_lead_hours),
    int(overnight_pull_hour),
    int(prior_night_pull_hour),
    int(max_staged_hours),
)
delivery_summary, delivery_by_hour, delivery_by_user = build_refill_delivery_profile(events)
med_signal_detail = build_med_signal_detail(events, orders, audit_usage, inventory, gap_df)
recommendation_summary = build_recommendation_summary(
    selected_devices, current_summary, prior_summary, suggestions, med_signal_detail, gap_df
)

if events.empty and orders.empty and audit_usage.empty and inventory.empty:
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

tab_recommendation, tab_trend, tab_case_window, tab_usage, tab_refill_times, tab_workflow, tab_zero_gap, tab_weekend, tab_problem_meds, tab_inventory, tab_raw = st.tabs([
    "Recommendation",
    "Daily Trend",
    "Case Window",
    "Usage",
    "Optimal Refill Times",
    "Pull & Delivery Feasibility",
    "Vend-to-Zero Gap",
    "Weekend / Weekday",
    "Problem Meds",
    "Inventory Cross-Check",
    "Raw Data",
])

with tab_recommendation:
    st.subheader("Twice-Daily Refill Recommendation")
    st.caption("This rolls up usage, stockout pressure, vend-to-zero gaps, and current suggested timing into one manager-facing table.")
    if not workflow_plan.empty:
        staged_review_count = int(
            workflow_plan["workflow_risk"]
            .astype(str)
            .str.contains("staging|staged", case=False, regex=True, na=False)
            .sum()
        )
        if staged_review_count:
            st.warning(
                "At least one suggested delivery time needs workflow review because the carousel pull/check work may need to happen the night before."
            )
    st.dataframe(
        recommendation_summary,
        width="stretch",
        hide_index=True,
        column_config={
            "usage_qty": st.column_config.NumberColumn("Usage Qty", format="%.0f"),
            "stockout_orders": st.column_config.NumberColumn("Stockouts", format="%d"),
            "problem_meds": st.column_config.NumberColumn("Problem Meds", format="%d"),
            "long_zero_gaps": st.column_config.NumberColumn("Long Zero Gaps", format="%d"),
        },
    )

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

with tab_case_window:
    case_summary = build_case_window_summary(events, orders, audit_usage, int(blocked_start), int(blocked_end))
    if case_summary.empty:
        st.info("No usage, refill, or stockout activity was found to split by case-window timing.")
    else:
        st.caption(f"Pre-case, during cases, and post-case are based on limited-access hours {int(blocked_start):02d}:00-{int(blocked_end):02d}:00.")
        chart_cols = [col for col in ["usage_qty", "refill_qty", "stockout_orders"] if col in case_summary.columns]
        chart_data = case_summary.melt(["device", "case_window"], value_vars=chart_cols, var_name="measure", value_name="value")
        st.plotly_chart(
            px.bar(chart_data, x="case_window", y="value", color="measure", facet_col="device", barmode="group"),
            width="stretch",
        )
        st.dataframe(case_summary, width="stretch", hide_index=True)

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

with tab_workflow:
    st.subheader("Pull & Delivery Feasibility")
    st.caption(
        "This separates cabinet delivery time from the carousel pull/pharmacist-check work needed to make that delivery possible."
    )
    if workflow_plan.empty:
        st.info("No refill workflow plan is available yet.")
    else:
        st.dataframe(
            workflow_plan,
            width="stretch",
            hide_index=True,
            column_config={
                "device": st.column_config.TextColumn("Device"),
                "suggested_delivery": st.column_config.TextColumn("Suggested Delivery"),
                "prep_plan": st.column_config.TextColumn("Prep Plan"),
                "pull_check_start": st.column_config.TextColumn("Pull/Check Start"),
                "staged_hours": st.column_config.NumberColumn("Staged Hours", format="%.0f"),
                "workflow_risk": st.column_config.TextColumn("Workflow Risk"),
                "workflow_note": st.column_config.TextColumn("Why"),
            },
        )
        st.info(
            "A 05:00 refill should be treated as a delivery target, not proof the pull can happen at 05:00. "
            "If the needed pull/check start collides with the 04:00 overnight pull, this tab calls out the prior-night staging tradeoff."
        )

    st.divider()
    st.subheader("Actual Refill Delivery Pattern")
    st.caption(
        "Based on refill/load/restock rows in Events for the selected devices and date range. "
        "This is the best proxy for when the Pyxis tech actually services the cabinet."
    )
    if delivery_summary.empty:
        st.info("No refill/load/restock rows were found for the selected devices and dates.")
    else:
        st.dataframe(
            delivery_summary,
            width="stretch",
            hide_index=True,
            column_config={
                "device": st.column_config.TextColumn("Device"),
                "refill_events": st.column_config.NumberColumn("Refill Rows", format="%d"),
                "refill_days": st.column_config.NumberColumn("Days With Refills", format="%d"),
                "average_delivery_time": st.column_config.TextColumn("Average Delivery"),
                "median_delivery_time": st.column_config.TextColumn("Median Delivery"),
                "earliest_delivery": st.column_config.TextColumn("Earliest"),
                "latest_delivery": st.column_config.TextColumn("Latest"),
                "refill_qty": st.column_config.NumberColumn("Refill Qty", format="%.0f"),
                "unique_meds": st.column_config.NumberColumn("Unique Meds", format="%d"),
            },
        )
        if not delivery_by_hour.empty:
            st.plotly_chart(
                px.bar(
                    delivery_by_hour,
                    x="delivery_hour_label",
                    y="refill_events",
                    color="device",
                    barmode="group",
                    labels={"delivery_hour_label": "Delivery Hour", "refill_events": "Refill Rows"},
                ),
                width="stretch",
            )
        d1, d2 = st.columns(2)
        with d1:
            st.markdown("**By Hour**")
            st.dataframe(
                delivery_by_hour[["device", "delivery_hour_label", "refill_events", "refill_qty", "unique_meds"]],
                width="stretch",
                hide_index=True,
                column_config={
                    "delivery_hour_label": st.column_config.TextColumn("Hour"),
                    "refill_events": st.column_config.NumberColumn("Refill Rows", format="%d"),
                    "refill_qty": st.column_config.NumberColumn("Refill Qty", format="%.0f"),
                    "unique_meds": st.column_config.NumberColumn("Unique Meds", format="%d"),
                },
            )
        with d2:
            st.markdown("**By Tech**")
            st.dataframe(
                delivery_by_user,
                width="stretch",
                hide_index=True,
                column_config={
                    "user_name": st.column_config.TextColumn("Tech"),
                    "refill_events": st.column_config.NumberColumn("Refill Rows", format="%d"),
                    "refill_qty": st.column_config.NumberColumn("Refill Qty", format="%.0f"),
                    "unique_meds": st.column_config.NumberColumn("Unique Meds", format="%d"),
                    "average_delivery_time": st.column_config.TextColumn("Average Delivery"),
                    "first_delivery": st.column_config.DatetimeColumn("First", format="MM/DD/YY HH:mm"),
                    "last_delivery": st.column_config.DatetimeColumn("Last", format="MM/DD/YY HH:mm"),
                },
            )

with tab_zero_gap:
    if gap_df.empty:
        st.info("No clinical Audit Detail rows were found where a vend/remove/waste left the device ending quantity at zero.")
    else:
        g1, g2, g3 = st.columns(3)
        matched_gaps = gap_df[gap_df["gap_hours"].notna()]
        g1.metric("Clinical Vend-to-Zero Rows", f"{len(gap_df):,}")
        g2.metric("Matched to Refill/Load", f"{len(matched_gaps):,}")
        g3.metric(
            "Median Zero-to-Refill",
            f"{matched_gaps['refill_gap_hours'].median():.1f}h" if not matched_gaps.empty else "-",
        )
        st.caption(
            "This pairs a clinical Audit Transaction Detail removal/vend/dispense/withdraw that ended at zero with the next pharmacy verify-zero and refill/load for the same device and med. Waste-only rows are excluded."
        )
        st.dataframe(
            gap_df,
            width="stretch",
            hide_index=True,
            column_config={
                "clinical_zero_time": st.column_config.DatetimeColumn("Clinical Zero Time", format="MM/DD/YY HH:mm"),
                "next_staff_verify_zero": st.column_config.DatetimeColumn("Next Staff Verify Zero", format="MM/DD/YY HH:mm"),
                "verify_gap_hours": st.column_config.NumberColumn("Zero-to-Verify Hours", format="%.1f"),
                "next_refill": st.column_config.DatetimeColumn("Next Refill/Load", format="MM/DD/YY HH:mm"),
                "refill_gap_hours": st.column_config.NumberColumn("Zero-to-Refill Hours", format="%.1f"),
                "clinical_qty": st.column_config.NumberColumn("Clinical Qty", format="%.0f"),
            },
        )

with tab_weekend:
    if audit_usage.empty and orders.empty:
        st.info("No Audit Detail usage or stockout-order rows found for weekend/weekday comparison.")
    else:
        frames = []
        if not audit_usage.empty:
            wk = audit_usage.copy()
            wk["day_type"] = wk["dt"].dt.weekday.apply(lambda x: "Weekend" if x >= 5 else "Weekday")
            frames.append(wk.groupby(["device", "day_type"]).agg(
                usage_events=("pk", "count"),
                usage_qty=("abs_qty", "sum"),
                usage_meds=("med_id", "nunique"),
            ).reset_index())
        if not orders.empty:
            so = orders[orders["is_stockout"]].copy()
            if not so.empty:
                so["day_type"] = so["dt"].dt.weekday.apply(lambda x: "Weekend" if x >= 5 else "Weekday")
                frames.append(so.groupby(["destination", "day_type"]).agg(
                    stockout_orders=("pk", "count"),
                    stockout_qty=("abs_qty", "sum"),
                ).reset_index().rename(columns={"destination": "device"}))
        frames = [f for f in frames if not f.empty]
        if frames:
            weekend = frames[0]
            for frame in frames[1:]:
                weekend = weekend.merge(frame, on=["device", "day_type"], how="outer")
            weekend = weekend.fillna(0)
            st.plotly_chart(
                px.bar(weekend.melt(["device", "day_type"], var_name="measure", value_name="value"), x="day_type", y="value", color="measure", facet_col="device", barmode="group"),
                width="stretch",
            )
            st.dataframe(weekend, width="stretch", hide_index=True)

with tab_problem_meds:
    if med_signal_detail.empty:
        st.info("No medication-level signal found for these devices.")
    else:
        st.subheader("Top Problem Meds")
        st.caption("Ranked by stockouts, clinical vend-to-zero events, usage volume, and zero-to-staff gap length.")
        show_cols = [
            "device", "med_id", "med_desc", "problem_score", "usage_events", "usage_qty",
            "stockout_orders", "clinical_zero_events", "max_zero_gap_hours",
            "refill_qty", "current_quantity", "min_qty", "max_qty", "standard_stock", "days_unused",
        ]
        st.dataframe(med_signal_detail[[c for c in show_cols if c in med_signal_detail.columns]].head(50), width="stretch", hide_index=True)
        st.download_button(
            "Download problem med detail",
            data=med_signal_detail.to_csv(index=False).encode("utf-8"),
            file_name="device_utilization_problem_meds.csv",
            mime="text/csv",
        )

with tab_inventory:
    sub_cross, sub_config = st.tabs(["Days Unused Cross-Check", "Stock Configuration"])
    with sub_cross:
        crosscheck = build_inventory_crosscheck(audit_usage, inventory)
        if crosscheck.empty:
            st.info("No Device Inventory rows found for the selected devices.")
        else:
            st.dataframe(
                crosscheck[[
                    "device", "med_id", "med_desc", "crosscheck_status", "days_unused",
                    "audit_usage_events", "audit_usage_qty", "audit_last_usage",
                    "current_quantity", "min_qty", "max_qty", "standard_stock", "pocket_location",
                ]],
                width="stretch",
                hide_index=True,
            )
    with sub_config:
        config_review = build_stock_config_review(med_signal_detail)
        if config_review.empty:
            st.info("No stock configuration review is available for these devices.")
        else:
            config_cols = [
                "device", "med_id", "med_desc", "config_review", "config_score",
                "current_quantity", "min_qty", "max_qty", "standard_stock",
                "days_unused", "usage_events", "stockout_orders", "clinical_zero_events",
                "pocket_location", "outdate_tracking",
            ]
            st.dataframe(
                config_review[[c for c in config_cols if c in config_review.columns]],
                width="stretch",
                hide_index=True,
            )
            st.download_button(
                "Download stock configuration review",
                data=config_review.to_csv(index=False).encode("utf-8"),
                file_name="device_utilization_stock_config_review.csv",
                mime="text/csv",
            )

with tab_raw:
    raw_choice = st.segmented_control(
        "Raw table",
        ["Audit Usage", "Inventory Events", "Pharmacy Orders", "Device Inventory"],
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
    elif raw_choice == "Pharmacy Orders":
        if orders.empty:
            st.info("No pharmacy orders found for the selected devices.")
        else:
            st.dataframe(orders.sort_values("dt", ascending=False), width="stretch", hide_index=True)
    else:
        if inventory.empty:
            st.info("No Device Inventory rows found for the selected devices.")
        else:
            st.dataframe(inventory, width="stretch", hide_index=True)
