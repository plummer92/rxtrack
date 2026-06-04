import pandas as pd
import streamlit as st
from sqlalchemy import text

import App
from rxtrack_shared import add_return_compare_qty


st.set_page_config(page_title="Unload Review", page_icon="📤", layout="wide")
App.apply_global_styles()
start_date, end_date = App.render_sidebar()
engine = App.engine

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Unload Review",
        "Review Pyxis unloads against daily inventory snapshots, days unused, active orders, and care-area mapping.",
        kicker="Operations",
    )
else:
    st.header("Unload Review")
    st.caption("Review unloads against days unused, active orders, and care-area mapping.")


@st.cache_data(ttl=300)
def load_unloads(start, end):
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)
    try:
        with engine.connect() as conn:
            df = pd.read_sql(
                text(
                    """
                    WITH audit_days AS (
                        SELECT DISTINCT dt::date AS d, UPPER(TRIM(station_name)) AS device
                        FROM audit_transaction_detail_rc
                        WHERE dt::timestamp >= :start_ts
                          AND dt::timestamp < :end_ts
                    ),
                    audit_unloads AS (
                        SELECT
                            pk,
                            dt::timestamp AS dt,
                            dt::date AS date,
                            user_name,
                            UPPER(TRIM(station_name)) AS device,
                            care_area_name,
                            location,
                            drawer_subdrawer_pocket,
                            source_filename,
                            transaction_type AS event_type,
                            med_id,
                            med_desc,
                            qty,
                            beginning_qty,
                            ending_qty,
                            discrepancy_difference AS discrepancy_qty,
                            (
                                transaction_type ILIKE '%eject%'
                                OR EXISTS (
                                    SELECT 1
                                    FROM audit_transaction_detail_rc ej
                                    WHERE ej.transaction_type ILIKE '%eject%'
                                      AND ej.dt::timestamp >= a.dt::timestamp - INTERVAL '2 minutes'
                                      AND ej.dt::timestamp <= a.dt::timestamp + INTERVAL '2 minutes'
                                      AND UPPER(TRIM(ej.station_name)) = UPPER(TRIM(a.station_name))
                                      AND UPPER(TRIM(ej.med_id)) = UPPER(TRIM(a.med_id))
                                      AND COALESCE(NULLIF(TRIM(ej.user_name), ''), '') = COALESCE(NULLIF(TRIM(a.user_name), ''), '')
                                )
                            ) AS cubie_ejected
                        FROM audit_transaction_detail_rc
                        a
                        WHERE dt::timestamp >= :start_ts
                          AND dt::timestamp < :end_ts
                          AND transaction_type ILIKE '%unload%'
                          AND transaction_type NOT ILIKE '%cancel%'
                    ),
                    legacy_unloads AS (
                        SELECT
                            e.pk,
                            e.dt::timestamp AS dt,
                            e.dt::date AS date,
                            e.user_name,
                            UPPER(TRIM(e.device)) AS device,
                            NULL::text AS care_area_name,
                            NULL::text AS location,
                            NULL::text AS drawer_subdrawer_pocket,
                            NULL::text AS source_filename,
                            e.event_type,
                            e.med_id,
                            e.med_desc,
                            e.qty,
                            e.beginning_qty,
                            e.ending_qty,
                            e.discrepancy_qty,
                            e.event_type ILIKE '%eject%' AS cubie_ejected
                        FROM events e
                        WHERE e.dt::timestamp >= :start_ts
                          AND e.dt::timestamp < :end_ts
                          AND e.event_type ILIKE '%unload%'
                          AND e.event_type NOT ILIKE '%cancel%'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM audit_days ad
                              WHERE ad.d = e.dt::date
                                AND ad.device = UPPER(TRIM(e.device))
                          )
                    )
                    SELECT * FROM audit_unloads
                    UNION ALL
                    SELECT * FROM legacy_unloads
                    ORDER BY dt DESC
                    """
                ),
                conn,
                params={"start_ts": start_ts, "end_ts": end_ts},
            )
    except Exception as exc:
        st.error(f"Could not load unload rows: {exc}")
        return pd.DataFrame()
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    for col in ["qty", "beginning_qty", "ending_qty", "discrepancy_qty"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in [
        "user_name", "device", "care_area_name", "location", "drawer_subdrawer_pocket",
        "source_filename", "event_type", "med_id", "med_desc"
    ]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["cubie_ejected"] = df["cubie_ejected"].fillna(False).astype(bool)
    return df


@st.cache_data(ttl=300)
def load_inventory_context(start, end):
    try:
        with engine.connect() as conn:
            return pd.read_sql(
                text(
                    """
                    SELECT
                        UPPER(TRIM(device)) AS device,
                        UPPER(TRIM(med_id)) AS med_id,
                        STRING_AGG(DISTINCT NULLIF(TRIM(pocket_location), ''), ', ') AS pocket_locations,
                        MAX(days_unused) AS max_days_unused,
                        STRING_AGG(DISTINCT NULLIF(TRIM(active_orders), ''), ', ') AS active_orders,
                        STRING_AGG(DISTINCT NULLIF(TRIM(outdate_tracking), ''), ', ') AS outdate_tracking,
                        STRING_AGG(DISTINCT NULLIF(TRIM(standard_stock), ''), ', ') AS standard_stock
                    FROM (
                        SELECT device, med_id, pocket_location, days_unused, active_orders, outdate_tracking, standard_stock
                        FROM device_inventory
                        WHERE med_id IS NOT NULL
                        UNION ALL
                        SELECT device, med_id, pocket_location, days_unused, active_orders, outdate_tracking, standard_stock
                        FROM device_inventory_history
                        WHERE snapshot_date BETWEEN :start_date AND :end_date
                          AND med_id IS NOT NULL
                    ) inv
                    WHERE device IS NOT NULL
                    GROUP BY UPPER(TRIM(device)), UPPER(TRIM(med_id))
                    """
                ),
                conn,
                params={"start_date": start, "end_date": end},
            )
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_inventory_timeline(start, end):
    timeline_start = (pd.Timestamp(start) - pd.Timedelta(days=14)).date()
    try:
        with engine.connect() as conn:
            df = pd.read_sql(
                text(
                    """
                    SELECT
                        snapshot_date::date AS snapshot_date,
                        COALESCE(snapshot_dt, snapshot_date::timestamp) AS snapshot_ts,
                        UPPER(TRIM(device)) AS device,
                        UPPER(TRIM(med_id)) AS med_id,
                        STRING_AGG(DISTINCT NULLIF(TRIM(pocket_location), ''), ', ') AS pocket_locations,
                        MAX(days_unused) AS days_unused,
                        STRING_AGG(DISTINCT NULLIF(TRIM(active_orders), ''), ', ') AS active_orders,
                        STRING_AGG(DISTINCT NULLIF(TRIM(outdate_tracking), ''), ', ') AS outdate_tracking,
                        STRING_AGG(DISTINCT NULLIF(TRIM(standard_stock), ''), ', ') AS standard_stock
                    FROM device_inventory_history
                    WHERE snapshot_date BETWEEN :timeline_start AND :end_date
                      AND device IS NOT NULL
                      AND med_id IS NOT NULL
                    GROUP BY snapshot_date::date, COALESCE(snapshot_dt, snapshot_date::timestamp), UPPER(TRIM(device)), UPPER(TRIM(med_id))
                    UNION ALL
                    SELECT
                        COALESCE(snapshot_dt::date, CURRENT_DATE) AS snapshot_date,
                        COALESCE(snapshot_dt, CURRENT_TIMESTAMP) AS snapshot_ts,
                        UPPER(TRIM(device)) AS device,
                        UPPER(TRIM(med_id)) AS med_id,
                        STRING_AGG(DISTINCT NULLIF(TRIM(pocket_location), ''), ', ') AS pocket_locations,
                        MAX(days_unused) AS days_unused,
                        STRING_AGG(DISTINCT NULLIF(TRIM(active_orders), ''), ', ') AS active_orders,
                        STRING_AGG(DISTINCT NULLIF(TRIM(outdate_tracking), ''), ', ') AS outdate_tracking,
                        STRING_AGG(DISTINCT NULLIF(TRIM(standard_stock), ''), ', ') AS standard_stock
                    FROM device_inventory
                    WHERE device IS NOT NULL
                      AND med_id IS NOT NULL
                    GROUP BY COALESCE(snapshot_dt::date, CURRENT_DATE), COALESCE(snapshot_dt, CURRENT_TIMESTAMP), UPPER(TRIM(device)), UPPER(TRIM(med_id))
                    ORDER BY snapshot_ts
                    """
                ),
                conn,
                params={"timeline_start": timeline_start, "end_date": end},
            )
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], errors="coerce").dt.date
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], errors="coerce", utc=True).dt.tz_convert(None)
    df["days_unused"] = pd.to_numeric(df["days_unused"], errors="coerce")
    return df.dropna(subset=["snapshot_date", "snapshot_ts"])


