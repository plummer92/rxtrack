import streamlit as st
import pandas as pd
import numpy as np
from App import load_data, seconds_to_mmss

st.set_page_config(page_title="Return Reconciliation", page_icon="🔄", layout="wide")

st.header("🔄 Return Reconciliation Intelligence")
st.caption("Inventory integrity monitoring and unresolved discrepancy detection.")

if 'start_date' not in st.session_state:
    st.info("👈 Select a date range on Overview first.")
    st.stop()

# Load event data only
df_events, _, _, _, _ = load_data(
    st.session_state.start_date,
    st.session_state.end_date
)

if df_events.empty:
    st.warning("No transaction data found for selected range.")
    st.stop()

# ----------------------------
# 1️⃣ Basic Cleanup
# ----------------------------

df = df_events.copy()
df['dt'] = pd.to_datetime(df['dt'])
df.sort_values(['device', 'med_id', 'dt'], inplace=True)

# ----------------------------
# 2️⃣ Unresolved Discrepancies
# ----------------------------

# Handle optional resolution column safely
if 'resolution_dt' in df.columns:
    unresolved = df[
        (df['discrepancy_qty'].fillna(0) != 0) &
        (df['resolution_dt'].isna())
    ]
else:
    # If no resolution tracking exists, treat all discrepancies as unresolved
    unresolved = df[
        (df['discrepancy_qty'].fillna(0) != 0)
    ]

# ----------------------------
# 3️⃣ Inventory Gap Detection
# ----------------------------

df['next_beginning'] = df.groupby(['device', 'med_id'])['beginning_qty'].shift(-1)
df['inventory_gap'] = df['ending_qty'] - df['next_beginning']

gaps = df[df['inventory_gap'].fillna(0) != 0]

# ----------------------------
# 4️⃣ Return Behavior Analysis
# ----------------------------

returns = df[df['event_type'].str.contains("return", case=False, na=False)]
pulls = df[df['event_type'].str.contains("remove|pull", case=False, na=False)]

user_returns = returns.groupby('user_name')['qty'].count()
user_pulls = pulls.groupby('user_name')['qty'].count()

behavior = pd.concat([user_returns, user_pulls], axis=1).fillna(0)
behavior.columns = ['returns', 'pulls']
behavior['return_ratio'] = behavior['returns'] / behavior['pulls'].replace(0, np.nan)
behavior = behavior.reset_index()

# ----------------------------
# 📊 DASHBOARD METRICS
# ----------------------------

c1, c2, c3 = st.columns(3)
c1.metric("Unresolved Discrepancies", len(unresolved))
c2.metric("Inventory Gaps", len(gaps))
c3.metric("High Return Users", len(behavior[behavior['return_ratio'] > 0.5]))

st.divider()

# ----------------------------
# 📋 SECTION 1: UNRESOLVED
# ----------------------------

st.subheader("🚨 Unresolved Discrepancies")

if unresolved.empty:
    st.success("No unresolved discrepancies detected.")
else:
    st.dataframe(
        unresolved[['dt', 'user_name', 'device', 'med_desc', 'discrepancy_qty', 'discrepancy_reason']],
        use_container_width=True,
        column_config={
            "dt": st.column_config.DatetimeColumn("Time", format="MM/DD HH:mm"),
            "discrepancy_qty": st.column_config.NumberColumn("Qty Diff", format="%.0f")
        }
    )

# ----------------------------
# 📋 SECTION 2: INVENTORY GAPS
# ----------------------------

st.subheader("📦 Inventory Continuity Gaps")

if gaps.empty:
    st.success("No inventory continuity issues detected.")
else:
    st.dataframe(
        gaps[['dt', 'device', 'med_desc', 'ending_qty', 'next_beginning', 'inventory_gap']],
        use_container_width=True,
        column_config={
            "dt": st.column_config.DatetimeColumn("Time", format="MM/DD HH:mm"),
            "inventory_gap": st.column_config.NumberColumn("Gap", format="%.0f")
        }
    )

# ----------------------------
# 📋 SECTION 3: RETURN RISK
# ----------------------------

st.subheader("👤 Technician Return Behavior")

st.dataframe(
    behavior.sort_values('return_ratio', ascending=False),
    use_container_width=True,
    column_config={
        "return_ratio": st.column_config.NumberColumn("Return Ratio", format="%.2f")
    }
)

st.caption("⚠ Return Ratio > 0.50 may indicate excessive pull-return cycles.")
