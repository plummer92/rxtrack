import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import text
import App
from rxtrack_shared import (
    add_return_compare_qty,
    group_return_compare_qty,
    group_return_unit_notes,
)

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(page_title="Return Reconciliation", page_icon="🔄", layout="wide")
App.apply_global_styles()

load_data = App.load_data
render_sidebar = App.render_sidebar
engine = App.engine

start_date, end_date = render_sidebar()
if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Closed-Loop Return Integrity Engine",
        "Validate Pyxis unload workflow against pharmacy return and restock activity without dropping back into the old page layout.",
        kicker="Operations",
    )
    _debug_event("Return Reconciliation", "shared_intro_loaded")
    _debug_panel("Return Reconciliation", intro_mode="shared")
else:
    st.header("🔄 Closed-Loop Return Integrity Engine")
    st.caption("Validate Pyxis unload workflow against pharmacy return and restock activity.")
    _debug_event("Return Reconciliation", "fallback_header_used")
    _debug_panel("Return Reconciliation", intro_mode="fallback")

with st.spinner("Loading data..."):
    df_events, _, df_pharm, _, _ = load_data(start_date, end_date)

if df_events.empty and df_pharm.empty:
    st.warning("No data found for selected dates.")
    st.stop()

for df in [df_events, df_pharm]:
    if not df.empty and "dt" in df.columns:
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")

all_users = sorted(list(set(
    list(df_events["user_name"].dropna().unique() if not df_events.empty else []) +
    list(df_pharm["user_name"].dropna().unique() if not df_pharm.empty else [])
)))

with st.sidebar:
    st.divider()
    st.subheader("Filters")
    selected_users   = st.multiselect("Filter by User", options=all_users)
    exclude_controls = st.checkbox("Exclude Controlled Substances", value=True)
    exclude_dummy    = st.checkbox("Exclude Dummy Medications", value=True)
    exclude_pat_refs = st.checkbox("Exclude PAT/ref med IDs (9000...)", value=True)
    exclude_patient_specific_refs = st.checkbox("Exclude patient-specific/ref/cassette descriptions", value=True)
    exclude_bulk_package_returns = st.checkbox("Exclude likely packaging bulk returns", value=True)
    return_match_window_hours = st.number_input(
        "Return match window after unload (hours)",
        min_value=1,
        max_value=48,
        value=12,
        step=1,
    )

# --- Identify Workflow Events ---

pyxis_unload = pd.DataFrame()
pharm_all = pd.DataFrame()

if not df_events.empty and "event_type" in df_events.columns:
    event_text = df_events["event_type"].fillna("").astype(str)
    pyxis_all_raw = df_events[
        event_text.str.contains("empty|unload|return bin|destock", case=False, na=False) &
        ~event_text.str.contains("cancel", case=False, na=False)
    ].copy()
    pyxis_unload_raw = df_events[
        event_text.str.contains(r"\bunload\b", case=False, regex=True, na=False) &
        ~df_events["event_type"].astype(str).str.contains("cancel", case=False, na=False)
    ].copy()

    # Unload Eject = broken cassette, not a real medication removal — split out for reference
    unload_eject = pyxis_all_raw[
        pyxis_all_raw["event_type"].astype(str).str.contains("eject", case=False, na=False)
    ].copy()
    pyxis_reference_removals = pyxis_all_raw[
        ~pyxis_all_raw["event_type"].astype(str).str.contains(r"\bunload\b|eject", case=False, regex=True, na=False)
    ].copy()
    pyxis_unload = pyxis_unload_raw[
        ~pyxis_unload_raw["event_type"].astype(str).str.contains("eject", case=False, na=False)
    ].copy()
    if "device" in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            ~pyxis_unload["device"].astype(str).str.contains("cass|patient", case=False, na=False)
        ]
    if "device" in pyxis_reference_removals.columns:
        pyxis_reference_removals = pyxis_reference_removals[
            ~pyxis_reference_removals["device"].astype(str).str.contains("cass|patient", case=False, na=False)
        ]
else:
    unload_eject = pd.DataFrame()
    pyxis_reference_removals = pd.DataFrame()

if not df_pharm.empty:
    pharm_df = df_pharm.copy()
    event_col = "event_type" if "event_type" in pharm_df.columns else ("priority" if "priority" in pharm_df.columns else None)
    if event_col:
        pharm_all = pharm_df[
            pharm_df[event_col].astype(str).str.contains("return|restock|instant|inventory", case=False, na=False)
        ].copy()

# --- Classify Workflow Type ---

def classify_workflow(row):
    text_blob = " ".join([str(row.get("event_type", "")), str(row.get("priority", ""))]).lower()
    if "inventory" in text_blob: return "Inventory Move"
    if "instant" in text_blob and "return" in text_blob: return "Instant Return"
    if "instant" in text_blob and "restock" in text_blob: return "Instant Restock"
    if "restock" in text_blob: return "Restock"
    if "return" in text_blob: return "Return"
    return "Other"

