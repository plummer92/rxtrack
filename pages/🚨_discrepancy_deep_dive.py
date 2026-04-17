import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import io
from sqlalchemy import text
import App


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

st.set_page_config(
    page_title="Discrepancy Deep Dive",
    page_icon="🚨",
    layout="wide"
)

# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════

engine = App.engine
render_sidebar = App.render_sidebar

start_date, end_date = render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Discrepancy Deep Dive",
        "Count errors, dollar risk, and likely-cause attribution by device and medication without dropping back into the old interface.",
        kicker="Performance",
    )
    App.record_ui_debug_event("Discrepancy Deep Dive", "shared_intro_loaded")
    App.render_ui_debugger("Discrepancy Deep Dive", intro_mode="shared")
else:
    st.header("🚨 Discrepancy Deep Dive")
    st.caption("Count errors, dollar risk, and likely-cause attribution by device and medication.")
    App.record_ui_debug_event("Discrepancy Deep Dive", "fallback_header_used")
    App.render_ui_debugger("Discrepancy Deep Dive", intro_mode="fallback")

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — DATA LOADERS
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_discrepancies(start, end):
    """Load all events with a non-zero discrepancy in the date range."""
    try:
        sql = text("""
            SELECT e.pk, e.dt, e.user_name, e.device, e.med_id, e.med_desc,
                   e.event_type, e.qty,
                   e.discrepancy_qty, e.discrepancy_reason,
                   COALESCE(c.cost_per_unit, 0) AS cost_per_unit
            FROM events e
            LEFT JOIN med_costs c ON e.med_id = c.med_id
            WHERE e.dt::date BETWEEN :start AND :end
              AND e.discrepancy_qty IS NOT NULL
              AND e.discrepancy_qty <> 0
            ORDER BY e.dt DESC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        df["dt"]              = pd.to_datetime(df["dt"], errors="coerce")
        df["discrepancy_qty"] = pd.to_numeric(df["discrepancy_qty"], errors="coerce")
        df["cost_per_unit"]   = pd.to_numeric(df["cost_per_unit"],   errors="coerce").fillna(0)
        df["dollar_risk"]     = df["discrepancy_qty"].abs() * df["cost_per_unit"]
        df["date"]            = df["dt"].dt.date
        return df
    except Exception as e:
        st.error(f"[load_discrepancies] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_prior_transactions(start, end):
    """
    Load all non-discrepancy events in a window before+during the date range
    so we can look up who last touched a med+device before each discrepancy.
    Pulls 60 days before start to ensure coverage.
    """
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id
            FROM events
            WHERE dt::date BETWEEN :lookback AND :end
              AND (discrepancy_qty IS NULL OR discrepancy_qty = 0)
            ORDER BY dt ASC
        """)
        from datetime import timedelta
        lookback = start - timedelta(days=60)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"lookback": lookback, "end": end})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        return df
    except Exception as e:
        st.warning(f"[load_prior_transactions] {e}")
        return pd.DataFrame()


# ── Execute loaders ───────────────────────────────────────────────────────────

with st.spinner("Loading discrepancy data..."):
    df_disc  = load_discrepancies(start_date, end_date)
    df_prior = load_prior_transactions(start_date, end_date)

if df_disc.empty:
    st.success("✅ No discrepancies found in the selected date range.")
    st.stop()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — PRIOR TRANSACTION ATTRIBUTION
# For each discrepancy, find the most recent non-discrepancy transaction
# on the same med_id + device BEFORE the discrepancy timestamp.
# ═══════════════════════════════════════════════════════════════════════════════

def find_prior_user(disc_row, prior_df):
    """Return the user_name of the last clean tx on same med+device before disc."""
    if prior_df.empty:
        return "Unknown"
    mask = (
        (prior_df["med_id"] == disc_row["med_id"]) &
        (prior_df["device"]  == disc_row["device"]) &
        (prior_df["dt"]      <  disc_row["dt"])
    )
    candidates = prior_df[mask]
    if candidates.empty:
        return "Unknown"
    return candidates.sort_values("dt").iloc[-1]["user_name"]