@st.cache_data(ttl=300)
def load_device_care_area_map(start, end):
    lookback_start = (pd.Timestamp(start) - pd.Timedelta(days=45)).to_pydatetime()
    try:
        with engine.connect() as conn:
            df = pd.read_sql(
                text(
                    """
                    SELECT
                        UPPER(TRIM(station_name)) AS device,
                        NULLIF(TRIM(care_area_name), '') AS care_area_name,
                        COUNT(*) AS audit_rows,
                        MAX(dt::timestamp) AS last_seen
                    FROM audit_transaction_detail_rc
                    WHERE dt::timestamp >= :lookback_start
                      AND dt::timestamp < :end_ts
                      AND station_name IS NOT NULL
                      AND care_area_name IS NOT NULL
                      AND TRIM(care_area_name) <> ''
                    GROUP BY UPPER(TRIM(station_name)), NULLIF(TRIM(care_area_name), '')
                    ORDER BY device, audit_rows DESC, last_seen DESC
                    """
                ),
                conn,
                params={"lookback_start": lookback_start, "end_ts": pd.Timestamp(end) + pd.Timedelta(days=1)},
            )
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    df["last_seen"] = pd.to_datetime(df["last_seen"], errors="coerce")
    ranked = df.sort_values(["device", "audit_rows", "last_seen"], ascending=[True, False, False]).copy()
    primary = ranked.groupby("device", as_index=False).first().rename(
        columns={
            "care_area_name": "primary_care_area",
            "audit_rows": "primary_care_area_rows",
            "last_seen": "primary_care_area_last_seen",
        }
    )
    counts = ranked.groupby("device", as_index=False).agg(
        care_area_count=("care_area_name", "nunique"),
        care_area_options=("care_area_name", lambda s: ", ".join(s.dropna().astype(str).head(5))),
    )
    return primary.merge(counts, on="device", how="left")


