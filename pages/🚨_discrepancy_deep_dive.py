import io
from datetime import timedelta

import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import text

import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)

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
REFILL_EVENT_PATTERN = "ARRAY['%restock%', '%refill%', '%load%', '%replenish%']"


st.set_page_config(
    page_title="Verify Count Audit",
    page_icon="!",
    layout="wide",
)

engine = App.engine
render_sidebar = App.render_sidebar
start_date, end_date = render_sidebar()


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


def classify_med_section(row: pd.Series) -> pd.Series:
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


@st.cache_data(ttl=300)
def load_verify_discrepancies(start, end):
    """Verify Inventory events where Pyxis automatically recorded a count discrepancy."""
    try:
        sql = text("""
            SELECT e.pk, e.dt, e.user_name, e.device, e.med_id, e.med_desc,
                   e.event_type, e.qty, e.beginning_qty, e.ending_qty,
                   e.discrepancy_qty, e.discrepancy_reason,
                   COALESCE(c.cost_per_unit, 0) AS cost_per_unit
            FROM events e
            LEFT JOIN med_costs c ON e.med_id = c.med_id
            WHERE e.dt::date BETWEEN :start AND :end
              AND e.event_type ILIKE '%verify%'
              AND e.discrepancy_qty IS NOT NULL
              AND e.discrepancy_qty <> 0
            ORDER BY e.dt DESC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        return normalize_event_numbers(df)
    except Exception as exc:
        st.error(f"[load_verify_discrepancies] {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_prior_refills(start, end, lookback_days=60):
    """Prior refill/load events that could explain the later verify mismatch."""
    try:
        lookback = start - timedelta(days=lookback_days)
        sql = text(f"""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type,
                   qty, beginning_qty, ending_qty
            FROM events
            WHERE dt::date BETWEEN :lookback AND :end
              AND event_type ILIKE ANY ({REFILL_EVENT_PATTERN})
              AND event_type NOT ILIKE '%cancel%'
              AND event_type NOT ILIKE '%unload%'
              AND event_type NOT ILIKE '%empty%'
            ORDER BY dt ASC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"lookback": lookback, "end": end})
        return normalize_event_numbers(df)
    except Exception as exc:
        st.warning(f"[load_prior_refills] {exc}")
        return pd.DataFrame()


