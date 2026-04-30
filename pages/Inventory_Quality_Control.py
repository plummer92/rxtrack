import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

import App


st.set_page_config(page_title="Inventory Quality Control", page_icon="📦", layout="wide")

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


def build_isa_lifecycle(isa_items, receiving_summary, inventory_counts):
    if isa_items.empty:
        return pd.DataFrame()

    base = isa_items.merge(receiving_summary, on="med_id", how="left")
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
    base["receiving_events"] = pd.to_numeric(base["receiving_events"], errors="coerce").fillna(0).astype(int)
    base["total_received_qty"] = pd.to_numeric(base["total_received_qty"], errors="coerce").fillna(0)
    if "current_count" not in base.columns:
        base["current_count"] = 0
    if "pocket_count" not in base.columns:
        base["pocket_count"] = 0
    base["current_count"] = pd.to_numeric(base["current_count"], errors="coerce").fillna(0)
    base["pocket_count"] = pd.to_numeric(base["pocket_count"], errors="coerce").fillna(0).astype(int)
    base["receiving_status"] = base["last_received"].apply(lambda value: "No Receiving Match" if pd.isna(value) else "Matched")

    return base.sort_values(
        ["receiving_status", "days_since_last_received", "isa_name", "med_desc"],
        ascending=[True, False, True, True],
    )


receiving = prep_receiving(load_receiving_history())
isa_items = prep_isa_items(load_latest_isa_items())
inventory_counts = load_inventory_counts()
receiving_summary = build_receiving_summary(receiving)
isa_lifecycle = build_isa_lifecycle(isa_items, receiving_summary, inventory_counts)

st.subheader("ISA Receiving Lifecycle")
st.caption("This starts the med lifecycle clock from Pharmacy Workflow rows where event type is exactly `Receiving`.")

if receiving.empty:
    st.warning("No `Receiving` rows are loaded in the Pharmacy Workflow table.")
    st.stop()

if isa_lifecycle.empty:
    st.warning("No ISA item snapshot is loaded. Upload the Days Since Last Cycle Count Report to populate ISA items.")
    st.stop()

snapshot_date = isa_lifecycle["snapshot_date"].dropna().max()
oldest_receipt = receiving["received_dt"].min()
newest_receipt = receiving["received_dt"].max()

m1, m2, m3, m4 = st.columns(4)
m1.metric("ISA Items", f"{len(isa_lifecycle):,}")
m2.metric("Items With Receiving Match", f"{int((isa_lifecycle['receiving_status'] == 'Matched').sum()):,}")
m3.metric("Receiving Rows Loaded", f"{len(receiving):,}")
m4.metric("ISA Snapshot", snapshot_date.strftime("%m/%d/%Y") if pd.notna(snapshot_date) else "Unknown")

h1, h2 = st.columns(2)
h1.metric("Oldest Receiving Row", oldest_receipt.strftime("%m/%d/%Y") if pd.notna(oldest_receipt) else "Unknown")
h2.metric("Newest Receiving Row", newest_receipt.strftime("%m/%d/%Y") if pd.notna(newest_receipt) else "Unknown")

filter_col1, filter_col2, filter_col3 = st.columns(3)
isa_options = sorted(isa_lifecycle["isa_name"].dropna().unique())
selected_isas = filter_col1.multiselect("ISA", isa_options, default=isa_options[:1] if isa_options else [])
status_options = ["Matched", "No Receiving Match"]
selected_statuses = filter_col2.multiselect("Receiving status", status_options, default=status_options)
med_search = filter_col3.text_input("Medication search")

view = isa_lifecycle.copy()
if selected_isas:
    view = view[view["isa_name"].isin(selected_isas)]
if selected_statuses:
    view = view[view["receiving_status"].isin(selected_statuses)]
if med_search:
    med_mask = (
        view["med_id"].str.contains(med_search, case=False, na=False)
        | view["med_desc"].str.contains(med_search, case=False, na=False)
    )
    view = view[med_mask]

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
    aging = view[view["days_since_last_received"].notna()].copy()
    if aging.empty:
        st.info("No matched receiving dates to chart.")
    else:
        aging["aging_bucket"] = pd.cut(
            aging["days_since_last_received"],
            bins=[-1, 30, 60, 90, 180, 365, 99999],
            labels=["0-30", "31-60", "61-90", "91-180", "181-365", "365+"],
        )
        bucket_summary = aging.groupby("aging_bucket", observed=False).size().reset_index(name="item_count")
        st.markdown("##### Items by Days Since Last Received")
        st.plotly_chart(px.bar(bucket_summary, x="aging_bucket", y="item_count"), width="stretch")

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

st.markdown("##### ISA Item Lifecycle Table")
display_cols = [
    "isa_name",
    "location",
    "med_id",
    "med_desc",
    "current_count",
    "pocket_count",
    "last_received",
    "days_since_last_received",
    "first_received",
    "days_since_first_received",
    "receiving_events",
    "total_received_qty",
    "last_received_by",
    "receiving_status",
]
st.dataframe(view[display_cols], width="stretch", hide_index=True)

st.download_button(
    "Download ISA receiving lifecycle CSV",
    data=view[display_cols].to_csv(index=False).encode("utf-8"),
    file_name="isa_receiving_lifecycle.csv",
    mime="text/csv",
)

with st.expander("Raw receiving rows"):
    raw_cols = ["received_dt", "queue_id", "med_id", "med_desc", "user_name", "qty"]
    st.dataframe(receiving[raw_cols], width="stretch", hide_index=True)
