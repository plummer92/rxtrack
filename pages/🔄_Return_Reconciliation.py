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

for df in [df_events, df_pharm]:
    if not df.empty and "dt" in df.columns:
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")

# ----------------------------------------------------
# 3️⃣ Identify Workflow Events
# ----------------------------------------------------

pyxis_unload = pd.DataFrame()
pharm_activity = pd.DataFrame()

# -------- PYXIS UNLOAD --------
if not df_events.empty and "event_type" in df_events.columns:
    pyxis_unload = df_events[
        df_events["event_type"].astype(str).str.contains(
            "empty|unload|return bin",
            case=False,
            na=False
        )
    ].copy()

    if "device" in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            ~pyxis_unload["device"].astype(str).str.contains(
                "cass|patient",
                case=False,
                na=False
            )
        ]

# -------- PHARMACY ACTIVITY --------
if not df_pharm.empty:
    pharm_df = df_pharm.copy()

    event_col = "priority" if "priority" in pharm_df.columns else "event_type"

    pharm_activity = pharm_df[
        pharm_df[event_col].astype(str).str.contains(
            "return|restock|instant|inventory|move",
            case=False,
            na=False
        )
    ].copy()

# ----------------------------------------------------
# 4️⃣ Load Master Mapping (Control Identification)
# ----------------------------------------------------

with engine.connect() as conn:
    result = conn.execute(
        text("SELECT med_id, carousel_location FROM carousel_master_mapping")
    )
    rows = result.fetchall()
    df_master = pd.DataFrame(rows, columns=result.keys())

if not pyxis_unload.empty:
    pyxis_unload = pyxis_unload.merge(df_master, on="med_id", how="left")

if not pharm_activity.empty:
    pharm_activity = pharm_activity.merge(df_master, on="med_id", how="left")

# ----------------------------------------------------
# 5️⃣ Filters
# ----------------------------------------------------

all_users = sorted(list(set(
    list(pyxis_unload.get("user_name", pd.Series()).dropna().unique()) +
    list(pharm_activity.get("user_name", pd.Series()).dropna().unique())
)))

selected_users = st.multiselect("Filter by User (Optional)", options=all_users)

exclude_controls = st.checkbox("Exclude Controlled Substances (CW)")
exclude_dummy = st.checkbox("Exclude Dummy Medications", value=True)

if selected_users:
    if "user_name" in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            pyxis_unload["user_name"].isin(selected_users)
        ]
    if "user_name" in pharm_activity.columns:
        pharm_activity = pharm_activity[
            pharm_activity["user_name"].isin(selected_users)
        ]

def remove_dummy(df):
    if df.empty or "med_desc" not in df.columns:
        return df
    return df[~df["med_desc"].astype(str).str.contains("cassette", case=False, na=False)]

def remove_controls(df):
    if df.empty or "carousel_location" not in df.columns:
        return df
    return df[~df["carousel_location"].astype(str).str.contains("CW", case=False, na=False)]

if exclude_dummy:
    pyxis_unload = remove_dummy(pyxis_unload)
    pharm_activity = remove_dummy(pharm_activity)

if exclude_controls:
    pyxis_unload = remove_controls(pyxis_unload)
    pharm_activity = remove_controls(pharm_activity)

# ----------------------------------------------------
# 6️⃣ Normalize Date
# ----------------------------------------------------

def ensure_date(df):
    if not df.empty:
        df["date"] = pd.to_datetime(df["dt"], errors="coerce").dt.date
    return df

pyxis_unload = ensure_date(pyxis_unload)
pharm_activity = ensure_date(pharm_activity)

# ----------------------------------------------------
# 7️⃣ Aggregate with Movement Type
# ----------------------------------------------------

def group_pyxis(df):
    if df.empty:
        return pd.DataFrame(columns=["med_id", "date", "qty_pyxis"])
    return (
        df.groupby(["med_id", "date"])["qty"]
        .sum()
        .reset_index()
        .rename(columns={"qty": "qty_pyxis"})
    )

def group_pharm(df):
    if df.empty:
        return pd.DataFrame(columns=["med_id", "date", "qty_pharm", "movement_type"])

    grouped = (
        df.groupby(["med_id", "date"])
        .agg(
            qty_pharm=("qty", "sum"),
            movement_type=("priority", lambda x: ", ".join(x.dropna().unique()))
        )
        .reset_index()
    )

    return grouped

pyxis_sum = group_pyxis(pyxis_unload)
pharm_sum = group_pharm(pharm_activity)

# ----------------------------------------------------
# 8️⃣ Merge
# ----------------------------------------------------

recon = pd.merge(
    pyxis_sum,
    pharm_sum,
    on=["med_id", "date"],
    how="outer"
)

recon[["qty_pyxis", "qty_pharm"]] = recon[
    ["qty_pyxis", "qty_pharm"]
].fillna(0)

recon["movement_type"] = recon.get("movement_type", "None").fillna("None")

# Restore med_desc
med_lookup = pd.concat([
    pyxis_unload[["med_id", "med_desc"]],
    pharm_activity[["med_id", "med_desc"]]
]).drop_duplicates("med_id")

recon = recon.merge(med_lookup, on="med_id", how="left")

recon["difference"] = recon["qty_pyxis"] - recon["qty_pharm"]

# Flag inventory moves clearly
recon["event_flag"] = np.where(
    recon["movement_type"].str.contains("inventory", case=False),
    "Inventory Move",
    "Return Workflow"
)

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
m2.metric("Total Pharmacy Activity Qty", int(total_return))
m3.metric("Reconciliation %", f"{recon_pct:.2f}%")
m4.metric("Unmatched Med-Days", len(unmatched))

st.divider()

# ----------------------------------------------------
# 🔟 Variance Table
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

    st.dataframe(display, use_container_width=True)
