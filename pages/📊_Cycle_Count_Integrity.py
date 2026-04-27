import io
from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


st.set_page_config(page_title="Cycle Count Integrity", page_icon="??", layout="wide")

load_data = App.load_data
engine = App.engine
render_sidebar = App.render_sidebar

start_date, end_date = render_sidebar()
COMPLIANCE_DAYS = 84

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Cycle Count Integrity Dashboard",
        f"Use the latest cycle count status snapshot plus return activity to prioritize overdue inventory across a {COMPLIANCE_DAYS}-day window.",
        kicker="Tools",
    )
    _debug_event("Cycle Count Integrity", "shared_intro_loaded")
    _debug_panel("Cycle Count Integrity", intro_mode="shared")
else:
    st.header("Cycle Count Integrity Dashboard")
    st.caption(f"Use cycle count status plus return activity to prioritize overdue inventory across a {COMPLIANCE_DAYS}-day window.")
    _debug_event("Cycle Count Integrity", "fallback_header_used")
    _debug_panel("Cycle Count Integrity", intro_mode="fallback")


@st.cache_data(ttl=3600)
def load_master_mapping():
    try:
        with engine.connect() as conn:
            df = pd.read_sql(
                text("SELECT med_id, med_desc, carousel_location FROM carousel_master_mapping"),
                conn,
            )
        if df.empty:
            return df
        df["med_id"] = df["med_id"].astype(str).str.strip().str.upper()
        df["is_controlled"] = df["carousel_location"].astype(str).str.startswith("CW")
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_all_pharm():
    try:
        with engine.connect() as conn:
            df = pd.read_sql(
                text("SELECT priority, dt, med_id, med_desc, user_name, qty FROM pharmacy_orders"),
                conn,
            )
        if df.empty:
            return df
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["med_id"] = df["med_id"].astype(str).str.strip().str.upper()
        df["med_desc"] = df["med_desc"].fillna("").astype(str).str.strip()
        df["user_name"] = df["user_name"].fillna("").astype(str).str.strip()
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_open_variances():
    try:
        sql = text(
            """
            WITH removals AS (
                SELECT med_id, dt::date AS tx_date, SUM(qty) AS qty_pyxis
                FROM events
                WHERE (
                    event_type ILIKE '%unload%'
                    OR event_type ILIKE '%empty return bin%'
                    OR event_type ILIKE '%destock%'
                )
                  AND event_type NOT ILIKE '%cancel%'
                  AND event_type NOT ILIKE '%eject%'
                GROUP BY med_id, dt::date
            ),
            returns AS (
                SELECT med_id, dt::date AS tx_date, SUM(qty) AS qty_pharm
                FROM pharmacy_orders
                WHERE priority IN ('Return', 'Returns', 'Instant Return', 'Instant Restock')
                GROUP BY med_id, dt::date
            ),
            reconciled AS (
                SELECT
                    COALESCE(rm.med_id, rt.med_id) AS med_id,
                    COALESCE(rm.qty_pyxis, 0) AS qty_pyxis,
                    COALESCE(rt.qty_pharm, 0) AS qty_pharm
                FROM removals rm
                FULL OUTER JOIN returns rt
                  ON rm.med_id = rt.med_id
                 AND rm.tx_date = rt.tx_date
            )
            SELECT DISTINCT med_id FROM reconciled WHERE qty_pyxis <> qty_pharm
            """
        )
        with engine.connect() as conn:
            result = conn.execute(sql)
            return {str(row[0]).strip().upper() for row in result if row[0]}
    except Exception:
        return set()


