import hashlib
import re
from datetime import date
from io import BytesIO
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st
from sqlalchemy import text
from PIL import Image

try:
    import zxingcpp
except ImportError:
    zxingcpp = None

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
        "Use the iPhone camera or enter a barcode, match it to an RxTrack med, and save the active BUD review.",
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

PHOTO_RETENTION_NOTE = (
    "Barcode photos are decoded in memory only. RxTrack stores the decoded barcode text and med mapping, not the image."
)

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
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS barcode_med_map (
                barcode_text TEXT PRIMARY KEY,
                barcode_digits TEXT,
                med_id TEXT,
                med_desc TEXT,
                isa_name TEXT,
                location TEXT,
                source TEXT,
                verification_status TEXT DEFAULT 'Needs automated verification',
                verified_by TEXT,
                verified_dt TIMESTAMP,
                verification_note TEXT,
                last_seen_dt TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("ALTER TABLE barcode_med_map ADD COLUMN IF NOT EXISTS verification_status TEXT DEFAULT 'Needs automated verification'"))
        conn.execute(text("ALTER TABLE barcode_med_map ADD COLUMN IF NOT EXISTS verified_by TEXT"))
        conn.execute(text("ALTER TABLE barcode_med_map ADD COLUMN IF NOT EXISTS verified_dt TIMESTAMP"))
        conn.execute(text("ALTER TABLE barcode_med_map ADD COLUMN IF NOT EXISTS verification_note TEXT"))
        conn.execute(text("""
            UPDATE barcode_med_map
            SET verification_status = 'Needs automated verification'
            WHERE verification_status IS NULL
               OR TRIM(verification_status) = ''
               OR verification_status = 'Pending pharmacist check'
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_barcode_med_map_digits ON barcode_med_map (barcode_digits)"))


def normalize_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def normalize_text(value):
    return str(value or "").strip().upper()


def barcode_key(value):
    text_value = normalize_text(value)
    digit_value = normalize_digits(value)
    return text_value, digit_value.lstrip("0") or digit_value


def ndc_variants_from_digits(digits):
    clean = normalize_digits(digits)
    variants = set()
    if not clean:
        return variants

    candidates = {clean, clean.lstrip("0") or "0"}
    if len(clean) >= 14:
        candidates.add(clean[-14:])
        candidates.add(clean[-14:].lstrip("0") or "0")
    if len(clean) >= 12:
        candidates.add(clean[-12:])
        candidates.add(clean[-12:].lstrip("0") or "0")
    if len(clean) >= 11:
        candidates.add(clean[-11:])
        candidates.add(clean[-11:].lstrip("0") or "0")
    for candidate in candidates:
        if len(candidate) == 11:
            variants.add(f"{candidate[:5]}-{candidate[5:9]}-{candidate[9:]}")
            variants.add(f"{candidate[:5]}-{candidate[5:8]}-{candidate[8:]}")
            variants.add(f"{candidate[:4]}-{candidate[4:8]}-{candidate[8:]}")
        elif len(candidate) == 10:
            variants.add(f"{candidate[:5]}-{candidate[5:9]}-{candidate[9:]}")
            variants.add(f"{candidate[:5]}-{candidate[5:8]}-{candidate[8:]}")
            variants.add(f"{candidate[:4]}-{candidate[4:8]}-{candidate[8:]}")
        elif len(candidate) == 12 and candidate.startswith("3"):
            variants.update(ndc_variants_from_digits(candidate[1:]))
        elif len(candidate) == 14:
            variants.update(ndc_variants_from_digits(candidate[3:]))
    return {variant for variant in variants if variant.strip("-")}


def compact_ndc_set(value):
    ndcs = set()
    raw_values = re.split(r"[,;\s]+", str(value or ""))
    for raw in raw_values:
        digits = normalize_digits(raw)
        if digits:
            ndcs.add(digits)
            ndcs.add(digits.lstrip("0") or "0")
            for variant in ndc_variants_from_digits(digits):
                variant_digits = normalize_digits(variant)
                ndcs.add(variant_digits)
                ndcs.add(variant_digits.lstrip("0") or "0")
    return {item for item in ndcs if item}


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


def decode_barcode_photo(uploaded_image):
    if uploaded_image is None or zxingcpp is None:
        return []

    image = Image.open(BytesIO(uploaded_image.getvalue())).convert("RGB")
    image_array = np.array(image)
    results = zxingcpp.read_barcodes(image_array)
    decoded = []
    for result in results:
        text_value = str(getattr(result, "text", "") or "").strip()
        if text_value:
            decoded.append(text_value)
    return decoded


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


def automated_verification(row, barcode_value, openfda_matches):
    barcode_ndcs = set()
    _, digit_value = barcode_key(barcode_value)
    if digit_value:
        barcode_ndcs.add(digit_value)
        barcode_ndcs.update(compact_ndc_set(digit_value))

    row_ndcs = compact_ndc_set(row.get("ndc_values", ""))
    if row_ndcs and barcode_ndcs.intersection(row_ndcs):
        return (
            "Auto verified",
            "RxTrack NDC cross-check",
            "Scanned barcode NDC matches an NDC already loaded for this RxTrack med.",
        )

    if str(row.get("match_type", "")) == "NDC":
        return (
            "Auto verified",
            "RxTrack NDC match",
            "RxTrack matched this barcode from the packaging-report NDC for the selected med.",
        )

    if not openfda_matches.empty and row_ndcs:
        fda_ndcs = set()
        for col in ["query_ndc", "package_ndc", "product_ndc"]:
            if col in openfda_matches.columns:
                for value in openfda_matches[col].dropna():
                    fda_ndcs.update(compact_ndc_set(value))
        if fda_ndcs.intersection(row_ndcs):
            return (
                "Auto verified",
                "FDA NDC cross-check",
                "FDA/openFDA package NDC agrees with an NDC already loaded for this RxTrack med.",
            )

    if not openfda_matches.empty:
        return (
            "Needs review",
            "FDA NDC lookup available",
            "FDA identified the package, but RxTrack does not have a matching local NDC for this med yet.",
        )

    return (
        "Needs review",
        "No external NDC confirmation",
        "No FDA/openFDA or RxTrack NDC cross-check confirmed this barcode-to-med link.",
    )


def save_barcode_mapping(row, barcode_value, source="Confirmed mobile scan", openfda_matches=None):
    barcode_text, barcode_digits = barcode_key(barcode_value)
    if not barcode_text:
        return
    if openfda_matches is None:
        openfda_matches = pd.DataFrame()
    verification_status, verified_by, verification_note = automated_verification(row, barcode_value, openfda_matches)

    sql = text("""
        INSERT INTO barcode_med_map (
            barcode_text, barcode_digits, med_id, med_desc, isa_name, location,
            source, verification_status, verified_by, verified_dt, verification_note, last_seen_dt
        )
        VALUES (
            :barcode_text, :barcode_digits, :med_id, :med_desc, :isa_name, :location,
            :source, :verification_status, :verified_by,
            CASE WHEN :verification_status = 'Auto verified' THEN NOW() ELSE NULL END,
            :verification_note, NOW()
        )
        ON CONFLICT (barcode_text) DO UPDATE SET
            barcode_digits = EXCLUDED.barcode_digits,
            med_id = EXCLUDED.med_id,
            med_desc = EXCLUDED.med_desc,
            isa_name = EXCLUDED.isa_name,
            location = EXCLUDED.location,
            source = EXCLUDED.source,
            verification_status = EXCLUDED.verification_status,
            verified_by = EXCLUDED.verified_by,
            verified_dt = EXCLUDED.verified_dt,
            verification_note = EXCLUDED.verification_note,
            last_seen_dt = NOW()
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "barcode_text": barcode_text,
            "barcode_digits": barcode_digits,
            "med_id": normalize_text(row.get("med_id")),
            "med_desc": str(row.get("med_desc") or ""),
            "isa_name": str(row.get("isa_name") or ""),
            "location": str(row.get("location") or ""),
            "source": source,
            "verification_status": verification_status,
            "verified_by": verified_by,
            "verification_note": verification_note,
        })
    load_barcode_crosswalk.clear()


