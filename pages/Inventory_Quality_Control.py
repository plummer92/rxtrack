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
    out["packaged_expire_date"] = out["hospital_expire_date"].fillna(out["bud"])
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


def build_isa_lifecycle(isa_items, receiving_summary, inventory_counts, packaging_summary):
    if isa_items.empty:
        return pd.DataFrame()

    base = isa_items.merge(receiving_summary, on="med_id", how="left")
    if not packaging_summary.empty:
        base = base.merge(packaging_summary, on="med_id", how="left")
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
    if "last_packaged" not in base.columns:
        base["last_packaged"] = pd.NaT
    if "latest_packaged_expire_date" not in base.columns:
        base["latest_packaged_expire_date"] = pd.NaT
    base["last_packaged"] = pd.to_datetime(base["last_packaged"], errors="coerce")
    base["latest_packaged_expire_date"] = pd.to_datetime(base["latest_packaged_expire_date"], errors="coerce")
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
    if "current_count" not in base.columns:
        base["current_count"] = 0
    if "pocket_count" not in base.columns:
        base["pocket_count"] = 0
    base["current_count"] = pd.to_numeric(base["current_count"], errors="coerce").fillna(0)
    base["pocket_count"] = pd.to_numeric(base["pocket_count"], errors="coerce").fillna(0).astype(int)
    base["receiving_status"] = base["last_received"].apply(lambda value: "No Receiving Match" if pd.isna(value) else "Matched")
    base["packaging_status"] = base["last_packaged"].apply(lambda value: "Packaged" if pd.notna(value) else "Not Packaged")
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
isa_items = prep_isa_items(load_latest_isa_items())
inventory_counts = load_inventory_counts()
pyxis_inventory = prep_pyxis_inventory(load_current_pyxis_inventory())
packaging = prep_packaging(load_packaging_history())
receiving_summary = build_receiving_summary(receiving)
packaging_summary = build_packaging_summary(packaging)
isa_lifecycle = build_isa_lifecycle(isa_items, receiving_summary, inventory_counts, packaging_summary)

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
if not packaged_review.empty:
    st.markdown("##### Packaged Items Expiring Within 90 Days")
    exp_cols = [
        "isa_name", "location", "med_id", "med_desc", "last_packaged",
        "latest_packaged_expire_date", "days_until_packaged_expire", "last_packaged_by",
    ]
    st.dataframe(
        packaged_review.sort_values("days_until_packaged_expire")[exp_cols],
        width="stretch",
        hide_index=True,
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
    "receiving_age_bucket",
    "receiving_review_priority",
    "first_received",
    "days_since_first_received",
    "receiving_events",
    "total_received_qty",
    "last_received_by",
    "receiving_status",
    "packaging_status",
    "last_packaged",
    "days_since_last_packaged",
    "latest_packaged_expire_date",
    "days_until_packaged_expire",
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
        "last_packaged": st.column_config.DatetimeColumn("Last Packaged", format="MM/DD/YYYY HH:mm"),
        "latest_packaged_expire_date": st.column_config.DatetimeColumn("Packaged Expire", format="MM/DD/YYYY"),
        "current_count": st.column_config.NumberColumn("Current Count", format="%.0f"),
        "pocket_count": st.column_config.NumberColumn("Carousel Pockets", format="%d"),
        "days_since_last_received": st.column_config.NumberColumn("Days Since Received", format="%.0f"),
        "days_until_packaged_expire": st.column_config.NumberColumn("Days Until Packaged Expire", format="%.0f"),
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

with st.expander("Raw packaging rows"):
    if packaging.empty:
        st.info("No packaging report rows are loaded yet.")
    else:
        package_cols = [
            "dispense_dt", "med_id", "med_desc", "qty_per_pack", "qoh",
            "manufacturer", "hospital_lot_number", "packaged_expire_date", "packaged_by", "confirmer",
        ]
        st.dataframe(packaging[package_cols], width="stretch", hide_index=True)
