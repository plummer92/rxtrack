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

# Optional filter controls
exclude_controls = st.checkbox("Exclude Controlled Substances")
exclude_dummy = st.checkbox("Exclude Dummy Medications", value=True)

df_events, _, df_pharm, _, _ = load_data(start_date, end_date)

if df_events.empty and df_pharm.empty:
    st.warning("No data found for selected dates.")
    st.stop()

# Ensure datetime safety
for df in [df_events, df_pharm]:
    if not df.empty and 'dt' in df.columns:
        df['dt'] = pd.to_datetime(df['dt'], errors='coerce')


# ----------------------------------------------------
# 2️⃣ Identify Workflow Events
# ----------------------------------------------------

pyxis_unload = pd.DataFrame()
pharm_return = pd.DataFrame()

# ---------------- PYXIS UNLOAD ----------------
if not df_events.empty and 'event_type' in df_events.columns:

    pyxis_unload = df_events[
        df_events['event_type'].astype(str).str.contains(
            "empty|unload|return bin",
            case=False,
            na=False
        )
    ].copy()

    # Hard exclude cassette devices
    if 'device' in pyxis_unload.columns:
        pyxis_unload = pyxis_unload[
            ~pyxis_unload['device'].astype(str).str.contains(
                "cass|patient",
                case=False,
                na=False
            )
        ]

# ---------------- PHARM RETURN ----------------
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
# 3️⃣ Remove Dummy Medications
# ----------------------------------------------------

def remove_dummy_med(df):
    if df.empty:
        return df

    if 'med_desc' not in df.columns or 'med_id' not in df.columns:
        return df

    mask = (
        df['med_desc'].astype(str).str.contains("cassette", case=False, na=False) |
        df['med_id'].astype(str).isin(['99995'])
    )

    return df[~mask]

if exclude_dummy:
    pyxis_unload = remove_dummy_med(pyxis_unload)
    pharm_return = remove_dummy_med(pharm_return)


# ----------------------------------------------------
# 4️⃣ Optional Control Exclusion
# ----------------------------------------------------

def remove_controls(df):
    if df.empty:
        return df

    # Structured flags first
    if 'is_control' in df.columns:
        return df[df['is_control'] != True]

    if 'control_flag' in df.columns:
        return df[df['control_flag'] != True]

    if 'schedule' in df.columns:
        return df[df['schedule'].isna()]

    # Fallback keyword detection
    if 'med_desc' in df.columns:
        mask = df['med_desc'].astype(str).str.contains(
            "CII|CIII|CIV|CV|control|narc|morphine|hydromorphone|oxycodone|fentanyl|amphetamine|methylphenidate",
            case=False,
            na=False
        )
        return df[~mask]

    return df

if exclude_controls:
    pyxis_unload = remove_controls(pyxis_unload)
    pharm_return = remove_controls(pharm_return)


# ----------------------------------------------------
# 5️⃣ Normalize Dates
# ----------------------------------------------------

if not pyxis_unload.empty:
    pyxis_unload['date'] = pyxis_unload['dt'].dt.date

if not pharm_return.empty:
    pharm_return['date'] = pharm_return['dt'].dt.date


# ----------------------------------------------------
# 6️⃣ Aggregate Quantities
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
# 7️⃣ Reconciliation Engine
# ----------------------------------------------------

pyxis_merge = pyxis_sum.drop(columns=['med_desc'], errors='ignore')
pharm_merge = pharm_sum.drop(columns=['med_desc'], errors='ignore')

recon = pd.merge(
    pyxis_merge,
    pharm_merge,
    on=['med_id', 'date'],
    how='outer'
)

recon['qty_pyxis'] = recon.get('qty_pyxis', 0)
recon['qty_pharm'] = recon.get('qty_pharm', 0)

recon[['qty_pyxis', 'qty_pharm']] = recon[['qty_pyxis', 'qty_pharm']].fillna(0)

# Attach med description
med_lookup = pd.concat([
    pyxis_sum[['med_id', 'med_desc']],
    pharm_sum[['med_id', 'med_desc']]
]).drop_duplicates('med_id')

if not med_lookup.empty:
    recon = recon.merge(med_lookup, on='med_id', how='left')

recon['difference'] = recon['qty_pyxis'] - recon['qty_pharm']


# ----------------------------------------------------
# 8️⃣ Executive Metrics
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
# 9️⃣ Variance Table + Drilldown
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
            st.dataframe(
                unload_detail[['dt', 'user_name', 'device', 'qty']],
                use_container_width=True
            )

        with c2:
            st.markdown("### 🟩 Pharmacy Return/Restock Events")
            st.dataframe(
                return_detail[['dt', 'user_name', 'destination', 'qty']],
                use_container_width=True
            )

        st.metric("Net Quantity Difference", int(selected['difference']))


# ----------------------------------------------------
# 🔎 Debug
# ----------------------------------------------------

with st.expander("🛠 Debug Info"):
    st.write("Pyxis unload rows:", len(pyxis_unload))
    st.write("Pharmacy return rows:", len(pharm_return))