if not pharm_all.empty:
    pharm_all["workflow_type"] = pharm_all.apply(classify_workflow, axis=1)

# --- Split: Excluded from reconciliation math ---
# Inventory Move = surplus-to-working-stock transfer (not a Pyxis return)
# Restock = pharmacy proactively refilling (not triggered by a Pyxis unload)
# Instant Restock = KEPT in reconciliation — ambiguous, may be used as a return
# Return + Instant Return + Instant Restock = count toward reconciliation

EXCLUDED_TYPES = {"Inventory Move", "Restock"}
INCLUDED_RETURN_TYPES = {"Return", "Instant Return", "Instant Restock"}

inv_moves = pharm_all[pharm_all["workflow_type"] == "Inventory Move"].copy() if not pharm_all.empty else pd.DataFrame()
restocks   = pharm_all[pharm_all["workflow_type"] == "Restock"].copy() if not pharm_all.empty else pd.DataFrame()
pharm_return = pharm_all[pharm_all["workflow_type"].isin(INCLUDED_RETURN_TYPES)].copy() if not pharm_all.empty else pd.DataFrame()

# --- Apply User Filter ---

detail_pyxis_unload = pyxis_unload.copy()
detail_pharm_return = pharm_return.copy()
detail_inv_moves = inv_moves.copy()
detail_restocks = restocks.copy()
detail_pyxis_reference_removals = pyxis_reference_removals.copy()

if selected_users:
    if not detail_pyxis_unload.empty: detail_pyxis_unload = detail_pyxis_unload[detail_pyxis_unload["user_name"].isin(selected_users)]
    if not detail_pharm_return.empty: detail_pharm_return = detail_pharm_return[detail_pharm_return["user_name"].isin(selected_users)]
    if not detail_inv_moves.empty: detail_inv_moves = detail_inv_moves[detail_inv_moves["user_name"].isin(selected_users)]
    if not detail_restocks.empty: detail_restocks = detail_restocks[detail_restocks["user_name"].isin(selected_users)]
    if not detail_pyxis_reference_removals.empty:
        detail_pyxis_reference_removals = detail_pyxis_reference_removals[
            detail_pyxis_reference_removals["user_name"].isin(selected_users)
        ]

# --- Apply Med Filters ---

def remove_dummy(df):
    if df.empty or "med_desc" not in df.columns: return df
    return df[~df["med_desc"].astype(str).str.contains("cassette", case=False, na=False)]

def remove_patient_specific_refs(df):
    if df.empty or "med_desc" not in df.columns:
        return df
    med_text = df["med_desc"].fillna("").astype(str)
    pattern = (
        r"patient\s*specific|pat\s*specific|"
        r"patient\s*(?:ref|relat|cass)|pat\s*(?:ref|relat|cass)|"
        r"\bpat\s*cassette\b|\bpatient\s*cassette\b|"
        r"\bref\s*(?:relat|related|only)?\b"
    )
    return df[~med_text.str.contains(pattern, case=False, regex=True, na=False)]

@st.cache_data(ttl=3600)
def get_control_ids():
    """Query CW vault med_ids directly — no import cache dependency."""
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT DISTINCT med_id FROM carousel_master_mapping WHERE carousel_location LIKE 'CW%'"
            ))
            ids = set(row[0].strip().upper() for row in result if row[0])
        if not ids:
            st.warning("⚠️ No controlled meds found in carousel mapping. Is the master mapping uploaded?")
        return ids
    except Exception as e:
        st.warning(f"⚠️ Could not load controlled med list: {e}")
        return set()

def remove_controls(df):
    if df.empty or "med_id" not in df.columns: return df
    control_ids = get_control_ids()
    if not control_ids:
        return df  # nothing to filter, return as-is
    return df[~df["med_id"].astype(str).str.strip().str.upper().isin(control_ids)]

def remove_pat_refs(df):
    if df.empty or "med_id" not in df.columns: return df
    return df[~df["med_id"].astype(str).str.strip().str.match(r"^9000\d+", na=False)]

def is_likely_bulk_package_return(df):
    if df.empty or "qty" not in df.columns:
        return pd.Series(False, index=df.index)
    qty = pd.to_numeric(df["qty"], errors="coerce").fillna(0).abs()
    dt = pd.to_datetime(df["dt"], errors="coerce") if "dt" in df.columns else pd.Series(pd.NaT, index=df.index)
    after_buyer_overstock_walk = dt.dt.hour.mul(60).add(dt.dt.minute).ge((14 * 60) + 30)
    # Packaged-med and buyer-overstock returns can be entered as carousel returns even though
    # they are not Pyxis-return reconciliation work.
    clean_bulk_count = qty.isin([60, 90, 100, 500]) | ((qty >= 50) & (qty % 10 == 0))
    buyer_overstock_return = qty.ge(50) & after_buyer_overstock_walk
    return clean_bulk_count | buyer_overstock_return


