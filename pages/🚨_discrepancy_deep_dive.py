import io
import json
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
INVENTORY_CHANGE_EVENT_PATTERN = (
    "ARRAY['%restock%', '%refill%', '%load%', '%replenish%', '%count inventory%', "
    "'%unload%', '%empty%', '%outdate%', '%adjust%']"
)
PATIENT_CASSETTE_PATTERN = r"patient\s*cass|cassette|cass\b"
COACHING_LOG_TABLE = "verify_count_audit_coaching_log"
MANUAL_CORRECTION_USERS = ["Jared Wolfe"]
EVIDENCE_OPTIONS = [
    "Strong refill-entry pattern",
    "Possible refill-entry pattern",
    "Needs inventory-chain review",
    "Missing pull data",
    "Refill matched pull",
    "No prior refill found",
]


st.set_page_config(
    page_title="Verify Count Audit",
    page_icon="🚨",
    layout="wide",
)
App.apply_global_styles()

engine = App.engine
App.init_db()
render_sidebar = App.render_sidebar
start_date, end_date = render_sidebar()
App.require_management_access("Verify Count Audit")


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


def fmt_qty(value) -> str:
    if pd.isna(value):
        return "missing"
    return f"{float(value):.0f}"


def build_coaching_actions(
    filtered_df: pd.DataFrame,
    user_summary_df: pd.DataFrame,
    min_example_qty_off: int = 3,
    max_users: int = 10,
) -> pd.DataFrame:
    if filtered_df.empty or user_summary_df.empty:
        return pd.DataFrame()

    action_rows = []
    prioritized_users = user_summary_df[
        (user_summary_df["Strong refill-entry pattern"] > 0) |
        (user_summary_df["Possible refill-entry pattern"] > 0)
    ].head(max_users)

    for _, user in prioritized_users.iterrows():
        user_name = user["prior_refill_by"]
        user_rows = filtered_df[filtered_df["prior_refill_by"] == user_name].copy()
        signal_rows = user_rows[
            user_rows["evidence_status"].isin(["Strong refill-entry pattern", "Possible refill-entry pattern"])
        ].sort_values(["evidence_status", "abs_discrepancy_qty"], ascending=[True, False])
        if signal_rows.empty:
            continue

        example_rows = signal_rows[signal_rows["abs_discrepancy_qty"] >= min_example_qty_off]
        if example_rows.empty:
            example_rows = signal_rows

        examples = []
        for _, row in example_rows.head(3).iterrows():
            prior_event = str(row.get("prior_refill_event_type") or "Refill/Load")
            examples.append(
                f"{row['med_id']} at {row['device']} on {row['dt']:%m/%d %H:%M}: "
                f"{prior_event.lower()} entered {fmt_qty(row['prior_refill_qty'])}, "
                f"pull was {fmt_qty(row['refill_date_pull_qty'])}, "
                f"later verify was off {fmt_qty(row['discrepancy_qty'])}."
            )

        strong = int(user.get("Strong refill-entry pattern", 0))
        possible = int(user.get("Possible refill-entry pattern", 0))
        chain_review = int(user.get("Needs inventory-chain review", 0))
        priority = "Coach now" if strong >= 2 or (strong >= 1 and possible >= 1) else "Review examples first"
        action_rows.append({
            "priority": priority,
            "prior_refill_user": user_name,
            "strong": strong,
            "possible": possible,
            "chain_review": chain_review,
            "total_rows": int(user["mismatch_count"]),
            "suggested_action": (
                f"Review {strong} strong and {possible} possible refill-entry pattern(s). "
                f"Use examples with quantity off >= {min_example_qty_off} when available. "
                "Focus coaching on entering the actual refill/load quantity from the Pyxis pull."
            ),
            "example_evidence": "\n".join(examples),
        })

    return pd.DataFrame(action_rows)


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


