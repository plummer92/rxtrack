import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(page_title="Return Reconciliation", page_icon="🔄", layout="wide")

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
    exclude_controls = st.checkbox("Exclude Controlled Substances")
    exclude_dummy    = st.checkbox("Exclude Dummy Medications", value=True)

# --- Identify Workflow Events ---

pyxis_unload = pd.DataFrame()
pharm_all = pd.DataFrame()

if not df_events.empty and "event_type" in df_events.columns:
    pyxis_all_raw = df_events[
        df_events["event_type"].astype(str).str.contains("empty|unload|return bin", case=False, na=False) &
        ~df_events["event_type"].astype(str).str.contains("cancelled", case=False, na=False)
    ].copy()

    # Unload Eject = broken cassette, not a real medication removal — split out for reference
    unload_eject = pyxis_all_raw[
        pyxis_all_raw["event_type"].astype(str).str.contains("eject", case=False, na=False)
    ].copy()
    pyxis_unload = pyxis_all_raw[
        ~pyxis_all_raw["event_type"].astype(str).str.contains("eject", case=False, na=False)
    ].copy()
    if "device" in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            ~pyxis_unload["device"].astype(str).str.contains("cass|patient", case=False, na=False)
        ]
    pyxis_reload = df_events[
        df_events["event_type"].astype(str).str.contains(r"restock|refill|\bload\b|replenish", case=False, na=False) &
        ~df_events["event_type"].astype(str).str.contains("cancel|unload|empty", case=False, na=False)
    ].copy()
    if "device" in pyxis_reload.columns:
        pyxis_reload = pyxis_reload[
            ~pyxis_reload["device"].astype(str).str.contains("cass|patient", case=False, na=False)
        ]
else:
    unload_eject = pd.DataFrame()
    pyxis_reload = pd.DataFrame()

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

inv_moves = pharm_all[pharm_all["workflow_type"] == "Inventory Move"].copy() if not pharm_all.empty else pd.DataFrame()
restocks   = pharm_all[pharm_all["workflow_type"] == "Restock"].copy() if not pharm_all.empty else pd.DataFrame()
pharm_return = pharm_all[~pharm_all["workflow_type"].isin(EXCLUDED_TYPES)].copy() if not pharm_all.empty else pd.DataFrame()

# --- Apply User Filter ---

if selected_users:
    if not pyxis_unload.empty: pyxis_unload = pyxis_unload[pyxis_unload["user_name"].isin(selected_users)]
    if not pyxis_reload.empty: pyxis_reload = pyxis_reload[pyxis_reload["user_name"].isin(selected_users)]
    if not pharm_return.empty: pharm_return = pharm_return[pharm_return["user_name"].isin(selected_users)]
    if not inv_moves.empty: inv_moves = inv_moves[inv_moves["user_name"].isin(selected_users)]
    if not restocks.empty: restocks = restocks[restocks["user_name"].isin(selected_users)]

# --- Apply Med Filters ---

def remove_dummy(df):
    if df.empty or "med_desc" not in df.columns: return df
    return df[~df["med_desc"].astype(str).str.contains("cassette", case=False, na=False)]

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

if exclude_dummy:
    pyxis_unload = remove_dummy(pyxis_unload)
    pyxis_reload = remove_dummy(pyxis_reload)
    pharm_return = remove_dummy(pharm_return)

if exclude_controls:
    pyxis_unload = remove_controls(pyxis_unload)
    pyxis_reload = remove_controls(pyxis_reload)
    pharm_return = remove_controls(pharm_return)

# --- Normalize Date ---

def ensure_date_column(df):
    if not df.empty and "date" not in df.columns:
        df["date"] = pd.to_datetime(df.get("dt"), errors="coerce").dt.date
    return df

pyxis_unload = ensure_date_column(pyxis_unload)
pyxis_reload = ensure_date_column(pyxis_reload)
pharm_return = ensure_date_column(pharm_return)
inv_moves    = ensure_date_column(inv_moves)
restocks     = ensure_date_column(restocks)
unload_eject = ensure_date_column(unload_eject)

# --- Aggregate ---

def safe_group(df, qty_name):
    if df.empty or not {"med_id", "med_desc", "date", "qty"}.issubset(df.columns):
        return pd.DataFrame(columns=["med_id", "med_desc", "date", qty_name])
    return df.groupby(["med_id", "med_desc", "date"])["qty"].sum().reset_index().rename(columns={"qty": qty_name})