@st.cache_data(ttl=300)
def load_unload_inventory_context(start, end):
    try:
        with engine.connect() as conn:
            return pd.read_sql(
                text(
                    """
                    SELECT
                        UPPER(TRIM(device)) AS device,
                        UPPER(TRIM(med_id)) AS med_id,
                        STRING_AGG(DISTINCT NULLIF(TRIM(pocket_location), ''), ', ') AS pocket_locations,
                        MAX(days_unused) AS max_days_unused,
                        STRING_AGG(DISTINCT NULLIF(TRIM(outdate_tracking), ''), ', ') AS outdate_tracking,
                        STRING_AGG(DISTINCT NULLIF(TRIM(status), ''), ', ') AS inventory_status
                    FROM (
                        SELECT device, med_id, pocket_location, days_unused, outdate_tracking, status
                        FROM device_inventory
                        WHERE med_id IS NOT NULL
                        UNION ALL
                        SELECT device, med_id, pocket_location, days_unused, outdate_tracking, status
                        FROM device_inventory_history
                        WHERE snapshot_date BETWEEN :start_date AND :end_date
                          AND med_id IS NOT NULL
                    ) inv
                    WHERE device IS NOT NULL
                    GROUP BY UPPER(TRIM(device)), UPPER(TRIM(med_id))
                    """
                ),
                conn,
                params={"start_date": start, "end_date": end},
            )
    except Exception:
        return pd.DataFrame()


def split_returns_by_unload_timing(return_df, unload_df, match_window_hours=12):
    if (
        return_df.empty
        or unload_df.empty
        or not {"med_id", "dt"}.issubset(return_df.columns)
        or not {"med_id", "dt", "date"}.issubset(unload_df.columns)
    ):
        return return_df.copy(), pd.DataFrame()

    returns = return_df.copy()
    returns["dt"] = pd.to_datetime(returns["dt"], errors="coerce")
    returns["med_id"] = returns["med_id"].astype(str).str.strip().str.upper()
    unloads = unload_df.copy()
    unloads["dt"] = pd.to_datetime(unloads["dt"], errors="coerce")
    unloads["med_id"] = unloads["med_id"].astype(str).str.strip().str.upper()
    unloads = unloads[unloads["dt"].notna() & unloads["med_id"].ne("")]
    if unloads.empty:
        return returns, pd.DataFrame()

    windows = (
        unloads.groupby(["med_id", "date"], dropna=False)["dt"]
        .min()
        .reset_index(name="first_unload_dt")
    )
    windows["match_end_dt"] = windows["first_unload_dt"] + pd.to_timedelta(match_window_hours, unit="h")
    windows_by_med = {
        med_id: group.sort_values("first_unload_dt").reset_index(drop=True)
        for med_id, group in windows.groupby("med_id", dropna=False)
    }

    matched_rows = []
    excluded_rows = []
    for _, row in returns.iterrows():
        row_dt = row.get("dt")
        med_id = row.get("med_id")
        med_windows = windows_by_med.get(med_id)
        if med_windows is None or med_windows.empty or pd.isna(row_dt):
            matched_rows.append(row)
            continue

        eligible_windows = med_windows[
            (row_dt >= med_windows["first_unload_dt"])
            & (row_dt <= med_windows["match_end_dt"])
        ]
        if eligible_windows.empty:
            excluded = row.copy()
            next_unload = med_windows[med_windows["first_unload_dt"] > row_dt].head(1)
            previous_unload = med_windows[med_windows["first_unload_dt"] <= row_dt].tail(1)
            if not next_unload.empty:
                excluded["timing_exclusion_reason"] = "Carousel return happened before Pyxis unload"
                excluded["nearest_unload_dt"] = next_unload.iloc[0]["first_unload_dt"]
            elif not previous_unload.empty:
                excluded["timing_exclusion_reason"] = f"Carousel return happened more than {match_window_hours}h after Pyxis unload"
                excluded["nearest_unload_dt"] = previous_unload.iloc[0]["first_unload_dt"]
            else:
                excluded["timing_exclusion_reason"] = "No matching Pyxis unload window"
                excluded["nearest_unload_dt"] = pd.NaT
            excluded_rows.append(excluded)
            continue

        selected_window = eligible_windows.iloc[-1]
        matched = row.copy()
        matched["date"] = selected_window["date"]
        matched["matched_unload_start_dt"] = selected_window["first_unload_dt"]
        matched_rows.append(matched)

    matched = pd.DataFrame(matched_rows) if matched_rows else returns.iloc[0:0].copy()
    excluded = pd.DataFrame(excluded_rows) if excluded_rows else returns.iloc[0:0].copy()
    return matched, excluded

