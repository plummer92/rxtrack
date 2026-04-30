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
        "Start with the daily pharmacy workflow orders that represent medication receiving into the carousel.",
        kicker="Receiving Orders",
    )
else:
    st.header("Inventory Quality Control")
    st.caption("Receiving orders into the carousel by event type.")


@st.cache_data(ttl=60)
def load_pharmacy_orders(start, end):
    sql = text("""
        SELECT
            pk,
            queue_id,
            priority AS event_type,
            dt::timestamp AS dt,
            med_id,
            med_desc,
            destination,
            user_name,
            qty
        FROM pharmacy_orders
        WHERE dt::date BETWEEN :start_dt AND :end_dt
        ORDER BY dt::timestamp DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"start_dt": start, "end_dt": end})


@st.cache_data(ttl=60)
def load_all_pharmacy_order_bounds():
    sql = text("""
        SELECT
            COUNT(*) AS order_rows,
            COUNT(DISTINCT med_id) AS unique_meds,
            MIN(dt::timestamp) AS first_order_dt,
            MAX(dt::timestamp) AS last_order_dt
        FROM pharmacy_orders
        WHERE dt IS NOT NULL
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


def prep_orders(df):
    if df.empty:
        return df

    out = df.copy()
    out["dt"] = pd.to_datetime(out["dt"], errors="coerce")
    out["order_date"] = out["dt"].dt.date
    out["event_type"] = out["event_type"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
    out["destination"] = out["destination"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
    out["user_name"] = out["user_name"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
    out["med_id"] = out["med_id"].fillna("").astype(str).str.strip()
    out["med_desc"] = out["med_desc"].fillna("").astype(str).str.strip()
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").fillna(0)
    return out


orders = prep_orders(load_pharmacy_orders(start_date, end_date))
bounds = load_all_pharmacy_order_bounds()

st.subheader("Receiving Orders Into Carousel")
st.caption("This table uses the Pharmacy Workflow upload. In that file, `Priority` is treated as the event type.")

if bounds.empty or int(bounds.loc[0, "order_rows"] or 0) == 0:
    st.warning("No pharmacy workflow order data is loaded yet.")
    st.stop()

total_rows = int(bounds.loc[0, "order_rows"] or 0)
total_meds = int(bounds.loc[0, "unique_meds"] or 0)
first_loaded = pd.to_datetime(bounds.loc[0, "first_order_dt"], errors="coerce")
last_loaded = pd.to_datetime(bounds.loc[0, "last_order_dt"], errors="coerce")

g1, g2, g3, g4 = st.columns(4)
g1.metric("All Loaded Order Rows", f"{total_rows:,}")
g2.metric("All Loaded Meds", f"{total_meds:,}")
g3.metric("Oldest Loaded Order", first_loaded.strftime("%m/%d/%Y") if pd.notna(first_loaded) else "Unknown")
g4.metric("Newest Loaded Order", last_loaded.strftime("%m/%d/%Y") if pd.notna(last_loaded) else "Unknown")

if orders.empty:
    st.info("No receiving orders found for the selected date range.")
    st.stop()

all_event_types = sorted(orders["event_type"].dropna().unique())
all_destinations = sorted(orders["destination"].dropna().unique())

f1, f2, f3 = st.columns(3)
selected_event_types = f1.multiselect(
    "Event type",
    all_event_types,
    default=all_event_types,
)
selected_destinations = f2.multiselect("Destination", all_destinations)
med_search = f3.text_input("Medication search")

view = orders.copy()
if selected_event_types:
    view = view[view["event_type"].isin(selected_event_types)]
if selected_destinations:
    view = view[view["destination"].isin(selected_destinations)]
if med_search:
    med_mask = (
        view["med_id"].str.contains(med_search, case=False, na=False)
        | view["med_desc"].str.contains(med_search, case=False, na=False)
    )
    view = view[med_mask]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Filtered Rows", f"{len(view):,}")
m2.metric("Filtered Meds", f"{view['med_id'].nunique():,}")
m3.metric("Filtered Qty", f"{view['qty'].sum():,.0f}")
m4.metric("Event Types", f"{view['event_type'].nunique():,}")

chart_col1, chart_col2 = st.columns(2)
with chart_col1:
    event_summary = (
        view.groupby("event_type", dropna=False)
        .agg(order_rows=("pk", "count"), total_qty=("qty", "sum"), unique_meds=("med_id", "nunique"))
        .reset_index()
        .sort_values("order_rows", ascending=False)
    )
    st.markdown("##### Orders by Event Type")
    st.plotly_chart(
        px.bar(event_summary, x="order_rows", y="event_type", orientation="h"),
        width="stretch",
    )

with chart_col2:
    daily_summary = (
        view.groupby("order_date", dropna=False)
        .agg(order_rows=("pk", "count"), total_qty=("qty", "sum"))
        .reset_index()
        .sort_values("order_date")
    )
    st.markdown("##### Daily Receiving Volume")
    st.plotly_chart(px.bar(daily_summary, x="order_date", y="order_rows"), width="stretch")

detail_col1, detail_col2 = st.columns(2)
with detail_col1:
    st.markdown("##### Top Meds Received")
    med_summary = (
        view.groupby(["med_id", "med_desc"], dropna=False)
        .agg(order_rows=("pk", "count"), total_qty=("qty", "sum"), last_received=("dt", "max"))
        .reset_index()
        .sort_values(["order_rows", "total_qty"], ascending=[False, False])
        .head(25)
    )
    st.dataframe(med_summary, width="stretch", hide_index=True)

with detail_col2:
    st.markdown("##### Destination Summary")
    dest_summary = (
        view.groupby("destination", dropna=False)
        .agg(order_rows=("pk", "count"), total_qty=("qty", "sum"), unique_meds=("med_id", "nunique"))
        .reset_index()
        .sort_values("order_rows", ascending=False)
        .head(25)
    )
    st.dataframe(dest_summary, width="stretch", hide_index=True)

st.markdown("##### Receiving Order Detail")
display_cols = ["dt", "queue_id", "event_type", "med_id", "med_desc", "destination", "user_name", "qty"]
st.dataframe(view[display_cols], width="stretch", hide_index=True)

st.download_button(
    "Download filtered receiving orders CSV",
    data=view[display_cols].to_csv(index=False).encode("utf-8"),
    file_name="receiving_orders_by_event_type.csv",
    mime="text/csv",
)
