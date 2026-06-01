from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import text

import App


st.set_page_config(page_title="Pocket Investigation", page_icon="🕵️", layout="wide")
App.apply_global_styles()
App.render_sidebar()
engine = App.engine

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Pocket Investigation",
        "Reconstruct pocket, device, and medication history when an unexpected med is found in a Pyxis pocket.",
        kicker="Operations",
    )
else:
    st.header("Pocket Investigation")
    st.caption("Reconstruct pocket/device/medication history for mixed-med pocket events.")


def clean_text(value):
    return str(value or "").strip()


def clean_device(value):
    return clean_text(value).upper()


def med_search_pattern(value):
    value = clean_text(value)
    return f"%{value}%" if value else "%"


@st.cache_data(ttl=300)
def load_devices():
    queries = [
        "SELECT DISTINCT UPPER(TRIM(station_name)) AS device FROM audit_transaction_detail_rc WHERE station_name IS NOT NULL",
        "SELECT DISTINCT UPPER(TRIM(device)) AS device FROM device_inventory WHERE device IS NOT NULL",
        "SELECT DISTINCT UPPER(TRIM(device)) AS device FROM device_inventory_history WHERE device IS NOT NULL",
        "SELECT DISTINCT UPPER(TRIM(device)) AS device FROM events WHERE device IS NOT NULL",
    ]
    frames = []
    with engine.connect() as conn:
        for query in queries:
            try:
                frames.append(pd.read_sql(text(query), conn))
            except Exception:
                continue
    if not frames:
        return []
    devices = pd.concat(frames, ignore_index=True)["device"].dropna().astype(str).str.strip()
    return sorted({device for device in devices if device})


def read_sql_safe(query, params):
    try:
        with engine.connect() as conn:
            return pd.read_sql(text(query), conn, params=params)
    except Exception as exc:
        st.warning(f"Could not load one investigation source: {exc}")
        return pd.DataFrame()


def load_current_pocket(device, pocket):
    return read_sql_safe(
        """
        SELECT
            device,
            zone,
            pocket_location,
            med_id,
            med_desc,
            brand_name,
            status,
            current_quantity,
            min_qty,
            max_qty,
            standard_stock,
            days_unused,
            snapshot_dt
        FROM device_inventory
        WHERE UPPER(TRIM(device)) = :device
          AND (:pocket = '' OR UPPER(TRIM(COALESCE(pocket_location, ''))) = UPPER(TRIM(:pocket)))
        ORDER BY pocket_location, med_desc
        """,
        {"device": device, "pocket": pocket},
    )


def load_current_device_inventory(device):
    return load_current_pocket(device, "")


def load_pocket_snapshots(device, pocket, start_dt, end_dt):
    return read_sql_safe(
        """
        SELECT
            snapshot_date,
            device,
            zone,
            pocket_location,
            med_id,
            med_desc,
            brand_name,
            status,
            current_quantity,
            min_qty,
            max_qty,
            standard_stock,
            days_unused,
            snapshot_dt
        FROM device_inventory_history
        WHERE UPPER(TRIM(device)) = :device
          AND snapshot_date >= :start_date
          AND snapshot_date <= :end_date
          AND (:pocket = '' OR UPPER(TRIM(COALESCE(pocket_location, ''))) = UPPER(TRIM(:pocket)))
        ORDER BY snapshot_date DESC, pocket_location, med_desc
        """,
        {
            "device": device,
            "pocket": pocket,
            "start_date": start_dt.date(),
            "end_date": end_dt.date(),
        },
    )


def load_audit_pocket_events(device, pocket, start_dt, end_dt):
    return read_sql_safe(
        """
        SELECT
            pk,
            dt::timestamp AS dt,
            user_name,
            user_type,
            UPPER(TRIM(station_name)) AS device,
            drawer_subdrawer_pocket AS pocket,
            med_id,
            med_desc,
            transaction_type AS event_type,
            qty,
            beginning_qty,
            ending_qty,
            discrepancy_difference AS discrepancy_qty
        FROM audit_transaction_detail_rc
        WHERE dt::timestamp >= :start_dt
          AND dt::timestamp <= :end_dt
          AND UPPER(TRIM(station_name)) = :device
          AND (:pocket = '' OR UPPER(TRIM(COALESCE(drawer_subdrawer_pocket, ''))) = UPPER(TRIM(:pocket)))
        ORDER BY dt
        """,
        {"device": device, "pocket": pocket, "start_dt": start_dt, "end_dt": end_dt},
    )