if exclude_dummy:
    pyxis_unload = remove_dummy(pyxis_unload)
    pharm_return = remove_dummy(pharm_return)
    detail_pyxis_unload = remove_dummy(detail_pyxis_unload)
    detail_pharm_return = remove_dummy(detail_pharm_return)
    pyxis_reference_removals = remove_dummy(pyxis_reference_removals)
    detail_pyxis_reference_removals = remove_dummy(detail_pyxis_reference_removals)

if exclude_controls:
    pyxis_unload = remove_controls(pyxis_unload)
    pharm_return = remove_controls(pharm_return)
    detail_pyxis_unload = remove_controls(detail_pyxis_unload)
    detail_pharm_return = remove_controls(detail_pharm_return)
    pyxis_reference_removals = remove_controls(pyxis_reference_removals)
    detail_pyxis_reference_removals = remove_controls(detail_pyxis_reference_removals)

if exclude_pat_refs:
    pyxis_unload = remove_pat_refs(pyxis_unload)
    pharm_return = remove_pat_refs(pharm_return)
    detail_pyxis_unload = remove_pat_refs(detail_pyxis_unload)
    detail_pharm_return = remove_pat_refs(detail_pharm_return)
    detail_inv_moves = remove_pat_refs(detail_inv_moves)
    detail_restocks = remove_pat_refs(detail_restocks)
    pyxis_reference_removals = remove_pat_refs(pyxis_reference_removals)
    detail_pyxis_reference_removals = remove_pat_refs(detail_pyxis_reference_removals)

if exclude_patient_specific_refs:
    pyxis_unload = remove_patient_specific_refs(pyxis_unload)
    pharm_return = remove_patient_specific_refs(pharm_return)
    detail_pyxis_unload = remove_patient_specific_refs(detail_pyxis_unload)
    detail_pharm_return = remove_patient_specific_refs(detail_pharm_return)
    detail_inv_moves = remove_patient_specific_refs(detail_inv_moves)
    detail_restocks = remove_patient_specific_refs(detail_restocks)
    pyxis_reference_removals = remove_patient_specific_refs(pyxis_reference_removals)
    detail_pyxis_reference_removals = remove_patient_specific_refs(detail_pyxis_reference_removals)

bulk_package_returns = pd.DataFrame()
if exclude_bulk_package_returns and not pharm_return.empty:
    bulk_mask = is_likely_bulk_package_return(pharm_return)
    bulk_package_returns = pharm_return[bulk_mask].copy()
    pharm_return = pharm_return[~bulk_mask].copy()
    if not detail_pharm_return.empty:
        detail_pharm_return = detail_pharm_return[~is_likely_bulk_package_return(detail_pharm_return)].copy()

# --- Normalize Date ---

def ensure_date_column(df):
    if not df.empty and "date" not in df.columns:
        df["date"] = pd.to_datetime(df.get("dt"), errors="coerce").dt.date
    return df

pyxis_unload = ensure_date_column(pyxis_unload)
pharm_return = ensure_date_column(pharm_return)
inv_moves    = ensure_date_column(inv_moves)
restocks     = ensure_date_column(restocks)
unload_eject = ensure_date_column(unload_eject)
pyxis_reference_removals = ensure_date_column(pyxis_reference_removals)
detail_pyxis_unload = ensure_date_column(detail_pyxis_unload)
detail_pharm_return = ensure_date_column(detail_pharm_return)
detail_inv_moves    = ensure_date_column(detail_inv_moves)
detail_restocks     = ensure_date_column(detail_restocks)
detail_pyxis_reference_removals = ensure_date_column(detail_pyxis_reference_removals)
inventory_context = load_unload_inventory_context(start_date, end_date)

pharm_return, timing_excluded_returns = split_returns_by_unload_timing(
    pharm_return,
    pyxis_unload,
    return_match_window_hours,
)
detail_pharm_return, detail_timing_excluded_returns = split_returns_by_unload_timing(
    detail_pharm_return,
    detail_pyxis_unload,
    return_match_window_hours,
)

# --- Aggregate ---

def safe_group(df, qty_name):
    source = "pyxis" if qty_name == "qty_pyxis" else "carousel"
    return group_return_compare_qty(df, qty_name, source=source)


def conversion_note_group(df):
    return group_return_unit_notes(df)

pyxis_sum = safe_group(pyxis_unload, "qty_pyxis")
pharm_sum = safe_group(pharm_return, "qty_pharm")
unit_notes = conversion_note_group(pyxis_unload)

