import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
from sqlalchemy import text
from App import load_data, engine, render_sidebar

st.set_page_config(
    page_title="Cycle Count Integrity",
    page_icon="📊",
    layout="wide"
)

start_date, end_date = render_sidebar()

COMPLIANCE_DAYS = 84  # Every med must be cycle counted within this window

st.header("📊 Cycle Count Integrity Dashboard")
st.caption(f"Risk-scored cycle count compliance · {COMPLIANCE_DAYS}-day compliance window · with technician accountability and carousel coverage.")

# ─────────────────────────────────────────────────────
# DATA LOADERS
# ─────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_master_mapping():
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT med_id, med_desc, carousel_location FROM carousel_master_mapping"))
            df = pd.DataFrame(result.fetchall(), columns=result.keys())
        df["med_id"] = df["med_id"].astype(str).str.strip().str.upper()
        df["is_controlled"] = df["carousel_location"].astype(str).str.startswith("CW")
        return df
    except Exception as e:
        st.warning(f"⚠️ Could not load carousel mapping: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=300)
def load_all_pharm():
    try:
        with engine.connect() as conn:
            df = pd.read_sql(
                text("SELECT priority, dt, med_id, med_desc, user_name, qty FROM pharmacy_orders"),
                conn
            )
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["med_id"] = df["med_id"].astype(str).str.strip().str.upper()
        return df
    except Exception as e:
        st.warning(f"⚠️ Could not load pharmacy data: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=300)
def load_open_variances():
    """Med_ids that have unmatched qty between Pyxis unloads and pharmacy returns."""
    try:
        sql = text("""
            WITH unloads AS (
                SELECT med_id, dt::date AS tx_date, SUM(qty) AS qty_pyxis
                FROM events
                WHERE (event_type ILIKE '%unload%' OR event_type ILIKE '%empty%')
                  AND event_type NOT ILIKE '%cancel%'
                  AND event_type NOT ILIKE '%eject%'
                GROUP BY med_id, dt::date
            ),
            returns AS (
                SELECT med_id, dt::date AS tx_date, SUM(qty) AS qty_pharm
                FROM pharmacy_orders
                WHERE priority = 'Returns'
                GROUP BY med_id, dt::date
            ),
            reconciled AS (
                SELECT
                    COALESCE(u.med_id, r.med_id) AS med_id,
                    COALESCE(u.qty_pyxis, 0)     AS qty_pyxis,
                    COALESCE(r.qty_pharm, 0)      AS qty_pharm
                FROM unloads u
                FULL OUTER JOIN returns r ON u.med_id = r.med_id AND u.tx_date = r.tx_date
            )
            SELECT DISTINCT med_id FROM reconciled WHERE qty_pyxis != qty_pharm
        """)
        with engine.connect() as conn:
            result = conn.execute(sql)
            ids = set(row[0].strip().upper() for row in result if row[0])
        return ids
    except Exception as e:
        st.warning(f"⚠️ Could not load return variance data: {e}")
        return set()

df_all_pharm      = load_all_pharm()
df_master         = load_master_mapping()
open_variance_ids = load_open_variances()

if df_all_pharm.empty:
    st.warning("No pharmacy workflow data found.")
    st.stop()

# ─────────────────────────────────────────────────────
# CYCLE COUNT HISTORY (full all-time)
# ─────────────────────────────────────────────────────

cycle_counts = df_all_pharm[
    df_all_pharm["priority"].astype(str).str.strip() == "Cycle Count"
].copy()

if cycle_counts.empty:
    st.warning("No cycle count transactions found in database.")
    st.stop()

cycle_counts["cycle_date"] = cycle_counts["dt"].dt.date

# Latest cycle count per med — who did it and when
latest_cycle = (
    cycle_counts.sort_values("dt")
    .groupby("med_id").last()
    .reset_index()[["med_id", "cycle_date", "user_name"]]
    .rename(columns={"user_name": "cycle_count_user"})
)

ever_counted_ids = set(cycle_counts["med_id"].unique())

# ─────────────────────────────────────────────────────
# RETURNS IN DATE RANGE
# ─────────────────────────────────────────────────────

returns = df_all_pharm[
    df_all_pharm["priority"].astype(str).str.strip() == "Returns"
].copy()

returns["return_date"] = returns["dt"].dt.date
returns = returns[
    (returns["return_date"] >= start_date) &
    (returns["return_date"] <= end_date)
].copy()

if returns.empty:
    st.warning("No return activity found in selected date range.")
    st.stop()

# ─────────────────────────────────────────────────────
# BUILD TRACKER
# ─────────────────────────────────────────────────────

tracker = returns.merge(latest_cycle, on="med_id", how="left")

tracker["days_since_cycle"] = (
    pd.to_datetime(tracker["return_date"]) -
    pd.to_datetime(tracker["cycle_date"])
).dt.days

tracker["never_cycle_counted"] = ~tracker["med_id"].isin(ever_counted_ids)

if not df_master.empty:
    tracker = tracker.merge(
        df_master[["med_id", "carousel_location", "is_controlled"]],
        on="med_id", how="left"
    )
else:
    tracker["carousel_location"] = None
    tracker["is_controlled"] = False

tracker["is_controlled"] = tracker["is_controlled"].fillna(False)

# ─────────────────────────────────────────────────────
# RISK SCORING
# Per med_id: days_since_cycle * return_frequency * controlled_multiplier
# Never counted gets a heavy penalty (999 days equivalent)
# ─────────────────────────────────────────────────────

return_freq = (
    tracker.groupby("med_id")
    .size()
    .reset_index(name="return_frequency")
)

risk_df = (
    tracker.groupby(["med_id", "med_desc", "carousel_location", "is_controlled",
                     "cycle_date", "cycle_count_user", "never_cycle_counted"])
    .agg(
        days_since_cycle=("days_since_cycle", "max"),
        total_qty_returned=("qty", "sum"),
    )
    .reset_index()
    .merge(return_freq, on="med_id", how="left")
)

# Assign effective days — never counted gets 999
risk_df["effective_days"] = np.where(
    risk_df["never_cycle_counted"],
    999,
    risk_df["days_since_cycle"].fillna(0)
)

# Controlled substances get 2x weight
risk_df["controlled_multiplier"] = np.where(risk_df["is_controlled"], 2.0, 1.0)

# Risk score = effective_days * return_frequency * controlled_multiplier
risk_df["risk_score"] = (
    risk_df["effective_days"] *
    risk_df["return_frequency"] *
    risk_df["controlled_multiplier"]
)

# Risk tier
def risk_tier(score):
    if score >= 5000: return "🔴 Critical"
    if score >= 1000: return "🟠 High"
    if score >= 200:  return "🟡 Medium"
    return "🟢 Low"

risk_df["risk_tier"] = risk_df["risk_score"].apply(risk_tier)

# 84-day compliance status
risk_df["compliance_status"] = np.where(
    risk_df["never_cycle_counted"],
    "⛔ Never Counted",
    np.where(
        risk_df["effective_days"] > COMPLIANCE_DAYS,
        f"❌ Overdue (>{COMPLIANCE_DAYS}d)",
        "✅ Compliant"
    )
)

# Cross-reference with return reconciliation
risk_df["open_return_variance"] = risk_df["med_id"].isin(open_variance_ids)

risk_df = risk_df.sort_values("risk_score", ascending=False).reset_index(drop=True)

# ─────────────────────────────────────────────────────
# TECH ACCOUNTABILITY
# ─────────────────────────────────────────────────────

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

# How many of each tech's counted meds are now overdue (>30 days since their last count)
today = date.today()
tech_overdue = (
    latest_cycle[latest_cycle["cycle_count_user"].notna()]
    .copy()
)
tech_overdue["days_since"] = (
    pd.to_datetime(today) - pd.to_datetime(tech_overdue["cycle_date"])
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
    (pd.to_datetime(tech_stats["last_count_date"]) -
     pd.to_datetime(tech_stats["first_count_date"])).dt.days /
    tech_stats["total_counts"].clip(lower=1)
).round(1)
tech_stats = tech_stats.sort_values("overdue_locations", ascending=False)

# ─────────────────────────────────────────────────────
# CAROUSEL COVERAGE HEATMAP
# Extract carousel number and shelf from location
# Format: CAR1-02-05-01 → CAR1, shelf 02
# ─────────────────────────────────────────────────────

carousel_df = latest_cycle.merge(
    df_master[["med_id", "carousel_location"]], on="med_id", how="left"
) if not df_master.empty else latest_cycle.copy()

if "carousel_location" in carousel_df.columns:
    carousel_df = carousel_df[carousel_df["carousel_location"].notna()].copy()
    # Extract carousel unit (CAR1, CAR2, CAR3, etc.)
    carousel_df["carousel_unit"] = carousel_df["carousel_location"].astype(str).str.extract(r'^(CAR\d+)')
    # Extract shelf number
    carousel_df["shelf"] = carousel_df["carousel_location"].astype(str).str.extract(r'^CAR\d+-(\d+)')
    carousel_df = carousel_df[carousel_df["carousel_unit"].notna()].copy()

    carousel_df["days_since_count"] = (
        pd.to_datetime(today) - pd.to_datetime(carousel_df["cycle_date"])
    ).dt.days.fillna(999)

    heatmap_data = (
        carousel_df.groupby(["carousel_unit", "shelf"])
        .agg(avg_days=("days_since_count", "mean"), med_count=("med_id", "count"))
        .reset_index()
    )
    heatmap_data["avg_days"] = heatmap_data["avg_days"].round(0)
else:
    heatmap_data = pd.DataFrame()

# ─────────────────────────────────────────────────────
# EXECUTIVE METRICS
# ─────────────────────────────────────────────────────

avg_days     = tracker["days_since_cycle"].mean()
max_days     = tracker["days_since_cycle"].max()
never_count  = risk_df["never_cycle_counted"].sum()
overdue      = len(risk_df[risk_df["compliance_status"].str.startswith("❌")])
compliant    = len(risk_df[risk_df["compliance_status"] == "✅ Compliant"])
total_meds   = len(risk_df)
comply_pct   = (compliant / total_meds * 100) if total_meds > 0 else 0
open_var_ct  = risk_df["open_return_variance"].sum()

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Avg Days Since Count",       f"{avg_days:.1f}" if pd.notna(avg_days) else "N/A")
m2.metric("Max Days Since Count",       int(max_days) if pd.notna(max_days) else 0)
m3.metric(f"Overdue (>{COMPLIANCE_DAYS}d)",  int(overdue))
m4.metric("Never Counted",              int(never_count))
m5.metric(f"Compliance Rate",           f"{comply_pct:.1f}%")
m6.metric("⚠️ Open Return Variances",   int(open_var_ct))

st.divider()

# ─────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🎯 Risk Worklist",
    "🚨 Never Counted",
    "👩‍⚕️ Tech Accountability",
    "🗺️ Carousel Coverage",
    "🔍 Return Activity"
])

