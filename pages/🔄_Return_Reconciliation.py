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
# 4️⃣ Load Master Mapping
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
# 5️⃣ Normalize Date
# ----------------------------------------------------

def ensure_date(df):
    if not df.empty:
        df["date"] = pd.to_datetime(df["dt"], errors="coerce").dt.date
    return df

pyxis_unload = ensure_date(pyxis_unload)
pharm_activity = ensure_date(pharm_activity)

# ----------------------------------------------------
# 6️⃣ Aggregate
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

    return (
        df.groupby(["med_id", "date"])
        .agg(
            qty_pharm=("qty", "sum"),
            movement_type=("priority", lambda x: ", ".join(x.dropna().unique()))
        )
        .reset_index()
    )

pyxis_sum = group_pyxis(pyxis_unload)
pharm_sum = group_pharm(pharm_activity)

# ----------------------------------------------------
# 7️⃣ Merge
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

# ----------------------------------------------------
# 🔟 Exclusion System
# ----------------------------------------------------

if "manual_exclusions" not in st.session_state:
    st.session_state.manual_exclusions = set()

recon["recon_key"] = (
    recon["med_id"].astype(str) + "_" +
    recon["date"].astype(str)
)

recon["auto_exclude"] = recon["movement_type"].str.contains(
    "inventory",
    case=False,
    na=False
)

recon["manual_exclude"] = recon["recon_key"].isin(
    st.session_state.manual_exclusions
)

recon["excluded_from_pct"] = (
    recon["auto_exclude"] | recon["manual_exclude"]
)

# ----------------------------------------------------
# 1️⃣1️⃣ Adjusted Metrics
# ----------------------------------------------------

adjusted = recon[~recon["excluded_from_pct"]]

adj_unload = adjusted["qty_pyxis"].sum()
adj_pharm = adjusted["qty_pharm"].sum()

recon_pct = (
    (min(adj_unload, adj_pharm) / adj_unload) * 100
    if adj_unload > 0 else 100
)

unmatched = recon[recon["difference"] != 0]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Adjusted Pyxis Qty", int(adj_unload))
m2.metric("Adjusted Pharm Qty", int(adj_pharm))
m3.metric("Reconciliation %", f"{recon_pct:.2f}%")
m4.metric("Unmatched Med-Days", len(unmatched))

st.divider()

# ----------------------------------------------------
# 1️⃣2️⃣ Interactive Variance Table
# ----------------------------------------------------

st.subheader("🚨 Unmatched Workflow Events")

if unmatched.empty:
    st.success("✅ 100% Reconciliation Achieved.")
else:
    display = unmatched.sort_values(
        "difference",
        key=abs,
        ascending=False
    )

    for _, row in display.iterrows():

        key = row["recon_key"]

        with st.expander(
            f"{row['med_desc']} | {row['date']} | Diff: {row['difference']}"
        ):

            st.write(f"Movement Type: **{row['movement_type']}**")
            st.write(f"Auto-Excluded (Inventory): {row['auto_exclude']}")

            exclude = st.checkbox(
                "Exclude from reconciliation %",
                value=(key in st.session_state.manual_exclusions),
                key=f"exclude_{key}"
            )

            if exclude:
                st.session_state.manual_exclusions.add(key)
            else:
                st.session_state.manual_exclusions.discard(key)

            med_id = row["med_id"]
            date = row["date"]

            unload_detail = pyxis_unload[
                (pyxis_unload["med_id"] == med_id) &
                (pyxis_unload["date"] == date)
            ].sort_values("dt")

            pharm_detail = pharm_activity[
                (pharm_activity["med_id"] == med_id) &
                (pharm_activity["date"] == date)
            ].sort_values("dt")

            c1, c2 = st.columns(2)

            with c1:
                st.markdown("### 🟦 Pyxis Unload")
                st.dataframe(
                    unload_detail[["dt", "user_name", "device", "qty"]],
                    use_container_width=True
                )

            with c2:
                st.markdown("### 🟩 Pharmacy Activity")
                st.dataframe(
                    pharm_detail[["dt", "user_name", "priority", "qty"]],
                    use_container_width=True
                )
