import pandas as pd
import plotly.express as px
import streamlit as st

import App

_debug_event = getattr(App, "record_ui_debug_event", lambda *args, **kwargs: None)
_debug_panel = getattr(App, "render_ui_debugger", lambda *args, **kwargs: None)


def to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


def collapse_iv_display_rows(df):
    if df.empty:
        return df.copy()

    collapsed = df.copy()
    collapsed["approved_by"] = collapsed["approved_by"].fillna("").astype(str).str.strip()
    collapsed["prepared_by"] = collapsed["prepared_by"].fillna("").astype(str).str.strip()
    collapsed["secondary_approved_by"] = collapsed["secondary_approved_by"].fillna("").astype(str).str.strip()
    collapsed["order_lot_number"] = collapsed["order_lot_number"].fillna("").astype(str).str.strip()
    collapsed["dose_number"] = collapsed["dose_number"].fillna("").astype(str).str.strip()
    collapsed["drug_name"] = collapsed["drug_name"].fillna("").astype(str).str.strip()
    collapsed["compound_type"] = collapsed["compound_type"].fillna("").astype(str).str.strip()
    collapsed["facility_name"] = collapsed["facility_name"].fillna("").astype(str).str.strip()
    collapsed["completed_on"] = pd.to_datetime(collapsed["completed_on"], errors="coerce")
    collapsed["prepare_tat_minutes"] = pd.to_numeric(collapsed["prepare_tat_minutes"], errors="coerce")

    collapse_keys = [
        "facility_name",
        "order_lot_number",
        "drug_name",
        "dose_number",
        "compound_type",
        "num_preparations",
    ]
    fallback_keys = collapse_keys + ["pk"]

    has_lot = collapsed["order_lot_number"].ne("")
    collapsed["display_group"] = ""
    collapsed.loc[has_lot, "display_group"] = (
        collapsed.loc[has_lot, collapse_keys]
        .astype(str)
        .agg(" | ".join, axis=1)
    )
    collapsed.loc[~has_lot, "display_group"] = (
        collapsed.loc[~has_lot, fallback_keys]
        .astype(str)
        .agg(" | ".join, axis=1)
    )

    collapsed["has_approved_by"] = collapsed["approved_by"].ne("").astype(int)
    collapsed["has_named_preparer"] = collapsed["prepared_by"].str.lower().ne("unassigned").astype(int)
    collapsed["has_completed_on"] = collapsed["completed_on"].notna().astype(int)
    collapsed["has_secondary_approval"] = collapsed["secondary_approved_by"].ne("").astype(int)
    collapsed["prepare_tat_rank"] = collapsed["prepare_tat_minutes"].fillna(-1)

    collapsed = collapsed.sort_values(
        [
            "display_group",
            "has_approved_by",
            "has_named_preparer",
            "has_completed_on",
            "has_secondary_approval",
            "prepare_tat_rank",
            "order_dt",
        ],
        ascending=[True, False, False, False, False, False, False],
        na_position="last",
    )
    collapsed = collapsed.drop_duplicates(subset=["display_group"], keep="first").copy()

    return collapsed.drop(
        columns=[
            "display_group",
            "has_approved_by",
            "has_named_preparer",
            "has_completed_on",
            "has_secondary_approval",
            "prepare_tat_rank",
        ],
        errors="ignore",
    )


def classify_iv_order_status(df):
    if df.empty:
        return df.copy()

    out = df.copy()
    for col in ["prepared_by", "approved_by", "secondary_approved_by"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str).str.strip()

    out["completed_on"] = pd.to_datetime(out["completed_on"], errors="coerce")
    if "compound_type" not in out.columns:
        out["compound_type"] = ""
    compound_type = out["compound_type"].fillna("").astype(str).str.upper()
    is_batch = compound_type.eq("BATCH")
    no_completion = out["completed_on"].isna()
    no_preparer = out["prepared_by"].str.lower().isin(["", "unassigned", "none", "nan"])
    no_approval = out["approved_by"].str.lower().isin(["", "unassigned", "none", "nan"])
    no_secondary = out["secondary_approved_by"].str.lower().isin(["", "unassigned", "none", "nan"])
    has_any_approval = ~(no_approval & no_secondary)

    out["order_status"] = "Completed / Made"
    out.loc[no_completion & no_preparer & no_approval & no_secondary, "order_status"] = "Canceled / Not Made"
    out.loc[no_completion & ~(no_preparer & no_approval & no_secondary), "order_status"] = "Needs Completion Review"
    out.loc[
        ~is_batch & no_completion & ~no_preparer & has_any_approval,
        "order_status",
    ] = "Made / Completion Time Missing"
    out.loc[is_batch & no_completion & ~no_preparer & no_approval & no_secondary, "order_status"] = (
        "Batch Ready / Staged"
    )
    out.loc[is_batch & no_completion & has_any_approval, "order_status"] = "Batch Started Prepare"
    return out


def add_batch_make_window(batch_df, workflow_df):
    if batch_df.empty:
        return batch_df.copy()

    out = batch_df.copy()
    out["batch_make_start_dt"] = pd.to_datetime(out.get("order_dt"), errors="coerce")
    out["batch_make_stop_dt"] = pd.NaT
    if workflow_df.empty:
        return out

    wf = workflow_df.copy()
    for col in ["order_lot_number", "dose_number", "workflow_step_name", "workflow_step_category"]:
        if col not in wf.columns:
            wf[col] = ""
        wf[col] = wf[col].fillna("").astype(str).str.strip()
    wf["start_dt"] = pd.to_datetime(wf.get("start_dt"), errors="coerce")
    wf["stop_dt"] = pd.to_datetime(wf.get("stop_dt"), errors="coerce")
    wf["total_duration_minutes"] = pd.to_numeric(wf.get("total_duration_minutes"), errors="coerce")

    prepare_work = wf[
        wf["workflow_step_category"].eq("Working")
        & wf["workflow_step_name"].str.contains("prepare", case=False, na=False)
        & wf["start_dt"].notna()
    ].copy()
    if prepare_work.empty:
        return out

    make_windows = (
        prepare_work.groupby(["order_lot_number", "dose_number"], as_index=False)
        .agg(
            batch_make_start_dt=("start_dt", "min"),
            batch_make_stop_dt=("stop_dt", "max"),
            workflow_make_minutes=("total_duration_minutes", "sum"),
        )
    )
    out["order_lot_number"] = out["order_lot_number"].fillna("").astype(str).str.strip()
    out["dose_number"] = out["dose_number"].fillna("").astype(str).str.strip()
    out = out.merge(
        make_windows,
        on=["order_lot_number", "dose_number"],
        how="left",
        suffixes=("", "_workflow"),
    )
    out["batch_make_start_dt"] = out["batch_make_start_dt_workflow"].fillna(out["batch_make_start_dt"])
    out["batch_make_stop_dt"] = out["batch_make_stop_dt_workflow"].fillna(out["batch_make_stop_dt"])
    return out.drop(columns=["batch_make_start_dt_workflow", "batch_make_stop_dt_workflow"], errors="ignore")


st.set_page_config(page_title="IV Room", page_icon="💉", layout="wide")
App.apply_global_styles()

render_sidebar = App.render_sidebar
load_iv_room_data = App.load_iv_room_data
load_iv_room_workflow_detail = getattr(App, "load_iv_room_workflow_detail", lambda *_args, **_kwargs: pd.DataFrame())

start_date, end_date = render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "IV Room Workload",
        "Track patient-order and batch compounding demand, STAT pressure, technician throughput, and preparation turnaround in the same RxTrack shell.",
        kicker="Operations",
    )
    _debug_event("IV Room", "shared_intro_loaded")
    _debug_panel("IV Room", intro_mode="shared")
else:
    st.header("💉 IV Room Workload")
    st.caption("Track sterile compounding demand, technician throughput, and turnaround time.")
    _debug_event("IV Room", "fallback_header_used")
    _debug_panel("IV Room", intro_mode="fallback")

with st.spinner("Loading IV room workload..."):
    df_iv = load_iv_room_data(start_date, end_date)
    workflow_detail = load_iv_room_workflow_detail(start_date, end_date)

if df_iv.empty:
    st.info("No IV room workload found for this date range. Upload an `IV Room Workload` or `IV Room Batching` file from the sidebar to get started.")
    st.stop()

