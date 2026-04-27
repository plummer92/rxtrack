import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import io
from sqlalchemy import text
import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


INSULIN_PATTERN = (
    r"\b(insulin|regular insulin|insulin regular|lispro|aspart|glargine|detemir|degludec|glulisine|nph|"
    r"humalog|novolog|novolin|humulin|lantus|levemir|tresiba|toujeo|basaglar|"
    r"semglee|fiasp|apidra|admelog|afrezza|lyumjev|rezvoglar|relion)\b"
)
INHALER_PATTERN = (
    r"\b(inhaler|hfa|mdi|dpi|ellipta|respimat|diskus|flexhaler|twisthaler|"
    r"redihaler|aerosol|puff|actuat|albuterol|levalbuterol|ipratropium|"
    r"tiotropium|fluticasone|salmeterol|budesonide|formoterol|mometasone|"
    r"umeclidinium|vilanterol|beclomethasone|ciclesonide|breo|advair|"
    r"symbicort|spiriva|combivent|proair|ventolin|xopenex|dulera|trelegy|"
    r"anoro|qvar|asmanex|pulmicort)\b"
)
SPECIAL_MED_SECTION = "Insulins & Inhalers"
OTHER_MED_SECTION = "All Other Meds"


def classify_med_section(row: pd.Series) -> pd.Series:
    """Split high-touch insulin/inhaler meds from the rest for discrepancy review."""
    med_text = f"{row.get('med_desc', '')} {row.get('med_id', '')}".lower()
    is_insulin = bool(pd.Series([med_text]).str.contains(INSULIN_PATTERN, regex=True, na=False).iloc[0])
    is_inhaler = bool(pd.Series([med_text]).str.contains(INHALER_PATTERN, regex=True, na=False).iloc[0])

    if is_insulin and is_inhaler:
        med_group = "Insulin + Inhaler Match"
    elif is_insulin:
        med_group = "Insulin"
    elif is_inhaler:
        med_group = "Inhaler"
    else:
        med_group = "Other"

    return pd.Series({
        "med_section": SPECIAL_MED_SECTION if is_insulin or is_inhaler else OTHER_MED_SECTION,
        "med_group": med_group,
    })

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
    _debug_event("Discrepancy Deep Dive", "shared_intro_loaded")
    _debug_panel("Discrepancy Deep Dive", intro_mode="shared")
else:
    st.header("🚨 Discrepancy Deep Dive")
    st.caption("Count errors, dollar risk, and likely-cause attribution by device and medication.")
    _debug_event("Discrepancy Deep Dive", "fallback_header_used")
    _debug_panel("Discrepancy Deep Dive", intro_mode="fallback")

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

@st.cache_data(ttl=300)
def load_refill_transactions(start, end):
    """Load refill/load transactions for pre-refill verify-inventory auditing."""
    try:
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type, qty
            FROM events
            WHERE dt::date BETWEEN :start AND :end
              AND event_type ILIKE ANY (ARRAY['%restock%', '%refill%', '%load%', '%replenish%'])
              AND event_type NOT ILIKE '%cancel%'
              AND event_type NOT ILIKE '%unload%'
              AND event_type NOT ILIKE '%empty%'
            ORDER BY dt ASC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        return df
    except Exception as e:
        st.warning(f"[load_refill_transactions] {e}")
        return pd.DataFrame()


with st.spinner("Loading discrepancy data..."):
    df_disc  = load_discrepancies(start_date, end_date)
    df_prior = load_prior_transactions(start_date, end_date)
    df_refills = load_refill_transactions(start_date, end_date)

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

df_disc["verify_inventory_flag"] = df_disc["event_type"].astype(str).str.contains("verify", case=False, na=False)


def find_next_refill(disc_row, refill_df):
    if refill_df.empty:
        return pd.Series({"next_refill_dt": pd.NaT, "next_refill_by": "None", "minutes_to_refill": np.nan})
    mask = (
        (refill_df["med_id"] == disc_row["med_id"]) &
        (refill_df["device"] == disc_row["device"]) &
        (refill_df["dt"] >= disc_row["dt"]) &
        (refill_df["dt"] <= disc_row["dt"] + pd.Timedelta(hours=12))
    )
    candidates = refill_df[mask].sort_values("dt")
    if candidates.empty:
        return pd.Series({"next_refill_dt": pd.NaT, "next_refill_by": "None", "minutes_to_refill": np.nan})
    nxt = candidates.iloc[0]
    return pd.Series({
        "next_refill_dt": nxt["dt"],
        "next_refill_by": str(nxt["user_name"] or "Unknown"),
        "minutes_to_refill": (nxt["dt"] - disc_row["dt"]).total_seconds() / 60,
    })


