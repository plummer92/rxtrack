from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd
import streamlit as st

import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)


SPECIAL_UNITS = {
    "SJSEMS",
    "SJSER",
    "SJSEDTRIAG",
    "SJSBRONCH",
    "SJSEDSO",
    "SJSPEDISED",
    "SJSRADM",
    "SJSTRAUMA1",
    "SJSTRAUMA2",
    "SJSTRAUMA 3",
    "SJSTRAUMA3",
}

TEMPLATE_DEFAULTS = {
    "Current State": {
        "roles": [
            {"role": "0500 Tech", "start": "05:00", "end": "13:30", "available_min": 450},
            {"role": "0600 Tech", "start": "06:00", "end": "14:30", "available_min": 450},
            {"role": "Packager", "start": "07:00", "end": "15:30", "available_min": 450},
            {"role": "IV Room", "start": "07:00", "end": "15:30", "available_min": 450},
        ],
        "allocations": {
            "scheduled_pyxis_route": {"0500 Tech": 45, "0600 Tech": 55, "Packager": 0, "IV Room": 0},
            "special_unit_pyxis_route": {"0500 Tech": 0, "0600 Tech": 100, "Packager": 0, "IV Room": 0},
            "patient_priority_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 60, "IV Room": 0},
            "stockout_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 70, "IV Room": 0},
            "transfer_discharge_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 60, "IV Room": 0},
            "routine_patient_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 70, "IV Room": 0},
            "return_processing": {"0500 Tech": 50, "0600 Tech": 50, "Packager": 0, "IV Room": 0},
            "cartfill_priority_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 80, "IV Room": 0},
            "packaging_support": {"0500 Tech": 0, "0600 Tech": 10, "Packager": 90, "IV Room": 0},
            "code_cart_replenishment": {"0500 Tech": 0, "0600 Tech": 25, "Packager": 75, "IV Room": 0},
        },
    },
    "0500/0600 Redesign": {
        "roles": [
            {"role": "0500 Tech", "start": "05:00", "end": "13:30", "available_min": 450},
            {"role": "0600 Tech", "start": "06:00", "end": "14:30", "available_min": 450},
            {"role": "Packager", "start": "07:00", "end": "15:30", "available_min": 180},
            {"role": "IV Room", "start": "07:00", "end": "15:30", "available_min": 450},
        ],
        "allocations": {
            "scheduled_pyxis_route": {"0500 Tech": 100, "0600 Tech": 0, "Packager": 0, "IV Room": 0},
            "special_unit_pyxis_route": {"0500 Tech": 100, "0600 Tech": 0, "Packager": 0, "IV Room": 0},
            "patient_priority_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 100, "IV Room": 0},
            "stockout_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 100, "IV Room": 0},
            "transfer_discharge_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 100, "IV Room": 0},
            "routine_patient_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 100, "IV Room": 0},
            "return_processing": {"0500 Tech": 0, "0600 Tech": 100, "Packager": 0, "IV Room": 0},
            "cartfill_priority_delivery": {"0500 Tech": 0, "0600 Tech": 0, "Packager": 100, "IV Room": 0},
            "packaging_support": {"0500 Tech": 0, "0600 Tech": 100, "Packager": 0, "IV Room": 0},
            "code_cart_replenishment": {"0500 Tech": 0, "0600 Tech": 100, "Packager": 0, "IV Room": 0},
        },
    },
    "0700/1430 Delivery Lane": {
        "roles": [
            {"role": "0500 Tech", "start": "05:00", "end": "13:30", "available_min": 450},
            {"role": "0600 Tech", "start": "06:00", "end": "14:30", "available_min": 450},
            {"role": "0700 Delivery", "start": "07:00", "end": "15:30", "available_min": 450},
            {"role": "1430 Delivery", "start": "14:30", "end": "23:30", "available_min": 480},
            {"role": "IV Room", "start": "07:00", "end": "15:30", "available_min": 450},
        ],
        "allocations": {
            "scheduled_pyxis_route": {"0500 Tech": 50, "0600 Tech": 50, "0700 Delivery": 0, "1430 Delivery": 0, "IV Room": 0},
            "special_unit_pyxis_route": {"0500 Tech": 20, "0600 Tech": 80, "0700 Delivery": 0, "1430 Delivery": 0, "IV Room": 0},
            "patient_priority_delivery": {"0500 Tech": 0, "0600 Tech": 0, "0700 Delivery": 70, "1430 Delivery": 30, "IV Room": 0},
            "stockout_delivery": {"0500 Tech": 0, "0600 Tech": 0, "0700 Delivery": 70, "1430 Delivery": 30, "IV Room": 0},
            "transfer_discharge_delivery": {"0500 Tech": 0, "0600 Tech": 0, "0700 Delivery": 65, "1430 Delivery": 35, "IV Room": 0},
            "routine_patient_delivery": {"0500 Tech": 0, "0600 Tech": 0, "0700 Delivery": 70, "1430 Delivery": 30, "IV Room": 0},
            "return_processing": {"0500 Tech": 35, "0600 Tech": 65, "0700 Delivery": 0, "1430 Delivery": 0, "IV Room": 0},
            "cartfill_priority_delivery": {"0500 Tech": 0, "0600 Tech": 0, "0700 Delivery": 25, "1430 Delivery": 75, "IV Room": 0},
            "packaging_support": {"0500 Tech": 0, "0600 Tech": 0, "0700 Delivery": 10, "1430 Delivery": 20, "IV Room": 70},
            "code_cart_replenishment": {"0500 Tech": 0, "0600 Tech": 0, "0700 Delivery": 80, "1430 Delivery": 20, "IV Room": 0},
        },
    },
    "1430 Delivery + 2 Overnights": {
        "roles": [
            {"role": "1430 Delivery", "start": "14:30", "end": "23:30", "available_min": 480},
            {"role": "2100 Overnight A", "start": "21:00", "end": "07:30", "available_min": 570},
            {"role": "2100 Overnight B", "start": "21:00", "end": "07:30", "available_min": 570},
            {"role": "0500 Tech", "start": "05:00", "end": "13:30", "available_min": 450},
            {"role": "0600 Tech", "start": "06:00", "end": "14:30", "available_min": 450},
            {"role": "0700 Delivery", "start": "07:00", "end": "15:30", "available_min": 450},
            {"role": "1430 Pyxis", "start": "14:30", "end": "23:00", "available_min": 450},
        ],
        "allocations": {
            "scheduled_pyxis_route": {
                "1430 Delivery": 0,
                "2100 Overnight A": 0,
                "2100 Overnight B": 0,
                "0500 Tech": 35,
                "0600 Tech": 35,
                "0700 Delivery": 0,
                "1430 Pyxis": 30,
            },
            "special_unit_pyxis_route": {
                "1430 Delivery": 0,
                "2100 Overnight A": 0,
                "2100 Overnight B": 0,
                "0500 Tech": 20,
                "0600 Tech": 65,
                "0700 Delivery": 0,
                "1430 Pyxis": 15,
            },
            "patient_priority_delivery": {
                "1430 Delivery": 55,
                "2100 Overnight A": 10,
                "2100 Overnight B": 10,
                "0500 Tech": 0,
                "0600 Tech": 0,
                "0700 Delivery": 25,
                "1430 Pyxis": 0,
            },
            "stockout_delivery": {
                "1430 Delivery": 55,
                "2100 Overnight A": 10,
                "2100 Overnight B": 10,
                "0500 Tech": 0,
                "0600 Tech": 0,
                "0700 Delivery": 25,
                "1430 Pyxis": 0,
            },
            "transfer_discharge_delivery": {
                "1430 Delivery": 45,
                "2100 Overnight A": 5,
                "2100 Overnight B": 5,
                "0500 Tech": 0,
                "0600 Tech": 0,
                "0700 Delivery": 45,
                "1430 Pyxis": 0,
            },
            "routine_patient_delivery": {
                "1430 Delivery": 50,
                "2100 Overnight A": 10,
                "2100 Overnight B": 10,
                "0500 Tech": 0,
                "0600 Tech": 0,
                "0700 Delivery": 30,
                "1430 Pyxis": 0,
            },
            "return_processing": {
                "1430 Delivery": 0,
                "2100 Overnight A": 25,
                "2100 Overnight B": 25,
                "0500 Tech": 15,
                "0600 Tech": 20,
                "0700 Delivery": 0,
                "1430 Pyxis": 15,
            },
            "cartfill_priority_delivery": {
                "1430 Delivery": 45,
                "2100 Overnight A": 15,
                "2100 Overnight B": 15,
                "0500 Tech": 0,
                "0600 Tech": 0,
                "0700 Delivery": 25,
                "1430 Pyxis": 0,
            },
            "packaging_support": {
                "1430 Delivery": 0,
                "2100 Overnight A": 45,
                "2100 Overnight B": 45,
                "0500 Tech": 0,
                "0600 Tech": 0,
                "0700 Delivery": 0,
                "1430 Pyxis": 10,
            },
            "code_cart_replenishment": {
                "1430 Delivery": 45,
                "2100 Overnight A": 15,
                "2100 Overnight B": 15,
                "0500 Tech": 0,
                "0600 Tech": 0,
                "0700 Delivery": 25,
                "1430 Pyxis": 0,
            },
        },
    },
}

