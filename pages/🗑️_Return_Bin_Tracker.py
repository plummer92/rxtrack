import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import io
from datetime import date, timedelta
from sqlalchemy import text
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

NON_CASSETTE_DEVICES = {
    "SJSANTE", "SJSBCAB", "SJSBCC", "SJSBCTRIAG", "SJSBRONCH", "SJSCARDVS", "SJSCARDVS2", "SJSCARDVS3",
    "SJSCATH4-ANES", "SJSCATHL1", "SJSCATHL10", "SJSCATHL11", "SJSCATHL12", "SJSCATHL3", "SJSCATHL4",
    "SJSCATHL5", "SJSCATHL6", "SJSCATHL7", "SJSCATHL8", "SJSCATHL9", "SJSCSECT-CORE", "SJSCSECT1",
    "SJSCSECT2", "SJSCSOR", "SJSCSOR-2", "SJSCSOR-3", "SJSCSOR-4", "SJSCSOR-5", "SJSCSOR-6", "SJSCSOR-7",
    "SJSCSOR-8", "SJSDIAL", "SJSEDSO", "SJSEDTRIAG", "SJSEMS", "SJSER", "SJSGI1", "SJSGI2", "SJSGI3",
    "SJSGI4", "SJSGIMAIN", "SJSICE", "SJSNICU", "SJSNUCMED", "SJSOR", "SJSOR-1", "SJSOR-10", "SJSOR-11",
    "SJSOR-12", "SJSOR-13", "SJSOR-14", "SJSOR-15", "SJSOR-16", "SJSOR-2", "SJSOR-3", "SJSOR-4", "SJSOR-5",
    "SJSOR-6", "SJSOR-7", "SJSOR-8", "SJSOR-9", "SJSOR2", "SJSOSC1", "SJSOSC2", "SJSOSC3", "SJSOSC4",
    "SJSOSC5", "SJSOSCOR", "SJSOSCPACU", "SJSPACU", "SJSPACU-21", "SJSPACUNOR", "SJSPEDISED", "SJSPEDOP",
    "SJSPEDPREA", "SJSPHARM", "SJSPREAN", "SJSPREAN2", "SJSPRECATH", "SJSRADM", "SJSRWC", "SJSTRAUMA1",
    "SJSTRAUMA2", "SJSTRAUMA3",
}

st.set_page_config(page_title="Return Bin & Patient Cassette Tracker", page_icon="🗑️", layout="wide")
App.apply_global_styles()

engine = App.engine
render_sidebar = App.render_sidebar

start_date, end_date = render_sidebar()
if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Return Bin & Patient Cassette Tracker",
        "Monitor empty-return-bin and patient cassette activity per Pyxis device with visibility into coverage, recency, and operational gaps.",
        kicker="Operations",
    )
    _debug_event("Return Bin & Patient Cassette Tracker", "shared_intro_loaded")
    _debug_panel("Return Bin & Patient Cassette Tracker", intro_mode="shared")
else:
    st.header("🗑️ Return Bin & Patient Cassette Tracker")
    st.caption("Monitor empty-return-bin and patient cassette activity per Pyxis device with visibility into coverage, recency, and operational gaps.")
    _debug_event("Return Bin & Patient Cassette Tracker", "fallback_header_used")
    _debug_panel("Return Bin & Patient Cassette Tracker", intro_mode="fallback")


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADERS
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_bin_events(start, end):
    """All empty-return-bin events in the selected window."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type, qty
            FROM events
            WHERE dt::date BETWEEN :start AND :end
              AND (
                    event_type ILIKE '%empty%'
                 OR event_type ILIKE '%return bin%'
              )
              AND event_type NOT ILIKE '%cancel%'
              AND event_type NOT ILIKE '%eject%'
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["device"] = df["device"].fillna("Unknown").astype(str).str.strip()
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"[load_bin_events] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_last_emptied_all_time():
    """Most recent empty-return-bin event per device across all time."""
    try:
        sql = text("""
            SELECT device, MAX(dt) AS last_emptied
            FROM events
            WHERE (event_type ILIKE '%empty%' OR event_type ILIKE '%return bin%')
              AND event_type NOT ILIKE '%cancel%'
              AND event_type NOT ILIKE '%eject%'
            GROUP BY device
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn)
        df["last_emptied"] = pd.to_datetime(df["last_emptied"], errors="coerce")
        df["device"] = df["device"].fillna("Unknown").astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"[load_last_emptied_all_time] {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_all_devices():
    """All distinct devices that have ever had any transaction."""
    try:
        sql = text("SELECT DISTINCT device FROM events WHERE device IS NOT NULL")
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn)
        df["device"] = df["device"].fillna("Unknown").astype(str).str.strip()
        return df
    except Exception as e:
        return pd.DataFrame(columns=["device"])


