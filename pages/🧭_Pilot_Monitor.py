from datetime import timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from App import load_data, render_sidebar, engine


def to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


@st.cache_data(ttl=3600)
def get_control_ids():
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT DISTINCT med_id FROM carousel_master_mapping WHERE carousel_location LIKE 'CW%'"
                )
            )
            return {row[0].strip().upper() for row in result if row[0]}
    except Exception:
        return set()


def classify_workflow(row):
    text_blob = " ".join([str(row.get("event_type", "")), str(row.get("priority", ""))]).lower()
    if "inventory" in text_blob:
        return "Inventory Move"
    if "instant" in text_blob and "return" in text_blob:
        return "Instant Return"
    if "instant" in text_blob and "restock" in text_blob:
        return "Instant Restock"
    if "restock" in text_blob:
        return "Restock"
    if "return" in text_blob:
        return "Return"
    return "Other"


def remove_dummy(df):
    if df.empty or "med_desc" not in df.columns:
        return df
    return df[~df["med_desc"].astype(str).str.contains("cassette", case=False, na=False)]


def remove_controls(df):
    if df.empty or "med_id" not in df.columns:
        return df
    control_ids = get_control_ids()
    if not control_ids:
        return df
    return df[~df["med_id"].astype(str).str.strip().str.upper().isin(control_ids)]


def ensure_date_column(df):
    if not df.empty and "date" not in df.columns:
        df["date"] = pd.to_datetime(df.get("dt"), errors="coerce").dt.date
    return df


def safe_group(df, qty_name):
    if df.empty or not {"med_id", "med_desc", "date", "qty"}.issubset(df.columns):
        return pd.DataFrame(columns=["med_id", "med_desc", "date", qty_name])
    return (
        df.groupby(["med_id", "med_desc", "date"])["qty"]
        .sum()
        .reset_index()
        .rename(columns={"qty": qty_name})
    )