TASK_LABELS = {
    "scheduled_pyxis_route": "Scheduled Pyxis Route Work",
    "special_unit_pyxis_route": "Special-Unit Pyxis Route Work",
    "patient_priority_delivery": "Patient Priority Delivery",
    "stockout_delivery": "Stockout Delivery",
    "transfer_discharge_delivery": "Transfer / Discharge Delivery",
    "routine_patient_delivery": "Routine Patient Delivery",
    "return_processing": "Return Processing",
    "cartfill_priority_delivery": "Cartfill Priority Delivery",
    "packaging_support": "Packaging Support",
    "code_cart_replenishment": "Code Cart / Tray Replenishment",
}

TASK_DEFAULT_MINUTES = {
    "scheduled_pyxis_route": 1.8,
    "special_unit_pyxis_route": 2.3,
    "patient_priority_delivery": 6.0,
    "stockout_delivery": 5.5,
    "transfer_discharge_delivery": 5.5,
    "routine_patient_delivery": 4.0,
    "return_processing": 3.0,
    "cartfill_priority_delivery": 4.5,
    "packaging_support": 8.0,
    "code_cart_replenishment": 12.0,
}

TASK_DEFAULT_PEAK = {
    "scheduled_pyxis_route": 1.20,
    "special_unit_pyxis_route": 1.25,
    "patient_priority_delivery": 1.40,
    "stockout_delivery": 1.35,
    "transfer_discharge_delivery": 1.30,
    "routine_patient_delivery": 1.20,
    "return_processing": 1.15,
    "cartfill_priority_delivery": 1.35,
    "packaging_support": 1.10,
    "code_cart_replenishment": 1.15,
}