if not df_refills.empty:
    df_disc = pd.concat([df_disc, df_disc.apply(lambda row: find_next_refill(row, df_refills), axis=1)], axis=1)
else:
    df_disc["next_refill_dt"] = pd.NaT
    df_disc["next_refill_by"] = "None"
    df_disc["minutes_to_refill"] = np.nan

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

# Medication section split
# Insulins and inhalers get their own review lane because their count units
# are more error-prone than typical tablet/capsule inventory.
df_disc[["med_section", "med_group"]] = df_disc.apply(classify_med_section, axis=1)
df_disc["med_category"] = df_disc["med_section"]  # Backward-compatible name for older chart code.

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
    verify_only = st.checkbox(
        "Verify Inventory discrepancies only",
        value=True,
        help="Defaulted on because this catches count-entry mismatches found when the next tech verifies inventory.",
        key="disc_verify_only"
    )
    med_section_filter = st.radio(
        "Medication Section",
        [OTHER_MED_SECTION, SPECIAL_MED_SECTION, "All"],
        index=0,
        help="Defaulted to All Other Meds so insulins and inhalers do not skew the tech count-entry signal.",
        key="disc_med_section_filter"
    )
    med_group_filter = st.multiselect(
        "Medication Type",
        sorted(df_disc["med_group"].dropna().unique()),
        placeholder="All types",
        key="disc_med_group_filter"
    )

filtered = df_disc.copy()
if dev_filter:    filtered = filtered[filtered["device"].isin(dev_filter)]
if med_filter:    filtered = filtered[filtered["med_desc"].isin(med_filter)]
if cause_filter:  filtered = filtered[filtered["likely_cause"].isin(cause_filter)]
if flagged_only:  filtered = filtered[filtered["flags"] != ""]
if verify_only:   filtered = filtered[filtered["verify_inventory_flag"]]
if med_section_filter != "All": filtered = filtered[filtered["med_section"] == med_section_filter]
if med_group_filter: filtered = filtered[filtered["med_group"].isin(med_group_filter)]

likely_cause_audit = df_disc.copy()
if dev_filter:    likely_cause_audit = likely_cause_audit[likely_cause_audit["device"].isin(dev_filter)]
if med_filter:    likely_cause_audit = likely_cause_audit[likely_cause_audit["med_desc"].isin(med_filter)]
if cause_filter:  likely_cause_audit = likely_cause_audit[likely_cause_audit["likely_cause"].isin(cause_filter)]
if flagged_only:  likely_cause_audit = likely_cause_audit[likely_cause_audit["flags"] != ""]
if med_group_filter: likely_cause_audit = likely_cause_audit[likely_cause_audit["med_group"].isin(med_group_filter)]
likely_cause_audit = likely_cause_audit[
    (likely_cause_audit["verify_inventory_flag"]) &
    (likely_cause_audit["med_section"] == OTHER_MED_SECTION)
]

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 5 — EXECUTIVE METRICS
# ═══════════════════════════════════════════════════════════════════════════════

total_disc   = len(filtered)
total_risk   = filtered["dollar_risk"].sum()
avg_risk     = filtered["dollar_risk"].mean()
unique_meds  = filtered["med_id"].nunique()
unique_devs  = filtered["device"].nunique()
verify_disc  = int(filtered["verify_inventory_flag"].sum()) if "verify_inventory_flag" in filtered else 0

m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
m1.metric("Total Discrepancies",  f"{total_disc:,}")
m2.metric("Total Dollar Risk",    f"${total_risk:,.2f}")
m3.metric("Avg Risk per Event",   f"${avg_risk:,.2f}")
m4.metric("Medications Affected", unique_meds)
m5.metric("Devices Affected",     unique_devs)
m6.metric("⚠️ Flagged Events",    flagged_ct)
m7.metric("Verify Inventory",     f"{verify_disc:,}")

