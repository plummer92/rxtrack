import pandas as pd
import plotly.express as px
import re
import streamlit as st
from sqlalchemy import text

import App


st.set_page_config(page_title="Inventory Quality Control", page_icon="📦", layout="wide")
App.apply_global_styles()

engine = App.engine
start_date, end_date = App.render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Inventory Quality Control",
        "Use Receiving transactions to answer the first lifecycle question: how long has each ISA item gone since product was last received?",
        kicker="Receiving Lifecycle",
    )
else:
    st.header("Inventory Quality Control")
    st.caption("Days since last received by ISA item.")

st.markdown("""
    <style>
    div[data-testid="stButton"] button,
    div[data-testid="stFormSubmitButton"] button,
    div[data-testid="stDownloadButton"] button {
        background: #0f766e !important;
        color: #ffffff !important;
        border: 1px solid #0f766e !important;
        font-weight: 700 !important;
    }
    div[data-testid="stButton"] button:hover,
    div[data-testid="stFormSubmitButton"] button:hover,
    div[data-testid="stDownloadButton"] button:hover {
        background: #115e59 !important;
        color: #ffffff !important;
        border-color: #115e59 !important;
    }
    div[data-testid="stButton"] button p,
    div[data-testid="stFormSubmitButton"] button p,
    div[data-testid="stDownloadButton"] button p {
        color: #ffffff !important;
    }
    div[data-testid="stSegmentedControl"] button {
        background: #ffffff !important;
        color: #111827 !important;
        border-color: #9ca3af !important;
        font-weight: 700 !important;
    }
    div[data-testid="stSegmentedControl"] button p,
    div[data-testid="stSegmentedControl"] button span {
        color: #111827 !important;
    }
    div[data-testid="stSegmentedControl"] button[aria-pressed="true"] {
        background: #0f766e !important;
        color: #ffffff !important;
        border-color: #0f766e !important;
    }
    div[data-testid="stSegmentedControl"] button[aria-pressed="true"] p,
    div[data-testid="stSegmentedControl"] button[aria-pressed="true"] span {
        color: #ffffff !important;
    }
    </style>
""", unsafe_allow_html=True)


