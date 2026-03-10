import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from sqlalchemy import text
from App import engine, render_sidebar

st.set_page_config(
    page_title="Pends Analyzer",
    page_icon="📥",
    layout="wide"
)

# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════

start_date, end_date = render_sidebar()

st.header("📥 Pends Analyzer")
st.caption("Audit who pended medications, what par levels they set, and whether they made them standard stock.")

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — DATA LOADERS
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_pends(start, end):
    """Load config_events for the selected date range."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, location,
                   action_type, activity_category, min_qty, max_qty, is_standard
            FROM config_events
            WHERE dt::date BETWEEN :start AND :end
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        df["dt"]          = pd.to_datetime(df["dt"], errors="coerce")
        df["user_name"]   = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["device"]      = df["device"].fillna("Unknown").astype(str).str.strip()
        df["med_id"]      = df["med_id"].astype(str).str.strip().str.upper()
        df["action_type"] = df["action_type"].astype(str).str.strip()
        df["date"]        = df["dt"].dt.date
        return df
    except Exception as e:
        st.error(f"[load_pends] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def load_drug_names():
    """Load med_id -> drug_name + trade_name + carousel_location from master mapping."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT med_id, med_desc, drug_name, trade_name, carousel_location "
                "FROM carousel_master_mapping"
            ))
            df = pd.DataFrame(result.fetchall(), columns=result.keys())
        df["med_id"] = df["med_id"].astype(str).str.strip().str.upper()
        df["display_name"] = df.apply(
            lambda r: (
                f"{r['drug_name']} ({r['trade_name']})"
                if pd.notna(r.get("trade_name")) and str(r.get("trade_name", "")).strip()
                else str(r.get("drug_name", r["med_desc"]))
            ),
            axis=1
        )
        df["is_controlled"] = df["carousel_location"].astype(str).str.startswith("CW")
        return df[["med_id", "drug_name", "trade_name", "display_name",
                   "carousel_location", "is_controlled"]]
    except Exception as e:
        st.warning(f"[load_drug_names] {e}")
        return pd.DataFrame()


df_raw   = load_pends(start_date, end_date)
df_drugs = load_drug_names()

if df_raw.empty:
    st.warning("No pend activity found for the selected date range.")
    st.info("Upload a Device Activity Log (Pends) via the main upload page to populate this view.")
    st.stop()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — JOIN DRUG NAMES
# ═══════════════════════════════════════════════════════════════════════════════

if not df_drugs.empty:
    df = df_raw.merge(df_drugs, on="med_id", how="left")
else:
    df = df_raw.copy()
    for col in ["drug_name", "trade_name", "display_name", "carousel_location"]:
        df[col] = None
    df["is_controlled"] = False

df["display_name"] = df["display_name"].fillna(df["med_id"])
df["drug_name"]    = df["drug_name"].fillna(df["med_id"])

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — SIDEBAR FILTERS
# ═══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.divider()
    st.subheader("Filters")

    med_filter = st.multiselect(
        "Medication",
        sorted(df["display_name"].dropna().unique()),
        placeholder="All medications",
        key="pends_med_filter"
    )
    user_filter = st.multiselect(
        "User",
        sorted(df["user_name"].dropna().unique()),
        placeholder="All users",
        key="pends_user_filter"
    )
    device_filter = st.multiselect(
        "Device",
        sorted(df["device"].dropna().unique()),
        placeholder="All devices",
        key="pends_device_filter"
    )
    standard_filter = st.radio(
        "Standard Stock",
        ["All", "Standard Only", "Non-Standard Only"],
        index=0,
        key="pends_standard_filter"
    )

filtered = df.copy()
if med_filter:     filtered = filtered[filtered["display_name"].isin(med_filter)]
if user_filter:    filtered = filtered[filtered["user_name"].isin(user_filter)]
if device_filter:  filtered = filtered[filtered["device"].isin(device_filter)]
if standard_filter == "Standard Only":
    filtered = filtered[filtered["is_standard"] == True]
elif standard_filter == "Non-Standard Only":
    filtered = filtered[filtered["is_standard"] == False]

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — EXECUTIVE METRICS
# ═══════════════════════════════════════════════════════════════════════════════

