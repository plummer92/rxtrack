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
PATIENT_CASSETTE_PATTERN = r"patient\s*cass|cassette|cass\b"
COACHING_LOG_TABLE = "verify_count_audit_coaching_log"
MANUAL_CORRECTION_USERS = ["Jared Wolfe"]


st.set_page_config(
    page_title="Verify Count Audit",
    page_icon="🚨",
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


def ensure_coaching_log_table():
    sql = text(f"""
        CREATE TABLE IF NOT EXISTS {COACHING_LOG_TABLE} (
            audit_pk TEXT PRIMARY KEY,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            coaching_user TEXT,
            verify_dt TIMESTAMP,
            device TEXT,
            med_id TEXT,
            med_desc TEXT,
            qty_off FLOAT,
            prior_refill_dt TIMESTAMP,
            prior_refill_qty FLOAT,
            correction_dt TIMESTAMP,
            correction_by TEXT,
            correction_qty FLOAT,
            refill_date_pull_qty FLOAT,
            verify_date_pull_qty FLOAT,
            refill_qty_vs_pull FLOAT,
            coaching_status TEXT DEFAULT 'Needs Coaching',
            coaching_completed_at TIMESTAMP,
            notes TEXT
        )
    """)
    with engine.begin() as conn:
        conn.execute(sql)
        conn.execute(text(f"ALTER TABLE {COACHING_LOG_TABLE} ADD COLUMN IF NOT EXISTS refill_date_pull_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {COACHING_LOG_TABLE} ADD COLUMN IF NOT EXISTS verify_date_pull_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {COACHING_LOG_TABLE} ADD COLUMN IF NOT EXISTS refill_qty_vs_pull FLOAT"))
        conn.execute(text(f"ALTER TABLE {COACHING_LOG_TABLE} ADD COLUMN IF NOT EXISTS coaching_status TEXT DEFAULT 'Needs Coaching'"))
        conn.execute(text(f"ALTER TABLE {COACHING_LOG_TABLE} ADD COLUMN IF NOT EXISTS coaching_completed_at TIMESTAMP"))


@st.cache_data(ttl=60)
def load_completed_audit_pks() -> set:
    ensure_coaching_log_table()
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT audit_pk FROM {COACHING_LOG_TABLE}")).fetchall()
    return {row[0] for row in rows}


def db_value(value):
    return None if pd.isna(value) else value


def save_completed_rows(rows: pd.DataFrame, notes: str = "", manual_correction_by: str | None = None) -> int:
    if rows.empty:
        return 0
    ensure_coaching_log_table()
    sql = text(f"""
        INSERT INTO {COACHING_LOG_TABLE} (
            audit_pk, coaching_user, verify_dt, device, med_id, med_desc, qty_off,
            prior_refill_dt, prior_refill_qty, correction_dt, correction_by,
            correction_qty, refill_date_pull_qty, verify_date_pull_qty,
            refill_qty_vs_pull, notes
        )
        VALUES (
            :audit_pk, :coaching_user, :verify_dt, :device, :med_id, :med_desc, :qty_off,
            :prior_refill_dt, :prior_refill_qty, :correction_dt, :correction_by,
            :correction_qty, :refill_date_pull_qty, :verify_date_pull_qty,
            :refill_qty_vs_pull, :notes
        )
        ON CONFLICT (audit_pk) DO NOTHING
    """)
    payload = []
    completed_at = pd.Timestamp.now()
    for _, row in rows.iterrows():
        correction_by = row["correction_by"]
        correction_dt = row["correction_dt"]
        if manual_correction_by:
            correction_by = manual_correction_by
            if pd.isna(correction_dt):
                correction_dt = completed_at
        payload.append(
            {
                "audit_pk": row["pk"],
                "coaching_user": row["prior_refill_by"],
                "verify_dt": db_value(row["dt"]),
                "device": row["device"],
                "med_id": row["med_id"],
                "med_desc": row["med_desc"],
                "qty_off": db_value(row["discrepancy_qty"]),
                "prior_refill_dt": db_value(row["prior_refill_dt"]),
                "prior_refill_qty": db_value(row["prior_refill_qty"]),
                "correction_dt": db_value(correction_dt),
                "correction_by": correction_by,
                "correction_qty": db_value(row["correction_qty"]),
                "refill_date_pull_qty": db_value(row["refill_date_pull_qty"]),
                "verify_date_pull_qty": db_value(row["verify_date_pull_qty"]),
                "refill_qty_vs_pull": db_value(row["refill_qty_vs_pull"]),
                "notes": notes,
            }
        )
    with engine.begin() as conn:
        result = conn.execute(sql, payload)
    return result.rowcount or 0


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


def is_patient_cassette(row: pd.Series) -> bool:
    med_text = f"{row.get('med_desc', '')} {row.get('med_id', '')}".lower()
    return bool(pd.Series([med_text]).str.contains(PATIENT_CASSETTE_PATTERN, regex=True, na=False).iloc[0])


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


@st.cache_data(ttl=300)
def load_count_inventory_corrections(start, end, followup_days=14):
    """Count Inventory events entered after a mismatch to correct the Pyxis count."""
    try:
        followup_end = end + timedelta(days=followup_days)
        sql = text("""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type,
                   qty, beginning_qty, ending_qty
            FROM events
            WHERE dt::date BETWEEN :start AND :followup_end
              AND event_type ILIKE '%count inventory%'
            ORDER BY dt ASC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "followup_end": followup_end})
        return normalize_event_numbers(df)
    except Exception as exc:
        st.warning(f"[load_count_inventory_corrections] {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_pyxis_pulls(start, end, lookback_days=60):
    """Carousel/Pyxis pull demand lines from pharmacy_orders."""
    try:
        lookback = start - timedelta(days=lookback_days)
        sql = text("""
            SELECT pk, dt, user_name, destination, med_id, med_desc, priority, qty
            FROM pharmacy_orders
            WHERE dt::date BETWEEN :lookback AND :end
              AND priority ILIKE '%pyxis%pull%'
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"lookback": lookback, "end": end})
        if df.empty:
            return df
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["pull_date"] = df["dt"].dt.date
        df["destination"] = df["destination"].fillna("Unknown").astype(str).str.strip()
        df["med_id"] = df["med_id"].fillna("").astype(str).str.strip()
        df["user_name"] = df["user_name"].fillna("Unknown").astype(str).str.strip()
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        return df
    except Exception as exc:
        st.warning(f"[load_pyxis_pulls] {exc}")
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