# ── TAB 1: RISK WORKLIST ──────────────────────────────
with tab1:
    st.subheader("Prioritized Cycle Count Worklist")
    st.caption(
        "Risk Score = Days Since Last Count × Return Frequency × Controlled Multiplier (2×). "
        "Never-counted meds assigned 999 days. Sort by Risk Score to know exactly where to count first."
    )

    # Filter controls
    col1, col2, col3 = st.columns(3)
    tier_filter = col1.multiselect(
        "Risk Tier", ["🔴 Critical", "🟠 High", "🟡 Medium", "🟢 Low"],
        default=["🔴 Critical", "🟠 High"]
    )
    controlled_only = col2.checkbox("Controlled Only")
    never_only = col3.checkbox("Never Counted Only")

    filtered = risk_df.copy()
    if tier_filter:
        filtered = filtered[filtered["risk_tier"].isin(tier_filter)]
    if controlled_only:
        filtered = filtered[filtered["is_controlled"] == True]
    if never_only:
        filtered = filtered[filtered["never_cycle_counted"] == True]

    # Bar chart — top 20
    top20 = filtered.head(20)
    if not top20.empty:
        fig = px.bar(
            top20,
            x="risk_score",
            y="med_desc",
            orientation="h",
            color="risk_tier",
            color_discrete_map={
                "🔴 Critical": "#ef4444",
                "🟠 High":     "#f97316",
                "🟡 Medium":   "#eab308",
                "🟢 Low":      "#22c55e"
            },
            title="Top 20 Highest Risk Medications",
            labels={"risk_score": "Risk Score", "med_desc": ""}
        )
        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            showlegend=True,
            height=500
        )
        st.plotly_chart(fig, use_container_width=True)

    display_cols = [c for c in [
        "risk_tier", "compliance_status", "open_return_variance",
        "risk_score", "med_id", "med_desc", "carousel_location",
        "is_controlled", "days_since_cycle", "return_frequency",
        "total_qty_returned", "cycle_date", "cycle_count_user"
    ] if c in filtered.columns]

    st.dataframe(
        filtered[display_cols],
        use_container_width=True,
        column_config={
            "risk_score":           st.column_config.NumberColumn("Risk Score", format="%.0f"),
            "compliance_status":    st.column_config.TextColumn("84-Day Status"),
            "open_return_variance": st.column_config.CheckboxColumn("⚠️ Open Variance"),
            "days_since_cycle":     st.column_config.NumberColumn("Days Since Count", format="%d"),
            "return_frequency":     st.column_config.NumberColumn("Return Events", format="%d"),
            "total_qty_returned":   st.column_config.NumberColumn("Total Qty", format="%.0f"),
            "cycle_date":           st.column_config.DateColumn("Last Count"),
            "is_controlled":        st.column_config.CheckboxColumn("Controlled"),
        },
        hide_index=True
    )