def save_strong_patterns_to_management(action_plan: pd.DataFrame, filtered_df: pd.DataFrame, audit_start, audit_end) -> int:
    if action_plan.empty:
        return 0
    strong_plan = action_plan[action_plan["strong"].fillna(0).astype(int) > 0].copy()
    if strong_plan.empty:
        return 0

    sql = text("""
        INSERT INTO management_coaching_notes
            (staff_name, topic, coaching_date, follow_up_date, status, summary, next_steps, source_page, source_key, source_payload_json)
        VALUES
            (:staff_name, :topic, CURRENT_DATE, CURRENT_DATE, 'Open', :summary, :next_steps, :source_page, :source_key, :source_payload_json)
        ON CONFLICT (source_key) WHERE source_key IS NOT NULL DO UPDATE SET
            coaching_date = CURRENT_DATE,
            follow_up_date = CURRENT_DATE,
            status = CASE
                WHEN management_coaching_notes.status = 'Closed' THEN 'Open'
                ELSE management_coaching_notes.status
            END,
            summary = EXCLUDED.summary,
            next_steps = EXCLUDED.next_steps,
            source_payload_json = EXCLUDED.source_payload_json,
            updated_at = NOW()
    """)
    payload = []
    for _, row in strong_plan.iterrows():
        staff_name = str(row["prior_refill_user"]).strip()
        source_key = f"verify-count-audit-strong:{staff_name.lower()}"
        strong_rows = filtered_df[
            (filtered_df["prior_refill_by"].astype(str).str.strip() == staff_name) &
            (filtered_df["evidence_status"] == "Strong refill-entry pattern")
        ].sort_values("abs_discrepancy_qty", ascending=False)
        evidence_rows = []
        examples = []
        for _, detail in strong_rows.iterrows():
            verify_time = pd.to_datetime(detail.get("dt"), errors="coerce")
            prior_time = pd.to_datetime(detail.get("prior_refill_dt"), errors="coerce")
            verify_label = verify_time.strftime("%m/%d %H:%M") if pd.notna(verify_time) else "unknown time"
            evidence_rows.append({
                "Verify Time": verify_time.strftime("%m/%d/%y %H:%M") if pd.notna(verify_time) else "",
                "Pyxis": str(detail.get("device") or ""),
                "Med ID": str(detail.get("med_id") or ""),
                "Medication": str(detail.get("med_desc") or ""),
                "Verify User": str(detail.get("user_name") or ""),
                "Prior Refill Time": prior_time.strftime("%m/%d/%y %H:%M") if pd.notna(prior_time) else "",
                "Prior Event": str(detail.get("prior_refill_event_type") or ""),
                "Refill Entered": db_value(detail.get("prior_refill_qty")),
                "Pull Qty": db_value(detail.get("refill_date_pull_qty")),
                "Refill vs Pull": db_value(detail.get("refill_qty_vs_pull")),
                "Later Verify Off": db_value(detail.get("discrepancy_qty")),
                "Inventory Events Since": db_value(detail.get("inventory_events_since_refill")),
                "Why It Matched": str(detail.get("evidence_reason") or ""),
            })
            examples.append(
                f"{detail['med_id']} at {detail['device']} on {verify_label}: "
                f"refill entered {fmt_qty(detail['prior_refill_qty'])}, "
                f"pull was {fmt_qty(detail['refill_date_pull_qty'])}, "
                f"later verify was off {fmt_qty(detail['discrepancy_qty'])}."
            )
        summary = (
            f"Verify Count Audit found {int(row['strong'])} strong refill-entry pattern(s) "
            f"for {staff_name} between {audit_start} and {audit_end}.\n\n"
            f"Strong example{'s' if len(examples) != 1 else ''}:\n"
            f"{chr(10).join(examples[:5]) if examples else 'Review Verify Count Audit drilldown for examples.'}"
        )
        next_steps = (
            "Coach on entering the actual refill/load quantity from the Pyxis pull, then document the conversation. "
            "Use the Verify Count Audit examples to show the specific medication, device, entered refill quantity, pull quantity, and later verify discrepancy."
        )
        payload.append({
            "staff_name": staff_name,
            "topic": "Discrepancy",
            "summary": summary,
            "next_steps": next_steps,
            "source_page": "Verify Count Audit",
            "source_key": source_key,
            "source_payload_json": json.dumps(evidence_rows, default=str),
        })

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
def load_inventory_change_events(start, end, lookback_days=60):
    """Inventory-changing events that can break the chain between a refill and later verify."""
    try:
        lookback = start - timedelta(days=lookback_days)
        sql = text(f"""
            SELECT pk, dt, user_name, device, med_id, med_desc, event_type,
                   qty, beginning_qty, ending_qty
            FROM events
            WHERE dt::date BETWEEN :lookback AND :end
              AND event_type ILIKE ANY ({INVENTORY_CHANGE_EVENT_PATTERN})
              AND event_type NOT ILIKE '%verify%'
              AND event_type NOT ILIKE '%remove%'
            ORDER BY dt ASC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"lookback": lookback, "end": end})
        return normalize_event_numbers(df)
    except Exception as exc:
        st.warning(f"[load_inventory_change_events] {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_clinical_activity(start, end, lookback_days=60):
    """Nursing/clinical Pyxis vend and waste activity from Audit Transaction Detail RC."""
    try:
        lookback = start - timedelta(days=lookback_days)
        sql = text("""
            SELECT
                pk,
                dt,
                user_name,
                user_type,
                station_name AS device,
                med_id,
                med_desc,
                transaction_type AS event_type,
                qty,
                beginning_qty,
                ending_qty,
                waste_amount,
                location
            FROM audit_transaction_detail_rc
            WHERE dt::date BETWEEN :lookback AND :end
              AND (
                    transaction_type ILIKE '%vend%'
                 OR transaction_type ILIKE '%waste%'
              )
              AND (
                    user_type ILIKE '%registered nurse%'
                 OR user_type ILIKE '%nurse%'
                 OR user_type ILIKE '%anesthesia%'
                 OR user_type ILIKE '%respiratory%'
              )
            ORDER BY dt ASC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"lookback": lookback, "end": end})
        return normalize_event_numbers(df)
    except Exception as exc:
        st.warning(f"[load_clinical_activity] {exc}")
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


def find_inventory_changes_since_refill(verify_row: pd.Series, inventory_events: pd.DataFrame) -> pd.Series:
    if inventory_events.empty or pd.isna(verify_row.get("prior_refill_dt")):
        return empty_inventory_chain()
    matches = inventory_events[
        (inventory_events["med_id"] == verify_row["med_id"]) &
        (inventory_events["device"] == verify_row["device"]) &
        (inventory_events["dt"] > verify_row["prior_refill_dt"]) &
        (inventory_events["dt"] < verify_row["dt"]) &
        (inventory_events["pk"] != verify_row["pk"])
    ].sort_values("dt")
    if matches.empty:
        return empty_inventory_chain()

    last_event = matches.iloc[-1]
    return pd.Series({
        "inventory_events_since_refill": int(len(matches)),
        "last_inventory_event_dt": last_event["dt"],
        "last_inventory_event_by": str(last_event.get("user_name") or "Unknown"),
        "last_inventory_event_type": last_event.get("event_type", ""),
        "last_inventory_event_qty": last_event.get("qty", np.nan),
        "last_inventory_event_beginning_qty": last_event.get("beginning_qty", np.nan),
        "last_inventory_event_ending_qty": last_event.get("ending_qty", np.nan),
    })