def ensure_packaging_table():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS packaged_meds (
                pk TEXT PRIMARY KEY,
                dispense_dt TIMESTAMP,
                reception_num TEXT,
                med_id TEXT,
                med_desc TEXT,
                dose_form TEXT,
                qty_per_pack FLOAT,
                qoh FLOAT,
                manufacturer TEXT,
                ndc TEXT,
                mfg_lot_number TEXT,
                mfg_expire_date DATE,
                device_id TEXT,
                hospital_lot_number TEXT,
                hospital_expire_date DATE,
                bud DATE,
                packaged_by TEXT,
                confirmer TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS device_inventory (
                pk TEXT PRIMARY KEY,
                med_desc TEXT,
                device TEXT,
                zone TEXT,
                pocket_location TEXT,
                status TEXT,
                brand_name TEXT,
                med_id TEXT,
                med_class TEXT,
                current_quantity FLOAT,
                min_qty FLOAT,
                max_qty FLOAT,
                outdate_tracking TEXT,
                loaded_as_fraction TEXT,
                backordered TEXT,
                standard_stock TEXT,
                active_orders TEXT,
                days_unused FLOAT,
                snapshot_dt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS device_inventory_history (
                snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
                pk TEXT NOT NULL,
                med_desc TEXT,
                device TEXT,
                zone TEXT,
                pocket_location TEXT,
                status TEXT,
                brand_name TEXT,
                med_id TEXT,
                med_class TEXT,
                current_quantity FLOAT,
                min_qty FLOAT,
                max_qty FLOAT,
                outdate_tracking TEXT,
                loaded_as_fraction TEXT,
                backordered TEXT,
                standard_stock TEXT,
                active_orders TEXT,
                days_unused FLOAT,
                snapshot_dt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_device_inventory_history_snapshot_pk
            ON device_inventory_history (snapshot_date, pk)
        """))
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
            CREATE TABLE IF NOT EXISTS mobile_bud_project_portfolio (
                id SERIAL PRIMARY KEY,
                project_key TEXT UNIQUE,
                med_id TEXT,
                med_desc TEXT,
                isa_name TEXT,
                location TEXT,
                barcode_text TEXT,
                action_type TEXT,
                project_status TEXT DEFAULT 'Logged',
                quantity_checked FLOAT,
                project_value FLOAT,
                action_by TEXT,
                action_dt TIMESTAMP DEFAULT NOW(),
                follow_up_dt DATE,
                note TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS inventory_audit (
                pk TEXT PRIMARY KEY,
                med_id TEXT,
                med_desc TEXT,
                med_class TEXT,
                unit_cost FLOAT,
                qty_on_hand FLOAT,
                min_lvl FLOAT,
                max_lvl FLOAT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pyxis_savings_projects (
                id SERIAL PRIMARY KEY,
                project_key TEXT UNIQUE,
                project_name TEXT,
                device TEXT,
                zone TEXT,
                pocket_location TEXT,
                med_id TEXT,
                med_desc TEXT,
                brand_name TEXT,
                action_type TEXT,
                project_status TEXT DEFAULT 'Planned',
                owner TEXT,
                current_quantity FLOAT,
                prior_min_qty FLOAT,
                prior_max_qty FLOAT,
                new_min_qty FLOAT,
                new_max_qty FLOAT,
                excess_quantity FLOAT,
                cost_per_unit FLOAT,
                estimated_savings FLOAT,
                actual_savings FLOAT,
                identified_dt TIMESTAMP DEFAULT NOW(),
                implemented_dt DATE,
                follow_up_dt DATE,
                note TEXT
            )
        """))
        for ddl in [
            "ALTER TABLE pyxis_savings_projects ADD COLUMN IF NOT EXISTS project_name TEXT",
            "ALTER TABLE pyxis_savings_projects ADD COLUMN IF NOT EXISTS project_status TEXT DEFAULT 'Planned'",
            "ALTER TABLE pyxis_savings_projects ADD COLUMN IF NOT EXISTS actual_savings FLOAT",
            "ALTER TABLE pyxis_savings_projects ADD COLUMN IF NOT EXISTS implemented_dt DATE",
            "ALTER TABLE pyxis_savings_projects ADD COLUMN IF NOT EXISTS follow_up_dt DATE",
        ]:
            conn.execute(text(ddl))


ensure_packaging_table()


@st.cache_data(ttl=60)
def load_receiving_history():
    sql = text("""
        SELECT
            pk,
            queue_id,
            dt::timestamp AS received_dt,
            med_id,
            med_desc,
            user_name,
            qty
        FROM pharmacy_orders
        WHERE UPPER(TRIM(COALESCE(priority, ''))) = 'RECEIVING'
          AND dt IS NOT NULL
        ORDER BY dt::timestamp DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_stock_add_history():
    sql = text("""
        SELECT
            pk,
            queue_id,
            priority,
            dt::timestamp AS stock_add_dt,
            med_id,
            med_desc,
            destination,
            user_name,
            qty
        FROM pharmacy_orders
        WHERE med_id IS NOT NULL
          AND dt IS NOT NULL
          AND (
                UPPER(TRIM(COALESCE(priority, ''))) = 'RECEIVING'
             OR priority ILIKE '%return%'
             OR priority ILIKE '%restock%'
             OR priority ILIKE '%instant%'
             OR priority ILIKE '%inventory%'
          )
        ORDER BY dt::timestamp DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_deduction_history():
    sql = text("""
        SELECT
            pk,
            queue_id,
            priority,
            dt::timestamp AS deducted_dt,
            med_id,
            med_desc,
            destination,
            user_name,
            qty
        FROM pharmacy_orders
        WHERE med_id IS NOT NULL
          AND dt IS NOT NULL
          AND (
                priority ILIKE '%pyxis%pull%'
             OR priority ILIKE '%pull%'
             OR priority ILIKE '%dispense%'
             OR priority ILIKE '%deduct%'
          )
        ORDER BY dt::timestamp DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_inventory_qc_actions():
    sql = text("""
        SELECT
            action_key,
            action_type,
            med_id,
            med_desc,
            isa_name,
            location,
            action_status,
            action_dt,
            action_by,
            note,
            replacement_expire_date
        FROM inventory_qc_actions
        ORDER BY action_dt DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


def save_inventory_qc_action(action):
    sql = text("""
        INSERT INTO inventory_qc_actions (
            action_key, action_type, med_id, med_desc, isa_name, location,
            action_status, action_by, note, replacement_expire_date
        )
        VALUES (
            :action_key, :action_type, :med_id, :med_desc, :isa_name, :location,
            :action_status, :action_by, :note, :replacement_expire_date
        )
        ON CONFLICT (action_key) DO UPDATE SET
            action_status = EXCLUDED.action_status,
            action_dt = NOW(),
            action_by = EXCLUDED.action_by,
            note = EXCLUDED.note,
            replacement_expire_date = EXCLUDED.replacement_expire_date
    """)
    with engine.begin() as conn:
        conn.execute(sql, action)
    load_inventory_qc_actions.clear()


def delete_inventory_qc_action(action_key):
    sql = text("""
        DELETE FROM inventory_qc_actions
        WHERE action_key = :action_key
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, {"action_key": action_key})
    load_inventory_qc_actions.clear()
    return result.rowcount or 0


@st.cache_data(ttl=60)
def load_pyxis_savings_projects():
    sql = text("""
        SELECT
            id,
            project_key,
            project_name,
            device,
            zone,
            pocket_location,
            med_id,
            med_desc,
            brand_name,
            action_type,
            project_status,
            owner,
            current_quantity,
            prior_min_qty,
            prior_max_qty,
            new_min_qty,
            new_max_qty,
            excess_quantity,
            cost_per_unit,
            estimated_savings,
            actual_savings,
            identified_dt,
            implemented_dt,
            follow_up_dt,
            note
        FROM pyxis_savings_projects
        ORDER BY identified_dt DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_no_inventory_buyer_review():
    sql = text("""
        WITH no_inventory AS (
            SELECT
                project_key,
                UPPER(TRIM(med_id)) AS med_id,
                med_desc,
                isa_name,
                location,
                action_by,
                action_dt,
                follow_up_dt,
                note
            FROM mobile_bud_project_portfolio
            WHERE action_type = 'No inventory on hand'
        ),
        detailed AS (
            SELECT
                UPPER(TRIM(med_id)) AS med_id,
                station,
                pocket_location,
                SUM(COALESCE(current_count, 0)) AS detailed_current_count
            FROM inventory_detailed
            WHERE COALESCE(TRIM(med_id), '') <> ''
              AND COALESCE(station, '') NOT ILIKE 'CAR%%'
            GROUP BY UPPER(TRIM(med_id)), station, pocket_location
        ),
        device_config AS (
            SELECT
                UPPER(TRIM(med_id)) AS med_id,
                device,
                zone,
                pocket_location,
                status,
                brand_name,
                med_class,
                current_quantity,
                min_qty,
                max_qty,
                outdate_tracking,
                loaded_as_fraction,
                backordered,
                standard_stock,
                active_orders,
                days_unused,
                snapshot_dt
            FROM device_inventory
            WHERE COALESCE(TRIM(med_id), '') <> ''
        )
        SELECT
            n.med_id,
            n.med_desc,
            n.isa_name,
            n.location,
            n.action_by AS checked_by,
            n.action_dt AS checked_dt,
            n.follow_up_dt,
            n.note,
            COALESCE(dc.device, d.station) AS configured_device,
            COALESCE(dc.pocket_location, d.pocket_location) AS configured_pocket,
            dc.zone,
            dc.status AS pocket_status,
            dc.brand_name,
            dc.med_class,
            COALESCE(dc.current_quantity, d.detailed_current_count, 0) AS current_quantity,
            dc.min_qty,
            dc.max_qty,
            dc.standard_stock,
            dc.active_orders,
            dc.backordered,
            dc.outdate_tracking,
            dc.loaded_as_fraction,
            dc.days_unused,
            dc.snapshot_dt,
            CASE
                WHEN COALESCE(dc.min_qty, 0) = 0 THEN 'Min is zero - will not reorder from min/max logic'
                WHEN dc.min_qty IS NULL THEN 'No min configured in latest Device Inventory'
                WHEN COALESCE(dc.max_qty, 0) = 0 THEN 'Max is zero'
                WHEN dc.med_id IS NULL AND d.med_id IS NULL THEN 'No matching pocket configuration found'
                WHEN COALESCE(dc.current_quantity, d.detailed_current_count, 0) = 0 THEN 'Configured pocket is empty'
                ELSE 'Review pocket configuration'
            END AS buyer_review_reason
        FROM no_inventory n
        LEFT JOIN detailed d
          ON n.med_id = d.med_id
         AND UPPER(TRIM(COALESCE(d.station, ''))) = UPPER(TRIM(COALESCE(n.isa_name, '')))
         AND UPPER(TRIM(COALESCE(d.pocket_location, ''))) = UPPER(TRIM(COALESCE(n.location, '')))
        LEFT JOIN device_config dc
          ON n.med_id = dc.med_id
         AND UPPER(TRIM(COALESCE(dc.device, ''))) = UPPER(TRIM(COALESCE(n.isa_name, '')))
         AND UPPER(TRIM(COALESCE(dc.pocket_location, ''))) = UPPER(TRIM(COALESCE(n.location, '')))
        ORDER BY n.action_dt DESC, n.isa_name, n.location, n.med_desc
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


def save_pyxis_savings_project(project):
    sql = text("""
        INSERT INTO pyxis_savings_projects (
            project_key, project_name, device, zone, pocket_location, med_id, med_desc, brand_name,
            action_type, project_status, owner, current_quantity, prior_min_qty, prior_max_qty,
            new_min_qty, new_max_qty, excess_quantity, cost_per_unit, estimated_savings,
            actual_savings, implemented_dt, follow_up_dt, note
        )
        VALUES (
            :project_key, :project_name, :device, :zone, :pocket_location, :med_id, :med_desc, :brand_name,
            :action_type, :project_status, :owner, :current_quantity, :prior_min_qty, :prior_max_qty,
            :new_min_qty, :new_max_qty, :excess_quantity, :cost_per_unit, :estimated_savings,
            :actual_savings, :implemented_dt, :follow_up_dt, :note
        )
        ON CONFLICT (project_key) DO UPDATE SET
            project_name = EXCLUDED.project_name,
            action_type = EXCLUDED.action_type,
            project_status = EXCLUDED.project_status,
            owner = EXCLUDED.owner,
            current_quantity = EXCLUDED.current_quantity,
            prior_min_qty = EXCLUDED.prior_min_qty,
            prior_max_qty = EXCLUDED.prior_max_qty,
            new_min_qty = EXCLUDED.new_min_qty,
            new_max_qty = EXCLUDED.new_max_qty,
            excess_quantity = EXCLUDED.excess_quantity,
            cost_per_unit = EXCLUDED.cost_per_unit,
            estimated_savings = EXCLUDED.estimated_savings,
            actual_savings = EXCLUDED.actual_savings,
            implemented_dt = EXCLUDED.implemented_dt,
            follow_up_dt = EXCLUDED.follow_up_dt,
            note = EXCLUDED.note
    """)
    with engine.begin() as conn:
        conn.execute(sql, project)
    load_pyxis_savings_projects.clear()


def update_packaged_bud_after_removal(med_id, current_expire_date, new_bud_date):
    sql = text("""
        UPDATE packaged_meds
        SET bud = :new_bud_date
        WHERE med_id = :med_id
          AND COALESCE(bud, hospital_expire_date) = :current_expire_date
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, {
            "med_id": med_id,
            "current_expire_date": current_expire_date,
            "new_bud_date": new_bud_date,
        })
    load_packaging_history.clear()
    return result.rowcount or 0


@st.cache_data(ttl=60)
def load_latest_isa_items():
    sql = text("""
        WITH latest AS (
            SELECT MAX(snapshot_date) AS snapshot_date
            FROM cycle_count_status
        )
        SELECT
            c.snapshot_date,
            c.isa_name,
            c.med_id,
            c.med_desc,
            c.location,
            c.cycle_count_interval,
            c.last_cycle_count,
            c.days_since_last_count,
            c.days_over_due
        FROM cycle_count_status c
        JOIN latest l ON c.snapshot_date = l.snapshot_date
        ORDER BY c.isa_name, c.med_desc, c.location
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_inventory_counts():
    sql = text("""
        SELECT
            station AS isa_name,
            med_id,
            SUM(current_count) AS current_count,
            COUNT(*) AS pocket_count
        FROM inventory_detailed
        GROUP BY station, med_id
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_current_pyxis_inventory():
    sql = text("""
        SELECT
            station,
            med_id,
            med_desc,
            current_count,
            pocket_location,
            unit_cost,
            current_count * COALESCE(unit_cost, 0) AS inventory_value
        FROM inventory_detailed
        WHERE COALESCE(current_count, 0) > 0
          AND COALESCE(station, '') NOT ILIKE 'CAR%%'
        ORDER BY station, pocket_location
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_device_inventory():
    sql = text("""
        SELECT
            med_desc,
            device,
            zone,
            pocket_location,
            status,
            brand_name,
            med_id,
            med_class,
            current_quantity,
            min_qty,
            max_qty,
            outdate_tracking,
            loaded_as_fraction,
            backordered,
            standard_stock,
            active_orders,
            days_unused,
            snapshot_dt
        FROM device_inventory
        ORDER BY days_unused DESC, device, med_desc
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_device_inventory_snapshot_dates():
    sql = text("""
        SELECT snapshot_date
        FROM device_inventory_history
        GROUP BY snapshot_date
        ORDER BY snapshot_date DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_device_inventory_daily_delta():
    sql = text("""
        WITH latest_date AS (
            SELECT MAX(snapshot_date) AS snapshot_date
            FROM device_inventory_history
        ),
        previous_date AS (
            SELECT MAX(snapshot_date) AS snapshot_date
            FROM device_inventory_history
            WHERE snapshot_date < (SELECT snapshot_date FROM latest_date)
        ),
        latest_rows AS (
            SELECT *
            FROM device_inventory_history
            WHERE snapshot_date = (SELECT snapshot_date FROM latest_date)
        ),
        previous_rows AS (
            SELECT *
            FROM device_inventory_history
            WHERE snapshot_date = (SELECT snapshot_date FROM previous_date)
        )
        SELECT
            COALESCE(l.pk, p.pk) AS pk,
            (SELECT snapshot_date FROM latest_date) AS latest_snapshot_date,
            (SELECT snapshot_date FROM previous_date) AS previous_snapshot_date,
            COALESCE(l.device, p.device) AS device,
            COALESCE(l.zone, p.zone) AS zone,
            COALESCE(l.pocket_location, p.pocket_location) AS pocket_location,
            COALESCE(l.med_id, p.med_id) AS med_id,
            COALESCE(l.med_desc, p.med_desc) AS med_desc,
            COALESCE(l.brand_name, p.brand_name) AS brand_name,
            COALESCE(l.status, p.status) AS status,
            COALESCE(l.standard_stock, p.standard_stock) AS standard_stock,
            COALESCE(l.active_orders, p.active_orders) AS active_orders,
            COALESCE(l.days_unused, p.days_unused) AS days_unused,
            COALESCE(p.current_quantity, 0) AS previous_quantity,
            COALESCE(l.current_quantity, 0) AS current_quantity,
            COALESCE(l.current_quantity, 0) - COALESCE(p.current_quantity, 0) AS net_quantity_change,
            GREATEST(COALESCE(p.current_quantity, 0) - COALESCE(l.current_quantity, 0), 0) AS removed_quantity,
            GREATEST(COALESCE(l.current_quantity, 0) - COALESCE(p.current_quantity, 0), 0) AS added_quantity,
            CASE
                WHEN l.pk IS NULL THEN 'Removed from device/pocket'
                WHEN p.pk IS NULL THEN 'New on device/pocket'
                WHEN COALESCE(l.current_quantity, 0) < COALESCE(p.current_quantity, 0) THEN 'Net removed'
                WHEN COALESCE(l.current_quantity, 0) > COALESCE(p.current_quantity, 0) THEN 'Net added'
                ELSE 'No quantity change'
            END AS movement_type
        FROM latest_rows l
        FULL OUTER JOIN previous_rows p ON l.pk = p.pk
        ORDER BY removed_quantity DESC, added_quantity DESC, device, med_desc
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_clinical_pyxis_activity(start_date, end_date):
    sql = text("""
        SELECT
            station_name AS device,
            med_id,
            med_desc,
            transaction_type,
            dt,
            user_name,
            user_type,
            qty,
            waste_amount
        FROM audit_transaction_detail_rc
        WHERE dt::date BETWEEN :start_date AND :end_date
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
    """)
    try:
        with engine.connect() as conn:
            return pd.read_sql(sql, conn, params={"start_date": start_date, "end_date": end_date})
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_med_costs():
    sql = text("""
        SELECT
            UPPER(TRIM(med_id)) AS med_id,
            cost_per_unit
        FROM med_costs
        WHERE COALESCE(TRIM(med_id), '') <> ''
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_inventory_audit():
    sql = text("""
        SELECT
            UPPER(TRIM(med_id)) AS med_id,
            med_desc,
            med_class,
            unit_cost,
            qty_on_hand,
            min_lvl,
            max_lvl
        FROM inventory_audit
        WHERE COALESCE(TRIM(med_id), '') <> ''
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_packaging_history():
    sql = text("""
        SELECT
            dispense_dt,
            med_id,
            med_desc,
            dose_form,
            qty_per_pack,
            qoh,
            manufacturer,
            mfg_expire_date,
            hospital_lot_number,
            hospital_expire_date,
            bud,
            packaged_by,
            confirmer
        FROM packaged_meds
        WHERE dispense_dt IS NOT NULL
          AND COALESCE(hospital_lot_number, '') <> 'MANUAL-BUD'
          AND COALESCE(dose_form, '') <> 'Manual BUD'
        ORDER BY dispense_dt DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


def prep_receiving(df):
    if df.empty:
        return df
    out = df.copy()
    out["received_dt"] = pd.to_datetime(out["received_dt"], errors="coerce")
    out["med_id"] = out["med_id"].fillna("").astype(str).str.strip().str.upper()
    out["med_desc"] = out["med_desc"].fillna("").astype(str).str.strip()
    out["user_name"] = out["user_name"].fillna("Unknown").astype(str).str.strip()
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").fillna(0)
    return out.dropna(subset=["received_dt"])


def classify_stock_add_mode(priority):
    text = str(priority or "").strip().lower()
    if text == "receiving":
        return "Receiving"
    if "inventory" in text:
        return "Inventory Move"
    if "instant" in text and "return" in text:
        return "Instant Return"
    if "instant" in text and "restock" in text:
        return "Instant Restock"
    if "restock" in text:
        return "Restock"
    if "return" in text:
        return "Return"
    return "Other stock-add candidate"


def prep_stock_add_history(df):
    if df.empty:
        return df
    out = df.copy()
    out["stock_add_dt"] = pd.to_datetime(out["stock_add_dt"], errors="coerce")
    out["med_id"] = out["med_id"].fillna("").astype(str).str.strip().str.upper()
    out["med_desc"] = out["med_desc"].fillna("").astype(str).str.strip()
    out["priority"] = out["priority"].fillna("").astype(str).str.strip()
    out["user_name"] = out["user_name"].fillna("").astype(str).str.strip()
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").fillna(0)
    out["stock_add_mode"] = out["priority"].apply(classify_stock_add_mode)
    return out.dropna(subset=["stock_add_dt"])


def prep_deduction_history(df):
    if df.empty:
        return df
    out = df.copy()
    out["deducted_dt"] = pd.to_datetime(out["deducted_dt"], errors="coerce")
    out["med_id"] = out["med_id"].fillna("").astype(str).str.strip().str.upper()
    out["med_desc"] = out["med_desc"].fillna("").astype(str).str.strip()
    out["priority"] = out["priority"].fillna("").astype(str).str.strip()
    out["destination"] = out["destination"].fillna("").astype(str).str.strip()
    out["user_name"] = out["user_name"].fillna("").astype(str).str.strip()
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").fillna(0)
    return out.dropna(subset=["deducted_dt"])


def prep_isa_items(df):
    if df.empty:
        return df
    out = df.copy()
    out["snapshot_date"] = pd.to_datetime(out["snapshot_date"], errors="coerce").dt.date
    out["isa_name"] = out["isa_name"].fillna("").astype(str).str.strip()
    out["med_id"] = out["med_id"].fillna("").astype(str).str.strip().str.upper()
    out["med_desc"] = out["med_desc"].fillna("").astype(str).str.strip()
    out["location"] = out["location"].fillna("").astype(str).str.strip()
    out["last_cycle_count"] = pd.to_datetime(out["last_cycle_count"], errors="coerce")
    return out


def prep_packaging(df):
    if df.empty:
        return df
    out = df.copy()
    out["dispense_dt"] = pd.to_datetime(out["dispense_dt"], errors="coerce")
    out["med_id"] = out["med_id"].fillna("").astype(str).str.strip().str.upper()
    out["med_desc"] = out["med_desc"].fillna("").astype(str).str.strip()
    out["hospital_expire_date"] = pd.to_datetime(out["hospital_expire_date"], errors="coerce")
    out["bud"] = pd.to_datetime(out["bud"], errors="coerce")
    out["packaged_expire_date"] = out["bud"].fillna(out["hospital_expire_date"])
    out["packaged_expire_date"] = out["packaged_expire_date"].fillna(out["dispense_dt"] + pd.Timedelta(days=365))
    out["qty_per_pack"] = pd.to_numeric(out["qty_per_pack"], errors="coerce").fillna(0)
    out["qoh"] = pd.to_numeric(out["qoh"], errors="coerce").fillna(0)
    return out.dropna(subset=["dispense_dt"])


def prep_pyxis_inventory(df):
    if df.empty:
        return df
    out = df.copy()
    out["station"] = out["station"].fillna("").astype(str).str.strip()
    out["med_id"] = out["med_id"].fillna("").astype(str).str.strip().str.upper()
    out["med_desc"] = out["med_desc"].fillna("").astype(str).str.strip()
    out["pocket_location"] = out["pocket_location"].fillna("").astype(str).str.strip()
    out["current_count"] = pd.to_numeric(out["current_count"], errors="coerce").fillna(0)
    out["unit_cost"] = pd.to_numeric(out["unit_cost"], errors="coerce").fillna(0)
    out["inventory_value"] = pd.to_numeric(out["inventory_value"], errors="coerce").fillna(0)
    return out


def prep_device_inventory(df):
    if df.empty:
        return df
    out = df.copy()
    for col in [
        "med_desc", "device", "zone", "pocket_location", "status", "brand_name",
        "med_id", "med_class", "outdate_tracking", "loaded_as_fraction",
        "backordered", "standard_stock", "active_orders",
    ]:
        out[col] = out[col].fillna("").astype(str).str.strip()
    out["med_id"] = out["med_id"].str.upper()
    for col in ["current_quantity", "min_qty", "max_qty", "days_unused"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["snapshot_dt"] = pd.to_datetime(out["snapshot_dt"], errors="coerce")

    is_exempt = out["standard_stock"].str.upper().eq("Y") | out["active_orders"].str.upper().eq("Y")
    over_28 = out["days_unused"] > 28
    over_60 = out["days_unused"] > 60

    out["unload_status"] = "Compliant"
    out.loc[over_28 & is_exempt, "unload_status"] = "Exempt"
    out.loc[over_28 & ~is_exempt, "unload_status"] = "Unload Due"
    out.loc[over_60 & ~is_exempt, "unload_status"] = "High Priority"
    out["exemption_reason"] = ""
    out.loc[out["standard_stock"].str.upper().eq("Y"), "exemption_reason"] = "Standard stock"
    out.loc[out["active_orders"].str.upper().eq("Y"), "exemption_reason"] = out["exemption_reason"].where(
        out["exemption_reason"].eq(""),
        out["exemption_reason"] + "; ",
    ) + "Active order"
    valid_yn = {"Y", "N", ""}
    out["standard_stock_check"] = "OK"
    out["outdate_tracking_check"] = "OK"
    out.loc[~out["standard_stock"].str.upper().isin(valid_yn), "standard_stock_check"] = "Invalid standard stock flag"
    out.loc[~out["outdate_tracking"].str.upper().isin(valid_yn), "outdate_tracking_check"] = "Invalid outdate tracking flag"
    probable_supply = out["med_desc"].str.contains(r"\b(key|paper|misc|premix)\b", case=False, na=False)
    out.loc[
        out["outdate_tracking"].str.upper().eq("N") & ~probable_supply,
        "outdate_tracking_check",
    ] = "Outdate tracking off - verify"
    return out


def prep_med_costs(df):
    if df.empty:
        return pd.DataFrame(columns=["med_id", "cost_per_unit"])
    out = df.copy()
    out["med_id"] = out["med_id"].fillna("").astype(str).str.strip().str.upper()
    out["cost_per_unit"] = pd.to_numeric(out["cost_per_unit"], errors="coerce").fillna(0)
    return out.drop_duplicates("med_id", keep="last")


def prep_inventory_audit(df):
    if df.empty:
        return pd.DataFrame(columns=["med_id", "med_desc", "med_class", "unit_cost", "qty_on_hand", "inventory_value"])
    out = df.copy()
    out["med_id"] = out["med_id"].fillna("").astype(str).str.strip().str.upper()
    out["med_desc"] = out["med_desc"].fillna("").astype(str).str.strip()
    out["med_class"] = out["med_class"].fillna("").astype(str).str.strip()
    for col in ["unit_cost", "qty_on_hand"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["inventory_value"] = out["unit_cost"] * out["qty_on_hand"]
    return out


def prep_pyxis_savings_projects(df):
    if df.empty:
        return df
    out = df.copy()
    for col in [
        "project_name", "device", "zone", "pocket_location", "med_id", "med_desc",
        "brand_name", "action_type", "project_status", "owner", "note",
    ]:
        out[col] = out[col].fillna("").astype(str).str.strip()
    for col in [
        "current_quantity", "prior_min_qty", "prior_max_qty", "new_min_qty", "new_max_qty",
        "excess_quantity", "cost_per_unit", "estimated_savings", "actual_savings",
    ]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["identified_dt"] = pd.to_datetime(out["identified_dt"], errors="coerce")
    out["implemented_dt"] = pd.to_datetime(out["implemented_dt"], errors="coerce")
    out["follow_up_dt"] = pd.to_datetime(out["follow_up_dt"], errors="coerce")
    return out


PROCEDURAL_DEVICE_TERMS = [
    "ANES",
    "ANESTH",
    "CATH",
    "ENDO",
    "GI",
    "IR",
    "OB",
    "OR",
    "PACU",
    "PREOP",
    "PROC",
    "PROCED",
    "SURG",
]


def suggest_procedural_devices(device_options):
    suggested = []
    for device in device_options:
        normalized = re.sub(r"[^A-Z0-9]", "", str(device).upper())
        if any(term in normalized for term in PROCEDURAL_DEVICE_TERMS):
            suggested.append(device)
    return suggested


def build_receiving_summary(receiving):
    if receiving.empty:
        return pd.DataFrame(columns=[
            "med_id", "first_received", "last_received", "receiving_events",
            "total_received_qty", "last_received_by",
        ])

    sorted_receiving = receiving.sort_values("received_dt")
    summary = sorted_receiving.groupby("med_id", dropna=False).agg(
        first_received=("received_dt", "min"),
        last_received=("received_dt", "max"),
        receiving_events=("pk", "count"),
        total_received_qty=("qty", "sum"),
    ).reset_index()

    last_rows = sorted_receiving.groupby("med_id", dropna=False).tail(1)[["med_id", "user_name"]]
    last_rows = last_rows.rename(columns={"user_name": "last_received_by"})
    return summary.merge(last_rows, on="med_id", how="left")


def build_pyxis_overstock_savings(device_inventory, deduction_history, med_costs):
    columns = [
        "device", "zone", "pocket_location", "med_id", "med_desc", "brand_name",
        "current_quantity", "min_qty", "max_qty", "days_unused", "standard_stock",
        "active_orders", "status", "cost_per_unit", "current_value", "deduct_qty_30d",
        "deduct_qty_90d", "last_deducted", "avg_daily_use_90d", "suggested_max",
        "excess_quantity", "estimated_excess_value", "savings_priority", "suggested_action",
    ]
    if device_inventory.empty:
        return pd.DataFrame(columns=columns)

    base = device_inventory.copy()
    if not med_costs.empty:
        base = base.merge(med_costs, on="med_id", how="left")
    if "cost_per_unit" not in base.columns:
        base["cost_per_unit"] = 0
    base["cost_per_unit"] = pd.to_numeric(base["cost_per_unit"], errors="coerce").fillna(0)

    today = pd.Timestamp.today().normalize()
    if deduction_history.empty:
        usage = pd.DataFrame(columns=["med_id", "deduct_qty_30d", "deduct_qty_90d", "last_deducted"])
    else:
        deductions = deduction_history.copy()
        deductions["deducted_dt"] = pd.to_datetime(deductions["deducted_dt"], errors="coerce")
        deductions["med_id"] = deductions["med_id"].fillna("").astype(str).str.strip().str.upper()
        deductions["qty"] = pd.to_numeric(deductions["qty"], errors="coerce").fillna(0).abs()
        recent_90 = deductions[deductions["deducted_dt"].ge(today - pd.Timedelta(days=90))]
        recent_30 = deductions[deductions["deducted_dt"].ge(today - pd.Timedelta(days=30))]
        usage_90 = recent_90.groupby("med_id", dropna=False).agg(
            deduct_qty_90d=("qty", "sum"),
            last_deducted=("deducted_dt", "max"),
        )
        usage_30 = recent_30.groupby("med_id", dropna=False).agg(deduct_qty_30d=("qty", "sum"))
        usage = usage_90.join(usage_30, how="outer").reset_index()

    base = base.merge(usage, on="med_id", how="left")
    for col in ["deduct_qty_30d", "deduct_qty_90d"]:
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0)
    base["last_deducted"] = pd.to_datetime(base["last_deducted"], errors="coerce")
    base["avg_daily_use_90d"] = base["deduct_qty_90d"] / 90
    base["current_value"] = base["current_quantity"] * base["cost_per_unit"]

    is_exempt = base["standard_stock"].str.upper().eq("Y") | base["active_orders"].str.upper().eq("Y")
    usage_buffer = (base["avg_daily_use_90d"] * 14).apply(lambda value: int(value) + (0 if value == int(value) else 1))
    conservative_floor = base["min_qty"].clip(lower=0)
    base["suggested_max"] = pd.concat([usage_buffer, conservative_floor], axis=1).max(axis=1)
    base.loc[base["deduct_qty_90d"].eq(0) & base["days_unused"].ge(28) & ~is_exempt, "suggested_max"] = conservative_floor
    base.loc[is_exempt, "suggested_max"] = base[["suggested_max", "min_qty"]].max(axis=1)
    max_cap = base["max_qty"].where(base["max_qty"].gt(0), base["suggested_max"])
    base["suggested_max"] = pd.concat([base["suggested_max"], max_cap], axis=1).min(axis=1)
    base["excess_quantity"] = (base["current_quantity"] - base["suggested_max"]).clip(lower=0)
    base["estimated_excess_value"] = base["excess_quantity"] * base["cost_per_unit"]

    base["savings_priority"] = "Monitor"
    base.loc[base["estimated_excess_value"].ge(100), "savings_priority"] = "High Dollar Review"
    base.loc[base["estimated_excess_value"].between(25, 99.99, inclusive="both"), "savings_priority"] = "Review"
    base.loc[
        base["deduct_qty_90d"].eq(0) & base["days_unused"].ge(28) & base["current_quantity"].gt(0) & ~is_exempt,
        "savings_priority",
    ] = "No-Movement Removal Review"
    base.loc[is_exempt, "savings_priority"] = "Exempt / Verify Need"

    base["suggested_action"] = "Monitor usage"
    base.loc[base["excess_quantity"].gt(0), "suggested_action"] = "Review Pyxis max/par reduction"
    base.loc[
        base["deduct_qty_90d"].eq(0) & base["days_unused"].ge(28) & base["current_quantity"].gt(0) & ~is_exempt,
        "suggested_action",
    ] = "Consider unload or remove from device"
    base.loc[is_exempt & base["excess_quantity"].gt(0), "suggested_action"] = "Verify standard stock/active order need"

    return base[columns].sort_values(
        ["estimated_excess_value", "excess_quantity", "days_unused"],
        ascending=[False, False, False],
    )


def build_inventory_turns(deduction_history, pyxis_inventory, inventory_audit, med_costs, start_date, end_date):
    detail_columns = [
        "inventory_source", "med_id", "med_desc", "on_hand_qty", "inventory_value",
        "issue_qty", "issue_value", "annualized_issue_value", "inventory_turns",
        "cost_per_unit",
    ]
    if deduction_history.empty:
        usage = pd.DataFrame(columns=["med_id", "issue_qty", "last_issue_dt"])
    else:
        usage = deduction_history.copy()
        usage["deducted_dt"] = pd.to_datetime(usage["deducted_dt"], errors="coerce")
        usage = usage[
            usage["deducted_dt"].dt.date.between(start_date, end_date)
        ].copy()
        usage["med_id"] = usage["med_id"].fillna("").astype(str).str.strip().str.upper()
        usage["qty"] = pd.to_numeric(usage["qty"], errors="coerce").fillna(0).abs()
        usage = usage.groupby("med_id", dropna=False).agg(
            issue_qty=("qty", "sum"),
            last_issue_dt=("deducted_dt", "max"),
        ).reset_index()

    cost_lookup = med_costs.copy() if not med_costs.empty else pd.DataFrame(columns=["med_id", "cost_per_unit"])
    cost_lookup["med_id"] = cost_lookup["med_id"].fillna("").astype(str).str.strip().str.upper()
    cost_lookup["cost_per_unit"] = pd.to_numeric(cost_lookup["cost_per_unit"], errors="coerce").fillna(0)

    sources = []
    if not pyxis_inventory.empty:
        pyxis = pyxis_inventory.copy()
        pyxis["med_id"] = pyxis["med_id"].fillna("").astype(str).str.strip().str.upper()
        pyxis["current_count"] = pd.to_numeric(pyxis["current_count"], errors="coerce").fillna(0)
        pyxis["unit_cost"] = pd.to_numeric(pyxis["unit_cost"], errors="coerce").fillna(0)
        pyxis_source = pyxis.groupby("med_id", dropna=False).agg(
            med_desc=("med_desc", "first"),
            on_hand_qty=("current_count", "sum"),
            inventory_value=("inventory_value", "sum"),
            unit_cost=("unit_cost", "max"),
        ).reset_index()
        pyxis_source["inventory_source"] = "Pyxis detailed inventory"
        sources.append(pyxis_source)

    if not inventory_audit.empty:
        audit = inventory_audit.copy()
        audit_source = audit.groupby("med_id", dropna=False).agg(
            med_desc=("med_desc", "first"),
            on_hand_qty=("qty_on_hand", "sum"),
            inventory_value=("inventory_value", "sum"),
            unit_cost=("unit_cost", "max"),
        ).reset_index()
        audit_source["inventory_source"] = "Inventory audit"
        sources.append(audit_source)

    if not sources:
        return pd.DataFrame(), pd.DataFrame(columns=detail_columns)

    days = max((pd.to_datetime(end_date) - pd.to_datetime(start_date)).days + 1, 1)
    annualization_factor = 365 / days
    details = []
    for source in sources:
        detail = source.merge(usage, on="med_id", how="left").merge(cost_lookup, on="med_id", how="left")
        detail["issue_qty"] = pd.to_numeric(detail["issue_qty"], errors="coerce").fillna(0)
        detail["cost_per_unit"] = pd.to_numeric(detail["cost_per_unit"], errors="coerce").fillna(0)
        detail["cost_per_unit"] = detail["cost_per_unit"].where(detail["cost_per_unit"].gt(0), detail["unit_cost"])
        detail["issue_value"] = detail["issue_qty"] * detail["cost_per_unit"]
        detail["annualized_issue_value"] = detail["issue_value"] * annualization_factor
        detail["inventory_value"] = pd.to_numeric(detail["inventory_value"], errors="coerce").fillna(0)
        detail["inventory_turns"] = detail["annualized_issue_value"] / detail["inventory_value"].replace({0: pd.NA})
        details.append(detail[detail_columns])

    detail_df = pd.concat(details, ignore_index=True)
    summary = (
        detail_df.groupby("inventory_source", dropna=False)
        .agg(
            meds=("med_id", "nunique"),
            on_hand_qty=("on_hand_qty", "sum"),
            inventory_value=("inventory_value", "sum"),
            issue_qty=("issue_qty", "sum"),
            issue_value=("issue_value", "sum"),
            annualized_issue_value=("annualized_issue_value", "sum"),
        )
        .reset_index()
    )
    summary["inventory_turns"] = summary["annualized_issue_value"] / summary["inventory_value"].replace({0: pd.NA})
    return summary, detail_df.sort_values(["inventory_source", "inventory_turns"], ascending=[True, False])


def build_packaging_summary(packaging):
    if packaging.empty:
        return pd.DataFrame(columns=[
            "med_id", "last_packaged", "packaging_events", "latest_packaged_expire_date",
            "last_packaged_by", "latest_hospital_lot_number",
        ])

    sorted_packaging = packaging.sort_values("dispense_dt")
    summary = sorted_packaging.groupby("med_id", dropna=False).agg(
        last_packaged=("dispense_dt", "max"),
        first_packaged=("dispense_dt", "min"),
        packaging_events=("dispense_dt", "count"),
        latest_packaged_expire_date=("packaged_expire_date", "max"),
    ).reset_index()

    last_rows = sorted_packaging.groupby("med_id", dropna=False).tail(1)[[
        "med_id", "packaged_by", "hospital_lot_number", "manufacturer",
    ]]
    last_rows = last_rows.rename(columns={
        "packaged_by": "last_packaged_by",
        "hospital_lot_number": "latest_hospital_lot_number",
        "manufacturer": "latest_packaged_manufacturer",
    })
    return summary.merge(last_rows, on="med_id", how="left")


def build_stock_add_summary(stock_add_history):
    columns = [
        "med_id", "first_stock_add", "last_stock_add", "stock_add_events",
        "return_restock_events", "latest_stock_add_mode", "latest_stock_add_priority",
        "latest_stock_add_by", "latest_stock_add_qty", "stock_add_trail_status",
        "stock_add_followup",
    ]
    if stock_add_history.empty:
        return pd.DataFrame(columns=columns)

    sorted_stock = stock_add_history.sort_values("stock_add_dt")
    summary = sorted_stock.groupby("med_id", dropna=False).agg(
        first_stock_add=("stock_add_dt", "min"),
        last_stock_add=("stock_add_dt", "max"),
        stock_add_events=("pk", "count"),
        return_restock_events=("stock_add_mode", lambda s: s.isin([
            "Return", "Instant Return", "Instant Restock", "Restock", "Inventory Move",
        ]).sum()),
    ).reset_index()

    last_rows = sorted_stock.groupby("med_id", dropna=False).tail(1)[[
        "med_id", "stock_add_mode", "priority", "user_name", "qty",
    ]].rename(columns={
        "stock_add_mode": "latest_stock_add_mode",
        "priority": "latest_stock_add_priority",
        "user_name": "latest_stock_add_by",
        "qty": "latest_stock_add_qty",
    })
    summary = summary.merge(last_rows, on="med_id", how="left")
    summary["stock_add_trail_status"] = "Stock-add trail found"
    summary.loc[summary["return_restock_events"].gt(0), "stock_add_trail_status"] = "Return/restock trail found"
    summary["stock_add_followup"] = "Stock-add trail found - review latest event if expiration concern remains"
    return summary[columns]


def build_deduction_summary(deduction_history):
    columns = [
        "med_id", "last_deducted", "deduction_events", "total_deducted_qty",
        "last_deducted_by", "last_deduction_destination", "last_deduction_priority",
        "last_deduction_qty",
    ]
    if deduction_history.empty:
        return pd.DataFrame(columns=columns)

    sorted_deductions = deduction_history.sort_values("deducted_dt")
    summary = sorted_deductions.groupby("med_id", dropna=False).agg(
        last_deducted=("deducted_dt", "max"),
        deduction_events=("pk", "count"),
        total_deducted_qty=("qty", "sum"),
    ).reset_index()
    last_rows = sorted_deductions.groupby("med_id", dropna=False).tail(1)[[
        "med_id", "user_name", "destination", "priority", "qty",
    ]].rename(columns={
        "user_name": "last_deducted_by",
        "destination": "last_deduction_destination",
        "priority": "last_deduction_priority",
        "qty": "last_deduction_qty",
    })
    return summary.merge(last_rows, on="med_id", how="left")[columns]


def build_pyxis_exposure_summary(pyxis_inventory):
    columns = [
        "med_id", "pyxis_machine_count", "pyxis_pocket_count",
        "pyxis_total_count", "pyxis_machines_to_check",
    ]
    if pyxis_inventory.empty:
        return pd.DataFrame(columns=columns)

    work = pyxis_inventory.copy()
    work["med_id"] = work["med_id"].fillna("").astype(str).str.strip().str.upper()
    work["station"] = work["station"].fillna("").astype(str).str.strip()
    work["current_count"] = pd.to_numeric(work["current_count"], errors="coerce").fillna(0)
    summary = (
        work.groupby("med_id", dropna=False)
        .agg(
            pyxis_machine_count=("station", "nunique"),
            pyxis_pocket_count=("station", "count"),
            pyxis_total_count=("current_count", "sum"),
            pyxis_machines_to_check=("station", lambda s: ", ".join(sorted(set(s.dropna().astype(str))))),
        )
        .reset_index()
    )
    return summary[columns]


def build_manual_bud_summary(qc_actions):
    columns = ["med_id", "manual_bud_date", "manual_bud_by", "manual_bud_note"]
    if qc_actions.empty:
        return pd.DataFrame(columns=columns)

    actions = qc_actions[
        qc_actions["action_type"].eq("active_bud")
        | qc_actions["action_status"].isin([
            "Manual BUD record created",
            "Old product removed - BUD updated",
            "Expired product removed - none remaining",
        ])
    ].copy()
    if actions.empty:
        return pd.DataFrame(columns=columns)

    actions["replacement_expire_date"] = pd.to_datetime(actions["replacement_expire_date"], errors="coerce")
    actions["action_dt"] = pd.to_datetime(actions["action_dt"], errors="coerce")
    actions["med_id"] = actions["med_id"].fillna("").astype(str).str.strip().str.upper()
    actions = actions[actions["replacement_expire_date"].notna()].sort_values("action_dt")
    latest = actions.groupby("med_id", dropna=False).tail(1).rename(columns={
        "replacement_expire_date": "manual_bud_date",
        "action_by": "manual_bud_by",
        "note": "manual_bud_note",
    })
    return latest[columns]


def build_active_bud_review_summary(qc_actions):
    columns = [
        "action_key",
        "isa_name",
        "location",
        "med_id",
        "active_bud_review_status",
        "active_bud_review_dt",
        "active_bud_review_by",
        "active_bud_review_note",
        "reviewed_active_bud_date",
    ]
    if qc_actions.empty:
        return pd.DataFrame(columns=columns)

    actions = qc_actions[
        qc_actions["action_type"].eq("active_bud")
        | qc_actions["action_status"].isin([
            "Manual BUD record created",
            "Old product removed - BUD updated",
            "Expired product removed - none remaining",
        ])
    ].copy()
    if actions.empty:
        return pd.DataFrame(columns=columns)

    for col in ["isa_name", "location", "med_id"]:
        actions[col] = actions[col].fillna("").astype(str).str.strip()
    actions["med_id"] = actions["med_id"].str.upper()
    actions["action_dt"] = pd.to_datetime(actions["action_dt"], errors="coerce")
    actions["replacement_expire_date"] = pd.to_datetime(actions["replacement_expire_date"], errors="coerce")
    actions = actions.sort_values("action_dt")
    latest = actions.groupby(["isa_name", "location", "med_id"], dropna=False).tail(1).rename(columns={
        "action_status": "active_bud_review_status",
        "action_dt": "active_bud_review_dt",
        "action_by": "active_bud_review_by",
        "note": "active_bud_review_note",
        "replacement_expire_date": "reviewed_active_bud_date",
    })
    return latest[columns]


def build_isa_lifecycle(isa_items, receiving_summary, inventory_counts, packaging_summary, stock_add_summary, deduction_summary, manual_bud_summary):
    if isa_items.empty:
        return pd.DataFrame()

    base = isa_items.merge(receiving_summary, on="med_id", how="left")
    if not stock_add_summary.empty:
        base = base.merge(stock_add_summary, on="med_id", how="left")
    if not deduction_summary.empty:
        base = base.merge(deduction_summary, on="med_id", how="left")
    if not packaging_summary.empty:
        base = base.merge(packaging_summary, on="med_id", how="left")
    if not manual_bud_summary.empty:
        base = base.merge(manual_bud_summary, on="med_id", how="left")
    if not inventory_counts.empty:
        inv = inventory_counts.copy()
        inv["isa_name"] = inv["isa_name"].fillna("").astype(str).str.strip()
        inv["med_id"] = inv["med_id"].fillna("").astype(str).str.strip().str.upper()
        base = base.merge(inv, on=["isa_name", "med_id"], how="left")

    today = pd.Timestamp.today().normalize()
    base["last_received"] = pd.to_datetime(base["last_received"], errors="coerce")
    base["first_received"] = pd.to_datetime(base["first_received"], errors="coerce")
    base["days_since_last_received"] = (today - base["last_received"]).dt.days
    base["days_since_first_received"] = (today - base["first_received"]).dt.days
    base["last_cycle_count"] = pd.to_datetime(base["last_cycle_count"], errors="coerce")
    base["days_since_last_cycle_count"] = (today - base["last_cycle_count"]).dt.days
    if "last_deducted" not in base.columns:
        base["last_deducted"] = pd.NaT
    base["last_deducted"] = pd.to_datetime(base["last_deducted"], errors="coerce")
    base["days_since_last_deducted"] = (today - base["last_deducted"]).dt.days
    for col in ["deduction_events", "total_deducted_qty", "last_deduction_qty"]:
        if col not in base.columns:
            base[col] = 0
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0)
    base["deduction_events"] = base["deduction_events"].astype(int)
    for col in ["last_deducted_by", "last_deduction_destination", "last_deduction_priority"]:
        if col not in base.columns:
            base[col] = ""
        base[col] = base[col].fillna("").astype(str)
    if "last_packaged" not in base.columns:
        base["last_packaged"] = pd.NaT
    if "latest_packaged_expire_date" not in base.columns:
        base["latest_packaged_expire_date"] = pd.NaT
    base["last_packaged"] = pd.to_datetime(base["last_packaged"], errors="coerce")
    base["latest_packaged_expire_date"] = pd.to_datetime(base["latest_packaged_expire_date"], errors="coerce")
    if "manual_bud_date" not in base.columns:
        base["manual_bud_date"] = pd.NaT
    base["manual_bud_date"] = pd.to_datetime(base["manual_bud_date"], errors="coerce")
    for col in ["manual_bud_by", "manual_bud_note"]:
        if col not in base.columns:
            base[col] = ""
        base[col] = base[col].fillna("").astype(str)
    base["days_since_last_packaged"] = (today - base["last_packaged"]).dt.days
    base["days_until_packaged_expire"] = (base["latest_packaged_expire_date"] - today).dt.days
    base["receiving_events"] = pd.to_numeric(base["receiving_events"], errors="coerce").fillna(0).astype(int)
    base["total_received_qty"] = pd.to_numeric(base["total_received_qty"], errors="coerce").fillna(0)
    if "packaging_events" not in base.columns:
        base["packaging_events"] = 0
    for col in ["last_packaged_by", "latest_hospital_lot_number", "latest_packaged_manufacturer"]:
        if col not in base.columns:
            base[col] = ""
    base["packaging_events"] = pd.to_numeric(base["packaging_events"], errors="coerce").fillna(0).astype(int)
    base["active_bud_date"] = base["latest_packaged_expire_date"].fillna(base["manual_bud_date"])
    base["active_bud_source"] = "None"
    base.loc[base["latest_packaged_expire_date"].notna(), "active_bud_source"] = "Packaging report"
    base.loc[base["latest_packaged_expire_date"].isna() & base["manual_bud_date"].notna(), "active_bud_source"] = "Manual BUD update"
    if "current_count" not in base.columns:
        base["current_count"] = 0
    if "pocket_count" not in base.columns:
        base["pocket_count"] = 0
    base["current_count"] = pd.to_numeric(base["current_count"], errors="coerce").fillna(0)
    base["pocket_count"] = pd.to_numeric(base["pocket_count"], errors="coerce").fillna(0).astype(int)
    base["receiving_status"] = base["last_received"].apply(lambda value: "No Receiving Match" if pd.isna(value) else "Matched")
    base["packaging_status"] = base["last_packaged"].apply(lambda value: "Packaged" if pd.notna(value) else "Not Packaged")
    for col in ["stock_add_events", "return_restock_events"]:
        if col not in base.columns:
            base[col] = 0
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0).astype(int)
    for col in [
        "latest_stock_add_mode", "latest_stock_add_priority", "latest_stock_add_by",
        "stock_add_trail_status", "stock_add_followup",
    ]:
        if col not in base.columns:
            base[col] = ""
        base[col] = base[col].fillna("").astype(str)
    for col in ["first_stock_add", "last_stock_add"]:
        if col not in base.columns:
            base[col] = pd.NaT
        base[col] = pd.to_datetime(base[col], errors="coerce")
    if "latest_stock_add_qty" not in base.columns:
        base["latest_stock_add_qty"] = 0
    base["latest_stock_add_qty"] = pd.to_numeric(base["latest_stock_add_qty"], errors="coerce").fillna(0)
    no_stock_add = base["stock_add_events"].eq(0)
    base.loc[no_stock_add, "stock_add_trail_status"] = "Before data recording / no stock-add found"
    base.loc[no_stock_add, "stock_add_followup"] = "Likely before data recording - review pocket for expired meds"
    base["receiving_age_bucket"] = pd.cut(
        base["days_since_last_received"],
        bins=[-1, 30, 60, 90, 180, 99999],
        labels=["0-30", "31-60", "61-90", "91-180", "180+"],
    ).astype("object")
    base["receiving_age_bucket"] = base["receiving_age_bucket"].where(
        base["receiving_status"].eq("Matched"),
        "No Receiving Match",
    )
    priority_map = {
        "0-30": "Fresh",
        "31-60": "Normal",
        "61-90": "Watch",
        "91-180": "Review",
        "180+": "High Review",
        "No Receiving Match": "Review Mapping / Legacy Item",
    }
    base["receiving_review_priority"] = base["receiving_age_bucket"].map(priority_map).fillna("Review")

    return base.sort_values(
        ["receiving_age_bucket", "days_since_last_received", "isa_name", "med_desc"],
        ascending=[True, False, True, True],
    )


receiving = prep_receiving(load_receiving_history())
stock_add_history = prep_stock_add_history(load_stock_add_history())
deduction_history = prep_deduction_history(load_deduction_history())
qc_actions = load_inventory_qc_actions()
no_inventory_buyer_review = load_no_inventory_buyer_review()
isa_items = prep_isa_items(load_latest_isa_items())
inventory_counts = load_inventory_counts()
pyxis_inventory = prep_pyxis_inventory(load_current_pyxis_inventory())
packaging = prep_packaging(load_packaging_history())
device_inventory = prep_device_inventory(load_device_inventory())
device_inventory_snapshot_dates = load_device_inventory_snapshot_dates()
device_inventory_daily_delta = load_device_inventory_daily_delta()
clinical_pyxis_activity = load_clinical_pyxis_activity(start_date, end_date)
if not clinical_pyxis_activity.empty:
    clinical_pyxis_activity = clinical_pyxis_activity.copy()
    clinical_pyxis_activity["device"] = clinical_pyxis_activity["device"].fillna("").astype(str).str.strip()
    clinical_pyxis_activity["med_id"] = clinical_pyxis_activity["med_id"].fillna("").astype(str).str.strip().str.upper()
    clinical_pyxis_activity["transaction_type"] = clinical_pyxis_activity["transaction_type"].fillna("").astype(str).str.strip()
    clinical_pyxis_activity["qty"] = pd.to_numeric(clinical_pyxis_activity["qty"], errors="coerce").fillna(0)
    clinical_pyxis_activity["waste_amount"] = pd.to_numeric(clinical_pyxis_activity["waste_amount"], errors="coerce").fillna(0)
    clinical_summary = (
        clinical_pyxis_activity.groupby(["device", "med_id"], as_index=False)
        .agg(
            clinical_events=("transaction_type", "count"),
            clinical_vends=("transaction_type", lambda s: s.str.contains("vend", case=False, na=False).sum()),
            clinical_wastes=("transaction_type", lambda s: s.str.contains("waste", case=False, na=False).sum()),
            clinical_qty=("qty", "sum"),
            clinical_waste_qty=("waste_amount", "sum"),
            last_clinical_dt=("dt", "max"),
        )
    )
else:
    clinical_summary = pd.DataFrame(columns=[
        "device", "med_id", "clinical_events", "clinical_vends", "clinical_wastes",
        "clinical_qty", "clinical_waste_qty", "last_clinical_dt",
    ])
med_costs = prep_med_costs(load_med_costs())
inventory_audit = prep_inventory_audit(load_inventory_audit())
pyxis_savings_projects = prep_pyxis_savings_projects(load_pyxis_savings_projects())
receiving_summary = build_receiving_summary(receiving)
stock_add_summary = build_stock_add_summary(stock_add_history)
deduction_summary = build_deduction_summary(deduction_history)
pyxis_overstock_savings = build_pyxis_overstock_savings(device_inventory, deduction_history, med_costs)
inventory_turns_summary, inventory_turns_detail = build_inventory_turns(
    deduction_history,
    pyxis_inventory,
    inventory_audit,
    med_costs,
    start_date,
    end_date,
)
pyxis_exposure_summary = build_pyxis_exposure_summary(pyxis_inventory)
packaging_summary = build_packaging_summary(packaging)
manual_bud_summary = build_manual_bud_summary(qc_actions)
active_bud_review_summary = build_active_bud_review_summary(qc_actions)
isa_lifecycle = build_isa_lifecycle(
    isa_items,
    receiving_summary,
    inventory_counts,
    packaging_summary,
    stock_add_summary,
    deduction_summary,
    manual_bud_summary,
)
if not active_bud_review_summary.empty:
    isa_lifecycle = isa_lifecycle.merge(
        active_bud_review_summary,
        on=["isa_name", "location", "med_id"],
        how="left",
    )
for col in ["active_bud_review_status", "active_bud_review_by", "active_bud_review_note"]:
    if col not in isa_lifecycle.columns:
        isa_lifecycle[col] = ""
    isa_lifecycle[col] = isa_lifecycle[col].fillna("").astype(str)
for col in ["active_bud_review_dt", "reviewed_active_bud_date"]:
    if col not in isa_lifecycle.columns:
        isa_lifecycle[col] = pd.NaT
    isa_lifecycle[col] = pd.to_datetime(isa_lifecycle[col], errors="coerce")
isa_lifecycle["active_bud_reviewed"] = isa_lifecycle["active_bud_review_dt"].notna()

tab_lifecycle, tab_unload, tab_buyer_review, tab_turns, tab_savings = st.tabs([
    "ISA Receiving Lifecycle",
    "Pyxis 28-Day Unload",
    "Buyer No-Inventory Review",
    "Inventory Turns",
    "Pyxis Overstock Savings",
])

with tab_lifecycle:
    st.subheader("ISA Receiving Lifecycle")
    st.caption("This starts the med lifecycle clock from Pharmacy Workflow rows where event type is exactly `Receiving`.")

    if receiving.empty:
        st.warning("No `Receiving` rows are loaded in the Pharmacy Workflow table.")
    elif isa_lifecycle.empty:
        st.warning("No ISA item snapshot is loaded. Upload the Days Since Last Cycle Count Report to populate ISA items.")
    else:
        snapshot_date = isa_lifecycle["snapshot_date"].dropna().max()
        oldest_receipt = receiving["received_dt"].min()
        newest_receipt = receiving["received_dt"].max()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("ISA Items", f"{len(isa_lifecycle):,}")
        m2.metric("Items With Receiving Match", f"{int((isa_lifecycle['receiving_status'] == 'Matched').sum()):,}")
        m3.metric("Receiving Rows Loaded", f"{len(receiving):,}")
        m4.metric("ISA Snapshot", snapshot_date.strftime("%m/%d/%Y") if pd.notna(snapshot_date) else "Unknown")

        h1, h2, h3 = st.columns(3)
        h1.metric("Oldest Receiving Row", oldest_receipt.strftime("%m/%d/%Y") if pd.notna(oldest_receipt) else "Unknown")
        h2.metric("Newest Receiving Row", newest_receipt.strftime("%m/%d/%Y") if pd.notna(newest_receipt) else "Unknown")
        h3.metric("Packaged ISA Items", f"{int((isa_lifecycle['packaging_status'] == 'Packaged').sum()):,}")

        filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
        isa_options = sorted(isa_lifecycle["isa_name"].dropna().unique())
        selected_isas = filter_col1.multiselect("ISA", isa_options, default=isa_options[:1] if isa_options else [])
        status_options = ["Matched", "No Receiving Match"]
        selected_statuses = filter_col2.multiselect("Receiving status", status_options, default=status_options)
        bucket_order = ["0-30", "31-60", "61-90", "91-180", "180+", "No Receiving Match"]
        selected_buckets = filter_col3.multiselect("Receiving age bucket", bucket_order, default=bucket_order)
        med_search = filter_col4.text_input("Medication search")

        pack_filter = st.segmented_control(
            "Packaging filter",
            ["All", "Packaged only", "Not packaged only"],
            default="All",
        )

        view = isa_lifecycle.copy()
        if selected_isas:
            view = view[view["isa_name"].isin(selected_isas)]
        if selected_statuses:
            view = view[view["receiving_status"].isin(selected_statuses)]
        if selected_buckets:
            view = view[view["receiving_age_bucket"].isin(selected_buckets)]
        if pack_filter == "Packaged only":
            view = view[view["packaging_status"].eq("Packaged")]
        elif pack_filter == "Not packaged only":
            view = view[view["packaging_status"].eq("Not Packaged")]
        if med_search:
            med_mask = (
                view["med_id"].str.contains(med_search, case=False, na=False)
                | view["med_desc"].str.contains(med_search, case=False, na=False)
            )
            view = view[med_mask]
        reviewed_active_bud = view[view["active_bud_reviewed"]].copy()
        view = view[~view["active_bud_reviewed"]].copy()

        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Filtered ISA Items", f"{len(view):,}")
        v2.metric("No Receiving Match", f"{int((view['receiving_status'] == 'No Receiving Match').sum()):,}")
        v3.metric(
            "Median Days Since Received",
            f"{view['days_since_last_received'].dropna().median():.0f}" if view["days_since_last_received"].notna().any() else "N/A",
        )
        v4.metric("Current Count", f"{view['current_count'].sum():,.0f}")

        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            bucket_summary = (
                view.groupby("receiving_age_bucket", dropna=False)
                .size()
                .reindex(bucket_order, fill_value=0)
                .reset_index(name="item_count")
                .rename(columns={"index": "receiving_age_bucket"})
            )
            st.markdown("##### Items by Receiving Age Bucket")
            st.plotly_chart(px.bar(bucket_summary, x="receiving_age_bucket", y="item_count"), width="stretch")

        with chart_col2:
            top_old = (
                view[view["days_since_last_received"].notna()]
                .sort_values("days_since_last_received", ascending=False)
                .head(20)
                .copy()
            )
            if top_old.empty:
                st.info("No oldest received items to chart.")
            else:
                top_old["med_label"] = top_old["med_desc"].fillna(top_old["med_id"]).astype(str).str.slice(0, 55)
                st.markdown("##### Oldest Last-Received Items")
                st.plotly_chart(
                    px.bar(top_old.sort_values("days_since_last_received"), x="days_since_last_received", y="med_label", orientation="h"),
                    width="stretch",
                )

        packaged_review = view[
            view["packaging_status"].eq("Packaged")
            & view["days_until_packaged_expire"].notna()
            & (view["days_until_packaged_expire"] <= 90)
        ].copy()
        package_actions = pd.DataFrame()
        if not qc_actions.empty:
            package_actions = qc_actions[qc_actions["action_type"].eq("packaged_expiration")].copy()
            package_actions["replacement_expire_date"] = pd.to_datetime(
                package_actions["replacement_expire_date"], errors="coerce"
            )
        if not packaged_review.empty:
            if not pyxis_exposure_summary.empty:
                packaged_review = packaged_review.merge(pyxis_exposure_summary, on="med_id", how="left")
            for col in ["pyxis_machine_count", "pyxis_pocket_count", "pyxis_total_count"]:
                if col not in packaged_review.columns:
                    packaged_review[col] = 0
                packaged_review[col] = pd.to_numeric(packaged_review[col], errors="coerce").fillna(0)
            if "pyxis_machines_to_check" not in packaged_review.columns:
                packaged_review["pyxis_machines_to_check"] = ""
            packaged_review["pyxis_machines_to_check"] = packaged_review["pyxis_machines_to_check"].fillna("").astype(str)
            packaged_review["pyxis_check_status"] = packaged_review["pyxis_machine_count"].apply(
                lambda count: "Check Pyxis machines" if count > 0 else "No current Pyxis stock found"
            )
            packaged_review["action_key"] = (
                packaged_review["isa_name"].astype(str) + "|"
                + packaged_review["location"].astype(str) + "|"
                + packaged_review["med_id"].astype(str) + "|"
                + packaged_review["latest_hospital_lot_number"].fillna("").astype(str) + "|"
                + packaged_review["latest_packaged_expire_date"].astype(str)
            )
            if not package_actions.empty:
                packaged_review = packaged_review.merge(
                    package_actions[[
                        "action_key", "action_status", "replacement_expire_date", "action_by", "action_dt", "note"
                    ]].drop_duplicates("action_key", keep="first"),
                    on="action_key",
                    how="left",
                )
            else:
                packaged_review["action_status"] = ""
                packaged_review["replacement_expire_date"] = pd.NaT
                packaged_review["action_by"] = ""
                packaged_review["action_dt"] = pd.NaT
                packaged_review["note"] = ""
            packaged_review["replacement_expire_date"] = pd.to_datetime(
                packaged_review["replacement_expire_date"], errors="coerce"
            )
            packaged_review["effective_packaged_expire_date"] = packaged_review["replacement_expire_date"].fillna(
                packaged_review["latest_packaged_expire_date"]
            )
            packaged_review["effective_days_until_expire"] = (
                packaged_review["effective_packaged_expire_date"] - pd.Timestamp.today().normalize()
            ).dt.days
            packaged_review["qc_status"] = packaged_review["action_status"].fillna("").replace("", "Needs review")
        if not packaged_review.empty:
            st.markdown("##### Packaged Items Expiring Within 90 Days")
            hide_removed = st.toggle("Hide packaged items marked removed", value=True)
            review_display = packaged_review.copy()
            if hide_removed:
                review_display = review_display[~review_display["qc_status"].eq("Removed from carousel")]
            review_display = review_display[
                review_display["effective_days_until_expire"].notna()
                & review_display["effective_days_until_expire"].le(90)
            ]
            if review_display.empty:
                st.success("All packaged items in this filter have been removed or updated beyond the 90-day window.")
            else:
                exp_cols = [
                    "isa_name", "location", "med_id", "med_desc", "last_packaged",
                    "latest_packaged_expire_date", "effective_packaged_expire_date",
                    "effective_days_until_expire", "last_packaged_by",
                    "latest_hospital_lot_number", "pyxis_check_status", "pyxis_machine_count",
                    "pyxis_total_count", "pyxis_machines_to_check", "qc_status",
                ]
                packaged_event = st.dataframe(
                    review_display.sort_values("effective_days_until_expire")[exp_cols],
                    width="stretch",
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    column_config={
                        "latest_packaged_expire_date": st.column_config.DatetimeColumn("Original Packaged Expire", format="MM/DD/YYYY"),
                        "effective_packaged_expire_date": st.column_config.DatetimeColumn("Current Closest Expire", format="MM/DD/YYYY"),
                        "effective_days_until_expire": st.column_config.NumberColumn("Days Until Closest Expire", format="%.0f"),
                        "pyxis_machine_count": st.column_config.NumberColumn("Pyxis Machines", format="%d"),
                        "pyxis_total_count": st.column_config.NumberColumn("Pyxis Qty", format="%.0f"),
                    },
                )
                if packaged_event.selection.rows:
                    selected_pkg = review_display.sort_values("effective_days_until_expire").reset_index(drop=True).iloc[
                        packaged_event.selection.rows[0]
                    ]
                    selected_pyxis = pyxis_inventory[pyxis_inventory["med_id"].eq(str(selected_pkg["med_id"]).strip().upper())].copy()
                    if not selected_pyxis.empty:
                        st.caption("Current Pyxis locations to check for this expiring packaged med.")
                        st.dataframe(
                            selected_pyxis[[
                                "station", "pocket_location", "med_id", "med_desc", "current_count", "unit_cost", "inventory_value",
                            ]],
                            width="stretch",
                            hide_index=True,
                            column_config={
                                "station": "Pyxis Machine",
                                "pocket_location": "Pocket",
                                "current_count": st.column_config.NumberColumn("Current Count", format="%.0f"),
                                "unit_cost": st.column_config.NumberColumn("Unit Cost", format="$%.2f"),
                                "inventory_value": st.column_config.NumberColumn("Value", format="$%.2f"),
                            },
                        )
                    with st.form("packaged_expiration_action_form"):
                        st.caption("Use this after you check the carousel/Pyxis locations for the expiring packaged item.")
                        action_status = st.radio(
                            "Action taken",
                            ["Removed from carousel", "Old lot used - expiration updated"],
                            horizontal=True,
                        )
                        replacement_expire_date = st.date_input(
                            "Closest remaining expiration",
                            value=None,
                            help="Use this when the expiring lot has already been used and a later-dated package is now the closest expiration.",
                        )
                        action_by = st.text_input("Reviewed by", value="")
                        note = st.text_area("Note", value="Reviewed packaged expiration in carousel/Pyxis locations.")
                        submitted = st.form_submit_button("Save packaged expiration review")
                        if submitted:
                            save_inventory_qc_action({
                                "action_key": selected_pkg["action_key"],
                                "action_type": "packaged_expiration",
                                "med_id": selected_pkg["med_id"],
                                "med_desc": selected_pkg["med_desc"],
                                "isa_name": selected_pkg["isa_name"],
                                "location": selected_pkg["location"],
                                "action_status": action_status,
                                "action_by": action_by,
                                "note": note,
                                "replacement_expire_date": replacement_expire_date if action_status == "Old lot used - expiration updated" else None,
                            })
                            st.success("Saved packaged expiration review.")
                            st.rerun()

        st.markdown("##### Reviewed Active BUD Meds")
        if reviewed_active_bud.empty:
            st.info("No active BUD updates have been reviewed in the current filters.")
        else:
            reviewed_cols = [
                "isa_name",
                "location",
                "med_id",
                "med_desc",
                "active_bud_review_status",
                "reviewed_active_bud_date",
                "active_bud_review_dt",
                "active_bud_review_by",
                "active_bud_review_note",
            ]
            st.dataframe(
                reviewed_active_bud.sort_values("active_bud_review_dt", ascending=False)[reviewed_cols],
                width="stretch",
                hide_index=True,
                column_config={
                    "active_bud_review_status": "Review Status",
                    "reviewed_active_bud_date": st.column_config.DatetimeColumn("Active BUD", format="MM/DD/YYYY"),
                    "active_bud_review_dt": st.column_config.DatetimeColumn("Reviewed", format="MM/DD/YYYY HH:mm"),
                    "active_bud_review_by": "Reviewed By",
                    "active_bud_review_note": "Note",
                },
            )
            revert_options = reviewed_active_bud.sort_values("active_bud_review_dt", ascending=False).copy()
            revert_options["review_label"] = revert_options.apply(
                lambda row: (
                    f"{row.get('med_id', '')} | {row.get('med_desc', '')} | "
                    f"{row.get('isa_name', '')} {row.get('location', '')} | "
                    f"{pd.to_datetime(row.get('active_bud_review_dt'), errors='coerce').strftime('%m/%d/%Y %H:%M') if pd.notna(row.get('active_bud_review_dt')) else 'No review date'}"
                ),
                axis=1,
            )
            selected_revert_label = st.selectbox(
                "Review to revert",
                revert_options["review_label"].tolist(),
                key="active_bud_review_revert_choice",
            )
            selected_revert = revert_options[revert_options["review_label"].eq(selected_revert_label)].iloc[0]
            confirm_revert = st.checkbox(
                "I understand this will remove this Active BUD review and put the med back into the work queue.",
                key="active_bud_review_revert_confirm",
            )
            if st.button("Delete selected review", disabled=not confirm_revert, width="stretch"):
                deleted_rows = delete_inventory_qc_action(selected_revert["action_key"])
                if deleted_rows:
                    st.success("Deleted the selected Active BUD review. The med can now be reviewed again.")
                    st.rerun()
                else:
                    st.warning("That review was already gone. Refreshing the table.")
                    st.rerun()

        st.markdown("##### ISA Item Lifecycle Table")
        display_cols = [
            "isa_name",
            "location",
            "med_id",
            "med_desc",
            "current_count",
            "pocket_count",
            "last_cycle_count",
            "days_since_last_cycle_count",
            "last_deducted",
            "days_since_last_deducted",
            "last_deduction_qty",
            "last_deduction_priority",
            "last_deduction_destination",
            "last_deducted_by",
            "deduction_events",
            "total_deducted_qty",
            "last_received",
            "days_since_last_received",
            "receiving_age_bucket",
            "receiving_review_priority",
            "first_received",
            "days_since_first_received",
            "receiving_events",
            "total_received_qty",
            "last_received_by",
            "receiving_status",
            "stock_add_trail_status",
            "stock_add_followup",
            "last_stock_add",
            "latest_stock_add_mode",
            "latest_stock_add_priority",
            "latest_stock_add_by",
            "latest_stock_add_qty",
            "stock_add_events",
            "return_restock_events",
            "packaging_status",
            "last_packaged",
            "days_since_last_packaged",
            "latest_packaged_expire_date",
            "days_until_packaged_expire",
            "manual_bud_date",
            "active_bud_date",
            "active_bud_source",
            "packaging_events",
            "last_packaged_by",
            "latest_hospital_lot_number",
        ]
        selected_table = st.dataframe(
            view[display_cols],
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "last_received": st.column_config.DatetimeColumn("Last Received", format="MM/DD/YYYY HH:mm"),
                "first_received": st.column_config.DatetimeColumn("First Received", format="MM/DD/YYYY HH:mm"),
                "last_cycle_count": st.column_config.DatetimeColumn("Last Cycle Count", format="MM/DD/YYYY HH:mm"),
                "last_deducted": st.column_config.DatetimeColumn("Last Deducted", format="MM/DD/YYYY HH:mm"),
                "last_stock_add": st.column_config.DatetimeColumn("Last Stock Add", format="MM/DD/YYYY HH:mm"),
                "last_packaged": st.column_config.DatetimeColumn("Last Packaged", format="MM/DD/YYYY HH:mm"),
                "latest_packaged_expire_date": st.column_config.DatetimeColumn("Packaged Expire", format="MM/DD/YYYY"),
                "manual_bud_date": st.column_config.DatetimeColumn("Manual BUD", format="MM/DD/YYYY"),
                "active_bud_date": st.column_config.DatetimeColumn("Active BUD", format="MM/DD/YYYY"),
                "current_count": st.column_config.NumberColumn("Current Count", format="%.0f"),
                "pocket_count": st.column_config.NumberColumn("Carousel Pockets", format="%d"),
                "days_since_last_cycle_count": st.column_config.NumberColumn("Days Since Cycle Count", format="%.0f"),
                "days_since_last_deducted": st.column_config.NumberColumn("Days Since Deducted", format="%.0f"),
                "last_deduction_qty": st.column_config.NumberColumn("Last Deducted Qty", format="%.0f"),
                "deduction_events": st.column_config.NumberColumn("Deduction Events", format="%d"),
                "total_deducted_qty": st.column_config.NumberColumn("Total Deducted Qty", format="%.0f"),
                "days_since_last_received": st.column_config.NumberColumn("Days Since Received", format="%.0f"),
                "days_until_packaged_expire": st.column_config.NumberColumn("Days Until Packaged Expire", format="%.0f"),
                "latest_stock_add_qty": st.column_config.NumberColumn("Latest Stock Add Qty", format="%.0f"),
            },
        )

        if selected_table.selection.rows:
            selected_row = view[display_cols].reset_index(drop=True).iloc[selected_table.selection.rows[0]]
            selected_med_id = str(selected_row["med_id"]).strip().upper()
            selected_med_desc = selected_row["med_desc"]
            pyxis_matches = pyxis_inventory[pyxis_inventory["med_id"].eq(selected_med_id)].copy()

            st.divider()
            st.subheader("Selected Med Current Pyxis Stocking")
            st.caption(
                "These are current non-carousel Pyxis locations for the selected ISA item. "
                "`CAR` stations are excluded because they are carousel/pharmacy inventory."
            )

            detail_1, detail_2, detail_3, detail_4 = st.columns(4)
            detail_1.metric("Selected Med", selected_med_id)
            detail_2.metric("Receiving Bucket", selected_row.get("receiving_age_bucket", ""))
            detail_3.metric(
                "Days Since Received",
                "N/A" if pd.isna(selected_row.get("days_since_last_received")) else f"{selected_row.get('days_since_last_received'):.0f}",
            )
            detail_4.metric("Pyxis Machines Stocking", f"{pyxis_matches['station'].nunique() if not pyxis_matches.empty else 0:,}")

            st.markdown(f"**{selected_med_desc}**")

            current_packaged_expire = pd.to_datetime(
                selected_row.get("latest_packaged_expire_date"),
                errors="coerce",
            )
            current_manual_bud = pd.to_datetime(
                selected_row.get("manual_bud_date"),
                errors="coerce",
            )
            current_active_bud = current_packaged_expire if pd.notna(current_packaged_expire) else current_manual_bud
            st.markdown("##### Update Active BUD After Removal")
            if pd.isna(current_active_bud):
                st.info("This selected row does not have an active BUD yet. Add the current expiration here after you confirm the product date.")
                with st.form(f"selected_med_bud_create_{selected_med_id}"):
                    created_bud_date = st.date_input(
                        "Active BUD",
                        value=pd.Timestamp.today().date(),
                        min_value=pd.Timestamp.today().date(),
                    )
                    created_by = st.text_input("Reviewed by", value="")
                    created_note = st.text_area(
                        "Note",
                        value="Created active BUD record after checking current Pyxis/carousel product.",
                    )
                    create_bud = st.form_submit_button("Create active BUD record")
                    if create_bud:
                        action_key = (
                            f"selected-row-bud-create|{selected_row.get('isa_name', '')}|"
                            f"{selected_row.get('location', '')}|{selected_med_id}|{created_bud_date}"
                        )
                        save_inventory_qc_action({
                            "action_key": action_key,
                            "action_type": "active_bud",
                            "med_id": selected_med_id,
                            "med_desc": selected_med_desc,
                            "isa_name": selected_row.get("isa_name", ""),
                            "location": selected_row.get("location", ""),
                            "action_status": "Manual BUD record created",
                            "action_by": created_by,
                            "note": created_note,
                            "replacement_expire_date": created_bud_date,
                        })
                        st.success("Saved active BUD record.")
                        st.rerun()
            else:
                st.caption("Use this after the old product has been removed and the next remaining date should become the active BUD.")
                with st.form(f"selected_med_bud_update_{selected_med_id}"):
                    b1, b2 = st.columns(2)
                    b1.date_input(
                        "Current active BUD",
                        value=current_active_bud.date(),
                        disabled=True,
                    )
                    new_bud_date = b2.date_input(
                        "New active BUD",
                        value=current_active_bud.date(),
                        min_value=pd.Timestamp.today().date(),
                    )
                    reviewed_by = st.text_input("Reviewed by", value="")
                    bud_note = st.text_area(
                        "Note",
                        value="Removed old packaged product and updated active BUD to the next remaining date.",
                    )
                    update_bud = st.form_submit_button("Save BUD update")
                    if update_bud:
                        updated_rows = 0
                        if pd.notna(current_packaged_expire):
                            updated_rows = update_packaged_bud_after_removal(
                                selected_med_id,
                                current_packaged_expire.date(),
                                new_bud_date,
                            )
                        action_key = (
                            f"selected-row-bud|{selected_row.get('isa_name', '')}|"
                            f"{selected_row.get('location', '')}|{selected_med_id}|"
                            f"{current_active_bud.date()}"
                        )
                        save_inventory_qc_action({
                            "action_key": action_key,
                            "action_type": "active_bud",
                            "med_id": selected_med_id,
                            "med_desc": selected_med_desc,
                            "isa_name": selected_row.get("isa_name", ""),
                            "location": selected_row.get("location", ""),
                            "action_status": "Old product removed - BUD updated",
                            "action_by": reviewed_by,
                            "note": bud_note,
                            "replacement_expire_date": new_bud_date,
                        })
                        if pd.isna(current_packaged_expire):
                            st.success("Updated manual active BUD record.")
                        elif updated_rows:
                            st.success(f"Updated BUD on {updated_rows} packaged row(s).")
                        else:
                            st.warning("Saved the review, but no packaged rows matched that med/current BUD.")
                        st.rerun()

            if pyxis_matches.empty:
                st.info("This selected med is not currently stocked in any non-carousel Pyxis machine in the latest detailed inventory upload.")
            else:
                p1, p2, p3 = st.columns(3)
                p1.metric("Total Pyxis Count", f"{pyxis_matches['current_count'].sum():,.0f}")
                p2.metric("Pyxis Pockets", f"{len(pyxis_matches):,}")
                p3.metric("Estimated Value", f"${pyxis_matches['inventory_value'].sum():,.2f}")

                pyxis_cols = [
                    "station",
                    "pocket_location",
                    "med_id",
                    "med_desc",
                    "current_count",
                    "unit_cost",
                    "inventory_value",
                ]
                st.dataframe(
                    pyxis_matches[pyxis_cols],
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "station": "Pyxis Machine",
                        "pocket_location": "Pocket",
                        "current_count": st.column_config.NumberColumn("Current Count", format="%.0f"),
                        "unit_cost": st.column_config.NumberColumn("Unit Cost", format="$%.2f"),
                        "inventory_value": st.column_config.NumberColumn("Value", format="$%.2f"),
                    },
                )

        st.download_button(
            "Download ISA receiving lifecycle CSV",
            data=view[display_cols].to_csv(index=False).encode("utf-8"),
            file_name="isa_receiving_lifecycle.csv",
            mime="text/csv",
        )

        with st.expander("Raw receiving rows"):
            raw_cols = ["received_dt", "queue_id", "med_id", "med_desc", "user_name", "qty"]
            st.dataframe(receiving[raw_cols], width="stretch", hide_index=True)

        with st.expander("Raw stock-add rows"):
            raw_stock_cols = [
                "stock_add_dt", "stock_add_mode", "priority", "queue_id",
                "med_id", "med_desc", "destination", "user_name", "qty",
            ]
            st.dataframe(stock_add_history[raw_stock_cols], width="stretch", hide_index=True)

        with st.expander("Raw deduction rows"):
            raw_deduction_cols = [
                "deducted_dt", "priority", "queue_id", "med_id", "med_desc",
                "destination", "user_name", "qty",
            ]
            st.dataframe(deduction_history[raw_deduction_cols], width="stretch", hide_index=True)

        with st.expander("Raw packaging rows"):
            if packaging.empty:
                st.info("No packaging report rows are loaded yet.")
            else:
                package_cols = [
                    "dispense_dt", "med_id", "med_desc", "qty_per_pack", "qoh",
                    "manufacturer", "hospital_lot_number", "packaged_expire_date", "packaged_by", "confirmer",
                ]
                st.dataframe(packaging[package_cols], width="stretch", hide_index=True)

with tab_unload:
    st.subheader("Pyxis 28-Day Unload Compliance")
    st.caption(
        "Uses Device Inventory List rows. Meds with `DaysUnused > 28` should be unloaded unless "
        "`StandardStock` or `ActiveOrders` is `Y`."
    )

    if device_inventory.empty:
        st.warning("No Device Inventory List rows are loaded yet. Upload the device inventory CSV from the main upload page.")
    else:
        device_view = device_inventory.copy()
        if not clinical_summary.empty:
            device_view = device_view.merge(clinical_summary, on=["device", "med_id"], how="left")
        for col in ["clinical_events", "clinical_vends", "clinical_wastes", "clinical_qty", "clinical_waste_qty"]:
            if col not in device_view.columns:
                device_view[col] = 0
            device_view[col] = pd.to_numeric(device_view[col], errors="coerce").fillna(0)
        if "last_clinical_dt" not in device_view.columns:
            device_view["last_clinical_dt"] = pd.NaT
        device_view["last_clinical_dt"] = pd.to_datetime(device_view["last_clinical_dt"], errors="coerce")
        clinically_active = device_view["clinical_events"].gt(0)
        device_view.loc[
            device_view["unload_status"].isin(["High Priority", "Unload Due"]) & clinically_active,
            "unload_status",
        ] = "Clinical Use Review"
        device_snapshot = device_view["snapshot_dt"].dropna().max()

        due_mask = device_view["unload_status"].isin(["High Priority", "Unload Due"])
        exempt_mask = device_view["unload_status"].eq("Exempt")

        d1, d2, d3, d4, d5, d6 = st.columns(6)
        d1.metric("Inventory Rows", f"{len(device_view):,}")
        d2.metric("Unload Due", f"{int(due_mask.sum()):,}")
        d3.metric("High Priority", f"{int(device_view['unload_status'].eq('High Priority').sum()):,}")
        d4.metric("Exempt Over 28 Days", f"{int(exempt_mask.sum()):,}")
        d5.metric("Clinical Use Review", f"{int(device_view['unload_status'].eq('Clinical Use Review').sum()):,}")
        d6.metric("Snapshot", device_snapshot.strftime("%m/%d/%Y %H:%M") if pd.notna(device_snapshot) else "Unknown")

        f1, f2, f3, f4 = st.columns(4)
        device_options = sorted(device_view["device"].dropna().unique())
        selected_devices = f1.multiselect("Device", device_options)
        status_order = ["Clinical Use Review", "High Priority", "Unload Due", "Exempt", "Compliant"]
        selected_unload_statuses = f2.multiselect(
            "Unload status",
            status_order,
            default=["High Priority", "Unload Due"],
        )
        min_days = int(device_view["days_unused"].max()) if device_view["days_unused"].notna().any() else 0
        days_floor = f3.number_input("Minimum days unused", min_value=0, max_value=max(min_days, 29), value=29, step=1)
        device_med_search = f4.text_input("Device med search")

        suggested_procedural = suggest_procedural_devices(device_options)
        exclude_procedural = st.toggle(
            "Exclude procedural/anesthesia machines from this review",
            value=True,
            help="Use this to hide OR, PACU, endoscopy, cath lab, IR, anesthesia, and other fixed procedural machines that are not part of the routine unload workflow.",
        )
        excluded_devices = st.multiselect(
            "Procedural/anesthesia devices to exclude",
            device_options,
            default=suggested_procedural,
            disabled=not exclude_procedural,
            help="Edit this list if a device is incorrectly included or excluded.",
        )

        if selected_devices:
            device_view = device_view[device_view["device"].isin(selected_devices)]
        if exclude_procedural and excluded_devices:
            device_view = device_view[~device_view["device"].isin(excluded_devices)]
        if selected_unload_statuses:
            device_view = device_view[device_view["unload_status"].isin(selected_unload_statuses)]
        device_view = device_view[device_view["days_unused"] >= days_floor]
        if device_med_search:
            search_mask = (
                device_view["med_id"].str.contains(device_med_search, case=False, na=False)
                | device_view["med_desc"].str.contains(device_med_search, case=False, na=False)
                | device_view["brand_name"].str.contains(device_med_search, case=False, na=False)
                | device_view["device"].str.contains(device_med_search, case=False, na=False)
            )
            device_view = device_view[search_mask]

        dv1, dv2, dv3, dv4 = st.columns(4)
        filtered_due = device_view["unload_status"].isin(["High Priority", "Unload Due"])
        dv1.metric("Filtered Rows", f"{len(device_view):,}")
        dv2.metric("Filtered Unload Due", f"{int(filtered_due.sum()):,}")
        dv3.metric("Filtered Quantity Due", f"{device_view.loc[filtered_due, 'current_quantity'].sum():,.0f}")
        dv4.metric(
            "Oldest Days Unused",
            f"{device_view['days_unused'].max():.0f}" if not device_view.empty else "N/A",
        )
        st.caption(
            f"Clinical vend/waste activity in this analysis window: "
            f"{int(device_view['clinical_events'].gt(0).sum()):,} visible pocket(s)."
        )

        unload_chart_1, unload_chart_2 = st.columns(2)
        with unload_chart_1:
            status_summary = (
                device_view.groupby("unload_status", dropna=False)
                .size()
                .reindex(status_order, fill_value=0)
                .reset_index(name="row_count")
                .rename(columns={"index": "unload_status"})
            )
            st.markdown("##### Rows by Unload Status")
            st.plotly_chart(px.bar(status_summary, x="unload_status", y="row_count"), width="stretch")

        with unload_chart_2:
            due_by_device = (
                device_view[device_view["unload_status"].isin(["High Priority", "Unload Due"])]
                .groupby("device", dropna=False)
                .size()
                .sort_values(ascending=False)
                .head(20)
                .reset_index(name="due_rows")
            )
            st.markdown("##### Top Devices With Unload Work")
            if due_by_device.empty:
                st.info("No unload-due rows match the current filters.")
            else:
                st.plotly_chart(px.bar(due_by_device.sort_values("due_rows"), x="due_rows", y="device", orientation="h"), width="stretch")

        st.markdown("##### Daily Device Quantity Movement")
        st.caption(
            "Compares the latest Device Inventory upload with the previous Device Inventory upload. "
            "Negative net change is the quantity no longer in that Pyxis pocket/device."
        )
        if device_inventory_snapshot_dates.empty or len(device_inventory_snapshot_dates) < 2:
            st.info("Upload Device Inventory on at least two different days to unlock day-to-day quantity movement.")
        elif device_inventory_daily_delta.empty:
            st.info("No device quantity movement was found between the latest two Device Inventory snapshots.")
        else:
            delta_view = device_inventory_daily_delta.copy()
            for col in [
                "previous_quantity", "current_quantity", "net_quantity_change",
                "removed_quantity", "added_quantity", "days_unused",
            ]:
                delta_view[col] = pd.to_numeric(delta_view[col], errors="coerce").fillna(0)
            for col in ["device", "zone", "pocket_location", "med_id", "med_desc", "brand_name", "movement_type"]:
                delta_view[col] = delta_view[col].fillna("").astype(str)

            if selected_devices:
                delta_view = delta_view[delta_view["device"].isin(selected_devices)]
            if exclude_procedural and excluded_devices:
                delta_view = delta_view[~delta_view["device"].isin(excluded_devices)]
            if device_med_search:
                delta_search_mask = (
                    delta_view["med_id"].str.contains(device_med_search, case=False, na=False)
                    | delta_view["med_desc"].str.contains(device_med_search, case=False, na=False)
                    | delta_view["brand_name"].str.contains(device_med_search, case=False, na=False)
                    | delta_view["device"].str.contains(device_med_search, case=False, na=False)
                )
                delta_view = delta_view[delta_search_mask]

            movement_only = st.toggle("Only show rows with quantity changes", value=True)
            removals_only = st.toggle("Only show net removals", value=True)
            if movement_only:
                delta_view = delta_view[delta_view["net_quantity_change"].ne(0)]
            if removals_only:
                delta_view = delta_view[delta_view["removed_quantity"].gt(0)]

            latest_delta_date = pd.to_datetime(device_inventory_daily_delta["latest_snapshot_date"].dropna().max(), errors="coerce")
            previous_delta_date = pd.to_datetime(device_inventory_daily_delta["previous_snapshot_date"].dropna().max(), errors="coerce")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Latest Snapshot", latest_delta_date.strftime("%m/%d/%Y") if pd.notna(latest_delta_date) else "Unknown")
            m2.metric("Compared To", previous_delta_date.strftime("%m/%d/%Y") if pd.notna(previous_delta_date) else "Unknown")
            m3.metric("Quantity Removed", f"{delta_view['removed_quantity'].sum():,.0f}")
            m4.metric("Changed Pockets", f"{int(delta_view['net_quantity_change'].ne(0).sum()):,}")

            removed_by_device = (
                delta_view[delta_view["removed_quantity"].gt(0)]
                .groupby("device", dropna=False)["removed_quantity"]
                .sum()
                .sort_values(ascending=False)
                .head(20)
                .reset_index()
            )
            if not removed_by_device.empty:
                st.plotly_chart(
                    px.bar(
                        removed_by_device.sort_values("removed_quantity"),
                        x="removed_quantity",
                        y="device",
                        orientation="h",
                    ),
                    width="stretch",
                )

            movement_cols = [
                "movement_type",
                "device",
                "zone",
                "pocket_location",
                "med_id",
                "med_desc",
                "brand_name",
                "previous_quantity",
                "current_quantity",
                "net_quantity_change",
                "removed_quantity",
                "added_quantity",
                "days_unused",
                "standard_stock",
                "active_orders",
            ]
            st.dataframe(
                delta_view.sort_values(["removed_quantity", "added_quantity", "device", "med_desc"], ascending=[False, False, True, True])[movement_cols],
                width="stretch",
                hide_index=True,
                column_config={
                    "movement_type": "Movement",
                    "pocket_location": "Pocket",
                    "previous_quantity": st.column_config.NumberColumn("Previous Qty", format="%.0f"),
                    "current_quantity": st.column_config.NumberColumn("Current Qty", format="%.0f"),
                    "net_quantity_change": st.column_config.NumberColumn("Net Change", format="%.0f"),
                    "removed_quantity": st.column_config.NumberColumn("Removed Qty", format="%.0f"),
                    "added_quantity": st.column_config.NumberColumn("Added Qty", format="%.0f"),
                    "days_unused": st.column_config.NumberColumn("Days Unused", format="%.0f"),
                },
            )
            st.download_button(
                "Download daily device movement CSV",
                data=delta_view[movement_cols].to_csv(index=False).encode("utf-8"),
                file_name="daily_device_inventory_movement.csv",
                mime="text/csv",
            )

        st.markdown("##### Device Inventory 28-Day Review")
        standard_counts = device_view["standard_stock"].fillna("").astype(str).str.upper().value_counts()
        outdate_counts = device_view["outdate_tracking"].fillna("").astype(str).str.upper().value_counts()
        if len(device_view) > 0 and standard_counts.get("Y", 0) == 0:
            st.warning(
                "Standard Stock is `N` for every row in the current Device Inventory view. "
                "That usually means the StandardStock source column did not map correctly. "
                "Re-upload the Device Inventory List after this update so RxTrack uses column V for the flag."
            )
        if len(device_view) > 0 and outdate_counts.get("Y", 0) == 0:
            st.warning(
                "Outdate Tracking is not marked `Y` for any row in this Device Inventory view. "
                "Verify the source export has the OutdateTracking column."
            )
        device_cols = [
            "device",
            "zone",
            "pocket_location",
            "med_id",
            "med_desc",
            "brand_name",
            "current_quantity",
            "min_qty",
            "max_qty",
            "days_unused",
            "standard_stock",
            "active_orders",
            "unload_status",
            "exemption_reason",
            "status",
            "outdate_tracking",
            "standard_stock_check",
            "outdate_tracking_check",
            "clinical_events",
            "clinical_vends",
            "clinical_wastes",
            "clinical_qty",
            "last_clinical_dt",
            "backordered",
            "snapshot_dt",
        ]
        st.dataframe(
            device_view.sort_values(["unload_status", "days_unused"], ascending=[True, False])[device_cols],
            width="stretch",
            hide_index=True,
            column_config={
                "device": "Device",
                "pocket_location": "Pocket",
                "current_quantity": st.column_config.NumberColumn("Current Qty", format="%.0f"),
                "min_qty": st.column_config.NumberColumn("Min", format="%.0f"),
                "max_qty": st.column_config.NumberColumn("Max", format="%.0f"),
                "days_unused": st.column_config.NumberColumn("Days Unused", format="%.0f"),
                "clinical_events": st.column_config.NumberColumn("Clinical Events", format="%d"),
                "clinical_vends": st.column_config.NumberColumn("Clinical Vends", format="%d"),
                "clinical_wastes": st.column_config.NumberColumn("Clinical Wastes", format="%d"),
                "clinical_qty": st.column_config.NumberColumn("Clinical Qty", format="%.0f"),
                "last_clinical_dt": st.column_config.DatetimeColumn("Last Clinical", format="MM/DD/YYYY HH:mm"),
                "snapshot_dt": st.column_config.DatetimeColumn("Loaded Snapshot", format="MM/DD/YYYY HH:mm"),
            },
        )

        st.download_button(
            "Download 28-day unload review CSV",
            data=device_view[device_cols].to_csv(index=False).encode("utf-8"),
            file_name="pyxis_28_day_unload_review.csv",
            mime="text/csv",
        )


with tab_buyer_review:
    st.subheader("Buyer No-Inventory Pocket Review")
    st.caption(
        "Compiles Mobile BUD Scanner transactions marked `No inventory on hand` so purchasing can review pockets "
        "that may be configured with min/max values that will not replenish."
    )

    if no_inventory_buyer_review.empty:
        st.info("No no-inventory-on-hand transactions have been logged yet from Mobile BUD Scanner.")
    else:
        buyer_review = no_inventory_buyer_review.copy()
        for col in ["checked_dt", "follow_up_dt", "snapshot_dt"]:
            if col in buyer_review.columns:
                buyer_review[col] = pd.to_datetime(buyer_review[col], errors="coerce")
        for col in ["current_quantity", "min_qty", "max_qty", "days_unused"]:
            if col in buyer_review.columns:
                buyer_review[col] = pd.to_numeric(buyer_review[col], errors="coerce")

        review_reasons = sorted(buyer_review["buyer_review_reason"].dropna().astype(str).unique())
        selected_reasons = st.multiselect("Review reason", review_reasons, default=review_reasons)
        buyer_search = st.text_input("Buyer review search")

        buyer_view = buyer_review.copy()
        if selected_reasons:
            buyer_view = buyer_view[buyer_view["buyer_review_reason"].isin(selected_reasons)]
        if buyer_search:
            search_mask = (
                buyer_view["med_id"].fillna("").astype(str).str.contains(buyer_search, case=False, na=False)
                | buyer_view["med_desc"].fillna("").astype(str).str.contains(buyer_search, case=False, na=False)
                | buyer_view["isa_name"].fillna("").astype(str).str.contains(buyer_search, case=False, na=False)
                | buyer_view["location"].fillna("").astype(str).str.contains(buyer_search, case=False, na=False)
            )
            buyer_view = buyer_view[search_mask]

        zero_min_count = int(pd.to_numeric(buyer_view["min_qty"], errors="coerce").fillna(0).eq(0).sum())
        missing_config_count = int(
            buyer_view["buyer_review_reason"]
            .fillna("")
            .astype(str)
            .str.contains("No matching pocket configuration", case=False, na=False)
            .sum()
        )
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Review Rows", f"{len(buyer_view):,}")
        b2.metric("Min Zero / Missing", f"{zero_min_count:,}")
        b3.metric("No Config Match", f"{missing_config_count:,}")
        b4.metric("Unique Meds", f"{buyer_view['med_id'].nunique():,}")

        buyer_cols = [
            "buyer_review_reason",
            "med_id",
            "med_desc",
            "isa_name",
            "location",
            "configured_device",
            "configured_pocket",
            "current_quantity",
            "min_qty",
            "max_qty",
            "standard_stock",
            "active_orders",
            "backordered",
            "days_unused",
            "checked_by",
            "checked_dt",
            "note",
        ]
        buyer_cols = [col for col in buyer_cols if col in buyer_view.columns]
        st.dataframe(
            buyer_view[buyer_cols],
            width="stretch",
            hide_index=True,
            column_config={
                "buyer_review_reason": "Buyer Review Reason",
                "current_quantity": st.column_config.NumberColumn("Current Qty", format="%.0f"),
                "min_qty": st.column_config.NumberColumn("Min", format="%.0f"),
                "max_qty": st.column_config.NumberColumn("Max", format="%.0f"),
                "days_unused": st.column_config.NumberColumn("Days Unused", format="%.0f"),
                "checked_dt": st.column_config.DatetimeColumn("Checked", format="MM/DD/YYYY HH:mm"),
            },
        )
        st.download_button(
            "Download buyer no-inventory review CSV",
            data=buyer_view[buyer_cols].to_csv(index=False).encode("utf-8"),
            file_name="buyer_no_inventory_pocket_review.csv",
            mime="text/csv",
        )


with tab_turns:
    st.subheader("Pharmacy Inventory Turns")
    st.caption(
        "Turns are calculated as annualized issue value divided by current on-hand inventory value. "
        "Issue value uses Pharmacy Workflow pull/dispense/deduct rows in the selected analysis window."
    )

    if inventory_turns_summary.empty:
        st.warning("Inventory turns need Pharmacy Workflow deduction rows plus either Detailed Inventory or Inventory Audit rows.")
    else:
        period_days = max((pd.to_datetime(end_date) - pd.to_datetime(start_date)).days + 1, 1)
        st.caption(f"Analysis window: {start_date:%m/%d/%Y} through {end_date:%m/%d/%Y} ({period_days} day{'s' if period_days != 1 else ''})")

        primary_turns = inventory_turns_summary.sort_values("inventory_value", ascending=False).iloc[0]
        t1, t2, t3, t4 = st.columns(4)
        t1.metric("Primary Turns", f"{primary_turns['inventory_turns']:.2f}x" if pd.notna(primary_turns["inventory_turns"]) else "N/A")
        t2.metric("Inventory Value", f"${primary_turns['inventory_value']:,.0f}")
        t3.metric("Annualized Issue Value", f"${primary_turns['annualized_issue_value']:,.0f}")
        t4.metric("Issue Value In Window", f"${primary_turns['issue_value']:,.0f}")

        st.markdown("##### Turns by Inventory Source")
        st.dataframe(
            inventory_turns_summary,
            width="stretch",
            hide_index=True,
            column_config={
                "inventory_source": "Inventory Source",
                "on_hand_qty": st.column_config.NumberColumn("On Hand Qty", format="%.0f"),
                "inventory_value": st.column_config.NumberColumn("Inventory Value", format="$%.2f"),
                "issue_qty": st.column_config.NumberColumn("Issue Qty", format="%.0f"),
                "issue_value": st.column_config.NumberColumn("Issue Value", format="$%.2f"),
                "annualized_issue_value": st.column_config.NumberColumn("Annualized Issue Value", format="$%.2f"),
                "inventory_turns": st.column_config.NumberColumn("Turns", format="%.2f"),
            },
        )

        source_options = sorted(inventory_turns_detail["inventory_source"].dropna().unique())
        selected_turn_sources = st.multiselect("Inventory source", source_options, default=source_options)
        turns_search = st.text_input("Turns med search")
        turns_view = inventory_turns_detail.copy()
        if selected_turn_sources:
            turns_view = turns_view[turns_view["inventory_source"].isin(selected_turn_sources)]
        if turns_search:
            turns_view = turns_view[
                turns_view["med_id"].fillna("").astype(str).str.contains(turns_search, case=False, na=False)
                | turns_view["med_desc"].fillna("").astype(str).str.contains(turns_search, case=False, na=False)
            ]

        st.markdown("##### Medication-Level Turns")
        st.dataframe(
            turns_view,
            width="stretch",
            hide_index=True,
            column_config={
                "inventory_source": "Inventory Source",
                "on_hand_qty": st.column_config.NumberColumn("On Hand Qty", format="%.0f"),
                "inventory_value": st.column_config.NumberColumn("Inventory Value", format="$%.2f"),
                "issue_qty": st.column_config.NumberColumn("Issue Qty", format="%.0f"),
                "issue_value": st.column_config.NumberColumn("Issue Value", format="$%.2f"),
                "annualized_issue_value": st.column_config.NumberColumn("Annualized Issue Value", format="$%.2f"),
                "inventory_turns": st.column_config.NumberColumn("Turns", format="%.2f"),
                "cost_per_unit": st.column_config.NumberColumn("Unit Cost", format="$%.2f"),
            },
        )
        st.download_button(
            "Download inventory turns CSV",
            data=turns_view.to_csv(index=False).encode("utf-8"),
            file_name="pharmacy_inventory_turns.csv",
            mime="text/csv",
        )


with tab_savings:
    st.subheader("Pyxis Overstock Savings")
    st.caption(
        "Flags Pyxis pockets where current quantity appears higher than recent pull activity supports. "
        "Use this as a par-level review queue before changing min/max settings."
    )

    if pyxis_overstock_savings.empty:
        st.warning("No Device Inventory rows are loaded yet. Upload the Device Inventory List and med cost file to estimate savings.")
    else:
        savings_view = pyxis_overstock_savings.copy()
        savings_devices = sorted(savings_view["device"].dropna().unique())
        savings_priorities = [
            "No-Movement Removal Review",
            "High Dollar Review",
            "Review",
            "Exempt / Verify Need",
            "Monitor",
        ]

        s_filter_1, s_filter_2, s_filter_3, s_filter_4 = st.columns(4)
        selected_savings_devices = s_filter_1.multiselect("Device", savings_devices, key="savings_device_filter")
        selected_savings_priorities = s_filter_2.multiselect(
            "Savings priority",
            savings_priorities,
            default=["No-Movement Removal Review", "High Dollar Review", "Review"],
            key="savings_priority_filter",
        )
        min_excess_value = s_filter_3.number_input(
            "Minimum excess value",
            min_value=0,
            value=10,
            step=5,
            key="savings_min_excess_value",
        )
        savings_search = s_filter_4.text_input("Med/device search", key="savings_search")

        suggested_procedural = suggest_procedural_devices(savings_devices)
        exclude_savings_procedural = st.toggle(
            "Exclude procedural/anesthesia machines",
            value=True,
            key="savings_exclude_procedural",
        )
        if selected_savings_devices:
            savings_view = savings_view[savings_view["device"].isin(selected_savings_devices)]
        if exclude_savings_procedural and suggested_procedural:
            savings_view = savings_view[~savings_view["device"].isin(suggested_procedural)]
        if selected_savings_priorities:
            savings_view = savings_view[savings_view["savings_priority"].isin(selected_savings_priorities)]
        savings_view = savings_view[savings_view["estimated_excess_value"].ge(min_excess_value)]
        if savings_search:
            savings_mask = (
                savings_view["med_id"].str.contains(savings_search, case=False, na=False)
                | savings_view["med_desc"].str.contains(savings_search, case=False, na=False)
                | savings_view["brand_name"].str.contains(savings_search, case=False, na=False)
                | savings_view["device"].str.contains(savings_search, case=False, na=False)
            )
            savings_view = savings_view[savings_mask]

        sv1, sv2, sv3, sv4 = st.columns(4)
        sv1.metric("Review Rows", f"{len(savings_view):,}")
        sv2.metric("Estimated Excess Value", f"${savings_view['estimated_excess_value'].sum():,.0f}")
        sv3.metric("Excess Quantity", f"{savings_view['excess_quantity'].sum():,.0f}")
        sv4.metric(
            "No 90-Day Pull Rows",
            f"{int(savings_view['deduct_qty_90d'].eq(0).sum()):,}",
        )

        chart_left, chart_right = st.columns(2)
        with chart_left:
            priority_summary = (
                savings_view.groupby("savings_priority", dropna=False)["estimated_excess_value"]
                .sum()
                .reindex(savings_priorities, fill_value=0)
                .reset_index()
                .rename(columns={"index": "savings_priority"})
            )
            st.markdown("##### Excess Value by Priority")
            st.plotly_chart(px.bar(priority_summary, x="savings_priority", y="estimated_excess_value"), width="stretch")

        with chart_right:
            device_savings = (
                savings_view.groupby("device", dropna=False)["estimated_excess_value"]
                .sum()
                .sort_values(ascending=False)
                .head(20)
                .reset_index()
            )
            st.markdown("##### Top Devices by Excess Value")
            if device_savings.empty:
                st.info("No rows match the current savings filters.")
            else:
                st.plotly_chart(
                    px.bar(device_savings.sort_values("estimated_excess_value"), x="estimated_excess_value", y="device", orientation="h"),
                    width="stretch",
                )

        savings_cols = [
            "savings_priority",
            "suggested_action",
            "device",
            "zone",
            "pocket_location",
            "med_id",
            "med_desc",
            "brand_name",
            "current_quantity",
            "min_qty",
            "max_qty",
            "suggested_max",
            "excess_quantity",
            "cost_per_unit",
            "estimated_excess_value",
            "days_unused",
            "deduct_qty_30d",
            "deduct_qty_90d",
            "last_deducted",
            "standard_stock",
            "active_orders",
            "status",
        ]
        st.markdown("##### Overstock Review Queue")
        st.dataframe(
            savings_view.sort_values(["estimated_excess_value", "excess_quantity", "days_unused"], ascending=[False, False, False])[savings_cols],
            width="stretch",
            hide_index=True,
            column_config={
                "savings_priority": "Priority",
                "suggested_action": "Suggested Action",
                "pocket_location": "Pocket",
                "current_quantity": st.column_config.NumberColumn("Current Qty", format="%.0f"),
                "min_qty": st.column_config.NumberColumn("Min", format="%.0f"),
                "max_qty": st.column_config.NumberColumn("Max", format="%.0f"),
                "suggested_max": st.column_config.NumberColumn("Suggested Max", format="%.0f"),
                "excess_quantity": st.column_config.NumberColumn("Excess Qty", format="%.0f"),
                "cost_per_unit": st.column_config.NumberColumn("Unit Cost", format="$%.2f"),
                "estimated_excess_value": st.column_config.NumberColumn("Excess Value", format="$%.2f"),
                "days_unused": st.column_config.NumberColumn("Days Unused", format="%.0f"),
                "deduct_qty_30d": st.column_config.NumberColumn("30d Pull Qty", format="%.0f"),
                "deduct_qty_90d": st.column_config.NumberColumn("90d Pull Qty", format="%.0f"),
                "last_deducted": st.column_config.DatetimeColumn("Last Pull", format="MM/DD/YYYY"),
            },
        )

        if savings_view.empty:
            st.info("No savings opportunities match the current filters.")
        else:
            st.markdown("##### Log Savings Project Action")
            project_options = savings_view.sort_values(
                ["estimated_excess_value", "excess_quantity", "days_unused"],
                ascending=[False, False, False],
            ).copy()
            project_options["project_label"] = project_options.apply(
                lambda row: (
                    f"{row.get('med_id', '')} | {row.get('device', '')} | {row.get('pocket_location', '')} | "
                    f"${row.get('estimated_excess_value', 0):,.0f}"
                ),
                axis=1,
            )
            selected_project_label = st.selectbox(
                "Opportunity to log",
                project_options["project_label"].tolist(),
                key="savings_project_opportunity",
            )
            selected_project = project_options[project_options["project_label"].eq(selected_project_label)].iloc[0]

            with st.form("pyxis_savings_project_form"):
                p1, p2, p3 = st.columns(3)
                project_status = p1.selectbox(
                    "Project status",
                    ["Planned", "Implemented", "Monitoring", "Completed", "Cancelled"],
                    index=1,
                )
                action_type = p2.selectbox(
                    "Action taken",
                    [
                        "Reduced max",
                        "Reduced min/max",
                        "Removed from device",
                        "Moved quantity",
                        "Verified no change",
                        "Other",
                    ],
                )
                owner = p3.text_input("Owner", value="")

                q1, q2, q3 = st.columns(3)
                new_min_qty = q1.number_input(
                    "New min",
                    min_value=0.0,
                    value=float(selected_project.get("min_qty", 0) or 0),
                    step=1.0,
                )
                new_max_qty = q2.number_input(
                    "New max",
                    min_value=0.0,
                    value=float(selected_project.get("suggested_max", 0) or 0),
                    step=1.0,
                )
                actual_savings = q3.number_input(
                    "Actual savings",
                    min_value=0.0,
                    value=float(selected_project.get("estimated_excess_value", 0) or 0),
                    step=5.0,
                )

                d1, d2 = st.columns(2)
                implemented_dt = d1.date_input("Implemented date", value=pd.Timestamp.today().date())
                follow_up_dt = d2.date_input("Follow-up date", value=(pd.Timestamp.today() + pd.Timedelta(days=30)).date())
                project_note = st.text_area(
                    "Project note",
                    value=f"{selected_project.get('suggested_action', '')}. Original max {selected_project.get('max_qty', 0):.0f}; suggested max {selected_project.get('suggested_max', 0):.0f}.",
                )
                log_project = st.form_submit_button("Save to project portfolio")

                if log_project:
                    project_key = "|".join([
                        "pyxis-savings",
                        str(selected_project.get("device", "")),
                        str(selected_project.get("pocket_location", "")),
                        str(selected_project.get("med_id", "")),
                    ])
                    save_pyxis_savings_project({
                        "project_key": project_key,
                        "project_name": f"{selected_project.get('med_id', '')} par review - {selected_project.get('device', '')}",
                        "device": selected_project.get("device", ""),
                        "zone": selected_project.get("zone", ""),
                        "pocket_location": selected_project.get("pocket_location", ""),
                        "med_id": selected_project.get("med_id", ""),
                        "med_desc": selected_project.get("med_desc", ""),
                        "brand_name": selected_project.get("brand_name", ""),
                        "action_type": action_type,
                        "project_status": project_status,
                        "owner": owner,
                        "current_quantity": float(selected_project.get("current_quantity", 0) or 0),
                        "prior_min_qty": float(selected_project.get("min_qty", 0) or 0),
                        "prior_max_qty": float(selected_project.get("max_qty", 0) or 0),
                        "new_min_qty": new_min_qty,
                        "new_max_qty": new_max_qty,
                        "excess_quantity": float(selected_project.get("excess_quantity", 0) or 0),
                        "cost_per_unit": float(selected_project.get("cost_per_unit", 0) or 0),
                        "estimated_savings": float(selected_project.get("estimated_excess_value", 0) or 0),
                        "actual_savings": actual_savings,
                        "implemented_dt": implemented_dt if project_status in ["Implemented", "Monitoring", "Completed"] else None,
                        "follow_up_dt": follow_up_dt,
                        "note": project_note,
                    })
                    st.success("Saved project action to the Pyxis savings portfolio.")
                    st.rerun()

        st.download_button(
            "Download Pyxis overstock savings CSV",
            data=savings_view[savings_cols].to_csv(index=False).encode("utf-8"),
            file_name="pyxis_overstock_savings.csv",
            mime="text/csv",
        )

    st.markdown("##### Pyxis Savings Project Portfolio")
    if pyxis_savings_projects.empty:
        st.info("No Pyxis savings project actions have been logged yet.")
    else:
        portfolio = pyxis_savings_projects.copy()
        active_statuses = ["Planned", "Implemented", "Monitoring"]
        pf1, pf2, pf3, pf4 = st.columns(4)
        pf1.metric("Projects", f"{len(portfolio):,}")
        pf2.metric("Active Projects", f"{int(portfolio['project_status'].isin(active_statuses).sum()):,}")
        pf3.metric("Estimated Savings", f"${portfolio['estimated_savings'].sum():,.0f}")
        pf4.metric("Actual Savings", f"${portfolio['actual_savings'].sum():,.0f}")

        portfolio_summary = (
            portfolio.groupby("project_status", dropna=False)[["estimated_savings", "actual_savings"]]
            .sum()
            .reset_index()
        )
        st.plotly_chart(
            px.bar(
                portfolio_summary,
                x="project_status",
                y=["estimated_savings", "actual_savings"],
                barmode="group",
            ),
            width="stretch",
        )

        portfolio_cols = [
            "project_status",
            "project_name",
            "device",
            "pocket_location",
            "med_id",
            "med_desc",
            "action_type",
            "owner",
            "prior_min_qty",
            "prior_max_qty",
            "new_min_qty",
            "new_max_qty",
            "excess_quantity",
            "estimated_savings",
            "actual_savings",
            "implemented_dt",
            "follow_up_dt",
            "note",
        ]
        st.dataframe(
            portfolio.sort_values(["project_status", "estimated_savings"], ascending=[True, False])[portfolio_cols],
            width="stretch",
            hide_index=True,
            column_config={
                "project_status": "Status",
                "project_name": "Project",
                "pocket_location": "Pocket",
                "prior_min_qty": st.column_config.NumberColumn("Old Min", format="%.0f"),
                "prior_max_qty": st.column_config.NumberColumn("Old Max", format="%.0f"),
                "new_min_qty": st.column_config.NumberColumn("New Min", format="%.0f"),
                "new_max_qty": st.column_config.NumberColumn("New Max", format="%.0f"),
                "excess_quantity": st.column_config.NumberColumn("Excess Qty", format="%.0f"),
                "estimated_savings": st.column_config.NumberColumn("Estimated Savings", format="$%.2f"),
                "actual_savings": st.column_config.NumberColumn("Actual Savings", format="$%.2f"),
                "implemented_dt": st.column_config.DatetimeColumn("Implemented", format="MM/DD/YYYY"),
                "follow_up_dt": st.column_config.DatetimeColumn("Follow Up", format="MM/DD/YYYY"),
            },
        )
        st.download_button(
            "Download Pyxis savings portfolio CSV",
            data=portfolio[portfolio_cols].to_csv(index=False).encode("utf-8"),
            file_name="pyxis_savings_project_portfolio.csv",
            mime="text/csv",
        )