# ── TAB 2: NEVER COUNTED ─────────────────────────────
with tab2:
    st.subheader("Medications Never Cycle Counted")
    st.caption("These meds have been returned during the selected period but have NO cycle count record anywhere in the database.")

    never_df = risk_df[risk_df["never_cycle_counted"]].copy()

    if never_df.empty:
        st.success("✅ All returned medications have at least one recorded cycle count.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Distinct Meds Never Counted", len(never_df))
        c2.metric("Of Which Are Controlled", int(never_df["is_controlled"].sum()))

        st.dataframe(
            never_df[[c for c in [
                "med_id", "med_desc", "carousel_location", "is_controlled",
                "return_frequency", "total_qty_returned", "risk_score", "risk_tier"
            ] if c in never_df.columns]].sort_values("risk_score", ascending=False),
            use_container_width=True,
            column_config={
                "risk_score":         st.column_config.NumberColumn("Risk Score", format="%.0f"),
                "return_frequency":   st.column_config.NumberColumn("Return Events", format="%d"),
                "total_qty_returned": st.column_config.NumberColumn("Total Qty", format="%.0f"),
                "is_controlled":      st.column_config.CheckboxColumn("Controlled"),
            },
            hide_index=True
        )

        # By location
        st.divider()
        st.subheader("📍 By Carousel Location")
        loc_summary = (
            never_df.groupby("carousel_location", dropna=False)
            .agg(meds=("med_id", "count"), total_qty=("total_qty_returned", "sum"))
            .reset_index()
            .sort_values("meds", ascending=False)
        )
        st.dataframe(loc_summary, use_container_width=True, hide_index=True)