def find_clinical_activity_since_refill(verify_row: pd.Series, clinical_events: pd.DataFrame) -> pd.Series:
    if clinical_events.empty or pd.isna(verify_row.get("prior_refill_dt")):
        return empty_clinical_chain()
    matches = clinical_events[
        (clinical_events["med_id"] == verify_row["med_id"]) &
        (clinical_events["device"] == verify_row["device"]) &
        (clinical_events["dt"] > verify_row["prior_refill_dt"]) &
        (clinical_events["dt"] < verify_row["dt"])
    ].sort_values("dt")
    if matches.empty:
        return empty_clinical_chain()

    vend_count = int(matches["event_type"].fillna("").str.contains("vend", case=False, na=False).sum())
    waste_count = int(matches["event_type"].fillna("").str.contains("waste", case=False, na=False).sum())
    last_event = matches.iloc[-1]
    return pd.Series({
        "clinical_events_since_refill": int(len(matches)),
        "clinical_vends_since_refill": vend_count,
        "clinical_wastes_since_refill": waste_count,
        "clinical_qty_since_refill": pd.to_numeric(matches["qty"], errors="coerce").fillna(0).sum(),
        "last_clinical_event_dt": last_event["dt"],
        "last_clinical_event_by": str(last_event.get("user_name") or "Unknown"),
        "last_clinical_event_type": last_event.get("event_type", ""),
        "last_clinical_event_qty": last_event.get("qty", np.nan),
    })


def empty_clinical_chain() -> pd.Series:
    return pd.Series({
        "clinical_events_since_refill": 0,
        "clinical_vends_since_refill": 0,
        "clinical_wastes_since_refill": 0,
        "clinical_qty_since_refill": 0,
        "last_clinical_event_dt": pd.NaT,
        "last_clinical_event_by": "",
        "last_clinical_event_type": "",
        "last_clinical_event_qty": np.nan,
    })


def empty_inventory_chain() -> pd.Series:
    return pd.Series({
        "inventory_events_since_refill": 0,
        "last_inventory_event_dt": pd.NaT,
        "last_inventory_event_by": "",
        "last_inventory_event_type": "",
        "last_inventory_event_qty": np.nan,
        "last_inventory_event_beginning_qty": np.nan,
        "last_inventory_event_ending_qty": np.nan,
    })


def classify_evidence(row: pd.Series) -> pd.Series:
    discrepancy = row.get("discrepancy_qty", np.nan)
    refill_vs_pull = row.get("refill_qty_vs_pull", np.nan)
    refill_pull_qty = row.get("refill_date_pull_qty", np.nan)
    inventory_events = int(row.get("inventory_events_since_refill") or 0)

    if not row.get("has_prior_refill", False):
        status = "No prior refill found"
        reason = "No prior refill/load was found for this med and Pyxis before the verify event."
    elif inventory_events > 0:
        status = "Needs inventory-chain review"
        reason = "Another inventory-changing transaction happened after the prior refill and before the verify."
    elif int(row.get("clinical_events_since_refill") or 0) > 0:
        status = "Needs inventory-chain review"
        reason = "Nursing/clinical vend or waste activity happened after the prior refill and before the verify."
    elif pd.isna(refill_pull_qty):
        status = "Missing pull data"
        reason = "No refill-date Pyxis pull quantity was found, so the refill entry cannot be compared to pull quantity."
    elif pd.isna(refill_vs_pull) or pd.isna(discrepancy):
        status = "Missing pull data"
        reason = "The refill-vs-pull or discrepancy value is missing."
    elif abs(refill_vs_pull) <= 0.01:
        status = "Refill matched pull"
        reason = "The prior refill quantity matched the refill-date pull quantity."
    elif (
        abs(abs(refill_vs_pull) - abs(discrepancy)) <= 0.01 and
        np.sign(refill_vs_pull) == -np.sign(discrepancy)
    ):
        status = "Strong refill-entry pattern"
        reason = "The refill-vs-pull difference exactly lines up with the later verify mismatch."
    elif (
        abs(abs(refill_vs_pull) - abs(discrepancy)) <= 2 and
        np.sign(refill_vs_pull) == -np.sign(discrepancy)
    ):
        status = "Possible refill-entry pattern"
        reason = "The refill-vs-pull difference is close to the later verify mismatch."
    else:
        status = "Needs inventory-chain review"
        reason = "The refill-vs-pull difference does not cleanly explain the verify mismatch."

    return pd.Series({"evidence_status": status, "evidence_reason": reason})


def dedupe_verify_rows(audit_df: pd.DataFrame) -> pd.DataFrame:
    if audit_df.empty:
        return audit_df
    natural_key = ["dt", "device", "med_id", "user_name", "discrepancy_qty", "qty"]
    dedupe_keys = [col for col in natural_key if col in audit_df.columns]
    if dedupe_keys:
        return audit_df.sort_values("dt", ascending=False).drop_duplicates(subset=dedupe_keys, keep="first")
    if "pk" in audit_df.columns:
        return audit_df.sort_values("dt", ascending=False).drop_duplicates(subset=["pk"], keep="first")
    return audit_df


def summarize_pulls_by_date(pulls: pd.DataFrame) -> pd.DataFrame:
    if pulls.empty:
        return pd.DataFrame()
    return (
        pulls.groupby(["pull_date", "destination", "med_id"], dropna=False)
        .agg(
            pull_qty=("qty", "sum"),
            pull_lines=("pk", "count"),
            first_pull_dt=("dt", "min"),
            last_pull_dt=("dt", "max"),
            pull_users=("user_name", lambda s: ", ".join(sorted(s.dropna().astype(str).unique()))),
        )
        .reset_index()
    )