def has_active_order_value(value):
    text_value = str(value or "").strip()
    if not text_value or text_value.lower() in {"0", "0.0", "false", "n", "no", "none", "nan"}:
        return False
    if text_value.lower() in {"1", "1.0", "true", "y", "yes"}:
        return True
    numeric_parts = pd.Series([text_value]).str.extractall(r"(-?\d+(?:\.\d+)?)")[0]
    if not numeric_parts.empty:
        numbers = pd.to_numeric(numeric_parts, errors="coerce").dropna()
        if not numbers.empty:
            return bool(numbers.gt(0).any())
    return True


def attach_inventory_timeline(unloads, inventory_timeline):
    if unloads.empty or inventory_timeline.empty:
        return unloads
    timeline = inventory_timeline.copy()
    timeline["_device_key"] = timeline["device"].fillna("").astype(str).str.strip().str.upper()
    timeline["_med_key"] = timeline["med_id"].fillna("").astype(str).str.strip().str.upper()
    timeline = timeline.sort_values(["_device_key", "_med_key", "snapshot_ts"])
    lookup = {
        key: group.reset_index(drop=True)
        for key, group in timeline.groupby(["_device_key", "_med_key"], dropna=False)
    }

    rows = []
    for _, row in unloads.iterrows():
        out = row.to_dict()
        unload_dt = pd.to_datetime(row.get("dt"), errors="coerce")
        if pd.isna(unload_dt):
            rows.append(out)
            continue
        if getattr(unload_dt, "tzinfo", None) is not None:
            unload_dt = unload_dt.tz_convert(None)
        key = (
            str(row.get("device") or "").strip().upper(),
            str(row.get("med_id") or "").strip().upper(),
        )
        snapshots = lookup.get(key)
        if snapshots is None or snapshots.empty:
            rows.append(out)
            continue
        eligible = snapshots[snapshots["snapshot_date"].le(unload_dt.date())]
        if eligible.empty:
            rows.append(out)
            continue
        current = eligible.iloc[-1]
        prior = eligible[eligible["snapshot_ts"].lt(current["snapshot_ts"])].tail(1)
        prior_row = prior.iloc[0] if not prior.empty else None
        active_orders = current.get("active_orders")
        prior_active_orders = prior_row.get("active_orders") if prior_row is not None else ""
        active_now = has_active_order_value(active_orders)
        active_prior = has_active_order_value(prior_active_orders)
        out.update({
            "inventory_snapshot_ts": current.get("snapshot_ts"),
            "prior_inventory_snapshot_ts": prior_row.get("snapshot_ts") if prior_row is not None else pd.NaT,
            "days_unused_from_snapshot": current.get("days_unused"),
            "active_orders_from_snapshot": active_orders,
            "prior_active_orders": prior_active_orders,
            "active_orders_present": active_now,
            "active_orders_went_away": active_prior and not active_now,
            "pocket_locations_from_snapshot": current.get("pocket_locations"),
            "outdate_tracking_from_snapshot": current.get("outdate_tracking"),
            "standard_stock_from_snapshot": current.get("standard_stock"),
        })
        rows.append(out)
    return pd.DataFrame(rows)