work = df_iv.copy()
work["order_date"] = pd.to_datetime(work["order_date"], errors="coerce")
work["order_dt"] = pd.to_datetime(work["order_dt"], errors="coerce")
work["completed_on"] = pd.to_datetime(work["completed_on"], errors="coerce")
work["prepare_tat_minutes"] = pd.to_numeric(work["prepare_tat_minutes"], errors="coerce")
work["num_preparations"] = pd.to_numeric(work["num_preparations"], errors="coerce").fillna(0)
work["priority_name"] = work["priority_name"].fillna("").astype(str).str.strip()
work["compound_type"] = work["compound_type"].fillna("Unspecified").astype(str).str.strip().replace("", "Unspecified")
work["prepared_by"] = work["prepared_by"].fillna("").astype(str).str.strip().replace("", "Unassigned")
work["approved_by"] = work["approved_by"].fillna("").astype(str).str.strip().replace("", "Unassigned")
work["secondary_approved_by"] = work["secondary_approved_by"].fillna("").astype(str).str.strip().replace("", "Unassigned")
work = classify_iv_order_status(work)
raw_work = work.copy()
work = collapse_iv_display_rows(work)

facility_options = sorted(work["facility_name"].dropna().unique().tolist())
selected_facilities = st.multiselect("Facility", facility_options, default=facility_options)
compound_options = sorted(work["compound_type"].dropna().unique().tolist())
selected_compounds = st.multiselect("Compound Type", compound_options, default=compound_options)

filtered = work.copy()
status_options = sorted(filtered["order_status"].dropna().unique().tolist())
default_excluded_statuses = {"Canceled / Not Made", "Batch Ready / Staged"}
default_statuses = [status for status in status_options if status not in default_excluded_statuses]
if not default_statuses:
    default_statuses = status_options
selected_statuses = st.multiselect(
    "Order Status",
    status_options,
    default=default_statuses,
    help=(
        "Batch Ready / Staged rows are overnight setup events. Batch Started Prepare is the row used for actual "
        "batch workload and TAT."
    ),
)
if selected_facilities:
    filtered = filtered[filtered["facility_name"].isin(selected_facilities)]
if selected_compounds:
    filtered = filtered[filtered["compound_type"].isin(selected_compounds)]
if selected_statuses:
    filtered = filtered[filtered["order_status"].isin(selected_statuses)]

raw_filtered = raw_work.copy()
if selected_facilities:
    raw_filtered = raw_filtered[raw_filtered["facility_name"].isin(selected_facilities)]
if selected_compounds:
    raw_filtered = raw_filtered[raw_filtered["compound_type"].isin(selected_compounds)]
if selected_statuses:
    raw_filtered = raw_filtered[raw_filtered["order_status"].isin(selected_statuses)]

workflow_filtered = workflow_detail.copy()
if not workflow_filtered.empty and selected_facilities and "facility_name" in workflow_filtered.columns:
    workflow_filtered = workflow_filtered[workflow_filtered["facility_name"].isin(selected_facilities)]

if filtered.empty:
    st.warning("No IV room records match the current filters.")
    st.stop()

collapsed_count = len(raw_filtered) - len(filtered)
if collapsed_count > 0:
    st.caption(
        f"Collapsed {collapsed_count:,} workflow-stage duplicate rows for display. The raw log remains available below for verification."
    )

all_status_raw_filtered = raw_work.copy()
if selected_facilities:
    all_status_raw_filtered = all_status_raw_filtered[all_status_raw_filtered["facility_name"].isin(selected_facilities)]
if selected_compounds:
    all_status_raw_filtered = all_status_raw_filtered[all_status_raw_filtered["compound_type"].isin(selected_compounds)]

canceled_review = all_status_raw_filtered[all_status_raw_filtered["order_status"].eq("Canceled / Not Made")].copy()
batch_staged_review = all_status_raw_filtered[all_status_raw_filtered["order_status"].eq("Batch Ready / Staged")].copy()
review_completion = all_status_raw_filtered[all_status_raw_filtered["order_status"].eq("Needs Completion Review")].copy()

stat_mask = filtered["priority_name"].str.upper().eq("STAT")
tat_ready = filtered.dropna(subset=["prepare_tat_minutes"]).copy()

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("IV Orders", f"{len(filtered):,}")
m2.metric("Preparations", f"{int(filtered['num_preparations'].sum()):,}")
m3.metric("STAT Orders", f"{int(stat_mask.sum()):,}")
m4.metric("Prepared By Count", f"{filtered['prepared_by'].nunique():,}")
m5.metric(
    "Median Prep TAT",
    f"{tat_ready['prepare_tat_minutes'].median():.1f} min" if not tat_ready.empty else "N/A",
)
m6.metric("Excluded Setup/Cancel", f"{len(canceled_review) + len(batch_staged_review):,}")

if not canceled_review.empty or not batch_staged_review.empty:
    st.info(
        "Canceled / Not Made and Batch Ready / Staged rows are excluded from the default workload view. "
        "For batching, RxTrack uses the later Batch Started Prepare row for actual workload and TAT instead "
        "of the overnight ready/staging row."
    )
if "Made / Completion Time Missing" in set(filtered["order_status"].dropna().tolist()):
    st.caption(
        "Rows labeled Made / Completion Time Missing have a real preparer and approval user, so they count as made. "
        "The MedKeeper export did not provide a completed timestamp for those rows."
    )

daily_overview = (
    filtered.assign(day=filtered["order_date"].dt.date)
    .groupby("day", as_index=False)
    .agg(
        iv_orders=("pk", "count"),
        preparations=("num_preparations", "sum"),
        stat_orders=("priority_name", lambda s: s.astype(str).str.upper().eq("STAT").sum()),
        preparers=("prepared_by", "nunique"),
    )
    .sort_values("day")
)
top_preparer = (
    filtered.groupby("prepared_by", as_index=False)
    .agg(preparations=("num_preparations", "sum"), iv_orders=("pk", "count"))
    .sort_values(["preparations", "iv_orders"], ascending=False)
)
top_compound = (
    filtered.groupby("drug_name", as_index=False)
    .agg(preparations=("num_preparations", "sum"), iv_orders=("pk", "count"))
    .sort_values(["preparations", "iv_orders"], ascending=False)
)
long_tat_rows = pd.DataFrame()
if not tat_ready.empty:
    tat_threshold = tat_ready["prepare_tat_minutes"].quantile(0.90)
    long_tat_rows = tat_ready[tat_ready["prepare_tat_minutes"].ge(tat_threshold)].copy()

st.subheader("Manager Snapshot")
snapshot_tab, action_tab, guide_tab = st.tabs(["Overview", "Action Queue", "How to Read"])

with snapshot_tab:
    story_col, signal_col = st.columns([1.2, 1])
    with story_col:
        st.markdown("**What happened in this window**")
        if daily_overview.empty:
            st.info("No daily volume summary is available for the current filters.")
        else:
            busiest_day = daily_overview.sort_values("preparations", ascending=False).iloc[0]
            avg_daily_preps = daily_overview["preparations"].mean()
            st.write(
                f"Across {len(daily_overview):,} day(s), the IV room produced "
                f"{int(filtered['num_preparations'].sum()):,} preparation(s) across {len(filtered):,} order row(s). "
                f"The busiest day was {pd.to_datetime(busiest_day['day']).strftime('%b %d')} with "
                f"{int(busiest_day['preparations']):,} preparation(s). Average daily volume was "
                f"{avg_daily_preps:,.1f} preparation(s)."
            )
        if not top_preparer.empty:
            lead = top_preparer.iloc[0]
            st.write(
                f"Top preparer in the filtered view: **{lead['prepared_by']}** "
                f"with {int(lead['preparations']):,} preparation(s)."
            )
        if not top_compound.empty:
            compound = top_compound.iloc[0]
            st.write(
                f"Highest-volume compound: **{compound['drug_name']}** "
                f"with {int(compound['preparations']):,} preparation(s)."
            )
    with signal_col:
        st.markdown("**Data confidence**")
        workflow_state = "Loaded" if not workflow_filtered.empty else "Not loaded"
        tat_state = f"{len(tat_ready):,} TAT row(s)" if not tat_ready.empty else "No TAT rows"
        st.dataframe(
            pd.DataFrame(
                [
                    {"Signal": "Summary workload rows", "Status": f"{len(raw_filtered):,} raw / {len(filtered):,} display"},
                    {"Signal": "Workflow detail", "Status": workflow_state},
                    {"Signal": "Prepare TAT", "Status": tat_state},
                    {"Signal": "Excluded setup/cancel", "Status": f"{len(canceled_review) + len(batch_staged_review):,} row(s)"},
                ]
            ),
            width="stretch",
            hide_index=True,
        )