@st.cache_data(ttl=300)
def load_latest_cycle_count_status():
    try:
        sql = text(
            """
            WITH latest AS (
                SELECT MAX(snapshot_date) AS snapshot_date
                FROM cycle_count_status
            )
            SELECT
                c.snapshot_date,
                c.source_filename,
                c.isa_name,
                c.med_id,
                c.med_desc,
                c.location,
                c.cycle_count_interval,
                c.last_cycle_count,
                c.days_since_last_count,
                c.days_over_due
            FROM cycle_count_status c
            JOIN latest l
              ON c.snapshot_date = l.snapshot_date
            ORDER BY c.location, c.med_id
            """
        )
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn)
        if df.empty:
            return df
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], errors="coerce").dt.date
        df["last_cycle_count"] = pd.to_datetime(df["last_cycle_count"], errors="coerce")
        df["cycle_count_interval"] = pd.to_numeric(df["cycle_count_interval"], errors="coerce").fillna(0)
        df["days_since_last_count"] = pd.to_numeric(df["days_since_last_count"], errors="coerce").fillna(0)
        df["days_over_due"] = pd.to_numeric(df["days_over_due"], errors="coerce").fillna(0)
        df["med_id"] = df["med_id"].astype(str).str.strip().str.upper()
        df["med_desc"] = df["med_desc"].fillna("").astype(str).str.strip()
        df["location"] = df["location"].fillna("").astype(str).str.strip().str.upper()
        df["isa_name"] = df["isa_name"].fillna("").astype(str).str.strip()
        df["cycle_date"] = df["last_cycle_count"].dt.date
        return df
    except Exception:
        return pd.DataFrame()


with st.spinner("Loading cycle count data..."):
    df_all_pharm = load_all_pharm()
    df_master = load_master_mapping()
    open_variance_ids = load_open_variances()
    df_cycle_status = load_latest_cycle_count_status()

if df_all_pharm.empty and df_cycle_status.empty:
    st.warning("No pharmacy workflow data or cycle count status snapshot is loaded yet.")
    st.stop()

cycle_counts = pd.DataFrame()
latest_cycle_history = pd.DataFrame(columns=["med_id", "cycle_date", "cycle_count_user"])
ever_counted_ids = set()
if not df_all_pharm.empty:
    cycle_counts = df_all_pharm[df_all_pharm["priority"].astype(str).str.strip() == "Cycle Count"].copy()
    if not cycle_counts.empty:
        cycle_counts["cycle_date"] = cycle_counts["dt"].dt.date
        latest_cycle_history = (
            cycle_counts.sort_values("dt")
            .groupby("med_id")
            .last()
            .reset_index()[["med_id", "cycle_date", "user_name"]]
            .rename(columns={"user_name": "cycle_count_user"})
        )
        ever_counted_ids = set(cycle_counts["med_id"].astype(str))

returns = pd.DataFrame(columns=["med_id", "med_desc", "return_date", "qty", "user_name"])
if not df_all_pharm.empty:
    returns = df_all_pharm[
        df_all_pharm["priority"].astype(str).str.strip().isin(["Return", "Returns", "Instant Return", "Instant Restock"])
    ].copy()
    if not returns.empty:
        returns["return_date"] = returns["dt"].dt.date
        returns = returns[
            (returns["return_date"] >= start_date) & (returns["return_date"] <= end_date)
        ].copy()

use_cycle_status = not df_cycle_status.empty
status_snapshot_date = df_cycle_status["snapshot_date"].dropna().max() if use_cycle_status else None

if use_cycle_status:
    med_status = (
        df_cycle_status.groupby("med_id", as_index=False)
        .agg(
            med_desc_snapshot=("med_desc", "first"),
            cycle_date=("cycle_date", "min"),
            days_since_cycle=("days_since_last_count", "max"),
            days_over_due=("days_over_due", "max"),
            cycle_count_interval=("cycle_count_interval", "max"),
            location_count=("location", "nunique"),
            any_uncounted_location=("cycle_date", lambda s: s.isna().any()),
        )
    )
    ever_counted_ids = ever_counted_ids.union(
        set(med_status.loc[med_status["cycle_date"].notna(), "med_id"].astype(str))
    )
else:
    med_status = latest_cycle_history.copy()
    med_status["days_since_cycle"] = (
        pd.to_datetime(date.today()) - pd.to_datetime(med_status["cycle_date"])
    ).dt.days
    med_status["days_over_due"] = np.where(
        med_status["days_since_cycle"].fillna(0) > COMPLIANCE_DAYS,
        med_status["days_since_cycle"].fillna(0) - COMPLIANCE_DAYS,
        0,
    )
    med_status["cycle_count_interval"] = COMPLIANCE_DAYS
    med_status["location_count"] = np.nan
    med_status["any_uncounted_location"] = False
    med_status["med_desc_snapshot"] = None

