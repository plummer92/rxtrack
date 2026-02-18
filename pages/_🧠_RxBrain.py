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