def enrich_unloads(unloads, inventory_context, inventory_timeline, care_area_map):
    if unloads.empty:
        return unloads
    out = add_return_compare_qty(unloads, source="pyxis")
    out = attach_inventory_timeline(out, inventory_timeline)
    if not inventory_context.empty:
        left = out.copy()
        left["_device_key"] = left["device"].fillna("").astype(str).str.strip().str.upper()
        left["_med_key"] = left["med_id"].fillna("").astype(str).str.strip().str.upper()
        right = inventory_context.copy()
        right["_device_key"] = right["device"].fillna("").astype(str).str.strip().str.upper()
        right["_med_key"] = right["med_id"].fillna("").astype(str).str.strip().str.upper()
        out = left.merge(
            right.drop(columns=["device", "med_id"], errors="ignore"),
            on=["_device_key", "_med_key"],
            how="left",
        ).drop(columns=["_device_key", "_med_key"], errors="ignore")
    if not care_area_map.empty:
        left = out.copy()
        left["_device_key"] = left["device"].fillna("").astype(str).str.strip().str.upper()
        right = care_area_map.copy()
        right["_device_key"] = right["device"].fillna("").astype(str).str.strip().str.upper()
        out = left.merge(right.drop(columns=["device"], errors="ignore"), on="_device_key", how="left").drop(
            columns=["_device_key"],
            errors="ignore",
        )

    out["hour"] = pd.to_datetime(out["dt"], errors="coerce").dt.hour
    if "cubie_ejected" not in out.columns:
        out["cubie_ejected"] = out["event_type"].fillna("").astype(str).str.contains("eject", case=False, na=False)
    else:
        out["cubie_ejected"] = out["cubie_ejected"].fillna(False).astype(bool)
    out["unload_bucket"] = "Other unload"
    event_text = out["event_type"].fillna("").astype(str)
    out.loc[out["cubie_ejected"], "unload_bucket"] = "Cubie ejected"
    out.loc[event_text.str.contains("outdate|expire|28", case=False, regex=True, na=False), "unload_bucket"] = (
        "Outdate / expiration signal"
    )
    out.loc[out["cubie_ejected"], "unload_bucket"] = "Cubie ejected"
    if "days_unused_from_snapshot" in out.columns or "max_days_unused" in out.columns:
        days_unused = pd.to_numeric(
            out.get("days_unused_from_snapshot", out.get("max_days_unused")),
            errors="coerce",
        )
        out.loc[days_unused.ge(28), "unload_bucket"] = "28+ days unused signal"
        out.loc[out["cubie_ejected"], "unload_bucket"] = "Cubie ejected"
        if "active_orders_present" in out.columns:
            has_active = out["active_orders_present"].fillna(False).astype(bool)
            went_away = out.get("active_orders_went_away", pd.Series(False, index=out.index)).fillna(False).astype(bool)
            out.loc[days_unused.ge(28) & has_active, "unload_bucket"] = "28+ unused but active orders remained"
            out.loc[days_unused.ge(28) & ~has_active & went_away, "unload_bucket"] = (
                "28+ unused, orders cleared since prior snapshot"
            )
            out.loc[days_unused.ge(28) & ~has_active & ~went_away, "unload_bucket"] = (
                "28+ unused, no active orders"
            )
            out.loc[out["cubie_ejected"], "unload_bucket"] = "Cubie ejected"
    return out