with action_tab:
    action_rows = []
    if not review_completion.empty:
        action_rows.append({
            "Priority": "High",
            "Item": "Needs Completion Review",
            "Count": len(review_completion),
            "What it means": "Some prep or approval activity exists, but no completed timestamp was found.",
            "Drilldown": "Open the Needs Completion Review expander below.",
        })
    missing_completion = filtered[filtered["order_status"].eq("Made / Completion Time Missing")].copy()
    if not missing_completion.empty:
        action_rows.append({
            "Priority": "Medium",
            "Item": "Made / Completion Time Missing",
            "Count": len(missing_completion),
            "What it means": "Rows look made, but the export did not provide a completed timestamp.",
            "Drilldown": "Use the raw IV room log or workflow timing detail.",
        })
    if not long_tat_rows.empty:
        action_rows.append({
            "Priority": "Medium",
            "Item": "Slowest Prepare TAT Rows",
            "Count": len(long_tat_rows),
            "What it means": "These rows are at or above the 90th percentile for prepare TAT in this filtered view.",
            "Drilldown": "Use TAT by Technician, User Shift Drilldown, or Slowest/Fastest Batches.",
        })
    if not canceled_review.empty:
        action_rows.append({
            "Priority": "Context",
            "Item": "Canceled / Not Made",
            "Count": len(canceled_review),
            "What it means": "Sent to MedKeeper but likely canceled before compounding.",
            "Drilldown": "Open the Canceled / Not Made Order Review expander below.",
        })
    if not batch_staged_review.empty:
        action_rows.append({
            "Priority": "Context",
            "Item": "Batch Ready / Staged",
            "Count": len(batch_staged_review),
            "What it means": "Overnight setup/handoff rows; useful workflow context but excluded from make-time workload.",
            "Drilldown": "Use Batch Workflow Split > Overnight Setup.",
        })
    if action_rows:
        st.dataframe(pd.DataFrame(action_rows), width="stretch", hide_index=True)
    else:
        st.success("No obvious review queue items are present with the current filters.")

with guide_tab:
    st.markdown(
        """
        **Start here:** use the metrics and Overview tab to understand the selected window.

        **For daily volume:** use Daily IV Volume, then click a day or choose Selected day / Custom range.

        **For people questions:** use Technician Preparation Load first, then User Shift Drilldown for the exact work behind a person.

        **For timing questions:** use Workflow Timing Detail when workflow exports are loaded. That is stronger than the summary TAT field.

        **For batch questions:** use Batch Workflow Split. Overnight Setup is the setup/handoff work; Batch Making by Tech is the actual started-prepare workload.

        **For audit/proof:** use Raw IV Room Log after the summary sections point you to the right row.
        """
    )

st.caption("The detailed workbench below keeps the full audit trail, charts, and drilldowns available after the guided snapshot.")
st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("Daily IV Volume")
    daily = (
        filtered.assign(day=filtered["order_date"].dt.date)
        .groupby("day", as_index=False)
        .agg(
            iv_orders=("pk", "count"),
            preparations=("num_preparations", "sum"),
            stat_orders=("priority_name", lambda s: s.astype(str).str.upper().eq("STAT").sum()),
        )
    )
    fig_daily = px.bar(
        daily,
        x="day",
        y="preparations",
        hover_data=["iv_orders", "stat_orders"],
        labels={"day": "", "preparations": "Preparations"},
        color="preparations",
        color_continuous_scale="Blues",
        title="Click a day to inspect what was made",
    )
    fig_daily.update_layout(coloraxis_showscale=False, height=360)
    daily_event = st.plotly_chart(
        fig_daily,
        width="stretch",
        on_select="rerun",
        selection_mode="points",
        key="iv_daily_volume_chart",
    )

    try:
        selected_points = daily_event.selection.points
    except Exception:
        selected_points = []
    if selected_points:
        clicked_day = pd.to_datetime(selected_points[0].get("x"), errors="coerce")
        if pd.notna(clicked_day):
            st.session_state["iv_selected_day"] = clicked_day.date()

    day_options = sorted(daily["day"].dropna().tolist())
    if day_options:
        default_day = st.session_state.get("iv_selected_day", day_options[-1])
        if default_day not in day_options:
            default_day = day_options[-1]
        detail_mode = st.radio(
            "Detail view",
            ["Summary only", "Selected day", "Custom range"],
            horizontal=True,
            key="iv_detail_mode",
        )
        if detail_mode == "Selected day":
            selected_day = st.selectbox(
                "Inspect day",
                day_options,
                index=day_options.index(default_day),
                format_func=lambda d: pd.to_datetime(d).strftime("%a %b %d, %Y"),
                key="iv_daily_detail_day",
            )
            st.session_state["iv_selected_day"] = selected_day
            detail_start = selected_day
            detail_end = selected_day
        elif detail_mode == "Custom range":
            default_start = st.session_state.get("iv_detail_start", day_options[0])
            default_end = st.session_state.get("iv_detail_end", day_options[-1])
            if default_start not in day_options:
                default_start = day_options[0]
            if default_end not in day_options:
                default_end = day_options[-1]
            detail_start = st.date_input(
                "Detail start",
                value=default_start,
                min_value=day_options[0],
                max_value=day_options[-1],
                key="iv_detail_start",
            )
            detail_end = st.date_input(
                "Detail end",
                value=default_end,
                min_value=day_options[0],
                max_value=day_options[-1],
                key="iv_detail_end",
            )
            if detail_end < detail_start:
                detail_end = detail_start
            selected_day = None
        else:
            selected_day = None
            detail_start = None
            detail_end = None
    else:
        detail_mode = "Summary only"
        selected_day = None
        detail_start = None
        detail_end = None

with col2:
    st.subheader("Order Mix by Hour")
    hourly = filtered.dropna(subset=["order_dt"]).copy()
    if hourly.empty:
        st.info("No parsable order timestamps are available for hourly analysis.")
    else:
        hourly["hour"] = hourly["order_dt"].dt.hour
        hourly_mix = hourly.groupby("hour", as_index=False).agg(
            iv_orders=("pk", "count"),
            preparations=("num_preparations", "sum"),
        )
        fig_hour = px.line(
            hourly_mix,
            x="hour",
            y="preparations",
            markers=True,
            labels={"hour": "Hour of Day", "preparations": "Preparations"},
        )
        fig_hour.update_layout(height=360)
        st.plotly_chart(fig_hour, width="stretch")

if detail_mode != "Summary only" and detail_start is not None and detail_end is not None:
    day_detail = filtered[
        filtered["order_date"].dt.date.between(detail_start, detail_end)
    ].copy()
    if detail_start == detail_end:
        detail_label = pd.to_datetime(detail_start).strftime('%A, %B %d, %Y')
    else:
        detail_label = f"{pd.to_datetime(detail_start).strftime('%b %d, %Y')} to {pd.to_datetime(detail_end).strftime('%b %d, %Y')}"
    st.subheader(f"IV Room Detail for {detail_label}")

    d1, d2, d3, d4, d5 = st.columns(5)
    d1.metric("Orders Made", f"{len(day_detail):,}")
    d2.metric("Preparations", f"{int(day_detail['num_preparations'].sum()):,}")
    d3.metric("STAT Orders", f"{int(day_detail['priority_name'].str.upper().eq('STAT').sum()):,}")
    d4.metric("Preparers", f"{day_detail['prepared_by'].nunique():,}")
    day_tat = day_detail["prepare_tat_minutes"].dropna()
    d5.metric("Median TAT", f"{day_tat.median():.1f} min" if not day_tat.empty else "N/A")

    day_mix_col, day_table_col = st.columns([1, 2])
    with day_mix_col:
        day_compounds = (
            day_detail.groupby("compound_type", as_index=False)
            .agg(iv_orders=("pk", "count"), preparations=("num_preparations", "sum"))
            .sort_values("preparations", ascending=False)
        )
        fig_day_mix = px.bar(
            day_compounds,
            x="compound_type",
            y="preparations",
            hover_data=["iv_orders"],
            labels={"compound_type": "", "preparations": "Preparations"},
            color="compound_type",
            title="Day Mix",
        )
        fig_day_mix.update_layout(height=320, showlegend=False)
        st.plotly_chart(fig_day_mix, width="stretch")

    with day_table_col:
        detail_cols = [
            "order_dt",
            "completed_on",
            "facility_name",
            "compound_type",
            "num_preparations",
            "drug_name",
            "dose_number",
            "priority_name",
            "prepare_tat_minutes",
            "prepared_by",
            "approved_by",
            "secondary_approved_by",
            "order_lot_number",
        ]
        st.dataframe(
            day_detail[[c for c in detail_cols if c in day_detail.columns]].sort_values(
                ["order_dt", "drug_name"], ascending=[True, True], na_position="last"
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "order_dt": st.column_config.DatetimeColumn("Ordered", format="MM/DD/YY HH:mm"),
                "completed_on": st.column_config.DatetimeColumn("Completed", format="MM/DD/YY HH:mm"),
                "num_preparations": st.column_config.NumberColumn("Preps", format="%.0f"),
                "prepare_tat_minutes": st.column_config.NumberColumn("TAT Min", format="%.1f"),
            },
        )

    st.divider()

