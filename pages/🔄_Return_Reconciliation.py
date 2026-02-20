import streamlit as st
import pandas as pd
import numpy as np
from App import load_data

st.set_page_config(
    page_title="Return Reconciliation",
    page_icon="🔄",
    layout="wide"
)

st.header("🔄 Closed-Loop Return Integrity Engine")
st.caption("Validating Pyxis unload workflow against Pharmacy return/restock activity.")

# ----------------------------------------------------
# 1️⃣ Independent Date Filter
# ----------------------------------------------------

c1, c2 = st.columns(2)
start_date = c1.date_input("Start Date")
end_date = c2.date_input("End Date")

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

df_events, _, df_pharm, _, _ = load_data(start_date, end_date)

if df_events.empty and df_pharm.empty:
    st.warning("No data found for selected dates.")
    st.stop()

# Ensure datetime
if not df_events.empty and 'dt' in df_events.columns:
    df_events['dt'] = pd.to_datetime(df_events['dt'], errors='coerce')

if not df_pharm.empty and 'dt' in df_pharm.columns:
    df_pharm['dt'] = pd.to_datetime(df_pharm['dt'], errors='coerce')

# ----------------------------------------------------
# 2️⃣ Identify Workflow Events (Hard Cassette Exclusion)
# ----------------------------------------------------

pyxis_unload = pd.DataFrame()
pharm_return = pd.DataFrame()

# 🔹 PYXIS UNLOAD DETECTION
if not df_events.empty and 'event_type' in df_events.columns:

    pyxis_unload = df_events[
        df_events['event_type'].astype(str).str.contains(
            "empty|unload|return bin",
            case=False,
            na=False
        )
    ].copy()

    # HARD EXCLUDE CASSETTE
    if 'device' in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            ~pyxis_unload['device'].astype(str).str.contains(
                "cass|patient",
                case=False,
                na=False
            )
        ]

# 🔹 PHARMACY RETURN DETECTION
if not df_pharm.empty:

    pharm_df = df_pharm.copy()

    if 'event_type' in pharm_df.columns:
        event_col = 'event_type'
    elif 'priority' in pharm_df.columns:
        event_col = 'priority'
    else:
        event_col = None

    if event_col:
        pharm_return = pharm_df[
            pharm_df[event_col].astype(str).str.contains(
                "return|restock|instant",
                case=False,
                na=False
            )
        ].copy()

# ----------------------------------------------------
# 🚫 REMOVE DUMMY CASSETTE MEDICATIONS
# ----------------------------------------------------

def remove_dummy_med(df):
    if df.empty:
        return df

    mask = (
        df['med_desc'].astype(str).str.contains("cassette", case=False, na=False) |
        df['med_id'].astype(str).isin(['99995'])
    )

    return df[~mask]

pyxis_unload = remove_dummy_med(pyxis_unload)
pharm_return = remove_dummy_med(pharm_return)

# ----------------------------------------------------
# 3️⃣ Normalize Date
# ----------------------------------------------------

if not pyxis_unload.empty:
    pyxis_unload['date'] = pyxis_unload['dt'].dt.date

if not pharm_return.empty:
    pharm_return['date'] = pharm_return['dt'].dt.date

# ----------------------------------------------------
# 4️⃣ Aggregate Quantities
# ----------------------------------------------------

pyxis_sum = pd.DataFrame()
pharm_sum = pd.DataFrame()

if not pyxis_unload.empty:
    pyxis_sum = (
        pyxis_unload
        .groupby(['med_id', 'med_desc', 'date'])['qty']
        .sum()
        .reset_index()
        .rename(columns={'qty': 'qty_pyxis'})
    )

if not pharm_return.empty:
    pharm_sum = (
        pharm_return
        .groupby(['med_id', 'med_desc', 'date'])['qty']
        .sum()
        .reset_index()
        .rename(columns={'qty': 'qty_pharm'})
    )

if pyxis_sum.empty and pharm_sum.empty:
    st.warning("No unload/return workflow events found.")
    st.stop()