# ── TAB 3: TECH ACCOUNTABILITY ────────────────────────
with tab3:
    st.subheader("Technician Cycle Count Accountability")
    st.caption(f"Based on all-time cycle count history. 'Overdue' = locations last counted by this tech that are now >{COMPLIANCE_DAYS} days old.")

    if tech_stats.empty:
        st.info("No technician data available.")
    else:
        # Bar: overdue locations per tech
        fig_tech = px.bar(
            tech_stats.head(15),
            x="technician",
            y="overdue_locations",
            color="overdue_locations",
            color_continuous_scale="Reds",
            title="Overdue Locations by Last Counting Technician",
            labels={"overdue_locations": "Overdue Locations", "technician": ""}
        )
        fig_tech.update_layout(showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_tech, use_container_width=True)

        st.dataframe(
            tech_stats,
            use_container_width=True,
            column_config={
                "total_counts":            st.column_config.NumberColumn("Total Counts", format="%d"),
                "unique_meds_counted":     st.column_config.NumberColumn("Unique Meds", format="%d"),
                "overdue_locations":       st.column_config.NumberColumn("Overdue (>30d)", format="%d"),
                "last_count_date":         st.column_config.DateColumn("Last Count Date"),
                "first_count_date":        st.column_config.DateColumn("First Count Date"),
                "avg_days_between_counts": st.column_config.NumberColumn("Avg Days/Count", format="%.1f"),
            },
            hide_index=True
        )

