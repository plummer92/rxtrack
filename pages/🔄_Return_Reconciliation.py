import streamlit as st
import pandas as pd
import numpy as np
from sqlalchemy import text
from App import load_data, engine

st.set_page_config(
    page_title="Return Reconciliation",
    page_icon="🔄",
    layout="wide"
)

st.header("🔄 Closed-Loop Return Integrity Engine")
st.caption("Validating Pyxis unload workflow against Pharmacy return/restock activity.")

# ----------------------------------------------------
# 1️⃣ Date Selection
# ----------------------------------------------------

c1, c2 = st.columns(2)
start_date = c1.date_input("Start Date")
end_date = c2.date_input("End Date")

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

# ----------------------------------------------------
# 2️⃣ Load Data
# ----------------------------------------------------

df_events, _, df_pharm, _, _ = load_data(start_date, end_date)

if df_events.empty and df_pharm.empty:
    st.warning("No data found for selected dates.")
    st.stop()

# Ensure datetime safety
for df in [df_events, df_pharm]:
    if not df.empty and "dt" in df.columns:
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")

# ----------------------------------------------------
# 3️⃣ Identify Workflow Events
# ----------------------------------------------------

pyxis_unload = pd.DataFrame()
pharm_return = pd.DataFrame()

# -------- PYXIS UNLOAD --------
if not df_events.empty and "event_type" in df_events.columns:
    pyxis_unload = df_events[
        df_events["event_type"].astype(str).str.contains(
            "empty|unload|return bin",
            case=False,
            na=False
        )
    ].copy()

    # Exclude cassette & patient devices
    if "device" in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            ~pyxis_unload["device"].astype(str).str.contains(
                "cass|patient",
                case=False,
                na=False
            )
        ]

# -------- PHARMACY RETURN --------
if not df_pharm.empty:
    pharm_df = df_pharm.copy()

    event_col = None
    if "event_type" in pharm_df.columns:
        event_col = "event_type"
    elif "priority" in pharm_df.columns:
        event_col = "priority"

    if event_col:
        pharm_return = pharm_df[
            pharm_df[event_col].astype(str).str.contains(
                "return|restock|instant",
                case=False,
                na=False
            )
        ].copy()

# ----------------------------------------------------
# 4️⃣ Load Master Mapping (For Control Classification)
# ----------------------------------------------------

with engine.connect() as conn:
    result = conn.execute(
        text("SELECT med_id, carousel_location FROM carousel_master_mapping")
    )
    rows = result.fetchall()
    df_master = pd.DataFrame(rows, columns=result.keys())

# Merge mapping into both datasets
if not pyxis_unload.empty:
    pyxis_unload = pyxis_unload.merge(
        df_master,
        on="med_id",
        how="left"
    )

if not pharm_return.empty:
    pharm_return = pharm_return.merge(
        df_master,
        on="med_id",
        how="left"
    )

# ----------------------------------------------------
# 5️⃣ Filters
# ----------------------------------------------------

# Build user list
all_users = sorted(list(set(
    list(pyxis_unload.get("user_name", pd.Series()).dropna().unique()) +
    list(pharm_return.get("user_name", pd.Series()).dropna().unique())
)))

selected_users = st.multiselect(
    "Filter by User (Optional)",
    options=all_users
)

exclude_controls = st.checkbox("Exclude Controlled Substances (CW)")
exclude_dummy = st.checkbox("Exclude Dummy Medications", value=True)

# Apply user filter
if selected_users:
    if "user_name" in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            pyxis_unload["user_name"].isin(selected_users)
        ]
    if "user_name" in pharm_return.columns:
        pharm_return = pharm_return[
            pharm_return["user_name"].isin(selected_users)
        ]

# Remove dummy meds
def remove_dummy(df):
    if df.empty or "med_desc" not in df.columns:
        return df
    mask = df["med_desc"].astype(str).str.contains(
        "cassette",
        case=False,
        na=False
    )
    return df[~mask]

if exclude_dummy:
    pyxis_unload = remove_dummy(pyxis_unload)
    pharm_return = remove_dummy(pharm_return)