col3, col4 = st.columns(2)

with col3:
    st.subheader("Highest-Volume Compounds")
    top_drugs = (
        filtered.groupby("drug_name", as_index=False)
        .agg(
            iv_orders=("pk", "count"),
            preparations=("num_preparations", "sum"),
        )
        .sort_values(["preparations", "iv_orders"], ascending=False)
        .head(15)
    )
    fig_drugs = px.bar(
        top_drugs.sort_values("preparations"),
        x="preparations",
        y="drug_name",
        orientation="h",
        hover_data=["iv_orders"],
        labels={"preparations": "Preparations", "drug_name": ""},
        color="preparations",
        color_continuous_scale="Tealgrn",
    )
    fig_drugs.update_layout(coloraxis_showscale=False, height=420)
    st.plotly_chart(fig_drugs, width="stretch")

with col4:
    st.subheader("Technician Preparation Load")
    tech_load = (
        filtered.groupby("prepared_by", as_index=False)
        .agg(
            iv_orders=("pk", "count"),
            preparations=("num_preparations", "sum"),
            median_tat=("prepare_tat_minutes", "median"),
        )
        .sort_values(["preparations", "iv_orders"], ascending=False)
        .head(15)
    )
    fig_tech = px.bar(
        tech_load.sort_values("preparations"),
        x="preparations",
        y="prepared_by",
        orientation="h",
        hover_data=["iv_orders", "median_tat"],
        labels={"preparations": "Preparations", "prepared_by": ""},
        color="preparations",
        color_continuous_scale="Greens",
    )
    fig_tech.update_layout(coloraxis_showscale=False, height=420)
    st.plotly_chart(fig_tech, width="stretch")

canceled_cols = [
    "order_dt",
    "completed_on",
    "facility_name",
    "compound_type",
    "drug_name",
    "dose_number",
    "order_lot_number",
    "num_preparations",
    "priority_name",
    "prepare_tat_minutes",
    "prepared_by",
    "approved_by",
    "secondary_approved_by",
    "order_status",
]
if not canceled_review.empty:
    with st.expander(f"Canceled / Not Made Order Review ({len(canceled_review):,} rows)", expanded=False):
        st.caption(
            "These rows have no completion timestamp and no preparer or approval user. Based on your MedKeeper workflow, "
            "treat them as orders that were sent but canceled before compounding."
        )
        st.dataframe(
            canceled_review[[c for c in canceled_cols if c in canceled_review.columns]].sort_values(
                ["order_dt", "drug_name"], ascending=[False, True], na_position="last"
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "order_dt": st.column_config.DatetimeColumn("Ordered", format="MM/DD/YY HH:mm"),
                "completed_on": st.column_config.DatetimeColumn("Completed", format="MM/DD/YY HH:mm"),
                "num_preparations": st.column_config.NumberColumn("Preps", format="%.0f"),
                "prepare_tat_minutes": st.column_config.NumberColumn("TAT Min", format="%.1f"),
            },
        )
        st.download_button(
            "Export canceled / not made orders",
            data=to_csv_bytes(canceled_review[[c for c in canceled_cols if c in canceled_review.columns]]),
            file_name="iv_room_canceled_not_made_orders.csv",
            mime="text/csv",
        )

if not batch_staged_review.empty:
    with st.expander(f"Batch Ready / Staged Review ({len(batch_staged_review):,} rows)", expanded=False):
        st.caption(
            "These are batch setup handoffs, usually from the overnight tech. They are useful for workflow context, "
            "but excluded from default workload and TAT because the compound starts at Batch Started Prepare."
        )
        st.dataframe(
            batch_staged_review[[c for c in canceled_cols if c in batch_staged_review.columns]].sort_values(
                ["order_dt", "drug_name"], ascending=[False, True], na_position="last"
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "order_dt": st.column_config.DatetimeColumn("Ready/Staged", format="MM/DD/YY HH:mm"),
                "completed_on": st.column_config.DatetimeColumn("Completed", format="MM/DD/YY HH:mm"),
                "num_preparations": st.column_config.NumberColumn("Preps", format="%.0f"),
                "prepare_tat_minutes": st.column_config.NumberColumn("Source TAT Min", format="%.1f"),
            },
        )
        st.download_button(
            "Export batch ready / staged rows",
            data=to_csv_bytes(batch_staged_review[[c for c in canceled_cols if c in batch_staged_review.columns]]),
            file_name="iv_room_batch_ready_staged_rows.csv",
            mime="text/csv",
        )

if not review_completion.empty:
    with st.expander(f"Needs Completion Review ({len(review_completion):,} rows)", expanded=False):
        st.caption(
            "These rows have some preparation or approval activity but no completed timestamp. Review them before counting "
            "them as made or canceled."
        )
        raw_cols = [
            "order_dt",
            "completed_on",
            "facility_name",
            "compound_type",
            "drug_name",
            "dose_number",
            "order_lot_number",
            "num_preparations",
            "priority_name",
            "prepare_tat_minutes",
            "prepared_by",
            "approved_by",
            "secondary_approved_by",
            "order_status",
        ]
        st.dataframe(
            review_completion[[c for c in raw_cols if c in review_completion.columns]].sort_values(
                ["order_dt", "drug_name"], ascending=[False, True], na_position="last"
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "order_dt": st.column_config.DatetimeColumn("Ordered", format="MM/DD/YY HH:mm"),
                "completed_on": st.column_config.DatetimeColumn("Completed", format="MM/DD/YY HH:mm"),
                "num_preparations": st.column_config.NumberColumn("Preps", format="%.0f"),
                "prepare_tat_minutes": st.column_config.NumberColumn("TAT Min", format="%.1f"),
            },
        )

st.divider()

stat_col, tat_col = st.columns(2)

with stat_col:
    st.subheader("Priority Mix")
    priority_mix = (
        filtered.assign(priority_bucket=filtered["priority_name"].replace({"": "Routine"}))
        .groupby("priority_bucket", as_index=False)
        .agg(iv_orders=("pk", "count"), preparations=("num_preparations", "sum"))
        .sort_values("preparations", ascending=False)
    )
    fig_priority = px.pie(
        priority_mix,
        names="priority_bucket",
        values="preparations",
        hole=0.45,
    )
    fig_priority.update_layout(height=360)
    st.plotly_chart(fig_priority, width="stretch")

with tat_col:
    st.subheader("TAT by Technician")
    if tat_ready.empty:
        st.info("`Prepare TAT Minutes` is only populated on a small share of records in this export, so TAT benchmarking is limited right now.")
    else:
        tat_by_tech = (
            tat_ready.groupby("prepared_by", as_index=False)
            .agg(
                tat_records=("pk", "count"),
                median_tat=("prepare_tat_minutes", "median"),
                p90_tat=("prepare_tat_minutes", lambda s: s.quantile(0.90)),
            )
            .sort_values(["tat_records", "median_tat"], ascending=[False, True])
            .head(15)
        )
        fig_tat = px.scatter(
            tat_by_tech,
            x="tat_records",
            y="median_tat",
            size="p90_tat",
            hover_name="prepared_by",
            labels={"tat_records": "TAT Records", "median_tat": "Median TAT (min)", "p90_tat": "P90 TAT"},
        )
        fig_tat.update_layout(height=360)
        st.plotly_chart(fig_tat, width="stretch")

