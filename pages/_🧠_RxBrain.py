import streamlit as st
import pandas as pd
import numpy as np
from App import engine, normalize_name

st.header("🧠 RxTrack Intelligence Engine")
st.caption("Scanning global historical data for trends and anomalies.")

@st.cache_data(ttl=600)
def load_all_time_data():
    df_e = pd.read_sql("SELECT * FROM events", engine)
    df_p = pd.read_sql("SELECT * FROM pharmacy_orders", engine)
    df_s = pd.read_sql("SELECT * FROM staff_schedule", engine)

    if not df_e.empty:
        df_e["user_name"] = (
            df_e["user_name"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
        )
        df_e["dt"] = pd.to_datetime(df_e["dt"], errors="coerce")

    return df_e, df_p, df_s

try:
    df_events_all, df_pharm_all, df_sched_all = load_all_time_data()

    if not df_events_all.empty:
        st.success(f"Loaded {len(df_events_all)} events.")

        last_time = df_events_all["dt"].max()
        recent_24h = df_events_all[
            df_events_all["dt"] > (last_time - pd.Timedelta(hours=24))
        ]

        burn = (
            recent_24h.groupby(["device", "med_desc"])
            .agg(
                pulled=("qty", "sum"),
                current_inv=("ending_qty", "last"),
            )
            .reset_index()
        )

        burn["hrs_left"] = burn["current_inv"] / (
            (burn["pulled"] / 24).replace(0, np.nan)
        )

        critical = burn[burn["hrs_left"] < 12]

        if not critical.empty:
            st.error(f"🚩 {len(critical)} imminent stockout risks found.")
            st.dataframe(critical, width="stretch")
        else:
            st.info("No immediate stockout risks detected.")

except Exception as e:
    st.error(f"Brain Scan failed: {e}")