def find_next_correction(verify_row: pd.Series, corrections: pd.DataFrame) -> pd.Series:
    if corrections.empty:
        return empty_correction()
    matches = corrections[
        (corrections["med_id"] == verify_row["med_id"]) &
        (corrections["device"] == verify_row["device"]) &
        (corrections["dt"] > verify_row["dt"])
    ].sort_values("dt")
    if matches.empty:
        return empty_correction()

    correction = matches.iloc[0]
    hours_until = (correction["dt"] - verify_row["dt"]).total_seconds() / 3600
    return pd.Series({
        "correction_dt": correction["dt"],
        "correction_by": str(correction.get("user_name") or "Unknown"),
        "correction_qty": correction.get("qty", np.nan),
        "correction_beginning_qty": correction.get("beginning_qty", np.nan),
        "correction_ending_qty": correction.get("ending_qty", np.nan),
        "correction_event_type": correction.get("event_type", ""),
        "hours_until_correction": hours_until,
    })


def empty_correction() -> pd.Series:
    return pd.Series({
        "correction_dt": pd.NaT,
        "correction_by": "No Count Inventory found",
        "correction_qty": np.nan,
        "correction_beginning_qty": np.nan,
        "correction_ending_qty": np.nan,
        "correction_event_type": "",
        "hours_until_correction": np.nan,
    })


def pull_summary_for_date(row: pd.Series, pulls: pd.DataFrame, date_col: str, prefix: str) -> pd.Series:
    if pulls.empty or pd.isna(row.get(date_col)):
        return empty_pull_summary(prefix)
    matches = pulls[
        (pulls["pull_date"] == row[date_col]) &
        (pulls["destination"] == str(row["device"]).strip()) &
        (pulls["med_id"] == str(row["med_id"]).strip())
    ].sort_values("dt")
    if matches.empty:
        return empty_pull_summary(prefix)

    return pd.Series({
        f"{prefix}_pull_qty": matches["qty"].sum(),
        f"{prefix}_pull_lines": int(matches["pk"].count()),
        f"{prefix}_first_pull_dt": matches["dt"].min(),
        f"{prefix}_last_pull_dt": matches["dt"].max(),
        f"{prefix}_pull_users": ", ".join(sorted(matches["user_name"].dropna().astype(str).unique())),
    })


def empty_pull_summary(prefix: str) -> pd.Series:
    return pd.Series({
        f"{prefix}_pull_qty": np.nan,
        f"{prefix}_pull_lines": 0,
        f"{prefix}_first_pull_dt": pd.NaT,
        f"{prefix}_last_pull_dt": pd.NaT,
        f"{prefix}_pull_users": "",
    })