st.subheader("Workflow Timing Detail")
if workflow_filtered.empty:
    st.info(
        "Upload the MedKeeper workflow detail exports as `IV Room Workflow Detail` to see initial creation, "
        "component check, prepare, approve, and secondary approval timing."
    )
else:
    wf = workflow_filtered.copy()
    for col in ["workflow_step_type", "workflow_step_name", "workflow_step_category", "prepared_by", "approved_by"]:
        wf[col] = wf[col].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
    wf["total_duration_minutes"] = pd.to_numeric(wf["total_duration_minutes"], errors="coerce").fillna(0)
    wf["stage"] = wf["workflow_step_type"].str.strip().replace("", "Unknown")
    wf["activity"] = wf["workflow_step_name"].str.strip().replace("", "Unknown")
    wf["category"] = wf["workflow_step_category"].str.strip().replace("", "Unknown")

    timing_status = wf["category"].isin(["Working", "Waiting"])
    if not timing_status.any():
        st.warning("Workflow detail is loaded, but no Waiting/Working timing rows were found in the selected range.")
    else:
        wf_timing = wf[timing_status].copy()
        working = wf_timing[wf_timing["category"].eq("Working")].copy()
        waiting = wf_timing[wf_timing["category"].eq("Waiting")].copy()

        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Workflow Rows", f"{len(wf_timing):,}")
        w2.metric("Working Minutes", f"{working['total_duration_minutes'].sum():,.1f}")
        w3.metric("Waiting Minutes", f"{waiting['total_duration_minutes'].sum():,.1f}")
        w4.metric("Orders/Lots", f"{wf_timing['order_lot_number'].nunique():,}")

        stage_summary = (
            wf_timing.groupby(["stage", "activity", "category"], as_index=False)
            .agg(
                rows=("pk", "count"),
                total_minutes=("total_duration_minutes", "sum"),
                median_minutes=("total_duration_minutes", "median"),
                p90_minutes=("total_duration_minutes", lambda s: s.quantile(0.90)),
            )
            .sort_values(["stage", "category", "total_minutes"], ascending=[True, True, False])
        )

        timing_tab, prep_tab, approve_tab, detail_tab = st.tabs(
            ["Stage Timing", "Prepared By Working", "Approved By Working", "Order Timeline"]
        )
        with timing_tab:
            st.dataframe(
                stage_summary,
                width="stretch",
                hide_index=True,
                column_config={
                    "rows": st.column_config.NumberColumn("Rows", format="%.0f"),
                    "total_minutes": st.column_config.NumberColumn("Total Min", format="%.1f"),
                    "median_minutes": st.column_config.NumberColumn("Median Min", format="%.1f"),
                    "p90_minutes": st.column_config.NumberColumn("P90 Min", format="%.1f"),
                },
            )

        with prep_tab:
            prep_work = working[working["prepared_by"].ne("None") & working["prepared_by"].ne("Unknown")].copy()
            if prep_work.empty:
                st.info("No prepared-by working rows are loaded for the selected range.")
            else:
                prep_summary = (
                    prep_work.groupby("prepared_by", as_index=False)
                    .agg(
                        working_rows=("pk", "count"),
                        orders=("order_lot_number", "nunique"),
                        working_minutes=("total_duration_minutes", "sum"),
                        median_minutes=("total_duration_minutes", "median"),
                    )
                    .sort_values(["working_minutes", "working_rows"], ascending=False)
                )
                st.dataframe(
                    prep_summary,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "working_rows": st.column_config.NumberColumn("Working Rows", format="%.0f"),
                        "orders": st.column_config.NumberColumn("Orders/Lots", format="%.0f"),
                        "working_minutes": st.column_config.NumberColumn("Working Min", format="%.1f"),
                        "median_minutes": st.column_config.NumberColumn("Median Min", format="%.1f"),
                    },
                )

        with approve_tab:
            approve_work = working[working["approved_by"].ne("None") & working["approved_by"].ne("Unknown")].copy()
            if approve_work.empty:
                st.info("No approved-by working rows are loaded for the selected range.")
            else:
                approve_summary = (
                    approve_work.groupby("approved_by", as_index=False)
                    .agg(
                        working_rows=("pk", "count"),
                        orders=("order_lot_number", "nunique"),
                        working_minutes=("total_duration_minutes", "sum"),
                        median_minutes=("total_duration_minutes", "median"),
                    )
                    .sort_values(["working_minutes", "working_rows"], ascending=False)
                )
                st.dataframe(
                    approve_summary,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "working_rows": st.column_config.NumberColumn("Working Rows", format="%.0f"),
                        "orders": st.column_config.NumberColumn("Orders/Lots", format="%.0f"),
                        "working_minutes": st.column_config.NumberColumn("Working Min", format="%.1f"),
                        "median_minutes": st.column_config.NumberColumn("Median Min", format="%.1f"),
                    },
                )

        with detail_tab:
            lot_options = sorted(wf_timing["order_lot_number"].dropna().astype(str).unique().tolist())
            if not lot_options:
                st.info("No order/lot values are available in the workflow detail rows.")
            else:
                selected_lot = st.selectbox("Order/Lot timeline", lot_options, index=0)
                timeline_cols = [
                    "start_dt", "stop_dt", "order_lot_number", "dose_number", "drug_name",
                    "stage", "activity", "category", "total_duration_minutes", "prepared_by", "approved_by",
                    "source_file",
                ]
                timeline = wf_timing[wf_timing["order_lot_number"].astype(str).eq(str(selected_lot))].copy()
                st.dataframe(
                    timeline[[c for c in timeline_cols if c in timeline.columns]].sort_values(
                        ["start_dt", "stage", "activity"], na_position="last"
                    ),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "start_dt": st.column_config.DatetimeColumn("Start", format="MM/DD/YY HH:mm"),
                        "stop_dt": st.column_config.DatetimeColumn("Stop", format="MM/DD/YY HH:mm"),
                        "total_duration_minutes": st.column_config.NumberColumn("Minutes", format="%.2f"),
                    },
                )

st.subheader("User Shift Drilldown")
user_candidates = set()
if not workflow_filtered.empty:
    for user_col in ["prepared_by", "approved_by"]:
        if user_col in workflow_filtered.columns:
            user_candidates.update(
                workflow_filtered[user_col]
                .dropna()
                .astype(str)
                .str.strip()
                .replace({"": pd.NA, "None": pd.NA, "Unassigned": pd.NA})
                .dropna()
                .tolist()
            )
if not raw_filtered.empty:
    for user_col in ["prepared_by", "approved_by", "secondary_approved_by"]:
        if user_col in raw_filtered.columns:
            user_candidates.update(
                raw_filtered[user_col]
                .dropna()
                .astype(str)
                .str.strip()
                .replace({"": pd.NA, "None": pd.NA, "Unassigned": pd.NA})
                .dropna()
                .tolist()
            )

user_options = sorted(user_candidates)
if not user_options:
    st.info("No IV room users are available in the selected date range.")