def compute_reconciliation(df_events, df_pharm, selected_users=None, exclude_controls=False, exclude_dummy=True):
    pyxis_unload = pd.DataFrame()
    pharm_all = pd.DataFrame()
    unload_eject = pd.DataFrame()

    for df in [df_events, df_pharm]:
        if not df.empty and "dt" in df.columns:
            df["dt"] = pd.to_datetime(df["dt"], errors="coerce")

    if not df_events.empty and "event_type" in df_events.columns:
        pyxis_all_raw = df_events[
            df_events["event_type"].astype(str).str.contains("empty|unload|return bin", case=False, na=False)
            & ~df_events["event_type"].astype(str).str.contains("cancelled", case=False, na=False)
        ].copy()
        unload_eject = pyxis_all_raw[
            pyxis_all_raw["event_type"].astype(str).str.contains("eject", case=False, na=False)
        ].copy()
        pyxis_unload = pyxis_all_raw[
            ~pyxis_all_raw["event_type"].astype(str).str.contains("eject", case=False, na=False)
        ].copy()
        if "device" in pyxis_unload.columns:
            pyxis_unload = pyxis_unload[
                ~pyxis_unload["device"].astype(str).str.contains("cass|patient", case=False, na=False)
            ]

    if not df_pharm.empty:
        pharm_df = df_pharm.copy()
        event_col = "event_type" if "event_type" in pharm_df.columns else (
            "priority" if "priority" in pharm_df.columns else None
        )
        if event_col:
            pharm_all = pharm_df[
                pharm_df[event_col].astype(str).str.contains("return|restock|instant|inventory", case=False, na=False)
            ].copy()
            pharm_all["workflow_type"] = pharm_all.apply(classify_workflow, axis=1)

    excluded_types = {"Inventory Move", "Restock"}
    inv_moves = pharm_all[pharm_all["workflow_type"] == "Inventory Move"].copy() if not pharm_all.empty else pd.DataFrame()
    restocks = pharm_all[pharm_all["workflow_type"] == "Restock"].copy() if not pharm_all.empty else pd.DataFrame()
    pharm_return = pharm_all[~pharm_all["workflow_type"].isin(excluded_types)].copy() if not pharm_all.empty else pd.DataFrame()

    if selected_users:
        if not pyxis_unload.empty:
            pyxis_unload = pyxis_unload[pyxis_unload["user_name"].isin(selected_users)]
        if not pharm_return.empty:
            pharm_return = pharm_return[pharm_return["user_name"].isin(selected_users)]
        if not inv_moves.empty:
            inv_moves = inv_moves[inv_moves["user_name"].isin(selected_users)]
        if not restocks.empty:
            restocks = restocks[restocks["user_name"].isin(selected_users)]

    if exclude_dummy:
        pyxis_unload = remove_dummy(pyxis_unload)
        pharm_return = remove_dummy(pharm_return)

    if exclude_controls:
        pyxis_unload = remove_controls(pyxis_unload)
        pharm_return = remove_controls(pharm_return)

    pyxis_unload = ensure_date_column(pyxis_unload)
    pharm_return = ensure_date_column(pharm_return)
    inv_moves = ensure_date_column(inv_moves)
    restocks = ensure_date_column(restocks)
    unload_eject = ensure_date_column(unload_eject)

    pyxis_sum = safe_group(pyxis_unload, "qty_pyxis")
    pharm_sum = safe_group(pharm_return, "qty_pharm")

    recon = pd.merge(
        pyxis_sum.drop(columns=["med_desc"], errors="ignore"),
        pharm_sum.drop(columns=["med_desc"], errors="ignore"),
        on=["med_id", "date"],
        how="outer",
    )
    if recon.empty:
        recon = pd.DataFrame(columns=["med_id", "date", "qty_pyxis", "qty_pharm", "med_desc", "difference"])
    else:
        recon[["qty_pyxis", "qty_pharm"]] = recon[["qty_pyxis", "qty_pharm"]].fillna(0)
        med_lookup = pd.concat(
            [pyxis_sum[["med_id", "med_desc"]], pharm_sum[["med_id", "med_desc"]]]
        ).drop_duplicates("med_id")
        recon = recon.merge(med_lookup, on="med_id", how="left")
        recon["difference"] = recon["qty_pyxis"] - recon["qty_pharm"]

    total_unload = recon["qty_pyxis"].sum() if "qty_pyxis" in recon.columns else 0
    total_return = recon["qty_pharm"].sum() if "qty_pharm" in recon.columns else 0
    recon_pct = (min(total_unload, total_return) / total_unload * 100) if total_unload > 0 else 100.0
    unmatched = recon[recon["difference"] != 0].copy() if "difference" in recon.columns else pd.DataFrame()
    unmatched_abs_qty = unmatched["difference"].abs().sum() if not unmatched.empty else 0

    daily = recon.groupby("date", dropna=False).agg(
        qty_pyxis=("qty_pyxis", "sum"),
        qty_pharm=("qty_pharm", "sum"),
        unmatched_med_days=("med_id", lambda s: 0),
    ).reset_index() if not recon.empty else pd.DataFrame(columns=["date", "qty_pyxis", "qty_pharm", "unmatched_med_days"])

    if not daily.empty:
        daily = daily.drop(columns=["unmatched_med_days"])
        mismatch_daily = unmatched.groupby("date").size().reset_index(name="unmatched_med_days") if not unmatched.empty else pd.DataFrame(columns=["date", "unmatched_med_days"])
        daily = daily.merge(mismatch_daily, on="date", how="left")
        daily["unmatched_med_days"] = daily["unmatched_med_days"].fillna(0).astype(int)
        daily["recon_pct_day"] = 100.0
        nonzero_mask = daily["qty_pyxis"] > 0
        daily.loc[nonzero_mask, "recon_pct_day"] = (
            np.minimum(
                daily.loc[nonzero_mask, "qty_pyxis"],
                daily.loc[nonzero_mask, "qty_pharm"],
            )
            / daily.loc[nonzero_mask, "qty_pyxis"]
            * 100
        )
        daily["weekday"] = pd.to_datetime(daily["date"]).dt.day_name()

    med_summary = unmatched.groupby(["med_id", "med_desc"]).agg(
        unmatched_days=("date", "nunique"),
        net_difference=("difference", "sum"),
        abs_difference=("difference", lambda s: s.abs().sum()),
    ).reset_index() if not unmatched.empty else pd.DataFrame(columns=["med_id", "med_desc", "unmatched_days", "net_difference", "abs_difference"])

    metrics = {
        "total_unload": float(total_unload),
        "total_return": float(total_return),
        "recon_pct": float(recon_pct),
        "unmatched_med_days": int(len(unmatched)),
        "unmatched_abs_qty": float(unmatched_abs_qty),
        "avg_unmatched_per_day": float(daily["unmatched_med_days"].mean()) if not daily.empty else 0.0,
        "days_measured": int(daily["date"].nunique()) if not daily.empty else 0,
        "inv_move_qty": float(inv_moves["qty"].sum()) if not inv_moves.empty and "qty" in inv_moves.columns else 0.0,
        "restock_qty": float(restocks["qty"].sum()) if not restocks.empty and "qty" in restocks.columns else 0.0,
        "eject_qty": float(unload_eject["qty"].sum()) if not unload_eject.empty and "qty" in unload_eject.columns else 0.0,
    }

    return {
        "metrics": metrics,
        "recon": recon,
        "daily": daily,
        "unmatched": unmatched,
        "med_summary": med_summary.sort_values("abs_difference", ascending=False),
    }


