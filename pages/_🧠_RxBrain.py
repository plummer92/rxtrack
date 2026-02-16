import streamlit as st
import pandas as pd
import numpy as np
from App import load_data

st.set_page_config(page_title="RxBrain Intelligence", page_icon="🧠", layout="wide")

if 'start_date' not in st.session_state:
    st.info("👈 Please select a date range on the Home page.")
else:
    df_events, _, df_pharm, _, _ = load_data(st.session_state.start_date, st.session_state.end_date)

    if not df_events.empty:
        st.header("🧠 Predictive Outage Alerts")
        st.caption("Meds at high risk of stocking out based on current 'Burn Rate'.")

        # 1. Calculate Hourly Burn Rate (Last 24 Hours)
        now = df_events['dt'].max()
        last_24h = df_events[df_events['dt'] > (now - pd.Timedelta(hours=24))].copy()
        
        # Aggregate consumption per med per device
        burn_rate = last_24h.groupby(['device', 'med_id', 'med_desc']).agg(
            total_pulled=('qty', 'sum'),
            last_balance=('ending_qty', 'last') # Pulling 'End' from your report
        ).reset_index()

        burn_rate['hourly_rate'] = burn_rate['total_pulled'] / 24

        # 2. Predict Time-to-Zero
        # Calculation: Current Balance / Hourly Burn Rate
        burn_rate['hours_remaining'] = np.where(
            burn_rate['hourly_rate'] > 0, 
            burn_rate['last_balance'] / burn_rate['hourly_rate'], 
            99 # Placeholder for stable inventory
        )

        # 3. Filter for High-Risk Meds (Empty in < 8 hours)
        risky_meds = burn_rate[burn_rate['hours_remaining'] < 8].sort_values('hours_remaining')

        if not risky_meds.empty:
            st.error(f"🚨 {len(risky_meds)} Critical Outage Risks Detected")
            st.dataframe(
                risky_meds[['device', 'med_desc', 'last_balance', 'hourly_rate', 'hours_remaining']],
                width='stretch',
                column_config={
                    "last_balance": "Current Inv",
                    "hourly_rate": "Pulls/Hr",
                    "hours_remaining": st.column_config.NumberColumn("Estimated Hours Left", format="%.1f")
                }
            )
        else:
            st.success("✅ No imminent stockouts predicted for the next 8 hours.")

        # --- BRAIN LOGIC 2: THE "9 PULL" DISCREPANCY ---
        st.divider()
        st.subheader("🕵️ Inventory Drift & Discrepancy Brain")
        # Checking if a transaction qty doesn't match the Beg/End delta
        drift = df_events.copy()
        drift['expected_end'] = drift['beginning_qty'] - drift['qty']
        anomalies = drift[drift['ending_qty'] != drift['expected_end']]

        if not anomalies.empty:
            st.warning(f"⚠️ Found {len(anomalies)} transactions where inventory math doesn't add up.")
            st.dataframe(anomalies[['dt', 'user_name', 'device', 'med_desc', 'qty', 'beginning_qty', 'ending_qty']], width='stretch')
