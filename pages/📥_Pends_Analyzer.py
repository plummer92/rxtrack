import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import io
from sqlalchemy import text
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

st.set_page_config(
    page_title="Pends Analyzer",
    page_icon="📥",
    layout="wide"
)

# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════

engine = App.engine
render_sidebar = App.render_sidebar

start_date, end_date = render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Pends Analyzer",
        "Audit who pended medications, what par levels they set, and whether they made them standard stock.",
        kicker="Performance",
    )
    _debug_event("Pends Analyzer", "shared_intro_loaded")
    _debug_panel("Pends Analyzer", intro_mode="shared")
else:
    st.header("📥 Pends Analyzer")
    st.caption("Audit who pended medications, what par levels they set, and whether they made them standard stock.")
    _debug_event("Pends Analyzer", "fallback_header_used")
    _debug_panel("Pends Analyzer", intro_mode="fallback")

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — DATA LOADERS
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_pends(start, end):
    """Load config_events for the selected date range."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, location,
                   action_type, activity_category, min_qty, max_qty, is_standard
            FROM config_events
            WHERE dt::date BETWEEN :start AND :end
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        df["dt"]          = pd.to_datetime(df["dt"], errors="coerce")
        df["user_name"]   = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["device"]      = df["device"].fillna("Unknown").astype(str).str.strip()
        df["med_id"]      = df["med_id"].astype(str).str.strip().str.upper()
        df["action_type"] = df["action_type"].astype(str).str.strip()
        df["date"]        = df["dt"].dt.date
        return df
    except Exception as e:
        st.error(f"[load_pends] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def load_drug_names():
    """Load med_id -> drug_name + trade_name + carousel_location from master mapping."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT med_id, med_desc, drug_name, trade_name, carousel_location "
                "FROM carousel_master_mapping"
            ))
            df = pd.DataFrame(result.fetchall(), columns=result.keys())
        df["med_id"] = df["med_id"].astype(str).str.strip().str.upper()
        df["display_name"] = df.apply(
            lambda r: (
                f"{r['drug_name']} ({r['trade_name']})"
                if pd.notna(r.get("trade_name")) and str(r.get("trade_name", "")).strip()
                else str(r.get("drug_name", r["med_desc"]))
            ),
            axis=1
        )
        df["is_controlled"] = df["carousel_location"].astype(str).str.startswith("CW")
        df = (
            df.sort_values(["med_id", "is_controlled"], ascending=[True, False])
            .groupby("med_id", as_index=False)
            .agg(
                drug_name=("drug_name", "first"),
                trade_name=("trade_name", "first"),
                display_name=("display_name", "first"),
                carousel_location=("carousel_location", "first"),
                is_controlled=("is_controlled", "max"),
            )
        )
        return df[["med_id", "drug_name", "trade_name", "display_name",
                   "carousel_location", "is_controlled"]]
    except Exception as e:
        st.warning(f"[load_drug_names] {e}")
        return pd.DataFrame()


with st.spinner("Loading pends data..."):
    df_raw   = load_pends(start_date, end_date)
    df_drugs = load_drug_names()

if df_raw.empty:
    st.warning("No pend activity found for the selected date range.")
    st.info("Upload a Device Activity Log (Pends) via the main upload page to populate this view.")
    st.stop()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — JOIN DRUG NAMES
# ═══════════════════════════════════════════════════════════════════════════════

if not df_drugs.empty:
    df = df_raw.merge(df_drugs, on="med_id", how="left")
else:
    df = df_raw.copy()
    for col in ["drug_name", "trade_name", "display_name", "carousel_location"]:
        df[col] = None
    df["is_controlled"] = False

df["display_name"] = df["display_name"].fillna(df["med_id"])
df["drug_name"]    = df["drug_name"].fillna(df["med_id"])

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — SIDEBAR FILTERS
# ═══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.divider()
    st.subheader("Filters")

    med_filter = st.multiselect(
        "Medication",
        sorted(df["display_name"].dropna().unique()),
        placeholder="All medications",
        key="pends_med_filter"
    )
    user_filter = st.multiselect(
        "User",
        sorted(df["user_name"].dropna().unique()),
        placeholder="All users",
        key="pends_user_filter"
    )
    device_filter = st.multiselect(
        "Device",
        sorted(df["device"].dropna().unique()),
        placeholder="All devices",
        key="pends_device_filter"
    )
    standard_filter = st.radio(
        "Standard Stock",
        ["All", "Standard Only", "Non-Standard Only"],
        index=0,
        key="pends_standard_filter"
    )

filtered = df.copy()
if med_filter:     filtered = filtered[filtered["display_name"].isin(med_filter)]
if user_filter:    filtered = filtered[filtered["user_name"].isin(user_filter)]
if device_filter:  filtered = filtered[filtered["device"].isin(device_filter)]
if standard_filter == "Standard Only":
    filtered = filtered[filtered["is_standard"] == True]
elif standard_filter == "Non-Standard Only":
    filtered = filtered[filtered["is_standard"] == False]

# Tab 7 expects a pend-level event frame scoped to the active sidebar filters.
df_pends = filtered.copy()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — EXECUTIVE METRICS
# ═══════════════════════════════════════════════════════════════════════════════

total_pends    = len(filtered)
unique_meds    = filtered["med_id"].nunique()
unique_users   = filtered["user_name"].nunique()
unique_devices = filtered["device"].nunique()
standard_ct    = int((filtered["is_standard"] == True).sum())
nonstd_ct      = int((filtered["is_standard"] == False).sum())

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Total Pend Events",  total_pends)
m2.metric("Unique Medications", unique_meds)
m3.metric("Users",              unique_users)
m4.metric("Devices",            unique_devices)
m5.metric("Made Standard",      standard_ct)
m6.metric("Non-Standard",       nonstd_ct)

st.divider()


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4B — OUTLIER DETECTION
# Runs against FULL df (all-time history) so comparisons are meaningful,
# then flags events that fall within the filtered date range.
# Three detectors, all med-history-relative:
#   A. Par outlier  — min or max > 2 SD from that med's historical mean
#   B. Churning     — same med+device pended 3+ times within 24 hours
#   C. New device   — med pended on a device with no prior history for that med
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_all_pends_history():
    """Full all-time config_events — needed for historical baselines."""
    try:
        sql = text("SELECT pk, dt, user_name, device, med_id, min_qty, max_qty, is_standard FROM config_events")
        with engine.connect() as conn:
            df_hist = pd.read_sql(sql, conn)
        df_hist["dt"]     = pd.to_datetime(df_hist["dt"], errors="coerce")
        df_hist["med_id"] = df_hist["med_id"].astype(str).str.strip().str.upper()
        df_hist["device"] = df_hist["device"].fillna("Unknown").astype(str).str.strip()
        return df_hist
    except Exception as e:
        st.warning(f"[load_all_pends_history] {e}")
        return pd.DataFrame()

with st.spinner("Loading historical pends..."):
    df_hist = load_all_pends_history()

@st.cache_data(ttl=300)
def load_all_unloads():
    """All unload events (Unload + Empty return bin) across all time for lifecycle analysis.
    Returns (DataFrame, error_str). error_str is None on success."""
    try:
        sql = text("""
            SELECT e.pk, e.dt, e.user_name, e.device, e.med_id, e.med_desc,
                   e.event_type, e.qty,
                   COALESCE(c.cost_per_unit, 0) AS cost_per_unit
            FROM events e
            LEFT JOIN med_costs c ON e.med_id = c.med_id
            WHERE (e.event_type ILIKE '%unload%' OR e.event_type ILIKE '%empty%')
              AND e.event_type NOT ILIKE '%cancel%'
              AND e.event_type NOT ILIKE '%eject%'
        """)
        with engine.connect() as conn:
            df_ul = pd.read_sql(sql, conn)
        df_ul["dt"]     = pd.to_datetime(df_ul["dt"], errors="coerce")
        df_ul["med_id"] = df_ul["med_id"].astype(str).str.strip().str.upper()
        df_ul["device"] = df_ul["device"].fillna("Unknown").astype(str).str.strip()
        return df_ul, None
    except Exception as e:
        return pd.DataFrame(), str(e)