def load_med_device_events(device, expected_med, unexpected_med, start_dt, end_dt):
    return read_sql_safe(
        """
        SELECT
            pk,
            dt::timestamp AS dt,
            user_name,
            UPPER(TRIM(device)) AS device,
            med_id,
            med_desc,
            event_type,
            qty,
            beginning_qty,
            ending_qty,
            discrepancy_qty
        FROM events
        WHERE dt::timestamp >= :start_dt
          AND dt::timestamp <= :end_dt
          AND UPPER(TRIM(device)) = :device
          AND (
                (:expected_med = '' AND :unexpected_med = '')
             OR (:expected_med <> '' AND med_desc ILIKE :expected_pattern)
             OR (:unexpected_med <> '' AND med_desc ILIKE :unexpected_pattern)
          )
        ORDER BY dt
        """,
        {
            "device": device,
            "expected_med": expected_med,
            "unexpected_med": unexpected_med,
            "expected_pattern": med_search_pattern(expected_med),
            "unexpected_pattern": med_search_pattern(unexpected_med),
            "start_dt": start_dt,
            "end_dt": end_dt,
        },
    )


def load_pharmacy_orders(device, expected_med, unexpected_med, start_dt, end_dt):
    return read_sql_safe(
        """
        SELECT
            pk,
            dt::timestamp AS dt,
            queue_id,
            priority,
            UPPER(TRIM(destination)) AS device,
            med_id,
            med_desc,
            user_name,
            qty
        FROM pharmacy_orders
        WHERE dt::timestamp >= :start_dt
          AND dt::timestamp <= :end_dt
          AND UPPER(TRIM(destination)) = :device
          AND (
                (:expected_med = '' AND :unexpected_med = '')
             OR (:expected_med <> '' AND med_desc ILIKE :expected_pattern)
             OR (:unexpected_med <> '' AND med_desc ILIKE :unexpected_pattern)
          )
        ORDER BY dt
        """,
        {
            "device": device,
            "expected_med": expected_med,
            "unexpected_med": unexpected_med,
            "expected_pattern": med_search_pattern(expected_med),
            "unexpected_pattern": med_search_pattern(unexpected_med),
            "start_dt": start_dt,
            "end_dt": end_dt,
        },
    )


def classify_event_type(value):
    text_value = clean_text(value).casefold()
    if any(term in text_value for term in ["unload", "empty", "outdate", "remove from station"]):
        return "Unload / remove"
    if any(term in text_value for term in ["load", "refill", "restock", "replenish"]):
        return "Load / refill"
    if any(term in text_value for term in ["verify", "count", "inventory"]):
        return "Count / verify"
    if any(term in text_value for term in ["vend", "remove", "dispense", "withdraw"]):
        return "Clinical use"
    return "Other"


def build_chain(audit_events, med_events, orders):
    frames = []
    if not audit_events.empty:
        audit = audit_events.copy()
        audit["source"] = "RC Audit Detail"
        audit["pocket"] = audit["pocket"].fillna("").astype(str)
        frames.append(
            audit[[
                "dt", "source", "user_name", "device", "pocket", "med_id", "med_desc",
                "event_type", "qty", "beginning_qty", "ending_qty", "discrepancy_qty",
            ]]
        )
    if not med_events.empty:
        events = med_events.copy()
        events["source"] = "Legacy Events"
        events["pocket"] = ""
        for col in ["beginning_qty", "ending_qty", "discrepancy_qty"]:
            if col not in events.columns:
                events[col] = None
        frames.append(
            events[[
                "dt", "source", "user_name", "device", "pocket", "med_id", "med_desc",
                "event_type", "qty", "beginning_qty", "ending_qty", "discrepancy_qty",
            ]]
        )
    if not orders.empty:
        order_rows = orders.copy()
        order_rows["source"] = "Pharmacy Orders"
        order_rows["pocket"] = ""
        order_rows["event_type"] = order_rows["priority"].fillna("").astype(str)
        order_rows["beginning_qty"] = None
        order_rows["ending_qty"] = None
        order_rows["discrepancy_qty"] = None
        frames.append(
            order_rows[[
                "dt", "source", "user_name", "device", "pocket", "med_id", "med_desc",
                "event_type", "qty", "beginning_qty", "ending_qty", "discrepancy_qty",
            ]]
        )
    if not frames:
        return pd.DataFrame()
    chain = pd.concat(frames, ignore_index=True)
    chain["dt"] = pd.to_datetime(chain["dt"], errors="coerce")
    chain["event_group"] = chain["event_type"].apply(classify_event_type)
    return chain.sort_values(["dt", "source"], na_position="last")