# --- Merge & Reconcile ---

recon = pd.merge(
    pyxis_sum.drop(columns=["med_desc"], errors="ignore"),
    pharm_sum.drop(columns=["med_desc"], errors="ignore"),
    on=["med_id", "date"], how="outer"
)
recon[["qty_pyxis", "qty_pharm"]] = recon[["qty_pyxis", "qty_pharm"]].fillna(0)
med_lookup = pd.concat([
    pyxis_sum[["med_id", "med_desc"]],
    pharm_sum[["med_id", "med_desc"]]
]).drop_duplicates("med_id")
recon = recon.merge(med_lookup, on="med_id", how="left")
if not unit_notes.empty:
    recon = recon.merge(unit_notes, on=["med_id", "date"], how="left")
else:
    recon["unit_note"] = ""
recon["unit_note"] = recon["unit_note"].fillna("")
recon["difference"] = recon["qty_pyxis"] - recon["qty_pharm"]

# --- Executive Metrics ---

total_unload = recon["qty_pyxis"].sum()
total_return = recon["qty_pharm"].sum()
recon_pct = (min(total_unload, total_return) / total_unload * 100) if total_unload > 0 else 100
unmatched = recon[recon["difference"] != 0]
inv_move_qty  = inv_moves["qty"].sum() if not inv_moves.empty and "qty" in inv_moves.columns else 0
restock_qty   = restocks["qty"].sum() if not restocks.empty and "qty" in restocks.columns else 0
eject_qty     = unload_eject["qty"].sum() if not unload_eject.empty and "qty" in unload_eject.columns else 0
reference_removal_qty = pyxis_reference_removals["qty"].sum() if not pyxis_reference_removals.empty and "qty" in pyxis_reference_removals.columns else 0
bulk_package_qty = bulk_package_returns["qty"].sum() if not bulk_package_returns.empty and "qty" in bulk_package_returns.columns else 0
timing_excluded_qty = timing_excluded_returns["qty"].sum() if not timing_excluded_returns.empty and "qty" in timing_excluded_returns.columns else 0

m1, m2, m3, m4, m5, m6, m7, m8, m9 = st.columns(9)
m1.metric("Total Pyxis Removal Qty", int(total_unload))
m2.metric("Total Carousel Return Qty", int(total_return))
m3.metric("Reconciliation %", f"{recon_pct:.1f}%")
m4.metric("Unmatched Med-Days", len(unmatched))
m5.metric("Inv Moves (excl.)", int(inv_move_qty))
m6.metric("Restocks (excl.)", int(restock_qty))
m7.metric("Eject Events (excl.)", int(eject_qty))
m8.metric("Bulk/Overstock Returns (excl.)", int(bulk_package_qty))
m9.metric("Early/Late Returns (excl.)", int(timing_excluded_qty))

st.divider()
st.caption(f"Core reconciliation starts with Pyxis `Unload` transactions only. Carousel returns count only when they occur after the Pyxis unload start within {return_match_window_hours} hours.")

# --- User Return Lookup ---

st.subheader("Carousel Returns by User")

return_users = sorted(
    detail_pharm_return["user_name"].dropna().astype(str).unique().tolist()
) if not detail_pharm_return.empty and "user_name" in detail_pharm_return.columns else []

selected_return_user = st.selectbox(
    "Select a user to review carousel returns",
    options=["All Users"] + return_users,
    index=0,
)

user_returns = detail_pharm_return.copy()
if selected_return_user != "All Users" and not user_returns.empty:
    user_returns = user_returns[user_returns["user_name"].astype(str) == selected_return_user]

if user_returns.empty:
    st.info("No carousel return rows found for this selection.")
else:
    user_returns = user_returns.sort_values("dt", ascending=False)
    total_user_return_qty = user_returns["qty"].sum() if "qty" in user_returns.columns else 0
    unique_return_meds = user_returns["med_id"].nunique() if "med_id" in user_returns.columns else 0
    active_return_days = user_returns["date"].nunique() if "date" in user_returns.columns else 0

    u1, u2, u3, u4 = st.columns(4)
    u1.metric("Return Rows", f"{len(user_returns):,}")
    u2.metric("Return Qty", f"{total_user_return_qty:,.0f}")
    u3.metric("Unique Meds", f"{unique_return_meds:,}")
    u4.metric("Active Return Days", f"{active_return_days:,}")

    display_cols = [
        c for c in [
            "dt", "date", "user_name", "workflow_type", "med_id", "med_desc",
            "destination", "qty", "priority", "queue_id"
        ]
        if c in user_returns.columns
    ]
    st.dataframe(
        user_returns[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "dt": st.column_config.DatetimeColumn("Date / Time"),
            "date": st.column_config.DateColumn("Date"),
            "user_name": "User",
            "workflow_type": "Return Type",
            "med_id": "Med ID",
            "med_desc": "Medication",
            "destination": "Destination",
            "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
            "priority": "Priority",
            "queue_id": "Queue ID",
        },
    )