MANUAL_TASKS = {"packaging_support", "code_cart_replenishment"}


def to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


def parse_hhmm(value):
    hour, minute = [int(x) for x in str(value).split(":")]
    return time(hour=hour, minute=minute)


def minutes_in_shift(start_str, end_str):
    start_dt = datetime.combine(datetime.today(), parse_hhmm(start_str))
    end_dt = datetime.combine(datetime.today(), parse_hhmm(end_str))
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return (end_dt - start_dt).total_seconds() / 60


def within_shift(ts_series, start_str, end_str):
    if ts_series.empty:
        return pd.Series(dtype=bool)
    start_t = parse_hhmm(start_str)
    end_t = parse_hhmm(end_str)
    times = ts_series.dt.time
    if end_t > start_t:
        return (times >= start_t) & (times < end_t)
    return (times >= start_t) | (times < end_t)


def build_task_frames(df_events, df_pharm):
    task_frames = {}

    events = df_events.copy() if not df_events.empty else pd.DataFrame()
    pharm = df_pharm.copy() if not df_pharm.empty else pd.DataFrame()

    if not events.empty:
        events["dt"] = pd.to_datetime(events["dt"], errors="coerce")
        events = events.dropna(subset=["dt"]).copy()
        events["device_norm"] = events["device"].fillna("").astype(str).str.strip().str.upper()
        events["event_norm"] = events["event_type"].fillna("").astype(str).str.lower()

        return_mask = (
            events["event_norm"].str.contains("empty|unload|return bin", case=False, na=False)
            & ~events["event_norm"].str.contains("cancelled|eject", case=False, na=False)
        )
        special_mask = events["device_norm"].isin(SPECIAL_UNITS)
        active_mask = ~events["event_norm"].str.contains("verify", case=False, na=False)

        task_frames["special_unit_pyxis_route"] = events[special_mask & active_mask].copy()
        task_frames["scheduled_pyxis_route"] = events[~special_mask & ~return_mask & active_mask].copy()
        task_frames["return_processing_events"] = events[return_mask].copy()
    else:
        task_frames["special_unit_pyxis_route"] = pd.DataFrame(columns=["dt"])
        task_frames["scheduled_pyxis_route"] = pd.DataFrame(columns=["dt"])
        task_frames["return_processing_events"] = pd.DataFrame(columns=["dt"])

    if not pharm.empty:
        pharm["dt"] = pd.to_datetime(pharm["dt"], errors="coerce")
        pharm = pharm.dropna(subset=["dt"]).copy()
        pharm["priority_norm"] = pharm["priority"].fillna("").astype(str).str.upper()
        pharm["dest_norm"] = pharm["destination"].fillna("").astype(str).str.upper()

        patient_priority_mask = pharm["priority_norm"].str.contains("STAT|CRITICAL|URGENT|PRIORITY", case=False, na=False)
        stockout_mask = pharm["priority_norm"].str.contains(r"STOCK\s*OUT|STOCKOUT", case=False, na=False)
        transfer_mask = (
            pharm["priority_norm"].str.contains("TRANSFER|DISCHARGE", case=False, na=False)
            | pharm["dest_norm"].str.contains("TRANSFER|DISCHARGE", case=False, na=False)
        )
        cartfill_mask = (
            pharm["priority_norm"].str.contains("CARTFILL|BATCH", case=False, na=False)
            | pharm["dest_norm"].str.contains("CARTFILL|BATCH", case=False, na=False)
        )
        return_mask = pharm["priority_norm"].str.contains("RETURN|RESTOCK|INSTANT|INVENTORY", case=False, na=False)
        special_mask = pharm["dest_norm"].isin(SPECIAL_UNITS)

        task_frames["patient_priority_delivery"] = pharm[patient_priority_mask & ~stockout_mask & ~transfer_mask & ~cartfill_mask & ~special_mask].copy()
        task_frames["stockout_delivery"] = pharm[stockout_mask].copy()
        task_frames["transfer_discharge_delivery"] = pharm[transfer_mask].copy()
        task_frames["cartfill_priority_delivery"] = pharm[cartfill_mask].copy()

        remaining_mask = ~(patient_priority_mask | stockout_mask | transfer_mask | cartfill_mask | return_mask | special_mask)
        task_frames["routine_patient_delivery"] = pharm[remaining_mask].copy()
        task_frames["return_processing_pharm"] = pharm[return_mask].copy()
    else:
        task_frames["patient_priority_delivery"] = pd.DataFrame(columns=["dt"])
        task_frames["stockout_delivery"] = pd.DataFrame(columns=["dt"])
        task_frames["transfer_discharge_delivery"] = pd.DataFrame(columns=["dt"])
        task_frames["cartfill_priority_delivery"] = pd.DataFrame(columns=["dt"])
        task_frames["routine_patient_delivery"] = pd.DataFrame(columns=["dt"])
        task_frames["return_processing_pharm"] = pd.DataFrame(columns=["dt"])

    combined_return = pd.concat(
        [task_frames["return_processing_events"], task_frames["return_processing_pharm"]],
        ignore_index=True,
    )
    task_frames["return_processing"] = combined_return

    return task_frames


