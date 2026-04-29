from datetime import date

import numpy as np
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
        "Use the daily pharmacy workflow orders as the receiving signal, then compare inventory age, Pyxis activity, returns, and cancellations to find medication lifecycle risk.",
        kicker="Operations Control",
    )
else:
    st.header("Inventory Quality Control")
    st.caption("Daily receiving/orders, lifecycle risk, receiving checks, and cancellation patterns.")


RECEIVING_TABLE = "inventory_quality_receiving_log"
CANCEL_REVIEW_TABLE = "cancelled_transaction_review_log"


def db_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def ensure_quality_tables():
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {RECEIVING_TABLE} (
                id SERIAL PRIMARY KEY,
                log_date DATE NOT NULL,
                receiver_name TEXT,
                batch_ref TEXT,
                expiration_checked BOOLEAN DEFAULT FALSE,
                short_dated_found BOOLEAN DEFAULT FALSE,
                short_dated_count INTEGER DEFAULT 0,
                shortest_date_tier TEXT,
                returns_processed BOOLEAN DEFAULT FALSE,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {CANCEL_REVIEW_TABLE} (
                id SERIAL PRIMARY KEY,
                source_pk TEXT UNIQUE,
                reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                cancel_dt TIMESTAMP,
                user_name TEXT,
                device TEXT,
                med_id TEXT,
                med_desc TEXT,
                event_type TEXT,
                cancel_category TEXT,
                follow_up_needed BOOLEAN DEFAULT FALSE,
                notes TEXT
            )
        """))


ensure_quality_tables()


@st.cache_data(ttl=60)
def load_events(start, end):
    sql = text("""
        SELECT pk, dt::timestamp AS dt, user_name, device, med_id, med_desc, event_type, qty,
               beginning_qty, ending_qty, discrepancy_qty, discrepancy_reason, resolution_dt
        FROM events
        WHERE dt::date BETWEEN :start_dt AND :end_dt
        ORDER BY dt::timestamp DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"start_dt": start, "end_dt": end})


@st.cache_data(ttl=60)
def load_orders(start, end):
    sql = text("""
        SELECT pk, queue_id, priority, dt::timestamp AS dt, med_id, med_desc, destination, user_name, qty
        FROM pharmacy_orders
        WHERE dt::date BETWEEN :start_dt AND :end_dt
        ORDER BY dt::timestamp DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"start_dt": start, "end_dt": end})


@st.cache_data(ttl=60)
def load_inventory():
    sql = text("""
        SELECT station, med_id, med_desc, unit_cost, current_count, pocket_location
        FROM inventory_detailed
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=60)
def load_receiving_log():
    with engine.connect() as conn:
        return pd.read_sql(text(f"SELECT * FROM {RECEIVING_TABLE} ORDER BY log_date DESC, id DESC"), conn)


@st.cache_data(ttl=60)
def load_cancel_review_log():
    with engine.connect() as conn:
        return pd.read_sql(text(f"SELECT * FROM {CANCEL_REVIEW_TABLE} ORDER BY reviewed_at DESC, id DESC"), conn)


def clear_quality_caches():
    load_receiving_log.clear()
    load_cancel_review_log.clear()
    load_events.clear()
    load_orders.clear()
    load_inventory.clear()


def event_text(df, col="event_type"):
    return df[col].fillna("").astype(str).str.lower()


def classify_cancel(row):
    evt = str(row.get("event_type") or "").lower()
    reason = str(row.get("discrepancy_reason") or "").lower()
    if "expir" in evt or "outdate" in evt or "expir" in reason:
        return "Expiration concern"
    if pd.notna(row.get("discrepancy_qty")) and float(row.get("discrepancy_qty") or 0) != 0:
        return "Count mismatch"
    if any(word in evt for word in ["restock", "refill", "load", "replenish"]):
        return "Restock/load cancelled"
    if any(word in evt for word in ["unload", "empty", "return"]):
        return "Return/unload cancelled"
    if any(word in evt for word in ["dispense", "remove", "pull"]):
        return "Pull cancelled"
    return "Unknown"


def build_cancel_analysis(events):
    if events.empty:
        return pd.DataFrame()
    df = events.copy()
    df["event_norm"] = event_text(df)
    cancels = df[df["event_norm"].str.contains("cancel", na=False)].copy()
    if cancels.empty:
        return cancels
    cancels["likely_category"] = cancels.apply(classify_cancel, axis=1)
    return cancels