st.divider()
st.subheader("Pyxis Unloads by User")

unload_users = sorted(
    detail_pyxis_unload["user_name"].dropna().astype(str).unique().tolist()
) if not detail_pyxis_unload.empty and "user_name" in detail_pyxis_unload.columns else []

selected_unload_user = st.selectbox(
    "Select a user to review Pyxis unloads",
    options=["All Users"] + unload_users,
    index=0,
)

user_unloads = detail_pyxis_unload.copy()
if selected_unload_user != "All Users" and not user_unloads.empty:
    user_unloads = user_unloads[user_unloads["user_name"].astype(str) == selected_unload_user]

if user_unloads.empty:
    st.info("No Pyxis unload rows found for this selection.")
else:
    user_unloads = user_unloads.sort_values("dt", ascending=False)
    user_unloads = add_return_compare_qty(user_unloads, source="pyxis")
    if not inventory_context.empty and {"device", "med_id"}.issubset(user_unloads.columns):
        unload_keys = user_unloads.copy()
        unload_keys["_device_key"] = unload_keys["device"].fillna("").astype(str).str.strip().str.upper()
        unload_keys["_med_key"] = unload_keys["med_id"].fillna("").astype(str).str.strip().str.upper()
        context_keys = inventory_context.copy()
        context_keys["_device_key"] = context_keys["device"].fillna("").astype(str).str.strip().str.upper()
        context_keys["_med_key"] = context_keys["med_id"].fillna("").astype(str).str.strip().str.upper()
        user_unloads = unload_keys.merge(
            context_keys.drop(columns=["device", "med_id"], errors="ignore"),
            on=["_device_key", "_med_key"],
            how="left",
        ).drop(columns=["_device_key", "_med_key"], errors="ignore")

    user_unloads["hour"] = pd.to_datetime(user_unloads["dt"], errors="coerce").dt.hour
    user_unloads["unload_bucket"] = "Other unload"
    if "event_type" in user_unloads.columns:
        unload_text = user_unloads["event_type"].fillna("").astype(str)
        user_unloads.loc[unload_text.str.contains("outdate|expire|28", case=False, regex=True, na=False), "unload_bucket"] = "Outdate / expiration signal"
    if "max_days_unused" in user_unloads.columns:
        days_unused = pd.to_numeric(user_unloads["max_days_unused"], errors="coerce")
        user_unloads.loc[days_unused.ge(28), "unload_bucket"] = "28+ days unused signal"

    total_user_unload_qty = user_unloads["qty"].sum() if "qty" in user_unloads.columns else 0
    unique_unload_meds = user_unloads["med_id"].nunique() if "med_id" in user_unloads.columns else 0
    active_unload_days = user_unloads["date"].nunique() if "date" in user_unloads.columns else 0
    unique_unload_devices = user_unloads["device"].nunique() if "device" in user_unloads.columns else 0
    likely_28_day_rows = int(user_unloads["unload_bucket"].eq("28+ days unused signal").sum())

    p1, p2, p3, p4, p5, p6 = st.columns(6)
    p1.metric("Unload Rows", f"{len(user_unloads):,}")
    p2.metric("Unload Qty", f"{total_user_unload_qty:,.0f}")
    p3.metric("Unique Meds", f"{unique_unload_meds:,}")
    p4.metric("Devices Hit", f"{unique_unload_devices:,}")
    p5.metric("28+ Day Signals", f"{likely_28_day_rows:,}")
    p6.metric("Active Days", f"{active_unload_days:,}")

    if selected_unload_user != "All Users":
        st.caption(
            "Spike read: if rows are spread across many devices and mostly tagged as 28+ days unused, this looks like a route/backlog cleanup. "
            "If it is concentrated in one or two devices or a small set of meds, investigate that cabinet/pocket instead."
        )
        a1, a2, a3 = st.columns(3)
        with a1:
            by_device = user_unloads.groupby("device", dropna=False).agg(
                unload_rows=("pk", "count"),
                unload_qty=("qty", "sum"),
                unique_meds=("med_id", "nunique"),
                first_unload=("dt", "min"),
                last_unload=("dt", "max"),
            ).reset_index().sort_values(["unload_rows", "unload_qty"], ascending=[False, False])
            st.markdown("**Devices driving the unloads**")
            st.dataframe(
                by_device,
                width="stretch",
                hide_index=True,
                column_config={
                    "first_unload": st.column_config.DatetimeColumn("First", format="HH:mm"),
                    "last_unload": st.column_config.DatetimeColumn("Last", format="HH:mm"),
                },
            )
        with a2:
            by_hour = user_unloads.groupby("hour", dropna=False).agg(
                unload_rows=("pk", "count"),
                unload_qty=("qty", "sum"),
                devices=("device", "nunique"),
                unique_meds=("med_id", "nunique"),
            ).reset_index().sort_values("hour")
            st.markdown("**Unload timing**")
            st.dataframe(by_hour, width="stretch", hide_index=True)
        with a3:
            by_bucket = user_unloads.groupby("unload_bucket", dropna=False).agg(
                unload_rows=("pk", "count"),
                unload_qty=("qty", "sum"),
                devices=("device", "nunique"),
                unique_meds=("med_id", "nunique"),
            ).reset_index().sort_values("unload_rows", ascending=False)
            st.markdown("**Why it may be high**")
            st.dataframe(by_bucket, width="stretch", hide_index=True)

        med_aggs = {
            "unload_rows": ("pk", "count"),
            "unload_qty": ("qty", "sum"),
            "devices": ("device", "nunique"),
        }
        if "max_days_unused" in user_unloads.columns:
            med_aggs["max_days_unused"] = ("max_days_unused", "max")
        if "pocket_locations" in user_unloads.columns:
            med_aggs["pocket_locations"] = (
                "pocket_locations",
                lambda s: ", ".join(sorted({str(v) for v in s.dropna() if str(v).strip()}))[:300],
            )
        by_med = (
            user_unloads.groupby(["med_id", "med_desc"], dropna=False)
            .agg(**med_aggs)
            .reset_index()
            .sort_values(["unload_rows", "devices", "unload_qty"], ascending=[False, False, False])
        )
        st.markdown("**Meds driving the unloads**")
        st.dataframe(
            by_med.head(50),
            width="stretch",
            hide_index=True,
            column_config={
                "max_days_unused": st.column_config.NumberColumn("Max Days Unused", format="%.0f"),
            },
        )

    unload_display_cols = [
        c for c in [
            "dt", "date", "user_name", "device", "event_type", "med_id",
            "med_desc", "qty", "return_unit_note", "compare_qty", "beginning_qty", "ending_qty",
            "max_days_unused", "pocket_locations", "outdate_tracking", "unload_bucket"
        ]
        if c in user_unloads.columns
    ]
    st.dataframe(
        user_unloads[unload_display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "dt": st.column_config.DatetimeColumn("Date / Time"),
            "date": st.column_config.DateColumn("Date"),
            "user_name": "User",
            "device": "Device",
            "event_type": "Event Type",
            "med_id": "Med ID",
            "med_desc": "Medication",
            "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
            "return_unit_note": "Compare Unit",
            "compare_qty": st.column_config.NumberColumn("Compare Qty", format="%.2f"),
            "beginning_qty": st.column_config.NumberColumn("Beginning Qty", format="%.0f"),
            "ending_qty": st.column_config.NumberColumn("Ending Qty", format="%.0f"),
            "max_days_unused": st.column_config.NumberColumn("Max Days Unused", format="%.0f"),
            "pocket_locations": "Known Pockets",
            "outdate_tracking": "Outdate Tracking",
            "unload_bucket": "Spike Bucket",
        },
    )