def normalize_event_numbers(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    for col in ["qty", "beginning_qty", "ending_qty", "discrepancy_qty", "cost_per_unit"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "discrepancy_qty" in df.columns:
        df["abs_discrepancy_qty"] = df["discrepancy_qty"].abs()
    if {"discrepancy_qty", "cost_per_unit"}.issubset(df.columns):
        df["dollar_risk"] = df["discrepancy_qty"].abs() * df["cost_per_unit"].fillna(0)
    return df


def find_prior_refill(verify_row: pd.Series, refills: pd.DataFrame) -> pd.Series:
    if refills.empty:
        return empty_prior_refill()
    matches = refills[
        (refills["med_id"] == verify_row["med_id"]) &
        (refills["device"] == verify_row["device"]) &
        (refills["dt"] < verify_row["dt"])
    ].sort_values("dt")
    if matches.empty:
        return empty_prior_refill()

    prior = matches.iloc[-1]
    hours_since = (verify_row["dt"] - prior["dt"]).total_seconds() / 3600
    return pd.Series({
        "prior_refill_dt": prior["dt"],
        "prior_refill_by": str(prior.get("user_name") or "Unknown"),
        "prior_refill_qty": prior.get("qty", np.nan),
        "prior_refill_beginning_qty": prior.get("beginning_qty", np.nan),
        "prior_refill_ending_qty": prior.get("ending_qty", np.nan),
        "prior_refill_event_type": prior.get("event_type", ""),
        "hours_since_refill": hours_since,
    })


def empty_prior_refill() -> pd.Series:
    return pd.Series({
        "prior_refill_dt": pd.NaT,
        "prior_refill_by": "No prior refill found",
        "prior_refill_qty": np.nan,
        "prior_refill_beginning_qty": np.nan,
        "prior_refill_ending_qty": np.nan,
        "prior_refill_event_type": "",
        "hours_since_refill": np.nan,
    })


if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Verify Count Audit",
        "Find Verify Inventory mismatches and the last refill/load user for that same med and device, all on one coaching row.",
        kicker="Discrepancy Review",
    )
    _debug_event("Discrepancy Deep Dive", "verify_count_audit_loaded")
    _debug_panel("Discrepancy Deep Dive", intro_mode="shared")
else:
    st.header("Verify Count Audit")
    st.caption("Verify Inventory mismatch plus the most recent prior refill/load for the same med and device.")
    _debug_event("Discrepancy Deep Dive", "verify_count_audit_fallback_header")
    _debug_panel("Discrepancy Deep Dive", intro_mode="fallback")

with st.spinner("Building verify count audit..."):
    df_verify = load_verify_discrepancies(start_date, end_date)
    df_refills = load_prior_refills(start_date, end_date)

if df_verify.empty:
    st.success("No Verify Inventory discrepancies found in the selected date range.")
    st.stop()

df_verify[["med_section", "med_group"]] = df_verify.apply(classify_med_section, axis=1)
prior_cols = df_verify.apply(lambda row: find_prior_refill(row, df_refills), axis=1)
audit_df = pd.concat([df_verify, prior_cols], axis=1)
audit_df["verify_date"] = audit_df["dt"].dt.date
audit_df["prior_refill_date"] = audit_df["prior_refill_dt"].dt.date
audit_df["has_prior_refill"] = audit_df["prior_refill_dt"].notna()

with st.sidebar:
    st.divider()
    st.subheader("Audit Filters")
    include_special_meds = st.checkbox(
        "Include insulins and inhalers",
        value=False,
        help="Keep this off for the coaching audit unless you specifically want those special workflows included.",
        key="verify_audit_include_special_meds",
    )
    device_filter = st.multiselect(
        "Device",
        sorted(audit_df["device"].dropna().astype(str).unique()),
        placeholder="All devices",
        key="verify_audit_device_filter",
    )
    med_filter = st.multiselect(
        "Medication",
        sorted(audit_df["med_desc"].dropna().astype(str).unique()),
        placeholder="All meds",
        key="verify_audit_med_filter",
    )
    refill_user_filter = st.multiselect(
        "Prior Refill User",
        sorted(audit_df["prior_refill_by"].dropna().astype(str).unique()),
        placeholder="All refill users",
        key="verify_audit_refill_user_filter",
    )
    only_matched = st.checkbox(
        "Only rows with a prior refill/load found",
        value=False,
        key="verify_audit_only_matched",
    )

filtered = audit_df.copy()
if not include_special_meds:
    filtered = filtered[filtered["med_section"] == OTHER_MED_SECTION]
if device_filter:
    filtered = filtered[filtered["device"].astype(str).isin(device_filter)]
if med_filter:
    filtered = filtered[filtered["med_desc"].astype(str).isin(med_filter)]
if refill_user_filter:
    filtered = filtered[filtered["prior_refill_by"].astype(str).isin(refill_user_filter)]
if only_matched:
    filtered = filtered[filtered["has_prior_refill"]]

st.info(
    "Pocket-level matching is approximated as same medication plus same Pyxis device because the imported events table does not store drawer/subdrawer/pocket."
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Verify Mismatches", f"{len(filtered):,}")
m2.metric("Matched to Prior Refill", f"{int(filtered['has_prior_refill'].sum()):,}")
m3.metric("Unmatched", f"{int((~filtered['has_prior_refill']).sum()):,}")
m4.metric("Prior Refill Users", f"{filtered['prior_refill_by'].nunique():,}")
m5.metric("Total Qty Off", f"{filtered['abs_discrepancy_qty'].sum():,.0f}")

st.divider()

st.subheader("Rows to Review")
st.caption("Each row is one Verify Inventory mismatch with the most recent prior refill/load for that same med and device.")

review_columns = [
    "dt", "device", "med_id", "med_desc", "user_name",
    "discrepancy_qty", "qty", "beginning_qty", "ending_qty", "discrepancy_reason",
    "prior_refill_dt", "prior_refill_by", "prior_refill_qty",
    "prior_refill_beginning_qty", "prior_refill_ending_qty", "prior_refill_event_type",
    "hours_since_refill", "med_section",
]

review_df = filtered[review_columns].sort_values("dt", ascending=False)
st.dataframe(
    review_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "dt": st.column_config.DatetimeColumn("Verify Time", format="MM/DD/YY HH:mm"),
        "device": st.column_config.TextColumn("Pyxis"),
        "med_id": st.column_config.TextColumn("Med ID"),
        "med_desc": st.column_config.TextColumn("Medication"),
        "user_name": st.column_config.TextColumn("Verify User"),
        "discrepancy_qty": st.column_config.NumberColumn("Qty Off", format="%.0f"),
        "qty": st.column_config.NumberColumn("Verify Qty", format="%.0f"),
        "beginning_qty": st.column_config.NumberColumn("Pyxis Begin", format="%.0f"),
        "ending_qty": st.column_config.NumberColumn("Pyxis End", format="%.0f"),
        "discrepancy_reason": st.column_config.TextColumn("Reason"),
        "prior_refill_dt": st.column_config.DatetimeColumn("Prior Refill Time", format="MM/DD/YY HH:mm"),
        "prior_refill_by": st.column_config.TextColumn("Prior Refill User"),
        "prior_refill_qty": st.column_config.NumberColumn("Prior Refill Qty", format="%.0f"),
        "prior_refill_beginning_qty": st.column_config.NumberColumn("Prior Begin", format="%.0f"),
        "prior_refill_ending_qty": st.column_config.NumberColumn("Prior End", format="%.0f"),
        "prior_refill_event_type": st.column_config.TextColumn("Prior Event"),
        "hours_since_refill": st.column_config.NumberColumn("Hours Since Refill", format="%.1f"),
        "med_section": st.column_config.TextColumn("Med Section"),
    },
)

st.download_button(
    "Export Review Rows to Excel",
    data=to_excel_bytes(review_df),
    file_name="verify_count_audit_rows.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()

st.subheader("Prior Refill User Summary")
st.caption("This is the coaching queue: users tied to the most prior refill/load before the count mismatch.")
if filtered.empty:
    st.info("No rows match the current filters.")
else:
    user_summary = (
        filtered.groupby("prior_refill_by")
        .agg(
            mismatch_count=("pk", "count"),
            total_qty_off=("abs_discrepancy_qty", "sum"),
            avg_qty_off=("abs_discrepancy_qty", "mean"),
            unique_meds=("med_id", "nunique"),
            unique_devices=("device", "nunique"),
            median_hours_since_refill=("hours_since_refill", "median"),
        )
        .reset_index()
        .sort_values(["mismatch_count", "total_qty_off"], ascending=False)
    )
    st.dataframe(
        user_summary,
        use_container_width=True,
        hide_index=True,
        column_config={
            "prior_refill_by": st.column_config.TextColumn("Prior Refill User"),
            "mismatch_count": st.column_config.NumberColumn("Mismatches", format="%d"),
            "total_qty_off": st.column_config.NumberColumn("Total Qty Off", format="%.0f"),
            "avg_qty_off": st.column_config.NumberColumn("Avg Qty Off", format="%.1f"),
            "unique_meds": st.column_config.NumberColumn("Meds", format="%d"),
            "unique_devices": st.column_config.NumberColumn("Devices", format="%d"),
            "median_hours_since_refill": st.column_config.NumberColumn("Median Hours Since Refill", format="%.1f"),
        },
    )
    st.download_button(
        "Export User Summary to Excel",
        data=to_excel_bytes(user_summary),
        file_name="verify_count_audit_user_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