def build_same_session(audit_events, chain, minutes=20):
    if audit_events.empty or chain.empty:
        return pd.DataFrame()
    focus = audit_events.copy()
    focus["event_group"] = focus["event_type"].apply(classify_event_type)
    focus = focus[focus["event_group"].isin(["Load / refill", "Unload / remove", "Count / verify"])]
    rows = []
    for _, event in focus.iterrows():
        event_dt = pd.to_datetime(event.get("dt"), errors="coerce")
        user = clean_text(event.get("user_name"))
        if pd.isna(event_dt) or not user:
            continue
        nearby = chain[
            chain["user_name"].fillna("").astype(str).eq(user)
            & chain["dt"].between(event_dt - pd.Timedelta(minutes=minutes), event_dt + pd.Timedelta(minutes=minutes))
        ].copy()
        if nearby.empty:
            continue
        for _, row in nearby.iterrows():
            rows.append({
                "focus_dt": event_dt,
                "focus_event": event.get("event_type"),
                "user_name": user,
                "nearby_dt": row.get("dt"),
                "minutes_from_focus": (row.get("dt") - event_dt).total_seconds() / 60 if pd.notna(row.get("dt")) else None,
                "source": row.get("source"),
                "device": row.get("device"),
                "pocket": row.get("pocket"),
                "med_desc": row.get("med_desc"),
                "event_type": row.get("event_type"),
                "qty": row.get("qty"),
            })
    return pd.DataFrame(rows).sort_values(["focus_dt", "nearby_dt"]) if rows else pd.DataFrame()


def similar_iv_bag_inventory(current_pocket, expected_med, unexpected_med):
    if current_pocket.empty:
        return pd.DataFrame()
    bag_pattern = r"IVPB|INFUSION|ML|BAG|DEXTROSE|SODIUM CHLORIDE|NS\b"
    view = current_pocket.copy()
    view["med_desc"] = view["med_desc"].fillna("").astype(str)
    view = view[view["med_desc"].str.contains(bag_pattern, case=False, regex=True, na=False)].copy()
    if expected_med:
        view = view[~view["med_desc"].str.contains(expected_med, case=False, regex=False, na=False)]
    if unexpected_med:
        view["matches_unexpected"] = view["med_desc"].str.contains(unexpected_med, case=False, regex=False, na=False)
    else:
        view["matches_unexpected"] = False
    return view.sort_values(["matches_unexpected", "pocket_location", "med_desc"], ascending=[False, True, True])


def find_last_zero_event(chain, discovery_dt):
    if chain.empty or "ending_qty" not in chain.columns:
        return None
    chain_before = chain[chain["dt"].le(discovery_dt)].copy()
    if chain_before.empty:
        return None
    ending_qty = pd.to_numeric(chain_before["ending_qty"], errors="coerce")
    zero_rows = chain_before[ending_qty.eq(0)]
    if zero_rows.empty:
        return None
    return zero_rows.sort_values("dt").iloc[-1].to_dict()