if not df_prior.empty:
    # Build a lookup dict keyed by (med_id, device) → sorted prior tx
    # for fast vectorised-ish lookup
    prior_grouped = df_prior.sort_values("dt")

    likely_causes = []
    for _, row in df_disc.iterrows():
        likely_causes.append(find_prior_user(row, prior_grouped))
    df_disc["likely_cause"] = [str(x) if x else "Unknown" for x in likely_causes]
else:
    df_disc["likely_cause"] = "Unknown"

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — OUTLIER FLAGS
# Flag discrepancies that are high-value OR from a repeat med/device
# ═══════════════════════════════════════════════════════════════════════════════

# Thresholds
DOLLAR_THRESHOLD  = df_disc["dollar_risk"].quantile(0.90)  # top 10% by value
REPEAT_MED_THRESH = 3   # med appears 3+ times
REPEAT_DEV_THRESH = 3   # device appears 3+ times

med_counts = df_disc["med_id"].value_counts()
dev_counts = df_disc["device"].value_counts()

def build_flags(row):
    flags = []
    if row["dollar_risk"] >= DOLLAR_THRESHOLD and row["dollar_risk"] > 0:
        flags.append("💰 High Value")
    if med_counts.get(row["med_id"], 0) >= REPEAT_MED_THRESH:
        flags.append("🔁 Repeat Med")
    if dev_counts.get(row["device"], 0) >= REPEAT_DEV_THRESH:
        flags.append("🖥️ Repeat Device")
    return ", ".join(flags) if flags else ""

df_disc["flags"] = df_disc.apply(build_flags, axis=1)
flagged_ct = (df_disc["flags"] != "").sum()

# ── Med Category Split ───────────────────────────────────────────────────────
# Inhalers/Insulin are flagged separately — they drive disproportionate counts
INHALER_INSULIN_PATTERN = r"hfa|inhaler|puff|actuat|insulin|lispro|glargine|detemir|aspart|glulisine|nph"

df_disc["med_category"] = df_disc["med_desc"].str.lower().str.contains(
    INHALER_INSULIN_PATTERN, regex=True, na=False
).map({True: "💨 Inhaler / Insulin", False: "💊 All Other Meds"})

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — SIDEBAR FILTERS
# ═══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.divider()
    st.subheader("🔎 Filters")

    dev_filter = st.multiselect(
        "Device",
        sorted(df_disc["device"].dropna().unique()),
        placeholder="All devices",
        key="disc_device_filter"
    )
    med_filter = st.multiselect(
        "Medication",
        sorted(df_disc["med_desc"].dropna().unique()),
        placeholder="All medications",
        key="disc_med_filter"
    )
    cause_filter = st.multiselect(
        "Likely Cause (User)",
        sorted(df_disc["likely_cause"].dropna().unique()),
        placeholder="All users",
        key="disc_cause_filter"
    )
    flagged_only = st.checkbox("Flagged only (high-value / repeat)", key="disc_flagged_only")
    cat_filter = st.radio(
        "Med Category",
        ["All", "💨 Inhaler / Insulin", "💊 All Other Meds"],
        index=0,
        key="disc_cat_filter"
    )

filtered = df_disc.copy()
if dev_filter:    filtered = filtered[filtered["device"].isin(dev_filter)]
if med_filter:    filtered = filtered[filtered["med_desc"].isin(med_filter)]
if cause_filter:  filtered = filtered[filtered["likely_cause"].isin(cause_filter)]
if flagged_only:  filtered = filtered[filtered["flags"] != ""]
if cat_filter != "All": filtered = filtered[filtered["med_category"] == cat_filter]

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 5 — EXECUTIVE METRICS
# ═══════════════════════════════════════════════════════════════════════════════

total_disc   = len(filtered)
total_risk   = filtered["dollar_risk"].sum()
avg_risk     = filtered["dollar_risk"].mean()
unique_meds  = filtered["med_id"].nunique()
unique_devs  = filtered["device"].nunique()

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Total Discrepancies",  f"{total_disc:,}")
m2.metric("Total Dollar Risk",    f"${total_risk:,.2f}")
m3.metric("Avg Risk per Event",   f"${avg_risk:,.2f}")
m4.metric("Medications Affected", unique_meds)
m5.metric("Devices Affected",     unique_devs)
m6.metric("⚠️ Flagged Events",    flagged_ct)