# --- Variance Table + Drilldown ---

st.subheader("🚨 Unmatched Workflow Events")

if unmatched.empty:
    st.success("✅ 100% Reconciliation Achieved.")
else:
    display = unmatched.sort_values("difference", key=abs, ascending=False).reset_index(drop=True)
    event = st.dataframe(display, width="stretch", on_select="rerun", selection_mode="single-row", hide_index=True)

    if len(event.selection.rows) > 0:
        idx = event.selection.rows[0]
        selected = display.iloc[idx]
        med_id = selected["med_id"]
        date = selected["date"]

        st.divider()
        st.subheader(f"🔎 Drilldown: {selected['med_desc']} — {date}")

        unload_detail = detail_pyxis_unload[
            (detail_pyxis_unload["med_id"] == med_id) & (detail_pyxis_unload["date"] == date)
        ].sort_values("dt")

        return_detail = detail_pharm_return[
            (detail_pharm_return["med_id"] == med_id) & (detail_pharm_return["date"] == date)
        ].sort_values("dt")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### Pyxis Removal Events")
            if not unload_detail.empty:
                unload_detail["med_id"] = unload_detail["med_id"].astype(str).str.strip().str.upper()
                unload_detail = add_return_compare_qty(unload_detail, source="pyxis")
            drill_cols = [c for c in ["dt", "user_name", "device", "qty", "return_unit_note", "compare_qty"] if c in unload_detail.columns]
            st.dataframe(unload_detail[drill_cols], width="stretch")
        with c2:
            st.markdown("### Carousel Return Events")
            return_cols = [
                c for c in ["dt", "matched_unload_start_dt", "user_name", "workflow_type", "qty"]
                if c in return_detail.columns
            ]
            st.dataframe(return_detail[return_cols], width="stretch")