def build_prior_meds(snapshots):
    if snapshots.empty:
        return pd.DataFrame()
    view = snapshots.copy()
    view["med_desc"] = view["med_desc"].fillna("").astype(str).str.strip()
    view = view[view["med_desc"].ne("")]
    if view.empty:
        return pd.DataFrame()
    return view.groupby(["pocket_location", "med_id", "med_desc"], dropna=False).agg(
        first_seen=("snapshot_date", "min"),
        last_seen=("snapshot_date", "max"),
        snapshot_days=("snapshot_date", "nunique"),
        max_current_qty=("current_quantity", "max"),
        max_days_unused=("days_unused", "max"),
    ).reset_index().sort_values(["last_seen", "snapshot_days"], ascending=[False, False])


def build_pocket_users(audit_events):
    if audit_events.empty:
        return pd.DataFrame()
    view = audit_events.copy()
    view["event_group"] = view["event_type"].apply(classify_event_type)
    return view.groupby(["user_name", "user_type"], dropna=False).agg(
        pocket_events=("pk", "count"),
        first_touch=("dt", "min"),
        last_touch=("dt", "max"),
        load_refill_events=("event_group", lambda s: int((s == "Load / refill").sum())),
        unload_events=("event_group", lambda s: int((s == "Unload / remove").sum())),
        count_events=("event_group", lambda s: int((s == "Count / verify").sum())),
    ).reset_index().sort_values(["pocket_events", "last_touch"], ascending=[False, False])


def build_evidence_flags(chain, snapshots, expected_med, unexpected_med, discovery_dt):
    flags = []
    chain_before = chain[chain["dt"].le(discovery_dt)].copy() if not chain.empty else pd.DataFrame()
    if not chain_before.empty:
        prior_zero = chain_before[
            pd.to_numeric(chain_before["ending_qty"], errors="coerce").fillna(999999).eq(0)
        ]
        if prior_zero.empty:
            flags.append({
                "signal": "No clear zero-ending count before discovery",
                "interpretation": "Supports old-med-left-behind if the unexpected med could have been in the pocket previously.",
                "weight": "Old med left behind",
            })
        else:
            flags.append({
                "signal": f"Last zero-ending event before discovery: {prior_zero['dt'].max():%m/%d/%y %H:%M}",
                "interpretation": "A documented zero makes a long-left-behind med less likely after that point.",
                "weight": "Recent load/intermix more likely after that zero",
            })
    else:
        flags.append({
            "signal": "No event history found for this pocket/device in the lookback",
            "interpretation": "Use current inventory and pharmacy order timing; upload RC audit detail if pocket-level history is needed.",
            "weight": "Insufficient event evidence",
        })

    if unexpected_med and not chain_before.empty:
        unexpected_rows = chain_before[
            chain_before["med_desc"].fillna("").astype(str).str.contains(unexpected_med, case=False, regex=False, na=False)
        ]
        if not unexpected_rows.empty:
            flags.append({
                "signal": f"Unexpected med appears in history {len(unexpected_rows):,} time(s)",
                "interpretation": "If those rows are tied to the same pocket or same loading session, intermix/loading error is plausible.",
                "weight": "Intermix/loading signal",
            })

    if unexpected_med and not snapshots.empty:
        snapshot_hits = snapshots[
            snapshots["med_desc"].fillna("").astype(str).str.contains(unexpected_med, case=False, regex=False, na=False)
        ]
        if not snapshot_hits.empty:
            flags.append({
                "signal": f"Unexpected med appears in device inventory snapshots {len(snapshot_hits):,} time(s)",
                "interpretation": "Prior/current pocket assignment may explain how the med remained in place.",
                "weight": "Old med or mapping signal",
            })

    if expected_med and unexpected_med and not chain_before.empty:
        expected_rows = chain_before[
            chain_before["med_desc"].fillna("").astype(str).str.contains(expected_med, case=False, regex=False, na=False)
        ]
        unexpected_rows = chain_before[
            chain_before["med_desc"].fillna("").astype(str).str.contains(unexpected_med, case=False, regex=False, na=False)
        ]
        if not expected_rows.empty and not unexpected_rows.empty:
            gap = abs((expected_rows["dt"].max() - unexpected_rows["dt"].max()).total_seconds()) / 3600
            if gap <= 24:
                flags.append({
                    "signal": "Expected and unexpected meds were handled within 24 hours of each other",
                    "interpretation": "Supports a possible same-delivery or same-restock intermix.",
                    "weight": "Intermix/loading signal",
                })

    return pd.DataFrame(flags)