else:
    drill_col1, drill_col2 = st.columns([2, 1])
    selected_user = drill_col1.selectbox("IV room user", user_options, key="iv_user_shift_drilldown")
    user_activity_scope = drill_col2.segmented_control(
        "Activity",
        ["All", "Prepared", "Approved"],
        default="All",
        key="iv_user_shift_activity_scope",
    )

    user_workflow = pd.DataFrame()
    if not workflow_filtered.empty:
        user_workflow = workflow_filtered.copy()
        for col in ["prepared_by", "approved_by", "workflow_step_type", "workflow_step_name", "workflow_step_category"]:
            if col not in user_workflow.columns:
                user_workflow[col] = ""
            user_workflow[col] = user_workflow[col].fillna("").astype(str).str.strip()
        user_workflow["total_duration_minutes"] = pd.to_numeric(
            user_workflow["total_duration_minutes"], errors="coerce"
        ).fillna(0)
        user_workflow["stage"] = user_workflow["workflow_step_type"].replace("", "Unknown")
        user_workflow["activity"] = user_workflow["workflow_step_name"].replace("", "Unknown")
        user_workflow["category"] = user_workflow["workflow_step_category"].replace("", "Unknown")
        if user_activity_scope == "Prepared":
            user_workflow = user_workflow[user_workflow["prepared_by"].eq(selected_user)].copy()
        elif user_activity_scope == "Approved":
            user_workflow = user_workflow[user_workflow["approved_by"].eq(selected_user)].copy()
        else:
            user_workflow = user_workflow[
                user_workflow["prepared_by"].eq(selected_user)
                | user_workflow["approved_by"].eq(selected_user)
            ].copy()

    user_orders = raw_filtered.copy()
    if not user_orders.empty:
        if user_activity_scope == "Prepared":
            user_orders = user_orders[user_orders["prepared_by"].fillna("").astype(str).eq(selected_user)].copy()
        elif user_activity_scope == "Approved":
            user_orders = user_orders[
                user_orders["approved_by"].fillna("").astype(str).eq(selected_user)
                | user_orders["secondary_approved_by"].fillna("").astype(str).eq(selected_user)
            ].copy()
        else:
            user_orders = user_orders[
                user_orders["prepared_by"].fillna("").astype(str).eq(selected_user)
                | user_orders["approved_by"].fillna("").astype(str).eq(selected_user)
                | user_orders["secondary_approved_by"].fillna("").astype(str).eq(selected_user)
            ].copy()

    working_rows = user_workflow[user_workflow["category"].eq("Working")].copy() if not user_workflow.empty else pd.DataFrame()
    waiting_rows = user_workflow[user_workflow["category"].eq("Waiting")].copy() if not user_workflow.empty else pd.DataFrame()
    first_activity = user_workflow["start_dt"].dropna().min() if not user_workflow.empty else pd.NaT
    last_activity = user_workflow["stop_dt"].dropna().max() if not user_workflow.empty else pd.NaT
    if pd.isna(last_activity) and not user_workflow.empty:
        last_activity = user_workflow["start_dt"].dropna().max()

    u1, u2, u3, u4, u5 = st.columns(5)
    u1.metric("Order Rows", f"{len(user_orders):,}")
    u2.metric("Workflow Rows", f"{len(user_workflow):,}")
    u3.metric("Working Min", f"{working_rows['total_duration_minutes'].sum():,.1f}" if not working_rows.empty else "0.0")
    u4.metric("Waiting Min", f"{waiting_rows['total_duration_minutes'].sum():,.1f}" if not waiting_rows.empty else "0.0")
    if pd.notna(first_activity) and pd.notna(last_activity):
        span_minutes = max((last_activity - first_activity).total_seconds() / 60, 0)
        u5.metric("Activity Span", f"{span_minutes / 60:.1f}h" if span_minutes >= 60 else f"{span_minutes:.0f}m")
    else:
        u5.metric("Activity Span", "-")

    shift_tab1, shift_tab2, shift_tab3, shift_tab4 = st.tabs(
        ["Timeline", "Stage Summary", "Order Rows", "Slowest Work"]
    )
    with shift_tab1:
        if user_workflow.empty:
            st.info("No workflow-detail rows found for this user and activity filter.")
        else:
            user_timeline_cols = [
                "start_dt", "stop_dt", "order_lot_number", "dose_number", "drug_name",
                "stage", "activity", "category", "total_duration_minutes", "prepared_by", "approved_by",
                "source_file",
            ]
            st.dataframe(
                user_workflow[[c for c in user_timeline_cols if c in user_workflow.columns]].sort_values(
                    ["start_dt", "order_lot_number"], na_position="last"
                ),
                width="stretch",
                hide_index=True,
                column_config={
                    "start_dt": st.column_config.DatetimeColumn("Start", format="MM/DD/YY HH:mm"),
                    "stop_dt": st.column_config.DatetimeColumn("Stop", format="MM/DD/YY HH:mm"),
                    "total_duration_minutes": st.column_config.NumberColumn("Minutes", format="%.2f"),
                },
            )

    with shift_tab2:
        if user_workflow.empty:
            st.info("No workflow-detail rows found for this user and activity filter.")
        else:
            user_stage_summary = (
                user_workflow.groupby(["stage", "activity", "category"], as_index=False)
                .agg(
                    rows=("pk", "count"),
                    orders=("order_lot_number", "nunique"),
                    minutes=("total_duration_minutes", "sum"),
                    median_minutes=("total_duration_minutes", "median"),
                )
                .sort_values(["minutes", "rows"], ascending=False)
            )
            st.dataframe(
                user_stage_summary,
                width="stretch",
                hide_index=True,
                column_config={
                    "rows": st.column_config.NumberColumn("Rows", format="%.0f"),
                    "orders": st.column_config.NumberColumn("Orders/Lots", format="%.0f"),
                    "minutes": st.column_config.NumberColumn("Minutes", format="%.1f"),
                    "median_minutes": st.column_config.NumberColumn("Median Min", format="%.1f"),
                },
            )

    with shift_tab3:
        if user_orders.empty:
            st.info("No IV summary order rows found for this user and activity filter.")
        else:
            user_order_cols = [
                "order_dt", "completed_on", "compound_type", "drug_name", "dose_number",
                "order_lot_number", "num_preparations", "priority_name", "prepare_tat_minutes",
                "prepared_by", "approved_by", "secondary_approved_by", "order_status",
            ]
            st.dataframe(
                user_orders[[c for c in user_order_cols if c in user_orders.columns]].sort_values(
                    ["order_dt", "drug_name"], ascending=[False, True], na_position="last"
                ),
                width="stretch",
                hide_index=True,
                column_config={
                    "order_dt": st.column_config.DatetimeColumn("Ordered", format="MM/DD/YY HH:mm"),
                    "completed_on": st.column_config.DatetimeColumn("Completed", format="MM/DD/YY HH:mm"),
                    "num_preparations": st.column_config.NumberColumn("Preps", format="%.0f"),
                    "prepare_tat_minutes": st.column_config.NumberColumn("Summary TAT Min", format="%.1f"),
                },
            )

    with shift_tab4:
        if working_rows.empty:
            st.info("No working rows found for this user and activity filter.")
        else:
            slow_user_work = working_rows.sort_values("total_duration_minutes", ascending=False).head(25)
            slow_cols = [
                "start_dt", "stop_dt", "order_lot_number", "dose_number", "drug_name",
                "stage", "activity", "total_duration_minutes", "prepared_by", "approved_by",
            ]
            st.dataframe(
                slow_user_work[[c for c in slow_cols if c in slow_user_work.columns]],
                width="stretch",
                hide_index=True,
                column_config={
                    "start_dt": st.column_config.DatetimeColumn("Start", format="MM/DD/YY HH:mm"),
                    "stop_dt": st.column_config.DatetimeColumn("Stop", format="MM/DD/YY HH:mm"),
                    "total_duration_minutes": st.column_config.NumberColumn("Working Min", format="%.2f"),
                },
            )

mix_col, batch_col = st.columns(2)

with mix_col:
    st.subheader("Compound Type Mix")
    compound_mix = (
        filtered.groupby("compound_type", as_index=False)
        .agg(
            iv_orders=("pk", "count"),
            preparations=("num_preparations", "sum"),
        )
        .sort_values("preparations", ascending=False)
    )
    fig_mix = px.bar(
        compound_mix,
        x="compound_type",
        y="preparations",
        hover_data=["iv_orders"],
        labels={"compound_type": "", "preparations": "Preparations"},
        color="compound_type",
    )
    fig_mix.update_layout(height=340, showlegend=False)
    st.plotly_chart(fig_mix, width="stretch")

with batch_col:
    st.subheader("Batch Making Summary")
    batch_making = all_status_raw_filtered[
        all_status_raw_filtered["order_status"].eq("Batch Started Prepare")
    ].copy()
    batch_making = add_batch_make_window(batch_making, workflow_filtered)
    if batch_making.empty:
        st.info("No batch making records are in the current filter window.")
    else:
        batch_summary = (
            batch_making.groupby("drug_name", as_index=False)
            .agg(
                batch_orders=("pk", "count"),
                batch_preparations=("num_preparations", "sum"),
                median_tat=("prepare_tat_minutes", "median"),
            )
            .sort_values(["batch_preparations", "batch_orders"], ascending=False)
            .head(12)
        )
        st.dataframe(
            batch_summary,
            width="stretch",
            hide_index=True,
            column_config={
                "batch_preparations": st.column_config.NumberColumn("Preparations", format="%.0f"),
                "median_tat": st.column_config.NumberColumn("Median TAT (min)", format="%.1f"),
            },
        )