# Remove controlled via CW mapping
def remove_controls(df):
    if df.empty or "carousel_location" not in df.columns:
        return df
    mask = df["carousel_location"].astype(str).str.contains(
        "CW",
        case=False,
        na=False
    )
    return df[~mask]

if exclude_controls:
    pyxis_unload = remove_controls(pyxis_unload)
    pharm_return = remove_controls(pharm_return)

# ----------------------------------------------------
# 6️⃣ Normalize Date
# ----------------------------------------------------

def ensure_date_column(df):
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["dt"], errors="coerce").dt.date
    return df

pyxis_unload = ensure_date_column(pyxis_unload)
pharm_return = ensure_date_column(pharm_return)

# ----------------------------------------------------
# 7️⃣ Aggregate Safely
# ----------------------------------------------------

def safe_group(df, qty_name):
    if df.empty:
        return pd.DataFrame(
            columns=["med_id", "med_desc", "date", qty_name]
        )

    grouped = (
        df.groupby(["med_id", "med_desc", "date"])["qty"]
        .sum()
        .reset_index()
        .rename(columns={"qty": qty_name})
    )

    return grouped

pyxis_sum = safe_group(pyxis_unload, "qty_pyxis")
pharm_sum = safe_group(pharm_return, "qty_pharm")

# ----------------------------------------------------
# 8️⃣ Merge
# ----------------------------------------------------

recon = pd.merge(
    pyxis_sum.drop(columns=["med_desc"], errors="ignore"),
    pharm_sum.drop(columns=["med_desc"], errors="ignore"),
    on=["med_id", "date"],
    how="outer"
)

recon[["qty_pyxis", "qty_pharm"]] = recon[
    ["qty_pyxis", "qty_pharm"]
].fillna(0)

# Restore med_desc
med_lookup = pd.concat([
    pyxis_sum[["med_id", "med_desc"]],
    pharm_sum[["med_id", "med_desc"]]
]).drop_duplicates("med_id")

recon = recon.merge(med_lookup, on="med_id", how="left")

recon["difference"] = recon["qty_pyxis"] - recon["qty_pharm"]

# ----------------------------------------------------
# 9️⃣ Executive Metrics
# ----------------------------------------------------

total_unload = recon["qty_pyxis"].sum()
total_return = recon["qty_pharm"].sum()

recon_pct = (
    (min(total_unload, total_return) / total_unload) * 100
    if total_unload > 0 else 100
)

unmatched = recon[recon["difference"] != 0]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Pyxis Unload Qty", int(total_unload))
m2.metric("Total Pharmacy Return Qty", int(total_return))
m3.metric("Reconciliation %", f"{recon_pct:.2f}%")
m4.metric("Unmatched Med-Days", len(unmatched))

st.divider()

# ----------------------------------------------------
# 🔟 Variance Table + Drilldown
# ----------------------------------------------------

st.subheader("🚨 Unmatched Workflow Events")

if unmatched.empty:
    st.success("✅ 100% Reconciliation Achieved.")
else:
    display = unmatched.sort_values(
        "difference",
        key=abs,
        ascending=False
    ).reset_index(drop=True)

    event = st.dataframe(
        display,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True
    )

    if len(event.selection.rows) > 0:
        idx = event.selection.rows[0]
        selected = display.iloc[idx]

        med_id = selected["med_id"]
        date = selected["date"]

        st.divider()
        st.subheader(f"🔎 Drilldown: {selected['med_desc']} — {date}")

        unload_detail = pyxis_unload[
            (pyxis_unload["med_id"] == med_id) &
            (pyxis_unload["date"] == date)
        ].sort_values("dt")

        return_detail = pharm_return[
            (pharm_return["med_id"] == med_id) &
            (pharm_return["date"] == date)
        ].sort_values("dt")

        c1, c2 = st.columns(2)

        with c1:
            st.markdown("### 🟦 Pyxis Unload Events")
            st.dataframe(
                unload_detail[["dt", "user_name", "device", "qty"]],
                use_container_width=True
            )

        with c2:
            st.markdown("### 🟩 Pharmacy Return Events")
            st.dataframe(
                return_detail[["dt", "user_name", "destination", "qty"]],
                use_container_width=True
            )