def add_pull_summary(audit_df: pd.DataFrame, pull_summary: pd.DataFrame, date_col: str, prefix: str) -> pd.DataFrame:
    pull_cols = [
        f"{prefix}_pull_qty",
        f"{prefix}_pull_lines",
        f"{prefix}_first_pull_dt",
        f"{prefix}_last_pull_dt",
        f"{prefix}_pull_users",
    ]
    if pull_summary.empty:
        for col in pull_cols:
            audit_df[col] = 0 if col.endswith("_lines") else np.nan
        audit_df[f"{prefix}_pull_users"] = ""
        return audit_df

    key_cols = [f"{prefix}_date_key", f"{prefix}_device_key", f"{prefix}_med_key"]
    summary = pull_summary.rename(columns={
        "pull_date": key_cols[0],
        "destination": key_cols[1],
        "med_id": key_cols[2],
        "pull_qty": f"{prefix}_pull_qty",
        "pull_lines": f"{prefix}_pull_lines",
        "first_pull_dt": f"{prefix}_first_pull_dt",
        "last_pull_dt": f"{prefix}_last_pull_dt",
        "pull_users": f"{prefix}_pull_users",
    })
    audit_df = audit_df.merge(
        summary,
        how="left",
        left_on=[date_col, "device", "med_id"],
        right_on=key_cols,
    )
    audit_df = audit_df.drop(columns=key_cols)
    audit_df[f"{prefix}_pull_lines"] = audit_df[f"{prefix}_pull_lines"].fillna(0).astype(int)
    audit_df[f"{prefix}_pull_users"] = audit_df[f"{prefix}_pull_users"].fillna("")
    return audit_df


@st.cache_data(ttl=300, show_spinner=False)
def build_count_audit_dataset(start, end) -> pd.DataFrame:
    df_verify = load_verify_discrepancies(start, end)
    df_refills = load_prior_refills(start, end)
    df_corrections = load_count_inventory_corrections(start, end)
    df_inventory_events = load_inventory_change_events(start, end)
    df_clinical_events = load_clinical_activity(start, end)
    df_pulls = load_pyxis_pulls(start, end)

    if df_verify.empty:
        return df_verify

    df_verify[["med_section", "med_group"]] = df_verify.apply(classify_med_section, axis=1)
    prior_cols = df_verify.apply(lambda row: find_prior_refill(row, df_refills), axis=1)
    correction_cols = df_verify.apply(lambda row: find_next_correction(row, df_corrections), axis=1)
    audit_df = pd.concat([df_verify, prior_cols, correction_cols], axis=1)
    inventory_chain_cols = audit_df.apply(
        lambda row: find_inventory_changes_since_refill(row, df_inventory_events),
        axis=1,
    )
    audit_df = pd.concat([audit_df, inventory_chain_cols], axis=1)
    clinical_chain_cols = audit_df.apply(
        lambda row: find_clinical_activity_since_refill(row, df_clinical_events),
        axis=1,
    )
    audit_df = pd.concat([audit_df, clinical_chain_cols], axis=1)
    audit_df["verify_date"] = audit_df["dt"].dt.date
    audit_df["prior_refill_date"] = audit_df["prior_refill_dt"].dt.date
    audit_df["has_prior_refill"] = audit_df["prior_refill_dt"].notna()
    audit_df["has_count_inventory_correction"] = audit_df["correction_dt"].notna()
    pull_summary = summarize_pulls_by_date(df_pulls)
    audit_df = add_pull_summary(audit_df, pull_summary, "prior_refill_date", "refill_date")
    audit_df = add_pull_summary(audit_df, pull_summary, "verify_date", "verify_date")
    audit_df["refill_qty_vs_pull"] = audit_df["prior_refill_qty"] - audit_df["refill_date_pull_qty"].fillna(0)
    audit_df["verify_qty_vs_pull"] = audit_df["qty"] - audit_df["verify_date_pull_qty"].fillna(0)
    audit_df[["evidence_status", "evidence_reason"]] = audit_df.apply(classify_evidence, axis=1)
    audit_df = audit_df[~audit_df.apply(is_patient_cassette, axis=1)].copy()
    audit_df = dedupe_verify_rows(audit_df)
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

max_qty_off_raw = pd.to_numeric(audit_df["abs_discrepancy_qty"], errors="coerce").max() if not audit_df.empty else 0
max_qty_off = int(np.ceil(max_qty_off_raw)) if pd.notna(max_qty_off_raw) else 1
max_qty_off = max(1, max_qty_off)
slider_key = "verify_audit_min_qty_off"
if slider_key in st.session_state:
    st.session_state[slider_key] = min(max(1, int(st.session_state[slider_key])), max_qty_off)
