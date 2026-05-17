import streamlit as st
import pandas as pd
import plotly.express as px
import App

st.set_page_config(page_title="Shift Audit Monitor", page_icon="📈", layout="wide")

render_sidebar = App.render_sidebar
seconds_to_mmss = App.seconds_to_mmss


def flatten_work_type_metrics(rows):
    metrics = {}
    for row in rows or []:
        work_type = row.get("work_type")
        if not work_type:
            continue
        metrics[work_type] = {
            "sessions": float(row.get("sessions", 0) or 0),
            "active_sec": float(row.get("active_sec", 0) or 0),
            "walk_sec": float(row.get("walk_sec", 0) or 0),
        }
    return metrics


def flag_profile_days(df_profile):
    if df_profile.empty:
        return df_profile

    flagged = df_profile.copy()
    active_median = max(flagged["active_sec"].median(), 1)
    walk_median = max(flagged["walk_sec"].median(), 1)
    gap_median = flagged["long_gap_count"].median()
    dominant_mode = flagged["dominant_work_type"].mode().iloc[0] if flagged["dominant_work_type"].notna().any() else ""

    statuses = []
    reasons = []
    for row in flagged.itertuples(index=False):
        issues = []
        severity = "Normal"

        if row.active_sec > active_median * 1.2:
            issues.append("Active time ran high")
        elif row.active_sec < active_median * 0.8:
            issues.append("Active time ran low")

        if row.walk_sec > max(walk_median * 1.35, walk_median + 900):
            issues.append("Walk time spiked")

        if row.long_gap_count >= max(gap_median + 2, 2):
            issues.append("Too many >20m gaps")

        if row.training_count > 0:
            issues.append("Training on shift")

        if dominant_mode and row.dominant_work_type and row.dominant_work_type != dominant_mode:
            issues.append(f"Dominant work changed to {row.dominant_work_type}")

        if len(issues) >= 2:
            severity = "Investigate"
        elif len(issues) == 1:
            severity = "Watch"

        statuses.append(severity)
        reasons.append("; ".join(issues) if issues else "Within expected range")

    flagged["status"] = statuses
    flagged["reason"] = reasons
    return flagged


start_date, end_date = render_sidebar()
App.require_management_access("Shift Audit Monitor")
if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Shift Audit Monitor",
        "Run saved 0500 and 0600 audit profiles across a date range, store the daily results, flag unusual days, and test reassignment ideas.",
        kicker="Performance",
    )
else:
    st.header("📈 Shift Audit Monitor")
    st.caption("Run saved shift audits across time and compare reassignment scenarios.")

profiles = App.load_shift_audit_profiles("shift_work_map")
if profiles.empty:
    st.warning("No saved shift audit profiles found yet. Save your 0500 and 0600 profiles in Session Explorer first.")
    st.stop()

profile_names = profiles["profile_name"].tolist()
default_start = pd.to_datetime(start_date).date() if start_date else pd.Timestamp.today().date() - pd.Timedelta(days=14)
default_end = pd.to_datetime(end_date).date() if end_date else pd.Timestamp.today().date()

f1, f2, f3 = st.columns([1, 1, 2])
with f1:
    monitor_start = st.date_input("Audit Start", value=default_start, key="audit_monitor_start")
with f2:
    monitor_end = st.date_input("Audit End", value=default_end, key="audit_monitor_end")
with f3:
    selected_profiles = st.multiselect(
        "Audit Profiles",
        options=profile_names,
        default=profile_names,
        key="audit_monitor_profiles",
    )

if not selected_profiles:
    st.info("Select at least one saved audit profile to continue.")
    st.stop()

if monitor_start > monitor_end:
    st.error("Audit start date must be on or before the end date.")
    st.stop()

selected_profile_rows = profiles[profiles["profile_name"].isin(selected_profiles)].copy()
date_range = pd.date_range(monitor_start, monitor_end, freq="D")

with st.spinner("Running saved audit profiles across the selected date range..."):
    for audit_dt in date_range:
        date_value = audit_dt.date()
        for profile in selected_profile_rows.itertuples(index=False):
            result = App.run_shift_audit_profile_for_date(
                date_value,
                shifts=profile.shifts,
                selected_names=profile.selected_names,
                view_scope=profile.view_scope or "Whole Shift Team",
            )
            if result:
                App.save_shift_audit_result(profile.profile_name, result)
    st.cache_data.clear()
    audit_results = App.load_shift_audit_results(monitor_start, monitor_end, selected_profiles)

if audit_results.empty:
    st.warning("No audit results were produced for the selected date range.")
    st.stop()

