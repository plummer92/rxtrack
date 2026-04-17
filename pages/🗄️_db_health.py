import streamlit as st
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

st.set_page_config(
    page_title="DB Health",
    page_icon="🗄️",
    layout="wide"
)
if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.render_sidebar()

engine = App.engine

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Database Health",
        "Review row counts, date coverage, upload gaps, and quality signals across every table from the updated control shell.",
        kicker="Tools",
    )
    _debug_event("DB Health", "shared_intro_loaded")
    _debug_panel("DB Health", intro_mode="shared")
else:
    st.header("🗄️ Database Health")
    st.caption("Review row counts, date coverage, upload gaps, and quality signals across every table.")
    _debug_event("DB Health", "fallback_header_used")
    _debug_panel("DB Health", intro_mode="fallback")

today = date.today()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — TABLE HEALTH QUERIES
# One query per table. Each returns counts, min/max date, last upload.
# Fails gracefully — missing or empty tables show as red, not crashes.
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=120)
def get_table_health():
    """
    Returns a list of dicts — one per monitored table — with:
      table, label, row_count, min_date, max_date, days_since_last,
      date_col, status
    """
    tables = [
        # (table_name,          friendly_label,                 date_col,    upload_type)
        ("events",              "Pyxis Events",                 "dt",        "Daily Transaction Report"),
        ("config_events",       "Pends (Device Activity)",      "dt",        "Device Activity Log"),
        ("pharmacy_orders",     "Pharmacy Orders",              "dt",        "Pharmacy Workflow Report"),
        ("staff_schedule",      "Staff Schedule",               "dt",        "Staff Schedule"),
        ("attendance_punches",  "Attendance Punches",           "dt_date",   "Attendance Tracking"),
        ("med_costs",           "Med Cost Prices",              None,        "Inventory Audit (Prices)"),
        ("carousel_master_mapping","Carousel Master Mapping",   None,        "Admin Upload"),
        ("cycle_count_variances","Cycle Count Variances",       "dt",        "Cycle Count Variance Report"),
        ("inventory_detailed",  "Detailed Inventory",           None,        "Inventory Audit (Detailed RC)"),
    ]

    results = []
    with engine.connect() as conn:
        for table, label, date_col, upload_type in tables:
            row = {
                "table":       table,
                "label":       label,
                "upload_type": upload_type,
                "date_col":    date_col,
                "row_count":   0,
                "min_date":    None,
                "max_date":    None,
                "days_since_last": None,
                "status":      "⚪ Empty",
            }
            try:
                # Row count
                r = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                row["row_count"] = r.scalar() or 0

                if row["row_count"] == 0:
                    row["status"] = "🔴 Empty"
                    results.append(row)
                    continue

                # Date range (only for tables with a date column)
                if date_col:
                    r2 = conn.execute(text(
                        f"SELECT MIN({date_col})::date, MAX({date_col})::date FROM {table}"
                    ))
                    mn, mx = r2.fetchone()
                    row["min_date"] = mn
                    row["max_date"] = mx
                    if mx:
                        row["days_since_last"] = (today - mx).days

                # Status
                if date_col and row["days_since_last"] is not None:
                    if row["days_since_last"] <= 1:
                        row["status"] = "🟢 Current"
                    elif row["days_since_last"] <= 7:
                        row["status"] = "🟡 Aging"
                    else:
                        row["status"] = "🔴 Stale"
                else:
                    # Static tables (no date col) — just check they have data
                    row["status"] = "🟢 Loaded" if row["row_count"] > 0 else "🔴 Empty"

            except Exception as e:
                row["status"] = "⚠️ Error"
                row["error"]  = str(e)

            results.append(row)

    return results


@st.cache_data(ttl=120)
def get_daily_coverage(table, date_col, lookback_days=90):
    """
    Returns a set of dates that have at least one row in the given table.
    Used to render the gap calendar.
    """
    try:
        sql = text(f"""
            SELECT DISTINCT {date_col}::date AS d
            FROM {table}
            WHERE {date_col}::date >= CURRENT_DATE - INTERVAL '{lookback_days} days'
        """)
        with engine.connect() as conn:
            result = conn.execute(sql)
            return set(row[0] for row in result)
    except Exception:
        return set()


@st.cache_data(ttl=120)
def get_cost_coverage():
    """
    How many distinct med_ids in events have a matching cost in med_costs.
    """
    try:
        sql = text("""
            SELECT
                COUNT(DISTINCT e.med_id)                                      AS total_meds,
                COUNT(DISTINCT c.med_id)                                      AS costed_meds,
                COUNT(DISTINCT e.med_id) - COUNT(DISTINCT c.med_id)           AS missing_cost
            FROM events e
            LEFT JOIN med_costs c ON e.med_id = c.med_id
        """)
        with engine.connect() as conn:
            r = conn.execute(sql)
            row = r.fetchone()
            return {"total": row[0], "costed": row[1], "missing": row[2]}
    except Exception:
        return {"total": 0, "costed": 0, "missing": 0}