if not df_master.empty:
    master_rollup = (
        df_master.groupby("med_id", as_index=False)
        .agg(
            carousel_location=("carousel_location", "first"),
            is_controlled=("is_controlled", "max"),
        )
    )
else:
    master_rollup = pd.DataFrame(columns=["med_id", "carousel_location", "is_controlled"])

status_summary = med_status.merge(master_rollup, on="med_id", how="left")
status_summary["is_controlled"] = status_summary["is_controlled"].fillna(False)
status_summary["never_cycle_counted"] = ~status_summary["med_id"].isin(ever_counted_ids)
status_summary["compliance_status"] = np.where(
    status_summary["never_cycle_counted"],
    "Never Counted",
    np.where(status_summary["days_over_due"].fillna(0) > 0, "Overdue", "Compliant"),
)
status_summary["cycle_source"] = np.where(
    use_cycle_status,
    "Cycle Count Status Report",
    "Pharmacy Cycle Count Transactions",
)

tracker = pd.DataFrame()
risk_df = pd.DataFrame()
if not returns.empty:
    tracker = (
        returns.merge(
            status_summary[[
                "med_id", "cycle_date", "days_since_cycle", "days_over_due", "cycle_count_interval",
                "carousel_location", "is_controlled", "never_cycle_counted", "compliance_status"
            ]],
            on="med_id",
            how="left",
        )
        .merge(latest_cycle_history[["med_id", "cycle_count_user"]], on="med_id", how="left")
    )
    tracker["is_controlled"] = tracker["is_controlled"].fillna(False)
    tracker["never_cycle_counted"] = tracker["never_cycle_counted"].fillna(True)
    tracker["compliance_status"] = tracker["compliance_status"].fillna("Never Counted")
    tracker["open_return_variance"] = tracker["med_id"].isin(open_variance_ids)

    return_summary = (
        tracker.groupby("med_id", as_index=False)
        .agg(
            med_desc=("med_desc", "first"),
            return_frequency=("med_id", "count"),
            total_qty_returned=("qty", "sum"),
            cycle_date=("cycle_date", "first"),
            cycle_count_user=("cycle_count_user", "first"),
            days_since_cycle=("days_since_cycle", "max"),
            days_over_due=("days_over_due", "max"),
            cycle_count_interval=("cycle_count_interval", "max"),
            carousel_location=("carousel_location", "first"),
            is_controlled=("is_controlled", "max"),
            never_cycle_counted=("never_cycle_counted", "max"),
            compliance_status=("compliance_status", "first"),
            open_return_variance=("open_return_variance", "max"),
        )
    )
    risk_df = return_summary.copy()
    risk_df["effective_days"] = np.where(
        risk_df["never_cycle_counted"],
        999,
        risk_df["days_since_cycle"].fillna(0),
    )
    risk_df["controlled_multiplier"] = np.where(risk_df["is_controlled"], 2.0, 1.0)
    risk_df["risk_score"] = (
        risk_df["effective_days"] *
        risk_df["return_frequency"].fillna(0) *
        risk_df["controlled_multiplier"]
    )

    def risk_tier(score):
        if score >= 5000:
            return "Critical"
        if score >= 1000:
            return "High"
        if score >= 200:
            return "Medium"
        return "Low"

    risk_df["risk_tier"] = risk_df["risk_score"].apply(risk_tier)
    risk_df = risk_df.sort_values(["risk_score", "days_over_due", "total_qty_returned"], ascending=False).reset_index(drop=True)