@st.cache_data(ttl=300)
def load_all_reloads():
    """All reload/restock events across all time for same-device loop analysis.
    Returns (DataFrame, error_str). error_str is None on success."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, med_desc,
                   event_type, qty
            FROM events
            WHERE event_type ILIKE '%restock%'
               OR event_type ILIKE '%refill%'
               OR event_type ILIKE '%replenish%'
               OR event_type ~* '(^|[^a-z])load([^a-z]|$)'
        """)
        with engine.connect() as conn:
            df_rl = pd.read_sql(sql, conn)
        df_rl["dt"] = pd.to_datetime(df_rl["dt"], errors="coerce")
        df_rl["med_id"] = df_rl["med_id"].astype(str).str.strip().str.upper()
        df_rl["device"] = df_rl["device"].fillna("Unknown").astype(str).str.strip()
        df_rl = df_rl[
            ~df_rl["event_type"].astype(str).str.contains("cancel|unload|empty", case=False, na=False)
        ]
        if "device" in df_rl.columns:
            df_rl = df_rl[
                ~df_rl["device"].astype(str).str.contains("cass|patient", case=False, na=False)
            ]
        return df_rl, None
    except Exception as e:
        return pd.DataFrame(), str(e)

with st.spinner("Loading unload history..."):
    _unloads_result = load_all_unloads()
df_unloads, _unloads_error = _unloads_result
if _unloads_error:
    st.warning(f"⚠️ Could not load unload history for lifecycle analysis: {_unloads_error}")

with st.spinner("Loading reload history..."):
    _reloads_result = load_all_reloads()
df_reloads, _reloads_error = _reloads_result
if _reloads_error:
    st.warning(f"Could not load reload history for loop analysis: {_reloads_error}")

outlier_flags = []  # list of dicts, one per flagged event

if not df_hist.empty and not filtered.empty:

    # ── A. Par Outlier: min/max > 2 SD from that med's own historical mean ──
    par_stats = (
        df_hist[df_hist["min_qty"].notna() | df_hist["max_qty"].notna()]
        .groupby("med_id")
        .agg(
            mean_min=("min_qty", "mean"),  std_min=("min_qty", "std"),
            mean_max=("max_qty", "mean"),  std_max=("max_qty", "std"),
            hist_count=("pk", "count")
        )
        .reset_index()
    )
    # Need at least 3 historical pends to have a meaningful baseline
    par_stats = par_stats[par_stats["hist_count"] >= 3]

    par_check = filtered[filtered["min_qty"].notna() | filtered["max_qty"].notna()].merge(
        par_stats, on="med_id", how="inner"
    )

    def is_par_outlier(row):
        reasons = []
        if pd.notna(row.get("min_qty")) and pd.notna(row.get("std_min")) and row["std_min"] > 0:
            if abs(row["min_qty"] - row["mean_min"]) > 2 * row["std_min"]:
                reasons.append(f"Min {row['min_qty']:.0f} vs avg {row['mean_min']:.1f} (±{row['std_min']:.1f})")
        if pd.notna(row.get("max_qty")) and pd.notna(row.get("std_max")) and row["std_max"] > 0:
            if abs(row["max_qty"] - row["mean_max"]) > 2 * row["std_max"]:
                reasons.append(f"Max {row['max_qty']:.0f} vs avg {row['mean_max']:.1f} (±{row['std_max']:.1f})")
        return "; ".join(reasons) if reasons else None

    for _, row in par_check.iterrows():
        reason = is_par_outlier(row)
        if reason:
            outlier_flags.append({
                "pk":           row["pk"],
                "dt":           row["dt"],
                "user_name":    row["user_name"],
                "device":       row["device"],
                "med_id":       row["med_id"],
                "display_name": row.get("display_name", row["med_id"]),
                "min_qty":      row.get("min_qty"),
                "max_qty":      row.get("max_qty"),
                "is_standard":  row.get("is_standard"),
                "flag_type":    "⚠️ Par Outlier",
                "flag_detail":  reason,
            })

    # ── B. Churning: same med+device pended 3+ times within any 24-hour window ──
    churn_check = filtered.sort_values("dt").copy()
    churn_check["dt"] = pd.to_datetime(churn_check["dt"])

    for (med_id, device), grp in churn_check.groupby(["med_id", "device"]):
        if len(grp) < 3:
            continue
        times = grp["dt"].sort_values().reset_index(drop=True)
        for i in range(len(times)):
            window = times[(times >= times[i]) & (times <= times[i] + pd.Timedelta(hours=24))]
            if len(window) >= 3:
                # Flag the first event in the window
                first_row = grp[grp["dt"] == times[i]].iloc[0]
                outlier_flags.append({
                    "pk":           first_row["pk"],
                    "dt":           first_row["dt"],
                    "user_name":    first_row["user_name"],
                    "device":       device,
                    "med_id":       med_id,
                    "display_name": first_row.get("display_name", med_id),
                    "min_qty":      first_row.get("min_qty"),
                    "max_qty":      first_row.get("max_qty"),
                    "is_standard":  first_row.get("is_standard"),
                    "flag_type":    "🔄 Churning",
                    "flag_detail":  f"{len(window)} pends within 24h on {device}",
                })
                break  # only flag once per med+device group

    # ── C. New Device: med pended on a device with no prior history ──
    # "Prior" = before the current filter window start date
    prior_hist = df_hist[df_hist["dt"].dt.date < start_date]
    prior_device_pairs = set(
        zip(
            prior_hist["med_id"].astype(str).str.strip().str.upper(),
            prior_hist["device"].astype(str).str.strip()
        )
    )

    for _, row in filtered.iterrows():
        pair = (str(row["med_id"]).strip().upper(), str(row["device"]).strip())
        if pair not in prior_device_pairs:
            outlier_flags.append({
                "pk":           row["pk"],
                "dt":           row["dt"],
                "user_name":    row["user_name"],
                "device":       row["device"],
                "med_id":       row["med_id"],
                "display_name": row.get("display_name", row["med_id"]),
                "min_qty":      row.get("min_qty"),
                "max_qty":      row.get("max_qty"),
                "is_standard":  row.get("is_standard"),
                "flag_type":    "🆕 New Device",
                "flag_detail":  f"First time {row['med_id']} seen on {row['device']}",
            })

df_outliers = pd.DataFrame(outlier_flags).drop_duplicates(subset=["pk", "flag_type"]) if outlier_flags else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4C — MED LIFECYCLE BUILDER
# Unit: med_id + device pair
# Start:  first pend event on that device (from config_events, all-time)
# Unloads: all unload events for same med+device AFTER the first pend
# Metrics: days to first unload, total unload qty, unload qty vs max par,
#          total unload value, active/never used classification
# ═══════════════════════════════════════════════════════════════════════════════

df_lifecycle = pd.DataFrame()

if not df_hist.empty and not df_unloads.empty:

    # First pend per med+device (all-time baseline)
    first_pend = (
        df_hist
        .groupby(["med_id", "device"])
        .agg(
            first_pend_dt = ("dt",         "min"),
            pend_count    = ("pk",         "count"),
            last_min_qty  = ("min_qty",    "last"),
            last_max_qty  = ("max_qty",    "last"),
            pend_user     = ("user_name",  "first"),
            made_standard = ("is_standard",lambda x: (x == True).any()),
        )
        .reset_index()
    )

    # Only keep pairs where the first pend falls within the sidebar date range
    first_pend["first_pend_date"] = first_pend["first_pend_dt"].dt.date
    first_pend_filtered = first_pend[
        (first_pend["first_pend_date"] >= start_date) &
        (first_pend["first_pend_date"] <= end_date)
    ].copy()

    if not first_pend_filtered.empty:
        # Join unloads that occurred AFTER the first pend on the same med+device
        lifecycle_rows = []
        for _, pend_row in first_pend_filtered.iterrows():
            mid     = pend_row["med_id"]
            dev     = pend_row["device"]
            pend_dt = pend_row["first_pend_dt"]

            ul = df_unloads[
                (df_unloads["med_id"] == mid) &
                (df_unloads["device"] == dev) &
                (df_unloads["dt"]     >= pend_dt)
            ]

            if not ul.empty:
                first_unload_dt  = ul["dt"].min()
                days_to_first    = (first_unload_dt - pend_dt).days
                total_unload_qty = ul["qty"].sum()
                total_unload_val = (ul["qty"] * ul["cost_per_unit"].fillna(0)).sum()
                unload_events    = len(ul)
                last_unload_dt   = ul["dt"].max()
                # Par utilisation: how much was unloaded vs the max par set
                par_util_pct     = (
                    (total_unload_qty / pend_row["last_max_qty"] * 100)
                    if pd.notna(pend_row["last_max_qty"]) and pend_row["last_max_qty"] > 0
                    else None
                )
                status = "✅ Active"
            else:
                first_unload_dt  = None
                days_to_first    = None
                total_unload_qty = 0
                total_unload_val = 0
                unload_events    = 0
                last_unload_dt   = None
                par_util_pct     = 0
                status           = "💤 Never Unloaded"

            # Drug name lookup
            drug_info = df_drugs[df_drugs["med_id"] == mid]
            display_name = drug_info["display_name"].iloc[0] if not drug_info.empty else mid

            lifecycle_rows.append({
                "med_id":          mid,
                "device":          dev,
                "display_name":    display_name,
                "first_pend_dt":   pend_dt,
                "pend_user":       pend_row["pend_user"],
                "pend_count":      pend_row["pend_count"],
                "last_min_qty":    pend_row["last_min_qty"],
                "last_max_qty":    pend_row["last_max_qty"],
                "made_standard":   pend_row["made_standard"],
                "status":          status,
                "days_to_first_unload": days_to_first,
                "first_unload_dt": first_unload_dt,
                "last_unload_dt":  last_unload_dt,
                "unload_events":   unload_events,
                "total_unload_qty":total_unload_qty,
                "total_unload_val":total_unload_val,
                "par_util_pct":    par_util_pct,
            })

        df_lifecycle = pd.DataFrame(lifecycle_rows).sort_values(
            "days_to_first_unload", ascending=True, na_position="last"
        )

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 5 — TABS
# ═══════════════════════════════════════════════════════════════════════════════