st.subheader("Batch Workflow Split")
st.caption(
    "Overnight setup is tracked separately from actual batch making. Setup rows show what was prepared for the queue; "
    "making rows show who started preparing the batch and how long that compound took."
)
setup_tab, making_tab, slow_tab = st.tabs(["Overnight Setup", "Batch Making by Tech", "Slowest/Fastest Batches"])

with setup_tab:
    if batch_staged_review.empty:
        st.info("No batch ready/staged rows are in the current filter window.")
    else:
        setup_summary = (
            batch_staged_review.groupby("prepared_by", as_index=False)
            .agg(
                setup_rows=("pk", "count"),
                setup_preparations=("num_preparations", "sum"),
                unique_batches=("order_lot_number", "nunique"),
                unique_drugs=("drug_name", "nunique"),
                median_source_tat=("prepare_tat_minutes", "median"),
            )
            .sort_values(["setup_preparations", "setup_rows"], ascending=False)
        )
        st.dataframe(
            setup_summary,
            width="stretch",
            hide_index=True,
            column_config={
                "setup_rows": st.column_config.NumberColumn("Setup Rows", format="%.0f"),
                "setup_preparations": st.column_config.NumberColumn("Setup Preps", format="%.0f"),
                "unique_batches": st.column_config.NumberColumn("Batches", format="%.0f"),
                "unique_drugs": st.column_config.NumberColumn("Meds", format="%.0f"),
                "median_source_tat": st.column_config.NumberColumn("Median Source TAT", format="%.1f"),
            },
        )

with making_tab:
    batch_making = all_status_raw_filtered[
        all_status_raw_filtered["order_status"].eq("Batch Started Prepare")
    ].copy()
    batch_making = add_batch_make_window(batch_making, workflow_filtered)
    if batch_making.empty:
        st.info("No batch started-prepare rows are in the current filter window.")
    else:
        maker_summary = (
            batch_making.groupby("prepared_by", as_index=False)
            .agg(
                batches_made=("pk", "count"),
                preparations=("num_preparations", "sum"),
                unique_drugs=("drug_name", "nunique"),
                median_make_tat=("prepare_tat_minutes", "median"),
                fastest_make_tat=("prepare_tat_minutes", "min"),
                slowest_make_tat=("prepare_tat_minutes", "max"),
            )
            .sort_values(["preparations", "batches_made"], ascending=False)
        )
        st.dataframe(
            maker_summary,
            width="stretch",
            hide_index=True,
            column_config={
                "batches_made": st.column_config.NumberColumn("Batches Made", format="%.0f"),
                "preparations": st.column_config.NumberColumn("Preparations", format="%.0f"),
                "unique_drugs": st.column_config.NumberColumn("Meds", format="%.0f"),
                "median_make_tat": st.column_config.NumberColumn("Median Make TAT", format="%.1f"),
                "fastest_make_tat": st.column_config.NumberColumn("Fastest", format="%.1f"),
                "slowest_make_tat": st.column_config.NumberColumn("Slowest", format="%.1f"),
            },
        )
        maker_options = maker_summary["prepared_by"].dropna().astype(str).tolist()
        selected_maker = st.selectbox(
            "Drill into batch maker",
            maker_options,
            key="iv_batch_maker_drilldown",
        )
        maker_rows = batch_making[batch_making["prepared_by"].astype(str).eq(str(selected_maker))].copy()
        maker_detail_cols = [
            "batch_make_start_dt",
            "batch_make_stop_dt",
            "drug_name",
            "order_lot_number",
            "num_preparations",
            "prepare_tat_minutes",
            "workflow_make_minutes",
            "approved_by",
            "secondary_approved_by",
            "order_status",
        ]
        st.dataframe(
            maker_rows[[c for c in maker_detail_cols if c in maker_rows.columns]].sort_values(
                ["batch_make_start_dt", "drug_name"], ascending=[False, True], na_position="last"
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "batch_make_start_dt": st.column_config.DatetimeColumn("Started Prepare", format="MM/DD/YY HH:mm"),
                "batch_make_stop_dt": st.column_config.DatetimeColumn("Stopped Prepare", format="MM/DD/YY HH:mm"),
                "num_preparations": st.column_config.NumberColumn("Preps", format="%.0f"),
                "prepare_tat_minutes": st.column_config.NumberColumn("Make TAT Min", format="%.1f"),
                "workflow_make_minutes": st.column_config.NumberColumn("Workflow Prepare Min", format="%.1f"),
            },
        )
        if not workflow_filtered.empty:
            maker_workflow = workflow_filtered[
                workflow_filtered["prepared_by"].fillna("").astype(str).eq(str(selected_maker))
                & workflow_filtered["workflow_step_category"].fillna("").astype(str).str.strip().eq("Working")
            ].copy()
            if not maker_workflow.empty:
                maker_workflow["total_duration_minutes"] = pd.to_numeric(
                    maker_workflow["total_duration_minutes"], errors="coerce"
                ).fillna(0)
                maker_workflow["stage"] = maker_workflow["workflow_step_type"].fillna("").astype(str).str.strip()
                maker_workflow["activity"] = maker_workflow["workflow_step_name"].fillna("").astype(str).str.strip()
                st.caption("Workflow-detail rows behind this maker's working time.")
                workflow_cols = [
                    "start_dt", "stop_dt", "order_lot_number", "dose_number", "drug_name",
                    "stage", "activity", "total_duration_minutes", "approved_by", "source_file",
                ]
                st.dataframe(
                    maker_workflow[[c for c in workflow_cols if c in maker_workflow.columns]].sort_values(
                        ["start_dt", "order_lot_number"], na_position="last"
                    ),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "start_dt": st.column_config.DatetimeColumn("Start", format="MM/DD/YY HH:mm"),
                        "stop_dt": st.column_config.DatetimeColumn("Stop", format="MM/DD/YY HH:mm"),
                        "total_duration_minutes": st.column_config.NumberColumn("Working Min", format="%.2f"),
                    },
                )