tech_stats = pd.DataFrame()
if not cycle_counts.empty:
    tech_cycle_stats = (
        cycle_counts.groupby("user_name")
        .agg(
            total_counts=("med_id", "count"),
            unique_meds_counted=("med_id", "nunique"),
            last_count_date=("cycle_date", "max"),
            first_count_date=("cycle_date", "min"),
        )
        .reset_index()
        .rename(columns={"user_name": "technician"})
    )
    tech_overdue = latest_cycle_history[latest_cycle_history["cycle_count_user"].notna()].copy()
    tech_overdue["days_since"] = (
        pd.to_datetime(date.today()) - pd.to_datetime(tech_overdue["cycle_date"])
    ).dt.days
    tech_overdue_counts = (
        tech_overdue[tech_overdue["days_since"] > COMPLIANCE_DAYS]
        .groupby("cycle_count_user")
        .size()
        .reset_index(name="overdue_locations")
        .rename(columns={"cycle_count_user": "technician"})
    )
    tech_stats = tech_cycle_stats.merge(tech_overdue_counts, on="technician", how="left")
    tech_stats["overdue_locations"] = tech_stats["overdue_locations"].fillna(0).astype(int)
    tech_stats["avg_days_between_counts"] = (
        (pd.to_datetime(tech_stats["last_count_date"]) - pd.to_datetime(tech_stats["first_count_date"])).dt.days /
        tech_stats["total_counts"].clip(lower=1)
    ).round(1)
    tech_stats = tech_stats.sort_values(["overdue_locations", "total_counts"], ascending=[False, False])

if use_cycle_status:
    carousel_df = df_cycle_status.copy()
    carousel_df["carousel_location"] = carousel_df["location"]
    carousel_df["days_since_count"] = carousel_df["days_since_last_count"].fillna(999)
else:
    carousel_df = latest_cycle_history.merge(master_rollup[["med_id", "carousel_location"]], on="med_id", how="left") if not master_rollup.empty else latest_cycle_history.copy()
    carousel_df["days_since_count"] = (
        pd.to_datetime(date.today()) - pd.to_datetime(carousel_df["cycle_date"])
    ).dt.days.fillna(999)

if "carousel_location" in carousel_df.columns:
    carousel_df = carousel_df[carousel_df["carousel_location"].notna()].copy()
    carousel_df["carousel_unit"] = carousel_df["carousel_location"].astype(str).str.extract(r"^(CAR\d+)")
    carousel_df["shelf"] = carousel_df["carousel_location"].astype(str).str.extract(r"^CAR\d+-(\d+)")
    carousel_df = carousel_df[carousel_df["carousel_unit"].notna()].copy()
    heatmap_data = (
        carousel_df.groupby(["carousel_unit", "shelf"], as_index=False)
        .agg(avg_days=("days_since_count", "mean"), med_count=("med_id", "count"))
    )
    heatmap_data["avg_days"] = heatmap_data["avg_days"].round(0)
else:
    heatmap_data = pd.DataFrame()

status_basis = status_summary.copy()
avg_days = status_basis["days_since_cycle"].dropna().mean()
max_days = status_basis["days_since_cycle"].dropna().max()
overdue_ct = int((status_basis["days_over_due"].fillna(0) > 0).sum()) if not status_basis.empty else 0
never_count = int(status_basis["never_cycle_counted"].sum()) if not status_basis.empty else 0
compliant = int((status_basis["compliance_status"] == "Compliant").sum()) if not status_basis.empty else 0
total_meds = len(status_basis)
comply_pct = (compliant / total_meds * 100) if total_meds else 0
open_var_ct = int(risk_df["open_return_variance"].sum()) if not risk_df.empty else 0

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Avg Days Since Count", f"{avg_days:.1f}" if pd.notna(avg_days) else "N/A")
m2.metric("Max Days Since Count", int(max_days) if pd.notna(max_days) else 0)
m3.metric(f"Overdue (>{COMPLIANCE_DAYS}d)", overdue_ct)
m4.metric("Never Counted", never_count)
m5.metric("Compliance Rate", f"{comply_pct:.1f}%")
m6.metric("Open Return Variances", open_var_ct)

if use_cycle_status and pd.notna(status_snapshot_date):
    st.caption(
        f"Using the latest Days Since Last Cycle Count Report snapshot dated {pd.to_datetime(status_snapshot_date).strftime('%b %d, %Y')} for current freshness. Tech accountability still comes from pharmacy cycle count transactions."
    )
else:
    st.caption("No cycle count status snapshot is loaded yet, so freshness is estimated from pharmacy cycle count transactions.")

st.divider()

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Risk Worklist",
    "Never Counted",
    "Tech Accountability",
    "Carousel Coverage",
    "Return Activity",
])