@st.cache_data(ttl=86400)
def lookup_openfda_ndc(raw_value):
    digits = normalize_digits(raw_value)
    variants = ndc_variants_from_digits(digits)
    if not variants:
        return pd.DataFrame()

    rows = []
    for ndc in sorted(variants):
        url = f"https://api.fda.gov/drug/ndc.json?search=packaging.package_ndc:%22{quote(ndc)}%22&limit=5"
        try:
            response = requests.get(url, timeout=8)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            payload = response.json()
        except Exception:
            continue

        for result in payload.get("results", []):
            packages = result.get("packaging") or []
            package_codes = [
                package.get("package_ndc", "")
                for package in packages
                if package.get("package_ndc")
            ]
            rows.append({
                "query_ndc": ndc,
                "package_ndc": ", ".join(package_codes),
                "product_ndc": result.get("product_ndc", ""),
                "brand_name": result.get("brand_name", ""),
                "generic_name": result.get("generic_name", ""),
                "dosage_form": result.get("dosage_form", ""),
                "route": ", ".join(result.get("route") or []),
                "labeler_name": result.get("labeler_name", ""),
            })
    return pd.DataFrame(rows).drop_duplicates() if rows else pd.DataFrame()


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


@st.cache_data(ttl=60)
def load_mobile_scan_queue():
    sql = text("""
        WITH latest_snapshot AS (
            SELECT MAX(snapshot_date) AS snapshot_date
            FROM cycle_count_status
        ),
        latest_action AS (
            SELECT DISTINCT ON (UPPER(TRIM(med_id)), COALESCE(isa_name, ''), COALESCE(location, ''))
                UPPER(TRIM(med_id)) AS med_id,
                COALESCE(isa_name, '') AS isa_name,
                COALESCE(location, '') AS location,
                action_status,
                action_dt,
                action_by,
                replacement_expire_date
            FROM inventory_qc_actions
            WHERE action_type = 'active_bud'
              AND replacement_expire_date IS NOT NULL
            ORDER BY UPPER(TRIM(med_id)), COALESCE(isa_name, ''), COALESCE(location, ''), action_dt DESC
        ),
        packaged AS (
            SELECT
                UPPER(TRIM(med_id)) AS med_id,
                MAX(COALESCE(bud, hospital_expire_date)) AS packaged_bud
            FROM packaged_meds
            WHERE dispense_dt IS NOT NULL
              AND COALESCE(hospital_lot_number, '') <> 'MANUAL-BUD'
              AND COALESCE(dose_form, '') <> 'Manual BUD'
            GROUP BY UPPER(TRIM(med_id))
        ),
        pyxis AS (
            SELECT
                UPPER(TRIM(med_id)) AS med_id,
                COUNT(*) AS pyxis_pockets,
                SUM(current_count) AS pyxis_qty,
                STRING_AGG(DISTINCT station, ', ') AS pyxis_stations
            FROM inventory_detailed
            WHERE COALESCE(current_count, 0) > 0
              AND COALESCE(station, '') NOT ILIKE 'CAR%%'
            GROUP BY UPPER(TRIM(med_id))
        )
        SELECT
            c.isa_name,
            c.location,
            UPPER(TRIM(c.med_id)) AS med_id,
            c.med_desc,
            c.snapshot_date,
            c.last_cycle_count,
            c.days_since_last_count,
            p.packaged_bud,
            a.replacement_expire_date AS active_bud_date,
            a.action_dt AS reviewed_dt,
            a.action_by AS reviewed_by,
            COALESCE(y.pyxis_pockets, 0) AS pyxis_pockets,
            COALESCE(y.pyxis_qty, 0) AS pyxis_qty,
            COALESCE(y.pyxis_stations, '') AS pyxis_stations
        FROM cycle_count_status c
        JOIN latest_snapshot s ON c.snapshot_date = s.snapshot_date
        LEFT JOIN latest_action a
          ON UPPER(TRIM(c.med_id)) = a.med_id
         AND COALESCE(c.isa_name, '') = a.isa_name
         AND COALESCE(c.location, '') = a.location
        LEFT JOIN packaged p ON UPPER(TRIM(c.med_id)) = p.med_id
        LEFT JOIN pyxis y ON UPPER(TRIM(c.med_id)) = y.med_id
        WHERE COALESCE(TRIM(c.med_id), '') <> ''
    """)
    with engine.connect() as conn:
        queue = pd.read_sql(sql, conn)
    if queue.empty:
        return queue

    today = pd.Timestamp.today().normalize()
    queue["packaged_bud"] = pd.to_datetime(queue["packaged_bud"], errors="coerce")
    queue["active_bud_date"] = pd.to_datetime(queue["active_bud_date"], errors="coerce")
    queue["reviewed_dt"] = pd.to_datetime(queue["reviewed_dt"], errors="coerce")
    queue["days_until_active_bud"] = (queue["active_bud_date"] - today).dt.days
    queue["days_until_packaged_bud"] = (queue["packaged_bud"] - today).dt.days
    queue["pyxis_qty"] = pd.to_numeric(queue["pyxis_qty"], errors="coerce").fillna(0)
    queue["pyxis_pockets"] = pd.to_numeric(queue["pyxis_pockets"], errors="coerce").fillna(0)
    queue["queue_reason"] = "Review active BUD"
    queue.loc[queue["active_bud_date"].isna(), "queue_reason"] = "No active BUD review saved"
    queue.loc[
        queue["active_bud_date"].notna() & queue["days_until_active_bud"].le(30),
        "queue_reason"
    ] = "Active BUD due within 30 days"
    queue.loc[
        queue["active_bud_date"].notna() & queue["days_until_active_bud"].between(31, 90, inclusive="both"),
        "queue_reason"
    ] = "Active BUD due within 90 days"
    queue["priority_rank"] = 3
    queue.loc[queue["active_bud_date"].isna(), "priority_rank"] = 0
    queue.loc[queue["days_until_active_bud"].le(30), "priority_rank"] = 1
    queue.loc[queue["days_until_active_bud"].between(31, 90, inclusive="both"), "priority_rank"] = 2
    queue = queue[
        queue["active_bud_date"].isna()
        | queue["days_until_active_bud"].le(90)
    ].copy()
    return queue.sort_values(
        ["priority_rank", "days_until_active_bud", "pyxis_qty", "med_desc"],
        ascending=[True, True, False, True],
    )