@st.cache_data(ttl=120)
def get_events_gap_days(lookback_days=90):
    """Days in last 90 days with zero Pyxis event records."""
    present = get_daily_coverage("events", "dt", lookback_days)
    all_days = {today - timedelta(days=i) for i in range(lookback_days)}
    return sorted(all_days - present, reverse=True)


# ── Execute all queries ──────────────────────────────────────────────────────

health      = get_table_health()
cost_cov    = get_cost_coverage()
gap_days    = get_events_gap_days(90)
health_df   = pd.DataFrame(health)

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — TOP SUMMARY METRICS
# ═══════════════════════════════════════════════════════════════════════════════

green  = sum(1 for r in health if r["status"] in ("🟢 Current", "🟢 Loaded"))
yellow = sum(1 for r in health if r["status"] == "🟡 Aging")
red    = sum(1 for r in health if r["status"] in ("🔴 Stale", "🔴 Empty"))
error  = sum(1 for r in health if r["status"] == "⚠️ Error")

cost_pct = (cost_cov["costed"] / cost_cov["total"] * 100) if cost_cov["total"] > 0 else 0
gap_ct   = len([d for d in gap_days if d <= today and d >= today - timedelta(days=30)])

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("🟢 Current / Loaded",  green)
m2.metric("🟡 Aging (2–7 days)",  yellow)
m3.metric("🔴 Stale / Empty",     red + error)
m4.metric("Cost Coverage",        f"{cost_pct:.1f}%",
          delta=f"{cost_cov['missing']} meds missing",
          delta_color="inverse" if cost_cov["missing"] > 0 else "normal")
m5.metric("Event Gaps (30d)",     gap_ct,
          delta="days with no data" if gap_ct > 0 else "✅ No gaps",
          delta_color="inverse" if gap_ct > 0 else "normal")
m6.metric("Tables Monitored",     len(health))

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — TABLE STATUS CARDS
# ═══════════════════════════════════════════════════════════════════════════════

st.subheader("📋 Table Status")

# Status colour map for the dataframe
status_order = {"🔴 Stale": 0, "🔴 Empty": 1, "⚠️ Error": 2,
                "🟡 Aging": 3, "🟢 Current": 4, "🟢 Loaded": 5, "⚪ Empty": 6}

display_df = health_df.copy()
display_df["sort_key"]  = display_df["status"].map(status_order).fillna(9)
display_df = display_df.sort_values("sort_key").drop(columns=["sort_key", "table",
                                                               "date_col", "error"],
                                                      errors="ignore")
display_df["days_since_last"] = display_df["days_since_last"].where(
    display_df["days_since_last"].notna(), other=None
)

st.dataframe(
    display_df.rename(columns={
        "label":           "Table",
        "upload_type":     "Uploaded Via",
        "row_count":       "Row Count",
        "min_date":        "Oldest Record",
        "max_date":        "Most Recent Record",
        "days_since_last": "Days Since Last Upload",
        "status":          "Status",
    }),
    use_container_width=True,
    column_config={
        "Row Count":             st.column_config.NumberColumn(format="%d"),
        "Days Since Last Upload":st.column_config.NumberColumn(format="%d"),
        "Oldest Record":         st.column_config.DateColumn(format="MM/DD/YY"),
        "Most Recent Record":    st.column_config.DateColumn(format="MM/DD/YY"),
    },
    hide_index=True
)

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — GAP CALENDAR (Pyxis Events — last 90 days)
# ═══════════════════════════════════════════════════════════════════════════════

st.subheader("📅 Pyxis Events — Upload Gap Calendar (Last 90 Days)")
st.caption("Each cell = one day. 🟢 = data present  🔴 = no records found")

present_days = get_daily_coverage("events", "dt", 90)

# Build calendar grid — group by week
cal_start = today - timedelta(days=89)
all_days  = [cal_start + timedelta(days=i) for i in range(90)]

# Group into rows of 7 (weeks)
weeks = [all_days[i:i+7] for i in range(0, len(all_days), 7)]

# Render as an HTML table for compact display
html = """
<style>
.cal-table { border-collapse: collapse; font-size: 11px; font-family: monospace; }
.cal-table td { width: 90px; padding: 3px 6px; border: 1px solid #2a2a2a; text-align: center; border-radius: 4px; }
.cal-present { background: #166534; color: #bbf7d0; }
.cal-missing { background: #7f1d1d; color: #fecaca; }
.cal-future  { background: #1e1e1e; color: #555; }
</style>
<table class="cal-table"><tr>
<th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th><th>Sun</th>
</tr>
"""