st.divider()

# ── Inhaler / Insulin vs All Other Meds split ────────────────────────────────
st.subheader("Insulins & Inhalers vs All Other Meds")
st.caption("Insulins and inhalers are isolated into their own section so their unit-of-measure noise does not hide the rest of the medication discrepancy picture.")

inh_df  = filtered[filtered["med_section"] == SPECIAL_MED_SECTION]
other_df = filtered[filtered["med_section"] == OTHER_MED_SECTION]

sp1, sp2, sp3, sp4, sp5, sp6 = st.columns(6)
sp1.metric("Insulin/Inhaler Count",    f"{len(inh_df):,}")
sp2.metric("Insulin/Inhaler Risk",     f"${inh_df['dollar_risk'].sum():,.2f}")
sp3.metric("Insulin/Inhaler % Count",
           f"{len(inh_df)/len(filtered)*100:.1f}%" if len(filtered) > 0 else "0%")
sp4.metric("Other Meds Count",         f"{len(other_df):,}")
sp5.metric("Other Meds Risk",          f"${other_df['dollar_risk'].sum():,.2f}")
sp6.metric("Other Meds % Count",
           f"{len(other_df)/len(filtered)*100:.1f}%" if len(filtered) > 0 else "0%")

# Side-by-side donut: count split and dollar risk split
if len(filtered) > 0:
    sc1, sc2 = st.columns(2)
    with sc1:
        cat_counts = filtered["med_section"].value_counts().reset_index()
        cat_counts.columns = ["category", "count"]
        fig_cat_ct = px.pie(
            cat_counts, names="category", values="count",
            hole=0.5,
            title="Share of Discrepancy Count",
            color="category",
            color_discrete_map={
                SPECIAL_MED_SECTION: "#f97316",
                OTHER_MED_SECTION: "#3b82f6"
            }
        )
        fig_cat_ct.update_layout(height=300, margin=dict(t=40, b=0))
        st.plotly_chart(fig_cat_ct, use_container_width=True)
    with sc2:
        cat_risk = filtered.groupby("med_section")["dollar_risk"].sum().reset_index()
        cat_risk.columns = ["category", "dollar_risk"]
        fig_cat_risk = px.pie(
            cat_risk, names="category", values="dollar_risk",
            hole=0.5,
            title="Share of Dollar Risk",
            color="category",
            color_discrete_map={
                SPECIAL_MED_SECTION: "#f97316",
                OTHER_MED_SECTION: "#3b82f6"
            }
        )
        fig_cat_risk.update_layout(height=300, margin=dict(t=40, b=0))
        st.plotly_chart(fig_cat_risk, use_container_width=True)

section_tabs = st.tabs([SPECIAL_MED_SECTION, OTHER_MED_SECTION])
section_columns = [
    "med_group", "med_id", "med_desc", "disc_count", "total_risk",
    "avg_risk", "unique_devs", "flagged"
]
for section_tab, section_name, section_df in zip(
    section_tabs,
    [SPECIAL_MED_SECTION, OTHER_MED_SECTION],
    [inh_df, other_df],
):
    with section_tab:
        if section_df.empty:
            st.info(f"No {section_name.lower()} discrepancies match the current filters.")
            continue

        section_summary = (
            section_df.groupby(["med_group", "med_id", "med_desc"])
            .agg(
                disc_count=("pk", "count"),
                total_risk=("dollar_risk", "sum"),
                avg_risk=("dollar_risk", "mean"),
                unique_devs=("device", "nunique"),
                flagged=("flags", lambda x: (x != "").sum()),
            )
            .reset_index()
            .sort_values(["disc_count", "total_risk"], ascending=False)
        )

        st.dataframe(
            section_summary[section_columns],
            use_container_width=True,
            column_config={
                "med_group": st.column_config.TextColumn("Type"),
                "disc_count": st.column_config.NumberColumn("Count", format="%d"),
                "total_risk": st.column_config.NumberColumn("Total Risk", format="$%.2f"),
                "avg_risk": st.column_config.NumberColumn("Avg Risk", format="$%.2f"),
                "unique_devs": st.column_config.NumberColumn("Devices", format="%d"),
                "flagged": st.column_config.NumberColumn("Flagged", format="%d"),
            },
            hide_index=True,
        )
        st.download_button(
            f"Export {section_name} Summary to Excel",
            data=to_excel_bytes(section_summary),
            file_name=f"discrepancy_{section_name.lower().replace(' ', '_').replace('&', 'and')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"export_{section_name.lower().replace(' ', '_').replace('&', 'and')}",
        )