devices = load_devices()
if not devices:
    st.warning("No device names were found in the available tables.")
    st.stop()

with st.expander("Investigation Setup", expanded=True):
    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        device_search = st.text_input("Filter device", value="", key="pocket_investigation_device_search").strip().upper()
        device_options = [device for device in devices if not device_search or device_search in device]
        selected_device = st.selectbox("Station / device", device_options or devices, key="pocket_investigation_device")
    with c2:
        discovery_date = st.date_input("Discovery date", value=date.today())
    with c3:
        discovery_time = st.time_input("Discovery time", value=time(8, 0))

    d1, d2, d3 = st.columns([1, 1, 1])
    with d1:
        pocket = st.text_input("Pocket / drawer / bin", value="", placeholder="Example: Drw 3 / SubDrw A / Pkt 2")
    with d2:
        lookback_days = st.number_input("Lookback days", min_value=1, max_value=180, value=45, step=1)
    with d3:
        session_minutes = st.number_input("Same-session window minutes", min_value=5, max_value=120, value=20, step=5)

    m1, m2 = st.columns(2)
    with m1:
        expected_med = st.text_input("Expected med", value="", placeholder="Dobutamine 250")
    with m2:
        unexpected_med = st.text_input("Unexpected med", value="", placeholder="Med found in same pocket")

discovery_dt = datetime.combine(discovery_date, discovery_time)
start_dt = discovery_dt - timedelta(days=int(lookback_days))
device = clean_device(selected_device)
pocket = clean_text(pocket)
expected_med = clean_text(expected_med)
unexpected_med = clean_text(unexpected_med)

st.caption(
    f"Investigating {device}"
    + (f" / {pocket}" if pocket else "")
    + f" from {start_dt:%m/%d/%y %H:%M} through {discovery_dt:%m/%d/%y %H:%M}."
)

current_pocket = load_current_pocket(device, pocket)
current_device_inventory = load_current_device_inventory(device)
snapshots = load_pocket_snapshots(device, pocket, start_dt, discovery_dt)
audit_events = load_audit_pocket_events(device, pocket, start_dt, discovery_dt)
med_events = load_med_device_events(device, expected_med, unexpected_med, start_dt, discovery_dt)
orders = load_pharmacy_orders(device, expected_med, unexpected_med, start_dt, discovery_dt)
chain = build_chain(audit_events, med_events, orders)
same_session = build_same_session(audit_events, chain, int(session_minutes))
similar_bags = similar_iv_bag_inventory(current_device_inventory, expected_med, unexpected_med)
flags = build_evidence_flags(chain, snapshots, expected_med, unexpected_med, discovery_dt)
last_zero = find_last_zero_event(chain, discovery_dt)
prior_meds = build_prior_meds(snapshots)
pocket_users = build_pocket_users(audit_events)

summary_cols = st.columns(6)
summary_cols[0].metric("Pocket Events", f"{len(audit_events):,}")
summary_cols[1].metric("Device/Med Events", f"{len(med_events):,}")
summary_cols[2].metric("Pharmacy Orders", f"{len(orders):,}")
summary_cols[3].metric("Inventory Snapshots", f"{len(snapshots):,}")
summary_cols[4].metric("Same-Session Rows", f"{len(same_session):,}")
summary_cols[5].metric(
    "Last Zero",
    f"{last_zero['dt']:%m/%d %H:%M}" if last_zero and pd.notna(last_zero.get("dt")) else "Not found",
)

tab_summary, tab_chain, tab_inventory, tab_same_session, tab_similar, tab_raw = st.tabs(
    [
        "Evidence Summary",
        "Timeline",
        "Pocket Inventory",
        "Same-Session Meds",
        "Similar IV Bags",
        "Raw Exports",
    ]
)