def merge_frames(frames):
    if not frames:
        return pd.DataFrame()
    risk = frames[0]
    for frame in frames[1:]:
        risk = risk.merge(frame, on=["med_id", "med_desc"], how="outer")
    return risk


def build_lifecycle_risk(events, orders, inventory):
    frames = []

    if not orders.empty:
        received = orders.copy()
        received["dt"] = pd.to_datetime(received["dt"], errors="coerce")
        frames.append(received.groupby(["med_id", "med_desc"], dropna=False).agg(
            first_received=("dt", "min"),
            last_received=("dt", "max"),
            received_events=("pk", "count"),
            received_qty=("qty", "sum"),
        ).reset_index())

    if not events.empty:
        ev = events.copy()
        ev["dt"] = pd.to_datetime(ev["dt"], errors="coerce")
        ev["event_norm"] = event_text(ev)
        pull_mask = ev["event_norm"].str.contains("dispense|remove|pull", na=False)
        access_mask = ~ev["event_norm"].str.contains("cancel", na=False)

        frames.append(ev[access_mask].groupby(["med_id", "med_desc"], dropna=False).agg(
            pyxis_accesses=("pk", "count"),
            last_pyxis_access=("dt", "max"),
        ).reset_index())
        frames.append(ev[pull_mask].groupby(["med_id", "med_desc"], dropna=False).agg(
            pull_events=("pk", "count"),
            last_pull=("dt", "max"),
        ).reset_index())
        frames.append(build_cancel_analysis(ev).groupby(["med_id", "med_desc"], dropna=False).agg(
            cancelled_events=("pk", "count"),
        ).reset_index())
        frames.append(ev[ev["event_norm"].str.contains("return|unload|empty", na=False)].groupby(["med_id", "med_desc"], dropna=False).agg(
            return_loop_events=("pk", "count"),
        ).reset_index())
        frames.append(ev[ev["event_norm"].str.contains("outdate|expir", na=False)].groupby(["med_id", "med_desc"], dropna=False).agg(
            outdate_events=("pk", "count"),
        ).reset_index())

    if inventory is not None and not inventory.empty:
        inv = inventory.copy()
        frames.append(inv.groupby(["med_id", "med_desc"], dropna=False).agg(
            current_count=("current_count", "sum"),
            stocked_locations=("station", "nunique"),
            pocket_examples=("pocket_location", lambda s: ", ".join(s.dropna().astype(str).head(3))),
        ).reset_index())

    risk = merge_frames(frames)
    if risk.empty:
        return risk

    today = pd.Timestamp(date.today())
    for col in ["first_received", "last_received", "last_pyxis_access", "last_pull"]:
        if col in risk.columns:
            risk[col] = pd.to_datetime(risk[col], errors="coerce")
        else:
            risk[col] = pd.NaT

    risk["days_since_first_received"] = (today - risk["first_received"]).dt.days
    risk["days_since_last_received"] = (today - risk["last_received"]).dt.days
    risk["days_since_last_access"] = (today - risk["last_pyxis_access"]).dt.days

    numeric_cols = [
        "received_events", "received_qty", "pyxis_accesses", "pull_events", "cancelled_events",
        "return_loop_events", "outdate_events", "current_count", "stocked_locations",
    ]
    for col in numeric_cols:
        if col not in risk.columns:
            risk[col] = 0
        risk[col] = pd.to_numeric(risk[col], errors="coerce").fillna(0)

    days_in_window = max((pd.to_datetime(end_date) - pd.to_datetime(start_date)).days + 1, 1)
    risk["pulls_per_30_days"] = risk["pull_events"] / days_in_window * 30
    risk["accesses_per_30_days"] = risk["pyxis_accesses"] / days_in_window * 30

    risk["risk_score"] = 0
    risk["risk_score"] += np.where(risk["days_since_first_received"].fillna(0) >= 180, 25, 0)
    risk["risk_score"] += np.where(risk["days_since_last_access"].fillna(999) >= 60, 25, 0)
    risk["risk_score"] += np.where(risk["pulls_per_30_days"] <= 2, 20, 0)
    risk["risk_score"] += np.where(risk["current_count"] > 0, 10, 0)
    risk["risk_score"] += np.where(risk["cancelled_events"] > 0, 10, 0)
    risk["risk_score"] += np.where(risk["return_loop_events"] > 0, 10, 0)
    risk["risk_score"] += np.where(risk["outdate_events"] > 0, 20, 0)

    risk["risk_level"] = pd.cut(
        risk["risk_score"],
        bins=[-1, 24, 49, 74, 999],
        labels=["Low", "Watch", "High", "Critical"],
    ).astype(str)

    return risk.sort_values(["risk_score", "days_since_first_received"], ascending=[False, False])


