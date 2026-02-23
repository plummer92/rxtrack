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
# 1️⃣ Date Filter
# ----------------------------------------------------

c1, c2 = st.columns(2)
start_date = c1.date_input("Start Date")
end_date = c2.date_input("End Date")

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

exclude_controls = st.checkbox("Exclude Controlled Substances")
exclude_dummy = st.checkbox("Exclude Dummy Medications", value=True)

df_events, _, df_pharm, _, _ = load_data(start_date, end_date)

if df_events.empty and df_pharm.empty:
    st.warning("No data found for selected dates.")
    st.stop()

# Standardize column names
for df in [df_events, df_pharm]:
    if not df.empty:
        df.columns = df.columns.str.strip().str.lower()
        if 'dt' in df.columns:
            df['dt'] = pd.to_datetime(df['dt'], errors='coerce')

# ----------------------------------------------------
# 2️⃣ User Filter
# ----------------------------------------------------

all_users = []

if not df_events.empty and 'user_name' in df_events.columns:
    all_users.extend(df_events['user_name'].dropna().unique())

if not df_pharm.empty and 'user_name' in df_pharm.columns:
    all_users.extend(df_pharm['user_name'].dropna().unique())

all_users = sorted(set(all_users))

selected_users = st.multiselect(
    "Filter by User (Optional)",
    options=all_users
)

# ----------------------------------------------------
# 3️⃣ Identify Workflow Events
# ----------------------------------------------------

pyxis_unload = pd.DataFrame()
pharm_return = pd.DataFrame()

if not df_events.empty and 'event_type' in df_events.columns:
    pyxis_unload = df_events[
        df_events['event_type'].astype(str).str.contains(
            "empty|unload|return bin",
            case=False,
            na=False
        )
    ].copy()

    if 'device' in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            ~pyxis_unload['device'].astype(str).str.contains(
                "cass|patient",
                case=False,
                na=False
            )
        ]

if not df_pharm.empty:
    pharm_df = df_pharm.copy()

    event_col = None
    if 'event_type' in pharm_df.columns:
        event_col = 'event_type'
    elif 'priority' in pharm_df.columns:
        event_col = 'priority'

    if event_col:
        pharm_return = pharm_df[
            pharm_df[event_col].astype(str).str.contains(
                "return|restock|instant",
                case=False,
                na=False
            )
        ].copy()

# ----------------------------------------------------
# 4️⃣ Apply User Filter
# ----------------------------------------------------

if selected_users:
    if not pyxis_unload.empty:
        pyxis_unload = pyxis_unload[
            pyxis_unload['user_name'].isin(selected_users)
        ]

    if not pharm_return.empty:
        pharm_return = pharm_return[
            pharm_return['user_name'].isin(selected_users)
        ]

# ----------------------------------------------------
# 5️⃣ Remove Dummy Meds
# ----------------------------------------------------

def remove_dummy(df):
    if df.empty:
        return df
    if 'med_desc' not in df.columns:
        return df
    mask = df['med_desc'].astype(str).str.contains(
        "cassette",
        case=False,
        na=False
    )
    return df[~mask]

if exclude_dummy:
    pyxis_unload = remove_dummy(pyxis_unload)
    pharm_return = remove_dummy(pharm_return)

# ----------------------------------------------------
# 6️⃣ Remove Controls
# ----------------------------------------------------

def remove_controls(df):
    if df.empty or 'med_desc' not in df.columns:
        return df
    mask = df['med_desc'].astype(str).str.contains(
        "CII|CIII|CIV|CV|morphine|hydromorphone|oxycodone|fentanyl|amphetamine|methylphenidate",
        case=False,
        na=False
    )
    return df[~mask]

if exclude_controls:
    pyxis_unload = remove_controls(pyxis_unload)
    pharm_return = remove_controls(pharm_return)

# ----------------------------------------------------
# 7️⃣ Ensure Required Columns Exist
# ----------------------------------------------------

required_cols = ['med_id', 'med_desc', 'dt', 'qty']

for col in required_cols:
    if col not in pyxis_unload.columns:
        pyxis_unload[col] = np.nan
    if col not in pharm_return.columns:
        pharm_return[col] = np.nan

# Create date column safely
if not pyxis_unload.empty:
    pyxis_unload['date'] = pyxis_unload['dt'].dt.date
else:
    pyxis_unload['date'] = pd.Series(dtype='object')

if not pharm_return.empty:
    pharm_return['date'] = pharm_return['dt'].dt.date
else:
    pharm_return['date'] = pd.Series(dtype='object')

# ----------------------------------------------------
# 8️⃣ Aggregate Safely
# ----------------------------------------------------

pyxis_sum = (
    pyxis_unload
    .groupby(['med_id', 'med_desc', 'date'], dropna=False)['qty']
    .sum()
    .reset_index()
    .rename(columns={'qty': 'qty_pyxis'})
)

pharm_sum = (
    pharm_return
    .groupby(['med_id', 'med_desc', 'date'], dropna=False)['qty']
    .sum()
    .reset_index()
    .rename(columns={'qty': 'qty_pharm'})
)

# ----------------------------------------------------
# 9️⃣ Safe Merge (No More Crashes)
# ----------------------------------------------------

recon = pd.merge(
    pyxis_sum,
    pharm_sum,
    on=['med_id', 'date'],
    how='outer'
)

recon['qty_pyxis'] = recon.get('qty_pyxis', 0)
recon['qty_pharm'] = recon.get('qty_pharm', 0)

recon[['qty_pyxis', 'qty_pharm']] = recon[['qty_pyxis', 'qty_pharm']].fillna(0)

recon['difference'] = recon['qty_pyxis'] - recon['qty_pharm']

# ----------------------------------------------------
# 🔟 Metrics
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