def observed_stats(task_df):
    if task_df.empty or "dt" not in task_df.columns:
        return {
            "days": 0,
            "avg_daily_count": 0.0,
            "p75_daily_count": 0.0,
            "avg_hourly_count": 0.0,
            "p90_hourly_count": 0.0,
        }

    work = task_df.copy()
    work["date"] = work["dt"].dt.date
    daily_counts = work.groupby("date").size()
    hourly_counts = work.groupby(work["dt"].dt.floor("h")).size()
    return {
        "days": int(daily_counts.size),
        "avg_daily_count": float(daily_counts.mean()) if not daily_counts.empty else 0.0,
        "p75_daily_count": float(daily_counts.quantile(0.75)) if not daily_counts.empty else 0.0,
        "avg_hourly_count": float(hourly_counts.mean()) if not hourly_counts.empty else 0.0,
        "p90_hourly_count": float(hourly_counts.quantile(0.90)) if not hourly_counts.empty else 0.0,
    }


def shift_hourly_p90(task_df, start_str, end_str):
    if task_df.empty or "dt" not in task_df.columns:
        return 0.0
    work = task_df.copy()
    work["dt"] = pd.to_datetime(work["dt"], errors="coerce")
    work = work.dropna(subset=["dt"])
    if work.empty:
        return 0.0
    mask = within_shift(work["dt"], start_str, end_str)
    work = work[mask].copy()
    if work.empty:
        return 0.0
    hourly_counts = work.groupby(work["dt"].dt.floor("h")).size()
    return float(hourly_counts.quantile(0.90)) if not hourly_counts.empty else 0.0