total_pends    = len(filtered)
unique_meds    = filtered["med_id"].nunique()
unique_users   = filtered["user_name"].nunique()
unique_devices = filtered["device"].nunique()
standard_ct    = int((filtered["is_standard"] == True).sum())
nonstd_ct      = int((filtered["is_standard"] == False).sum())

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Total Pend Events",  total_pends)
m2.metric("Unique Medications", unique_meds)
m3.metric("Users",              unique_users)
m4.metric("Devices",            unique_devices)
m5.metric("Made Standard",      standard_ct)
m6.metric("Non-Standard",       nonstd_ct)

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 5 — TABS
# ═══════════════════════════════════════════════════════════════════════════════

tab1, tab2, tab3, tab4 = st.tabs([
    "📋 Par Audit",
    "👤 By User",
    "🖥️ By Device",
    "🔍 Raw Detail",
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — PAR AUDIT
# ─────────────────────────────────────────────────────────────────────────────

with tab1:
    st.subheader("Par Level Audit")
    st.caption(
        "One row per medication — who pended it, what min/max they set, "
        "and whether it was made standard stock."
    )

    par_audit = (
        filtered
        .groupby(
            ["med_id", "display_name", "drug_name", "trade_name",
             "carousel_location", "is_controlled"],
            dropna=False
        )
        .agg(
            pend_count    = ("pk",          "count"),
            users         = ("user_name",   lambda x: ", ".join(sorted(x.dropna().astype(str).unique()))),
            devices       = ("device",      lambda x: ", ".join(sorted(x.dropna().astype(str).unique()))),
            latest_min    = ("min_qty",     "last"),
            latest_max    = ("max_qty",     "last"),
            made_standard = ("is_standard", lambda x: (x == True).any()),
            last_pend_dt  = ("dt",          "max"),
            first_pend_dt = ("dt",          "min"),
            action_types  = ("action_type", lambda x: ", ".join(sorted(x.dropna().astype(str).unique()))),
        )
        .reset_index()
        .sort_values("last_pend_dt", ascending=False)
    )

    par_audit["in_master"] = par_audit["carousel_location"].notna()

    sa, sb = st.columns(2)
    sa.metric("Meds Made Standard Stock", int(par_audit["made_standard"].sum()))
    sb.metric("Meds Kept Non-Standard",   int((par_audit["made_standard"] == False).sum()))

    # Most pended meds chart
    top_pended = par_audit.head(25)
    if not top_pended.empty:
        fig = px.bar(
            top_pended,
            x="pend_count", y="display_name",
            orientation="h",
            color="made_standard",
            color_discrete_map={True: "#22c55e", False: "#f97316"},
            title="Most Frequently Pended Medications",
            labels={"pend_count": "Pend Events", "display_name": "", "made_standard": "Standard Stock"}
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=500)
        st.plotly_chart(fig, use_container_width=True)

    # Full audit table
    st.divider()
    display_cols = [c for c in [
        "display_name", "drug_name", "trade_name", "carousel_location",
        "is_controlled", "pend_count", "latest_min", "latest_max",
        "made_standard", "users", "devices", "action_types",
        "last_pend_dt", "first_pend_dt", "in_master"
    ] if c in par_audit.columns]

    st.dataframe(
        par_audit[display_cols],
        use_container_width=True,
        column_config={
            "display_name":      st.column_config.TextColumn("Medication"),
            "drug_name":         st.column_config.TextColumn("Generic Name"),
            "trade_name":        st.column_config.TextColumn("Trade Name"),
            "carousel_location": st.column_config.TextColumn("Carousel Location"),
            "is_controlled":     st.column_config.CheckboxColumn("Controlled"),
            "pend_count":        st.column_config.NumberColumn("# Pend Events", format="%d"),
            "latest_min":        st.column_config.NumberColumn("Current Min",   format="%.0f"),
            "latest_max":        st.column_config.NumberColumn("Current Max",   format="%.0f"),
            "made_standard":     st.column_config.CheckboxColumn("Made Standard"),
            "in_master":         st.column_config.CheckboxColumn("In Carousel Master"),
            "last_pend_dt":      st.column_config.DatetimeColumn("Last Pended",  format="MM/DD/YY HH:mm"),
            "first_pend_dt":     st.column_config.DatetimeColumn("First Pended", format="MM/DD/YY HH:mm"),
        },
        hide_index=True
    )

    # Pended meds NOT in carousel master
    st.divider()
    st.subheader("New Additions — Not in Carousel Master")
    st.caption("These meds were pended but don't appear in your carousel master mapping. Possible new stock additions or one-offs.")
    new_meds = par_audit[par_audit["in_master"] == False]
    if new_meds.empty:
        st.success("All pended medications are in the carousel master mapping.")
    else:
        st.dataframe(
            new_meds[[c for c in [
                "med_id", "display_name", "pend_count", "latest_min", "latest_max",
                "made_standard", "users", "last_pend_dt"
            ] if c in new_meds.columns]],
            use_container_width=True,
            column_config={
                "pend_count":    st.column_config.NumberColumn("# Pend Events", format="%d"),
                "latest_min":    st.column_config.NumberColumn("Min",           format="%.0f"),
                "latest_max":    st.column_config.NumberColumn("Max",           format="%.0f"),
                "made_standard": st.column_config.CheckboxColumn("Made Standard"),
                "last_pend_dt":  st.column_config.DatetimeColumn("Last Pended", format="MM/DD/YY HH:mm"),
            },
            hide_index=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — BY USER
# ─────────────────────────────────────────────────────────────────────────────

with tab2:
    st.subheader("Pend Activity by User")

    user_summary = (
        filtered
        .groupby("user_name")
        .agg(
            total_pends    = ("pk",          "count"),
            unique_meds    = ("med_id",      "nunique"),
            unique_devices = ("device",      "nunique"),
            made_standard  = ("is_standard", lambda x: (x == True).sum()),
            non_standard   = ("is_standard", lambda x: (x == False).sum()),
            last_pend      = ("dt",          "max"),
        )
        .reset_index()
        .sort_values("total_pends", ascending=False)
    )

    fig_usr = px.bar(
        user_summary,
        x="user_name", y=["made_standard", "non_standard"],
        title="Pend Events by User — Standard vs Non-Standard",
        labels={"value": "Events", "user_name": "", "variable": ""},
        color_discrete_map={"made_standard": "#22c55e", "non_standard": "#f97316"},
        barmode="stack"
    )
    st.plotly_chart(fig_usr, use_container_width=True)

    st.dataframe(
        user_summary,
        use_container_width=True,
        column_config={
            "total_pends":    st.column_config.NumberColumn("Total Pends",   format="%d"),
            "unique_meds":    st.column_config.NumberColumn("Unique Meds",   format="%d"),
            "unique_devices": st.column_config.NumberColumn("Devices",       format="%d"),
            "made_standard":  st.column_config.NumberColumn("Made Standard", format="%d"),
            "non_standard":   st.column_config.NumberColumn("Non-Standard",  format="%d"),
            "last_pend":      st.column_config.DatetimeColumn("Last Pend",   format="MM/DD/YY HH:mm"),
        },
        hide_index=True
    )

    # Per-user drill-down
    st.divider()
    st.subheader("User Drill-Down")
    sel_user = st.selectbox(
        "Select user",
        sorted(filtered["user_name"].dropna().unique()),
        key="user_drilldown"
    )
    if sel_user:
        user_detail = filtered[filtered["user_name"] == sel_user][[
            "dt", "display_name", "drug_name", "device",
            "action_type", "min_qty", "max_qty", "is_standard"
        ]].sort_values("dt", ascending=False)
        st.dataframe(
            user_detail,
            use_container_width=True,
            column_config={
                "dt":           st.column_config.DatetimeColumn("Date/Time", format="MM/DD/YY HH:mm"),
                "display_name": st.column_config.TextColumn("Medication"),
                "drug_name":    st.column_config.TextColumn("Generic"),
                "min_qty":      st.column_config.NumberColumn("Min",        format="%.0f"),
                "max_qty":      st.column_config.NumberColumn("Max",        format="%.0f"),
                "is_standard":  st.column_config.CheckboxColumn("Standard"),
            },
            hide_index=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — BY DEVICE
# ─────────────────────────────────────────────────────────────────────────────

with tab3:
    st.subheader("Pend Activity by Device")

    device_summary = (
        filtered
        .groupby("device")
        .agg(
            total_pends   = ("pk",          "count"),
            unique_meds   = ("med_id",      "nunique"),
            unique_users  = ("user_name",   "nunique"),
            made_standard = ("is_standard", lambda x: (x == True).sum()),
            non_standard  = ("is_standard", lambda x: (x == False).sum()),
            last_pend     = ("dt",          "max"),
        )
        .reset_index()
        .sort_values("total_pends", ascending=False)
    )

    fig_dev = px.bar(
        device_summary,
        x="device", y=["made_standard", "non_standard"],
        title="Pend Events by Device — Standard vs Non-Standard",
        labels={"value": "Events", "device": "", "variable": ""},
        color_discrete_map={"made_standard": "#22c55e", "non_standard": "#f97316"},
        barmode="stack"
    )
    st.plotly_chart(fig_dev, use_container_width=True)

    st.dataframe(
        device_summary,
        use_container_width=True,
        column_config={
            "total_pends":   st.column_config.NumberColumn("Total Pends",   format="%d"),
            "unique_meds":   st.column_config.NumberColumn("Unique Meds",   format="%d"),
            "unique_users":  st.column_config.NumberColumn("Users",         format="%d"),
            "made_standard": st.column_config.NumberColumn("Made Standard", format="%d"),
            "non_standard":  st.column_config.NumberColumn("Non-Standard",  format="%d"),
            "last_pend":     st.column_config.DatetimeColumn("Last Pend",   format="MM/DD/YY HH:mm"),
        },
        hide_index=True
    )

    # Per-device drill-down
    st.divider()
    st.subheader("Device Drill-Down")
    sel_device = st.selectbox(
        "Select device",
        sorted(filtered["device"].dropna().unique()),
        key="device_drilldown"
    )
    if sel_device:
        dev_detail = filtered[filtered["device"] == sel_device][[
            "dt", "display_name", "drug_name", "user_name",
            "action_type", "min_qty", "max_qty", "is_standard"
        ]].sort_values("dt", ascending=False)
        st.dataframe(
            dev_detail,
            use_container_width=True,
            column_config={
                "dt":           st.column_config.DatetimeColumn("Date/Time", format="MM/DD/YY HH:mm"),
                "display_name": st.column_config.TextColumn("Medication"),
                "drug_name":    st.column_config.TextColumn("Generic"),
                "min_qty":      st.column_config.NumberColumn("Min",        format="%.0f"),
                "max_qty":      st.column_config.NumberColumn("Max",        format="%.0f"),
                "is_standard":  st.column_config.CheckboxColumn("Standard"),
            },
            hide_index=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — RAW DETAIL
# ─────────────────────────────────────────────────────────────────────────────

with tab4:
    st.subheader("Raw Pend Detail")
    st.caption(f"{len(filtered):,} events matching current filters.")

    display_cols = [c for c in [
        "dt", "user_name", "device",
        "med_id", "display_name", "drug_name", "trade_name",
        "carousel_location", "is_controlled",
        "action_type", "activity_category",
        "min_qty", "max_qty", "is_standard",
    ] if c in filtered.columns]

    st.dataframe(
        filtered[display_cols].sort_values("dt", ascending=False),
        use_container_width=True,
        column_config={
            "dt":                st.column_config.DatetimeColumn("Date/Time",    format="MM/DD/YY HH:mm"),
            "display_name":      st.column_config.TextColumn("Medication"),
            "drug_name":         st.column_config.TextColumn("Generic"),
            "trade_name":        st.column_config.TextColumn("Trade Name"),
            "carousel_location": st.column_config.TextColumn("Carousel Loc"),
            "is_controlled":     st.column_config.CheckboxColumn("Controlled"),
            "min_qty":           st.column_config.NumberColumn("Min",            format="%.0f"),
            "max_qty":           st.column_config.NumberColumn("Max",            format="%.0f"),
            "is_standard":       st.column_config.CheckboxColumn("Standard"),
        },
        hide_index=True
    )
