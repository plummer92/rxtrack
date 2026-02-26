import streamlit as st
import pandas as pd
import numpy as np
from App import engine, normalize_name, render_sidebar

st.set_page_config(page_title="RxBrain", page_icon="🧠", layout="wide")

start_date, end_date = render_sidebar()

st.header("🧠 RxTrack Intelligence Engine")
st.caption("Scanning historical data for trends and anomalies.")

# ----------------------------------------------------
# Rolling window selector — default 90 days
# avoids full table scan while still catching trends
# ----------------------------------------------------

lookback_days = st.sidebar.slider("Lookback Window (Days)", 7, 365, 90)
cutoff = pd.Timestamp.now() - pd.Timedelta(days=lookback_days)

@st.cache_data(ttl=600)
def load_rxbrain_data(cutoff_str):
    """Load only recent data — never a full table scan."""
    with engine.connect() as conn:
        df_e = pd.read_sql(
            "SELECT * FROM events WHERE dt >= :cutoff",
            conn,
            params={"cutoff": cutoff_str}
        )
        df_p = pd.read_sql(
            "SELECT * FROM pharmacy_orders WHERE dt >= :cutoff",
            conn,
            params={"cutoff": cutoff_str}
        )
        df_s = pd.read_sql(
            "SELECT * FROM staff_schedule WHERE dt >= :cutoff",
            conn,
            params={"cutoff": cutoff_str}
        )
    return df_e, df_p, df_s

try:
    # Pass cutoff as string so Streamlit can hash it for caching
    df_events_all, df_pharm_all, df_sched_all = load_rxbrain_data(str(cutoff))

    if not df_events_all.empty:
        df_events_all["user_name"] = (
            df_events_all["user_name"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
        )
        df_events_all["dt"] = pd.to_datetime(df_events_all["dt"], errors="coerce")

        st.success(f"Loaded {len(df_events_all):,} events from the last {lookback_days} days.")

        # ----------------------------------------------------
        # Stockout Risk — based on last 24 hours of activity
        # ----------------------------------------------------
        st.subheader("🚨 Imminent Stockout Risk (< 12 hrs)")

        last_time = df_events_all["dt"].max()
        recent_24h = df_events_all[
            df_events_all["dt"] > (last_time - pd.Timedelta(hours=24))
        ]

        if recent_24h.empty:
            st.info("No activity in the last 24 hours to calculate burn rate.")
        else:
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

            critical = burn[burn["hrs_left"] < 12].sort_values("hrs_left")

            if not critical.empty:
                st.error(f"🚩 {len(critical)} imminent stockout risks found.")
                st.dataframe(
                    critical,
                    use_container_width=True,
                    column_config={
                        "hrs_left": st.column_config.NumberColumn("Hours Left", format="%.1f"),
                        "pulled": st.column_config.NumberColumn("Units Pulled (24h)", format="%.0f"),
                        "current_inv": st.column_config.NumberColumn("Current Inventory", format="%.0f"),
                    }
                )
            else:
                st.success("✅ No immediate stockout risks detected.")

        # ----------------------------------------------------
        # High Burn Rate Trend — top medications by volume
        # ----------------------------------------------------
        st.divider()
        st.subheader(f"📈 Top Medications by Pull Volume ({lookback_days}-day window)")

        top_meds = (
            df_events_all.groupby("med_desc")["qty"]
            .sum()
            .reset_index()
            .sort_values("qty", ascending=False)
            .head(15)
        )

        import plotly.express as px
        fig = px.bar(
            top_meds,
            x="qty",
            y="med_desc",
            orientation="h",
            color="qty",
            color_continuous_scale="Reds"
        )
        fig.update_layout(
            yaxis={"categoryorder": "total ascending", "title": ""},
            xaxis_title="Total Units Pulled",
            showlegend=False
        )
        st.plotly_chart(fig, use_container_width=True)

    else:
        st.warning("No event data found for the selected window.")

except Exception as e:
    st.error(f"Brain Scan failed: {e}")