def metric_delta(current, baseline, suffix=""):
    if baseline == 0:
        return "n/a"
    delta = ((current - baseline) / baseline) * 100
    return f"{delta:+.1f}%{suffix}"


st.set_page_config(page_title="Pilot Monitor", page_icon="🧭", layout="wide")

global_start, global_end = render_sidebar()

st.header("🧭 Pilot Monitor")
st.caption("Baseline vs pilot comparison for the 0500/0600 workflow redesign, with returns reconciliation as a stress signal.")

default_pilot_start = global_start
default_pilot_end = global_end
pilot_days = max((default_pilot_end - default_pilot_start).days + 1, 1)
default_baseline_end = default_pilot_start - timedelta(days=1)
default_baseline_start = default_baseline_end - timedelta(days=pilot_days - 1)

with st.sidebar:
    st.divider()
    st.subheader("Pilot Window")
    pilot_start = st.date_input("Pilot Start", value=default_pilot_start, key="pilot_start")
    pilot_end = st.date_input("Pilot End", value=default_pilot_end, key="pilot_end")

    st.subheader("Baseline Window")
    baseline_start = st.date_input("Baseline Start", value=default_baseline_start, key="baseline_start")
    baseline_end = st.date_input("Baseline End", value=default_baseline_end, key="baseline_end")

    compare_weekdays_only = st.checkbox("Compare Weekdays Only", value=True)
    exclude_controls = st.checkbox("Exclude Controlled Substances", key="pilot_excl_controls")
    exclude_dummy = st.checkbox("Exclude Dummy Medications", value=True, key="pilot_excl_dummy")

if pilot_start > pilot_end or baseline_start > baseline_end:
    st.error("Start dates must be on or before end dates.")
    st.stop()

with st.spinner("Loading baseline and pilot data..."):
    pilot_events, _, pilot_pharm, _, _ = load_data(pilot_start, pilot_end)
    base_events, _, base_pharm, _, _ = load_data(baseline_start, baseline_end)

if compare_weekdays_only:
    for df in [pilot_events, pilot_pharm, base_events, base_pharm]:
        if not df.empty and "dt" in df.columns:
            dt = pd.to_datetime(df["dt"], errors="coerce")
            keep = dt.dt.weekday < 5
            df.drop(df.index[~keep.fillna(False)], inplace=True)

all_users = sorted(
    list(
        set(
            list(pilot_events["user_name"].dropna().unique() if not pilot_events.empty else [])
            + list(pilot_pharm["user_name"].dropna().unique() if not pilot_pharm.empty else [])
            + list(base_events["user_name"].dropna().unique() if not base_events.empty else [])
            + list(base_pharm["user_name"].dropna().unique() if not base_pharm.empty else [])
        )
    )
)

with st.sidebar:
    selected_users = st.multiselect("Filter by User", options=all_users)

pilot = compute_reconciliation(
    pilot_events.copy(),
    pilot_pharm.copy(),
    selected_users=selected_users,
    exclude_controls=exclude_controls,
    exclude_dummy=exclude_dummy,
)
baseline = compute_reconciliation(
    base_events.copy(),
    base_pharm.copy(),
    selected_users=selected_users,
    exclude_controls=exclude_controls,
    exclude_dummy=exclude_dummy,
)

pilot_metrics = pilot["metrics"]
baseline_metrics = baseline["metrics"]

st.info(
    "Interpret this page as an operational monitor. If reconciliation worsens during the pilot, that suggests the new 0600 role may be absorbing too much returns workload."
)