@st.cache_data(ttl=300)
def load_cassette_events(start, end):
    """Patient cassette events in the selected window."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type, qty
            FROM events
            WHERE dt::date BETWEEN :start AND :end
              AND (
                    event_type ILIKE '%cassette%'
                 OR med_desc ILIKE '%cassette%'
                 OR device ILIKE '%cassette%'
                 OR device ILIKE '%patient%'
              )
              AND event_type NOT ILIKE '%cancel%'
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["device"] = df["device"].fillna("Unknown").astype(str).str.strip()
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"[load_cassette_events] {e}")
        return pd.DataFrame(columns=["pk", "dt", "user_name", "device", "med_id", "med_desc", "event_type", "qty"])


@st.cache_data(ttl=300)
def load_last_cassette_all_time():
    """Most recent patient cassette event per device across all time."""
    try:
        sql = text("""
            SELECT device, MAX(dt) AS last_cassette
            FROM events
            WHERE (
                    event_type ILIKE '%cassette%'
                 OR med_desc ILIKE '%cassette%'
                 OR device ILIKE '%cassette%'
                 OR device ILIKE '%patient%'
              )
              AND event_type NOT ILIKE '%cancel%'
            GROUP BY device
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn)
        df["last_cassette"] = pd.to_datetime(df["last_cassette"], errors="coerce")
        df["device"] = df["device"].fillna("Unknown").astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"[load_last_cassette_all_time] {e}")
        return pd.DataFrame(columns=["device", "last_cassette"])


with st.spinner("Loading return bin data..."):
    df_bins    = load_bin_events(start_date, end_date)
    df_last    = load_last_emptied_all_time()
    df_devices = load_all_devices()
    df_cassette = load_cassette_events(start_date, end_date)
    df_cassette_last = load_last_cassette_all_time()

if df_devices.empty:
    st.warning("No device data found. Upload a Daily Transaction Report first.")
    st.stop()

all_devices = set(df_devices["device"].unique())
cassette_eligible_devices = sorted(all_devices - NON_CASSETTE_DEVICES)

# ═══════════════════════════════════════════════════════════════════════════════
# RECENCY TABLE — all devices + days since last emptied
# ═══════════════════════════════════════════════════════════════════════════════

today = date.today()

recency = pd.DataFrame({"device": sorted(all_devices)})
recency = recency.merge(df_last, on="device", how="left")
recency["days_since"] = recency["last_emptied"].apply(
    lambda x: (pd.Timestamp(today) - x).days if pd.notna(x) else None
)
recency["status"] = recency["days_since"].apply(
    lambda d: "✅ Recent (≤1d)"   if pd.notna(d) and d <= 1
         else "🟡 1–3 days"        if pd.notna(d) and d <= 3
         else "🟠 4–7 days"        if pd.notna(d) and d <= 7
         else "🔴 Over a week"     if pd.notna(d) and d > 7
         else "⬛ Never recorded"
)
recency["last_emptied_display"] = recency["last_emptied"].dt.strftime("%b %d, %Y %H:%M").fillna("—")

# ── Summary KPIs ──────────────────────────────────────────────────────────────
total_devices  = len(recency)
recent_ct      = int((recency["days_since"] <= 1).sum())
warn_ct        = int(((recency["days_since"] > 1) & (recency["days_since"] <= 7)).sum())
overdue_ct     = int((recency["days_since"] > 7).sum())
never_ct       = int(recency["days_since"].isna().sum())
touched_window = df_bins["device"].nunique() if not df_bins.empty else 0

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Total Devices",          total_devices)
k2.metric("✅ Emptied Today/Yesterday", recent_ct)
k3.metric("🟡 1–3 Days Ago",        warn_ct)
k4.metric("🔴 Overdue (>7 days)",   overdue_ct)
k5.metric("⬛ Never Recorded",      never_ct)
k6.metric("Touched in Window",      touched_window)

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "⏱️ Recency Status",
    "📅 Daily Coverage",
    "👤 By Technician",
    "🔍 Raw Events",
    "Patient Cassettes",
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — RECENCY STATUS
# ─────────────────────────────────────────────────────────────────────────────