flagged_chunks = []
for profile_name in selected_profiles:
    profile_slice = audit_results[audit_results["profile_name"] == profile_name].copy()
    if profile_slice.empty:
        continue
    flagged_chunks.append(flag_profile_days(profile_slice))

if not flagged_chunks:
    st.warning("No usable audit rows were available after processing the selected profiles.")
    st.stop()

flagged_results = pd.concat(flagged_chunks, ignore_index=True)

summary = (
    flagged_results.groupby("profile_name", as_index=False)
    .agg(
        days=("audit_date", "count"),
        avg_active_sec=("active_sec", "mean"),
        avg_walk_sec=("walk_sec", "mean"),
        investigate_days=("status", lambda s: int((s == "Investigate").sum())),
        watch_days=("status", lambda s: int((s == "Watch").sum())),
    )
    .sort_values("profile_name")
)
summary["avg_active_time"] = summary["avg_active_sec"].apply(seconds_to_mmss)
summary["avg_walk_time"] = summary["avg_walk_sec"].apply(seconds_to_mmss)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Profile Days Stored", f"{len(flagged_results):,}")
k2.metric("Profiles in View", f"{flagged_results['profile_name'].nunique():,}")
k3.metric("Investigate Flags", f"{int((flagged_results['status'] == 'Investigate').sum()):,}")
k4.metric("Watch Flags", f"{int((flagged_results['status'] == 'Watch').sum()):,}")

st.divider()
st.subheader("Saved Audit Monitor")
st.dataframe(
    summary[["profile_name", "days", "avg_active_time", "avg_walk_time", "watch_days", "investigate_days"]].rename(columns={
        "profile_name": "Profile",
        "days": "Days",
        "avg_active_time": "Avg Active Time",
        "avg_walk_time": "Avg Walk Time",
        "watch_days": "Watch Days",
        "investigate_days": "Investigate Days",
    }),
    use_container_width=True,
    hide_index=True,
)

trend_tab, flag_tab, sim_tab = st.tabs(["📈 Audit Trends", "🚩 Unusual Days", "🧪 What-If Simulator"])

with trend_tab:
    trend_source = flagged_results.copy()
    trend_source["audit_date"] = pd.to_datetime(trend_source["audit_date"])
    metric_choice = st.radio(
        "Trend Metric",
        options=["Active Seconds", "Walk Seconds", "Long Gaps >20m"],
        horizontal=True,
        key="audit_monitor_metric",
    )
    metric_col = {
        "Active Seconds": "active_sec",
        "Walk Seconds": "walk_sec",
        "Long Gaps >20m": "long_gap_count",
    }[metric_choice]

    fig = px.line(
        trend_source,
        x="audit_date",
        y=metric_col,
        color="profile_name",
        markers=True,
        labels={"audit_date": "Date", metric_col: metric_choice, "profile_name": "Profile"},
    )
    fig.update_layout(height=380)
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        flagged_results[["audit_date", "profile_name", "staff_on_shift", "sessions", "active_sec", "walk_sec", "long_gap_count", "training_count", "dominant_work_type"]].rename(columns={
            "audit_date": "Date",
            "profile_name": "Profile",
            "staff_on_shift": "Staff",
            "sessions": "Sessions",
            "active_sec": "Active Sec",
            "walk_sec": "Walk Sec",
            "long_gap_count": "Long Gaps >20m",
            "training_count": "Training",
            "dominant_work_type": "Dominant Work Type",
        }),
        use_container_width=True,
        hide_index=True,
    )

with flag_tab:
    flagged_only = flagged_results[flagged_results["status"] != "Normal"].copy()
    if flagged_only.empty:
        st.success("No unusual days were flagged in this date range.")
    else:
        flagged_only["active_time"] = flagged_only["active_sec"].apply(seconds_to_mmss)
        flagged_only["walk_time"] = flagged_only["walk_sec"].apply(seconds_to_mmss)
        st.dataframe(
            flagged_only[["audit_date", "profile_name", "status", "reason", "active_time", "walk_time", "long_gap_count", "training_count", "dominant_work_type"]].rename(columns={
                "audit_date": "Date",
                "profile_name": "Profile",
                "status": "Flag",
                "reason": "Why It Was Flagged",
                "active_time": "Active Time",
                "walk_time": "Walk Time",
                "long_gap_count": "Long Gaps >20m",
                "training_count": "Training",
                "dominant_work_type": "Dominant Work Type",
            }),
            use_container_width=True,
            hide_index=True,
        )