st.caption("Use the **Medication Section** filter in the sidebar to drill into one section across all tabs.")

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 6 — TABS
# ═══════════════════════════════════════════════════════════════════════════════

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🖥️ By Device",
    "💊 By Medication",
    "👤 Likely Cause",
    "🔍 Raw Detail",
    "Verify Inventory",
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
        filtered.groupby(["med_section", "med_group", "med_id", "med_desc"])
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
            "med_section": st.column_config.TextColumn("Section"),
            "med_group":   st.column_config.TextColumn("Type"),
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
    st.subheader("Likely Cause: Count Entry Mismatches")
    st.caption(
        "Focused on Verify Inventory discrepancies for All Other Meds by default. "
        "The likely cause is the user who last completed a clean transaction on the same med/device "
        "before another tech found the count did not match what Pyxis expected."
    )

    if med_section_filter != OTHER_MED_SECTION:
        st.info(
            "This tab intentionally ignores the Medication Section sidebar choice and audits Verify Inventory "
            "events for All Other Meds only. Insulins and inhalers stay out of this likely-cause view."
        )

    cause_summary = (
        likely_cause_audit.groupby("likely_cause")
        .agg(
            disc_count  = ("pk",          "count"),
            total_risk  = ("dollar_risk", "sum"),
            verify_count = ("verify_inventory_flag", "sum"),
            avg_risk    = ("dollar_risk", "mean"),
            unique_meds = ("med_id",      "nunique"),
            unique_devs = ("device",      "nunique"),
        )
        .reset_index()
        .sort_values(["disc_count", "verify_count", "total_risk"], ascending=False)
    )
    cause_summary["verify_count"] = cause_summary["verify_count"].astype(int)

    # Exclude Unknown from charts but keep in table
    known = cause_summary[cause_summary["likely_cause"] != "Unknown"]

    known_disc_count = int(known["disc_count"].sum()) if not known.empty else 0
    top_likely_cause = known.iloc[0]["likely_cause"] if not known.empty else "None"
    lc1, lc2, lc3, lc4 = st.columns(4)
    lc1.metric("Known Likely-Cause Events", f"{known_disc_count:,}")
    lc2.metric("Unknown Events", f"{int((likely_cause_audit['likely_cause'] == 'Unknown').sum()):,}")
    lc3.metric("Top Likely Cause", top_likely_cause)
    lc4.metric("Verify Inventory Events", f"{len(likely_cause_audit):,}")

    col_a, col_b = st.columns(2)

    with col_a:
        if not known.empty:
            fig_cause_ct = px.bar(
                known.head(12),
                x="disc_count", y="likely_cause",
                orientation="h",
                color="disc_count",
                color_continuous_scale="Reds",
                title="Verify Count Mismatches by Likely Cause",
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

    unknown_ct = int((likely_cause_audit["likely_cause"] == "Unknown").sum())
    if unknown_ct > 0:
        st.info(
            f"ℹ️ {unknown_ct} discrepancies ({unknown_ct/len(likely_cause_audit)*100:.1f}%) have no prior "
            f"transaction on record — likely the first ever transaction on that med+device, "
            f"or data predates your earliest upload."
        )

    st.divider()

    # Drill-down: pick a user and see their events
    st.subheader("User Drill-Down")
    known_users = sorted(likely_cause_audit[
        (likely_cause_audit["likely_cause"] != "Unknown") &
        (likely_cause_audit["likely_cause"].notna())
    ]["likely_cause"].astype(str).unique())
    if known_users:
        sel_user = st.selectbox(
            "Select likely cause user to review their events",
            known_users,
            key="disc_drilldown_user"
        )
        user_events = likely_cause_audit[likely_cause_audit["likely_cause"] == sel_user][[
            "med_section", "med_group", "dt", "device", "med_desc", "discrepancy_qty",
            "discrepancy_reason", "dollar_risk", "flags"
        ]].sort_values("dt", ascending=False)

        st.dataframe(
            user_events,
            use_container_width=True,
            column_config={
                "med_section":       st.column_config.TextColumn("Med Section"),
                "med_group":         st.column_config.TextColumn("Med Type"),
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
            "verify_count": st.column_config.NumberColumn("Verify Count", format="%d"),
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
            "flags", "med_section", "med_group", "dt", "device", "med_desc",
            "discrepancy_qty", "discrepancy_reason",
            "cost_per_unit", "dollar_risk",
            "user_name", "likely_cause"
        ]],
        use_container_width=True,
        column_config={
            "flags":             st.column_config.TextColumn("⚠️ Flags"),
            "med_section":       st.column_config.TextColumn("Med Section"),
            "med_group":         st.column_config.TextColumn("Med Type"),
            "dt":                st.column_config.DatetimeColumn("Date/Time",      format="MM/DD/YY HH:mm"),
            "discrepancy_qty":   st.column_config.NumberColumn("Disc Qty",         format="%.0f"),
            "cost_per_unit":     st.column_config.NumberColumn("Unit Cost",        format="$%.2f"),
            "dollar_risk":       st.column_config.NumberColumn("Dollar Risk",      format="$%.2f"),
            "user_name":         st.column_config.TextColumn("Found By"),
            "likely_cause":      st.column_config.TextColumn("Likely Cause"),
        },
        hide_index=True
    )