col1, col2, col3, col4 = st.columns(4)
col1.metric(
    "Pilot Reconciliation %",
    f"{pilot_metrics['recon_pct']:.1f}%",
    delta=metric_delta(pilot_metrics["recon_pct"], baseline_metrics["recon_pct"]),
    delta_color="normal",
)
col2.metric(
    "Pilot Unmatched Med-Days",
    f"{pilot_metrics['unmatched_med_days']:,}",
    delta=metric_delta(pilot_metrics["unmatched_med_days"], baseline_metrics["unmatched_med_days"]),
    delta_color="inverse",
)
col3.metric(
    "Avg Unmatched / Day",
    f"{pilot_metrics['avg_unmatched_per_day']:.2f}",
    delta=metric_delta(pilot_metrics["avg_unmatched_per_day"], baseline_metrics["avg_unmatched_per_day"]),
    delta_color="inverse",
)
col4.metric(
    "Unmatched Qty Drift",
    f"{pilot_metrics['unmatched_abs_qty']:.0f}",
    delta=metric_delta(pilot_metrics["unmatched_abs_qty"], baseline_metrics["unmatched_abs_qty"]),
    delta_color="inverse",
)

summary = pd.DataFrame(
    [
        {
            "Window": "Baseline",
            "Start": baseline_start,
            "End": baseline_end,
            "Days Measured": baseline_metrics["days_measured"],
            "Unload Qty": baseline_metrics["total_unload"],
            "Return Qty": baseline_metrics["total_return"],
            "Reconciliation %": baseline_metrics["recon_pct"],
            "Unmatched Med-Days": baseline_metrics["unmatched_med_days"],
            "Avg Unmatched / Day": baseline_metrics["avg_unmatched_per_day"],
            "Excluded Inv Moves": baseline_metrics["inv_move_qty"],
            "Excluded Restocks": baseline_metrics["restock_qty"],
        },
        {
            "Window": "Pilot",
            "Start": pilot_start,
            "End": pilot_end,
            "Days Measured": pilot_metrics["days_measured"],
            "Unload Qty": pilot_metrics["total_unload"],
            "Return Qty": pilot_metrics["total_return"],
            "Reconciliation %": pilot_metrics["recon_pct"],
            "Unmatched Med-Days": pilot_metrics["unmatched_med_days"],
            "Avg Unmatched / Day": pilot_metrics["avg_unmatched_per_day"],
            "Excluded Inv Moves": pilot_metrics["inv_move_qty"],
            "Excluded Restocks": pilot_metrics["restock_qty"],
        },
    ]
)

st.dataframe(
    summary,
    width="stretch",
    hide_index=True,
    column_config={
        "Unload Qty": st.column_config.NumberColumn(format="%.0f"),
        "Return Qty": st.column_config.NumberColumn(format="%.0f"),
        "Reconciliation %": st.column_config.NumberColumn(format="%.1f"),
        "Unmatched Med-Days": st.column_config.NumberColumn(format="%d"),
        "Avg Unmatched / Day": st.column_config.NumberColumn(format="%.2f"),
        "Excluded Inv Moves": st.column_config.NumberColumn(format="%.0f"),
        "Excluded Restocks": st.column_config.NumberColumn(format="%.0f"),
    },
)

tab1, tab2, tab3 = st.tabs(["Daily Trend", "Worsening Meds", "Raw Variance"])

