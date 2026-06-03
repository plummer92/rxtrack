import io
import json
import re
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
REFILL_OCCURRENCE_LOG_TABLE = "refill_entry_occurrence_log"
MANUAL_CORRECTION_USERS = ["Jared Wolfe"]
KNOWN_PHARMACY_COLLEAGUES = [
    "Koehler, Dave",
    "Gartshore, Taylor",
    "Simmons, Amber",
    "Todd, Samantha",
    "Torbert, Jake",
    "Sloman, Lindsey",
    "Gathard, Sydney",
    "Strader, Brandi",
    "Gall, Mallory",
    "Ho, Ali",
    "Kain, Amy",
    "Mauney, Sarah",
    "Clay, Nicholas",
    "Keane, Bronagh",
    "Madonia, Tori",
    "Sprehe, Rebekah",
    "Brockhouse, Jamie",
    "Voigt, Ashley",
    "Ozere, Kara",
    "Dillon, Austin",
    "Barker, Brett",
    "Wunderlich, Ben",
    "Zitzke, Jessica",
    "Vizral, Isaac",
    "Zhu, Michael",
    "Gardner, Sara",
    "McNeely, Bryant",
    "Bernstein, Shirley",
    "Bhandari, Shiva",
    "Ryan, Alden",
    "Wunderlich, Emily",
    "Jabusch, Daniel",
    "Gonzalez, Tiffany",
    "Wilson, Ian",
    "Frazier, Liz",
    "Smith, Lori",
    "Ridley, Erica",
    "Neale, Sara",
    "Schleeper, Cady",
    "Evanich, David",
    "Haley, Lu Ann",
    "Dykstra, Javier",
    "Javier Dykstra",
    "Kincaid, Shelby",
    "Smith, Matthew",
    "Klosowski, Joe",
    "Davidson, Tamra",
    "Cole, Jaycie",
    "Wallace, Lillian",
    "Wolfe, Jared",
    "Smith, Kati",
    "Moquia, Wilmar",
    "Lorenson, Jessica",
    "Torricelli, Bill",
    "Patterson, Berni",
    "Kaylor, Heather",
    "Voudrie, Lauren",
    "Allen, Logan",
    "Shields, Melissa",
    "Spain, Dee",
    "Dorsey, Latessa",
    "Valenti, Kris",
    "Scott, Jason",
    "Willman, Dave",
    "Saleh, Shaima",
    "Schuerman, Adrean",
    "Morgan, Mia",
    "Yates, Christine",
    "Underwood, Rick",
    "Little, Erica",
    "Simmons, Nicole",
    "Cady, Elizabeth",
    "Stewart, Anna",
    "Harris, Megan",
    "Carty, Crystal",
    "Reynolds, Morissa",
    "Alishaqi, Rand",
    "Shultz, Richard",
    "Garman, Krista",
    "Foley, Jaimes",
    "Lax, Kristin",
]
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


def pharmacy_colleague_keys() -> set[str]:
    return {App.normalize_name(name) for name in KNOWN_PHARMACY_COLLEAGUES if App.normalize_name(name)}


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
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, np.generic):
        return value.item()
    return value


