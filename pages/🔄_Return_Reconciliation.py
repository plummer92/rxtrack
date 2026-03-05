import streamlit as st
import pandas as pd
import numpy as np
from App import load_data, render_sidebar, engine

st.set_page_config(page_title="Return Reconciliation", page_icon="🔄", layout="wide")

start_date, end_date = render_sidebar()

st.header("🔄 Closed-Loop Return Integrity Engine")
st.caption("Validating Pyxis unload workflow against Pharmacy return/restock activity.")

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

selected_users = st.multiselect("Filter by User (Optional)", options=all_users)
exclude_controls = st.checkbox("Exclude Controlled Substances")
exclude_dummy = st.checkbox("Exclude Dummy Medications", value=True)

# --- Identify Workflow Events ---

pyxis_unload = pd.DataFrame()
pharm_all = pd.DataFrame()

if not df_events.empty and "event_type" in df_events.columns:
    pyxis_unload = df_events[
        df_events["event_type"].astype(str).str.contains("empty|unload|return bin", case=False, na=False)
    ].copy()
    if "device" in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            ~pyxis_unload["device"].astype(str).str.contains("cass|patient", case=False, na=False)
        ]

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
        with engine.connect() as conn:
            df = pd.read_sql(
                "SELECT DISTINCT med_id FROM carousel_master_mapping WHERE carousel_location LIKE 'CW%'",
                conn
            )
        ids = set(df["med_id"].dropna().str.strip().str.upper())
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
    pharm_return = remove_dummy(pharm_return)

if exclude_controls:
    pyxis_unload = remove_controls(pyxis_unload)
    pharm_return = remove_controls(pharm_return)

# --- Normalize Date ---

def ensure_date_column(df):
    if not df.empty and "date" not in df.columns:
        df["date"] = pd.to_datetime(df.get("dt"), errors="coerce").dt.date
    return df

pyxis_unload = ensure_date_column(pyxis_unload)
pharm_return = ensure_date_column(pharm_return)
inv_moves = ensure_date_column(inv_moves)
restocks  = ensure_date_column(restocks)

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
inv_move_qty = inv_moves["qty"].sum() if not inv_moves.empty and "qty" in inv_moves.columns else 0
restock_qty  = restocks["qty"].sum() if not restocks.empty and "qty" in restocks.columns else 0

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Total Pyxis Unload Qty", int(total_unload))
m2.metric("Total Pharmacy Return Qty", int(total_return))
m3.metric("Reconciliation %", f"{recon_pct:.2f}%")
m4.metric("Unmatched Med-Days", len(unmatched))
m5.metric("Inv Moves (excl.)", int(inv_move_qty))
m6.metric("Restocks (excl.)", int(restock_qty))

st.divider()

# --- Variance Table + Drilldown ---

st.subheader("🚨 Unmatched Workflow Events")

if unmatched.empty:
    st.success("✅ 100% Reconciliation Achieved.")
else:
    display = unmatched.sort_values("difference", key=abs, ascending=False).reset_index(drop=True)
    event = st.dataframe(display, use_container_width=True, on_select="rerun", selection_mode="single-row", hide_index=True)

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
            st.dataframe(unload_detail[["dt", "user_name", "device", "qty"]], use_container_width=True)
        with c2:
            st.markdown("### 🟩 Pharmacy Return Events")
            st.dataframe(return_detail[["dt", "user_name", "workflow_type", "qty"]], use_container_width=True)

# --- Inventory Moves (reference only, excluded from reconciliation) ---

st.divider()
with st.expander(f"📦 Inventory Moves — Excluded from Reconciliation ({int(inv_move_qty)} units)", expanded=False):
    st.caption("These are surplus-to-working-inventory transfers, not Pyxis returns. They are shown here for reference only.")
    if inv_moves.empty:
        st.info("No inventory moves found for this date range.")
    else:
        cols = [c for c in ["dt", "date", "user_name", "med_desc", "qty", "workflow_type"] if c in inv_moves.columns]
        st.dataframe(inv_moves[cols].sort_values("dt") if "dt" in cols else inv_moves[cols], use_container_width=True)

with st.expander(f"🔁 Restocks — Excluded from Reconciliation ({int(restock_qty)} units)", expanded=False):
    st.caption("These are proactive pharmacy refills, not returns triggered by a Pyxis unload. Shown here for reference only.")
    if restocks.empty:
        st.info("No restocks found for this date range.")
    else:
        cols = [c for c in ["dt", "date", "user_name", "med_desc", "qty", "workflow_type"] if c in restocks.columns]
        st.dataframe(restocks[cols].sort_values("dt") if "dt" in cols else restocks[cols], use_container_width=True)