# ── TAB 4: CAROUSEL HEATMAP ───────────────────────────
with tab4:
    st.subheader("Carousel Coverage Heatmap")
    st.caption("Average days since last cycle count per carousel unit and shelf. Darker red = longer since counted.")

    if heatmap_data.empty:
        st.info("No carousel location data available to build heatmap.")
    else:
        carousel_units = sorted(heatmap_data["carousel_unit"].dropna().unique())
        selected_carousels = st.multiselect(
            "Filter Carousel Units", carousel_units, default=carousel_units
        )

        hm_filtered = heatmap_data[heatmap_data["carousel_unit"].isin(selected_carousels)]

        if not hm_filtered.empty:
            pivot = hm_filtered.pivot_table(
                index="shelf",
                columns="carousel_unit",
                values="avg_days",
                aggfunc="mean"
            ).round(0)

            fig_hm = px.imshow(
                pivot,
                color_continuous_scale="RdYlGn_r",
                title="Avg Days Since Cycle Count (by Carousel × Shelf)",
                labels={"color": "Avg Days"},
                aspect="auto"
            )
            fig_hm.update_layout(height=600)
            st.plotly_chart(fig_hm, use_container_width=True)

            # Summary per carousel unit
            st.divider()
            carousel_summary = (
                hm_filtered.groupby("carousel_unit")
                .agg(
                    avg_days=("avg_days", "mean"),
                    max_days=("avg_days", "max"),
                    total_meds=("med_count", "sum")
                )
                .reset_index()
                .round(1)
                .sort_values("avg_days", ascending=False)
            )
            st.dataframe(
                carousel_summary,
                use_container_width=True,
                column_config={
                    "avg_days":   st.column_config.NumberColumn("Avg Days Since Count", format="%.1f"),
                    "max_days":   st.column_config.NumberColumn("Max Days", format="%.0f"),
                    "total_meds": st.column_config.NumberColumn("Meds Tracked", format="%d"),
                },
                hide_index=True
            )

# ── TAB 5: RAW RETURN ACTIVITY ────────────────────────
with tab5:
    st.subheader("Return Activity Detail")
    st.caption(f"All returns from {start_date} to {end_date} with cycle count dates.")

    display_cols = [c for c in [
        "med_id", "med_desc", "carousel_location",
        "return_date", "qty", "user_name",
        "cycle_date", "cycle_count_user", "days_since_cycle"
    ] if c in tracker.columns]

    st.dataframe(
        tracker[display_cols].sort_values("days_since_cycle", ascending=False),
        use_container_width=True,
        column_config={
            "days_since_cycle": st.column_config.NumberColumn("Days Since Count", format="%d days"),
            "return_date":      st.column_config.DateColumn("Return Date"),
            "cycle_date":       st.column_config.DateColumn("Last Cycle Count"),
            "qty":              st.column_config.NumberColumn("Qty", format="%.0f"),
        },
        hide_index=True
    )
