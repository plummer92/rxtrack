import hashlib
import re
from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy import text

import App


st.set_page_config(page_title="Mobile BUD Scanner", page_icon="📱", layout="wide")

engine = App.engine

if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.init_db()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Mobile BUD Scanner",
        "Enter or scan a product barcode, match it to an RxTrack med, and save the active BUD review.",
        kicker="Inventory QC",
    )
else:
    st.header("Mobile BUD Scanner")

st.markdown("""
    <style>
    div[data-testid="stButton"] button,
    div[data-testid="stFormSubmitButton"] button {
        background: #0f766e !important;
        color: #ffffff !important;
        border: 1px solid #0f766e !important;
        font-weight: 800 !important;
        min-height: 48px;
    }
    div[data-testid="stButton"] button p,
    div[data-testid="stFormSubmitButton"] button p {
        color: #ffffff !important;
    }
    </style>
""", unsafe_allow_html=True)

def ensure_qc_actions_table():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS inventory_qc_actions (
                id SERIAL PRIMARY KEY,
                action_key TEXT UNIQUE,
                action_type TEXT,
                med_id TEXT,
                med_desc TEXT,
                isa_name TEXT,
                location TEXT,
                action_status TEXT,
                action_dt TIMESTAMP DEFAULT NOW(),
                action_by TEXT,
                note TEXT,
                replacement_expire_date DATE
            )
        """))
        conn.execute(text("ALTER TABLE inventory_qc_actions ADD COLUMN IF NOT EXISTS replacement_expire_date DATE"))


def normalize_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def normalize_text(value):
    return str(value or "").strip().upper()


def barcode_candidates(raw_value):
    raw_text = normalize_text(raw_value)
    digits = normalize_digits(raw_value)
    candidates = {raw_text}
    digit_candidates = set()
    if digits:
        digit_candidates.add(digits)
        digit_candidates.add(digits.lstrip("0") or "0")
        for width in (14, 12, 11, 10):
            if len(digits) >= width:
                for start in range(0, len(digits) - width + 1):
                    piece = digits[start:start + width]
                    digit_candidates.add(piece)
                    digit_candidates.add(piece.lstrip("0") or "0")
    return {item for item in candidates if item}, {item for item in digit_candidates if item}


def parse_gs1_expiration(raw_value):
    compact = normalize_text(raw_value).replace("(", "").replace(")", "")
    digits = normalize_digits(compact)
    match = re.search(r"01\d{14}17(\d{6})", digits) or re.search(r"17(\d{6})", digits)
    if not match:
        return None

    yy, mm, dd = match.group(1)[:2], match.group(1)[2:4], match.group(1)[4:6]
    year = 2000 + int(yy)
    try:
        return date(year, int(mm), int(dd))
    except ValueError:
        return None


def save_active_bud_review(row, bud_date, reviewed_by, note, barcode_value):
    sql = text("""
        INSERT INTO inventory_qc_actions (
            action_key, action_type, med_id, med_desc, isa_name, location,
            action_status, action_by, note, replacement_expire_date
        )
        VALUES (
            :action_key, 'active_bud', :med_id, :med_desc, :isa_name, :location,
            'Mobile BUD reviewed', :action_by, :note, :replacement_expire_date
        )
        ON CONFLICT (action_key) DO UPDATE SET
            action_status = EXCLUDED.action_status,
            action_dt = NOW(),
            action_by = EXCLUDED.action_by,
            note = EXCLUDED.note,
            replacement_expire_date = EXCLUDED.replacement_expire_date
    """)
    action_key = "|".join([
        "mobile-bud",
        normalize_text(row.get("isa_name")),
        normalize_text(row.get("location")),
        normalize_text(row.get("med_id")),
    ])
    barcode_hash = hashlib.sha256(str(barcode_value or "").encode()).hexdigest()[:10]
    full_note = f"{note or 'Mobile barcode BUD review.'} Barcode: {barcode_value or 'manual'} ({barcode_hash})"
    with engine.begin() as conn:
        conn.execute(sql, {
            "action_key": action_key,
            "med_id": normalize_text(row.get("med_id")),
            "med_desc": str(row.get("med_desc") or ""),
            "isa_name": str(row.get("isa_name") or ""),
            "location": str(row.get("location") or ""),
            "action_by": reviewed_by or "",
            "note": full_note,
            "replacement_expire_date": bud_date,
        })


@st.cache_data(ttl=60)
def load_scan_catalog():
    cycle_sql = text("""
        WITH latest_snapshot AS (
            SELECT MAX(snapshot_date) AS snapshot_date
            FROM cycle_count_status
        )
        SELECT
            c.snapshot_date,
            c.isa_name,
            c.location,
            UPPER(TRIM(c.med_id)) AS med_id,
            c.med_desc
        FROM cycle_count_status c
        JOIN latest_snapshot s ON c.snapshot_date = s.snapshot_date
        WHERE COALESCE(TRIM(c.med_id), '') <> ''
    """)
    package_sql = text("""
        SELECT
            UPPER(TRIM(med_id)) AS med_id,
            STRING_AGG(DISTINCT ndc, ', ') AS ndc_values,
            MAX(bud) AS latest_packaged_bud
        FROM packaged_meds
        WHERE COALESCE(TRIM(med_id), '') <> ''
          AND COALESCE(hospital_lot_number, '') <> 'MANUAL-BUD'
          AND COALESCE(dose_form, '') <> 'Manual BUD'
        GROUP BY UPPER(TRIM(med_id))
    """)
    pyxis_sql = text("""
        SELECT
            UPPER(TRIM(med_id)) AS med_id,
            COUNT(*) AS pyxis_pockets,
            SUM(current_count) AS pyxis_qty,
            STRING_AGG(DISTINCT station, ', ') AS pyxis_stations
        FROM inventory_detailed
        WHERE COALESCE(TRIM(med_id), '') <> ''
          AND COALESCE(station, '') NOT ILIKE 'CAR%'
        GROUP BY UPPER(TRIM(med_id))
    """)
    with engine.connect() as conn:
        cycle = pd.read_sql(cycle_sql, conn)
        package = pd.read_sql(package_sql, conn)
        pyxis = pd.read_sql(pyxis_sql, conn)

    if cycle.empty:
        return pd.DataFrame()

    catalog = cycle.merge(package, on="med_id", how="left").merge(pyxis, on="med_id", how="left")
    catalog["latest_packaged_bud"] = pd.to_datetime(catalog["latest_packaged_bud"], errors="coerce")
    for col in ["ndc_values", "pyxis_stations"]:
        catalog[col] = catalog[col].fillna("").astype(str)
    for col in ["pyxis_pockets", "pyxis_qty"]:
        catalog[col] = pd.to_numeric(catalog[col], errors="coerce").fillna(0)
    return catalog


def find_matches(catalog, raw_value):
    text_candidates, digit_candidates = barcode_candidates(raw_value)
    if catalog.empty or (not text_candidates and not digit_candidates):
        return pd.DataFrame()

    matches = catalog.copy()
    med_match = matches["med_id"].isin(text_candidates)
    ndc_match = matches["ndc_values"].apply(
        lambda value: any((normalize_digits(ndc) in digit_candidates) or ((normalize_digits(ndc).lstrip("0") or "0") in digit_candidates)
                          for ndc in str(value).split(",") if normalize_digits(ndc))
    )
    matches = matches[med_match | ndc_match].copy()
    if matches.empty:
        return matches

    matches["match_type"] = "NDC"
    matches.loc[med_match.loc[matches.index], "match_type"] = "Med ID"
    return matches.sort_values(["match_type", "isa_name", "location", "med_id"])


ensure_qc_actions_table()

catalog = load_scan_catalog()
st.info(
    "Use a Bluetooth/USB barcode scanner or type the barcode/Med ID below. "
    "The embedded phone-camera scanner was disabled because Streamlit Cloud rejected the custom component loader."
)
scan_value = st.text_input("Barcode or Med ID", key="mobile_barcode_input")

parsed_expiration = parse_gs1_expiration(scan_value)
matches = find_matches(catalog, scan_value)

if scan_value:
    st.caption(f"Scanned value: `{scan_value}`")

if catalog.empty:
    st.warning("No ISA item snapshot is loaded yet.")
elif not scan_value:
    st.info("Scan a barcode or enter a Med ID.")
elif matches.empty:
    st.warning("No matching RxTrack med found.")
else:
    st.subheader("Matched Med")
    labels = [
        f"{row.med_id} | {row.med_desc} | {row.isa_name} {row.location} | {row.match_type}"
        for row in matches.itertuples(index=False)
    ]
    selected_label = st.selectbox("Match", labels)
    selected_index = labels.index(selected_label)
    selected = matches.iloc[selected_index]

    c1, c2, c3 = st.columns(3)
    c1.metric("Med ID", selected["med_id"])
    c2.metric("ISA", selected["isa_name"])
    c3.metric("Location", selected["location"])

    detail_cols = [
        "med_id", "med_desc", "isa_name", "location", "latest_packaged_bud",
        "ndc_values", "pyxis_pockets", "pyxis_qty", "pyxis_stations",
    ]
    st.dataframe(
        matches[detail_cols],
        width="stretch",
        hide_index=True,
        column_config={
            "latest_packaged_bud": st.column_config.DatetimeColumn("Latest Packaged BUD", format="MM/DD/YYYY"),
            "pyxis_pockets": st.column_config.NumberColumn("Pyxis Pockets", format="%d"),
            "pyxis_qty": st.column_config.NumberColumn("Pyxis Qty", format="%.0f"),
        },
    )

    with st.form("mobile_bud_review_form"):
        default_bud = parsed_expiration or pd.Timestamp.today().date()
        bud_date = st.date_input("Active BUD", value=default_bud)
        reviewed_by = st.text_input("Reviewed by", value="")
        note = st.text_area("Note", value="Mobile barcode BUD review.")
        submitted = st.form_submit_button("Save Active BUD Review")
        if submitted:
            save_active_bud_review(selected, bud_date, reviewed_by, note, scan_value)
            load_scan_catalog.clear()
            st.success("Saved Active BUD review.")
            st.rerun()