with tab1:
    st.subheader("Prioritized Cycle Count Worklist")
    st.caption("Return activity shows how many hands have been in it. The latest cycle count status snapshot tells us whether the med is actually overdue right now.")
    if risk_df.empty:
        st.info("No return activity found in the selected date range, so there is no return-weighted worklist to rank.")
    else:
        col1, col2, col3 = st.columns(3)
        tier_filter = col1.multiselect("Risk Tier", ["Critical", "High", "Medium", "Low"], default=["Critical", "High"])
        controlled_only = col2.checkbox("Controlled Only")
        overdue_only = col3.checkbox("Overdue Only")

        filtered = risk_df.copy()
        if tier_filter:
            filtered = filtered[filtered["risk_tier"].isin(tier_filter)]
        if controlled_only:
            filtered = filtered[filtered["is_controlled"] == True]
        if overdue_only:
            filtered = filtered[filtered["days_over_due"].fillna(0) > 0]

        top20 = filtered.head(20)
        if not top20.empty:
            fig = px.bar(
                top20.sort_values("risk_score"),
                x="risk_score",
                y="med_desc",
                orientation="h",
                color="risk_tier",
                color_discrete_map={
                    "Critical": "#ef4444",
                    "High": "#f97316",
                    "Medium": "#eab308",
                    "Low": "#22c55e",
                },
                labels={"risk_score": "Risk Score", "med_desc": ""},
                title="Top 20 Highest Risk Medications",
            )
            fig.update_layout(height=500)
            st.plotly_chart(fig, width="stretch")

        display_cols = [
            "risk_tier", "compliance_status", "open_return_variance", "risk_score", "med_id", "med_desc",
            "carousel_location", "is_controlled", "days_since_cycle", "days_over_due", "return_frequency",
            "total_qty_returned", "cycle_date", "cycle_count_user",
        ]
        st.dataframe(
            filtered[[c for c in display_cols if c in filtered.columns]],
            width="stretch",
            hide_index=True,
            column_config={
                "open_return_variance": st.column_config.CheckboxColumn("Open Variance"),
                "is_controlled": st.column_config.CheckboxColumn("Controlled"),
                "risk_score": st.column_config.NumberColumn("Risk Score", format="%.0f"),
                "days_since_cycle": st.column_config.NumberColumn("Days Since Count", format="%.0f"),
                "days_over_due": st.column_config.NumberColumn("Days Overdue", format="%.0f"),
                "return_frequency": st.column_config.NumberColumn("Return Events", format="%.0f"),
                "total_qty_returned": st.column_config.NumberColumn("Total Qty", format="%.0f"),
                "cycle_date": st.column_config.DateColumn("Last Count"),
            },
        )
        st.download_button(
            "Export Worklist to Excel",
            data=to_excel_bytes(filtered[[c for c in display_cols if c in filtered.columns]]),
            file_name="cycle_count_worklist.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

with tab2:
    st.subheader("Medications Never Cycle Counted")
    st.caption("These meds have return activity in the selected period but no known cycle count history in the current database or status snapshot.")
    if risk_df.empty:
        st.info("No return activity found in the selected date range.")
    else:
        never_df = risk_df[risk_df["never_cycle_counted"]].copy()
        if never_df.empty:
            st.success("All returned medications have at least one recorded cycle count.")
        else:
            c1, c2 = st.columns(2)
            c1.metric("Distinct Meds Never Counted", len(never_df))
            c2.metric("Of Which Are Controlled", int(never_df["is_controlled"].sum()))
            st.dataframe(
                never_df[[c for c in [
                    "med_id", "med_desc", "carousel_location", "is_controlled",
                    "return_frequency", "total_qty_returned", "risk_score", "risk_tier"
                ] if c in never_df.columns]].sort_values("risk_score", ascending=False),
                width="stretch",
                hide_index=True,
                column_config={
                    "is_controlled": st.column_config.CheckboxColumn("Controlled"),
                    "return_frequency": st.column_config.NumberColumn("Return Events", format="%.0f"),
                    "total_qty_returned": st.column_config.NumberColumn("Total Qty", format="%.0f"),
                    "risk_score": st.column_config.NumberColumn("Risk Score", format="%.0f"),
                },
            )

with tab3:
    st.subheader("Technician Cycle Count Accountability")
    st.caption(f"This section stays based on actual pharmacy `Cycle Count` transactions. Overdue means the med this tech last counted is now older than {COMPLIANCE_DAYS} days.")
    if cycle_counts.empty:
        st.info("No pharmacy `Cycle Count` transactions are loaded yet, so technician accountability cannot be calculated.")
    else:
        fig_tech = px.bar(
            tech_stats.head(15),
            x="technician",
            y="overdue_locations",
            color="overdue_locations",
            color_continuous_scale="Reds",
            labels={"technician": "", "overdue_locations": "Overdue Locations"},
            title="Overdue Locations by Last Counting Technician",
        )
        fig_tech.update_layout(coloraxis_showscale=False)
        st.plotly_chart(fig_tech, width="stretch")
        st.dataframe(
            tech_stats,
            width="stretch",
            hide_index=True,
            column_config={
                "total_counts": st.column_config.NumberColumn("Total Counts", format="%.0f"),
                "unique_meds_counted": st.column_config.NumberColumn("Unique Meds", format="%.0f"),
                "overdue_locations": st.column_config.NumberColumn("Overdue Locations", format="%.0f"),
                "last_count_date": st.column_config.DateColumn("Last Count Date"),
                "first_count_date": st.column_config.DateColumn("First Count Date"),
                "avg_days_between_counts": st.column_config.NumberColumn("Avg Days/Count", format="%.1f"),
            },
        )

with tab4:
    st.subheader("Carousel Coverage Heatmap")
    if heatmap_data.empty:
        st.info("No carousel location data is available to build the heatmap.")
    else:
        carousel_units = sorted(heatmap_data["carousel_unit"].dropna().unique())
        selected_carousels = st.multiselect("Filter Carousel Units", carousel_units, default=carousel_units)
        hm_filtered = heatmap_data[heatmap_data["carousel_unit"].isin(selected_carousels)]
        if not hm_filtered.empty:
            pivot = hm_filtered.pivot_table(index="shelf", columns="carousel_unit", values="avg_days", aggfunc="mean").round(0)
            fig_hm = px.imshow(
                pivot,
                color_continuous_scale="RdYlGn_r",
                labels={"color": "Avg Days"},
                aspect="auto",
                title="Average Days Since Cycle Count by Carousel and Shelf",
            )
            fig_hm.update_layout(height=600)
            st.plotly_chart(fig_hm, width="stretch")
            carousel_summary = (
                hm_filtered.groupby("carousel_unit", as_index=False)
                .agg(avg_days=("avg_days", "mean"), max_days=("avg_days", "max"), total_meds=("med_count", "sum"))
                .round(1)
                .sort_values("avg_days", ascending=False)
            )
            st.dataframe(
                carousel_summary,
                width="stretch",
                hide_index=True,
                column_config={
                    "avg_days": st.column_config.NumberColumn("Avg Days Since Count", format="%.1f"),
                    "max_days": st.column_config.NumberColumn("Max Days", format="%.0f"),
                    "total_meds": st.column_config.NumberColumn("Meds Tracked", format="%.0f"),
                },
            )

with tab5:
    st.subheader("Return Activity Detail")
    if tracker.empty:
        st.info("No return activity found in the selected date range.")
    else:
        detail_cols = [
            "med_id", "med_desc", "carousel_location", "return_date", "qty", "user_name",
            "cycle_date", "cycle_count_user", "days_since_cycle", "days_over_due", "compliance_status"
        ]
        st.dataframe(
            tracker[[c for c in detail_cols if c in tracker.columns]].sort_values(
                ["days_over_due", "days_since_cycle", "return_date"],
                ascending=[False, False, False],
                na_position="last",
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "return_date": st.column_config.DateColumn("Return Date"),
                "cycle_date": st.column_config.DateColumn("Last Cycle Count"),
                "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
                "days_since_cycle": st.column_config.NumberColumn("Days Since Count", format="%.0f"),
                "days_over_due": st.column_config.NumberColumn("Days Overdue", format="%.0f"),
            },
        )