@st.cache_data(ttl=300, show_spinner=False)
def build_count_audit_dataset(start, end) -> pd.DataFrame:
    df_verify = load_verify_discrepancies(start, end)
    df_refills = load_prior_refills(start, end)
    df_corrections = load_count_inventory_corrections(start, end)
    df_pulls = load_pyxis_pulls(start, end)

    if df_verify.empty:
        return df_verify

    df_verify[["med_section", "med_group"]] = df_verify.apply(classify_med_section, axis=1)
    prior_cols = df_verify.apply(lambda row: find_prior_refill(row, df_refills), axis=1)
    correction_cols = df_verify.apply(lambda row: find_next_correction(row, df_corrections), axis=1)
    audit_df = pd.concat([df_verify, prior_cols, correction_cols], axis=1)
    audit_df["verify_date"] = audit_df["dt"].dt.date
    audit_df["prior_refill_date"] = audit_df["prior_refill_dt"].dt.date
    audit_df["has_prior_refill"] = audit_df["prior_refill_dt"].notna()
    audit_df["has_count_inventory_correction"] = audit_df["correction_dt"].notna()
    refill_pull_cols = audit_df.apply(
        lambda row: pull_summary_for_date(row, df_pulls, "prior_refill_date", "refill_date"),
        axis=1,
    )
    verify_pull_cols = audit_df.apply(
        lambda row: pull_summary_for_date(row, df_pulls, "verify_date", "verify_date"),
        axis=1,
    )
    audit_df = pd.concat([audit_df, refill_pull_cols, verify_pull_cols], axis=1)
    audit_df["refill_qty_vs_pull"] = audit_df["prior_refill_qty"] - audit_df["refill_date_pull_qty"].fillna(0)
    audit_df["verify_qty_vs_pull"] = audit_df["qty"] - audit_df["verify_date_pull_qty"].fillna(0)
    audit_df = audit_df[~audit_df.apply(is_patient_cassette, axis=1)].copy()
    return audit_df


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
    audit_df = build_count_audit_dataset(start_date, end_date).copy()

if audit_df.empty:
    st.success("No Verify Inventory discrepancies found in the selected date range.")
    st.stop()

completed_pks = load_completed_audit_pks()
audit_df["completed"] = audit_df["pk"].isin(completed_pks)

max_qty_off = int(np.ceil(audit_df["abs_discrepancy_qty"].max())) if not audit_df.empty else 0
min_qty_off = st.slider(
    "Minimum quantity off",
    min_value=1,
    max_value=max(1, max_qty_off),
    value=1,
    step=1,
    help="Raise this to focus on larger count misses, such as 5 or more off.",
    key="verify_audit_min_qty_off",
)

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
    hide_completed = st.checkbox(
        "Hide completed coaching rows",
        value=True,
        key="verify_audit_hide_completed",
    )
    max_review_rows = st.number_input(
        "Rows to show in review table",
        min_value=50,
        max_value=5000,
        value=500,
        step=50,
        help="Keeping this lower makes checkbox edits and note entry much faster. Use filters if you need to narrow the list.",
        key="verify_audit_max_review_rows",
    )

filtered = audit_df.copy()
filtered = filtered[filtered["abs_discrepancy_qty"] >= min_qty_off]
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
if hide_completed:
    filtered = filtered[~filtered["completed"]]