def build_assumptions(task_frames):
    rows = []
    for task_key in TASK_LABELS:
        source_key = task_key
        if task_key == "return_processing":
            source_key = "return_processing"
        stats = observed_stats(task_frames.get(source_key, pd.DataFrame(columns=["dt"])))
        rows.append(
            {
                "task_key": task_key,
                "Task": TASK_LABELS[task_key],
                "Observed Avg / Day": round(stats["avg_daily_count"], 2),
                "Observed P75 / Day": round(stats["p75_daily_count"], 2),
                "Observed P90 / Hr": round(stats["p90_hourly_count"], 2),
                "Use Avg / Day": round(stats["avg_daily_count"], 2) if task_key not in MANUAL_TASKS else 0.0,
                "Use Busy / Day": round(stats["p75_daily_count"], 2) if task_key not in MANUAL_TASKS else 0.0,
                "Minutes / Unit": TASK_DEFAULT_MINUTES[task_key],
                "Peak Hour Multiplier": TASK_DEFAULT_PEAK[task_key],
                "Source": "Manual" if task_key in MANUAL_TASKS else "Observed",
            }
        )
    return pd.DataFrame(rows)


def build_allocation_df(template_name):
    template = TEMPLATE_DEFAULTS[template_name]
    role_names = [r["role"] for r in template["roles"]]
    rows = []
    for task_key, label in TASK_LABELS.items():
        row = {"task_key": task_key, "Task": label}
        for role in role_names:
            row[role] = template["allocations"].get(task_key, {}).get(role, 0)
        rows.append(row)
    return pd.DataFrame(rows)


def build_role_df(template_name):
    return pd.DataFrame(TEMPLATE_DEFAULTS[template_name]["roles"])


def build_template_snapshot(template_name, task_frames):
    role_df = build_role_df(template_name)
    assumptions_df = build_assumptions(task_frames)
    allocation_df = build_allocation_df(template_name)
    capacity_df, _ = summarize_role_capacity(role_df, assumptions_df, allocation_df, task_frames)
    return capacity_df


def build_delta_df(baseline_df, scenario_df):
    merge_cols = ["Role", "Average Day Min", "Busy Day Min", "Average Util %", "Busy Util %", "Peak Hour Util %"]
    baseline = baseline_df[merge_cols].rename(
        columns={
            "Average Day Min": "Baseline Avg Min",
            "Busy Day Min": "Baseline Busy Min",
            "Average Util %": "Baseline Avg Util %",
            "Busy Util %": "Baseline Busy Util %",
            "Peak Hour Util %": "Baseline Peak Util %",
        }
    )
    scenario = scenario_df[merge_cols].rename(
        columns={
            "Average Day Min": "Scenario Avg Min",
            "Busy Day Min": "Scenario Busy Min",
            "Average Util %": "Scenario Avg Util %",
            "Busy Util %": "Scenario Busy Util %",
            "Peak Hour Util %": "Scenario Peak Util %",
        }
    )
    delta = baseline.merge(scenario, on="Role", how="outer").fillna(0)
    delta["Avg Min Delta"] = delta["Scenario Avg Min"] - delta["Baseline Avg Min"]
    delta["Busy Min Delta"] = delta["Scenario Busy Min"] - delta["Baseline Busy Min"]
    delta["Avg Util Delta %"] = delta["Scenario Avg Util %"] - delta["Baseline Avg Util %"]
    delta["Busy Util Delta %"] = delta["Scenario Busy Util %"] - delta["Baseline Busy Util %"]
    delta["Peak Util Delta %"] = delta["Scenario Peak Util %"] - delta["Baseline Peak Util %"]
    return delta


def summarize_role_capacity(role_df, assumptions_df, allocation_df, task_frames):
    assumption_lookup = assumptions_df.set_index("task_key").to_dict("index")
    allocation_lookup = allocation_df.set_index("task_key")

    rows = []
    role_names = role_df["role"].tolist()
    unassigned_minutes = 0.0

    for task_key in TASK_LABELS:
        assume = assumption_lookup[task_key]
        total_share = allocation_lookup.loc[task_key, role_names].sum() / 100.0
        if total_share < 1:
            unassigned_minutes += (
                (1 - total_share)
                * float(assume["Use Avg / Day"])
                * float(assume["Minutes / Unit"])
            )

    for _, role in role_df.iterrows():
        role_name = role["role"]
        available_min = float(role["available_min"])
        avg_minutes = 0.0
        busy_minutes = 0.0
        peak_hour_minutes = 0.0

        for task_key in TASK_LABELS:
            assume = assumption_lookup[task_key]
            share = float(allocation_lookup.at[task_key, role_name]) / 100.0 if role_name in allocation_lookup.columns else 0.0
            avg_task_minutes = float(assume["Use Avg / Day"]) * float(assume["Minutes / Unit"]) * share
            busy_task_minutes = float(assume["Use Busy / Day"]) * float(assume["Minutes / Unit"]) * share
            shift_p90 = shift_hourly_p90(task_frames.get(task_key, pd.DataFrame(columns=["dt"])), role["start"], role["end"])
            peak_task_minutes = (
                shift_p90
                * float(assume["Minutes / Unit"])
                * float(assume["Peak Hour Multiplier"])
                * share
            )

            avg_minutes += avg_task_minutes
            busy_minutes += busy_task_minutes
            peak_hour_minutes += peak_task_minutes

        rows.append(
            {
                "Role": role_name,
                "Shift": f"{role['start']}-{role['end']}",
                "Available Min": available_min,
                "Average Day Min": avg_minutes,
                "Busy Day Min": busy_minutes,
                "Peak Hour Min": peak_hour_minutes,
                "Average Util %": (avg_minutes / available_min * 100) if available_min else 0,
                "Busy Util %": (busy_minutes / available_min * 100) if available_min else 0,
                "Peak Hour Util %": (peak_hour_minutes / 60 * 100),
                "Status": capacity_status((busy_minutes / available_min * 100) if available_min else 0),
            }
        )

    return pd.DataFrame(rows), unassigned_minutes