with tab1:
    st.subheader("Days Since Last Return Bin Emptied — All Devices")
    st.caption("Based on all-time event history. Devices with no record shown at bottom.")

    # Status filter
    status_opts = sorted(recency["status"].unique())
    sel_status = st.multiselect(
        "Filter by Status", options=status_opts, default=status_opts,
        key="recency_status_filter"
    )
    view = recency[recency["status"].isin(sel_status)] if sel_status else recency

    # Bar chart — days since per device
    chart_df = view[view["days_since"].notna()].sort_values("days_since", ascending=False)
    if not chart_df.empty:
        max_days = chart_df["days_since"].max()
        fig = px.bar(
            chart_df,
            x="days_since", y="device",
            orientation="h",
            color="days_since",
            color_continuous_scale=["#22c55e", "#facc15", "#f97316", "#ef4444"],
            range_color=[0, max(max_days, 14)],
            labels={"days_since": "Days Since Emptied", "device": ""},
            title="Days Since Last Return Bin Emptied",
            text="days_since",
        )
        fig.update_traces(texttemplate="%{text:.0f}d", textposition="outside")
        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False,
            height=max(300, len(chart_df) * 28),
            margin=dict(l=0, r=60, t=40, b=0)
        )
        st.plotly_chart(fig, use_container_width=True)

    # Never-emptied devices
    never_df = view[view["days_since"].isna()]
    if not never_df.empty:
        st.warning(f"⬛ **{len(never_df)} device(s) have no return bin event on record.**")

    st.divider()
    st.dataframe(
        view[["device", "last_emptied_display", "days_since", "status"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "device":               st.column_config.TextColumn("Device"),
            "last_emptied_display": st.column_config.TextColumn("Last Emptied"),
            "days_since":           st.column_config.NumberColumn("Days Since", format="%d"),
            "status":               st.column_config.TextColumn("Status"),
        }
    )
    st.download_button(
        "⬇️ Export Recency Status to Excel",
        data=to_excel_bytes(view[["device", "last_emptied_display", "days_since", "status"]].rename(
            columns={"last_emptied_display": "Last Emptied"}
        )),
        file_name="return_bin_recency.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — DAILY COVERAGE
# ─────────────────────────────────────────────────────────────────────────────

with tab2:
    st.subheader("Daily Coverage Heatmap")
    st.caption(
        "Each cell = whether a device had its return bin emptied on that day. "
        "Red = not touched, green = emptied at least once."
    )

    if df_bins.empty:
        st.info("No return bin events found in this date range.")
    else:
        df_bins["_date"] = df_bins["dt"].dt.date

        # Build a full device × date grid
        date_range = pd.date_range(start_date, end_date, freq="D").date
        device_list = sorted(all_devices)
        grid = pd.MultiIndex.from_product([device_list, date_range], names=["device", "_date"])
        grid_df = pd.DataFrame(index=grid).reset_index()

        daily_counts = (
            df_bins.groupby(["device", "_date"])
            .size()
            .reset_index(name="empties")
        )
        grid_df = grid_df.merge(daily_counts, on=["device", "_date"], how="left")
        grid_df["empties"] = grid_df["empties"].fillna(0).astype(int)
        grid_df["touched"] = (grid_df["empties"] > 0).astype(int)

        # Coverage % per day
        daily_cov = grid_df.groupby("_date").apply(
            lambda g: g["touched"].mean() * 100
        ).reset_index(name="coverage_pct")

        fig_cov = px.bar(
            daily_cov,
            x="_date", y="coverage_pct",
            labels={"_date": "", "coverage_pct": "% Devices Emptied"},
            title="Daily Return Bin Coverage (% of Devices)",
            color="coverage_pct",
            color_continuous_scale=["#ef4444", "#facc15", "#22c55e"],
            range_color=[0, 100],
            text=daily_cov["coverage_pct"].round(0).astype(int).astype(str) + "%",
        )
        fig_cov.update_traces(textposition="outside")
        fig_cov.update_layout(
            coloraxis_showscale=False,
            height=300,
            xaxis=dict(tickformat="%b %d", tickangle=-30),
        )
        st.plotly_chart(fig_cov, use_container_width=True)

        st.divider()

        # Heatmap — device × date
        pivot = grid_df.pivot(index="device", columns="_date", values="empties").fillna(0)
        pivot.columns = [str(c) for c in pivot.columns]

        fig_heat = px.imshow(
            pivot,
            color_continuous_scale=["#fef2f2", "#22c55e"],
            labels={"color": "# Empties"},
            aspect="auto",
            title="Return Bin Empties per Device per Day",
        )
        fig_heat.update_layout(
            height=max(300, len(device_list) * 22 + 80),
            xaxis=dict(tickangle=-45, tickfont=dict(size=10)),
            yaxis=dict(tickfont=dict(size=10)),
            coloraxis_showscale=True,
        )
        st.plotly_chart(fig_heat, use_container_width=True)

        # Devices never touched in window
        devices_in_window = set(df_bins["device"].unique())
        missing = sorted(all_devices - devices_in_window)
        if missing:
            st.warning(f"**{len(missing)} device(s) had zero return bin events in this window:**")
            st.write(", ".join(missing))

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — BY TECHNICIAN
# ─────────────────────────────────────────────────────────────────────────────

with tab3:
    st.subheader("Return Bin Activity by Technician")

    if df_bins.empty:
        st.info("No return bin events in this date range.")
    else:
        tech_summary = (
            df_bins.groupby("user_name")
            .agg(
                total_empties  = ("pk",      "count"),
                unique_devices = ("device",  "nunique"),
                first_empty    = ("dt",      "min"),
                last_empty     = ("dt",      "max"),
            )
            .reset_index()
            .sort_values("total_empties", ascending=False)
        )

        fig_tech = px.bar(
            tech_summary,
            x="total_empties", y="user_name",
            orientation="h",
            color="total_empties",
            color_continuous_scale="Greens",
            labels={"total_empties": "Return Bin Empties", "user_name": ""},
            title="Return Bin Empties by Technician",
            text="total_empties",
        )
        fig_tech.update_traces(textposition="outside")
        fig_tech.update_layout(
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False,
            height=max(300, len(tech_summary) * 32),
        )
        st.plotly_chart(fig_tech, use_container_width=True)

        st.divider()
        st.dataframe(
            tech_summary,
            use_container_width=True,
            hide_index=True,
            column_config={
                "user_name":      st.column_config.TextColumn("Technician"),
                "total_empties":  st.column_config.NumberColumn("Total Empties",   format="%d"),
                "unique_devices": st.column_config.NumberColumn("Devices Touched", format="%d"),
                "first_empty":    st.column_config.DatetimeColumn("First Empty",   format="MM/DD/YY HH:mm"),
                "last_empty":     st.column_config.DatetimeColumn("Last Empty",    format="MM/DD/YY HH:mm"),
            }
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — RAW EVENTS
# ─────────────────────────────────────────────────────────────────────────────

with tab4:
    st.subheader("Raw Return Bin Events")
    st.caption(f"{len(df_bins):,} events matching current date range.")

    if df_bins.empty:
        st.info("No return bin events in this date range.")
    else:
        with st.sidebar:
            st.divider()
            st.subheader("Raw Event Filters")
            dev_filter  = st.multiselect("Device",     options=sorted(df_bins["device"].unique()),    key="rb_dev_filter")
            user_filter = st.multiselect("Technician", options=sorted(df_bins["user_name"].unique()), key="rb_user_filter")

        raw_view = df_bins.copy()
        if dev_filter:  raw_view = raw_view[raw_view["device"].isin(dev_filter)]
        if user_filter: raw_view = raw_view[raw_view["user_name"].isin(user_filter)]

        st.dataframe(
            raw_view[["dt", "user_name", "device", "event_type", "med_desc", "qty"]].sort_values("dt", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "dt":         st.column_config.DatetimeColumn("Timestamp",  format="MM/DD/YY HH:mm"),
                "user_name":  st.column_config.TextColumn("Technician"),
                "device":     st.column_config.TextColumn("Device"),
                "event_type": st.column_config.TextColumn("Event Type"),
                "med_desc":   st.column_config.TextColumn("Medication"),
                "qty":        st.column_config.NumberColumn("Qty", format="%.0f"),
            }
        )
        st.download_button(
            "⬇️ Export to Excel",
            data=to_excel_bytes(raw_view[["dt", "user_name", "device", "event_type", "med_desc", "qty"]].sort_values("dt", ascending=False)),
            file_name="return_bin_events.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

with tab5:
    st.subheader("Patient Cassette Daily Coverage")
    st.caption("Each cell shows whether a cassette-eligible machine had a patient cassette transaction on that day.")

    cassette_recency = pd.DataFrame({"device": cassette_eligible_devices})
    cassette_recency = cassette_recency.merge(df_cassette_last, on="device", how="left")
    cassette_recency["days_since"] = cassette_recency["last_cassette"].apply(
        lambda x: (pd.Timestamp(today) - x).days if pd.notna(x) else None
    )
    cassette_recency["last_cassette_display"] = cassette_recency["last_cassette"].dt.strftime("%b %d, %Y %H:%M").fillna("-")

    date_range = pd.date_range(start_date, end_date, freq="D").date
    device_list = cassette_eligible_devices
    grid = pd.MultiIndex.from_product([device_list, date_range], names=["device", "_date"])
    cassette_grid = pd.DataFrame(index=grid).reset_index()

    if df_cassette.empty:
        cassette_grid["checks"] = 0
        st.info("No patient cassette transactions found in this date range.")
    else:
        df_cassette["_date"] = df_cassette["dt"].dt.date
        daily_counts = df_cassette.groupby(["device", "_date"]).size().reset_index(name="checks")
        cassette_grid = cassette_grid.merge(daily_counts, on=["device", "_date"], how="left")
        cassette_grid["checks"] = cassette_grid["checks"].fillna(0).astype(int)

    cassette_grid["checked"] = (cassette_grid["checks"] > 0).astype(int)
    daily_cov = cassette_grid.groupby("_date").apply(lambda g: g["checked"].mean() * 100).reset_index(name="coverage_pct")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cassette Events", f"{len(df_cassette):,}")
    c2.metric("Machines Checked", df_cassette["device"].nunique() if not df_cassette.empty else 0)
    c3.metric("Avg Daily Coverage", f"{daily_cov['coverage_pct'].mean():.1f}%" if not daily_cov.empty else "0.0%")
    c4.metric("Never Recorded", int(cassette_recency["days_since"].isna().sum()))

    if not daily_cov.empty:
        fig_cov = px.bar(
            daily_cov,
            x="_date",
            y="coverage_pct",
            labels={"_date": "", "coverage_pct": "% Machines Checked"},
            title="Daily Patient Cassette Coverage",
            color="coverage_pct",
            color_continuous_scale=["#ef4444", "#facc15", "#22c55e"],
            range_color=[0, 100],
            text=daily_cov["coverage_pct"].round(0).astype(int).astype(str) + "%",
        )
        fig_cov.update_traces(textposition="outside")
        fig_cov.update_layout(coloraxis_showscale=False, height=300, xaxis=dict(tickformat="%b %d", tickangle=-30))
        st.plotly_chart(fig_cov, use_container_width=True)

    pivot = cassette_grid.pivot(index="device", columns="_date", values="checks").fillna(0)
    pivot.columns = [str(c) for c in pivot.columns]
    if not pivot.empty:
        fig_heat = px.imshow(
            pivot,
            color_continuous_scale=["#fef2f2", "#22c55e"],
            labels={"color": "# Cassette Tx"},
            aspect="auto",
            title="Patient Cassette Transactions per Machine per Day",
        )
        fig_heat.update_layout(
            height=max(300, len(device_list) * 22 + 80),
            xaxis=dict(tickangle=-45, tickfont=dict(size=10)),
            yaxis=dict(tickfont=dict(size=10)),
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    st.dataframe(
        cassette_recency[["device", "last_cassette_display", "days_since"]].sort_values(
            ["days_since", "device"], ascending=[False, True], na_position="first"
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "device": st.column_config.TextColumn("Machine"),
            "last_cassette_display": st.column_config.TextColumn("Last Cassette Tx"),
            "days_since": st.column_config.NumberColumn("Days Since", format="%d"),
        },
    )
    if NON_CASSETTE_DEVICES:
        st.caption(f"Excluded from patient cassette coverage: {len(NON_CASSETTE_DEVICES)} machines configured as non-cassette areas.")

