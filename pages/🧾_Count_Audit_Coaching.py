import io

import pandas as pd
import streamlit as st
from sqlalchemy import text

import App

COACHING_LOG_TABLE = "verify_count_audit_coaching_log"

st.set_page_config(
    page_title="Count Audit Coaching",
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
            notes TEXT
        )
    """)
    with engine.begin() as conn:
        conn.execute(sql)
        conn.execute(text(f"ALTER TABLE {COACHING_LOG_TABLE} ADD COLUMN IF NOT EXISTS refill_date_pull_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {COACHING_LOG_TABLE} ADD COLUMN IF NOT EXISTS verify_date_pull_qty FLOAT"))
        conn.execute(text(f"ALTER TABLE {COACHING_LOG_TABLE} ADD COLUMN IF NOT EXISTS refill_qty_vs_pull FLOAT"))


@st.cache_data(ttl=300)
def load_coaching_rows(start, end):
    ensure_coaching_log_table()
    sql = text(f"""
        SELECT audit_pk, completed_at, coaching_user, verify_dt, device, med_id,
               med_desc, qty_off, prior_refill_dt, prior_refill_qty,
               correction_dt, correction_by, correction_qty,
               refill_date_pull_qty, verify_date_pull_qty, refill_qty_vs_pull,
               notes
        FROM {COACHING_LOG_TABLE}
        WHERE verify_dt::date BETWEEN :start AND :end
        ORDER BY completed_at DESC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"start": start, "end": end})
    if df.empty:
        return df
    for col in ["completed_at", "verify_dt", "prior_refill_dt", "correction_dt"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in [
        "qty_off", "prior_refill_qty", "correction_qty",
        "refill_date_pull_qty", "verify_date_pull_qty", "refill_qty_vs_pull",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["abs_qty_off"] = df["qty_off"].abs()
    return df


if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Count Audit Coaching",
        "Completed verify-count audit rows grouped by the refill user tied to the count mismatch.",
        kicker="Coaching",
    )
else:
    st.header("Count Audit Coaching")
    st.caption("Completed verify-count audit rows grouped by the refill user tied to the count mismatch.")

rows = load_coaching_rows(start_date, end_date)

if rows.empty:
    st.info("No completed coaching rows are logged for the selected date range yet.")
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

filtered = rows.copy()
if user_filter:
    filtered = filtered[filtered["coaching_user"].astype(str).isin(user_filter)]
if device_filter:
    filtered = filtered[filtered["device"].astype(str).isin(device_filter)]
if correction_user_filter:
    filtered = filtered[filtered["correction_by"].astype(str).isin(correction_user_filter)]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Completed Rows", f"{len(filtered):,}")
m2.metric("Coaching Users", f"{filtered['coaching_user'].nunique():,}")
m3.metric("Total Qty Off", f"{filtered['abs_qty_off'].sum():,.0f}")
m4.metric("Avg Qty Off", f"{filtered['abs_qty_off'].mean():,.1f}")

st.divider()

st.subheader("Coaching Summary by User")
summary = (
    filtered.groupby("coaching_user")
    .agg(
        completed_rows=("audit_pk", "count"),
        total_qty_off=("abs_qty_off", "sum"),
        avg_qty_off=("abs_qty_off", "mean"),
        unique_meds=("med_id", "nunique"),
        unique_devices=("device", "nunique"),
        latest_completed=("completed_at", "max"),
    )
    .reset_index()
    .sort_values(["completed_rows", "total_qty_off"], ascending=False)
)
st.dataframe(
    summary,
    use_container_width=True,
    hide_index=True,
    column_config={
        "coaching_user": st.column_config.TextColumn("Coaching User"),
        "completed_rows": st.column_config.NumberColumn("Rows", format="%d"),
        "total_qty_off": st.column_config.NumberColumn("Total Qty Off", format="%.0f"),
        "avg_qty_off": st.column_config.NumberColumn("Avg Qty Off", format="%.1f"),
        "unique_meds": st.column_config.NumberColumn("Meds", format="%d"),
        "unique_devices": st.column_config.NumberColumn("Devices", format="%d"),
        "latest_completed": st.column_config.DatetimeColumn("Latest Completed", format="MM/DD/YY HH:mm"),
    },
)

st.download_button(
    "Export Coaching Summary to Excel",
    data=to_excel_bytes(summary),
    file_name="count_audit_coaching_summary.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()

st.subheader("Completed Coaching Rows")
detail_columns = [
    "completed_at", "coaching_user", "verify_dt", "device", "med_id", "med_desc",
    "qty_off", "prior_refill_dt", "prior_refill_qty", "correction_dt",
    "correction_by", "correction_qty", "refill_date_pull_qty",
    "refill_qty_vs_pull", "verify_date_pull_qty", "notes",
]
st.dataframe(
    filtered[detail_columns].sort_values("completed_at", ascending=False),
    use_container_width=True,
    hide_index=True,
    column_config={
        "completed_at": st.column_config.DatetimeColumn("Completed", format="MM/DD/YY HH:mm"),
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
    "Export Completed Rows to Excel",
    data=to_excel_bytes(filtered[detail_columns]),
    file_name="count_audit_coaching_rows.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