@st.cache_data(ttl=60)
def load_barcode_crosswalk():
    sql = text("""
        SELECT
            barcode_text,
            barcode_digits,
            UPPER(TRIM(med_id)) AS med_id,
            med_desc,
            isa_name,
            location,
            source,
            verification_status,
            verified_by,
            verified_dt,
            verification_note,
            last_seen_dt
        FROM barcode_med_map
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn)
    if df.empty:
        return df
    for col in ["barcode_text", "barcode_digits", "med_id"]:
        df[col] = df[col].fillna("").astype(str).str.strip().str.upper()
    for col in ["source", "verification_status", "verified_by", "verification_note"]:
        df[col] = df[col].fillna("").astype(str)
    df["verified_dt"] = pd.to_datetime(df["verified_dt"], errors="coerce")
    return df


def find_matches(catalog, barcode_crosswalk, raw_value):
    text_candidates, digit_candidates = barcode_candidates(raw_value)
    if catalog.empty or (not text_candidates and not digit_candidates):
        return pd.DataFrame()

    work = catalog.copy()
    med_match = work["med_id"].isin(text_candidates)
    ndc_match = work["ndc_values"].apply(
        lambda value: any((normalize_digits(ndc) in digit_candidates) or ((normalize_digits(ndc).lstrip("0") or "0") in digit_candidates)
                          for ndc in str(value).split(",") if normalize_digits(ndc))
    )
    direct_matches = work[med_match | ndc_match].copy()
    if not direct_matches.empty:
        direct_matches["match_type"] = "NDC"
        direct_matches.loc[med_match.loc[direct_matches.index], "match_type"] = "Med ID"

    learned_matches = pd.DataFrame()
    if not barcode_crosswalk.empty:
        barcode_hits = barcode_crosswalk[
            barcode_crosswalk["barcode_text"].isin(text_candidates)
            | barcode_crosswalk["barcode_digits"].isin(digit_candidates)
        ].copy()
        if not barcode_hits.empty:
            learned_matches = barcode_hits.merge(
                work,
                on=["med_id", "isa_name", "location"],
                how="inner",
                suffixes=("_map", ""),
            )
            if not learned_matches.empty:
                learned_matches["match_type"] = "Saved barcode map"

    matches = pd.concat([learned_matches, direct_matches], ignore_index=True, sort=False)
    if matches.empty:
        return matches

    matches = matches.drop_duplicates(["isa_name", "location", "med_id"], keep="first")
    return matches.sort_values(["match_type", "isa_name", "location", "med_id"])


ensure_qc_actions_table()

if st.session_state.pop("mobile_bud_reset", False):
    st.session_state["mobile_barcode_input"] = ""
    st.session_state["barcode_med_search"] = ""

catalog = load_scan_catalog()
barcode_crosswalk = load_barcode_crosswalk()
scan_queue = load_mobile_scan_queue()

st.markdown("##### Priority Scan Queue")
if scan_queue.empty:
    st.success("No priority Active BUD scans are currently due.")
else:
    q1, q2, q3 = st.columns(3)
    q1.metric("Items To Scan", f"{len(scan_queue):,}")
    q2.metric("Missing Active BUD", f"{int(scan_queue['active_bud_date'].isna().sum()):,}")
    q3.metric("Due Within 30 Days", f"{int(scan_queue['days_until_active_bud'].le(30).sum()):,}")
    queue_cols = [
        "queue_reason",
        "med_id",
        "med_desc",
        "isa_name",
        "location",
        "active_bud_date",
        "days_until_active_bud",
        "packaged_bud",
        "pyxis_qty",
        "pyxis_pockets",
        "pyxis_stations",
    ]
    queue_event = st.dataframe(
        scan_queue[queue_cols].head(50),
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "active_bud_date": st.column_config.DatetimeColumn("Active BUD", format="MM/DD/YYYY"),
            "packaged_bud": st.column_config.DatetimeColumn("Packaged BUD", format="MM/DD/YYYY"),
            "days_until_active_bud": st.column_config.NumberColumn("Days Until Active BUD", format="%.0f"),
            "pyxis_qty": st.column_config.NumberColumn("Pyxis Qty", format="%.0f"),
            "pyxis_pockets": st.column_config.NumberColumn("Pyxis Pockets", format="%d"),
        },
    )
    if queue_event.selection.rows:
        selected_queue = scan_queue.head(50).reset_index(drop=True).iloc[queue_event.selection.rows[0]]
        st.caption(
            f"Selected queue item: {selected_queue['med_id']} | {selected_queue['med_desc']} | "
            f"{selected_queue['isa_name']} {selected_queue['location']}"
        )

st.caption(
    "On iPhone, tap the camera box, take a clear close-up photo of the barcode, then confirm the decoded value below."
)
st.caption(PHOTO_RETENTION_NOTE)
camera_photo = st.camera_input("iPhone camera barcode photo")
decoded_values = decode_barcode_photo(camera_photo)
if zxingcpp is None:
    st.warning("Camera barcode decoding is not installed yet. You can still type the barcode or Med ID.")
elif camera_photo is not None and not decoded_values:
    st.warning("No barcode was decoded from that photo. Try a closer, flatter picture with the barcode filling most of the frame.")
elif decoded_values:
    decoded_value = decoded_values[0]
    if decoded_value != st.session_state.get("mobile_barcode_input"):
        st.session_state["mobile_barcode_input"] = decoded_value
    if len(decoded_values) > 1:
        st.caption(f"Decoded {len(decoded_values)} barcodes; using the first one.")
    st.success(f"Decoded barcode: {decoded_value}")

scan_value = st.text_input("Barcode or Med ID", key="mobile_barcode_input")

parsed_expiration = parse_gs1_expiration(scan_value)
matches = find_matches(catalog, barcode_crosswalk, scan_value)
openfda_matches = lookup_openfda_ndc(scan_value) if scan_value else pd.DataFrame()

if scan_value:
    st.caption(f"Scanned value: `{scan_value}`")

if catalog.empty:
    st.warning("No ISA item snapshot is loaded yet.")
elif not scan_value:
    st.info("Scan a barcode or enter a Med ID.")
elif matches.empty:
    st.warning("No matching RxTrack med found.")
    if not openfda_matches.empty:
        st.markdown("##### National NDC Lookup")
        st.dataframe(openfda_matches, width="stretch", hide_index=True)
        st.caption("Use this as a hint, then link the barcode to the matching RxTrack med below.")
    search_value = st.text_input("Search RxTrack med to link this barcode", key="barcode_med_search")
    if search_value:
        search_mask = (
            catalog["med_id"].str.contains(search_value, case=False, na=False)
            | catalog["med_desc"].str.contains(search_value, case=False, na=False)
        )
        search_results = catalog[search_mask].copy().head(50)
        if search_results.empty:
            st.info("No meds matched that search.")
        else:
            map_labels = [
                f"{row.med_id} | {row.med_desc} | {row.isa_name} {row.location}"
                for row in search_results.itertuples(index=False)
            ]
            selected_map_label = st.selectbox("Link barcode to", map_labels)
            selected_map = search_results.iloc[map_labels.index(selected_map_label)]
            if st.button("Save Barcode Match"):
                save_barcode_mapping(selected_map, scan_value, source="Manual barcode link", openfda_matches=openfda_matches)
                st.success("Saved barcode match. RxTrack applied automated NDC verification when possible.")
                st.rerun()
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
    verification_status = str(selected.get("verification_status", "") or "").strip()
    if selected.get("match_type") == "Saved barcode map":
        if verification_status == "Auto verified":
            st.success(f"Barcode link auto-verified by {selected.get('verified_by', '') or 'RxTrack'}.")
        else:
            st.warning("Barcode link needs review because RxTrack could not confirm it by NDC.")

    detail_cols = [
        "med_id", "med_desc", "isa_name", "location", "latest_packaged_bud",
        "match_type", "verification_status", "verified_by", "verified_dt",
        "ndc_values", "pyxis_pockets", "pyxis_qty", "pyxis_stations",
    ]
    visible_detail_cols = [col for col in detail_cols if col in matches.columns]
    st.dataframe(
        matches[visible_detail_cols],
        width="stretch",
        hide_index=True,
        column_config={
            "latest_packaged_bud": st.column_config.DatetimeColumn("Latest Packaged BUD", format="MM/DD/YYYY"),
            "verified_dt": st.column_config.DatetimeColumn("Verified", format="MM/DD/YYYY HH:mm"),
            "pyxis_pockets": st.column_config.NumberColumn("Pyxis Pockets", format="%d"),
            "pyxis_qty": st.column_config.NumberColumn("Pyxis Qty", format="%.0f"),
        },
    )

    with st.expander("Automated barcode verification"):
        auto_status, auto_by, auto_note = automated_verification(selected, scan_value, openfda_matches)
        if auto_status == "Auto verified":
            st.success(auto_note)
        else:
            st.warning(auto_note)
        st.caption(f"Verification source: {auto_by}")

    with st.form("mobile_bud_review_form"):
        default_bud = parsed_expiration or pd.Timestamp.today().date()
        bud_date = st.date_input("Active BUD", value=default_bud)
        reviewed_by = st.text_input("Reviewed by", value="")
        note = st.text_area("Note", value="Mobile barcode BUD review.")
        submitted = st.form_submit_button("Save Active BUD Review")
        if submitted:
            save_barcode_mapping(selected, scan_value, openfda_matches=openfda_matches)
            save_active_bud_review(selected, bud_date, reviewed_by, note, scan_value)
            load_scan_catalog.clear()
            load_mobile_scan_queue.clear()
            st.session_state["mobile_bud_reset"] = True
            st.success("Saved Active BUD review.")
            st.rerun()