with tab_summary:
    st.subheader("Most Likely Cause Signals")
    if flags.empty:
        st.info("No evidence flags were generated. Add expected/unexpected med text or increase the lookback window.")
    else:
        st.dataframe(flags, width="stretch", hide_index=True)
    st.markdown("**Decision rule**")
    st.write(
        "If the pocket never clearly hit zero before discovery, old med left behind stays plausible. "
        "If the pocket did hit zero, focus on load/refill/order activity after that zero. "
        "If both meds were handled by the same user/session close together, investigate intermix during restock or delivery."
    )
    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**Prior meds assigned to this pocket**")
        if prior_meds.empty:
            st.info("No prior med assignment history found for this pocket in inventory snapshots.")
        else:
            st.dataframe(prior_meds.head(25), width="stretch", hide_index=True)
    with s2:
        st.markdown("**Users who touched this pocket**")
        if pocket_users.empty:
            st.info("No pocket-level user touches found in the lookback.")
        else:
            st.dataframe(
                pocket_users,
                width="stretch",
                hide_index=True,
                column_config={
                    "first_touch": st.column_config.DatetimeColumn("First Touch", format="MM/DD/YY HH:mm"),
                    "last_touch": st.column_config.DatetimeColumn("Last Touch", format="MM/DD/YY HH:mm"),
                },
            )

with tab_chain:
    st.subheader("Chain of Custody Timeline")
    if chain.empty:
        st.info("No chain-of-custody rows were found for this setup.")
    else:
        st.dataframe(
            chain,
            width="stretch",
            hide_index=True,
            column_config={
                "dt": st.column_config.DatetimeColumn("Date/Time", format="MM/DD/YY HH:mm:ss"),
                "event_group": st.column_config.TextColumn("Event Group"),
                "ending_qty": st.column_config.NumberColumn("Ending Qty", format="%.1f"),
                "beginning_qty": st.column_config.NumberColumn("Beginning Qty", format="%.1f"),
                "qty": st.column_config.NumberColumn("Qty", format="%.1f"),
            },
        )

with tab_inventory:
    st.subheader("Current Pocket / Device Inventory")
    if current_pocket.empty:
        st.info("No current inventory rows found for this device/pocket.")
    else:
        st.dataframe(current_pocket, width="stretch", hide_index=True)
    st.subheader("Historical Inventory Snapshots")
    if snapshots.empty:
        st.info("No historical inventory snapshots found for this device/pocket in the lookback.")
    else:
        st.dataframe(
            snapshots,
            width="stretch",
            hide_index=True,
            column_config={
                "snapshot_date": st.column_config.DateColumn("Snapshot Date"),
                "current_quantity": st.column_config.NumberColumn("Current Qty", format="%.1f"),
                "days_unused": st.column_config.NumberColumn("Days Unused", format="%.0f"),
            },
        )

with tab_same_session:
    st.subheader("Same-Session Meds Handled Nearby")
    st.caption("Rows touched by the same user within the configured minutes around pocket load/unload/count events.")
    if same_session.empty:
        st.info("No nearby same-user/session rows were found.")
    else:
        st.dataframe(
            same_session,
            width="stretch",
            hide_index=True,
            column_config={
                "focus_dt": st.column_config.DatetimeColumn("Focus Event", format="MM/DD/YY HH:mm:ss"),
                "nearby_dt": st.column_config.DatetimeColumn("Nearby Event", format="MM/DD/YY HH:mm:ss"),
                "minutes_from_focus": st.column_config.NumberColumn("Minutes From Focus", format="%.1f"),
            },
        )

with tab_similar:
    st.subheader("Similar IV Bag Meds Currently on Device")
    st.caption("Uses current device inventory to surface other bag/infusion-style meds on the same device.")
    if similar_bags.empty:
        st.info("No similar IV bag meds were found in current inventory for this device.")
    else:
        st.dataframe(similar_bags, width="stretch", hide_index=True)

with tab_raw:
    st.download_button(
        "Download chain timeline CSV",
        data=chain.to_csv(index=False).encode("utf-8") if not chain.empty else b"",
        file_name="pocket_investigation_chain.csv",
        mime="text/csv",
        disabled=chain.empty,
    )
    st.download_button(
        "Download same-session CSV",
        data=same_session.to_csv(index=False).encode("utf-8") if not same_session.empty else b"",
        file_name="pocket_investigation_same_session.csv",
        mime="text/csv",
        disabled=same_session.empty,
    )
