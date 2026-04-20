import math

import pandas as pd


def build_workload_plan(assumptions_df, demand_basis="avg", demand_multiplier=1.0):
    """Turn edited task assumptions into a scenario-level workload plan."""
    basis_map = {
        "avg": "Use Avg / Day",
        "busy": "Use Busy / Day",
    }
    units_col = basis_map.get(demand_basis, "Use Avg / Day")

    workload = assumptions_df.copy()
    workload["Scenario Units / Day"] = (
        pd.to_numeric(workload[units_col], errors="coerce").fillna(0) * float(demand_multiplier)
    )
    workload["Minutes / Unit"] = pd.to_numeric(workload["Minutes / Unit"], errors="coerce").fillna(0)
    workload["Scenario Workload Min"] = workload["Scenario Units / Day"] * workload["Minutes / Unit"]

    total_minutes = float(workload["Scenario Workload Min"].sum())
    workload["Workload Share %"] = (
        workload["Scenario Workload Min"] / total_minutes * 100 if total_minutes else 0
    )

    return workload[
        [
            "task_key",
            "Task",
            "Source",
            "Scenario Units / Day",
            "Minutes / Unit",
            "Scenario Workload Min",
            "Workload Share %",
        ]
    ].sort_values("Scenario Workload Min", ascending=False)


def summarize_shift_envelope(role_df, reserve_pct=0.0):
    """Aggregate role-level staffed capacity into a single shift envelope."""
    role_work = role_df.copy()
    role_work["available_min"] = pd.to_numeric(role_work["available_min"], errors="coerce").fillna(0)

    staffed_roles = int((role_work["available_min"] > 0).sum())
    nominal_capacity_min = float(role_work["available_min"].sum())
    reserve_pct = float(reserve_pct)
    reserve_pct = min(max(reserve_pct, 0.0), 0.5)
    effective_capacity_min = nominal_capacity_min * (1 - reserve_pct)

    return {
        "staffed_roles": staffed_roles,
        "nominal_capacity_min": nominal_capacity_min,
        "effective_capacity_min": effective_capacity_min,
        "reserve_pct": reserve_pct,
    }


def calculate_workload_summary(workload_df, role_df, reserve_pct=0.0):
    """Produce executive-friendly workload and capacity metrics for a scenario."""
    envelope = summarize_shift_envelope(role_df, reserve_pct=reserve_pct)
    total_workload_min = float(workload_df["Scenario Workload Min"].sum())
    effective_capacity_min = envelope["effective_capacity_min"]
    nominal_capacity_min = envelope["nominal_capacity_min"]

    utilization_pct = (total_workload_min / effective_capacity_min * 100) if effective_capacity_min else 0.0
    overload_min = max(total_workload_min - effective_capacity_min, 0.0)
    underload_min = max(effective_capacity_min - total_workload_min, 0.0)

    if utilization_pct >= 110:
        delay_risk = "Severe"
    elif utilization_pct >= 95:
        delay_risk = "High"
    elif utilization_pct >= 85:
        delay_risk = "Elevated"
    else:
        delay_risk = "Managed"

    avg_role_capacity = nominal_capacity_min / envelope["staffed_roles"] if envelope["staffed_roles"] else 0.0
    incremental_roles_needed = math.ceil(overload_min / avg_role_capacity) if overload_min > 0 and avg_role_capacity else 0

    return {
        "staffed_roles": envelope["staffed_roles"],
        "nominal_capacity_min": nominal_capacity_min,
        "effective_capacity_min": effective_capacity_min,
        "total_workload_min": total_workload_min,
        "utilization_pct": utilization_pct,
        "overload_min": overload_min,
        "underload_min": underload_min,
        "delay_risk": delay_risk,
        "incremental_roles_needed": incremental_roles_needed,
        "avg_role_capacity_min": avg_role_capacity,
        "reserve_pct": envelope["reserve_pct"],
    }


def build_recommendations(summary, capacity_df, workload_df, unassigned_minutes=0.0):
    """Transparent rule-based guidance for the current Phase 1 scenario."""
    recs = []

    if summary["overload_min"] > 0:
        if summary["incremental_roles_needed"] > 0:
            recs.append(
                f"Add {summary['incremental_roles_needed']} technician(s) or equivalent coverage: the modeled day is over capacity by {summary['overload_min']:.1f} minutes."
            )
        else:
            recs.append(
                f"Rebalance work or extend productive time: the modeled day is over capacity by {summary['overload_min']:.1f} minutes."
            )
    elif summary["utilization_pct"] < 70 and summary["underload_min"] > 0:
        recs.append(
            f"Capacity is materially underloaded by {summary['underload_min']:.1f} minutes; this window may support cross-coverage, training, or reduced staffing with low risk."
        )

    overloaded_roles = capacity_df[capacity_df["Busy Util %"] >= 95].sort_values("Busy Util %", ascending=False)
    if not overloaded_roles.empty:
        role_names = ", ".join(overloaded_roles["Role"].head(3).tolist())
        recs.append(
            f"Role bottleneck detected in {role_names}; these lanes are likely to drive backlog first on heavier days."
        )

    watch_roles = capacity_df[
        (capacity_df["Busy Util %"] >= 85) & (capacity_df["Busy Util %"] < 95)
    ].sort_values("Busy Util %", ascending=False)
    if recs == [] and not watch_roles.empty:
        role_names = ", ".join(watch_roles["Role"].head(3).tolist())
        recs.append(
            f"Watch {role_names}; they are still inside capacity but have limited interruption buffer on busy days."
        )

    if unassigned_minutes > 0:
        recs.append(
            f"{unassigned_minutes:.1f} average-day minutes are not assigned to any role, which means the scenario currently assumes some work is uncovered."
        )

    if not workload_df.empty:
        top_work = workload_df.sort_values("Scenario Workload Min", ascending=False).head(2)
        top_labels = ", ".join(top_work["Task"].tolist())
        recs.append(
            f"The biggest workload drivers in this scenario are {top_labels}; those are the best first targets for redesign or protected coverage."
        )

    if not recs:
        recs.append("This scenario stays inside modeled capacity with manageable utilization and no major role bottlenecks.")

    return pd.DataFrame({"Recommendation": recs})