st.divider()

# ── Inhaler / Insulin vs All Other Meds split ────────────────────────────────
st.subheader("💨 Inhaler & Insulin vs 💊 All Other Meds")
st.caption("These meds typically drive a disproportionate share of count errors due to unit-of-measure complexity.")

inh_df  = filtered[filtered["med_category"] == "💨 Inhaler / Insulin"]
other_df = filtered[filtered["med_category"] == "💊 All Other Meds"]

sp1, sp2, sp3, sp4, sp5, sp6 = st.columns(6)
sp1.metric("💨 Inhaler/Insulin Count",    f"{len(inh_df):,}")
sp2.metric("💨 Inhaler/Insulin Risk",     f"${inh_df['dollar_risk'].sum():,.2f}")
sp3.metric("💨 % of Total Count",
           f"{len(inh_df)/len(filtered)*100:.1f}%" if len(filtered) > 0 else "0%")
sp4.metric("💊 Other Meds Count",         f"{len(other_df):,}")
sp5.metric("💊 Other Meds Risk",          f"${other_df['dollar_risk'].sum():,.2f}")
sp6.metric("💊 % of Total Count",
           f"{len(other_df)/len(filtered)*100:.1f}%" if len(filtered) > 0 else "0%")

# Side-by-side donut: count split and dollar risk split
if len(filtered) > 0:
    sc1, sc2 = st.columns(2)
    with sc1:
        cat_counts = filtered["med_category"].value_counts().reset_index()
        cat_counts.columns = ["category", "count"]
        fig_cat_ct = px.pie(
            cat_counts, names="category", values="count",
            hole=0.5,
            title="Share of Discrepancy Count",
            color="category",
            color_discrete_map={
                "💨 Inhaler / Insulin": "#f97316",
                "💊 All Other Meds":    "#3b82f6"
            }
        )
        fig_cat_ct.update_layout(height=300, margin=dict(t=40, b=0))
        st.plotly_chart(fig_cat_ct, use_container_width=True)
    with sc2:
        cat_risk = filtered.groupby("med_category")["dollar_risk"].sum().reset_index()
        cat_risk.columns = ["category", "dollar_risk"]
        fig_cat_risk = px.pie(
            cat_risk, names="category", values="dollar_risk",
            hole=0.5,
            title="Share of Dollar Risk",
            color="category",
            color_discrete_map={
                "💨 Inhaler / Insulin": "#f97316",
                "💊 All Other Meds":    "#3b82f6"
            }
        )
        fig_cat_risk.update_layout(height=300, margin=dict(t=40, b=0))
        st.plotly_chart(fig_cat_risk, use_container_width=True)

st.caption("Use the **Med Category** filter in the sidebar to drill into just one group across all tabs.")

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 6 — TABS
# ═══════════════════════════════════════════════════════════════════════════════