with st.spinner("Loading unload review..."):
    unloads = load_unloads(start_date, end_date)
    inventory_context = load_inventory_context(start_date, end_date)
    inventory_timeline = load_inventory_timeline(start_date, end_date)
    care_area_map = load_device_care_area_map(start_date, end_date)
    unloads = enrich_unloads(unloads, inventory_context, inventory_timeline, care_area_map)

if unloads.empty:
    st.info("No Pyxis unload rows were found for the selected date range.")
    st.stop()

users = sorted(unloads["user_name"].replace("", pd.NA).dropna().unique())
devices = sorted(unloads["device"].replace("", pd.NA).dropna().unique())
bucket_options = sorted(unloads["unload_bucket"].replace("", pd.NA).dropna().unique())

f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.4, 1.4])
selected_user = f1.selectbox("User", ["All Users"] + users, key="unload_review_user")
selected_devices = f2.multiselect("Device", devices, key="unload_review_devices")
selected_buckets = f3.multiselect("Unload bucket", bucket_options, key="unload_review_buckets")
search = f4.text_input("Med search", key="unload_review_search")

view = unloads.copy()
if selected_user != "All Users":
    view = view[view["user_name"].eq(selected_user)]
if selected_devices:
    view = view[view["device"].isin(selected_devices)]
if selected_buckets:
    view = view[view["unload_bucket"].isin(selected_buckets)]
if search:
    view = view[
        view["med_id"].str.contains(search, case=False, na=False)
        | view["med_desc"].str.contains(search, case=False, na=False)
    ]

total_qty = pd.to_numeric(view["qty"], errors="coerce").abs().sum()
unique_meds = view["med_id"].replace("", pd.NA).nunique()
unique_devices = view["device"].replace("", pd.NA).nunique()
ejected_rows = int(view.get("cubie_ejected", pd.Series(False, index=view.index)).fillna(False).astype(bool).sum())
signals_28 = int(view["unload_bucket"].astype(str).str.contains(r"28\+.*unused", regex=True, na=False).sum())
active_orders_remained = int(view["unload_bucket"].eq("28+ unused but active orders remained").sum())
care_area_conflicts = int(
    view.loc[
        pd.to_numeric(view.get("care_area_count", pd.Series(0, index=view.index)), errors="coerce").fillna(0).gt(1),
        "device",
    ].nunique()
) if "device" in view.columns else 0

m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
m1.metric("Unload Rows", f"{len(view):,}")
m2.metric("Unload Qty", f"{total_qty:,.0f}")
m3.metric("Unique Meds", f"{unique_meds:,}")
m4.metric("Devices", f"{unique_devices:,}")
m5.metric("28+ Signals", f"{signals_28:,}")
m6.metric("28+ w/ Active Orders", f"{active_orders_remained:,}")
m7.metric("Cubie Ejected", f"{ejected_rows:,}")

if care_area_conflicts:
    st.warning(f"{care_area_conflicts} selected device(s) have more than one care area in recent RC history.")

tab_summary, tab_devices, tab_meds, tab_detail = st.tabs(["Summary", "By Device", "By Med", "Unload Detail"])

with tab_summary:
    by_bucket = view.groupby("unload_bucket", dropna=False).agg(
        unload_rows=("pk", "count"),
        unload_qty=("qty", "sum"),
        devices=("device", "nunique"),
        unique_meds=("med_id", "nunique"),
        cubie_ejected=("cubie_ejected", "sum"),
    ).reset_index().sort_values("unload_rows", ascending=False)
    st.dataframe(
        by_bucket,
        width="stretch",
        hide_index=True,
        column_config={"cubie_ejected": st.column_config.NumberColumn("Cubie Ejected", format="%d")},
    )