events = load_events(start_date, end_date)
orders = load_orders(start_date, end_date)
inventory = load_inventory()

tab_lifecycle, tab_orders, tab_receive, tab_cancel = st.tabs([
    "Lifecycle Risk",
    "Daily Receiving / Orders",
    "Receiving QC",
    "Cancelled Transactions",
])


with tab_orders:
    st.subheader("Daily Pharmacy Workflow Orders")
    st.caption("These rows come from the Pharmacy Workflow upload and act as the receiving/order stream for lifecycle risk.")

    if orders.empty:
        st.info("No pharmacy workflow orders found in the selected date range.")
    else:
        order_view = orders.copy()
        order_view["dt"] = pd.to_datetime(order_view["dt"], errors="coerce")
        order_view["order_date"] = order_view["dt"].dt.date
        order_view["priority"] = order_view["priority"].fillna("Unknown").astype(str)
        order_view["destination"] = order_view["destination"].fillna("Unknown").astype(str)
        order_view["user_name"] = order_view["user_name"].fillna("Unknown").astype(str)
        order_view["med_id"] = order_view["med_id"].fillna("").astype(str)
        order_view["med_desc"] = order_view["med_desc"].fillna("").astype(str)
        order_view["qty"] = pd.to_numeric(order_view["qty"], errors="coerce").fillna(0)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Order Rows", f"{len(order_view):,}")
        c2.metric("Unique Meds", f"{order_view['med_id'].nunique():,}")
        c3.metric("Total Qty", f"{order_view['qty'].sum():,.0f}")
        c4.metric("Destinations", f"{order_view['destination'].nunique():,}")

        f1, f2, f3 = st.columns(3)
        selected_priorities = f1.multiselect(
            "Priority / transaction type",
            sorted(order_view["priority"].dropna().unique()),
        )
        selected_destinations = f2.multiselect(
            "Destination",
            sorted(order_view["destination"].dropna().unique()),
        )
        med_search = f3.text_input("Medication search")

        if selected_priorities:
            order_view = order_view[order_view["priority"].isin(selected_priorities)]
        if selected_destinations:
            order_view = order_view[order_view["destination"].isin(selected_destinations)]
        if med_search:
            med_mask = (
                order_view["med_desc"].str.contains(med_search, case=False, na=False)
                | order_view["med_id"].str.contains(med_search, case=False, na=False)
            )
            order_view = order_view[med_mask]

        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            daily = order_view.groupby("order_date", dropna=False).agg(
                orders=("pk", "count"),
                quantity=("qty", "sum"),
            ).reset_index()
            st.plotly_chart(px.bar(daily, x="order_date", y="orders"), width="stretch")
        with chart_col2:
            top_meds = order_view.groupby(["med_id", "med_desc"], dropna=False).agg(
                orders=("pk", "count"),
                quantity=("qty", "sum"),
                last_received=("dt", "max"),
            ).reset_index().sort_values(["orders", "quantity"], ascending=[False, False]).head(20)
            top_meds["med_label"] = top_meds["med_desc"].fillna(top_meds["med_id"]).astype(str).str.slice(0, 55)
            st.plotly_chart(px.bar(top_meds.sort_values("orders"), x="orders", y="med_label", orientation="h"), width="stretch")

        st.markdown("##### Receiving/order rows")
        display_cols = ["dt", "queue_id", "priority", "med_id", "med_desc", "destination", "user_name", "qty"]
        st.dataframe(order_view[display_cols], width="stretch", hide_index=True)

        st.download_button(
            "Download filtered receiving/orders CSV",
            data=order_view[display_cols].to_csv(index=False).encode("utf-8"),
            file_name="daily_receiving_orders.csv",
            mime="text/csv",
        )