def capacity_status(util_pct):
    if util_pct < 70:
        return "Green"
    if util_pct < 85:
        return "Yellow"
    if util_pct < 95:
        return "Orange"
    return "Red"


def build_role_task_breakdown(role_name, role_df, assumptions_df, allocation_df, task_frames):
    assumption_lookup = assumptions_df.set_index("task_key").to_dict("index")
    role_row = role_df[role_df["role"] == role_name].iloc[0]
    rows = []
    for _, alloc in allocation_df.iterrows():
        task_key = alloc["task_key"]
        share = float(alloc.get(role_name, 0)) / 100.0
        assume = assumption_lookup[task_key]
        shift_p90 = shift_hourly_p90(task_frames.get(task_key, pd.DataFrame(columns=["dt"])), role_row["start"], role_row["end"])
        rows.append(
            {
                "Task": alloc["Task"],
                "Share %": share * 100,
                "Average Day Min": float(assume["Use Avg / Day"]) * float(assume["Minutes / Unit"]) * share,
                "Busy Day Min": float(assume["Use Busy / Day"]) * float(assume["Minutes / Unit"]) * share,
                "Peak Hour Min": shift_p90 * float(assume["Minutes / Unit"]) * float(assume["Peak Hour Multiplier"]) * share,
            }
        )
    return pd.DataFrame(rows).sort_values("Busy Day Min", ascending=False)


st.set_page_config(page_title="Workload Capacity Simulator", page_icon="⚖️", layout="wide")

load_data = App.load_data
render_sidebar = App.render_sidebar

start_date, end_date = render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Workload Capacity Simulator",
        "Test historical workload against proposed staffing models so role design decisions stay inside the same modern RxTrack shell.",
        kicker="Core",
    )
    _debug_event("Capacity Simulator", "shared_intro_loaded")
    _debug_panel("Capacity Simulator", intro_mode="shared")
else:
    st.header("⚖️ Workload Capacity Simulator")
    st.caption("Test historical workload against proposed staffing models.")
    _debug_event("Capacity Simulator", "fallback_header_used")
    _debug_panel("Capacity Simulator", intro_mode="fallback")

with st.spinner("Loading historical workload signals..."):
    df_events, _, df_pharm, _, _ = load_data(start_date, end_date)

task_frames = build_task_frames(df_events, df_pharm)
baseline_capacity_df = build_template_snapshot("Current State", task_frames)

with st.sidebar:
    st.divider()
    template_name = st.selectbox(
        "Role Template",
        options=list(TEMPLATE_DEFAULTS.keys()),
        index=0,
        help="Start with a predefined staffing model, then edit role times and allocation shares below.",
    )

role_df = build_role_df(template_name)
assumptions_df = build_assumptions(task_frames)
allocation_df = build_allocation_df(template_name)

st.info(
    "Use the editors below to tune time assumptions and task shares. The simulator uses your selected date window as the historical sample."
)
st.caption(
    "Task lanes are split into `Pyxis route work` versus `patient/cartfill delivery work`. "
    "The starter templates assume `0500/0600` stay on Pyxis routes, while `0700/1430 Delivery` own patient-priority, stockout, transfer/discharge, and cartfill lanes."
)

if template_name == "1430 Delivery + 2 Overnights":
    st.caption(
        "Starting assumption for this template: `0700 Delivery` and `1430 Delivery` own the non-Pyxis lane, "
        "`2100 Overnight A/B` absorb most overnight packaging-style work plus part of returns, and the "
        "`0500/0600/1430 Pyxis` team stays focused on scheduled Pyxis-side work."
    )