with tab5:
    st.subheader("Verify Inventory Before Refill")
    st.caption("Discrepancies where the transaction type looks like Verify Inventory, tied to the next refill/load on the same med and device within 12 hours.")

    verify_df = filtered[filtered["verify_inventory_flag"]].copy()
    if verify_df.empty:
        st.info("No Verify Inventory discrepancies found in the selected filters.")
    else:
        linked = int(verify_df["next_refill_dt"].notna().sum())
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Verify Discrepancies", f"{len(verify_df):,}")
        v2.metric("Linked to Refill", f"{linked:,}")
        v3.metric("Unlinked", f"{len(verify_df) - linked:,}")
        v4.metric(
            "Median Minutes to Refill",
            f"{verify_df['minutes_to_refill'].dropna().median():.0f}" if verify_df["minutes_to_refill"].notna().any() else "n/a"
        )

        device_verify = (
            verify_df.groupby("device")
            .agg(
                verify_discrepancies=("pk", "count"),
                linked_refills=("next_refill_dt", lambda x: x.notna().sum()),
                total_risk=("dollar_risk", "sum"),
                median_minutes_to_refill=("minutes_to_refill", "median"),
            )
            .reset_index()
            .sort_values(["verify_discrepancies", "total_risk"], ascending=False)
        )

        fig_verify = px.bar(
            device_verify.head(20),
            x="verify_discrepancies",
            y="device",
            orientation="h",
            color="total_risk",
            color_continuous_scale="Reds",
            title="Verify Inventory Discrepancies by Device",
            text="verify_discrepancies",
        )
        fig_verify.update_layout(yaxis={"categoryorder": "total ascending"}, coloraxis_showscale=False, height=420)
        st.plotly_chart(fig_verify, use_container_width=True)

        st.dataframe(
            verify_df[[
                "med_section", "med_group", "dt", "device", "med_desc", "discrepancy_qty", "dollar_risk",
                "user_name", "likely_cause", "next_refill_dt", "next_refill_by",
                "minutes_to_refill", "discrepancy_reason"
            ]].sort_values(["device", "dt"]),
            use_container_width=True,
            hide_index=True,
            column_config={
                "med_section": st.column_config.TextColumn("Med Section"),
                "med_group": st.column_config.TextColumn("Med Type"),
                "dt": st.column_config.DatetimeColumn("Verify Time", format="MM/DD/YY HH:mm"),
                "next_refill_dt": st.column_config.DatetimeColumn("Next Refill", format="MM/DD/YY HH:mm"),
                "discrepancy_qty": st.column_config.NumberColumn("Disc Qty", format="%.0f"),
                "dollar_risk": st.column_config.NumberColumn("Dollar Risk", format="$%.2f"),
                "minutes_to_refill": st.column_config.NumberColumn("Minutes to Refill", format="%.0f"),
            },
        )