st.info(
    "Pocket-level matching is approximated as same medication plus same Pyxis device because the imported events table does not store drawer/subdrawer/pocket."
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Verify Mismatches", f"{len(filtered):,}")
m2.metric("Matched to Prior Refill", f"{int(filtered['has_prior_refill'].sum()):,}")
m3.metric("Count Inventory Corrections", f"{int(filtered['has_count_inventory_correction'].sum()):,}")
m4.metric("Prior Refill Users", f"{filtered['prior_refill_by'].nunique():,}")
m5.metric("Total Qty Off", f"{filtered['abs_discrepancy_qty'].sum():,.0f}")

st.divider()

st.subheader("Rows to Review")
st.caption("Each row is one Verify Inventory mismatch with the most recent prior refill/load for that same med and device.")

review_columns = [
    "completed", "pk", "dt", "device", "med_id", "med_desc", "user_name",
    "discrepancy_qty", "qty", "beginning_qty", "ending_qty", "discrepancy_reason",
    "prior_refill_dt", "prior_refill_by", "prior_refill_qty",
    "refill_date_pull_qty", "refill_qty_vs_pull", "refill_date_pull_lines",
    "refill_date_first_pull_dt", "refill_date_last_pull_dt", "refill_date_pull_users",
    "prior_refill_beginning_qty", "prior_refill_ending_qty", "prior_refill_event_type",
    "hours_since_refill", "correction_dt", "correction_by", "correction_qty",
    "correction_beginning_qty", "correction_ending_qty", "correction_event_type",
    "hours_until_correction", "verify_date_pull_qty", "verify_qty_vs_pull",
    "verify_date_pull_lines", "verify_date_first_pull_dt", "verify_date_last_pull_dt",
    "verify_date_pull_users", "med_section",
]

review_df = filtered[review_columns].sort_values("dt", ascending=False).head(int(max_review_rows))
if len(filtered) > len(review_df):
    st.caption(
        f"Showing the newest {len(review_df):,} of {len(filtered):,} matching rows. "
        "Raise the row limit or narrow the filters if needed."
    )
edited_review_df = st.data_editor(
    review_df,
    use_container_width=True,
    hide_index=True,
    disabled=[col for col in review_columns if col != "completed"],
    column_config={
        "completed": st.column_config.CheckboxColumn("Complete", help="Check rows you have reviewed and want logged for coaching."),
        "pk": None,
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
        "refill_date_pull_qty": st.column_config.NumberColumn("Refill-Date Pull Qty", format="%.0f"),
        "refill_qty_vs_pull": st.column_config.NumberColumn("Refill Qty vs Pull", format="%.0f"),
        "refill_date_pull_lines": st.column_config.NumberColumn("Refill-Date Pull Lines", format="%d"),
        "refill_date_first_pull_dt": st.column_config.DatetimeColumn("Refill-Date First Pull", format="MM/DD/YY HH:mm"),
        "refill_date_last_pull_dt": st.column_config.DatetimeColumn("Refill-Date Last Pull", format="MM/DD/YY HH:mm"),
        "refill_date_pull_users": st.column_config.TextColumn("Refill-Date Pull Users"),
        "prior_refill_beginning_qty": st.column_config.NumberColumn("Prior Begin", format="%.0f"),
        "prior_refill_ending_qty": st.column_config.NumberColumn("Prior End", format="%.0f"),
        "prior_refill_event_type": st.column_config.TextColumn("Prior Event"),
        "hours_since_refill": st.column_config.NumberColumn("Hours Since Refill", format="%.1f"),
        "correction_dt": st.column_config.DatetimeColumn("Count Inventory Time", format="MM/DD/YY HH:mm"),
        "correction_by": st.column_config.TextColumn("Count Inventory User"),
        "correction_qty": st.column_config.NumberColumn("Count Inventory Qty", format="%.0f"),
        "correction_beginning_qty": st.column_config.NumberColumn("Count Inv Begin", format="%.0f"),
        "correction_ending_qty": st.column_config.NumberColumn("Count Inv End", format="%.0f"),
        "correction_event_type": st.column_config.TextColumn("Correction Event"),
        "hours_until_correction": st.column_config.NumberColumn("Hours to Correction", format="%.1f"),
        "verify_date_pull_qty": st.column_config.NumberColumn("Verify-Date Pull Qty", format="%.0f"),
        "verify_qty_vs_pull": st.column_config.NumberColumn("Verify Qty vs Pull", format="%.0f"),
        "verify_date_pull_lines": st.column_config.NumberColumn("Verify-Date Pull Lines", format="%d"),
        "verify_date_first_pull_dt": st.column_config.DatetimeColumn("Verify-Date First Pull", format="MM/DD/YY HH:mm"),
        "verify_date_last_pull_dt": st.column_config.DatetimeColumn("Verify-Date Last Pull", format="MM/DD/YY HH:mm"),
        "verify_date_pull_users": st.column_config.TextColumn("Verify-Date Pull Users"),
        "med_section": st.column_config.TextColumn("Med Section"),
    },
)

with st.form("verify_audit_completion_form", clear_on_submit=False):
    completion_note = st.text_input(
        "Completion note",
        placeholder="Optional note to attach to newly completed rows",
        key="verify_audit_completion_note",
    )
    manual_correction_user = st.selectbox(
        "Manual correction user for checked rows",
        ["Keep Count Inventory user from table"] + MANUAL_CORRECTION_USERS,
        help="Use this if the Count Inventory transaction has not appeared in the imported Pyxis data yet.",
        key="verify_audit_manual_correction_user",
    )
    submitted_completion = st.form_submit_button(
        "Mark checked rows completed and send to coaching report",
        type="primary",
    )

manual_correction_by = (
    None if manual_correction_user == "Keep Count Inventory user from table" else manual_correction_user
)
new_completed = edited_review_df[
    (edited_review_df["completed"]) &
    (~edited_review_df["pk"].isin(completed_pks))
]
if submitted_completion:
    with st.spinner("Saving completed rows..."):
        inserted = save_completed_rows(new_completed, completion_note, manual_correction_by)
    if inserted:
        load_completed_audit_pks.clear()
        st.success(f"Logged {inserted} completed row(s) to the coaching report.")
        st.rerun()
    else:
        st.info("No new checked rows to log.")

st.download_button(
    "Export Review Rows to Excel",
    data=to_excel_bytes(review_df.drop(columns=["completed"], errors="ignore")),
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