pyxis_sum = safe_group(pyxis_unload, "qty_pyxis")
pharm_sum = safe_group(pharm_return, "qty_pharm")

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
recon["difference"] = recon["qty_pyxis"] - recon["qty_pharm"]

# --- Executive Metrics ---

total_unload = recon["qty_pyxis"].sum()
total_return = recon["qty_pharm"].sum()
recon_pct = (min(total_unload, total_return) / total_unload * 100) if total_unload > 0 else 100
unmatched = recon[recon["difference"] != 0]
inv_move_qty  = inv_moves["qty"].sum() if not inv_moves.empty and "qty" in inv_moves.columns else 0
restock_qty   = restocks["qty"].sum() if not restocks.empty and "qty" in restocks.columns else 0
eject_qty     = unload_eject["qty"].sum() if not unload_eject.empty and "qty" in unload_eject.columns else 0

m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
m1.metric("Total Pyxis Unload Qty", int(total_unload))
m2.metric("Total Pharmacy Return Qty", int(total_return))
m3.metric("Reconciliation %", f"{recon_pct:.1f}%")
m4.metric("Unmatched Med-Days", len(unmatched))
m5.metric("Inv Moves (excl.)", int(inv_move_qty))
m6.metric("Restocks (excl.)", int(restock_qty))
m7.metric("Eject Events (excl.)", int(eject_qty))

st.divider()

# --- Unload -> Reload Loop Analysis ---

st.subheader("🔁 Unload to Same-Device Reload Timing")
st.caption("How long after a med is unloaded from a Pyxis device does that exact med get loaded back into that same device?")

reload_window_days = st.slider(
    "Reload window (days)",
    min_value=1,
    max_value=90,
    value=28,
    help="Counts a loop when the same med is reloaded to the same device within this many days after unload.",
)


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

    base_cols = [
        "dt", "date", "user_name", "device", "med_id", "med_desc", "qty", "event_type"
    ]
    working = unload_df[[c for c in base_cols if c in unload_df.columns]].copy()
    working = working.rename(columns={
        "dt": "unload_dt",
        "date": "unload_date",
        "user_name": "unload_user",
        "qty": "unload_qty",
        "event_type": "unload_event_type",
    })
    working["reload_dt"] = pd.NaT
    working["reload_user"] = None
    working["reload_qty"] = np.nan
    working["reload_event_type"] = None
    working["days_to_reload"] = np.nan
    working["hours_to_reload"] = np.nan

    if reload_df.empty:
        working["reload_bucket"] = "Not reloaded in window"
        working["reloaded_within_window"] = False
        return working

    grouped_reload = {
        key: grp.sort_values("dt").reset_index(drop=True)
        for key, grp in reload_df.groupby(["med_id", "device"], dropna=False)
    }

    max_delta = pd.Timedelta(days=max_days)
    for idx, row in working.iterrows():
        key = (row.get("med_id"), row.get("device"))
        candidates = grouped_reload.get(key)
        if candidates is None or candidates.empty or pd.isna(row.get("unload_dt")):
            continue
        future = candidates[candidates["dt"] > row["unload_dt"]]
        if future.empty:
            continue
        next_row = future.iloc[0]
        delta = next_row["dt"] - row["unload_dt"]
        if delta > max_delta:
            continue
        working.at[idx, "reload_dt"] = next_row["dt"]
        working.at[idx, "reload_user"] = next_row.get("user_name")
        working.at[idx, "reload_qty"] = next_row.get("qty")
        working.at[idx, "reload_event_type"] = next_row.get("event_type")
        working.at[idx, "days_to_reload"] = delta.total_seconds() / 86400
        working.at[idx, "hours_to_reload"] = delta.total_seconds() / 3600

    working["reload_bucket"] = working["days_to_reload"].apply(bucket_reload_days)
    working["reloaded_within_window"] = working["reload_dt"].notna()
    return working


reload_pairs = build_reload_pairs(pyxis_unload, pyxis_reload, reload_window_days)

if reload_pairs.empty:
    st.info("No Pyxis unload events available for reload timing analysis in this date range.")