with slow_tab:
    batch_making = all_status_raw_filtered[
        all_status_raw_filtered["order_status"].eq("Batch Started Prepare")
    ].copy()
    batch_making = add_batch_make_window(batch_making, workflow_filtered)
    if batch_making.empty:
        st.info("No batch making details are in the current filter window.")
    else:
        detail_cols = [
            "batch_make_start_dt",
            "batch_make_stop_dt",
            "prepared_by",
            "drug_name",
            "order_lot_number",
            "num_preparations",
            "prepare_tat_minutes",
            "workflow_make_minutes",
            "approved_by",
            "secondary_approved_by",
        ]
        slowest = batch_making.sort_values("prepare_tat_minutes", ascending=False).head(15)
        fastest = batch_making.sort_values("prepare_tat_minutes", ascending=True).head(15)
        slow_col, fast_col = st.columns(2)
        with slow_col:
            st.markdown("**Slowest Made Batches**")
            st.dataframe(
                slowest[[c for c in detail_cols if c in slowest.columns]],
                width="stretch",
                hide_index=True,
                column_config={
                    "order_dt": st.column_config.DatetimeColumn("Started", format="MM/DD/YY HH:mm"),
                    "batch_make_start_dt": st.column_config.DatetimeColumn("Started Prepare", format="MM/DD/YY HH:mm"),
                    "batch_make_stop_dt": st.column_config.DatetimeColumn("Stopped Prepare", format="MM/DD/YY HH:mm"),
                    "num_preparations": st.column_config.NumberColumn("Preps", format="%.0f"),
                    "prepare_tat_minutes": st.column_config.NumberColumn("Make TAT Min", format="%.1f"),
                    "workflow_make_minutes": st.column_config.NumberColumn("Workflow Prepare Min", format="%.1f"),
                },
            )
        with fast_col:
            st.markdown("**Fastest Made Batches**")
            st.dataframe(
                fastest[[c for c in detail_cols if c in fastest.columns]],
                width="stretch",
                hide_index=True,
                column_config={
                    "order_dt": st.column_config.DatetimeColumn("Started", format="MM/DD/YY HH:mm"),
                    "batch_make_start_dt": st.column_config.DatetimeColumn("Started Prepare", format="MM/DD/YY HH:mm"),
                    "batch_make_stop_dt": st.column_config.DatetimeColumn("Stopped Prepare", format="MM/DD/YY HH:mm"),
                    "num_preparations": st.column_config.NumberColumn("Preps", format="%.0f"),
                    "prepare_tat_minutes": st.column_config.NumberColumn("Make TAT Min", format="%.1f"),
                    "workflow_make_minutes": st.column_config.NumberColumn("Workflow Prepare Min", format="%.1f"),
                },
            )
        slow_options = (
            slowest.assign(
                drill_label=lambda d: d["order_lot_number"].astype(str)
                + " - "
                + d["drug_name"].astype(str).str.slice(0, 70)
                + " ("
                + d["prepared_by"].astype(str)
                + ")"
            )["drill_label"]
            .tolist()
        )
        selected_slow_label = st.selectbox(
            "Drill into a slow batch",
            slow_options,
            key="iv_slow_batch_drilldown",
        )
        selected_slow = slowest.assign(
            drill_label=lambda d: d["order_lot_number"].astype(str)
            + " - "
            + d["drug_name"].astype(str).str.slice(0, 70)
            + " ("
            + d["prepared_by"].astype(str)
            + ")"
        )
        selected_slow = selected_slow[selected_slow["drill_label"].eq(selected_slow_label)].head(1)
        if not selected_slow.empty:
            selected_row = selected_slow.iloc[0]
            selected_lot = str(selected_row.get("order_lot_number") or "")
            selected_dose = str(selected_row.get("dose_number") or "")
            selected_maker = str(selected_row.get("prepared_by") or "")
            selected_drug = str(selected_row.get("drug_name") or "")
            st.markdown("**Selected Batch Detail**")
            st.write(
                {
                    "Order/Lot": selected_lot,
                    "Dose": selected_dose,
                    "Prepared By": selected_maker,
                    "Drug": selected_drug,
                    "Started Prepare": selected_row.get("batch_make_start_dt"),
                    "Stopped Prepare": selected_row.get("batch_make_stop_dt"),
                    "Make TAT Min": selected_row.get("prepare_tat_minutes"),
                    "Workflow Prepare Min": selected_row.get("workflow_make_minutes"),
                }
            )
            if workflow_filtered.empty:
                st.info("Upload IV Room Workflow Detail files to see the timeline and competing work for this batch.")
            else:
                wf_batch = workflow_filtered[
                    workflow_filtered["order_lot_number"].fillna("").astype(str).eq(selected_lot)
                ].copy()
                if selected_dose:
                    wf_batch = wf_batch[
                        wf_batch["dose_number"].fillna("").astype(str).eq(selected_dose)
                        | wf_batch["dose_number"].isna()
                    ].copy()
                if wf_batch.empty:
                    st.warning("No workflow-detail rows matched this order/lot in the selected date range.")
                else:
                    wf_batch["stage"] = wf_batch["workflow_step_type"].fillna("").astype(str).str.strip()
                    wf_batch["activity"] = wf_batch["workflow_step_name"].fillna("").astype(str).str.strip()
                    wf_batch["category"] = wf_batch["workflow_step_category"].fillna("").astype(str).str.strip()
                    wf_batch["total_duration_minutes"] = pd.to_numeric(
                        wf_batch["total_duration_minutes"], errors="coerce"
                    ).fillna(0)
                    batch_timeline_cols = [
                        "start_dt", "stop_dt", "stage", "activity", "category",
                        "total_duration_minutes", "prepared_by", "approved_by", "source_file",
                    ]
                    st.markdown("**Batch Workflow Timeline**")
                    st.dataframe(
                        wf_batch[[c for c in batch_timeline_cols if c in wf_batch.columns]].sort_values(
                            ["start_dt", "stage", "activity"], na_position="last"
                        ),
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "start_dt": st.column_config.DatetimeColumn("Start", format="MM/DD/YY HH:mm"),
                            "stop_dt": st.column_config.DatetimeColumn("Stop", format="MM/DD/YY HH:mm"),
                            "total_duration_minutes": st.column_config.NumberColumn("Minutes", format="%.2f"),
                        },
                    )
                    batch_start = pd.to_datetime(selected_row.get("batch_make_start_dt"), errors="coerce")
                    batch_stop = pd.to_datetime(selected_row.get("batch_make_stop_dt"), errors="coerce")
                    if pd.isna(batch_start):
                        batch_start = wf_batch["start_dt"].dropna().min()
                    if pd.isna(batch_stop):
                        batch_stop = wf_batch["stop_dt"].dropna().max()
                    if pd.isna(batch_stop):
                        batch_stop = wf_batch["start_dt"].dropna().max()
                    if pd.notna(batch_start) and pd.notna(batch_stop):
                        other_work = workflow_filtered[
                            workflow_filtered["prepared_by"].fillna("").astype(str).eq(selected_maker)
                            & workflow_filtered["workflow_step_category"].fillna("").astype(str).str.strip().eq("Working")
                            & ~workflow_filtered["order_lot_number"].fillna("").astype(str).eq(selected_lot)
                        ].copy()
                        if not other_work.empty:
                            other_work["stop_for_overlap"] = other_work["stop_dt"].fillna(other_work["start_dt"])
                            overlap = other_work[
                                other_work["start_dt"].notna()
                                & other_work["stop_for_overlap"].notna()
                                & (other_work["start_dt"] <= batch_stop)
                                & (other_work["stop_for_overlap"] >= batch_start)
                            ].copy()
                        else:
                            overlap = pd.DataFrame()
                        st.markdown("**Other Work by Same Tech During This Batch Window**")
                        if overlap.empty:
                            st.success("No other working rows for this tech overlap the selected batch window.")
                        else:
                            overlap["stage"] = overlap["workflow_step_type"].fillna("").astype(str).str.strip()
                            overlap["activity"] = overlap["workflow_step_name"].fillna("").astype(str).str.strip()
                            overlap["total_duration_minutes"] = pd.to_numeric(
                                overlap["total_duration_minutes"], errors="coerce"
                            ).fillna(0)
                            overlap_cols = [
                                "start_dt", "stop_dt", "order_lot_number", "dose_number", "drug_name",
                                "stage", "activity", "total_duration_minutes", "approved_by",
                            ]
                            st.dataframe(
                                overlap[[c for c in overlap_cols if c in overlap.columns]].sort_values(
                                    ["start_dt", "order_lot_number"], na_position="last"
                                ),
                                width="stretch",
                                hide_index=True,
                                column_config={
                                    "start_dt": st.column_config.DatetimeColumn("Start", format="MM/DD/YY HH:mm"),
                                    "stop_dt": st.column_config.DatetimeColumn("Stop", format="MM/DD/YY HH:mm"),
                                    "total_duration_minutes": st.column_config.NumberColumn("Working Min", format="%.2f"),
                                },
                            )

st.subheader("IV Room Summary Table")
summary = (
    filtered.groupby(["prepared_by", "compound_type"], as_index=False)
    .agg(
        iv_orders=("pk", "count"),
        preparations=("num_preparations", "sum"),
        stat_orders=("priority_name", lambda s: s.astype(str).str.upper().eq("STAT").sum()),
        median_tat=("prepare_tat_minutes", "median"),
    )
    .sort_values(["preparations", "iv_orders"], ascending=False)
)
st.dataframe(
    summary,
    width="stretch",
    hide_index=True,
    column_config={
        "preparations": st.column_config.NumberColumn("Preparations", format="%.0f"),
        "median_tat": st.column_config.NumberColumn("Median TAT (min)", format="%.1f"),
    },
)

with st.expander("Raw IV Room Log"):
    raw_cols = [
        "facility_name",
        "order_lot_number",
        "compound_type",
        "num_preparations",
        "drug_name",
        "order_dt",
        "completed_on",
        "priority_name",
        "prepare_tat_minutes",
        "prepared_by",
        "approved_by",
        "secondary_approved_by",
        "order_status",
    ]
    st.dataframe(
        raw_filtered[raw_cols].sort_values("order_dt", ascending=False),
        width="stretch",
        hide_index=True,
    )
    st.download_button(
        "Export IV Room CSV",
        data=to_csv_bytes(raw_filtered[raw_cols]),
        file_name="iv_room_workload.csv",
        mime="text/csv",
    )