with tab1:
    st.subheader("Daily Reconciliation Trend")

    base_daily = baseline["daily"].copy()
    pilot_daily = pilot["daily"].copy()

    if base_daily.empty and pilot_daily.empty:
        st.warning("No daily reconciliation data available for the selected windows.")
    else:
        if not base_daily.empty:
            base_daily["window"] = "Baseline"
        if not pilot_daily.empty:
            pilot_daily["window"] = "Pilot"
        daily_compare = pd.concat([base_daily, pilot_daily], ignore_index=True)

        fig = px.line(
            daily_compare.sort_values("date"),
            x="date",
            y="unmatched_med_days",
            color="window",
            markers=True,
            title="Unmatched Med-Days by Day",
        )
        st.plotly_chart(fig, width="stretch")

        fig2 = px.line(
            daily_compare.sort_values("date"),
            x="date",
            y="recon_pct_day",
            color="window",
            markers=True,
            title="Daily Reconciliation %",
        )
        st.plotly_chart(fig2, width="stretch")

        weekday_compare = (
            daily_compare.groupby(["window", "weekday"])
            .agg(
                avg_recon_pct=("recon_pct_day", "mean"),
                avg_unmatched=("unmatched_med_days", "mean"),
            )
            .reset_index()
        )
        weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        weekday_compare["weekday"] = pd.Categorical(weekday_compare["weekday"], categories=weekday_order, ordered=True)
        weekday_compare = weekday_compare.sort_values("weekday")
        st.dataframe(
            weekday_compare,
            width="stretch",
            hide_index=True,
            column_config={
                "avg_recon_pct": st.column_config.NumberColumn("Avg Recon %", format="%.1f"),
                "avg_unmatched": st.column_config.NumberColumn("Avg Unmatched / Day", format="%.2f"),
            },
        )

with tab2:
    st.subheader("Top Medications Worsening in the Pilot")

    base_med = baseline["med_summary"].copy()
    pilot_med = pilot["med_summary"].copy()

    if base_med.empty and pilot_med.empty:
        st.warning("No med-level unmatched data found in either window.")
    else:
        base_med = base_med.rename(
            columns={
                "unmatched_days": "baseline_unmatched_days",
                "net_difference": "baseline_net_difference",
                "abs_difference": "baseline_abs_difference",
            }
        )
        pilot_med = pilot_med.rename(
            columns={
                "unmatched_days": "pilot_unmatched_days",
                "net_difference": "pilot_net_difference",
                "abs_difference": "pilot_abs_difference",
            }
        )
        med_delta = pd.merge(base_med, pilot_med, on=["med_id", "med_desc"], how="outer").fillna(0)
        med_delta["delta_unmatched_days"] = med_delta["pilot_unmatched_days"] - med_delta["baseline_unmatched_days"]
        med_delta["delta_abs_difference"] = med_delta["pilot_abs_difference"] - med_delta["baseline_abs_difference"]
        med_delta = med_delta.sort_values(
            ["delta_unmatched_days", "delta_abs_difference"], ascending=[False, False]
        )

        st.dataframe(
            med_delta.head(25),
            width="stretch",
            hide_index=True,
            column_config={
                "baseline_unmatched_days": st.column_config.NumberColumn("Baseline Days", format="%.0f"),
                "pilot_unmatched_days": st.column_config.NumberColumn("Pilot Days", format="%.0f"),
                "delta_unmatched_days": st.column_config.NumberColumn("Delta Days", format="%.0f"),
                "baseline_abs_difference": st.column_config.NumberColumn("Baseline Abs Drift", format="%.0f"),
                "pilot_abs_difference": st.column_config.NumberColumn("Pilot Abs Drift", format="%.0f"),
                "delta_abs_difference": st.column_config.NumberColumn("Delta Abs Drift", format="%.0f"),
            },
        )
        st.download_button(
            "Export Worsening Meds CSV",
            data=to_csv_bytes(med_delta),
            file_name="pilot_monitor_worsening_meds.csv",
            mime="text/csv",
        )

with tab3:
    st.subheader("Raw Variance Comparison")

    base_unmatched = baseline["unmatched"].copy()
    pilot_unmatched = pilot["unmatched"].copy()
    if not base_unmatched.empty:
        base_unmatched["window"] = "Baseline"
    if not pilot_unmatched.empty:
        pilot_unmatched["window"] = "Pilot"
    variance = pd.concat([base_unmatched, pilot_unmatched], ignore_index=True)

    if variance.empty:
        st.success("No unmatched variance found in either selected window.")
    else:
        variance = variance.sort_values("difference", key=lambda s: s.abs(), ascending=False)
        st.dataframe(variance, width="stretch", hide_index=True)
        st.download_button(
            "Export Raw Variance CSV",
            data=to_csv_bytes(variance),
            file_name="pilot_monitor_raw_variance.csv",
            mime="text/csv",
        )

st.divider()
st.markdown(
    """
    **How to read this**
    - Stable or improving reconciliation suggests the new 0600 role is absorbing returns safely.
    - Rising unmatched med-days suggests the new split may be pushing returns too late in the day.
    - The "Worsening Meds" tab helps separate broad process strain from a few noisy medications.
    """
)