def display_value(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def ensure_refill_occurrence_log_table():
    sql = text(f"""
        CREATE TABLE IF NOT EXISTS {REFILL_OCCURRENCE_LOG_TABLE} (
            occurrence_key TEXT PRIMARY KEY,
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            occurrence_status TEXT DEFAULT 'Needs Review',
            occurrence_user TEXT,
            refill_dt TIMESTAMP,
            device TEXT,
            pocket TEXT,
            med_id TEXT,
            med_desc TEXT,
            event_type TEXT,
            entered_qty FLOAT,
            beginning_qty FLOAT,
            ending_qty FLOAT,
            matched_pull_qty FLOAT,
            total_pull_qty FLOAT,
            entered_vs_matched_pull FLOAT,
            expected_ending_qty FLOAT,
            expected_ending_variance FLOAT,
            pull_users TEXT,
            first_pull_dt TIMESTAMP,
            last_pull_dt TIMESTAMP,
            note TEXT,
            source_payload_json TEXT
        )
    """)
    with engine.begin() as conn:
        conn.execute(sql)
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS occurrence_status TEXT DEFAULT 'Needs Review'"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS occurrence_user TEXT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS refill_dt TIMESTAMP"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS device TEXT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS pocket TEXT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS med_id TEXT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS med_desc TEXT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS event_type TEXT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS entered_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS beginning_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS ending_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS matched_pull_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS total_pull_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS entered_vs_matched_pull FLOAT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS expected_ending_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS expected_ending_variance FLOAT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS pull_users TEXT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS first_pull_dt TIMESTAMP"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS last_pull_dt TIMESTAMP"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS note TEXT"))
        conn.execute(text(f"ALTER TABLE {REFILL_OCCURRENCE_LOG_TABLE} ADD COLUMN IF NOT EXISTS source_payload_json TEXT"))


@st.cache_data(ttl=60)
def load_refill_occurrence_keys() -> set[str]:
    ensure_refill_occurrence_log_table()
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT occurrence_key FROM {REFILL_OCCURRENCE_LOG_TABLE}")).fetchall()
    return {str(row[0]) for row in rows}


@st.cache_data(ttl=60)
def load_refill_occurrence_log(start, end) -> pd.DataFrame:
    ensure_refill_occurrence_log_table()
    sql = text(f"""
        SELECT *
        FROM {REFILL_OCCURRENCE_LOG_TABLE}
        WHERE refill_dt::date BETWEEN :start AND :end
        ORDER BY refill_dt DESC, logged_at DESC
    """)
    try:
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        if df.empty:
            return df
        for col in ["logged_at", "refill_dt", "first_pull_dt", "last_pull_dt"]:
            df[col] = pd.to_datetime(df[col], errors="coerce")
        for col in [
            "entered_qty", "beginning_qty", "ending_qty", "matched_pull_qty",
            "total_pull_qty", "entered_vs_matched_pull", "expected_ending_qty",
            "expected_ending_variance",
        ]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception as exc:
        st.warning(f"[load_refill_occurrence_log] {exc}")
        return pd.DataFrame()


def refill_occurrence_key(refill_row: pd.Series) -> str:
    refill_pk = str(refill_row.get("pk") or "").strip()
    if refill_pk:
        return f"rc-refill:{refill_pk}"
    refill_dt = pd.to_datetime(refill_row.get("dt"), errors="coerce")
    refill_label = refill_dt.isoformat() if pd.notna(refill_dt) else "unknown-time"
    return (
        f"rc-refill:{refill_label}:"
        f"{str(refill_row.get('device') or '').strip()}:"
        f"{str(refill_row.get('drawer_subdrawer_pocket') or '').strip()}:"
        f"{str(refill_row.get('med_id') or '').strip()}"
    )


def save_refill_occurrence(
    refill_row: pd.Series,
    pull_evidence: pd.DataFrame,
    matched_pull_qty: float,
    total_pull_qty: float,
    note: str = "",
) -> int:
    ensure_refill_occurrence_log_table()
    entered_qty = pd.to_numeric(refill_row.get("qty"), errors="coerce")
    beginning_qty = pd.to_numeric(refill_row.get("beginning_qty"), errors="coerce")
    ending_qty = pd.to_numeric(refill_row.get("ending_qty"), errors="coerce")
    expected_ending_qty = (
        beginning_qty + matched_pull_qty
        if pd.notna(beginning_qty) and pd.notna(matched_pull_qty)
        else np.nan
    )
    expected_ending_variance = (
        ending_qty - expected_ending_qty
        if pd.notna(ending_qty) and pd.notna(expected_ending_qty)
        else np.nan
    )
    matched_pulls = pull_evidence[pull_evidence["destination_match"]].copy() if not pull_evidence.empty else pd.DataFrame()
    pull_source = matched_pulls if not matched_pulls.empty else pull_evidence
    pull_users = ""
    first_pull_dt = pd.NaT
    last_pull_dt = pd.NaT
    if not pull_source.empty:
        pull_users = ", ".join(sorted(pull_source["user_name"].dropna().astype(str).unique()))
        first_pull_dt = pull_source["dt"].min()
        last_pull_dt = pull_source["dt"].max()

    evidence_payload = {
        "refill": {
            "pk": str(refill_row.get("pk") or ""),
            "dt": str(refill_row.get("dt") or ""),
            "user_name": str(refill_row.get("user_name") or ""),
            "user_type": str(refill_row.get("user_type") or ""),
            "device": str(refill_row.get("device") or ""),
            "pocket": str(refill_row.get("drawer_subdrawer_pocket") or ""),
            "event_type": str(refill_row.get("event_type") or ""),
            "med_id": str(refill_row.get("med_id") or ""),
            "med_desc": str(refill_row.get("med_desc") or ""),
            "entered_qty": db_value(entered_qty),
            "beginning_qty": db_value(beginning_qty),
            "ending_qty": db_value(ending_qty),
        },
        "pull_evidence": pull_evidence.to_dict("records") if not pull_evidence.empty else [],
    }
    payload = {
        "occurrence_key": refill_occurrence_key(refill_row),
        "occurrence_user": str(refill_row.get("user_name") or "").strip(),
        "refill_dt": db_value(pd.to_datetime(refill_row.get("dt"), errors="coerce")),
        "device": str(refill_row.get("device") or "").strip(),
        "pocket": str(refill_row.get("drawer_subdrawer_pocket") or "").strip(),
        "med_id": str(refill_row.get("med_id") or "").strip(),
        "med_desc": str(refill_row.get("med_desc") or "").strip(),
        "event_type": str(refill_row.get("event_type") or "").strip(),
        "entered_qty": db_value(entered_qty),
        "beginning_qty": db_value(beginning_qty),
        "ending_qty": db_value(ending_qty),
        "matched_pull_qty": db_value(matched_pull_qty),
        "total_pull_qty": db_value(total_pull_qty),
        "entered_vs_matched_pull": db_value(entered_qty - matched_pull_qty if pd.notna(entered_qty) else np.nan),
        "expected_ending_qty": db_value(expected_ending_qty),
        "expected_ending_variance": db_value(expected_ending_variance),
        "pull_users": pull_users,
        "first_pull_dt": db_value(first_pull_dt),
        "last_pull_dt": db_value(last_pull_dt),
        "note": note,
        "source_payload_json": json.dumps(evidence_payload, default=str),
    }
    sql = text(f"""
        INSERT INTO {REFILL_OCCURRENCE_LOG_TABLE} (
            occurrence_key, occurrence_user, refill_dt, device, pocket, med_id,
            med_desc, event_type, entered_qty, beginning_qty, ending_qty,
            matched_pull_qty, total_pull_qty, entered_vs_matched_pull,
            expected_ending_qty, expected_ending_variance, pull_users,
            first_pull_dt, last_pull_dt, note, source_payload_json
        )
        VALUES (
            :occurrence_key, :occurrence_user, :refill_dt, :device, :pocket, :med_id,
            :med_desc, :event_type, :entered_qty, :beginning_qty, :ending_qty,
            :matched_pull_qty, :total_pull_qty, :entered_vs_matched_pull,
            :expected_ending_qty, :expected_ending_variance, :pull_users,
            :first_pull_dt, :last_pull_dt, :note, :source_payload_json
        )
        ON CONFLICT (occurrence_key) DO UPDATE SET
            logged_at = CURRENT_TIMESTAMP,
            occurrence_user = EXCLUDED.occurrence_user,
            refill_dt = EXCLUDED.refill_dt,
            device = EXCLUDED.device,
            pocket = EXCLUDED.pocket,
            med_id = EXCLUDED.med_id,
            med_desc = EXCLUDED.med_desc,
            event_type = EXCLUDED.event_type,
            entered_qty = EXCLUDED.entered_qty,
            beginning_qty = EXCLUDED.beginning_qty,
            ending_qty = EXCLUDED.ending_qty,
            matched_pull_qty = EXCLUDED.matched_pull_qty,
            total_pull_qty = EXCLUDED.total_pull_qty,
            entered_vs_matched_pull = EXCLUDED.entered_vs_matched_pull,
            expected_ending_qty = EXCLUDED.expected_ending_qty,
            expected_ending_variance = EXCLUDED.expected_ending_variance,
            pull_users = EXCLUDED.pull_users,
            first_pull_dt = EXCLUDED.first_pull_dt,
            last_pull_dt = EXCLUDED.last_pull_dt,
            note = EXCLUDED.note,
            source_payload_json = EXCLUDED.source_payload_json
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, payload)
    save_refill_occurrence_to_management(payload)
    return result.rowcount or 0


def save_refill_occurrence_to_management(payload: dict) -> int:
    staff_name = str(payload.get("occurrence_user") or "").strip()
    if not staff_name:
        return 0

    refill_dt = pd.to_datetime(payload.get("refill_dt"), errors="coerce")
    refill_label = refill_dt.strftime("%m/%d/%y %H:%M") if pd.notna(refill_dt) else "unknown time"
    summary = (
        f"Refill-entry occurrence logged for {staff_name}.\n\n"
        f"Medication: {payload.get('med_desc') or payload.get('med_id')}\n"
        f"Device: {payload.get('device')}\n"
        f"Pocket: {payload.get('pocket')}\n"
        f"Refill time: {refill_label}\n"
        f"Entered refill qty: {fmt_qty(payload.get('entered_qty'))}\n"
        f"Matched carousel pull qty: {fmt_qty(payload.get('matched_pull_qty'))}\n"
        f"Entered minus matched pull: {fmt_qty(payload.get('entered_vs_matched_pull'))}\n"
        f"Expected ending count: {fmt_qty(payload.get('expected_ending_qty'))}\n"
        f"Actual ending count: {fmt_qty(payload.get('ending_qty'))}\n"
        f"Actual minus expected ending: {fmt_qty(payload.get('expected_ending_variance'))}\n\n"
        f"Note: {payload.get('note') or 'No note entered.'}"
    )
    next_steps = (
        "Review the refill paper trail with the colleague. Coaching focus: enter the actual quantity loaded/refilled "
        "from the carousel pull, not the final pocket count or an accidentally keyed value. Document follow-up after review."
    )
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
    management_payload = {
        "staff_name": staff_name,
        "topic": "Discrepancy",
        "summary": summary,
        "next_steps": next_steps,
        "source_page": "Discrepancy Deep Dive - Refill Entry Occurrence",
        "source_key": f"refill-entry-occurrence:{payload.get('occurrence_key')}",
        "source_payload_json": payload.get("source_payload_json") or "{}",
    }
    try:
        with engine.begin() as conn:
            result = conn.execute(sql, management_payload)
        return result.rowcount or 0
    except Exception as exc:
        st.warning(f"[save_refill_occurrence_to_management] {exc}")
        return 0


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


def build_clinical_chain_actions(filtered_df: pd.DataFrame, min_example_qty_off: int = 3) -> pd.DataFrame:
    if filtered_df.empty or "clinical_events_since_refill" not in filtered_df.columns:
        return pd.DataFrame()
    clinical_rows = filtered_df[
        filtered_df["clinical_events_since_refill"].fillna(0).gt(0)
        & filtered_df["prior_refill_by"].fillna("").astype(str).ne("")
        & filtered_df["prior_refill_by"].ne("No prior refill found")
    ].copy()
    if clinical_rows.empty:
        return pd.DataFrame()

    action_rows = []
    for user_name, user_rows in clinical_rows.groupby("prior_refill_by"):
        example_rows = user_rows[user_rows["abs_discrepancy_qty"] >= min_example_qty_off]
        if example_rows.empty:
            example_rows = user_rows
        examples = []
        for _, row in example_rows.sort_values("abs_discrepancy_qty", ascending=False).head(3).iterrows():
            examples.append(
                f"{row['med_id']} at {row['device']} on {row['dt']:%m/%d %H:%M}: "
                f"pharmacy {str(row.get('prior_refill_event_type') or 'refill/load').lower()} entered "
                f"{fmt_qty(row['prior_refill_qty'])}; clinical chain had "
                f"{int(row.get('clinical_vends_since_refill') or 0)} vend(s), "
                f"{int(row.get('clinical_wastes_since_refill') or 0)} waste(s); "
                f"later verify was off {fmt_qty(row['discrepancy_qty'])}."
            )
        action_rows.append({
            "priority": "Review chain before coaching",
            "prior_refill_user": user_name,
            "clinical_chain_rows": len(user_rows),
            "clinical_events": int(user_rows["clinical_events_since_refill"].fillna(0).sum()),
            "clinical_vends": int(user_rows["clinical_vends_since_refill"].fillna(0).sum()),
            "clinical_wastes": int(user_rows["clinical_wastes_since_refill"].fillna(0).sum()),
            "total_qty_off": float(user_rows["abs_discrepancy_qty"].fillna(0).sum()),
            "suggested_action": (
                "Do not treat this as a clean refill-entry coaching signal until the clinical vend/waste chain is reviewed."
            ),
            "example_evidence": "\n".join(examples),
        })
    return pd.DataFrame(action_rows).sort_values(
        ["clinical_chain_rows", "clinical_events", "total_qty_off"],
        ascending=False,
    )


def save_clinical_chain_to_management(clinical_plan: pd.DataFrame, filtered_df: pd.DataFrame, audit_start, audit_end) -> int:
    if clinical_plan.empty:
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
    for _, plan_row in clinical_plan.iterrows():
        staff_name = str(plan_row["prior_refill_user"]).strip()
        source_key = f"verify-count-audit-clinical-chain:{staff_name.lower()}"
        rows = filtered_df[
            (filtered_df["prior_refill_by"].astype(str).str.strip() == staff_name)
            & filtered_df["clinical_events_since_refill"].fillna(0).gt(0)
        ].sort_values("abs_discrepancy_qty", ascending=False)
        evidence_rows = []
        for _, detail in rows.iterrows():
            verify_time = pd.to_datetime(detail.get("dt"), errors="coerce")
            prior_time = pd.to_datetime(detail.get("prior_refill_dt"), errors="coerce")
            clinical_time = pd.to_datetime(detail.get("last_clinical_event_dt"), errors="coerce")
            evidence_rows.append({
                "Verify Time": verify_time.strftime("%m/%d/%y %H:%M") if pd.notna(verify_time) else "",
                "Pyxis": str(detail.get("device") or ""),
                "Med ID": str(detail.get("med_id") or ""),
                "Medication": str(detail.get("med_desc") or ""),
                "Pharmacy Refill Time": prior_time.strftime("%m/%d/%y %H:%M") if pd.notna(prior_time) else "",
                "Pharmacy User": str(detail.get("prior_refill_by") or ""),
                "Refill Entered": db_value(detail.get("prior_refill_qty")),
                "Pull Qty": db_value(detail.get("refill_date_pull_qty")),
                "Clinical Events": db_value(detail.get("clinical_events_since_refill")),
                "Clinical Vends": db_value(detail.get("clinical_vends_since_refill")),
                "Clinical Wastes": db_value(detail.get("clinical_wastes_since_refill")),
                "Last Clinical Time": clinical_time.strftime("%m/%d/%y %H:%M") if pd.notna(clinical_time) else "",
                "Last Clinical User": str(detail.get("last_clinical_event_by") or ""),
                "Last Clinical Event": str(detail.get("last_clinical_event_type") or ""),
                "Later Verify Off": db_value(detail.get("discrepancy_qty")),
                "Why It Matched": str(detail.get("evidence_reason") or ""),
            })
        summary = (
            f"Verify Count Audit found {int(plan_row['clinical_chain_rows'])} discrepancy row(s) for {staff_name} "
            f"where clinical vend/waste activity occurred between the pharmacy refill/load and the later verify mismatch "
            f"between {audit_start} and {audit_end}.\n\n"
            f"Examples:\n{plan_row.get('example_evidence') or 'Review the clinical-chain evidence table below.'}"
        )
        next_steps = (
            "Review the chain before coaching. Confirm whether the mismatch is explained by clinical vend/waste activity, "
            "then coach only if the refill/load entry still appears inaccurate after that review."
        )
        payload.append({
            "staff_name": staff_name,
            "topic": "Discrepancy",
            "summary": summary,
            "next_steps": next_steps,
            "source_page": "Verify Count Audit - Clinical Chain",
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
def load_clinical_count_control_audit(start, end):
    """Clinical RC rows that show whether non-pharmacy users are changing Pyxis counts."""
    try:
        sql = text("""
            SELECT
                pk,
                dt,
                user_name,
                user_type,
                care_area_name,
                location,
                station_name AS device,
                transaction_type AS event_type,
                med_id,
                med_desc,
                drawer_subdrawer_pocket,
                qty,
                beginning_qty,
                ending_qty,
                discrepancy_difference,
                COALESCE(NULLIF(discrepancy_reason, ''), discrepancy_resolution_desc) AS discrepancy_reason,
                correction_quantity_before,
                correction_quantity_after,
                correction,
                resolution_user,
                waste_amount,
                source_filename
            FROM audit_transaction_detail_rc
            WHERE dt::date BETWEEN :start AND :end
              AND (
                    user_type ILIKE '%registered nurse%'
                 OR user_type ILIKE '%nurse%'
                 OR user_type ILIKE '%anesthesia%'
                 OR user_type ILIKE '%respiratory%'
              )
            ORDER BY dt DESC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        if df.empty:
            return df

        df = df.copy()
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        text_cols = [
            "user_name", "user_type", "care_area_name", "location", "device", "event_type",
            "med_id", "med_desc", "drawer_subdrawer_pocket", "discrepancy_reason",
            "correction", "resolution_user", "source_filename",
        ]
        for col in text_cols:
            df[col] = df[col].fillna("").astype(str).str.strip()
        for col in [
            "qty", "beginning_qty", "ending_qty", "discrepancy_difference",
            "correction_quantity_before", "correction_quantity_after", "waste_amount",
        ]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        known_pharmacy_keys = pharmacy_colleague_keys()
        df["user_match_key"] = df["user_name"].apply(App.normalize_name)
        df["known_pharmacy_colleague"] = df["user_match_key"].isin(known_pharmacy_keys)

        event_text = df["event_type"].str.lower()
        normal_vend = event_text.str.contains(r"vend|remove", regex=True, na=False)
        normal_waste = event_text.str.contains(r"waste", regex=True, na=False)
        refill_load = event_text.str.contains(
            r"restock|refill|replenish|\bload\b|unload|outdate|empty",
            regex=True,
            na=False,
        )
        inventory_control = event_text.str.contains(
            r"verify|verified|inventory|count|adjust|correct|correction",
            regex=True,
            na=False,
        )
        has_begin_end = df["beginning_qty"].notna() & df["ending_qty"].notna()
        begin_end_changed = has_begin_end & df["beginning_qty"].sub(df["ending_qty"]).abs().gt(0.001)
        discrepancy_changed = df["discrepancy_difference"].fillna(0).abs().gt(0.001)
        correction_changed = (
            df["correction_quantity_before"].notna()
            & df["correction_quantity_after"].notna()
            & df["correction_quantity_before"].sub(df["correction_quantity_after"]).abs().gt(0.001)
        )
        df["count_changed"] = begin_end_changed | discrepancy_changed | correction_changed
        df["count_delta"] = np.where(
            has_begin_end,
            df["ending_qty"].fillna(0) - df["beginning_qty"].fillna(0),
            df["discrepancy_difference"].fillna(0),
        )

        return_event = event_text.str.contains(r"return", regex=True, na=False)
        return_bin = (
            df["drawer_subdrawer_pocket"].str.contains(r"return\s*bin", case=False, regex=True, na=False)
            | df["drawer_subdrawer_pocket"].str.contains(r"\binternal\b", case=False, regex=True, na=False)
            | df["event_type"].str.contains(r"return\s*bin", case=False, regex=True, na=False)
        )
        pyxis_pocket_return = return_event & ~return_bin
        transfer_event = event_text.str.contains(r"\btransfer\b", regex=True, na=False)
        df["control_category"] = "Other clinical RC activity"
        df.loc[normal_vend | normal_waste, "control_category"] = "Expected clinical vend/waste"
        df.loc[return_bin, "control_category"] = "Expected clinical return bin"
        df.loc[pyxis_pocket_return, "control_category"] = "Clinical return to Pyxis pocket"
        df.loc[transfer_event, "control_category"] = "Clinical transfer"
        df.loc[inventory_control & ~df["count_changed"], "control_category"] = "Clinical inventory check, no change"
        df.loc[inventory_control & df["count_changed"], "control_category"] = "Clinical count correction"
        df.loc[
            df["count_changed"] & ~(normal_vend | normal_waste | inventory_control | refill_load | return_event | transfer_event),
            "control_category",
        ] = "Clinical count change outside vend"
        df.loc[refill_load, "control_category"] = "Clinical refill/load/unload"
        df["needs_review"] = df["control_category"].isin([
            "Clinical refill/load/unload",
            "Clinical count correction",
            "Clinical count change outside vend",
            "Clinical return to Pyxis pocket",
            "Clinical transfer",
            "Other clinical RC activity",
        ])
        return df.dropna(subset=["dt"])
    except Exception as exc:
        st.warning(f"[load_clinical_count_control_audit] {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_rc_pocket_timeline(device, med_id, pocket, selected_dt, selected_pk="", lookback_days=30, forward_days=3):
    """Load the RC paper trail for one device / med / pocket around a selected event."""
    try:
        selected_ts = pd.to_datetime(selected_dt, errors="coerce")
        if pd.isna(selected_ts):
            return pd.DataFrame()
        start_window = selected_ts - timedelta(days=lookback_days)
        end_window = selected_ts + timedelta(days=forward_days)
        sql = text("""
            SELECT
                pk,
                dt,
                user_name,
                user_type,
                care_area_name,
                location,
                station_name AS device,
                drawer_subdrawer_pocket,
                transaction_type AS event_type,
                med_id,
                med_desc,
                qty,
                beginning_qty,
                ending_qty,
                discrepancy_difference,
                COALESCE(NULLIF(discrepancy_reason, ''), discrepancy_resolution_desc) AS discrepancy_reason,
                correction_quantity_before,
                correction_quantity_after,
                correction,
                resolution_user,
                resolution_dt,
                waste_amount,
                waste_reason,
                witness_user_name,
                source_filename
            FROM audit_transaction_detail_rc
            WHERE dt BETWEEN :start_window AND :end_window
              AND UPPER(TRIM(station_name)) = UPPER(TRIM(:device))
              AND UPPER(TRIM(med_id)) = UPPER(TRIM(:med_id))
              AND (
                    COALESCE(:pocket, '') = ''
                 OR UPPER(TRIM(COALESCE(drawer_subdrawer_pocket, ''))) = UPPER(TRIM(:pocket))
              )
            ORDER BY dt ASC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(
                sql,
                conn,
                params={
                    "start_window": start_window,
                    "end_window": end_window,
                    "device": str(device or ""),
                    "med_id": str(med_id or ""),
                    "pocket": str(pocket or ""),
                },
            )
        if df.empty:
            return df
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["resolution_dt"] = pd.to_datetime(df["resolution_dt"], errors="coerce")
        for col in [
            "qty", "beginning_qty", "ending_qty", "discrepancy_difference",
            "correction_quantity_before", "correction_quantity_after", "waste_amount",
        ]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["count_delta"] = np.where(
            df["beginning_qty"].notna() & df["ending_qty"].notna(),
            df["ending_qty"].fillna(0) - df["beginning_qty"].fillna(0),
            df["discrepancy_difference"].fillna(0),
        )
        df["selected_event"] = df["pk"].astype(str).eq(str(selected_pk or ""))
        return df.dropna(subset=["dt"])
    except Exception as exc:
        st.warning(f"[load_rc_pocket_timeline] {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_pull_evidence_for_refill(device, med_id, refill_dt):
    """Load same-day pharmacy pull rows before a selected Pyxis refill/load."""
    try:
        refill_ts = pd.to_datetime(refill_dt, errors="coerce")
        if pd.isna(refill_ts):
            return pd.DataFrame()
        day_start = refill_ts.normalize()
        sql = text("""
            SELECT pk, queue_id, priority, dt, med_id, med_desc, destination, user_name, qty
            FROM pharmacy_orders
            WHERE dt >= :day_start
              AND dt <= :refill_ts
              AND UPPER(TRIM(med_id)) = UPPER(TRIM(:med_id))
              AND priority ILIKE '%pyxis%pull%'
            ORDER BY dt ASC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(
                sql,
                conn,
                params={
                    "day_start": day_start,
                    "refill_ts": refill_ts,
                    "med_id": str(med_id or ""),
                },
            )
        if df.empty:
            return df
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        for col in ["queue_id", "priority", "med_id", "med_desc", "destination", "user_name"]:
            df[col] = df[col].fillna("").astype(str).str.strip()
        device_text = str(device or "").strip().upper()
        df["destination_match"] = df["destination"].str.upper().str.contains(
            re.escape(device_text),
            regex=True,
            na=False,
        )
        return df.dropna(subset=["dt"])
    except Exception as exc:
        st.warning(f"[load_pull_evidence_for_refill] {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_carousel_return_processing(start, end):
    """Pharmacy workflow rows that represent returns processed back into carousel inventory."""
    try:
        sql = text("""
            SELECT pk, queue_id, priority, dt, med_id, med_desc, destination, user_name, qty
            FROM pharmacy_orders
            WHERE dt::date BETWEEN :start AND :end
              AND (
                    priority ILIKE '%return%'
                 OR priority ILIKE '%instant restock%'
              )
            ORDER BY dt DESC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"start": start, "end": end})
        if df.empty:
            return df
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        for col in ["queue_id", "priority", "med_id", "med_desc", "destination", "user_name"]:
            df[col] = df[col].fillna("").astype(str).str.strip()
        priority_text = df["priority"].str.lower()
        df["return_type"] = "Return"
        df.loc[priority_text.str.contains("instant", na=False) & priority_text.str.contains("return", na=False), "return_type"] = "Instant Return"
        df.loc[priority_text.str.contains("instant", na=False) & priority_text.str.contains("restock", na=False), "return_type"] = "Instant Restock"
        return df.dropna(subset=["dt"])
    except Exception as exc:
        st.warning(f"[load_carousel_return_processing] {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_return_source_paper_trail(med_id, return_dt, lookback_days=14):
    """Audit Transaction Detail RC rows that may explain where a carousel return came from."""
    try:
        return_ts = pd.to_datetime(return_dt, errors="coerce")
        if pd.isna(return_ts):
            return pd.DataFrame()
        start_window = return_ts - timedelta(days=lookback_days)
        sql = text("""
            SELECT
                pk,
                dt,
                user_name,
                user_type,
                care_area_name,
                location,
                station_name AS device,
                drawer_subdrawer_pocket,
                transaction_type AS event_type,
                med_id,
                med_desc,
                qty,
                beginning_qty,
                ending_qty,
                discrepancy_difference,
                COALESCE(NULLIF(discrepancy_reason, ''), discrepancy_resolution_desc) AS discrepancy_reason,
                correction,
                resolution_user,
                waste_amount,
                waste_reason,
                witness_user_name,
                source_filename
            FROM audit_transaction_detail_rc
            WHERE dt BETWEEN :start_window AND :return_ts
              AND UPPER(TRIM(med_id)) = UPPER(TRIM(:med_id))
            ORDER BY dt DESC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(
                sql,
                conn,
                params={
                    "start_window": start_window,
                    "return_ts": return_ts,
                    "med_id": str(med_id or ""),
                },
            )
        if df.empty:
            return df
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        for col in ["qty", "beginning_qty", "ending_qty", "discrepancy_difference", "waste_amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in [
            "user_name", "user_type", "care_area_name", "location", "device",
            "drawer_subdrawer_pocket", "event_type", "med_id", "med_desc",
            "discrepancy_reason", "correction", "resolution_user", "waste_reason",
            "witness_user_name", "source_filename",
        ]:
            df[col] = df[col].fillna("").astype(str).str.strip()
        df["minutes_before_carousel_return"] = (return_ts - df["dt"]).dt.total_seconds() / 60
        event_text = df["event_type"].str.lower()
        pocket_text = df["drawer_subdrawer_pocket"].str.lower()
        df["source_signal"] = "Other same-med RC activity"
        df.loc[event_text.str.contains("return", na=False) & pocket_text.str.contains(r"return\s*bin|internal", regex=True, na=False), "source_signal"] = "Returned to bin/internal"
        df.loc[event_text.str.contains("return", na=False) & ~pocket_text.str.contains(r"return\s*bin|internal", regex=True, na=False), "source_signal"] = "Returned to Pyxis pocket"
        df.loc[event_text.str.contains(r"empty|return\s*bin", regex=True, na=False), "source_signal"] = "Return bin emptied"
        df.loc[event_text.str.contains(r"\bunload\b|destock", regex=True, na=False), "source_signal"] = "Pyxis unload/destock"
        df.loc[event_text.str.contains(r"vend|remove", regex=True, na=False), "source_signal"] = "Clinical vend/removal"
        df.loc[event_text.str.contains(r"verify|inventory|count", regex=True, na=False), "source_signal"] = "Inventory/count check"
        return df.dropna(subset=["dt"])
    except Exception as exc:
        st.warning(f"[load_return_source_paper_trail] {exc}")
        return pd.DataFrame()


def render_carousel_return_paper_trail_section(start, end):
    with st.expander("Carousel Return Processing Paper Trail", expanded=False):
        st.caption(
            "Shows pharmacy workflow return rows that were processed into carousel inventory. "
            "Click a row to inspect same-med Audit Transaction Detail RC activity before that return."
        )
        returns = load_carousel_return_processing(start, end)
        if returns.empty:
            st.info("No carousel return-processing rows were found in Pharmacy Workflow for this date range.")
            return

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Return Rows", f"{len(returns):,}")
        r2.metric("Return Qty", f"{returns['qty'].sum():,.0f}")
        r3.metric("Meds", f"{returns['med_id'].nunique():,}")
        r4.metric("Users", f"{returns['user_name'].nunique():,}")

        f1, f2, f3 = st.columns([1, 1, 1])
        selected_return_types = f1.multiselect(
            "Return Type",
            sorted(returns["return_type"].dropna().unique()),
            default=sorted(returns["return_type"].dropna().unique()),
            key=f"carousel_return_type_filter_{start}_{end}",
        )
        selected_return_users = f2.multiselect(
            "Processed By",
            sorted(returns["user_name"].dropna().unique()),
            placeholder="All users",
            key=f"carousel_return_user_filter_{start}_{end}",
        )
        selected_return_destinations = f3.multiselect(
            "Carousel/Destination",
            sorted(returns["destination"].dropna().unique()),
            placeholder="All destinations",
            key=f"carousel_return_destination_filter_{start}_{end}",
        )

        visible_returns = returns.copy()
        if selected_return_types:
            visible_returns = visible_returns[visible_returns["return_type"].isin(selected_return_types)]
        else:
            visible_returns = visible_returns.iloc[0:0]
        if selected_return_users:
            visible_returns = visible_returns[visible_returns["user_name"].isin(selected_return_users)]
        if selected_return_destinations:
            visible_returns = visible_returns[visible_returns["destination"].isin(selected_return_destinations)]

        return_cols = ["pk", "dt", "return_type", "priority", "user_name", "destination", "med_id", "med_desc", "qty", "queue_id"]
        st.caption("Select a carousel return row to see where that med appears to have come from.")
        return_selection = st.dataframe(
            visible_returns[return_cols].head(2000),
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "pk": None,
                "dt": st.column_config.DatetimeColumn("Processed Time", format="MM/DD/YY HH:mm:ss"),
                "return_type": st.column_config.TextColumn("Return Type"),
                "priority": st.column_config.TextColumn("Priority"),
                "user_name": st.column_config.TextColumn("Processed By"),
                "destination": st.column_config.TextColumn("Destination"),
                "med_id": st.column_config.TextColumn("Med ID"),
                "med_desc": st.column_config.TextColumn("Medication"),
                "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
                "queue_id": st.column_config.TextColumn("Queue ID"),
            },
        )
        if len(visible_returns) > 2000:
            st.caption(f"Showing first 2,000 of {len(visible_returns):,} visible rows.")

        if len(return_selection.selection.rows) > 0:
            selected_return = visible_returns[return_cols].head(2000).iloc[return_selection.selection.rows[0]]
            st.markdown("#### Selected Return Source Trail")
            lookback_days = st.number_input(
                "Return source lookback days",
                min_value=1,
                max_value=90,
                value=14,
                step=1,
                key=f"carousel_return_source_lookback_{selected_return['pk']}_{start}_{end}",
            )
            source_trail = load_return_source_paper_trail(
                selected_return["med_id"],
                selected_return["dt"],
                int(lookback_days),
            )
            if source_trail.empty:
                st.info("No same-med RC rows were found before that carousel return in the selected lookback window.")
            else:
                source_summary = (
                    source_trail.groupby("source_signal")
                    .agg(rows=("pk", "count"), qty=("qty", "sum"), devices=("device", "nunique"), users=("user_name", "nunique"))
                    .reset_index()
                    .sort_values(["rows", "qty"], ascending=False)
                )
                st.dataframe(
                    source_summary,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "source_signal": st.column_config.TextColumn("Source Signal"),
                        "rows": st.column_config.NumberColumn("Rows", format="%d"),
                        "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
                        "devices": st.column_config.NumberColumn("Devices", format="%d"),
                        "users": st.column_config.NumberColumn("Users", format="%d"),
                    },
                )
                source_cols = [
                    "dt", "minutes_before_carousel_return", "source_signal", "user_name", "user_type",
                    "device", "drawer_subdrawer_pocket", "event_type", "qty",
                    "beginning_qty", "ending_qty", "discrepancy_difference",
                    "discrepancy_reason", "correction", "resolution_user",
                    "care_area_name", "location", "source_filename",
                ]
                st.dataframe(
                    source_trail[source_cols].head(2000),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "dt": st.column_config.DatetimeColumn("RC Time", format="MM/DD/YY HH:mm:ss"),
                        "minutes_before_carousel_return": st.column_config.NumberColumn("Min Before Return", format="%.0f"),
                        "source_signal": st.column_config.TextColumn("Signal"),
                        "drawer_subdrawer_pocket": st.column_config.TextColumn("Pocket"),
                        "event_type": st.column_config.TextColumn("RC Transaction"),
                        "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
                        "beginning_qty": st.column_config.NumberColumn("Begin", format="%.0f"),
                        "ending_qty": st.column_config.NumberColumn("End", format="%.0f"),
                        "discrepancy_difference": st.column_config.NumberColumn("Discrepancy Diff", format="%.0f"),
                    },
                )
                st.download_button(
                    "Export Selected Return Source Trail",
                    data=to_excel_bytes(source_trail),
                    file_name="selected_carousel_return_source_trail.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
        st.download_button(
            "Export Carousel Return Processing Rows",
            data=to_excel_bytes(visible_returns),
            file_name="carousel_return_processing_rows.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def render_clinical_count_control_audit(clinical_control_df: pd.DataFrame):
    with st.expander("Clinical Count Control Audit", expanded=not clinical_control_df.empty):
        st.caption(
            "Uses Audit Transaction Detail RC to check whether clinical users are only vending/wasting meds "
            "or returning to the return bin, versus returning meds to Pyxis pockets or changing counts."
        )
        if clinical_control_df.empty:
            st.info("No clinical Audit Transaction Detail RC rows were found for the selected date range.")
            return

        include_known_pharmacy = st.checkbox(
            "Include known pharmacy colleagues",
            value=False,
            help="Keep this off when auditing nursing/clinical count-control activity. Turn it on to inspect pharmacy colleagues that appear in RC clinical-user rows.",
            key=f"clinical_count_control_include_pharmacy_{start_date}_{end_date}",
        )
        known_pharmacy_rows = clinical_control_df["known_pharmacy_colleague"].fillna(False)
        scoped_control_df = clinical_control_df.copy() if include_known_pharmacy else clinical_control_df[~known_pharmacy_rows].copy()
        excluded_pharmacy_count = int(known_pharmacy_rows.sum())

        if excluded_pharmacy_count and not include_known_pharmacy:
            st.caption(f"Excluded {excluded_pharmacy_count:,} known pharmacy-colleague row(s) from the clinical audit.")

        if scoped_control_df.empty:
            st.info("No non-pharmacy clinical count-control rows were found after excluding known pharmacy colleagues.")
            return

        review_df = scoped_control_df[scoped_control_df["needs_review"]].copy()
        expected_df = scoped_control_df[~scoped_control_df["needs_review"]].copy()
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Clinical RC Rows", f"{len(scoped_control_df):,}")
        k2.metric("Needs Review", f"{len(review_df):,}")
        k3.metric("Pyxis Pocket Returns", f"{int((scoped_control_df['control_category'] == 'Clinical return to Pyxis pocket').sum()):,}")
        k4.metric("Count Corrections", f"{int((scoped_control_df['control_category'] == 'Clinical count correction').sum()):,}")
        k5.metric("Expected Activity", f"{len(expected_df):,}")

        category_summary = (
            scoped_control_df.groupby("control_category")
            .agg(
                rows=("pk", "count"),
                users=("user_name", "nunique"),
                devices=("device", "nunique"),
                meds=("med_id", "nunique"),
                total_abs_count_delta=("count_delta", lambda s: pd.to_numeric(s, errors="coerce").abs().sum()),
            )
            .reset_index()
            .sort_values(["rows", "total_abs_count_delta"], ascending=False)
        )
        st.dataframe(
            category_summary,
            use_container_width=True,
            hide_index=True,
            column_config={
                "control_category": st.column_config.TextColumn("Category"),
                "rows": st.column_config.NumberColumn("Rows", format="%d"),
                "users": st.column_config.NumberColumn("Users", format="%d"),
                "devices": st.column_config.NumberColumn("Devices", format="%d"),
                "meds": st.column_config.NumberColumn("Meds", format="%d"),
                "total_abs_count_delta": st.column_config.NumberColumn("Total Abs Count Change", format="%.0f"),
            },
        )

        c1, c2, c3 = st.columns([1, 1, 1])
        categories = sorted(scoped_control_df["control_category"].dropna().unique())
        selected_categories = c1.multiselect(
            "Categories",
            categories,
            default=[
                category for category in categories
                if category not in {
                    "Expected clinical vend/waste",
                    "Expected clinical return bin",
                    "Clinical inventory check, no change",
                }
            ],
            key=f"clinical_count_control_categories_{start_date}_{end_date}",
        )
        selected_users = c2.multiselect(
            "Clinical User",
            sorted(scoped_control_df["user_name"].dropna().unique()),
            placeholder="All users",
            key=f"clinical_count_control_users_{start_date}_{end_date}",
        )
        selected_devices = c3.multiselect(
            "Device",
            sorted(scoped_control_df["device"].dropna().unique()),
            placeholder="All devices",
            key=f"clinical_count_control_devices_{start_date}_{end_date}",
        )

        visible = scoped_control_df.copy()
        if selected_categories:
            visible = visible[visible["control_category"].isin(selected_categories)]
        else:
            visible = visible.iloc[0:0]
        if selected_users:
            visible = visible[visible["user_name"].isin(selected_users)]
        if selected_devices:
            visible = visible[visible["device"].isin(selected_devices)]

        st.caption(
            "Return-bin activity is expected. Returns to drawer/pocket locations are flagged because those put stock "
            "back into the Pyxis machine. Normal vend/waste rows can still change beginning/end quantity because "
            "the nurse removed medication."
        )
        display_cols = [
            "pk", "dt", "control_category", "known_pharmacy_colleague", "user_name", "user_type", "care_area_name", "location",
            "device", "drawer_subdrawer_pocket", "event_type", "med_id", "med_desc",
            "qty", "beginning_qty", "ending_qty", "count_delta", "discrepancy_difference",
            "discrepancy_reason", "correction", "resolution_user", "source_filename",
        ]
        visible_display = visible[display_cols].head(2000).copy()
        clinical_selection = st.dataframe(
            visible_display,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "pk": None,
                "dt": st.column_config.DatetimeColumn("Time", format="MM/DD/YY HH:mm:ss"),
                "control_category": st.column_config.TextColumn("Category"),
                "known_pharmacy_colleague": st.column_config.CheckboxColumn("Known Pharmacy"),
                "user_name": st.column_config.TextColumn("User"),
                "user_type": st.column_config.TextColumn("User Type"),
                "care_area_name": st.column_config.TextColumn("Care Area"),
                "drawer_subdrawer_pocket": st.column_config.TextColumn("Pocket"),
                "event_type": st.column_config.TextColumn("Transaction"),
                "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
                "beginning_qty": st.column_config.NumberColumn("Begin", format="%.0f"),
                "ending_qty": st.column_config.NumberColumn("End", format="%.0f"),
                "count_delta": st.column_config.NumberColumn("Count Delta", format="%.0f"),
                "discrepancy_difference": st.column_config.NumberColumn("Discrepancy Diff", format="%.0f"),
            },
        )
        if len(visible) > 2000:
            st.caption(f"Showing first 2,000 of {len(visible):,} visible rows. Export for the full filtered set.")

        if len(clinical_selection.selection.rows) > 0:
            selected_row = visible_display.iloc[clinical_selection.selection.rows[0]]
            st.markdown("#### Selected Event Paper Trail")
            st.caption(
                "Shows Audit Transaction Detail RC rows for the same device, medication, and pocket around the selected event."
            )
            lookback_days = st.number_input(
                "Timeline lookback days",
                min_value=1,
                max_value=180,
                value=30,
                step=1,
                key=f"clinical_count_control_timeline_lookback_{start_date}_{end_date}",
            )
            forward_days = st.number_input(
                "Timeline forward days",
                min_value=0,
                max_value=30,
                value=3,
                step=1,
                key=f"clinical_count_control_timeline_forward_{start_date}_{end_date}",
            )
            timeline = load_rc_pocket_timeline(
                selected_row["device"],
                selected_row["med_id"],
                selected_row["drawer_subdrawer_pocket"],
                selected_row["dt"],
                selected_row["pk"],
                int(lookback_days),
                int(forward_days),
            )
            if timeline.empty:
                st.info("No matching RC paper trail rows were found for that same device, med, and pocket.")
            else:
                selected_time = pd.to_datetime(selected_row["dt"], errors="coerce")
                timeline["minutes_from_selected"] = (
                    (timeline["dt"] - selected_time).dt.total_seconds() / 60
                    if pd.notna(selected_time)
                    else np.nan
                )
                trail_cols = [
                    "selected_event", "dt", "minutes_from_selected", "user_name", "user_type",
                    "event_type", "qty", "beginning_qty", "ending_qty", "count_delta",
                    "discrepancy_difference", "discrepancy_reason", "correction",
                    "resolution_user", "resolution_dt", "waste_amount", "waste_reason",
                    "witness_user_name", "care_area_name", "location", "source_filename",
                ]
                st.dataframe(
                    timeline[trail_cols],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "selected_event": st.column_config.CheckboxColumn("Selected"),
                        "dt": st.column_config.DatetimeColumn("Time", format="MM/DD/YY HH:mm:ss"),
                        "minutes_from_selected": st.column_config.NumberColumn("Min From Selected", format="%.1f"),
                        "event_type": st.column_config.TextColumn("Transaction"),
                        "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
                        "beginning_qty": st.column_config.NumberColumn("Begin", format="%.0f"),
                        "ending_qty": st.column_config.NumberColumn("End", format="%.0f"),
                        "count_delta": st.column_config.NumberColumn("Count Delta", format="%.0f"),
                        "discrepancy_difference": st.column_config.NumberColumn("Discrepancy Diff", format="%.0f"),
                        "resolution_dt": st.column_config.DatetimeColumn("Resolution Time", format="MM/DD/YY HH:mm:ss"),
                    },
                )

                refill_rows = timeline[
                    timeline["event_type"].fillna("").str.contains(
                        r"refill|restock|replenish|\bload\b",
                        case=False,
                        regex=True,
                    )
                ].sort_values("dt", ascending=False).copy()
                if not refill_rows.empty:
                    st.markdown("#### Pharmacy Pull Evidence For Refill")
                    st.caption(
                        "Pick a pharmacy refill/load row from the paper trail to compare the entered Pyxis refill quantity "
                        "against same-day pharmacy Pyxis-pull rows before that refill time."
                    )
                    refill_rows["refill_label"] = refill_rows.apply(
                        lambda row: (
                            f"{row['dt']:%m/%d %H:%M} | {row.get('user_name') or 'Unknown'} | "
                            f"{row.get('event_type') or 'Refill'} | entered {fmt_qty(row.get('qty'))} | "
                            f"{fmt_qty(row.get('beginning_qty'))} -> {fmt_qty(row.get('ending_qty'))}"
                        ),
                        axis=1,
                    )
                    selected_refill_label = st.selectbox(
                        "Refill/load row",
                        refill_rows["refill_label"].tolist(),
                        key=f"clinical_count_control_refill_drilldown_{selected_row['pk']}_{start_date}_{end_date}",
                    )
                    selected_refill = refill_rows[refill_rows["refill_label"].eq(selected_refill_label)].iloc[0]
                    pull_evidence = load_pull_evidence_for_refill(
                        selected_refill["device"],
                        selected_refill["med_id"],
                        selected_refill["dt"],
                    )
                    entered_qty = pd.to_numeric(selected_refill.get("qty"), errors="coerce")
                    if pull_evidence.empty:
                        st.info("No same-day pharmacy Pyxis-pull rows were found before that refill time for the same med ID.")
                    else:
                        destination_match_qty = pull_evidence.loc[pull_evidence["destination_match"], "qty"].sum()
                        total_pull_qty = pull_evidence["qty"].sum()
                        expected_ending_qty = (
                            pd.to_numeric(selected_refill.get("beginning_qty"), errors="coerce") + destination_match_qty
                            if pd.notna(pd.to_numeric(selected_refill.get("beginning_qty"), errors="coerce"))
                            else np.nan
                        )
                        actual_ending_qty = pd.to_numeric(selected_refill.get("ending_qty"), errors="coerce")
                        p1, p2, p3, p4, p5 = st.columns(5)
                        p1.metric("Entered Refill Qty", fmt_qty(entered_qty))
                        p2.metric("Matched Destination Pull Qty", fmt_qty(destination_match_qty))
                        p3.metric("All Same-Med Pull Qty", fmt_qty(total_pull_qty))
                        p4.metric(
                            "Entered - Matched Pull",
                            fmt_qty(entered_qty - destination_match_qty if pd.notna(entered_qty) else np.nan),
                        )
                        p5.metric(
                            "Expected Ending",
                            fmt_qty(expected_ending_qty),
                            delta=f"actual {fmt_qty(actual_ending_qty)}",
                        )
                        st.dataframe(
                            pull_evidence,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "dt": st.column_config.DatetimeColumn("Pull Time", format="MM/DD/YY HH:mm:ss"),
                                "destination_match": st.column_config.CheckboxColumn("Destination Match"),
                                "qty": st.column_config.NumberColumn("Pull Qty", format="%.0f"),
                                "priority": st.column_config.TextColumn("Priority"),
                                "destination": st.column_config.TextColumn("Destination"),
                                "user_name": st.column_config.TextColumn("Pull User"),
                                "queue_id": st.column_config.TextColumn("Queue ID"),
                                "med_desc": st.column_config.TextColumn("Medication"),
                            },
                        )
                        occurrence_key = refill_occurrence_key(selected_refill)
                        logged_occurrence_keys = load_refill_occurrence_keys()
                        already_logged = occurrence_key in logged_occurrence_keys
                        if already_logged:
                            st.success("This refill transaction is already logged as a refill-entry occurrence.")
                        with st.form(
                            f"refill_occurrence_log_form_{occurrence_key}_{start_date}_{end_date}",
                            clear_on_submit=False,
                        ):
                            log_occurrence = st.checkbox(
                                "Log this refill transaction as an occurrence for this tech",
                                value=not already_logged,
                                key=f"log_refill_occurrence_checkbox_{occurrence_key}_{start_date}_{end_date}",
                            )
                            occurrence_note = st.text_area(
                                "Occurrence note",
                                value=(
                                    f"Refill entered {fmt_qty(entered_qty)} but matched carousel pull evidence was "
                                    f"{fmt_qty(destination_match_qty)}. Expected ending count {fmt_qty(expected_ending_qty)}; "
                                    f"actual ending count {fmt_qty(actual_ending_qty)}."
                                ),
                                key=f"log_refill_occurrence_note_{occurrence_key}_{start_date}_{end_date}",
                            )
                            submit_occurrence = st.form_submit_button(
                                "Save refill occurrence",
                                disabled=not log_occurrence,
                                type="primary",
                            )
                        if submit_occurrence:
                            saved_count = save_refill_occurrence(
                                selected_refill,
                                pull_evidence,
                                float(destination_match_qty),
                                float(total_pull_qty),
                                occurrence_note,
                            )
                            load_refill_occurrence_keys.clear()
                            load_refill_occurrence_log.clear()
                            st.success(
                                "Refill occurrence logged."
                                if saved_count
                                else "No occurrence was saved."
                            )
                            st.rerun()
                        st.download_button(
                            "Export Pull Evidence For Selected Refill",
                            data=to_excel_bytes(pull_evidence),
                            file_name="selected_refill_pull_evidence.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                st.download_button(
                    "Export Selected Event Paper Trail",
                    data=to_excel_bytes(timeline),
                    file_name="clinical_count_control_paper_trail.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
        st.download_button(
            "Export Clinical Count Control Audit",
            data=to_excel_bytes(visible),
            file_name="clinical_count_control_audit.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        logged_occurrences = load_refill_occurrence_log(start_date, end_date)
        with st.expander("Logged Refill Entry Occurrences", expanded=not logged_occurrences.empty):
            if logged_occurrences.empty:
                st.info("No refill-entry occurrences have been logged for this date range yet.")
            else:
                occ_summary = (
                    logged_occurrences.groupby("occurrence_user")
                    .agg(
                        occurrences=("occurrence_key", "count"),
                        total_entered_vs_pull=("entered_vs_matched_pull", "sum"),
                        max_entered_vs_pull=("entered_vs_matched_pull", "max"),
                        meds=("med_id", "nunique"),
                        devices=("device", "nunique"),
                    )
                    .reset_index()
                    .sort_values(["occurrences", "total_entered_vs_pull"], ascending=False)
                )
                st.dataframe(
                    occ_summary,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "occurrence_user": st.column_config.TextColumn("Tech"),
                        "occurrences": st.column_config.NumberColumn("Occurrences", format="%d"),
                        "total_entered_vs_pull": st.column_config.NumberColumn("Total Entered vs Pull", format="%.0f"),
                        "max_entered_vs_pull": st.column_config.NumberColumn("Max Entered vs Pull", format="%.0f"),
                        "meds": st.column_config.NumberColumn("Meds", format="%d"),
                        "devices": st.column_config.NumberColumn("Devices", format="%d"),
                    },
                )
                occurrence_cols = [
                    "logged_at", "occurrence_status", "occurrence_user", "refill_dt", "device",
                    "pocket", "med_id", "med_desc", "event_type", "entered_qty",
                    "matched_pull_qty", "entered_vs_matched_pull", "beginning_qty",
                    "expected_ending_qty", "ending_qty", "expected_ending_variance",
                    "pull_users", "first_pull_dt", "last_pull_dt", "note",
                ]
                st.dataframe(
                    logged_occurrences[occurrence_cols],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "logged_at": st.column_config.DatetimeColumn("Logged", format="MM/DD/YY HH:mm"),
                        "refill_dt": st.column_config.DatetimeColumn("Refill Time", format="MM/DD/YY HH:mm"),
                        "occurrence_user": st.column_config.TextColumn("Tech"),
                        "entered_qty": st.column_config.NumberColumn("Entered Qty", format="%.0f"),
                        "matched_pull_qty": st.column_config.NumberColumn("Matched Pull Qty", format="%.0f"),
                        "entered_vs_matched_pull": st.column_config.NumberColumn("Entered vs Pull", format="%.0f"),
                        "beginning_qty": st.column_config.NumberColumn("Begin", format="%.0f"),
                        "expected_ending_qty": st.column_config.NumberColumn("Expected End", format="%.0f"),
                        "ending_qty": st.column_config.NumberColumn("Actual End", format="%.0f"),
                        "expected_ending_variance": st.column_config.NumberColumn("Actual - Expected", format="%.0f"),
                        "first_pull_dt": st.column_config.DatetimeColumn("First Pull", format="MM/DD/YY HH:mm"),
                        "last_pull_dt": st.column_config.DatetimeColumn("Last Pull", format="MM/DD/YY HH:mm"),
                    },
                )
                st.download_button(
                    "Export Logged Refill Occurrences",
                    data=to_excel_bytes(logged_occurrences),
                    file_name="logged_refill_entry_occurrences.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )


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

with st.spinner("Building clinical count control audit..."):
    clinical_count_control_df = load_clinical_count_control_audit(start_date, end_date)

render_clinical_count_control_audit(clinical_count_control_df)

render_carousel_return_paper_trail_section(start_date, end_date)

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
        raw_detail = (
            detail_row.drop(labels=["completed"], errors="ignore")
            .rename("Value")
            .reset_index()
            .rename(columns={"index": "Field"})
        )
        raw_detail["Value"] = raw_detail["Value"].map(display_value)
        st.dataframe(
            raw_detail,
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
            clinical_chain_rows=("clinical_events_since_refill", lambda s: s.fillna(0).gt(0).sum()),
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
            "clinical_chain_rows": st.column_config.NumberColumn("Clinical Chain", format="%d"),
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
    clinical_plan = build_clinical_chain_actions(filtered, int(min_action_example_qty))
    if action_plan.empty:
        st.info("No strong or possible refill-entry patterns are visible with the current filters.")
        if not clinical_plan.empty:
            st.markdown("#### Clinical Chain Review Worklist")
            st.caption(
                "These rows have clinical vend/waste activity between the pharmacy refill/load and the later verify mismatch. "
                "Use this before deciding whether the item is truly coaching for refill entry."
            )
            st.dataframe(
                clinical_plan,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "priority": st.column_config.TextColumn("Priority"),
                    "prior_refill_user": st.column_config.TextColumn("Pharmacy User"),
                    "clinical_chain_rows": st.column_config.NumberColumn("Rows", format="%d"),
                    "clinical_events": st.column_config.NumberColumn("Clinical Events", format="%d"),
                    "clinical_vends": st.column_config.NumberColumn("Vends", format="%d"),
                    "clinical_wastes": st.column_config.NumberColumn("Wastes", format="%d"),
                    "total_qty_off": st.column_config.NumberColumn("Total Qty Off", format="%.0f"),
                    "suggested_action": st.column_config.TextColumn("What To Do"),
                    "example_evidence": st.column_config.TextColumn("Examples"),
                },
            )
            if st.button(
                f"Add {len(clinical_plan)} clinical chain review item(s) to Management Coaching",
                key="verify_audit_send_clinical_chain_to_management_only",
            ):
                saved_count = save_clinical_chain_to_management(clinical_plan, filtered, start_date, end_date)
                st.success(
                    f"Management Coaching updated for {saved_count} clinical-chain item"
                    f"{'s' if saved_count != 1 else ''}."
                )
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

        st.markdown("#### Clinical Chain Review Worklist")
        st.caption(
            "These rows have clinical vend/waste activity between the pharmacy refill/load and the later verify mismatch. "
            "Use this before deciding whether the item is truly coaching for refill entry."
        )
        if clinical_plan.empty:
            st.info("No clinical vend/waste chain rows are visible with the current filters.")
        else:
            st.dataframe(
                clinical_plan,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "priority": st.column_config.TextColumn("Priority"),
                    "prior_refill_user": st.column_config.TextColumn("Pharmacy User"),
                    "clinical_chain_rows": st.column_config.NumberColumn("Rows", format="%d"),
                    "clinical_events": st.column_config.NumberColumn("Clinical Events", format="%d"),
                    "clinical_vends": st.column_config.NumberColumn("Vends", format="%d"),
                    "clinical_wastes": st.column_config.NumberColumn("Wastes", format="%d"),
                    "total_qty_off": st.column_config.NumberColumn("Total Qty Off", format="%.0f"),
                    "suggested_action": st.column_config.TextColumn("What To Do"),
                    "example_evidence": st.column_config.TextColumn("Examples"),
                },
            )
            if st.button(
                f"Add {len(clinical_plan)} clinical chain review item(s) to Management Coaching",
                key="verify_audit_send_clinical_chain_to_management",
            ):
                saved_count = save_clinical_chain_to_management(clinical_plan, filtered, start_date, end_date)
                st.success(
                    f"Management Coaching updated for {saved_count} clinical-chain item"
                    f"{'s' if saved_count != 1 else ''}."
                )

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