else:
    loop_ct = int(reload_pairs["reloaded_within_window"].sum())
    loop_rate = (loop_ct / len(reload_pairs) * 100) if len(reload_pairs) else 0
    median_reload_days = reload_pairs.loc[reload_pairs["reloaded_within_window"], "days_to_reload"].median()

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Unload Events", f"{len(reload_pairs):,}")
    r2.metric(f"Reloaded Within {reload_window_days}d", f"{loop_ct:,}")
    r3.metric("Loop Rate", f"{loop_rate:.1f}%")
    r4.metric("Median Days to Reload", f"{median_reload_days:.1f}" if pd.notna(median_reload_days) else "n/a")

    bucket_order = ["0-3 days", "4-7 days", "8-14 days", "15-28 days", "29+ days", "Not reloaded in window"]
    bucket_summary = (
        reload_pairs.groupby("reload_bucket", as_index=False)
        .agg(
            unload_events=("med_id", "count"),
            distinct_meds=("med_id", "nunique"),
            distinct_devices=("device", "nunique"),
        )
    )
    bucket_summary["reload_bucket"] = pd.Categorical(bucket_summary["reload_bucket"], categories=bucket_order, ordered=True)
    bucket_summary = bucket_summary.sort_values("reload_bucket")

    med_loop_summary = (
        reload_pairs.groupby(["med_id", "med_desc"], as_index=False)
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

    chart_col, med_col = st.columns(2)
    with chart_col:
        fig_loop = px.bar(
            bucket_summary,
            x="reload_bucket",
            y="unload_events",
            labels={"reload_bucket": "", "unload_events": "Unload Events"},
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

    reload_display = reload_pairs.sort_values(
        ["reloaded_within_window", "days_to_reload", "unload_dt"],
        ascending=[False, True, False],
    )
    st.dataframe(
        reload_display[[
            "unload_dt", "device", "med_desc", "unload_qty", "unload_user",
            "reload_dt", "reload_user", "reload_qty", "days_to_reload", "reload_bucket"
        ]],
        width="stretch",
        hide_index=True,
        column_config={
            "unload_dt": st.column_config.DatetimeColumn("Unload Time", format="MM/DD/YY HH:mm"),
            "reload_dt": st.column_config.DatetimeColumn("Reload Time", format="MM/DD/YY HH:mm"),
            "unload_qty": st.column_config.NumberColumn("Unload Qty", format="%.0f"),
            "reload_qty": st.column_config.NumberColumn("Reload Qty", format="%.0f"),
            "days_to_reload": st.column_config.NumberColumn("Days to Reload", format="%.1f"),
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

st.divider()

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

        unload_detail = pyxis_unload[
            (pyxis_unload["med_id"] == med_id) & (pyxis_unload["date"] == date)
        ].sort_values("dt")

        return_detail = pharm_return[
            (pharm_return["med_id"] == med_id) & (pharm_return["date"] == date)
        ].sort_values("dt")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### 🟦 Pyxis Unload Events")
            st.dataframe(unload_detail[["dt", "user_name", "device", "qty"]], width="stretch")
        with c2:
            st.markdown("### 🟩 Pharmacy Return Events")
            st.dataframe(return_detail[["dt", "user_name", "workflow_type", "qty"]], width="stretch")

# --- Inventory Moves (reference only, excluded from reconciliation) ---

st.divider()
with st.expander(f"📦 Inventory Moves — Excluded from Reconciliation ({int(inv_move_qty)} units)", expanded=False):
    st.caption("These are surplus-to-working-inventory transfers, not Pyxis returns. They are shown here for reference only.")
    if inv_moves.empty:
        st.info("No inventory moves found for this date range.")
    else:
        cols = [c for c in ["dt", "date", "user_name", "med_desc", "qty", "workflow_type"] if c in inv_moves.columns]
        st.dataframe(inv_moves[cols].sort_values("dt") if "dt" in cols else inv_moves[cols], width="stretch")

with st.expander(f"🔁 Restocks — Excluded from Reconciliation ({int(restock_qty)} units)", expanded=False):
    st.caption("These are proactive pharmacy refills, not returns triggered by a Pyxis unload. Shown here for reference only.")
    if restocks.empty:
        st.info("No restocks found for this date range.")
    else:
        cols = [c for c in ["dt", "date", "user_name", "med_desc", "qty", "workflow_type"] if c in restocks.columns]
        st.dataframe(restocks[cols].sort_values("dt") if "dt" in cols else restocks[cols], width="stretch")

with st.expander(f"⚙️ Unload Eject Events — Excluded from Reconciliation ({int(eject_qty)} units)", expanded=False):
    st.caption("These are broken cassette eject events, not real medication removals. Shown here for reference only.")
    if unload_eject.empty:
        st.info("No unload eject events found for this date range.")
    else:
        cols = [c for c in ["dt", "date", "user_name", "device", "med_desc", "qty", "event_type"] if c in unload_eject.columns]
        st.dataframe(unload_eject[cols].sort_values("dt") if "dt" in cols else unload_eject[cols], width="stretch")