# ----------------------------------------------------
# 5️⃣ Reconciliation Engine
# ----------------------------------------------------

# Remove med_desc before merge to avoid duplication
pyxis_merge = pyxis_sum.drop(columns=['med_desc'], errors='ignore')
pharm_merge = pharm_sum.drop(columns=['med_desc'], errors='ignore')

recon = pd.merge(
    pyxis_merge,
    pharm_merge,
    on=['med_id', 'date'],
    how='outer'
)

# Ensure quantity columns exist
if 'qty_pyxis' not in recon.columns:
    recon['qty_pyxis'] = 0

if 'qty_pharm' not in recon.columns:
    recon['qty_pharm'] = 0

recon[['qty_pyxis', 'qty_pharm']] = recon[['qty_pyxis', 'qty_pharm']].fillna(0)

# Attach ONE med description
med_lookup = pd.concat([
    pyxis_sum[['med_id', 'med_desc']],
    pharm_sum[['med_id', 'med_desc']]
]).drop_duplicates('med_id')

if not med_lookup.empty:
    recon = recon.merge(med_lookup, on='med_id', how='left')

recon['difference'] = recon['qty_pyxis'] - recon['qty_pharm']

# ----------------------------------------------------
# 6️⃣ Executive Metrics
# ----------------------------------------------------

total_unload = recon['qty_pyxis'].sum()
total_return = recon['qty_pharm'].sum()

reconciliation_pct = (
    (min(total_unload, total_return) / total_unload) * 100
    if total_unload > 0 else 100
)

unmatched = recon[recon['difference'] != 0]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Pyxis Unload Qty", int(total_unload))
m2.metric("Total Pharmacy Return Qty", int(total_return))
m3.metric("Reconciliation %", f"{reconciliation_pct:.2f}%")
m4.metric("Unmatched Med-Days", len(unmatched))

st.divider()

# ----------------------------------------------------
# 7️⃣ Variance Table + Drilldown
# ----------------------------------------------------

st.subheader("🚨 Unmatched Workflow Events")

if unmatched.empty:
    st.success("✅ 100% Reconciliation Achieved.")
else:

    display = unmatched.sort_values(
        'difference',
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

        med_id = selected['med_id']
        date = selected['date']

        st.divider()
        st.subheader(f"🔎 Drilldown: {selected['med_desc']} — {date}")

        unload_detail = pyxis_unload[
            (pyxis_unload['med_id'] == med_id) &
            (pyxis_unload['date'] == date)
        ].sort_values('dt')

        return_detail = pharm_return[
            (pharm_return['med_id'] == med_id) &
            (pharm_return['date'] == date)
        ].sort_values('dt')

        c1, c2 = st.columns(2)

        with c1:
            st.markdown("### 🟦 Pyxis Unload Events")
            if unload_detail.empty:
                st.info("No unload events found.")
            else:
                st.dataframe(
                    unload_detail[['dt', 'user_name', 'device', 'qty']],
                    use_container_width=True
                )

        with c2:
            st.markdown("### 🟩 Pharmacy Return/Restock Events")
            if return_detail.empty:
                st.info("No return events found.")
            else:
                st.dataframe(
                    return_detail[['dt', 'user_name', 'destination', 'qty']],
                    use_container_width=True
                )

        st.metric("Net Quantity Difference", int(selected['difference']))

# ----------------------------------------------------
# 8️⃣ Medication Variance Ranking
# ----------------------------------------------------

st.subheader("💊 Medication Variance Concentration")

if not unmatched.empty:
    med_variance = (
        unmatched
        .groupby('med_desc')['difference']
        .sum()
        .reset_index()
        .sort_values('difference', key=abs, ascending=False)
    )
    st.dataframe(med_variance.head(10), use_container_width=True)
else:
    st.info("No medication-level variance detected.")

# ----------------------------------------------------
# Debug
# ----------------------------------------------------

with st.expander("🛠 Debug Info"):
    st.write("Pyxis unload rows:", len(pyxis_unload))
    st.write("Pharmacy return rows:", len(pharm_return))