# --- Inventory Moves (reference only, excluded from reconciliation) ---

st.divider()
with st.expander(f"📦 Inventory Moves — Excluded from Reconciliation ({int(inv_move_qty)} units)", expanded=False):
    st.caption("These are surplus-to-working-inventory transfers, not Pyxis returns. They are shown here for reference only.")
    if detail_inv_moves.empty:
        st.info("No inventory moves found for this date range.")
    else:
        cols = [c for c in ["dt", "date", "user_name", "med_desc", "qty", "workflow_type"] if c in detail_inv_moves.columns]
        st.dataframe(detail_inv_moves[cols].sort_values("dt") if "dt" in cols else detail_inv_moves[cols], width="stretch")

with st.expander(f"🔁 Restocks — Excluded from Reconciliation ({int(restock_qty)} units)", expanded=False):
    st.caption("These are proactive pharmacy refills, not returns triggered by a Pyxis unload. Shown here for reference only.")
    if detail_restocks.empty:
        st.info("No restocks found for this date range.")
    else:
        cols = [c for c in ["dt", "date", "user_name", "med_desc", "qty", "workflow_type"] if c in detail_restocks.columns]
        st.dataframe(detail_restocks[cols].sort_values("dt") if "dt" in cols else detail_restocks[cols], width="stretch")

with st.expander(f"⚙️ Unload Eject Events — Excluded from Reconciliation ({int(eject_qty)} units)", expanded=False):
    st.caption("These are broken cassette eject events, not real medication removals. Shown here for reference only.")
    if unload_eject.empty:
        st.info("No unload eject events found for this date range.")
    else:
        cols = [c for c in ["dt", "date", "user_name", "device", "med_desc", "qty", "event_type"] if c in unload_eject.columns]
        st.dataframe(unload_eject[cols].sort_values("dt") if "dt" in cols else unload_eject[cols], width="stretch")

with st.expander(f"Pyxis Non-Unload Removal Events - Reference Only ({int(reference_removal_qty)} units)", expanded=False):
    st.caption("These are empty return bin, return-bin, or destock rows. They are visible for context but excluded from the simplified unload-only reconciliation.")
    if detail_pyxis_reference_removals.empty:
        st.info("No non-unload Pyxis removal rows found for this date range.")
    else:
        cols = [
            c for c in ["dt", "date", "user_name", "device", "event_type", "med_id", "med_desc", "qty"]
            if c in detail_pyxis_reference_removals.columns
        ]
        st.dataframe(
            detail_pyxis_reference_removals[cols].sort_values("dt") if "dt" in cols else detail_pyxis_reference_removals[cols],
            width="stretch",
            hide_index=True,
        )

with st.expander(f"Likely Bulk/Buyer Overstock Returns — Excluded from Reconciliation ({int(bulk_package_qty)} units)", expanded=False):
    st.caption("These are carousel return rows with clean bulk quantities or high quantities after 14:30. They often represent packaged meds or buyer overstock-shelf moves entered through return instead of restock/receiving.")
    if bulk_package_returns.empty:
        st.info("No likely packaging bulk returns were excluded for this date range.")
    else:
        cols = [c for c in ["dt", "date", "user_name", "med_id", "med_desc", "qty", "workflow_type", "priority"] if c in bulk_package_returns.columns]
        st.dataframe(bulk_package_returns[cols].sort_values("dt") if "dt" in cols else bulk_package_returns[cols], width="stretch")

with st.expander(f"Early/Late Carousel Returns — Excluded from Reconciliation ({int(timing_excluded_qty)} units)", expanded=False):
    st.caption(f"These carousel return rows did not happen after a matching Pyxis unload within the {return_match_window_hours}-hour return window.")
    if detail_timing_excluded_returns.empty:
        st.info("No carousel returns were excluded by timing for this selection.")
    else:
        cols = [
            c for c in [
                "dt", "nearest_unload_dt", "timing_exclusion_reason", "date", "user_name",
                "med_id", "med_desc", "qty", "workflow_type", "priority"
            ]
            if c in detail_timing_excluded_returns.columns
        ]
        st.dataframe(
            detail_timing_excluded_returns[cols].sort_values("dt") if "dt" in cols else detail_timing_excluded_returns[cols],
            width="stretch",
            hide_index=True,
            column_config={
                "dt": st.column_config.DatetimeColumn("Carousel Return Time"),
                "nearest_unload_dt": st.column_config.DatetimeColumn("Nearest Pyxis Unload"),
                "timing_exclusion_reason": "Reason",
                "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
            },
        )