elif template_name == "0500/0600 Redesign":
    st.caption(
        "Starting assumption for this template: `0500 Tech` absorbs all current `0600` delivery routes, "
        "`0600 Tech` shifts into returns and packaging/support, and `0500 Tech` no longer spends afternoon time returning meds to carousel."
    )

with st.expander("1. Role Setup", expanded=True):
    edited_roles = st.data_editor(
        role_df,
        width="stretch",
        hide_index=True,
        key="role_editor",
        column_config={
            "available_min": st.column_config.NumberColumn("Available Minutes", min_value=0, step=15),
        },
    )
    edited_roles["Shift Minutes"] = edited_roles.apply(lambda row: minutes_in_shift(row["start"], row["end"]), axis=1)
    st.caption("Available minutes should reflect productive time, not paid shift length. Subtract lunch and expected fixed downtime.")

with st.expander("2. Task Assumptions", expanded=True):
    edited_assumptions = st.data_editor(
        assumptions_df.drop(columns=["task_key"]),
        width="stretch",
        hide_index=True,
        key="assumption_editor",
        column_config={
            "Use Avg / Day": st.column_config.NumberColumn(min_value=0.0, step=0.5, format="%.2f"),
            "Use Busy / Day": st.column_config.NumberColumn(min_value=0.0, step=0.5, format="%.2f"),
            "Minutes / Unit": st.column_config.NumberColumn(min_value=0.0, step=0.25, format="%.2f"),
            "Peak Hour Multiplier": st.column_config.NumberColumn(min_value=0.0, step=0.05, format="%.2f"),
        },
    )
    edited_assumptions.insert(0, "task_key", assumptions_df["task_key"])
    st.caption("Observed columns come from your historical data. You can override the workload counts and minutes per unit for scenario testing.")

with st.expander("3. Task Allocation by Role", expanded=True):
    edited_allocations = st.data_editor(
        allocation_df.drop(columns=["task_key"]),
        width="stretch",
        hide_index=True,
        key="allocation_editor",
        column_config={
            col: st.column_config.NumberColumn(min_value=0, max_value=100, step=5, format="%d")
            for col in allocation_df.columns
            if col not in {"task_key", "Task"}
        },
    )
    edited_allocations.insert(0, "task_key", allocation_df["task_key"])
    share_checks = edited_allocations.drop(columns=["task_key", "Task"]).sum(axis=1)
    share_status = pd.DataFrame(
        {
            "Task": edited_allocations["Task"],
            "Total Assigned %": share_checks,
            "Unassigned %": 100 - share_checks,
        }
    )
    st.dataframe(
        share_status,
        width="stretch",
        hide_index=True,
        column_config={
            "Total Assigned %": st.column_config.NumberColumn(format="%.0f"),
            "Unassigned %": st.column_config.NumberColumn(format="%.0f"),
        },
    )
    st.caption("A task can be split across roles. If total assigned is under 100%, that portion of work is effectively unstaffed in the scenario.")

capacity_df, unassigned_minutes = summarize_role_capacity(edited_roles, edited_assumptions, edited_allocations, task_frames)
delta_df = build_delta_df(baseline_capacity_df, capacity_df)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Historical Days Loaded", f"{len(pd.date_range(start_date, end_date)):,}")
c2.metric("Roles Simulated", f"{len(edited_roles):,}")
c3.metric("Red Roles", int((capacity_df["Status"] == "Red").sum()))
c4.metric("Unassigned Avg-Day Minutes", f"{unassigned_minutes:.1f}")

st.subheader("Capacity Summary")
st.dataframe(
    capacity_df,
    width="stretch",
    hide_index=True,
    column_config={
        "Available Min": st.column_config.NumberColumn(format="%.0f"),
        "Average Day Min": st.column_config.NumberColumn(format="%.1f"),
        "Busy Day Min": st.column_config.NumberColumn(format="%.1f"),
        "Peak Hour Min": st.column_config.NumberColumn(format="%.1f"),
        "Average Util %": st.column_config.NumberColumn(format="%.1f"),
        "Busy Util %": st.column_config.NumberColumn(format="%.1f"),
        "Peak Hour Util %": st.column_config.NumberColumn(format="%.1f"),
    },
)