with sim_tab:
    st.caption(
        "Use this to test ideas like: what if the 0600 profile absorbed the 0500 work except returns. "
        "The model uses the stored work-type mix for each saved audit day."
    )

    if len(selected_profiles) < 2:
        st.info("Select at least two saved audit profiles above to use the what-if simulator.")
    else:
        s1, s2, s3 = st.columns([1, 1, 2])
        with s1:
            donor_profile = st.selectbox("From Profile", options=selected_profiles, index=0, key="whatif_donor")
        with s2:
            receiver_options = [p for p in selected_profiles if p != donor_profile]
            receiver_profile = st.selectbox("To Profile", options=receiver_options, key="whatif_receiver")
        with s3:
            excluded_work_types = st.multiselect(
                "Keep These With Original Shift",
                options=App.SHIFT_WORK_TYPE_ORDER,
                default=["Returns / Carousel Putaway"],
                key="whatif_exclusions",
            )

        donor_df = flagged_results[flagged_results["profile_name"] == donor_profile].copy()
        receiver_df = flagged_results[flagged_results["profile_name"] == receiver_profile].copy()
        merged = donor_df.merge(receiver_df, on="audit_date", suffixes=("_donor", "_receiver"))

        if merged.empty:
            st.info("There are no overlapping dates between those two saved profiles in the selected range.")
        else:
            donor_metrics = merged["work_type_breakdown_donor"].apply(flatten_work_type_metrics)

            transfer_active = []
            transfer_walk = []
            donor_remaining_active = []
            projected_receiver_active = []
            projected_receiver_walk = []

            for idx in range(len(merged)):
                donor_map = donor_metrics.iloc[idx]

                excluded_active = sum(donor_map.get(wt, {}).get("active_sec", 0) for wt in excluded_work_types)
                excluded_walk = sum(donor_map.get(wt, {}).get("walk_sec", 0) for wt in excluded_work_types)
                donor_total_active = float(merged.iloc[idx]["active_sec_donor"])
                donor_total_walk = float(merged.iloc[idx]["walk_sec_donor"])
                receiver_total_active = float(merged.iloc[idx]["active_sec_receiver"])
                receiver_total_walk = float(merged.iloc[idx]["walk_sec_receiver"])

                moved_active = max(donor_total_active - excluded_active, 0)
                moved_walk = max(donor_total_walk - excluded_walk, 0)

                transfer_active.append(moved_active)
                transfer_walk.append(moved_walk)
                donor_remaining_active.append(excluded_active)
                projected_receiver_active.append(receiver_total_active + moved_active)
                projected_receiver_walk.append(receiver_total_walk + moved_walk)

            merged["transfer_active_sec"] = transfer_active
            merged["transfer_walk_sec"] = transfer_walk
            merged["donor_remaining_active_sec"] = donor_remaining_active
            merged["projected_receiver_active_sec"] = projected_receiver_active
            merged["projected_receiver_walk_sec"] = projected_receiver_walk

            sim_k1, sim_k2, sim_k3 = st.columns(3)
            sim_k1.metric(
                f"{receiver_profile} Current Avg Active",
                seconds_to_mmss(merged["active_sec_receiver"].mean()),
            )
            sim_k2.metric(
                f"{receiver_profile} Projected Avg Active",
                seconds_to_mmss(merged["projected_receiver_active_sec"].mean()),
            )
            sim_k3.metric(
                f"{donor_profile} Remaining Avg Active",
                seconds_to_mmss(merged["donor_remaining_active_sec"].mean()),
            )

            sim_fig = px.line(
                merged.assign(audit_date=pd.to_datetime(merged["audit_date"])),
                x="audit_date",
                y=["active_sec_receiver", "projected_receiver_active_sec", "donor_remaining_active_sec"],
                labels={"audit_date": "Date", "value": "Active Seconds", "variable": "Scenario"},
            )
            sim_fig.update_layout(height=360)
            st.plotly_chart(sim_fig, use_container_width=True)

            merged["receiver_current"] = merged["active_sec_receiver"].apply(seconds_to_mmss)
            merged["receiver_projected"] = merged["projected_receiver_active_sec"].apply(seconds_to_mmss)
            merged["donor_remaining"] = merged["donor_remaining_active_sec"].apply(seconds_to_mmss)
            merged["transfer_active"] = merged["transfer_active_sec"].apply(seconds_to_mmss)

            st.dataframe(
                merged[["audit_date", "transfer_active", "receiver_current", "receiver_projected", "donor_remaining"]].rename(columns={
                    "audit_date": "Date",
                    "transfer_active": f"Moved From {donor_profile}",
                    "receiver_current": f"{receiver_profile} Current",
                    "receiver_projected": f"{receiver_profile} Projected",
                    "donor_remaining": f"{donor_profile} Remaining",
                }),
                use_container_width=True,
                hide_index=True,
            )
