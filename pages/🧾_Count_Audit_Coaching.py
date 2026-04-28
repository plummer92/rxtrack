import io

import pandas as pd
import streamlit as st
from sqlalchemy import text

import App

COACHING_LOG_TABLE = "verify_count_audit_coaching_log"
COACHING_STATUS_OPTIONS = ["Needs Coaching", "In Progress", "Coaching Done"]
OPEN_COACHING_STATUSES = ["Needs Coaching", "In Progress"]

st.set_page_config(
    page_title="Count Audit Coaching",
    page_icon="🧾",
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
        conn.execute(text(f"""
            UPDATE {COACHING_LOG_TABLE}
            SET coaching_status = 'Needs Coaching'
            WHERE coaching_status IS NULL OR coaching_status = ''
        """))


@st.cache_data(ttl=300)
def load_coaching_rows(start, end):
    ensure_coaching_log_table()
    sql = text(f"""
        SELECT audit_pk, completed_at, coaching_user, verify_dt, device, med_id,
               med_desc, qty_off, prior_refill_dt, prior_refill_qty,
               correction_dt, correction_by, correction_qty,
               refill_date_pull_qty, verify_date_pull_qty, refill_qty_vs_pull,
               COALESCE(NULLIF(coaching_status, ''), 'Needs Coaching') AS coaching_status,
               coaching_completed_at, notes
        FROM {COACHING_LOG_TABLE}
        WHERE verify_dt::date BETWEEN :start AND :end
        ORDER BY completed_at DESC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"start": start, "end": end})
    if df.empty:
        return df
    for col in ["completed_at", "verify_dt", "prior_refill_dt", "correction_dt", "coaching_completed_at"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in [
        "qty_off", "prior_refill_qty", "correction_qty",
        "refill_date_pull_qty", "verify_date_pull_qty", "refill_qty_vs_pull",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["abs_qty_off"] = df["qty_off"].abs()
    return df


def update_coaching_status(status_updates: list[dict]) -> int:
    if not status_updates:
        return 0
    ensure_coaching_log_table()
    sql = text(f"""
        UPDATE {COACHING_LOG_TABLE}
        SET coaching_status = :coaching_status,
            coaching_completed_at = CASE
                WHEN :coaching_status = 'Coaching Done'
                    THEN COALESCE(coaching_completed_at, CURRENT_TIMESTAMP)
                ELSE NULL
            END
        WHERE audit_pk = :audit_pk
    """)
    with engine.begin() as conn:
        conn.execute(sql, status_updates)
    return len(status_updates)


if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Count Audit Coaching",
        "Track count-audit rows from needing coaching through coaching done.",
        kicker="Coaching",
    )
else:
    st.header("Count Audit Coaching")
    st.caption("Track count-audit rows from needing coaching through coaching done.")

rows = load_coaching_rows(start_date, end_date)

if rows.empty:
    st.info("No count-audit coaching rows are logged for the selected date range yet.")
    st.stop()

with st.sidebar:
    st.divider()
    st.subheader("Report Filters")
    user_filter = st.multiselect(
        "Coaching User",
        sorted(rows["coaching_user"].dropna().astype(str).unique()),
        placeholder="All users",
        key="count_audit_coaching_user_filter",
    )
    device_filter = st.multiselect(
        "Device",
        sorted(rows["device"].dropna().astype(str).unique()),
        placeholder="All devices",
        key="count_audit_coaching_device_filter",
    )
    correction_user_filter = st.multiselect(
        "Correction User",
        sorted(rows["correction_by"].dropna().astype(str).unique()),
        placeholder="All correction users",
        key="count_audit_coaching_correction_user_filter",
    )
    status_filter = st.multiselect(
        "Coaching Status",
        COACHING_STATUS_OPTIONS,
        default=COACHING_STATUS_OPTIONS,
        key="count_audit_coaching_status_filter",
    )

filtered = rows.copy()
if user_filter:
    filtered = filtered[filtered["coaching_user"].astype(str).isin(user_filter)]
if device_filter:
    filtered = filtered[filtered["device"].astype(str).isin(device_filter)]
if correction_user_filter:
    filtered = filtered[filtered["correction_by"].astype(str).isin(correction_user_filter)]
if status_filter:
    filtered = filtered[filtered["coaching_status"].isin(status_filter)]
else:
    filtered = filtered.iloc[0:0]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Needs Coaching", f"{(filtered['coaching_status'] == 'Needs Coaching').sum():,}")
m2.metric("In Progress", f"{(filtered['coaching_status'] == 'In Progress').sum():,}")
m3.metric("Coaching Done", f"{(filtered['coaching_status'] == 'Coaching Done').sum():,}")
m4.metric("Total Qty Off", f"{filtered['abs_qty_off'].sum():,.0f}")

st.divider()

st.subheader("Coaching Summary by User")
summary = (
    filtered.groupby("coaching_user")
    .agg(
        total_rows=("audit_pk", "count"),
        needs_coaching=("coaching_status", lambda s: (s == "Needs Coaching").sum()),
        in_progress=("coaching_status", lambda s: (s == "In Progress").sum()),
        coaching_done=("coaching_status", lambda s: (s == "Coaching Done").sum()),
        total_qty_off=("abs_qty_off", "sum"),
        avg_qty_off=("abs_qty_off", "mean"),
        unique_meds=("med_id", "nunique"),
        unique_devices=("device", "nunique"),
        latest_logged=("completed_at", "max"),
    )
    .reset_index()
    .sort_values(["needs_coaching", "in_progress", "total_qty_off"], ascending=False)
)
st.dataframe(
    summary,
    use_container_width=True,
    hide_index=True,
    column_config={
        "coaching_user": st.column_config.TextColumn("Coaching User"),
        "total_rows": st.column_config.NumberColumn("Rows", format="%d"),
        "needs_coaching": st.column_config.NumberColumn("Needs Coaching", format="%d"),
        "in_progress": st.column_config.NumberColumn("In Progress", format="%d"),
        "coaching_done": st.column_config.NumberColumn("Coaching Done", format="%d"),
        "total_qty_off": st.column_config.NumberColumn("Total Qty Off", format="%.0f"),
        "avg_qty_off": st.column_config.NumberColumn("Avg Qty Off", format="%.1f"),
        "unique_meds": st.column_config.NumberColumn("Meds", format="%d"),
        "unique_devices": st.column_config.NumberColumn("Devices", format="%d"),
        "latest_logged": st.column_config.DatetimeColumn("Latest Logged", format="MM/DD/YY HH:mm"),
    },
)

st.download_button(
    "Export Coaching Summary to Excel",
    data=to_excel_bytes(summary),
    file_name="count_audit_coaching_summary.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()

st.subheader("Coaching Queue")
st.caption("Change the status here as you start coaching or finish it. Rows marked Coaching Done move into the completed section below.")
detail_columns = [
    "coaching_status", "completed_at", "coaching_completed_at", "coaching_user", "verify_dt", "device", "med_id", "med_desc",
    "qty_off", "prior_refill_dt", "prior_refill_qty", "correction_dt",
    "correction_by", "correction_qty", "refill_date_pull_qty",
    "refill_qty_vs_pull", "verify_date_pull_qty", "notes",
]
queue = filtered[filtered["coaching_status"].isin(OPEN_COACHING_STATUSES)].copy()
queue = queue.sort_values(["coaching_status", "completed_at"], ascending=[True, False])
edited_queue = st.data_editor(
    queue[["audit_pk"] + detail_columns],
    use_container_width=True,
    hide_index=True,
    disabled=[col for col in ["audit_pk"] + detail_columns if col != "coaching_status"],
    column_config={
        "audit_pk": None,
        "coaching_status": st.column_config.SelectboxColumn("Status", options=COACHING_STATUS_OPTIONS, required=True),
        "completed_at": st.column_config.DatetimeColumn("Logged", format="MM/DD/YY HH:mm"),
        "coaching_completed_at": st.column_config.DatetimeColumn("Coaching Done", format="MM/DD/YY HH:mm"),
        "coaching_user": st.column_config.TextColumn("Coaching User"),
        "verify_dt": st.column_config.DatetimeColumn("Verify Time", format="MM/DD/YY HH:mm"),
        "device": st.column_config.TextColumn("Pyxis"),
        "med_id": st.column_config.TextColumn("Med ID"),
        "med_desc": st.column_config.TextColumn("Medication"),
        "qty_off": st.column_config.NumberColumn("Qty Off", format="%.0f"),
        "prior_refill_dt": st.column_config.DatetimeColumn("Prior Refill", format="MM/DD/YY HH:mm"),
        "prior_refill_qty": st.column_config.NumberColumn("Prior Refill Qty", format="%.0f"),
        "correction_dt": st.column_config.DatetimeColumn("Count Inventory", format="MM/DD/YY HH:mm"),
        "correction_by": st.column_config.TextColumn("Count Inventory User"),
        "correction_qty": st.column_config.NumberColumn("Count Inventory Qty", format="%.0f"),
        "refill_date_pull_qty": st.column_config.NumberColumn("Refill-Date Pull Qty", format="%.0f"),
        "refill_qty_vs_pull": st.column_config.NumberColumn("Refill Qty vs Pull", format="%.0f"),
        "verify_date_pull_qty": st.column_config.NumberColumn("Verify-Date Pull Qty", format="%.0f"),
        "notes": st.column_config.TextColumn("Notes"),
    },
)
original_status = queue.set_index("audit_pk")["coaching_status"].to_dict()
status_updates = []
for _, row in edited_queue.iterrows():
    new_status = row["coaching_status"]
    if row["audit_pk"] in original_status and new_status != original_status[row["audit_pk"]]:
        status_updates.append({"audit_pk": row["audit_pk"], "coaching_status": new_status})

if st.button("Save coaching status changes", type="primary", disabled=not status_updates):
    changed = update_coaching_status(status_updates)
    load_coaching_rows.clear()
    st.success(f"Updated {changed} coaching status row(s).")
    st.rerun()

st.divider()

st.subheader("Coaching Done")
done_rows = filtered[filtered["coaching_status"] == "Coaching Done"].copy()
st.dataframe(
    done_rows[detail_columns].sort_values("coaching_completed_at", ascending=False),
    use_container_width=True,
    hide_index=True,
    column_config={
        "coaching_status": st.column_config.TextColumn("Status"),
        "completed_at": st.column_config.DatetimeColumn("Logged", format="MM/DD/YY HH:mm"),
        "coaching_completed_at": st.column_config.DatetimeColumn("Coaching Done", format="MM/DD/YY HH:mm"),
        "coaching_user": st.column_config.TextColumn("Coaching User"),
        "verify_dt": st.column_config.DatetimeColumn("Verify Time", format="MM/DD/YY HH:mm"),
        "device": st.column_config.TextColumn("Pyxis"),
        "med_id": st.column_config.TextColumn("Med ID"),
        "med_desc": st.column_config.TextColumn("Medication"),
        "qty_off": st.column_config.NumberColumn("Qty Off", format="%.0f"),
        "prior_refill_dt": st.column_config.DatetimeColumn("Prior Refill", format="MM/DD/YY HH:mm"),
        "prior_refill_qty": st.column_config.NumberColumn("Prior Refill Qty", format="%.0f"),
        "correction_dt": st.column_config.DatetimeColumn("Count Inventory", format="MM/DD/YY HH:mm"),
        "correction_by": st.column_config.TextColumn("Count Inventory User"),
        "correction_qty": st.column_config.NumberColumn("Count Inventory Qty", format="%.0f"),
        "refill_date_pull_qty": st.column_config.NumberColumn("Refill-Date Pull Qty", format="%.0f"),
        "refill_qty_vs_pull": st.column_config.NumberColumn("Refill Qty vs Pull", format="%.0f"),
        "verify_date_pull_qty": st.column_config.NumberColumn("Verify-Date Pull Qty", format="%.0f"),
        "notes": st.column_config.TextColumn("Notes"),
    },
)

st.download_button(
    "Export Coaching Rows to Excel",
    data=to_excel_bytes(filtered[detail_columns]),
    file_name="count_audit_coaching_rows.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