if template_name != "Current State":
    st.subheader("Current vs Proposed")
    st.caption(
        "This compares the selected scenario against the built-in `Current State` template so you can see exactly how many minutes move between roles."
    )
    st.dataframe(
        delta_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Baseline Avg Min": st.column_config.NumberColumn(format="%.1f"),
            "Scenario Avg Min": st.column_config.NumberColumn(format="%.1f"),
            "Avg Min Delta": st.column_config.NumberColumn(format="%+.1f"),
            "Baseline Busy Min": st.column_config.NumberColumn(format="%.1f"),
            "Scenario Busy Min": st.column_config.NumberColumn(format="%.1f"),
            "Busy Min Delta": st.column_config.NumberColumn(format="%+.1f"),
            "Baseline Avg Util %": st.column_config.NumberColumn(format="%.1f"),
            "Scenario Avg Util %": st.column_config.NumberColumn(format="%.1f"),
            "Avg Util Delta %": st.column_config.NumberColumn(format="%+.1f"),
            "Baseline Busy Util %": st.column_config.NumberColumn(format="%.1f"),
            "Scenario Busy Util %": st.column_config.NumberColumn(format="%.1f"),
            "Busy Util Delta %": st.column_config.NumberColumn(format="%+.1f"),
            "Baseline Peak Util %": st.column_config.NumberColumn(format="%.1f"),
            "Scenario Peak Util %": st.column_config.NumberColumn(format="%.1f"),
            "Peak Util Delta %": st.column_config.NumberColumn(format="%+.1f"),
        },
    )

    focus_roles = delta_df[delta_df["Role"].isin(["0500 Tech", "0600 Tech"])].copy()
    if not focus_roles.empty:
        st.caption(
            "For your current test, focus on `Avg Min Delta` and `Busy Min Delta` for `0500 Tech` and `0600 Tech`. "
            "A positive number means that role's day gets longer than it is today."
        )

tab1, tab2, tab3 = st.tabs(["Role Breakdown", "Observed Signals", "Exports"])

with tab1:
    selected_role = st.selectbox("Inspect role", options=capacity_df["Role"].tolist(), key="inspect_role")
    breakdown = build_role_task_breakdown(selected_role, edited_roles, edited_assumptions, edited_allocations, task_frames)
    st.dataframe(
        breakdown,
        width="stretch",
        hide_index=True,
        column_config={
            "Share %": st.column_config.NumberColumn(format="%.0f"),
            "Average Day Min": st.column_config.NumberColumn(format="%.1f"),
            "Busy Day Min": st.column_config.NumberColumn(format="%.1f"),
            "Peak Hour Min": st.column_config.NumberColumn(format="%.1f"),
        },
    )

    top_task = breakdown.iloc[0]["Task"] if not breakdown.empty else "None"
    st.caption(f"Largest busy-day workload driver for this role: {top_task}.")

with tab2:
    signal_rows = []
    for task_key, label in TASK_LABELS.items():
        stats = observed_stats(task_frames.get(task_key, pd.DataFrame(columns=["dt"])))
        signal_rows.append(
            {
                "Task": label,
                "Observed Avg / Day": stats["avg_daily_count"],
                "Observed P75 / Day": stats["p75_daily_count"],
                "Observed P90 / Hr": stats["p90_hourly_count"],
            }
        )
    st.dataframe(
        pd.DataFrame(signal_rows),
        width="stretch",
        hide_index=True,
        column_config={
            "Observed Avg / Day": st.column_config.NumberColumn(format="%.2f"),
            "Observed P75 / Day": st.column_config.NumberColumn(format="%.2f"),
            "Observed P90 / Hr": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption("These are the raw historical workload signals feeding the simulator. Manual-only tasks start at zero until you enter assumptions.")

with tab3:
    st.download_button(
        "Export Capacity Summary CSV",
        data=to_csv_bytes(capacity_df),
        file_name="workload_capacity_summary.csv",
        mime="text/csv",
    )
    st.download_button(
        "Export Task Assumptions CSV",
        data=to_csv_bytes(edited_assumptions),
        file_name="workload_capacity_assumptions.csv",
        mime="text/csv",
    )
    st.download_button(
        "Export Task Allocation CSV",
        data=to_csv_bytes(edited_allocations),
        file_name="workload_capacity_allocation.csv",
        mime="text/csv",
    )

st.divider()
st.markdown(
    """
    **How to use this**
    - `Average Util %` tells you how a normal day fits.
    - `Busy Util %` tells you how a heavier day fits.
    - `Peak Hour Util %` tells you whether work bunches into stressful hours even if the full shift looks acceptable.
    - For planning, aim for most roles to stay below roughly `85%` busy-day utilization so there is room for interruptions and real-life pharmacy noise.
    """
)