with tab_receive:
    st.subheader("Receiving and Return Putaway QC")
    st.caption("Use this as the simple audit trail for the person controlling meds entering the carousel.")

    with st.form("receiving_qc_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        log_date = c1.date_input("Date", value=date.today())
        receiver_name = c2.text_input("Receiver / inventory tech")
        batch_ref = c3.text_input("Tote, order, or batch reference")

        c4, c5, c6, c7 = st.columns(4)
        expiration_checked = c4.checkbox("Expiration check completed", value=True)
        short_dated_found = c5.checkbox("Short-dated items found")
        short_dated_count = c6.number_input("Short-dated count", min_value=0, step=1)
        returns_processed = c7.checkbox("Returns processed into carousel")

        shortest_date_tier = st.selectbox(
            "Shortest dating found",
            ["None found", "< 6 months", "< 3 months", "< 1 month - do not send to Pyxis"],
        )
        notes = st.text_area("Notes", placeholder="Example: two short-dated boxes flagged before putaway.")
        submitted = st.form_submit_button("Save receiving QC log")

    if submitted:
        with engine.begin() as conn:
            conn.execute(text(f"""
                INSERT INTO {RECEIVING_TABLE} (
                    log_date, receiver_name, batch_ref, expiration_checked, short_dated_found,
                    short_dated_count, shortest_date_tier, returns_processed, notes
                )
                VALUES (
                    :log_date, :receiver_name, :batch_ref, :expiration_checked, :short_dated_found,
                    :short_dated_count, :shortest_date_tier, :returns_processed, :notes
                )
            """), {
                "log_date": log_date,
                "receiver_name": receiver_name,
                "batch_ref": batch_ref,
                "expiration_checked": expiration_checked,
                "short_dated_found": short_dated_found,
                "short_dated_count": int(short_dated_count),
                "shortest_date_tier": shortest_date_tier,
                "returns_processed": returns_processed,
                "notes": notes,
            })
        clear_quality_caches()
        st.success("Receiving QC log saved.")
        st.rerun()

    receiving_log = load_receiving_log()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("QC Logs", f"{len(receiving_log):,}")
    m2.metric("Short-Dated Batches", f"{int(receiving_log['short_dated_found'].sum()) if not receiving_log.empty else 0:,}")
    m3.metric("Short-Dated Items", f"{int(receiving_log['short_dated_count'].sum()) if not receiving_log.empty else 0:,}")
    m4.metric("Return Putaway Checks", f"{int(receiving_log['returns_processed'].sum()) if not receiving_log.empty else 0:,}")
    if not receiving_log.empty:
        st.dataframe(receiving_log, width="stretch", hide_index=True)


with tab_cancel:
    st.subheader("Cancelled Transaction Analysis")
    cancels = build_cancel_analysis(events)
    reviewed = load_cancel_review_log()
    reviewed_pks = set(reviewed["source_pk"].dropna()) if not reviewed.empty else set()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cancelled Transactions", f"{len(cancels):,}")
    c2.metric("Users", f"{cancels['user_name'].nunique() if not cancels.empty else 0:,}")
    c3.metric("Meds", f"{cancels['med_id'].nunique() if not cancels.empty else 0:,}")
    c4.metric("Reviewed", f"{len(reviewed_pks):,}")

    if cancels.empty:
        st.info("No cancelled transactions found in the selected date range.")
    else:
        cancels["reviewed"] = cancels["pk"].isin(reviewed_pks)
        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            user_counts = cancels["user_name"].fillna("Unknown").value_counts().head(15).reset_index()
            user_counts.columns = ["User", "Cancels"]
            st.plotly_chart(px.bar(user_counts, x="Cancels", y="User", orientation="h"), width="stretch")
        with chart_col2:
            cat_counts = cancels["likely_category"].value_counts().reset_index()
            cat_counts.columns = ["Category", "Cancels"]
            st.plotly_chart(px.bar(cat_counts, x="Cancels", y="Category", orientation="h"), width="stretch")

        st.markdown("##### Review a cancelled transaction")
        cancel_options = cancels[["pk", "dt", "user_name", "device", "med_desc", "event_type", "likely_category"]].copy()
        cancel_options["label"] = cancel_options.apply(
            lambda r: f"{r['dt']} | {r['user_name']} | {r['device']} | {r['med_desc']} | {r['event_type']}",
            axis=1,
        )
        label_map = dict(zip(cancel_options["pk"], cancel_options["label"]))
        selected_cancel_pk = st.selectbox("Cancelled row", cancel_options["pk"], format_func=label_map.get)
        selected_cancel = cancels[cancels["pk"] == selected_cancel_pk].iloc[0]

        with st.form("cancel_review_form", clear_on_submit=True):
            cancel_category = st.selectbox(
                "Reason category",
                [
                    selected_cancel["likely_category"],
                    "Count mismatch",
                    "Wrong med/pocket",
                    "Not found in pocket",
                    "Expiration concern",
                    "Wrong patient/order selection",
                    "Workflow confusion",
                    "Normal correction",
                    "Unknown",
                ],
            )
            follow_up_needed = st.checkbox("Follow-up needed")
            cancel_notes = st.text_area("Review notes")
            save_cancel = st.form_submit_button("Save cancel review")

        if save_cancel:
            with engine.begin() as conn:
                conn.execute(text(f"""
                    INSERT INTO {CANCEL_REVIEW_TABLE} (
                        source_pk, cancel_dt, user_name, device, med_id, med_desc,
                        event_type, cancel_category, follow_up_needed, notes
                    )
                    VALUES (
                        :source_pk, :cancel_dt, :user_name, :device, :med_id, :med_desc,
                        :event_type, :cancel_category, :follow_up_needed, :notes
                    )
                    ON CONFLICT (source_pk) DO UPDATE SET
                        reviewed_at = CURRENT_TIMESTAMP,
                        cancel_category = EXCLUDED.cancel_category,
                        follow_up_needed = EXCLUDED.follow_up_needed,
                        notes = EXCLUDED.notes
                """), {
                    "source_pk": selected_cancel["pk"],
                    "cancel_dt": db_value(selected_cancel["dt"]),
                    "user_name": selected_cancel["user_name"],
                    "device": selected_cancel["device"],
                    "med_id": selected_cancel["med_id"],
                    "med_desc": selected_cancel["med_desc"],
                    "event_type": selected_cancel["event_type"],
                    "cancel_category": cancel_category,
                    "follow_up_needed": follow_up_needed,
                    "notes": cancel_notes,
                })
            clear_quality_caches()
            st.success("Cancel review saved.")
            st.rerun()

        st.dataframe(
            cancels[["reviewed", "dt", "user_name", "device", "med_id", "med_desc", "event_type", "qty", "likely_category"]],
            width="stretch",
            hide_index=True,
        )


with tab_lifecycle:
    st.subheader("Medication Lifecycle Risk")
    st.caption("This first model uses order/restock timestamps as the lifecycle start signal until exact manufacturer expiration dates are added.")

    risk = build_lifecycle_risk(events, orders, inventory)
    if risk.empty:
        st.info("No lifecycle data available for the selected date range.")
    else:
        level_filter = st.multiselect(
            "Risk level",
            ["Critical", "High", "Watch", "Low"],
            default=["Critical", "High", "Watch"],
        )
        filtered_risk = risk[risk["risk_level"].isin(level_filter)].copy() if level_filter else risk.copy()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Meds Scored", f"{len(risk):,}")
        c2.metric("Critical", f"{int((risk['risk_level'] == 'Critical').sum()):,}")
        c3.metric("High", f"{int((risk['risk_level'] == 'High').sum()):,}")
        c4.metric("With Current Stock", f"{int((risk['current_count'] > 0).sum()):,}")

        chart_df = filtered_risk.head(20).copy()
        chart_df["med_label"] = chart_df["med_desc"].fillna(chart_df["med_id"]).astype(str).str.slice(0, 55)
        st.plotly_chart(
            px.bar(chart_df, x="risk_score", y="med_label", color="risk_level", orientation="h"),
            width="stretch",
        )

        display_cols = [
            "risk_level", "risk_score", "med_id", "med_desc", "current_count", "stocked_locations",
            "days_since_first_received", "days_since_last_access", "pulls_per_30_days",
            "cancelled_events", "return_loop_events", "outdate_events", "first_received",
            "last_received", "last_pyxis_access", "pocket_examples",
        ]
        st.dataframe(filtered_risk[display_cols], width="stretch", hide_index=True)
        st.download_button(
            "Download lifecycle risk CSV",
            data=filtered_risk[display_cols].to_csv(index=False).encode("utf-8"),
            file_name="inventory_lifecycle_risk.csv",
            mime="text/csv",
        )