for week in weeks:
    html += "<tr>"
    # Pad short weeks
    for d in week:
        if d > today:
            html += f'<td class="cal-future">{d.strftime("%m/%d")}</td>'
        elif d in present_days:
            html += f'<td class="cal-present">✅ {d.strftime("%m/%d")}</td>'
        else:
            html += f'<td class="cal-missing">❌ {d.strftime("%m/%d")}</td>'
    html += "</tr>"
html += "</table>"

st.markdown(html, unsafe_allow_html=True)

# Gap list
if gap_days:
    with st.expander(f"🔴 {len(gap_days)} missing day(s) in last 90 days — click to see list"):
        gap_df = pd.DataFrame({
            "Date":    [d.strftime("%A, %B %d %Y") for d in gap_days[:30]],
            "Days Ago":[( today - d).days for d in gap_days[:30]],
        })
        st.dataframe(gap_df, use_container_width=True, hide_index=True)
else:
    st.success("✅ No gaps — every day in the last 90 days has Pyxis event data.")

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 5 — COST COVERAGE DETAIL
# ═══════════════════════════════════════════════════════════════════════════════

st.subheader("💲 Med Cost Coverage")
st.caption(
    "Medications in your events table with no matching price in med_costs. "
    "These contribute $0 to any dollar-value calculation."
)

cc1, cc2, cc3 = st.columns(3)
cc1.metric("Total Distinct Meds (Events)", f"{cost_cov['total']:,}")
cc2.metric("Meds With Cost",               f"{cost_cov['costed']:,}")
cc3.metric("Meds Missing Cost",            f"{cost_cov['missing']:,}",
           delta_color="inverse" if cost_cov["missing"] > 0 else "normal")

if cost_cov["missing"] > 0:
    @st.cache_data(ttl=120)
    def get_missing_cost_meds():
        sql = text("""
            SELECT e.med_id, e.med_desc, COUNT(*) AS event_count
            FROM events e
            LEFT JOIN med_costs c ON e.med_id = c.med_id
            WHERE c.med_id IS NULL
            GROUP BY e.med_id, e.med_desc
            ORDER BY event_count DESC
        """)
        with engine.connect() as conn:
            return pd.read_sql(sql, conn)

    missing_df = get_missing_cost_meds()
    with st.expander(f"View {len(missing_df)} meds missing a cost price"):
        st.dataframe(
            missing_df,
            use_container_width=True,
            column_config={
                "event_count": st.column_config.NumberColumn("Event Count", format="%d"),
            },
            hide_index=True
        )
        st.caption("Fix by uploading an Inventory Audit (Prices) file via the main upload page.")
else:
    st.success("✅ All medications have a cost price loaded.")

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 6 — PER-TABLE COVERAGE CALENDARS (collapsible)
# ═══════════════════════════════════════════════════════════════════════════════

st.subheader("📆 Coverage Detail by Table")
st.caption("Expand any table to see its own 90-day upload calendar.")

date_tables = [
    ("pharmacy_orders",  "dt",       "Pharmacy Orders"),
    ("config_events",    "dt",       "Pends (Device Activity)"),
    ("attendance_punches","dt_date", "Attendance Punches"),
]

for table, date_col, label in date_tables:
    row_info = next((r for r in health if r["table"] == table), {})
    row_count = row_info.get("row_count", 0)

    with st.expander(f"{label} — {row_count:,} rows"):
        if row_count == 0:
            st.warning("No data in this table yet.")
            continue

        present = get_daily_coverage(table, date_col, 90)
        gap_ct_local = sum(
            1 for i in range(90)
            if (today - timedelta(days=i)) not in present
            and (today - timedelta(days=i)) >= (today - timedelta(days=89))
        )

        if gap_ct_local == 0:
            st.success(f"✅ No gaps in last 90 days — {len(present)} days with data.")
        else:
            st.warning(f"⚠️ {gap_ct_local} day(s) with no data in the last 90 days.")

        # Mini calendar — just 4 weeks
        mini_days = [today - timedelta(days=i) for i in range(27, -1, -1)]
        mini_html = """
        <style>.mini-cal td { width:80px; padding:2px 5px; border:1px solid #2a2a2a;
        font-size:10px; font-family:monospace; text-align:center; border-radius:3px; }</style>
        <table class="mini-cal"><tr>
        <th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th><th>Sun</th>
        </tr>
        """
        weeks_mini = [mini_days[i:i+7] for i in range(0, 28, 7)]
        for week in weeks_mini:
            mini_html += "<tr>"
            for d in week:
                if d in present:
                    mini_html += f'<td class="cal-present">✅ {d.strftime("%m/%d")}</td>'
                else:
                    mini_html += f'<td class="cal-missing">❌ {d.strftime("%m/%d")}</td>'
            mini_html += "</tr>"
        mini_html += "</table>"
        st.markdown(mini_html, unsafe_allow_html=True)