with tab_devices:
    group_cols = ["device"]
    if "primary_care_area" in view.columns:
        group_cols.append("primary_care_area")
    by_device = view.groupby(group_cols, dropna=False).agg(
        unload_rows=("pk", "count"),
        unload_qty=("qty", "sum"),
        unique_meds=("med_id", "nunique"),
        cubie_ejected=("cubie_ejected", "sum"),
        first_unload=("dt", "min"),
        last_unload=("dt", "max"),
    ).reset_index().sort_values(["unload_rows", "unload_qty"], ascending=[False, False])
    if "care_area_options" in view.columns:
        by_device = by_device.merge(
            view[["device", "care_area_options", "care_area_count"]].drop_duplicates("device"),
            on="device",
            how="left",
        )
    st.dataframe(
        by_device,
        width="stretch",
        hide_index=True,
        column_config={
            "first_unload": st.column_config.DatetimeColumn("First", format="HH:mm"),
            "last_unload": st.column_config.DatetimeColumn("Last", format="HH:mm"),
            "cubie_ejected": st.column_config.NumberColumn("Cubie Ejected", format="%d"),
        },
    )

with tab_meds:
    med_aggs = {
        "unload_rows": ("pk", "count"),
        "unload_qty": ("qty", "sum"),
        "devices": ("device", "nunique"),
        "cubie_ejected": ("cubie_ejected", "sum"),
    }
    if "days_unused_from_snapshot" in view.columns:
        med_aggs["max_days_unused_from_snapshot"] = ("days_unused_from_snapshot", "max")
    if "active_orders_from_snapshot" in view.columns:
        med_aggs["active_orders_from_snapshot"] = (
            "active_orders_from_snapshot",
            lambda s: ", ".join(sorted({str(v) for v in s.dropna() if str(v).strip()}))[:300],
        )
    if "pocket_locations_from_snapshot" in view.columns:
        med_aggs["pocket_locations_from_snapshot"] = (
            "pocket_locations_from_snapshot",
            lambda s: ", ".join(sorted({str(v) for v in s.dropna() if str(v).strip()}))[:300],
        )
    by_med = (
        view.groupby(["med_id", "med_desc"], dropna=False)
        .agg(**med_aggs)
        .reset_index()
        .sort_values(["unload_rows", "devices", "unload_qty"], ascending=[False, False, False])
    )
    st.dataframe(by_med, width="stretch", hide_index=True)

with tab_detail:
    detail_cols = [
        c for c in [
            "dt", "date", "user_name", "device", "care_area_name", "primary_care_area", "location",
            "event_type", "cubie_ejected", "drawer_subdrawer_pocket", "med_id", "med_desc",
            "qty", "return_unit_note", "compare_qty",
            "beginning_qty", "ending_qty", "inventory_snapshot_ts", "days_unused_from_snapshot",
            "active_orders_from_snapshot", "prior_inventory_snapshot_ts", "prior_active_orders",
            "active_orders_went_away", "max_days_unused", "active_orders", "pocket_locations",
            "outdate_tracking", "standard_stock", "unload_bucket", "source_filename",
        ]
        if c in view.columns
    ]
    st.dataframe(
        view[detail_cols].sort_values("dt", ascending=False),
        width="stretch",
        hide_index=True,
        column_config={
            "dt": st.column_config.DatetimeColumn("Unload Time", format="MM/DD/YY HH:mm:ss"),
            "cubie_ejected": st.column_config.CheckboxColumn("Cubie Ejected"),
            "drawer_subdrawer_pocket": "Transaction Pocket",
            "inventory_snapshot_ts": st.column_config.DatetimeColumn("Matched Inventory Upload", format="MM/DD/YY HH:mm"),
            "prior_inventory_snapshot_ts": st.column_config.DatetimeColumn("Prior Inventory Upload", format="MM/DD/YY HH:mm"),
            "days_unused_from_snapshot": st.column_config.NumberColumn("Days Unused from Snapshot", format="%.0f"),
            "max_days_unused": st.column_config.NumberColumn("Max Days Unused in Window", format="%.0f"),
            "qty": st.column_config.NumberColumn("Qty", format="%.0f"),
            "compare_qty": st.column_config.NumberColumn("Compare Qty", format="%.2f"),
            "beginning_qty": st.column_config.NumberColumn("Beginning Qty", format="%.0f"),
            "ending_qty": st.column_config.NumberColumn("Ending Qty", format="%.0f"),
            "source_filename": "Source File",
        },
    )
    st.download_button(
        "Download unload review CSV",
        data=view[detail_cols].to_csv(index=False).encode("utf-8"),
        file_name="unload_review.csv",
        mime="text/csv",
    )