min_qty_off = st.slider(
    "Minimum quantity off",
    min_value=1,
    max_value=max_qty_off,
    value=1,
    step=1,
    help="Raise this to focus on larger count misses, such as 5 or more off.",
    key=slider_key,
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
    evidence_filter = st.multiselect(
        "Evidence Type",
        EVIDENCE_OPTIONS,
        default=["Strong refill-entry pattern", "Possible refill-entry pattern", "Needs inventory-chain review"],
        key="verify_audit_evidence_filter",
    )
    only_matched = st.checkbox(
        "Only rows with a prior refill/load found",
        value=False,
        key="verify_audit_only_matched",
    )
    only_clean_chain = st.checkbox(
        "Only clean refill chains",
        value=False,
        help="Show only rows with no inventory-changing transaction between the prior refill/load and the verify event.",
        key="verify_audit_only_clean_chain",
    )
    use_max_hours_filter = st.checkbox(
        "Use max-hours prior refill filter",
        value=False,
        help="Optional. Old refills can still be valid when the inventory chain is clean.",
        key="verify_audit_use_max_hours_filter",
    )
    max_hours_since_refill = st.number_input(
        "Max hours since prior refill/load",
        min_value=1,
        max_value=1440,
        value=48,
        step=1,
        disabled=not use_max_hours_filter,
        help="Optional stale-match filter. Leave off when you want old but clean refill chains to remain visible.",
        key="verify_audit_max_hours_since_refill",
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
if evidence_filter:
    filtered = filtered[filtered["evidence_status"].isin(evidence_filter)]
else:
    filtered = filtered.iloc[0:0]
if only_matched:
    filtered = filtered[filtered["has_prior_refill"]]
if only_clean_chain:
    filtered = filtered[filtered["inventory_events_since_refill"] == 0]
if use_max_hours_filter:
    filtered = filtered[
        filtered["hours_since_refill"].isna() |
        (filtered["hours_since_refill"] <= max_hours_since_refill)
    ]
if hide_completed:
    filtered = filtered[~filtered["completed"]]

st.info(
    "Pocket-level matching is approximated as same medication plus same Pyxis device because the imported events table does not store drawer/subdrawer/pocket."
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Verify Mismatches", f"{len(filtered):,}")
m2.metric("Strong Evidence", f"{int((filtered['evidence_status'] == 'Strong refill-entry pattern').sum()):,}")
m3.metric("Possible Evidence", f"{int((filtered['evidence_status'] == 'Possible refill-entry pattern').sum()):,}")
m4.metric("Clean Refill Chains", f"{int((filtered['inventory_events_since_refill'] == 0).sum()):,}")
m5.metric("Total Qty Off", f"{filtered['abs_discrepancy_qty'].sum():,.0f}")
st.caption(
    f"Clinical vend/waste activity between refill and verify: "
    f"{int(filtered['clinical_events_since_refill'].fillna(0).gt(0).sum()):,} row(s)."
)

if not filtered.empty:
    evidence_summary = (
        filtered.groupby("evidence_status")
        .agg(rows=("pk", "count"), total_qty_off=("abs_discrepancy_qty", "sum"))
        .reset_index()
        .sort_values(["rows", "total_qty_off"], ascending=False)
    )
    st.dataframe(
        evidence_summary,
        use_container_width=True,
        hide_index=True,
        column_config={
            "evidence_status": st.column_config.TextColumn("Evidence Type"),
            "rows": st.column_config.NumberColumn("Rows", format="%d"),
            "total_qty_off": st.column_config.NumberColumn("Total Qty Off", format="%.0f"),
        },
    )

st.divider()

st.subheader("Rows to Review")
st.caption(
    "The review grid is intentionally narrow for speed. It keeps the verify count, verify-date pull, "
    "prior refill quantity, and prior refill-date pull comparison visible for coaching."
)

review_columns = [
    "completed", "pk", "evidence_status", "evidence_reason", "dt", "device", "med_id", "med_desc", "user_name",
    "discrepancy_qty", "qty", "beginning_qty", "ending_qty", "discrepancy_reason",
    "prior_refill_dt", "prior_refill_by", "prior_refill_qty",
    "refill_date_pull_qty", "refill_qty_vs_pull", "refill_date_pull_lines",
    "refill_date_first_pull_dt", "refill_date_last_pull_dt", "refill_date_pull_users",
    "prior_refill_beginning_qty", "prior_refill_ending_qty", "prior_refill_event_type",
    "inventory_events_since_refill", "last_inventory_event_dt", "last_inventory_event_by",
    "last_inventory_event_type", "last_inventory_event_qty", "last_inventory_event_beginning_qty",
    "last_inventory_event_ending_qty",
    "clinical_events_since_refill", "clinical_vends_since_refill", "clinical_wastes_since_refill",
    "clinical_qty_since_refill", "last_clinical_event_dt", "last_clinical_event_by",
    "last_clinical_event_type", "last_clinical_event_qty",
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

review_grid_columns = [
    "completed", "pk", "evidence_status", "dt", "device", "med_id", "med_desc",
    "user_name", "discrepancy_qty", "qty", "verify_date_pull_qty",
    "verify_qty_vs_pull", "prior_refill_dt", "prior_refill_by",
    "prior_refill_qty", "refill_date_pull_qty", "refill_qty_vs_pull",
    "inventory_events_since_refill", "last_inventory_event_type",
    "clinical_events_since_refill", "last_clinical_event_type",
    "correction_dt", "correction_by", "correction_qty", "hours_since_refill",
]
review_grid_df = review_df[review_grid_columns].copy()
edited_review_df = st.data_editor(
    review_grid_df,
    use_container_width=True,
    hide_index=True,
    disabled=[col for col in review_grid_columns if col != "completed"],
    column_config={
        "completed": st.column_config.CheckboxColumn("Complete", help="Check rows you have reviewed and want logged for coaching."),
        "pk": None,
        "evidence_status": st.column_config.TextColumn("Evidence"),
        "dt": st.column_config.DatetimeColumn("Verify Time", format="MM/DD/YY HH:mm"),
        "device": st.column_config.TextColumn("Pyxis"),
        "med_id": st.column_config.TextColumn("Med ID"),
        "med_desc": st.column_config.TextColumn("Medication"),
        "user_name": st.column_config.TextColumn("Verify User"),
        "discrepancy_qty": st.column_config.NumberColumn("Qty Off", format="%.0f"),
        "qty": st.column_config.NumberColumn("Verify Qty", format="%.0f"),
        "verify_date_pull_qty": st.column_config.NumberColumn("Verify-Date Pull Qty", format="%.0f"),
        "verify_qty_vs_pull": st.column_config.NumberColumn("Verify Qty vs Pull", format="%.0f"),
        "prior_refill_dt": st.column_config.DatetimeColumn("Prior Refill Time", format="MM/DD/YY HH:mm"),
        "prior_refill_by": st.column_config.TextColumn("Prior Refill User"),
        "prior_refill_qty": st.column_config.NumberColumn("Prior Refill Qty", format="%.0f"),
        "refill_date_pull_qty": st.column_config.NumberColumn("Refill-Date Pull Qty", format="%.0f"),
        "refill_qty_vs_pull": st.column_config.NumberColumn("Refill Qty vs Pull", format="%.0f"),
        "inventory_events_since_refill": st.column_config.NumberColumn("Inv Events Since Refill", format="%d"),
        "last_inventory_event_type": st.column_config.TextColumn("Last Inv Event"),
        "clinical_events_since_refill": st.column_config.NumberColumn("Clinical Events", format="%d"),
        "last_clinical_event_type": st.column_config.TextColumn("Last Clinical Event"),
        "hours_since_refill": st.column_config.NumberColumn("Hours Since Refill", format="%.1f"),
        "correction_dt": st.column_config.DatetimeColumn("Count Inventory Time", format="MM/DD/YY HH:mm"),
        "correction_by": st.column_config.TextColumn("Count Inventory User"),
        "correction_qty": st.column_config.NumberColumn("Count Inventory Qty", format="%.0f"),
    },
)

if review_df.empty:
    st.info("No rows match the current filters.")
else:
    detail_options = review_df.assign(
        row_label=lambda df: (
            df["dt"].dt.strftime("%m/%d/%y %H:%M").fillna("No verify time") +
            " | " + df["device"].fillna("Unknown Pyxis").astype(str) +
            " | " + df["med_id"].fillna("").astype(str) +
            " | " + df["evidence_status"].fillna("Review").astype(str) +
            " | off " + df["discrepancy_qty"].fillna(0).round(0).astype(int).astype(str) +
            " | prior " + df["prior_refill_by"].fillna("No prior refill").astype(str)
        )
    )[["pk", "row_label"]]
    selected_label = st.selectbox(
        "Inspect full detail for one row",
        detail_options["row_label"].tolist(),
        key="verify_audit_detail_row",
    )
    selected_pk = detail_options.loc[detail_options["row_label"] == selected_label, "pk"].iloc[0]
    detail_row = review_df[review_df["pk"] == selected_pk].iloc[0]

    verify_tab, prior_tab, chain_tab, correction_tab, raw_tab = st.tabs([
        "Verify Count",
        "Prior Refill + Pull",
        "Inventory + Clinical Chain",
        "Count Inventory",
        "All Fields",
    ])
    st.info(str(detail_row["evidence_reason"]))
    with verify_tab:
        st.dataframe(
            pd.DataFrame([{
                "Verify Time": detail_row["dt"],
                "Verify User": detail_row["user_name"],
                "Pyxis": detail_row["device"],
                "Med ID": detail_row["med_id"],
                "Medication": detail_row["med_desc"],
                "Qty Off": detail_row["discrepancy_qty"],
                "Verify Qty": detail_row["qty"],
                "Pyxis Begin": detail_row["beginning_qty"],
                "Pyxis End": detail_row["ending_qty"],
                "Reason": detail_row["discrepancy_reason"],
                "Verify-Date Pull Qty": detail_row["verify_date_pull_qty"],
                "Verify Qty vs Pull": detail_row["verify_qty_vs_pull"],
                "Verify-Date Pull Lines": detail_row["verify_date_pull_lines"],
                "Verify-Date Pull Users": detail_row["verify_date_pull_users"],
                "Verify-Date First Pull": detail_row["verify_date_first_pull_dt"],
                "Verify-Date Last Pull": detail_row["verify_date_last_pull_dt"],
            }]),
            use_container_width=True,
            hide_index=True,
        )
    with prior_tab:
        st.dataframe(
            pd.DataFrame([{
                "Prior Refill Time": detail_row["prior_refill_dt"],
                "Prior Refill User": detail_row["prior_refill_by"],
                "Prior Refill Qty": detail_row["prior_refill_qty"],
                "Prior Begin": detail_row["prior_refill_beginning_qty"],
                "Prior End": detail_row["prior_refill_ending_qty"],
                "Prior Event": detail_row["prior_refill_event_type"],
                "Hours Since Refill": detail_row["hours_since_refill"],
                "Refill-Date Pull Qty": detail_row["refill_date_pull_qty"],
                "Refill Qty vs Pull": detail_row["refill_qty_vs_pull"],
                "Refill-Date Pull Lines": detail_row["refill_date_pull_lines"],
                "Refill-Date Pull Users": detail_row["refill_date_pull_users"],
                "Refill-Date First Pull": detail_row["refill_date_first_pull_dt"],
                "Refill-Date Last Pull": detail_row["refill_date_last_pull_dt"],
            }]),
            use_container_width=True,
            hide_index=True,
        )
    with chain_tab:
        st.dataframe(
            pd.DataFrame([{
                "Evidence": detail_row["evidence_status"],
                "Evidence Reason": detail_row["evidence_reason"],
                "Inventory Events Since Refill": detail_row["inventory_events_since_refill"],
                "Last Inventory Event Time": detail_row["last_inventory_event_dt"],
                "Last Inventory Event User": detail_row["last_inventory_event_by"],
                "Last Inventory Event Type": detail_row["last_inventory_event_type"],
                "Last Inventory Event Qty": detail_row["last_inventory_event_qty"],
                "Last Inventory Begin": detail_row["last_inventory_event_beginning_qty"],
                "Last Inventory End": detail_row["last_inventory_event_ending_qty"],
                "Clinical Events Since Refill": detail_row["clinical_events_since_refill"],
                "Clinical Vends Since Refill": detail_row["clinical_vends_since_refill"],
                "Clinical Wastes Since Refill": detail_row["clinical_wastes_since_refill"],
                "Clinical Qty Since Refill": detail_row["clinical_qty_since_refill"],
                "Last Clinical Event Time": detail_row["last_clinical_event_dt"],
                "Last Clinical Event User": detail_row["last_clinical_event_by"],
                "Last Clinical Event Type": detail_row["last_clinical_event_type"],
                "Last Clinical Event Qty": detail_row["last_clinical_event_qty"],
            }]),
            use_container_width=True,
            hide_index=True,
        )
    with correction_tab:
        st.dataframe(
            pd.DataFrame([{
                "Count Inventory Time": detail_row["correction_dt"],
                "Count Inventory User": detail_row["correction_by"],
                "Count Inventory Qty": detail_row["correction_qty"],
                "Count Inv Begin": detail_row["correction_beginning_qty"],
                "Count Inv End": detail_row["correction_ending_qty"],
                "Correction Event": detail_row["correction_event_type"],
                "Hours to Correction": detail_row["hours_until_correction"],
            }]),
            use_container_width=True,
            hide_index=True,
        )
    with raw_tab:
        st.dataframe(
            detail_row.drop(labels=["completed"], errors="ignore").rename("Value").reset_index().rename(columns={"index": "Field"}),
            use_container_width=True,
            hide_index=True,
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
        "Mark checked rows reviewed",
        type="primary",
    )

manual_correction_by = (
    None if manual_correction_user == "Keep Count Inventory user from table" else manual_correction_user
)
new_completed_pks = edited_review_df[
    (edited_review_df["completed"]) &
    (~edited_review_df["pk"].isin(completed_pks))
]["pk"]
new_completed = review_df[review_df["pk"].isin(new_completed_pks)]
if submitted_completion:
    with st.spinner("Saving completed rows..."):
        inserted = save_completed_rows(new_completed, completion_note, manual_correction_by)
    if inserted:
        load_completed_audit_pks.clear()
        st.success(f"Marked {inserted} row(s) reviewed.")
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

st.subheader("Verify User Catcher Summary")
st.caption("This highlights who is finding count mismatches during Verify Inventory.")
if filtered.empty:
    st.info("No rows match the current filters.")
else:
    catcher_summary = (
        filtered.groupby("user_name")
        .agg(
            caught_mismatches=("pk", "count"),
            total_qty_caught=("abs_discrepancy_qty", "sum"),
            avg_qty_caught=("abs_discrepancy_qty", "mean"),
            strong_refill_patterns=("evidence_status", lambda s: (s == "Strong refill-entry pattern").sum()),
            possible_refill_patterns=("evidence_status", lambda s: (s == "Possible refill-entry pattern").sum()),
            clean_refill_chains=("inventory_events_since_refill", lambda s: (s == 0).sum()),
            unique_meds=("med_id", "nunique"),
            unique_devices=("device", "nunique"),
        )
        .reset_index()
        .sort_values(["caught_mismatches", "total_qty_caught"], ascending=False)
    )
    st.dataframe(
        catcher_summary,
        use_container_width=True,
        hide_index=True,
        column_config={
            "user_name": st.column_config.TextColumn("Verify User"),
            "caught_mismatches": st.column_config.NumberColumn("Caught Mismatches", format="%d"),
            "total_qty_caught": st.column_config.NumberColumn("Total Qty Caught", format="%.0f"),
            "avg_qty_caught": st.column_config.NumberColumn("Avg Qty Caught", format="%.1f"),
            "strong_refill_patterns": st.column_config.NumberColumn("Strong Refill Patterns", format="%d"),
            "possible_refill_patterns": st.column_config.NumberColumn("Possible Refill Patterns", format="%d"),
            "clean_refill_chains": st.column_config.NumberColumn("Clean Chains", format="%d"),
            "unique_meds": st.column_config.NumberColumn("Meds", format="%d"),
            "unique_devices": st.column_config.NumberColumn("Devices", format="%d"),
        },
    )
    st.download_button(
        "Export Verify User Catcher Summary to Excel",
        data=to_excel_bytes(catcher_summary),
        file_name="verify_count_audit_catcher_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.divider()

st.subheader("Prior Refill User Summary")
st.caption("This counts how many rows each prior refill/load user has in each evidence category.")
if filtered.empty:
    st.info("No rows match the current filters.")
else:
    evidence_counts = (
        filtered.pivot_table(
            index="prior_refill_by",
            columns="evidence_status",
            values="pk",
            aggfunc="count",
            fill_value=0,
        )
        .reset_index()
    )
    for col in EVIDENCE_OPTIONS:
        if col not in evidence_counts.columns:
            evidence_counts[col] = 0
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
    user_summary = user_summary.merge(evidence_counts, on="prior_refill_by", how="left")
    evidence_count_cols = [col for col in EVIDENCE_OPTIONS if col in user_summary.columns]
    for col in evidence_count_cols:
        user_summary[col] = user_summary[col].fillna(0).astype(int)
    user_summary = user_summary.sort_values(
        ["Strong refill-entry pattern", "Possible refill-entry pattern", "mismatch_count", "total_qty_off"],
        ascending=False,
    )
    st.dataframe(
        user_summary,
        use_container_width=True,
        hide_index=True,
        column_config={
            "prior_refill_by": st.column_config.TextColumn("Prior Refill User"),
            "mismatch_count": st.column_config.NumberColumn("Mismatches", format="%d"),
            "Strong refill-entry pattern": st.column_config.NumberColumn("Strong", format="%d"),
            "Possible refill-entry pattern": st.column_config.NumberColumn("Possible", format="%d"),
            "Needs inventory-chain review": st.column_config.NumberColumn("Chain Review", format="%d"),
            "Missing pull data": st.column_config.NumberColumn("Missing Pull", format="%d"),
            "Refill matched pull": st.column_config.NumberColumn("Matched Pull", format="%d"),
            "No prior refill found": st.column_config.NumberColumn("No Prior", format="%d"),
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

    st.divider()
    st.subheader("Automated Coaching Action Plan")
    st.caption(
        "This reads the filtered table and turns strong/possible refill-entry patterns into a plain-English worklist."
    )
    min_action_example_qty = st.number_input(
        "Minimum quantity off for action-plan examples",
        min_value=1,
        max_value=50,
        value=3,
        step=1,
        help="Counts still include all visible rows, but examples will prefer rows at or above this quantity off.",
        key="verify_audit_action_plan_min_qty",
    )
    action_plan = build_coaching_actions(filtered, user_summary, int(min_action_example_qty))
    if action_plan.empty:
        st.info("No strong or possible refill-entry patterns are visible with the current filters.")
    else:
        st.dataframe(
            action_plan,
            use_container_width=True,
            hide_index=True,
            column_config={
                "priority": st.column_config.TextColumn("Priority"),
                "prior_refill_user": st.column_config.TextColumn("User"),
                "strong": st.column_config.NumberColumn("Strong", format="%d"),
                "possible": st.column_config.NumberColumn("Possible", format="%d"),
                "chain_review": st.column_config.NumberColumn("Chain Review", format="%d"),
                "total_rows": st.column_config.NumberColumn("Total Rows", format="%d"),
                "suggested_action": st.column_config.TextColumn("What To Do"),
                "example_evidence": st.column_config.TextColumn("Examples To Review"),
            },
        )
        st.download_button(
            "Export Coaching Action Plan to Excel",
            data=to_excel_bytes(action_plan),
            file_name="verify_count_audit_action_plan.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        strong_action_count = int((action_plan["strong"].fillna(0).astype(int) > 0).sum())
        if strong_action_count > 0:
            if st.button(
                f"Add {strong_action_count} strong pattern item(s) to Management Coaching",
                key="verify_audit_send_strong_to_management",
            ):
                saved_count = save_strong_patterns_to_management(action_plan, filtered, start_date, end_date)
                load_completed_audit_pks.clear()
                st.success(
                    f"Management Coaching updated for {saved_count} staff member"
                    f"{'s' if saved_count != 1 else ''}."
                )
        else:
            st.info("No strong refill-entry pattern is visible to add to Management Coaching.")

        st.markdown("#### Coaching Drilldown")
        selected_action_user = st.selectbox(
            "Show occurrences for user",
            action_plan["prior_refill_user"].tolist(),
            key="verify_audit_action_plan_user_drilldown",
        )
        user_occurrences = filtered[
            (filtered["prior_refill_by"] == selected_action_user) &
            (filtered["evidence_status"].isin(["Strong refill-entry pattern", "Possible refill-entry pattern"]))
        ].sort_values(["evidence_status", "abs_discrepancy_qty"], ascending=[True, False])
        above_threshold = user_occurrences["abs_discrepancy_qty"] >= min_action_example_qty
        st.caption(
            f"Showing all {len(user_occurrences):,} strong/possible occurrence(s) for this user. "
            f"{int(above_threshold.sum()):,} meet the current action-plan example threshold of {int(min_action_example_qty)}."
        )

        drilldown_columns = [
            "evidence_status", "dt", "device", "med_id", "med_desc", "user_name",
            "discrepancy_qty", "qty", "prior_refill_dt", "prior_refill_event_type", "prior_refill_qty",
            "refill_date_pull_qty", "refill_qty_vs_pull", "verify_date_pull_qty",
            "verify_qty_vs_pull", "inventory_events_since_refill", "evidence_reason",
        ]
        st.dataframe(
            user_occurrences[drilldown_columns],
            use_container_width=True,
            hide_index=True,
            column_config={
                "evidence_status": st.column_config.TextColumn("Evidence"),
                "dt": st.column_config.DatetimeColumn("Verify Time", format="MM/DD/YY HH:mm"),
                "device": st.column_config.TextColumn("Pyxis"),
                "med_id": st.column_config.TextColumn("Med ID"),
                "med_desc": st.column_config.TextColumn("Medication"),
                "user_name": st.column_config.TextColumn("Verify User"),
                "discrepancy_qty": st.column_config.NumberColumn("Qty Off", format="%.0f"),
                "qty": st.column_config.NumberColumn("Verify Qty", format="%.0f"),
                "prior_refill_dt": st.column_config.DatetimeColumn("Prior Refill/Load Time", format="MM/DD/YY HH:mm"),
                "prior_refill_event_type": st.column_config.TextColumn("Prior Event"),
                "prior_refill_qty": st.column_config.NumberColumn("Refill/Load Entered", format="%.0f"),
                "refill_date_pull_qty": st.column_config.NumberColumn("Pull Qty", format="%.0f"),
                "refill_qty_vs_pull": st.column_config.NumberColumn("Refill/Load vs Pull", format="%.0f"),
                "verify_date_pull_qty": st.column_config.NumberColumn("Verify-Date Pull", format="%.0f"),
                "verify_qty_vs_pull": st.column_config.NumberColumn("Verify vs Pull", format="%.0f"),
                "inventory_events_since_refill": st.column_config.NumberColumn("Inv Events Since", format="%d"),
                "evidence_reason": st.column_config.TextColumn("Why It Matched"),
            },
        )
        st.download_button(
            f"Export {selected_action_user} Occurrences to Excel",
            data=to_excel_bytes(user_occurrences[drilldown_columns]),
            file_name=f"verify_count_audit_{selected_action_user.replace(',', '').replace(' ', '_').lower()}_occurrences.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

