import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from App import engine, normalize_name 

st.set_page_config(page_title="RxTrack Brain", page_icon="🧠", layout="wide")

@st.cache_data(ttl=600)
def load_all_time_data():
    # Pull core tables using SQLAlchemy engine
    df_e = pd.read_sql("SELECT * FROM events", engine)
    df_p = pd.read_sql("SELECT * FROM pharmacy_orders", engine)
    df_s = pd.read_sql("SELECT * FROM staff_schedule", engine)

    # -------- GLOBAL SANITATION --------
    if not df_e.empty:
        df_e["user_name"] = (
            df_e["user_name"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
            .replace(["None", "none", ""], "unknown")
        )

        for col in ["qty", "discrepancy_qty", "beginning_qty", "ending_qty"]:
            if col in df_e.columns:
                df_e[col] = pd.to_numeric(df_e[col], errors="coerce").fillna(0)

        df_e["dt"] = pd.to_datetime(df_e["dt"], errors="coerce")
        df_e["date_only"] = df_e["dt"].dt.date

        df_e["match_key"] = df_e["user_name"].apply(
            lambda x: normalize_name(x) if isinstance(x, str) and len(x) > 1 else "unknown"
        )

    if not df_s.empty:
        df_s["staff_name"] = (
            df_s["staff_name"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
            .replace(["None", "none", ""], "unknown")
        )

        df_s["date_obj"] = pd.to_datetime(df_s["dt"], errors="coerce").dt.date

        df_s["match_key"] = df_s["staff_name"].apply(
            lambda x: normalize_name(x) if isinstance(x, str) and len(x) > 1 else "unknown"
        )

    return df_e, df_p, df_s

    st.header("🧠 RxTrack Intelligence Engine")
    st.caption("Scanning global historical data for trends and anomalies.")
    
    # Prevent NameErrors
    df_events_all = pd.DataFrame()
    df_pharm_all = pd.DataFrame()
    df_sched_all = pd.DataFrame()
    
    try:
        df_events_all, df_pharm_all, df_sched_all = load_all_time_data()
    
        # -------------------------
        # 1️⃣ Burn Rate Prediction
        # -------------------------
        if not df_events_all.empty:
            last_time = df_events_all["dt"].max()
            recent_24h = df_events_all[
                df_events_all["dt"] > (last_time - pd.Timedelta(hours=24))
            ].copy()
    
            burn = (
                recent_24h.dropna(subset=["ending_qty"])
                .groupby(["device", "med_desc"])
                .agg(
                    pulled=("qty", "sum"),
                    current_inv=("ending_qty", "last")
                )
                .reset_index()
            )
    
            burn["hrs_left"] = burn["current_inv"] / (
                (burn["pulled"] / 24).replace(0, np.nan)
            )
    
            critical = burn[burn["hrs_left"] < 12].sort_values("hrs_left")
    
            if not critical.empty:
                st.error(
                    f"🚩 High Alert: {len(critical)} imminent stockout risks found."
                )
                st.dataframe(critical, use_container_width=True)
    
        # -------------------------
        # 2️⃣ Drift Auditor
        # -------------------------
        st.divider()
        st.subheader("🕵️ Global Drift Auditor")
    
        drift_df = df_events_all.dropna(
            subset=["beginning_qty", "ending_qty"]
        ).copy()
    
        drift_df["expected"] = drift_df["beginning_qty"] - drift_df["qty"]
    
        mismatch = drift_df[
            drift_df["ending_qty"] != drift_df["expected"]
        ]
    
        if not mismatch.empty:
            st.warning(
                f"Found {len(mismatch)} historical count discrepancies."
            )
            st.dataframe(
                mismatch[
                    [
                        "dt",
                        "device",
                        "med_desc",
                        "qty",
                        "beginning_qty",
                        "ending_qty",
                    ]
                ],
                use_container_width=True,
            )
    
    except Exception as e:
        st.error(f"Brain Scan failed: {e}")