tab1, tab2, tab3, tab4 = st.tabs([
    "🖥️ By Device",
    "💊 By Medication",
    "👤 Likely Cause",
    "🔍 Raw Detail",
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — BY DEVICE
# ─────────────────────────────────────────────────────────────────────────────

with tab1:
    st.subheader("Discrepancies by Device")

    dev_summary = (
        filtered.groupby("device")
        .agg(
            disc_count   = ("pk",          "count"),
            total_risk   = ("dollar_risk", "sum"),
            avg_risk     = ("dollar_risk", "mean"),
            unique_meds  = ("med_id",      "nunique"),
            flagged      = ("flags",       lambda x: (x != "").sum()),
        )
        .reset_index()
        .sort_values("total_risk", ascending=False)
    )

    # Flag top offenders
    top_risk_dev = dev_summary["device"].iloc[0] if not dev_summary.empty else None

    col_a, col_b = st.columns(2)

    with col_a:
        fig_dev_ct = px.bar(
            dev_summary.head(15),
            x="disc_count", y="device",
            orientation="h",
            color="disc_count",
            color_continuous_scale="Reds",
            title="Discrepancy Count by Device",
            labels={"disc_count": "Count", "device": ""},
            text="disc_count"
        )
        fig_dev_ct.update_traces(textposition="outside")
        fig_dev_ct.update_layout(
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False, height=420,
            margin=dict(l=0, r=40, t=40, b=0)
        )
        st.plotly_chart(fig_dev_ct, use_container_width=True)

    with col_b:
        fig_dev_risk = px.bar(
            dev_summary.head(15),
            x="total_risk", y="device",
            orientation="h",
            color="total_risk",
            color_continuous_scale="Oranges",
            title="Dollar Risk by Device",
            labels={"total_risk": "Dollar Risk ($)", "device": ""},
            text=dev_summary.head(15)["total_risk"].apply(lambda x: f"${x:,.0f}")
        )
        fig_dev_risk.update_traces(textposition="outside")
        fig_dev_risk.update_layout(
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False, height=420,
            margin=dict(l=0, r=60, t=40, b=0)
        )
        st.plotly_chart(fig_dev_risk, use_container_width=True)

    # Outlier callout
    if top_risk_dev:
        top_row = dev_summary[dev_summary["device"] == top_risk_dev].iloc[0]
        st.warning(
            f"⚠️ **{top_risk_dev}** has the highest dollar risk — "
            f"${top_row['total_risk']:,.2f} across {int(top_row['disc_count'])} discrepancies "
            f"on {int(top_row['unique_meds'])} medications."
        )

    st.divider()
    st.dataframe(
        dev_summary,
        use_container_width=True,
        column_config={
            "disc_count":  st.column_config.NumberColumn("Count",        format="%d"),
            "total_risk":  st.column_config.NumberColumn("Total Risk",   format="$%.2f"),
            "avg_risk":    st.column_config.NumberColumn("Avg Risk",     format="$%.2f"),
            "unique_meds": st.column_config.NumberColumn("Unique Meds",  format="%d"),
            "flagged":     st.column_config.NumberColumn("Flagged",      format="%d"),
        },
        hide_index=True
    )
    st.download_button(
        "⬇️ Export Device Summary to Excel",
        data=to_excel_bytes(dev_summary),
        file_name="discrepancy_by_device.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — BY MEDICATION
# ─────────────────────────────────────────────────────────────────────────────

with tab2:
    st.subheader("Discrepancies by Medication")

    med_summary = (
        filtered.groupby(["med_id", "med_desc"])
        .agg(
            disc_count   = ("pk",          "count"),
            total_risk   = ("dollar_risk", "sum"),
            avg_risk     = ("dollar_risk", "mean"),
            unique_devs  = ("device",      "nunique"),
            flagged      = ("flags",       lambda x: (x != "").sum()),
        )
        .reset_index()
        .sort_values("total_risk", ascending=False)
    )

    top_risk_med = med_summary["med_desc"].iloc[0] if not med_summary.empty else None

    col_a, col_b = st.columns(2)

    with col_a:
        top_meds = med_summary.head(15)
        fig_med_ct = px.bar(
            top_meds,
            x="disc_count", y="med_desc",
            orientation="h",
            color="disc_count",
            color_continuous_scale="Reds",
            title="Most Frequently Discrepant Meds",
            labels={"disc_count": "Count", "med_desc": ""},
            text="disc_count"
        )
        fig_med_ct.update_traces(textposition="outside")
        fig_med_ct.update_layout(
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False, height=450,
            margin=dict(l=0, r=40, t=40, b=0)
        )
        st.plotly_chart(fig_med_ct, use_container_width=True)

    with col_b:
        fig_med_risk = px.bar(
            top_meds,
            x="total_risk", y="med_desc",
            orientation="h",
            color="total_risk",
            color_continuous_scale="Oranges",
            title="Highest Dollar Risk Meds",
            labels={"total_risk": "Dollar Risk ($)", "med_desc": ""},
            text=top_meds["total_risk"].apply(lambda x: f"${x:,.0f}")
        )
        fig_med_risk.update_traces(textposition="outside")
        fig_med_risk.update_layout(
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False, height=450,
            margin=dict(l=0, r=60, t=40, b=0)
        )
        st.plotly_chart(fig_med_risk, use_container_width=True)

    # Outlier callout
    if top_risk_med:
        top_row = med_summary[med_summary["med_desc"] == top_risk_med].iloc[0]
        st.warning(
            f"⚠️ **{top_risk_med}** has the highest dollar risk — "
            f"${top_row['total_risk']:,.2f} across {int(top_row['disc_count'])} discrepancies "
            f"on {int(top_row['unique_devs'])} device(s)."
        )

    st.divider()

    # Device × Med heatmap
    st.subheader("Device × Medication Heatmap")
    st.caption("Dollar risk at the intersection of each device and medication.")

    heat_data = (
        filtered.groupby(["device", "med_desc"])["dollar_risk"]
        .sum()
        .reset_index()
    )
    # Limit to top 15 devices and top 15 meds by total risk to keep it readable
    top_devs_heat = heat_data.groupby("device")["dollar_risk"].sum().nlargest(15).index
    top_meds_heat = heat_data.groupby("med_desc")["dollar_risk"].sum().nlargest(15).index
    heat_data = heat_data[
        heat_data["device"].isin(top_devs_heat) &
        heat_data["med_desc"].isin(top_meds_heat)
    ]

    if not heat_data.empty:
        heat_pivot = heat_data.pivot(index="med_desc", columns="device", values="dollar_risk").fillna(0)
        fig_heat = px.imshow(
            heat_pivot,
            color_continuous_scale="YlOrRd",
            title="Dollar Risk Heatmap — Top 15 Devices × Top 15 Meds",
            labels={"color": "Dollar Risk ($)"},
            aspect="auto"
        )
        fig_heat.update_layout(height=500)
        st.plotly_chart(fig_heat, use_container_width=True)

    st.divider()
    st.dataframe(
        med_summary,
        use_container_width=True,
        column_config={
            "disc_count":  st.column_config.NumberColumn("Count",       format="%d"),
            "total_risk":  st.column_config.NumberColumn("Total Risk",  format="$%.2f"),
            "avg_risk":    st.column_config.NumberColumn("Avg Risk",    format="$%.2f"),
            "unique_devs": st.column_config.NumberColumn("Devices",     format="%d"),
            "flagged":     st.column_config.NumberColumn("Flagged",     format="%d"),
        },
        hide_index=True
    )
    st.download_button(
        "⬇️ Export Medication Summary to Excel",
        data=to_excel_bytes(med_summary),
        file_name="discrepancy_by_medication.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — LIKELY CAUSE
# ─────────────────────────────────────────────────────────────────────────────

with tab3:
    st.subheader("Likely Cause Attribution")
    st.caption(
        "Based on whoever did the most recent clean transaction on that med+device "
        "before the discrepancy was logged. This is a probable indicator — not proof."
    )

    cause_summary = (
        filtered.groupby("likely_cause")
        .agg(
            disc_count  = ("pk",          "count"),
            total_risk  = ("dollar_risk", "sum"),
            avg_risk    = ("dollar_risk", "mean"),
            unique_meds = ("med_id",      "nunique"),
            unique_devs = ("device",      "nunique"),
        )
        .reset_index()
        .sort_values("total_risk", ascending=False)
    )

    # Exclude Unknown from charts but keep in table
    known = cause_summary[cause_summary["likely_cause"] != "Unknown"]

    col_a, col_b = st.columns(2)

    with col_a:
        if not known.empty:
            fig_cause_ct = px.bar(
                known.head(12),
                x="disc_count", y="likely_cause",
                orientation="h",
                color="disc_count",
                color_continuous_scale="Reds",
                title="Discrepancy Count by Likely Cause",
                labels={"disc_count": "Count", "likely_cause": ""},
                text="disc_count"
            )
            fig_cause_ct.update_traces(textposition="outside")
            fig_cause_ct.update_layout(
                yaxis={"categoryorder": "total ascending"},
                coloraxis_showscale=False, height=420,
                margin=dict(l=0, r=40, t=40, b=0)
            )
            st.plotly_chart(fig_cause_ct, use_container_width=True)

    with col_b:
        if not known.empty:
            fig_cause_risk = px.bar(
                known.head(12),
                x="total_risk", y="likely_cause",
                orientation="h",
                color="total_risk",
                color_continuous_scale="Oranges",
                title="Dollar Risk by Likely Cause",
                labels={"total_risk": "Dollar Risk ($)", "likely_cause": ""},
                text=known.head(12)["total_risk"].apply(lambda x: f"${x:,.0f}")
            )
            fig_cause_risk.update_traces(textposition="outside")
            fig_cause_risk.update_layout(
                yaxis={"categoryorder": "total ascending"},
                coloraxis_showscale=False, height=420,
                margin=dict(l=0, r=60, t=40, b=0)
            )
            st.plotly_chart(fig_cause_risk, use_container_width=True)

    unknown_ct = int((filtered["likely_cause"] == "Unknown").sum())
    if unknown_ct > 0:
        st.info(
            f"ℹ️ {unknown_ct} discrepancies ({unknown_ct/total_disc*100:.1f}%) have no prior "
            f"transaction on record — likely the first ever transaction on that med+device, "
            f"or data predates your earliest upload."
        )

    st.divider()

    # Drill-down: pick a user and see their events
    st.subheader("User Drill-Down")
    known_users = sorted(filtered[
        (filtered["likely_cause"] != "Unknown") &
        (filtered["likely_cause"].notna())
    ]["likely_cause"].astype(str).unique())
    if known_users:
        sel_user = st.selectbox(
            "Select likely cause user to review their events",
            known_users,
            key="disc_drilldown_user"
        )
        user_events = filtered[filtered["likely_cause"] == sel_user][[
            "dt", "device", "med_desc", "discrepancy_qty",
            "discrepancy_reason", "dollar_risk", "flags"
        ]].sort_values("dollar_risk", ascending=False)

        st.dataframe(
            user_events,
            use_container_width=True,
            column_config={
                "dt":               st.column_config.DatetimeColumn("Date/Time",    format="MM/DD/YY HH:mm"),
                "discrepancy_qty":  st.column_config.NumberColumn("Disc Qty",       format="%.0f"),
                "dollar_risk":      st.column_config.NumberColumn("Dollar Risk",    format="$%.2f"),
            },
            hide_index=True
        )

    st.divider()
    st.dataframe(
        cause_summary,
        use_container_width=True,
        column_config={
            "disc_count":  st.column_config.NumberColumn("Count",       format="%d"),
            "total_risk":  st.column_config.NumberColumn("Total Risk",  format="$%.2f"),
            "avg_risk":    st.column_config.NumberColumn("Avg Risk",    format="$%.2f"),
            "unique_meds": st.column_config.NumberColumn("Meds",        format="%d"),
            "unique_devs": st.column_config.NumberColumn("Devices",     format="%d"),
        },
        hide_index=True
    )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — RAW DETAIL
# ─────────────────────────────────────────────────────────────────────────────

with tab4:
    st.subheader("Raw Discrepancy Detail")
    st.caption(f"{len(filtered):,} discrepancy events — sorted by dollar risk descending.")

    # Flagged events first
    flagged_df = filtered[filtered["flags"] != ""].sort_values("dollar_risk", ascending=False)
    unflagged_df = filtered[filtered["flags"] == ""].sort_values("dollar_risk", ascending=False)
    display_df = pd.concat([flagged_df, unflagged_df])

    st.dataframe(
        display_df[[
            "flags", "dt", "device", "med_desc",
            "discrepancy_qty", "discrepancy_reason",
            "cost_per_unit", "dollar_risk",
            "user_name", "likely_cause"
        ]],
        use_container_width=True,
        column_config={
            "flags":             st.column_config.TextColumn("⚠️ Flags"),
            "dt":                st.column_config.DatetimeColumn("Date/Time",      format="MM/DD/YY HH:mm"),
            "discrepancy_qty":   st.column_config.NumberColumn("Disc Qty",         format="%.0f"),
            "cost_per_unit":     st.column_config.NumberColumn("Unit Cost",        format="$%.2f"),
            "dollar_risk":       st.column_config.NumberColumn("Dollar Risk",      format="$%.2f"),
            "user_name":         st.column_config.TextColumn("Found By"),
            "likely_cause":      st.column_config.TextColumn("Likely Cause"),
        },
        hide_index=True
    )