def bucket_reload_days(days_value):
    if pd.isna(days_value):
        return "Not reloaded in window"
    if days_value <= 3:
        return "0-3 days"
    if days_value <= 7:
        return "4-7 days"
    if days_value <= 14:
        return "8-14 days"
    if days_value <= 28:
        return "15-28 days"
    return "29+ days"


def build_reload_pairs(unload_df, reload_df, max_days):
    if unload_df.empty:
        return pd.DataFrame()

    base_cols = ["dt", "user_name", "device", "med_id", "med_desc", "qty", "event_type"]
    working = unload_df[[c for c in base_cols if c in unload_df.columns]].copy()
    working = working.drop_duplicates(subset=base_cols).reset_index(drop=True)
    working = working.rename(columns={
        "dt": "unload_dt",
        "user_name": "unload_user",
        "qty": "unload_qty",
        "event_type": "unload_event_type",
    })
    working["reload_dt"] = pd.NaT
    working["reload_user"] = None
    working["reload_qty"] = np.nan
    working["reload_event_type"] = None
    working["days_to_reload"] = np.nan

    if reload_df.empty:
        working["reload_bucket"] = "Not reloaded in window"
        working["reloaded_within_window"] = False
        return working

    reload_working = reload_df[[c for c in base_cols if c in reload_df.columns]].copy()
    reload_working = reload_working.drop_duplicates(subset=base_cols).reset_index(drop=True)
    grouped_reload = {}
    for key, grp in reload_working.groupby(["med_id", "device"], dropna=False):
        grp = grp.sort_values("dt").reset_index(drop=True).copy()
        grp["matched"] = False
        grouped_reload[key] = grp

    max_delta = pd.Timedelta(days=max_days)
    for idx, row in working.iterrows():
        key = (row.get("med_id"), row.get("device"))
        candidates = grouped_reload.get(key)
        if candidates is None or candidates.empty or pd.isna(row.get("unload_dt")):
            continue
        future = candidates[
            (~candidates["matched"]) &
            (candidates["dt"] > row["unload_dt"])
        ]
        if future.empty:
            continue
        next_row = future.iloc[0]
        delta = next_row["dt"] - row["unload_dt"]
        if delta > max_delta:
            continue
        matched_idx = next_row.name
        grouped_reload[key].at[matched_idx, "matched"] = True
        working.at[idx, "reload_dt"] = next_row["dt"]
        working.at[idx, "reload_user"] = next_row.get("user_name")
        working.at[idx, "reload_qty"] = next_row.get("qty")
        working.at[idx, "reload_event_type"] = next_row.get("event_type")
        working.at[idx, "days_to_reload"] = delta.total_seconds() / 86400

    working["reload_bucket"] = working["days_to_reload"].apply(bucket_reload_days)
    working["reloaded_within_window"] = working["reload_dt"].notna()
    return working


def classify_boomerang_source(event_type):
    text = str(event_type).lower()
    if "empty" in text or "return bin" in text:
        return "Empty Return Bin"
    if "destock" in text:
        return "Unload/Destock"
    if "unload" in text:
        return "Unload/Destock"
    return "Other"


def add_prior_pend_context(par_events, pend_history, reload_history, unload_history):
    if par_events.empty:
        return par_events

    enriched = par_events.copy()
    for col in [
        "prev_min", "prev_max", "delta_min", "delta_max", "last_loaded_qty",
        "hours_since_last_load", "last_unloaded_qty", "hours_since_last_unload",
    ]:
        enriched[col] = np.nan
    for col in ["prev_pend_dt", "last_loaded_dt", "last_unloaded_dt"]:
        enriched[col] = pd.NaT
    for col in [
        "prev_pend_user", "last_loaded_user", "last_loaded_event_type",
        "last_unloaded_user", "last_unloaded_event_type",
    ]:
        enriched[col] = ""

    pend_work = pend_history.copy() if not pend_history.empty else pd.DataFrame()
    if not pend_work.empty:
        pend_work = pend_work[
            pend_work["min_qty"].notna() | pend_work["max_qty"].notna()
        ].drop_duplicates(
            subset=["dt", "user_name", "device", "med_id", "min_qty", "max_qty", "is_standard"],
            keep="first",
        )
        pend_work = pend_work.sort_values("dt")

    reload_work = reload_history.copy() if not reload_history.empty else pd.DataFrame()
    if not reload_work.empty:
        reload_work = reload_work.drop_duplicates(
            subset=["dt", "user_name", "device", "med_id", "event_type", "qty"],
            keep="first",
        ).sort_values("dt")

    unload_work = unload_history.copy() if not unload_history.empty else pd.DataFrame()
    if not unload_work.empty:
        unload_work = unload_work.drop_duplicates(
            subset=["dt", "user_name", "device", "med_id", "event_type", "qty"],
            keep="first",
        ).sort_values("dt")

    for idx, row in enriched.iterrows():
        med_id = row["med_id"]
        device = row["device"]
        event_dt = row["dt"]

        if not pend_work.empty and pd.notna(event_dt):
            prior_pends = pend_work[
                pend_work["med_id"].eq(med_id) &
                pend_work["device"].eq(device) &
                pend_work["dt"].lt(event_dt)
            ]
            if not prior_pends.empty:
                prior = prior_pends.iloc[-1]
                enriched.at[idx, "prev_min"] = prior.get("min_qty", np.nan)
                enriched.at[idx, "prev_max"] = prior.get("max_qty", np.nan)
                enriched.at[idx, "prev_pend_dt"] = prior.get("dt", pd.NaT)
                enriched.at[idx, "prev_pend_user"] = str(prior.get("user_name") or "")

        if not reload_work.empty and pd.notna(event_dt):
            prior_loads = reload_work[
                reload_work["med_id"].eq(med_id) &
                reload_work["device"].eq(device) &
                reload_work["dt"].lt(event_dt)
            ]
            if not prior_loads.empty:
                prior_load = prior_loads.iloc[-1]
                enriched.at[idx, "last_loaded_dt"] = prior_load.get("dt", pd.NaT)
                enriched.at[idx, "last_loaded_user"] = str(prior_load.get("user_name") or "")
                enriched.at[idx, "last_loaded_event_type"] = str(prior_load.get("event_type") or "")
                enriched.at[idx, "last_loaded_qty"] = prior_load.get("qty", np.nan)

        if not unload_work.empty and pd.notna(event_dt):
            prior_unloads = unload_work[
                unload_work["med_id"].eq(med_id) &
                unload_work["device"].eq(device) &
                unload_work["dt"].lt(event_dt)
            ]
            if not prior_unloads.empty:
                prior_unload = prior_unloads.iloc[-1]
                enriched.at[idx, "last_unloaded_dt"] = prior_unload.get("dt", pd.NaT)
                enriched.at[idx, "last_unloaded_user"] = str(prior_unload.get("user_name") or "")
                enriched.at[idx, "last_unloaded_event_type"] = str(prior_unload.get("event_type") or "")
                enriched.at[idx, "last_unloaded_qty"] = prior_unload.get("qty", np.nan)

    enriched["delta_min"] = enriched["min_qty"] - enriched["prev_min"]
    enriched["delta_max"] = enriched["max_qty"] - enriched["prev_max"]
    enriched["hours_since_last_load"] = (
        enriched["dt"] - enriched["last_loaded_dt"]
    ).dt.total_seconds().div(3600)
    enriched["hours_since_last_unload"] = (
        enriched["dt"] - enriched["last_unloaded_dt"]
    ).dt.total_seconds().div(3600)
    return enriched


tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📋 Par Audit",
    "👤 By User",
    "🖥️ By Device",
    "🔍 Raw Detail",
    "🚨 Outlier Detector",
    "🔬 Med Lifecycle",
    "📝 Par Change Audit Trail",
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — PAR AUDIT
# ─────────────────────────────────────────────────────────────────────────────

with tab1:
    st.subheader("Par Level Audit")
    st.caption(
        "One row per medication — who pended it, what min/max they set, "
        "and whether it was made standard stock."
    )

    par_audit = (
        filtered
        .groupby(
            ["med_id", "display_name", "drug_name", "trade_name",
             "carousel_location", "is_controlled"],
            dropna=False
        )
        .agg(
            pend_count    = ("pk",          "count"),
            users         = ("user_name",   lambda x: ", ".join(sorted(x.dropna().astype(str).unique()))),
            devices       = ("device",      lambda x: ", ".join(sorted(x.dropna().astype(str).unique()))),
            latest_min    = ("min_qty",     "last"),
            latest_max    = ("max_qty",     "last"),
            made_standard = ("is_standard", lambda x: (x == True).any()),
            last_pend_dt  = ("dt",          "max"),
            first_pend_dt = ("dt",          "min"),
            action_types  = ("action_type", lambda x: ", ".join(sorted(x.dropna().astype(str).unique()))),
        )
        .reset_index()
        .sort_values("last_pend_dt", ascending=False)
    )

    par_audit["in_master"] = par_audit["carousel_location"].notna()

    sa, sb = st.columns(2)
    sa.metric("Meds Made Standard Stock", int(par_audit["made_standard"].sum()))
    sb.metric("Meds Kept Non-Standard",   int((par_audit["made_standard"] == False).sum()))

    # Most pended meds chart
    top_pended = par_audit.head(25)
    if not top_pended.empty:
        fig = px.bar(
            top_pended,
            x="pend_count", y="display_name",
            orientation="h",
            color="made_standard",
            color_discrete_map={True: "#22c55e", False: "#f97316"},
            title="Most Frequently Pended Medications",
            labels={"pend_count": "Pend Events", "display_name": "", "made_standard": "Standard Stock"}
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=500)
        st.plotly_chart(fig, use_container_width=True)

    # Full audit table
    st.divider()
    display_cols = [c for c in [
        "display_name", "drug_name", "trade_name", "carousel_location",
        "is_controlled", "pend_count", "latest_min", "latest_max",
        "made_standard", "users", "devices", "action_types",
        "last_pend_dt", "first_pend_dt", "in_master"
    ] if c in par_audit.columns]

    st.dataframe(
        par_audit[display_cols],
        use_container_width=True,
        column_config={
            "display_name":      st.column_config.TextColumn("Medication"),
            "drug_name":         st.column_config.TextColumn("Generic Name"),
            "trade_name":        st.column_config.TextColumn("Trade Name"),
            "carousel_location": st.column_config.TextColumn("Carousel Location"),
            "is_controlled":     st.column_config.CheckboxColumn("Controlled"),
            "pend_count":        st.column_config.NumberColumn("# Pend Events", format="%d"),
            "latest_min":        st.column_config.NumberColumn("Current Min",   format="%.0f"),
            "latest_max":        st.column_config.NumberColumn("Current Max",   format="%.0f"),
            "made_standard":     st.column_config.CheckboxColumn("Made Standard"),
            "in_master":         st.column_config.CheckboxColumn("In Carousel Master"),
            "last_pend_dt":      st.column_config.DatetimeColumn("Last Pended",  format="MM/DD/YY HH:mm"),
            "first_pend_dt":     st.column_config.DatetimeColumn("First Pended", format="MM/DD/YY HH:mm"),
        },
        hide_index=True
    )

    # Pended meds NOT in carousel master
    st.divider()
    st.subheader("New Additions — Not in Logistics Carousel Master")
    st.caption("These meds were pended but don't appear in the logistics carousel master mapping. Possible new stock additions or one-offs.")
    new_meds = par_audit[par_audit["in_master"] == False]
    if new_meds.empty:
        st.success("All pended medications are in the logistics carousel master mapping.")
    else:
        st.dataframe(
            new_meds[[c for c in [
                "med_id", "display_name", "pend_count", "latest_min", "latest_max",
                "made_standard", "users", "last_pend_dt"
            ] if c in new_meds.columns]],
            use_container_width=True,
            column_config={
                "pend_count":    st.column_config.NumberColumn("# Pend Events", format="%d"),
                "latest_min":    st.column_config.NumberColumn("Min",           format="%.0f"),
                "latest_max":    st.column_config.NumberColumn("Max",           format="%.0f"),
                "made_standard": st.column_config.CheckboxColumn("Made Standard"),
                "last_pend_dt":  st.column_config.DatetimeColumn("Last Pended", format="MM/DD/YY HH:mm"),
            },
            hide_index=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — BY USER
# ─────────────────────────────────────────────────────────────────────────────

with tab2:
    st.subheader("Pend Activity by User")

    user_summary = (
        filtered
        .groupby("user_name")
        .agg(
            total_pends    = ("pk",          "count"),
            unique_meds    = ("med_id",      "nunique"),
            unique_devices = ("device",      "nunique"),
            made_standard  = ("is_standard", lambda x: (x == True).sum()),
            non_standard   = ("is_standard", lambda x: (x == False).sum()),
            last_pend      = ("dt",          "max"),
        )
        .reset_index()
        .sort_values("total_pends", ascending=False)
    )

    fig_usr = px.bar(
        user_summary,
        x="user_name", y=["made_standard", "non_standard"],
        title="Pend Events by User — Standard vs Non-Standard",
        labels={"value": "Events", "user_name": "", "variable": ""},
        color_discrete_map={"made_standard": "#22c55e", "non_standard": "#f97316"},
        barmode="stack"
    )
    st.plotly_chart(fig_usr, use_container_width=True)

    st.dataframe(
        user_summary,
        use_container_width=True,
        column_config={
            "total_pends":    st.column_config.NumberColumn("Total Pends",   format="%d"),
            "unique_meds":    st.column_config.NumberColumn("Unique Meds",   format="%d"),
            "unique_devices": st.column_config.NumberColumn("Devices",       format="%d"),
            "made_standard":  st.column_config.NumberColumn("Made Standard", format="%d"),
            "non_standard":   st.column_config.NumberColumn("Non-Standard",  format="%d"),
            "last_pend":      st.column_config.DatetimeColumn("Last Pend",   format="MM/DD/YY HH:mm"),
        },
        hide_index=True
    )

    # Per-user drill-down
    st.divider()
    st.subheader("User Drill-Down")
    sel_user = st.selectbox(
        "Select user",
        sorted(filtered["user_name"].dropna().unique()),
        key="user_drilldown"
    )
    if sel_user:
        user_detail = filtered[filtered["user_name"] == sel_user][[
            "dt", "display_name", "drug_name", "device",
            "action_type", "min_qty", "max_qty", "is_standard"
        ]].sort_values("dt", ascending=False)
        st.dataframe(
            user_detail,
            use_container_width=True,
            column_config={
                "dt":           st.column_config.DatetimeColumn("Date/Time", format="MM/DD/YY HH:mm"),
                "display_name": st.column_config.TextColumn("Medication"),
                "drug_name":    st.column_config.TextColumn("Generic"),
                "min_qty":      st.column_config.NumberColumn("Min",        format="%.0f"),
                "max_qty":      st.column_config.NumberColumn("Max",        format="%.0f"),
                "is_standard":  st.column_config.CheckboxColumn("Standard"),
            },
            hide_index=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — BY DEVICE
# ─────────────────────────────────────────────────────────────────────────────

with tab3:
    st.subheader("Pend Activity by Device")

    device_summary = (
        filtered
        .groupby("device")
        .agg(
            total_pends   = ("pk",          "count"),
            unique_meds   = ("med_id",      "nunique"),
            unique_users  = ("user_name",   "nunique"),
            made_standard = ("is_standard", lambda x: (x == True).sum()),
            non_standard  = ("is_standard", lambda x: (x == False).sum()),
            last_pend     = ("dt",          "max"),
        )
        .reset_index()
        .sort_values("total_pends", ascending=False)
    )

    fig_dev = px.bar(
        device_summary,
        x="device", y=["made_standard", "non_standard"],
        title="Pend Events by Device — Standard vs Non-Standard",
        labels={"value": "Events", "device": "", "variable": ""},
        color_discrete_map={"made_standard": "#22c55e", "non_standard": "#f97316"},
        barmode="stack"
    )
    st.plotly_chart(fig_dev, use_container_width=True)

    st.dataframe(
        device_summary,
        use_container_width=True,
        column_config={
            "total_pends":   st.column_config.NumberColumn("Total Pends",   format="%d"),
            "unique_meds":   st.column_config.NumberColumn("Unique Meds",   format="%d"),
            "unique_users":  st.column_config.NumberColumn("Users",         format="%d"),
            "made_standard": st.column_config.NumberColumn("Made Standard", format="%d"),
            "non_standard":  st.column_config.NumberColumn("Non-Standard",  format="%d"),
            "last_pend":     st.column_config.DatetimeColumn("Last Pend",   format="MM/DD/YY HH:mm"),
        },
        hide_index=True
    )

    # Per-device drill-down
    st.divider()
    st.subheader("Device Drill-Down")
    sel_device = st.selectbox(
        "Select device",
        sorted(filtered["device"].dropna().unique()),
        key="device_drilldown"
    )
    if sel_device:
        dev_detail = filtered[filtered["device"] == sel_device][[
            "dt", "display_name", "drug_name", "user_name",
            "action_type", "min_qty", "max_qty", "is_standard"
        ]].sort_values("dt", ascending=False)
        st.dataframe(
            dev_detail,
            use_container_width=True,
            column_config={
                "dt":           st.column_config.DatetimeColumn("Date/Time", format="MM/DD/YY HH:mm"),
                "display_name": st.column_config.TextColumn("Medication"),
                "drug_name":    st.column_config.TextColumn("Generic"),
                "min_qty":      st.column_config.NumberColumn("Min",        format="%.0f"),
                "max_qty":      st.column_config.NumberColumn("Max",        format="%.0f"),
                "is_standard":  st.column_config.CheckboxColumn("Standard"),
            },
            hide_index=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — RAW DETAIL
# ─────────────────────────────────────────────────────────────────────────────

with tab4:
    st.subheader("Raw Pend Detail")
    st.caption(f"{len(filtered):,} events matching current filters.")

    display_cols = [c for c in [
        "dt", "user_name", "device",
        "med_id", "display_name", "drug_name", "trade_name",
        "carousel_location", "is_controlled",
        "action_type", "activity_category",
        "min_qty", "max_qty", "is_standard",
    ] if c in filtered.columns]

    st.dataframe(
        filtered[display_cols].sort_values("dt", ascending=False),
        use_container_width=True,
        column_config={
            "dt":                st.column_config.DatetimeColumn("Date/Time",    format="MM/DD/YY HH:mm"),
            "display_name":      st.column_config.TextColumn("Medication"),
            "drug_name":         st.column_config.TextColumn("Generic"),
            "trade_name":        st.column_config.TextColumn("Trade Name"),
            "carousel_location": st.column_config.TextColumn("Carousel Loc"),
            "is_controlled":     st.column_config.CheckboxColumn("Controlled"),
            "min_qty":           st.column_config.NumberColumn("Min",            format="%.0f"),
            "max_qty":           st.column_config.NumberColumn("Max",            format="%.0f"),
            "is_standard":       st.column_config.CheckboxColumn("Standard"),
        },
        hide_index=True
    )
    st.download_button(
        "⬇️ Export Raw Pends to Excel",
        data=to_excel_bytes(filtered[display_cols].sort_values("dt", ascending=False)),
        file_name="pends_raw_detail.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — OUTLIER DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

with tab5:
    st.subheader("🚨 Outlier Detector")
    st.caption(
        "Flags pend events that look suspicious compared to that medication's own history. "
        "Three detectors: par values outside normal range, churning (3+ pends in 24h), "
        "and first-time appearance on a device."
    )

    if df_outliers.empty:
        st.success("✅ No outliers detected in the selected date range.")
    else:
        # Summary metrics
        par_ct   = int((df_outliers["flag_type"] == "⚠️ Par Outlier").sum())
        churn_ct = int((df_outliers["flag_type"] == "🔄 Churning").sum())
        new_ct   = int((df_outliers["flag_type"] == "🆕 New Device").sum())
        total_ct = len(df_outliers)

        o1, o2, o3, o4 = st.columns(4)
        o1.metric("Total Flags",     total_ct)
        o2.metric("⚠️ Par Outliers", par_ct)
        o3.metric("🔄 Churning",     churn_ct)
        o4.metric("🆕 New Device",   new_ct)

        # Flag type breakdown chart
        if not df_outliers.empty:
            flag_counts = df_outliers.groupby("flag_type").size().reset_index(name="count")
            fig_flags = px.bar(
                flag_counts,
                x="flag_type", y="count",
                color="flag_type",
                color_discrete_map={
                    "⚠️ Par Outlier": "#f97316",
                    "🔄 Churning":    "#8b5cf6",
                    "🆕 New Device":  "#3b82f6",
                },
                title="Outlier Flags by Type",
                labels={"count": "Flags", "flag_type": ""}
            )
            fig_flags.update_layout(showlegend=False)
            st.plotly_chart(fig_flags, use_container_width=True)

        # Top flagged meds
        top_flagged = (
            df_outliers.groupby(["display_name", "flag_type"])
            .size()
            .reset_index(name="flag_count")
            .sort_values("flag_count", ascending=False)
            .head(20)
        )
        if not top_flagged.empty:
            fig_top = px.bar(
                top_flagged,
                x="flag_count", y="display_name",
                orientation="h",
                color="flag_type",
                color_discrete_map={
                    "⚠️ Par Outlier": "#f97316",
                    "🔄 Churning":    "#8b5cf6",
                    "🆕 New Device":  "#3b82f6",
                },
                title="Most Flagged Medications",
                labels={"flag_count": "Flags", "display_name": ""}
            )
            fig_top.update_layout(yaxis={"categoryorder": "total ascending"}, height=450)
            st.plotly_chart(fig_top, use_container_width=True)

        # Filter by flag type
        st.divider()
        flag_filter = st.multiselect(
            "Filter by Flag Type",
            ["⚠️ Par Outlier", "🔄 Churning", "🆕 New Device"],
            default=["⚠️ Par Outlier", "🔄 Churning", "🆕 New Device"],
            key="pends_outlier_flag_filter"
        )
        outlier_view = df_outliers[df_outliers["flag_type"].isin(flag_filter)] if flag_filter else df_outliers

        st.dataframe(
            outlier_view[[c for c in [
                "flag_type", "flag_detail", "dt", "user_name", "device",
                "display_name", "min_qty", "max_qty", "is_standard"
            ] if c in outlier_view.columns]].sort_values("dt", ascending=False),
            use_container_width=True,
            column_config={
                "flag_type":   st.column_config.TextColumn("Flag"),
                "flag_detail": st.column_config.TextColumn("Detail"),
                "dt":          st.column_config.DatetimeColumn("Date/Time",  format="MM/DD/YY HH:mm"),
                "display_name":st.column_config.TextColumn("Medication"),
                "min_qty":     st.column_config.NumberColumn("Min",          format="%.0f"),
                "max_qty":     st.column_config.NumberColumn("Max",          format="%.0f"),
                "is_standard": st.column_config.CheckboxColumn("Standard"),
            },
            hide_index=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 6 — MED LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

with tab6:
    st.subheader("🔬 Med Lifecycle — Pend to Unload")
    st.caption(
        "Tracks each med+device pair from its first pend event through all subsequent unloads. "
        "Shows how fast pended meds get used, whether par levels are appropriate, "
        "and which meds were pended but never touched."
    )

    if df_lifecycle.empty:
        st.info("No lifecycle data available. Make sure both pend (config_events) and unload (events) data are uploaded, and that some pend events fall within your selected date range.")
    else:
        # ── Summary metrics ──────────────────────────────────────────────────
        active_ct     = int((df_lifecycle["status"] == "✅ Active").sum())
        never_ct      = int((df_lifecycle["status"] == "💤 Never Unloaded").sum())
        total_pairs   = len(df_lifecycle)
        avg_days      = df_lifecycle["days_to_first_unload"].mean()
        total_val     = df_lifecycle["total_unload_val"].sum()
        fast_movers   = int((df_lifecycle["days_to_first_unload"] <= 3).sum())

        lc1, lc2, lc3, lc4, lc5, lc6 = st.columns(6)
        lc1.metric("Med+Device Pairs",      total_pairs)
        lc2.metric("✅ Active",              active_ct)
        lc3.metric("💤 Never Unloaded",      never_ct)
        lc4.metric("Avg Days to First Use",  f"{avg_days:.1f}" if pd.notna(avg_days) else "N/A")
        lc5.metric("Fast Movers (≤3 days)",  fast_movers)
        lc6.metric("Total Unload Value",     f"${total_val:,.2f}")

        st.divider()

        # ── Days to first unload distribution ────────────────────────────────
        active_df = df_lifecycle[df_lifecycle["days_to_first_unload"].notna()].copy()
        if not active_df.empty:
            fig_dist = px.histogram(
                active_df,
                x="days_to_first_unload",
                nbins=30,
                title="Distribution — Days from First Pend to First Unload",
                labels={"days_to_first_unload": "Days to First Unload", "count": "Meds"},
                color_discrete_sequence=["#3b82f6"]
            )
            fig_dist.add_vline(
                x=active_df["days_to_first_unload"].median(),
                line_dash="dash", line_color="#ef4444",
                annotation_text=f"Median: {active_df['days_to_first_unload'].median():.0f}d",
                annotation_position="top right"
            )
            st.plotly_chart(fig_dist, use_container_width=True)

        col_a, col_b = st.columns(2)

        # ── Par utilisation scatter ───────────────────────────────────────────
        with col_a:
            par_df = df_lifecycle[
                df_lifecycle["par_util_pct"].notna() &
                (df_lifecycle["par_util_pct"] > 0)
            ].copy()
            if not par_df.empty:
                fig_par = px.scatter(
                    par_df,
                    x="last_max_qty", y="total_unload_qty",
                    color="status",
                    hover_name="display_name",
                    hover_data=["device", "days_to_first_unload", "par_util_pct"],
                    title="Par Max Set vs Total Qty Unloaded",
                    labels={
                        "last_max_qty":    "Max Par Set",
                        "total_unload_qty":"Total Unloaded",
                    },
                    color_discrete_map={"✅ Active": "#22c55e", "💤 Never Unloaded": "#94a3b8"}
                )
                # 1:1 reference line — unloaded exactly = max par
                max_val = max(par_df["last_max_qty"].max(), par_df["total_unload_qty"].max())
                fig_par.add_shape(
                    type="line", x0=0, y0=0, x1=max_val, y1=max_val,
                    line=dict(color="#ef4444", dash="dot", width=1)
                )
                fig_par.add_annotation(
                    x=max_val * 0.8, y=max_val * 0.85,
                    text="Unloaded = Par Max", showarrow=False,
                    font=dict(color="#ef4444", size=11)
                )
                st.plotly_chart(fig_par, use_container_width=True)

        # ── Never unloaded breakdown ──────────────────────────────────────────
        with col_b:
            never_df = df_lifecycle[df_lifecycle["status"] == "💤 Never Unloaded"].copy()
            if not never_df.empty:
                fig_never = px.bar(
                    never_df.head(15),
                    x="pend_count", y="display_name",
                    orientation="h",
                    color="made_standard",
                    color_discrete_map={True: "#f97316", False: "#94a3b8"},
                    title="Never-Unloaded Meds (# Pend Events)",
                    labels={
                        "pend_count":    "Times Pended",
                        "display_name":  "",
                        "made_standard": "Made Standard"
                    }
                )
                fig_never.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig_never, use_container_width=True)

        st.divider()

        # ── Full lifecycle table ──────────────────────────────────────────────
        status_filter = st.multiselect(
            "Filter by Status",
            ["✅ Active", "💤 Never Unloaded"],
            default=["✅ Active", "💤 Never Unloaded"],
            key="pends_lifecycle_status_filter"
        )
        lc_view = df_lifecycle[df_lifecycle["status"].isin(status_filter)] if status_filter else df_lifecycle

        display_cols = [c for c in [
            "status", "display_name", "device", "pend_user",
            "first_pend_dt", "last_min_qty", "last_max_qty", "made_standard",
            "days_to_first_unload", "first_unload_dt", "last_unload_dt",
            "unload_events", "total_unload_qty", "par_util_pct", "total_unload_val",
        ] if c in lc_view.columns]

        st.dataframe(
            lc_view[display_cols],
            use_container_width=True,
            column_config={
                "status":               st.column_config.TextColumn("Status"),
                "display_name":         st.column_config.TextColumn("Medication"),
                "pend_user":            st.column_config.TextColumn("Pended By"),
                "first_pend_dt":        st.column_config.DatetimeColumn("First Pended",      format="MM/DD/YY HH:mm"),
                "last_min_qty":         st.column_config.NumberColumn("Min Par",             format="%.0f"),
                "last_max_qty":         st.column_config.NumberColumn("Max Par",             format="%.0f"),
                "made_standard":        st.column_config.CheckboxColumn("Standard Stock"),
                "days_to_first_unload": st.column_config.NumberColumn("Days to First Use",   format="%d"),
                "first_unload_dt":      st.column_config.DatetimeColumn("First Unloaded",    format="MM/DD/YY HH:mm"),
                "last_unload_dt":       st.column_config.DatetimeColumn("Last Unloaded",     format="MM/DD/YY HH:mm"),
                "unload_events":        st.column_config.NumberColumn("Unload Events",       format="%d"),
                "total_unload_qty":     st.column_config.NumberColumn("Total Qty Unloaded",  format="%.0f"),
                "par_util_pct":         st.column_config.NumberColumn("Par Utilisation %",   format="%.1f%%"),
                "total_unload_val":     st.column_config.NumberColumn("Total Unload Value",  format="$%.2f"),
            },
            hide_index=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 7 — PAR CHANGE AUDIT TRAIL
# ─────────────────────────────────────────────────────────────────────────────

    st.divider()
    st.subheader("Unload to Same-Device Reload Timing")
    st.caption(
        "After a med is unloaded from a device, track how often that exact med gets loaded back into the same device within a chosen number of days."
    )

    reload_window_days = st.slider(
        "Reload window (days)",
        min_value=1,
        max_value=90,
        value=30,
        key="pends_reload_window_days",
        help="Counts a loop when the same med is reloaded to the same device within this many days after unload.",
    )

    boomerang_source_filter = st.segmented_control(
        "Removal source",
        options=["All Removals", "Unload/Destock", "Empty Return Bin"],
        default="Unload/Destock",
        key="pends_boomerang_source_filter",
    )
    exclude_quick_corrections = st.checkbox(
        "Exclude quick unload/reload corrections",
        value=True,
        key="pends_exclude_quick_corrections",
        help="Filters out back-to-back unload/load pairs that likely reflect moving a med to a different pocket size rather than true churn.",
    )
    quick_correction_minutes = st.slider(
        "Quick correction window (minutes)",
        min_value=1,
        max_value=30,
        value=5,
        key="pends_quick_correction_minutes",
        help="Unload/reload pairs at or under this many minutes will be treated as pocket-correction moves.",
    )

    reload_unloads = df_unloads.copy()
    reload_events = df_reloads.copy()

    if med_filter:
        med_ids = set(filtered["med_id"].dropna().astype(str).str.strip().str.upper())
        reload_unloads = reload_unloads[reload_unloads["med_id"].isin(med_ids)]
        reload_events = reload_events[reload_events["med_id"].isin(med_ids)]
    if device_filter:
        reload_unloads = reload_unloads[reload_unloads["device"].isin(device_filter)]
        reload_events = reload_events[reload_events["device"].isin(device_filter)]

    if not reload_unloads.empty:
        reload_unloads = reload_unloads[
            (reload_unloads["dt"].dt.date >= start_date) &
            (reload_unloads["dt"].dt.date <= end_date)
        ].copy()
        reload_unloads["boomerang_source"] = reload_unloads["event_type"].apply(classify_boomerang_source)

        if boomerang_source_filter == "Unload/Destock":
            reload_unloads = reload_unloads[reload_unloads["boomerang_source"] == "Unload/Destock"]
        elif boomerang_source_filter == "Empty Return Bin":
            reload_unloads = reload_unloads[reload_unloads["boomerang_source"] == "Empty Return Bin"]

    reload_pairs = build_reload_pairs(reload_unloads, reload_events, reload_window_days)
    if not reload_pairs.empty:
        reload_pairs["boomerang_source"] = reload_pairs["unload_event_type"].apply(classify_boomerang_source)
        reload_pairs["minutes_to_reload"] = reload_pairs["days_to_reload"] * 1440
        reload_pairs["is_quick_correction"] = (
            reload_pairs["reloaded_within_window"] &
            reload_pairs["minutes_to_reload"].notna() &
            (reload_pairs["minutes_to_reload"] <= quick_correction_minutes)
        )
    else:
        reload_pairs["minutes_to_reload"] = pd.Series(dtype=float)
        reload_pairs["is_quick_correction"] = pd.Series(dtype=bool)

    quick_correction_pairs = reload_pairs[reload_pairs["is_quick_correction"]].copy() if not reload_pairs.empty else pd.DataFrame()
    analysis_pairs = reload_pairs.copy()
    if exclude_quick_corrections and not analysis_pairs.empty:
        analysis_pairs = analysis_pairs[~analysis_pairs["is_quick_correction"]].copy()

    if analysis_pairs.empty:
        st.info("No Pyxis unload events available for reload timing analysis in this date range.")
    else:
        loop_ct = int(analysis_pairs["reloaded_within_window"].sum())
        loop_rate = (loop_ct / len(analysis_pairs) * 100) if len(analysis_pairs) else 0
        median_reload_days = analysis_pairs.loc[
            analysis_pairs["reloaded_within_window"], "days_to_reload"
        ].median()
        excluded_quick_ct = len(quick_correction_pairs) if exclude_quick_corrections else 0

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Removal Txns", f"{len(analysis_pairs):,}")
        r2.metric(f"Reloaded Within {reload_window_days}d", f"{loop_ct:,}")
        r3.metric("Loop Rate", f"{loop_rate:.1f}%")
        r4.metric(
            "Median Days to Reload",
            f"{median_reload_days:.1f}" if pd.notna(median_reload_days) else "n/a",
            delta=f"-{excluded_quick_ct} quick corrections" if excluded_quick_ct else None
        )

        bucket_order = ["0-3 days", "4-7 days", "8-14 days", "15-28 days", "29+ days", "Not reloaded in window"]
        bucket_summary = (
            analysis_pairs.groupby("reload_bucket", as_index=False)
            .agg(
                unload_events=("med_id", "count"),
                distinct_meds=("med_id", "nunique"),
                distinct_devices=("device", "nunique"),
            )
        )
        bucket_summary["reload_bucket"] = pd.Categorical(
            bucket_summary["reload_bucket"], categories=bucket_order, ordered=True
        )
        bucket_summary = bucket_summary.sort_values("reload_bucket")

        med_loop_summary = (
            analysis_pairs.groupby(["med_id", "med_desc"], as_index=False)
            .agg(
                unload_events=("med_id", "count"),
                reloaded_within_window=("reloaded_within_window", "sum"),
                loop_rate=("reloaded_within_window", "mean"),
                median_days_to_reload=("days_to_reload", "median"),
                devices=("device", "nunique"),
            )
            .sort_values(["reloaded_within_window", "unload_events"], ascending=False)
        )
        med_loop_summary["loop_rate"] = med_loop_summary["loop_rate"] * 100

        machine_loop_summary = (
            analysis_pairs.groupby(["med_id", "med_desc", "device"], as_index=False)
            .agg(
                unload_txns=("med_id", "count"),
                reloaded_txns=("reloaded_within_window", "sum"),
                loop_rate=("reloaded_within_window", "mean"),
                total_unload_qty=("unload_qty", "sum"),
                total_reload_qty=("reload_qty", "sum"),
                median_days_to_reload=("days_to_reload", "median"),
                median_reload_qty=("reload_qty", "median"),
            )
            .sort_values(["reloaded_txns", "unload_txns"], ascending=False)
        )
        machine_loop_summary["loop_rate"] = machine_loop_summary["loop_rate"] * 100
        machine_loop_summary["med_device"] = (
            machine_loop_summary["med_desc"].fillna(machine_loop_summary["med_id"])
            + " | "
            + machine_loop_summary["device"].fillna("Unknown")
        )

        chart_col, med_col = st.columns(2)
        with chart_col:
            fig_loop = px.bar(
                bucket_summary,
                x="reload_bucket",
                y="unload_events",
                labels={"reload_bucket": "", "unload_events": "Removal Txns"},
                color="reload_bucket",
                category_orders={"reload_bucket": bucket_order},
                title="Days Until Same Med Reloaded to Same Device",
            )
            fig_loop.update_layout(height=340, showlegend=False)
            st.plotly_chart(fig_loop, width="stretch")

        with med_col:
            top_loops = med_loop_summary.head(15).sort_values("reloaded_within_window")
            fig_meds = px.bar(
                top_loops,
                x="reloaded_within_window",
                y="med_desc",
                orientation="h",
                hover_data=["unload_events", "loop_rate", "median_days_to_reload", "devices"],
                labels={"reloaded_within_window": f"Reloaded Within {reload_window_days}d", "med_desc": ""},
                color="reloaded_within_window",
                color_continuous_scale="Tealgrn",
                title="Top Meds That Boomerang Back",
            )
            fig_meds.update_layout(height=340, coloraxis_showscale=False)
            st.plotly_chart(fig_meds, width="stretch")

        if exclude_quick_corrections and not quick_correction_pairs.empty:
            st.caption(
                f"Excluded {len(quick_correction_pairs):,} quick unload/load pairs at or under {quick_correction_minutes} minutes as likely pocket-size corrections."
            )

        st.divider()
        st.subheader("Top Boomerang Machines")
        st.caption(f"Which exact med + device combinations are cycling back into Pyxis most often for {boomerang_source_filter.lower()}.")

        top_machine_loops = machine_loop_summary.head(20).sort_values("reloaded_txns")
        fig_machine = px.bar(
            top_machine_loops,
            x="reloaded_txns",
            y="med_device",
            orientation="h",
            hover_data=["unload_txns", "loop_rate", "total_unload_qty", "total_reload_qty", "median_days_to_reload", "median_reload_qty"],
            labels={"reloaded_txns": f"Reloaded Txns Within {reload_window_days}d", "med_device": ""},
            color="reloaded_txns",
            color_continuous_scale="Tealgrn",
            title="Boomerang Meds by Machine",
        )
        fig_machine.update_layout(height=520, coloraxis_showscale=False, yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_machine, width="stretch")

        reload_display = analysis_pairs.sort_values(
            ["reloaded_within_window", "days_to_reload", "unload_dt"],
            ascending=[False, True, False],
        )
        st.dataframe(
            reload_display[[
                "unload_dt", "device", "med_desc", "unload_qty", "unload_user",
                "reload_dt", "reload_user", "reload_qty", "days_to_reload", "minutes_to_reload", "reload_bucket"
            ]],
            width="stretch",
            hide_index=True,
            column_config={
                "unload_dt": st.column_config.DatetimeColumn("Unload Time", format="MM/DD/YY HH:mm"),
                "reload_dt": st.column_config.DatetimeColumn("Reload Time", format="MM/DD/YY HH:mm"),
                "unload_qty": st.column_config.NumberColumn("Unload Qty", format="%.0f"),
                "reload_qty": st.column_config.NumberColumn("Reload Qty", format="%.0f"),
                "days_to_reload": st.column_config.NumberColumn("Days to Reload", format="%.1f"),
                "minutes_to_reload": st.column_config.NumberColumn("Minutes to Reload", format="%.1f"),
            },
        )

        if not quick_correction_pairs.empty:
            with st.expander("Excluded Quick Corrections", expanded=False):
                st.dataframe(
                    quick_correction_pairs[[
                        "unload_dt", "device", "med_desc", "unload_qty", "unload_user",
                        "reload_dt", "reload_user", "reload_qty", "minutes_to_reload",
                        "unload_event_type", "reload_event_type"
                    ]].sort_values(["minutes_to_reload", "unload_dt"], ascending=[True, False]),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "unload_dt": st.column_config.DatetimeColumn("Unload Time", format="MM/DD/YY HH:mm"),
                        "reload_dt": st.column_config.DatetimeColumn("Reload Time", format="MM/DD/YY HH:mm"),
                        "unload_qty": st.column_config.NumberColumn("Unload Qty", format="%.0f"),
                        "reload_qty": st.column_config.NumberColumn("Reload Qty", format="%.0f"),
                        "minutes_to_reload": st.column_config.NumberColumn("Minutes to Reload", format="%.1f"),
                        "unload_event_type": st.column_config.TextColumn("Unload Event"),
                        "reload_event_type": st.column_config.TextColumn("Reload Event"),
                    },
                )

        with st.expander("Medication Loop Summary", expanded=False):
            st.dataframe(
                med_loop_summary,
                width="stretch",
                hide_index=True,
                column_config={
                    "unload_events": st.column_config.NumberColumn("Unload Events", format="%d"),
                    "reloaded_within_window": st.column_config.NumberColumn(f"Reloaded <= {reload_window_days}d", format="%d"),
                    "loop_rate": st.column_config.NumberColumn("Loop Rate %", format="%.1f"),
                    "median_days_to_reload": st.column_config.NumberColumn("Median Days", format="%.1f"),
                    "devices": st.column_config.NumberColumn("Devices", format="%d"),
                },
            )

        with st.expander("Machine Loop Summary", expanded=True):
            machine_loop_view = machine_loop_summary.reset_index(drop=True)
            machine_event = st.dataframe(
                machine_loop_view,
                width="stretch",
                on_select="rerun",
                selection_mode="single-row",
                hide_index=True,
                column_config={
                    "med_desc": st.column_config.TextColumn("Medication"),
                    "device": st.column_config.TextColumn("Device"),
                    "unload_txns": st.column_config.NumberColumn("Unload Txns", format="%d"),
                    "reloaded_txns": st.column_config.NumberColumn(f"Reloaded Txns <= {reload_window_days}d", format="%d"),
                    "loop_rate": st.column_config.NumberColumn("Loop Rate %", format="%.1f"),
                    "total_unload_qty": st.column_config.NumberColumn("Total Unload Qty", format="%.0f"),
                    "total_reload_qty": st.column_config.NumberColumn("Total Reload Qty", format="%.0f"),
                    "median_days_to_reload": st.column_config.NumberColumn("Median Days", format="%.1f"),
                    "median_reload_qty": st.column_config.NumberColumn("Median Reload Qty", format="%.1f"),
                },
            )

            if len(machine_event.selection.rows) > 0:
                selected_machine = machine_loop_view.iloc[machine_event.selection.rows[0]]
                machine_med_id = selected_machine["med_id"]
                machine_device = selected_machine["device"]

                supporting_events = analysis_pairs[
                    (analysis_pairs["med_id"] == machine_med_id) &
                    (analysis_pairs["device"] == machine_device)
                ].sort_values(
                    ["reloaded_within_window", "days_to_reload", "unload_dt"],
                    ascending=[False, True, False],
                )

                st.divider()
                st.subheader("Selected Machine Raw Data")
                st.caption(
                    f"{selected_machine['med_desc']} at {machine_device} — raw unload/reload events behind the selected summary row."
                )

                sr1, sr2, sr3, sr4 = st.columns(4)
                sr1.metric("Unload Txns", int(selected_machine["unload_txns"]))
                sr2.metric(f"Reloaded Txns <= {reload_window_days}d", int(selected_machine["reloaded_txns"]))
                sr3.metric("Total Unload Qty", f"{selected_machine['total_unload_qty']:.0f}")
                sr4.metric("Total Reload Qty", f"{selected_machine['total_reload_qty']:.0f}" if pd.notna(selected_machine["total_reload_qty"]) else "0")

                st.dataframe(
                    supporting_events[[
                        "unload_dt", "unload_user", "unload_qty", "reload_dt", "reload_user",
                        "reload_qty", "days_to_reload", "minutes_to_reload", "reload_bucket", "unload_event_type", "reload_event_type"
                    ]],
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "unload_dt": st.column_config.DatetimeColumn("Unload Time", format="MM/DD/YY HH:mm"),
                        "reload_dt": st.column_config.DatetimeColumn("Reload Time", format="MM/DD/YY HH:mm"),
                        "unload_user": st.column_config.TextColumn("Unload User"),
                        "reload_user": st.column_config.TextColumn("Reload User"),
                        "unload_qty": st.column_config.NumberColumn("Unload Qty", format="%.0f"),
                        "reload_qty": st.column_config.NumberColumn("Reload Qty", format="%.0f"),
                        "days_to_reload": st.column_config.NumberColumn("Days to Reload", format="%.2f"),
                        "minutes_to_reload": st.column_config.NumberColumn("Minutes to Reload", format="%.1f"),
                        "reload_bucket": st.column_config.TextColumn("Reload Bucket"),
                        "unload_event_type": st.column_config.TextColumn("Unload Event"),
                        "reload_event_type": st.column_config.TextColumn("Reload Event"),
                    },
                )

with tab7:
    st.subheader("Par Change Audit Trail")
    st.caption(
        "Chronological log of every par level change (min/max) for each med+device pair "
        "within the selected date range. Highlights large swings and frequent changers."
    )

    if df_pends.empty:
        st.info("No pend data available for this date range.")
    else:
        # Only rows where min_qty or max_qty is set (par events)
        par_events = df_pends[
            df_pends["action_type"].astype(str).str.contains("par|pend|config|add|modify|change", case=False, na=False) |
            df_pends["min_qty"].notna() |
            df_pends["max_qty"].notna()
        ].copy()

        if par_events.empty:
            st.info("No par change events found in this window.")
        else:
            par_events = (
                par_events
                .drop_duplicates(
                    subset=[
                        "dt", "user_name", "device", "med_id", "location",
                        "action_type", "min_qty", "max_qty", "is_standard",
                    ],
                    keep="first",
                )
                .sort_values(["med_id", "device", "dt"])
                .copy()
            )
            par_events = add_prior_pend_context(par_events, df_hist, df_reloads, df_unloads)

            # Summary metrics
            total_changes  = len(par_events)
            large_swings   = int((par_events["delta_max"].abs() > 10).sum())
            unique_meds    = par_events["med_id"].nunique()
            top_changer    = par_events["user_name"].value_counts().idxmax() if not par_events.empty else "N/A"

            pc1, pc2, pc3, pc4 = st.columns(4)
            pc1.metric("Total Par Changes", total_changes)
            pc2.metric("Large Swings (>10 units)", large_swings)
            pc3.metric("Unique Meds Affected", unique_meds)
            pc4.metric("Most Active User", top_changer)

            st.divider()

            # Filter controls
            fc1, fc2 = st.columns(2)
            with fc1:
                filter_user = st.multiselect(
                    "Filter by User", options=sorted(par_events["user_name"].unique()),
                    key="par_audit_user_filter"
                )
            with fc2:
                filter_device = st.multiselect(
                    "Filter by Device", options=sorted(par_events["device"].unique()),
                    key="par_audit_device_filter"
                )

            view = par_events.copy()
            if filter_user:
                view = view[view["user_name"].isin(filter_user)]
            if filter_device:
                view = view[view["device"].isin(filter_device)]

            show_cols = [c for c in [
                "dt", "user_name", "device", "med_id", "location",
                "action_type", "min_qty", "max_qty",
                "prev_min", "prev_max", "delta_min", "delta_max",
                "prev_pend_dt", "prev_pend_user",
                "last_loaded_dt", "hours_since_last_load", "last_loaded_qty",
                "last_loaded_user", "last_loaded_event_type",
                "last_unloaded_dt", "hours_since_last_unload", "last_unloaded_qty",
                "last_unloaded_user", "last_unloaded_event_type", "is_standard"
            ] if c in view.columns]

            st.dataframe(
                view[show_cols].sort_values("dt", ascending=False),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "dt":          st.column_config.DatetimeColumn("Timestamp", format="MM/DD/YY HH:mm"),
                    "min_qty":     st.column_config.NumberColumn("Min Par",      format="%.0f"),
                    "max_qty":     st.column_config.NumberColumn("Max Par",      format="%.0f"),
                    "prev_min":    st.column_config.NumberColumn("Prev Min",     format="%.0f"),
                    "prev_max":    st.column_config.NumberColumn("Prev Max",     format="%.0f"),
                    "delta_min":   st.column_config.NumberColumn("Δ Min",        format="%+.0f"),
                    "delta_max":   st.column_config.NumberColumn("Δ Max",        format="%+.0f"),
                    "prev_pend_dt": st.column_config.DatetimeColumn("Prev Par Time", format="MM/DD/YY HH:mm"),
                    "prev_pend_user": st.column_config.TextColumn("Prev Par User"),
                    "last_loaded_dt": st.column_config.DatetimeColumn("Last Loaded", format="MM/DD/YY HH:mm"),
                    "hours_since_last_load": st.column_config.NumberColumn("Hours Since Load", format="%.1f"),
                    "last_loaded_qty": st.column_config.NumberColumn("Last Load Qty", format="%.0f"),
                    "last_loaded_user": st.column_config.TextColumn("Last Load User"),
                    "last_loaded_event_type": st.column_config.TextColumn("Last Load Event"),
                    "last_unloaded_dt": st.column_config.DatetimeColumn("Last Unloaded", format="MM/DD/YY HH:mm"),
                    "hours_since_last_unload": st.column_config.NumberColumn("Hours Since Unload", format="%.1f"),
                    "last_unloaded_qty": st.column_config.NumberColumn("Last Unload Qty", format="%.0f"),
                    "last_unloaded_user": st.column_config.TextColumn("Last Unload User"),
                    "last_unloaded_event_type": st.column_config.TextColumn("Last Unload Event"),
                    "is_standard": st.column_config.CheckboxColumn("Standard"),
                }
            )

