from datetime import datetime, date

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

import App
from rxtrack_shared import add_return_compare_qty, group_return_compare_qty


st.set_page_config(page_title="Projects Portfolio", page_icon="📁", layout="wide")

if hasattr(App, "render_sidebar_chrome"):
    App.render_sidebar_chrome()
else:
    App.render_sidebar()

if hasattr(App, "render_page_intro"):
    App.render_page_intro(
        "Projects Portfolio",
        "A running record of pharmacy operations projects, process changes, and the impact they created.",
        kicker="Professional Portfolio",
    )
else:
    st.header("Projects Portfolio")
    st.caption("A running record of pharmacy operations projects, process changes, and impact.")


engine = App.engine
load_data = App.load_data

UNLOAD_PROJECT_START = date(2025, 12, 15)
UNLOAD_PROJECT_END = date(2026, 1, 5)
UNLOAD_PROJECT_USERS = ["Isaac Vizral", "Jaycie Cole", "Lauren Voudrie"]
WEEKEND_OLD_MORNING_SHIFT = ("07:00", "15:30")
WEEKEND_OLD_EVENING_SHIFT = ("13:00", "21:30")
WEEKEND_NEW_HYBRID_SHIFT = ("10:00", "18:30")


PROJECTS = [
    {
        "name": "Unload Window Optimization",
        "status": "Completed",
        "timeframe": "Dec 15, 2025 to Jan 5, 2026",
        "area": "Inventory Rotation / Carousel Stocking",
        "problem": (
            "The unload process was waiting 45 days before medications rotated back through the pharmacy workflow. "
            "That allowed usable inventory to sit in Pyxis longer while the carousel could appear short."
        ),
        "action": (
            "Changed the unload window from 45 days to 28 days so inventory rotates sooner, keeping more usable "
            "stock in the pharmacy carousel and improving visibility before new orders are placed."
        ),
        "impact": (
            "Less unnecessary ordering pressure because medication that would have been sitting in Pyxis is brought "
            "back into carousel availability sooner. The pharmacy can use on-hand inventory before buying more."
        ),
        "proof_points": [
            "Compare carousel on-hand quantity before and after the 28-day change.",
            "Track medications reordered while usable stock existed in Pyxis.",
            "Monitor unload volume and carousel restock volume by week.",
            "Estimate avoided orders or avoided dollars once cost data is available.",
        ],
    },
    {
        "name": "Weekend Runner Staffing Redesign",
        "status": "Completed",
        "timeframe": "Weekend schedule redesign",
        "area": "Staffing / Delivery Workflow",
        "problem": (
            "Weekend coverage used two runner/delivery positions: a 0700 shift and a 1300-2130 shift. "
            "The late Sunday shift was especially hard on pharmacy students commuting from St. Louis, so full-time "
            "staff carried more weekend nights."
        ),
        "action": (
            "Condensed the two weekend runner positions into one 1000-1830 hybrid position. Kept the same delivery, "
            "stat-med, odd-job, and cartfill support covered by reallocating some work to another position that had "
            "available time."
        ),
        "impact": (
            "Eliminated one weekend schedule position while keeping pharmacy operations smooth. The hybrid shift made "
            "the role more workable for students, reduced full-time staff weekend-night pressure, and opened room to "
            "expand weekend rotation fairness."
        ),
        "proof_points": [
            "Old schedule: 0700 runner plus 1300-2130 runner/delivery coverage.",
            "New schedule: one 1000-1830 hybrid position.",
            "Weekend position count reduced by one while core weekend delivery work remains covered.",
            "Track annualized hours removed and estimated labor-dollar opportunity.",
        ],
    },
    {
        "name": "RxTrack Operations Dashboard",
        "status": "In production",
        "timeframe": "Ongoing",
        "area": "Operations Intelligence",
        "problem": "Daily Pyxis, carousel, pharmacy workflow, schedule, and attendance data lived in separate reports.",
        "action": "Built a Streamlit dashboard that combines those sources into operational views and drilldowns.",
        "impact": "Faster visibility into workload, discrepancies, returns, pends, staffing patterns, and inventory quality.",
        "proof_points": [
            "Number of workflows now visible in one app.",
            "Time saved from manual report review.",
            "Examples of issues found through dashboard drilldowns.",
        ],
    },
    {
        "name": "Carousel Drop Tracker",
        "status": "In production",
        "timeframe": "Ongoing",
        "area": "Carousel / Pyxis Loop Closure",
        "problem": "Carousel pull demand and Pyxis refill completion were hard to compare by drop window.",
        "action": "Built drop-level reconciliation around pull quantity, loaded quantity, coverage, and device drilldowns.",
        "impact": "Makes missed refills, partial fills, and refill timing visible by drop and by device.",
        "proof_points": [
            "Coverage percentage by drop.",
            "Devices with pull demand but no refill activity.",
            "Repeated problem devices or windows.",
        ],
    },
    {
        "name": "Return Reconciliation Safety Improvement",
        "status": "In progress",
        "timeframe": "Ongoing",
        "area": "Medication Safety / Return Accuracy",
        "problem": (
            "Pyxis unloads and empty return-bin activity did not always match carousel return transactions. "
            "A low match rate means medication may have been returned incorrectly, creating preventable risk "
            "for wrong medication placement and downstream administration errors."
        ),
        "action": (
            "Built a reconciliation workflow that compares Pyxis removals to carousel return activity and used the "
            "match rate as the safety signal. Process changes pushed technicians away from basic return behavior "
            "and toward more scan-heavy instant return and restock workflows."
        ),
        "impact": (
            "Improves return accuracy, creates a clear exception list for follow-up, and shows whether process "
            "changes are increasing return match rate over time."
        ),
        "proof_points": [
            "Match rate trend over time.",
            "Unmatched med-days trend over time.",
            "Workflow mix shift toward Instant Return and Instant Restock.",
            "Medication-level and user-level drilldowns for exceptions.",
        ],
    },
]


def project_dataframe(projects):
    return pd.DataFrame(
        [
            {
                "Project": p["name"],
                "Status": p["status"],
                "Area": p["area"],
                "Timeframe": p["timeframe"],
            }
            for p in projects
        ]
    )


def shift_hours(start_time, end_time):
    start = datetime.strptime(start_time, "%H:%M")
    end = datetime.strptime(end_time, "%H:%M")
    hours = (end - start).total_seconds() / 3600
    if hours < 0:
        hours += 24
    return hours


def classify_return_workflow(row):
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


def remove_dummy_meds(df):
    if df.empty or "med_desc" not in df.columns:
        return df
    return df[~df["med_desc"].astype(str).str.contains("cassette", case=False, na=False)]


def remove_pat_refs(df):
    if df.empty or "med_id" not in df.columns:
        return df
    return df[~df["med_id"].astype(str).str.strip().str.match(r"^9000\d+", na=False)]


def is_likely_bulk_package_return(df):
    if df.empty or "qty" not in df.columns:
        return pd.Series(False, index=df.index)
    qty = pd.to_numeric(df["qty"], errors="coerce").fillna(0).abs()
    return qty.isin([60, 90, 100, 500]) | ((qty >= 50) & (qty % 10 == 0))


def matches_project_user(user_name, project_users):
    raw = str(user_name or "").strip().lower()
    if not raw:
        return False
    compact = raw.replace(",", " ")
    tokens = {part for part in compact.split() if part}
    normalized = App.normalize_name(raw)

    for project_user in project_users:
        first, *rest = project_user.lower().split()
        last = rest[-1] if rest else ""
        full = f"{first} {last}".strip()
        comma_name = f"{last}, {first}".strip(", ")
        if raw in {full, comma_name} or full in compact:
            return True
        if first in tokens and (not last or last in tokens):
            return True
        if normalized == first:
            return True
    return False


@st.cache_data(ttl=300)
def load_med_costs():
    try:
        with engine.connect() as conn:
            costs = pd.read_sql(
                text("SELECT med_id, cost_per_unit FROM med_costs"),
                conn,
            )
        costs["med_id"] = costs["med_id"].astype(str).str.strip()
        costs["cost_per_unit"] = pd.to_numeric(costs["cost_per_unit"], errors="coerce").fillna(0)
        return costs
    except Exception:
        return pd.DataFrame(columns=["med_id", "cost_per_unit"])


def build_unload_window_impact(
    start_date,
    end_date,
    exclude_dummy=True,
    savings_capture_pct=100,
    project_users=None,
):
    df_events, _, df_pharm, _, _ = load_data(start_date, end_date)
    for df in [df_events, df_pharm]:
        if not df.empty and "dt" in df.columns:
            df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
            df["date"] = df["dt"].dt.date

    pyxis_unload = pd.DataFrame()
    if not df_events.empty and "event_type" in df_events.columns:
        pyxis_all = df_events[
            df_events["event_type"].astype(str).str.contains("empty|unload|return bin|destock", case=False, na=False)
            & ~df_events["event_type"].astype(str).str.contains("cancel", case=False, na=False)
        ].copy()
        pyxis_unload = pyxis_all[
            ~pyxis_all["event_type"].astype(str).str.contains("eject", case=False, na=False)
        ].copy()
        if "device" in pyxis_unload.columns:
            pyxis_unload = pyxis_unload[
                ~pyxis_unload["device"].astype(str).str.contains("cass|patient", case=False, na=False)
            ]

    pharm_return = pd.DataFrame()
    if not df_pharm.empty:
        pharm_df = df_pharm.copy()
        event_col = "event_type" if "event_type" in pharm_df.columns else "priority"
        pharm_all = pharm_df[
            pharm_df[event_col].astype(str).str.contains("return|restock|instant|inventory", case=False, na=False)
        ].copy()
        if not pharm_all.empty:
            pharm_all["workflow_type"] = pharm_all.apply(classify_return_workflow, axis=1)
            pharm_return = pharm_all[
                pharm_all["workflow_type"].isin({"Return", "Instant Return", "Instant Restock"})
            ].copy()

    if exclude_dummy:
        pyxis_unload = remove_dummy_meds(pyxis_unload)
        pharm_return = remove_dummy_meds(pharm_return)

    if project_users:
        if not pyxis_unload.empty and "user_name" in pyxis_unload.columns:
            pyxis_unload = pyxis_unload[
                pyxis_unload["user_name"].apply(lambda value: matches_project_user(value, project_users))
            ]
        if not pharm_return.empty and "user_name" in pharm_return.columns:
            pharm_return = pharm_return[
                pharm_return["user_name"].apply(lambda value: matches_project_user(value, project_users))
            ]

    costs = load_med_costs()
    project_source = pyxis_unload.copy()
    if not project_source.empty:
        project_source["med_id"] = project_source["med_id"].astype(str).str.strip()
        project_source = add_return_compare_qty(project_source, source="pyxis")
        project_source["impact_unit_divisor"] = project_source["return_unit_divisor"]
        project_source["impact_qty"] = project_source["compare_qty"]
        returned = (
            project_source.groupby(["med_id", "med_desc"], dropna=False)
            .agg(
                returned_qty=("qty", "sum"),
                impact_qty=("impact_qty", "sum"),
                impact_unit_divisor=("impact_unit_divisor", "max"),
                impact_unit_note=("return_unit_note", lambda values: "; ".join(sorted(set(values.dropna().astype(str))))),
                unload_rows=("qty", "size"),
                users=("user_name", lambda values: ", ".join(sorted({str(v) for v in values if pd.notna(v)}))),
                devices=("device", lambda values: ", ".join(sorted({str(v) for v in values if pd.notna(v)}))),
            )
            .reset_index()
        )
    else:
        returned = pd.DataFrame(
            columns=[
                "med_id", "med_desc", "returned_qty", "impact_qty", "impact_unit_divisor",
                "impact_unit_note", "unload_rows", "users", "devices"
            ]
        )

    if not pharm_return.empty:
        pharm_return["med_id"] = pharm_return["med_id"].astype(str).str.strip()
        pharm_return["qty"] = pd.to_numeric(pharm_return["qty"], errors="coerce").fillna(0)
        carousel_returned = (
            pharm_return.groupby(["med_id", "med_desc"], dropna=False)["qty"]
            .sum()
            .reset_index(name="carousel_return_qty")
        )
    else:
        carousel_returned = pd.DataFrame(columns=["med_id", "med_desc", "carousel_return_qty"])

    returned = returned.merge(costs, on="med_id", how="left")
    returned = returned.merge(
        carousel_returned.drop(columns=["med_desc"], errors="ignore"),
        on="med_id",
        how="left",
    )
    returned["cost_per_unit"] = returned["cost_per_unit"].fillna(0)
    returned["carousel_return_qty"] = returned["carousel_return_qty"].fillna(0)
    returned["impact_qty"] = returned["impact_qty"].fillna(returned["returned_qty"])
    returned["impact_unit_divisor"] = returned["impact_unit_divisor"].fillna(1)
    if "impact_unit_note" not in returned.columns:
        returned["impact_unit_note"] = ""
    returned["impact_unit_note"] = returned["impact_unit_note"].fillna("").replace("", "Each")
    returned["returned_value"] = returned["impact_qty"] * returned["cost_per_unit"]
    returned = returned.sort_values("returned_value", ascending=False)

    days = max((pd.to_datetime(end_date).date() - pd.to_datetime(start_date).date()).days + 1, 1)
    capture_rate = savings_capture_pct / 100
    returned_units = float(returned["returned_qty"].sum()) if not returned.empty else 0
    impact_units = float(returned["impact_qty"].sum()) if not returned.empty else 0
    carousel_units = float(returned["carousel_return_qty"].sum()) if not returned.empty else 0
    returned_value = float(returned["returned_value"].sum()) if not returned.empty else 0
    annualized_units = impact_units / days * 365
    annualized_value = returned_value / days * 365 * capture_rate
    pyxis_units = float(pyxis_unload["qty"].sum()) if not pyxis_unload.empty and "qty" in pyxis_unload.columns else 0

    returned["projected_12mo_value"] = returned["returned_value"] / days * 365 * capture_rate
    return {
        "detail": returned,
        "days": days,
        "returned_units": returned_units,
        "impact_units": impact_units,
        "carousel_units": carousel_units,
        "returned_value": returned_value,
        "annualized_units": annualized_units,
        "annualized_value": annualized_value,
        "pyxis_units": pyxis_units,
    }


def build_return_reconciliation_trends(
    start_date,
    end_date,
    interval="W",
    exclude_dummy=True,
    exclude_pat_refs=True,
    exclude_bulk_package_returns=True,
):
    df_events, _, df_pharm, _, _ = load_data(start_date, end_date)
    for df in [df_events, df_pharm]:
        if not df.empty and "dt" in df.columns:
            df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
            df["date"] = df["dt"].dt.date

    pyxis_unload = pd.DataFrame()
    if not df_events.empty and "event_type" in df_events.columns:
        pyxis_all = df_events[
            df_events["event_type"].astype(str).str.contains("empty|unload|return bin|destock", case=False, na=False)
            & ~df_events["event_type"].astype(str).str.contains("cancel", case=False, na=False)
        ].copy()
        pyxis_unload = pyxis_all[
            ~pyxis_all["event_type"].astype(str).str.contains("eject", case=False, na=False)
        ].copy()
        if "device" in pyxis_unload.columns:
            pyxis_unload = pyxis_unload[
                ~pyxis_unload["device"].astype(str).str.contains("cass|patient", case=False, na=False)
            ]

    pharm_all = pd.DataFrame()
    if not df_pharm.empty:
        pharm_df = df_pharm.copy()
        event_col = "event_type" if "event_type" in pharm_df.columns else "priority"
        pharm_all = pharm_df[
            pharm_df[event_col].astype(str).str.contains("return|restock|instant|inventory", case=False, na=False)
        ].copy()
        if not pharm_all.empty:
            pharm_all["workflow_type"] = pharm_all.apply(classify_return_workflow, axis=1)

    included_return_types = {"Return", "Instant Return", "Instant Restock"}
    pharm_return = (
        pharm_all[pharm_all["workflow_type"].isin(included_return_types)].copy()
        if not pharm_all.empty
        else pd.DataFrame()
    )

    if exclude_dummy:
        pyxis_unload = remove_dummy_meds(pyxis_unload)
        pharm_return = remove_dummy_meds(pharm_return)
        pharm_all = remove_dummy_meds(pharm_all)

    if exclude_pat_refs:
        pyxis_unload = remove_pat_refs(pyxis_unload)
        pharm_return = remove_pat_refs(pharm_return)
        pharm_all = remove_pat_refs(pharm_all)

    bulk_package_returns = pd.DataFrame()
    if exclude_bulk_package_returns and not pharm_return.empty:
        bulk_mask = is_likely_bulk_package_return(pharm_return)
        bulk_package_returns = pharm_return[bulk_mask].copy()
        pharm_return = pharm_return[~bulk_mask].copy()
        if not pharm_all.empty:
            pharm_all = pharm_all[~is_likely_bulk_package_return(pharm_all)].copy()

    def safe_group(df, qty_name):
        source = "pyxis" if qty_name == "qty_pyxis" else "carousel"
        return group_return_compare_qty(df, qty_name, source=source)

    pyxis_sum = safe_group(pyxis_unload, "qty_pyxis")
    pharm_sum = safe_group(pharm_return, "qty_pharm")
    recon = pd.merge(
        pyxis_sum.drop(columns=["med_desc"], errors="ignore"),
        pharm_sum.drop(columns=["med_desc"], errors="ignore"),
        on=["med_id", "date"],
        how="outer",
    )
    if recon.empty:
        trend = pd.DataFrame(columns=["period", "qty_pyxis", "qty_pharm", "matched_qty", "match_rate", "unmatched_med_days"])
    else:
        recon[["qty_pyxis", "qty_pharm"]] = recon[["qty_pyxis", "qty_pharm"]].fillna(0)
        recon["matched_qty"] = recon[["qty_pyxis", "qty_pharm"]].min(axis=1)
        recon["unmatched"] = recon["qty_pyxis"] != recon["qty_pharm"]
        recon["period"] = pd.to_datetime(recon["date"]).dt.to_period(interval).dt.start_time.dt.date
        trend = (
            recon.groupby("period")
            .agg(
                qty_pyxis=("qty_pyxis", "sum"),
                qty_pharm=("qty_pharm", "sum"),
                matched_qty=("matched_qty", "sum"),
                unmatched_med_days=("unmatched", "sum"),
            )
            .reset_index()
        )
        trend["match_rate"] = (trend["matched_qty"] / trend["qty_pyxis"] * 100).where(trend["qty_pyxis"] > 0, 100)

    if pharm_all.empty or "workflow_type" not in pharm_all.columns:
        workflow_mix = pd.DataFrame(columns=["period", "workflow_type", "rows", "qty"])
    else:
        pharm_all = pharm_all.copy()
        pharm_all["qty"] = pd.to_numeric(pharm_all["qty"], errors="coerce").fillna(0)
        pharm_all["period"] = pd.to_datetime(pharm_all["date"]).dt.to_period(interval).dt.start_time.dt.date
        workflow_mix = (
            pharm_all.groupby(["period", "workflow_type"], dropna=False)
            .agg(rows=("workflow_type", "size"), qty=("qty", "sum"))
            .reset_index()
        )

    totals = {
        "pyxis_qty": float(trend["qty_pyxis"].sum()) if not trend.empty else 0,
        "carousel_qty": float(trend["qty_pharm"].sum()) if not trend.empty else 0,
        "matched_qty": float(trend["matched_qty"].sum()) if not trend.empty else 0,
        "unmatched_med_days": int(trend["unmatched_med_days"].sum()) if not trend.empty else 0,
        "bulk_package_qty": float(bulk_package_returns["qty"].sum()) if not bulk_package_returns.empty and "qty" in bulk_package_returns.columns else 0,
    }
    totals["match_rate"] = (totals["matched_qty"] / totals["pyxis_qty"] * 100) if totals["pyxis_qty"] > 0 else 100
    return trend, workflow_mix, totals


def render_project_card(project):
    with st.container(border=True):
        top = st.columns([2.2, 1, 1])
        top[0].subheader(project["name"])
        top[1].metric("Status", project["status"])
        top[2].metric("Area", project["area"])

        st.caption(project["timeframe"])
        st.markdown("**Problem**")
        st.write(project["problem"])
        st.markdown("**What I Changed**")
        st.write(project["action"])
        st.markdown("**Operational Impact**")
        st.write(project["impact"])

        with st.expander("Proof points to attach", expanded=False):
            for item in project["proof_points"]:
                st.markdown(f"- {item}")


summary = project_dataframe(PROJECTS)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Projects Tracked", len(PROJECTS))
c2.metric("Completed", sum(1 for p in PROJECTS if p["status"] == "Completed"))
c3.metric("In Production", sum(1 for p in PROJECTS if p["status"] == "In production"))
c4.metric("Last Updated", date.today().strftime("%b %d, %Y"))

st.divider()

st.subheader("Portfolio Summary")
st.dataframe(summary, use_container_width=True, hide_index=True)

st.divider()

st.subheader("Featured Project")
render_project_card(PROJECTS[0])

with st.expander("Unload Window Impact Calculator", expanded=True):
    st.caption(
        "Project mode values the Pyxis unload transactions completed by Isaac Vizral, Jaycie Cole, and Lauren Voudrie from Dec 15, 2025 through Jan 5, 2026."
    )

    use_project_scope = st.checkbox(
        "Use unload-window project scope",
        value=True,
        help="Dec 15, 2025 to Jan 5, 2026; Isaac Vizral, Jaycie Cole, and Lauren Voudrie.",
    )

    f1, f2, f3 = st.columns([1, 1, 1])
    default_start = UNLOAD_PROJECT_START if use_project_scope else date.today() - pd.Timedelta(days=89)
    default_end = UNLOAD_PROJECT_END if use_project_scope else date.today()
    impact_start = f1.date_input("Impact start date", value=default_start, disabled=use_project_scope)
    impact_end = f2.date_input("Impact end date", value=default_end, disabled=use_project_scope)
    capture_pct = f3.slider(
        "Savings capture assumption",
        min_value=0,
        max_value=100,
        value=100,
        step=5,
        help="Use 100% when every returned unit is assumed to offset a future purchase. Lower it if only some returned stock prevents ordering.",
    )
    exclude_dummy = st.checkbox("Exclude dummy/cassette medications", value=True)
    project_users = UNLOAD_PROJECT_USERS if use_project_scope else None
    if use_project_scope:
        st.caption("Included project users: " + ", ".join(UNLOAD_PROJECT_USERS))

    if impact_start > impact_end:
        st.warning("Start date must be before end date.")
    else:
        impact = build_unload_window_impact(impact_start, impact_end, exclude_dummy, capture_pct, project_users)
        detail = impact["detail"]

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Project Unload Qty", f"{impact['returned_units']:,.0f}")
        m2.metric("Carousel Return Units", f"{impact['carousel_units']:,.0f}")
        m3.metric("Returned Inventory Value", f"${impact['returned_value']:,.2f}")
        m4.metric("Projected 12-Mo Impact Units", f"{impact['annualized_units']:,.0f}")
        m5.metric("Projected 12-Mo Value", f"${impact['annualized_value']:,.2f}")

        st.caption(
            f"Projection is annualized from {impact['days']} selected day(s) at a {capture_pct}% capture assumption. "
            "Treat this as avoided purchasing opportunity from rotating 28-to-45-day Pyxis inventory back into usable stock, not booked savings, until purchasing data confirms the offset."
        )

        if detail.empty:
            st.info("No qualifying project unload rows found for this date range and user scope.")
        else:
            table = detail.rename(
                columns={
                    "med_id": "Med ID",
                    "med_desc": "Medication",
                    "returned_qty": "Raw Unload Qty",
                    "impact_qty": "Impact Units",
                    "impact_unit_note": "Unit Conversion",
                    "carousel_return_qty": "Carousel Return Qty",
                    "unload_rows": "Unload Rows",
                    "users": "Users",
                    "devices": "Devices",
                    "cost_per_unit": "Unit Cost",
                    "returned_value": "Returned Value",
                    "projected_12mo_value": "Projected 12-Mo Value",
                }
            )
            st.dataframe(
                table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Raw Unload Qty": st.column_config.NumberColumn(format="%.0f"),
                    "Impact Units": st.column_config.NumberColumn(format="%.2f"),
                    "Carousel Return Qty": st.column_config.NumberColumn(format="%.0f"),
                    "Unload Rows": st.column_config.NumberColumn(format="%.0f"),
                    "Unit Cost": st.column_config.NumberColumn(format="$%.2f"),
                    "Returned Value": st.column_config.NumberColumn(format="$%.2f"),
                    "Projected 12-Mo Value": st.column_config.NumberColumn(format="$%.2f"),
                },
            )

            top = detail.head(15).copy()
            top["label"] = top["med_desc"].fillna(top["med_id"]).astype(str).str.slice(0, 42)
            fig = px.bar(
                top.sort_values("returned_value"),
                x="returned_value",
                y="label",
                orientation="h",
                text="impact_qty",
                labels={"returned_value": "Returned inventory value", "label": ""},
                color="returned_value",
                color_continuous_scale="Teal",
            )
            fig.update_layout(height=420, coloraxis_showscale=False, margin=dict(l=10, r=20, t=20, b=10))
            fig.update_traces(texttemplate="%{text:.2f} impact units", textposition="outside")
            st.plotly_chart(fig, use_container_width=True)

st.divider()

st.subheader("Featured Staffing Project")
render_project_card(PROJECTS[1])

with st.expander("Weekend Runner Staffing Calculator", expanded=True):
    st.caption(
        "Models the weekend schedule change from two runner/delivery positions to one 1000-1830 hybrid position."
    )

    c1, c2, c3 = st.columns(3)
    old_morning_start = c1.text_input("Old morning start", value=WEEKEND_OLD_MORNING_SHIFT[0])
    old_morning_end = c1.text_input("Old morning end", value=WEEKEND_OLD_MORNING_SHIFT[1])
    old_evening_start = c2.text_input("Old evening start", value=WEEKEND_OLD_EVENING_SHIFT[0])
    old_evening_end = c2.text_input("Old evening end", value=WEEKEND_OLD_EVENING_SHIFT[1])
    new_hybrid_start = c3.text_input("New hybrid start", value=WEEKEND_NEW_HYBRID_SHIFT[0])
    new_hybrid_end = c3.text_input("New hybrid end", value=WEEKEND_NEW_HYBRID_SHIFT[1])

    a1, a2, a3 = st.columns(3)
    weekend_days = a1.number_input("Weekend days per week", min_value=1, max_value=7, value=2, step=1)
    hourly_rate = a2.number_input(
        "Fully loaded hourly rate",
        min_value=0.0,
        value=0.0,
        step=1.0,
        help="Optional. Enter wage plus benefits to estimate annual labor-dollar opportunity.",
    )
    weeks_per_year = a3.number_input("Weeks per year", min_value=1, max_value=53, value=52, step=1)

    try:
        old_daily_hours = shift_hours(old_morning_start, old_morning_end) + shift_hours(old_evening_start, old_evening_end)
        new_daily_hours = shift_hours(new_hybrid_start, new_hybrid_end)
        daily_hours_removed = max(old_daily_hours - new_daily_hours, 0)
        weekly_hours_removed = daily_hours_removed * weekend_days
        annual_hours_removed = weekly_hours_removed * weeks_per_year
        annual_labor_opportunity = annual_hours_removed * hourly_rate

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Old Weekend Hours / Day", f"{old_daily_hours:.1f}")
        m2.metric("New Weekend Hours / Day", f"{new_daily_hours:.1f}")
        m3.metric("Hours Removed / Week", f"{weekly_hours_removed:.1f}")
        m4.metric("Annual Hours Removed", f"{annual_hours_removed:,.0f}")

        if hourly_rate > 0:
            st.metric("Estimated Annual Labor-Dollar Opportunity", f"${annual_labor_opportunity:,.2f}")

        schedule_df = pd.DataFrame(
            [
                {"Schedule": "Old", "Position": "0700 runner / delivery", "Start": old_morning_start, "End": old_morning_end, "Hours": shift_hours(old_morning_start, old_morning_end)},
                {"Schedule": "Old", "Position": "1300-2130 runner / delivery", "Start": old_evening_start, "End": old_evening_end, "Hours": shift_hours(old_evening_start, old_evening_end)},
                {"Schedule": "New", "Position": "1000-1830 hybrid runner / delivery", "Start": new_hybrid_start, "End": new_hybrid_end, "Hours": new_daily_hours},
            ]
        )
        st.dataframe(schedule_df, use_container_width=True, hide_index=True)

        chart_df = pd.DataFrame(
            [
                {"Schedule": "Old two-position coverage", "Hours per weekend day": old_daily_hours},
                {"Schedule": "New hybrid coverage", "Hours per weekend day": new_daily_hours},
            ]
        )
        fig = px.bar(
            chart_df,
            x="Schedule",
            y="Hours per weekend day",
            text="Hours per weekend day",
            color="Schedule",
            color_discrete_map={
                "Old two-position coverage": "#ef4444",
                "New hybrid coverage": "#14b8a6",
            },
        )
        fig.update_layout(height=340, showlegend=False, margin=dict(l=10, r=20, t=20, b=10))
        fig.update_traces(texttemplate="%{text:.1f} hrs", textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

        st.caption(
            "Operational narrative: the late 1300-2130 weekend shift became a 1000-1830 hybrid role, making the "
            "position easier for pharmacy students to work and reducing full-time staff weekend-night burden."
        )
    except ValueError:
        st.warning("Use 24-hour HH:MM times, such as 07:00, 13:00, or 18:30.")

st.divider()

st.subheader("Featured Safety Project")
render_project_card(PROJECTS[-1])

with st.expander("Return Reconciliation Improvement Trend", expanded=True):
    st.caption(
        "Tracks whether Pyxis unload and empty return-bin quantities are matching carousel return transactions more accurately over time."
    )

    r1, r2, r3 = st.columns([1, 1, 1])
    recon_start = r1.date_input("Trend start date", value=date.today() - pd.Timedelta(days=179), key="return_recon_start")
    recon_end = r2.date_input("Trend end date", value=date.today(), key="return_recon_end")
    interval_label = r3.selectbox("Trend grouping", options=["Weekly", "Monthly"], index=0)
    recon_exclude_dummy = st.checkbox("Exclude dummy/cassette medications from return trend", value=True)
    recon_exclude_pat_refs = st.checkbox("Exclude PAT/ref med IDs (9000...) from return trend", value=True)
    recon_exclude_bulk = st.checkbox("Exclude likely packaging bulk returns from return trend", value=True)
    interval = "W" if interval_label == "Weekly" else "M"

    if recon_start > recon_end:
        st.warning("Trend start date must be before trend end date.")
    else:
        trend, workflow_mix, totals = build_return_reconciliation_trends(
            recon_start,
            recon_end,
            interval=interval,
            exclude_dummy=recon_exclude_dummy,
            exclude_pat_refs=recon_exclude_pat_refs,
            exclude_bulk_package_returns=recon_exclude_bulk,
        )

        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Overall Match Rate", f"{totals['match_rate']:.1f}%")
        s2.metric("Pyxis Removal Qty", f"{totals['pyxis_qty']:,.0f}")
        s3.metric("Carousel Return Qty", f"{totals['carousel_qty']:,.0f}")
        s4.metric("Unmatched Med-Days", f"{totals['unmatched_med_days']:,}")
        s5.metric("Bulk Returns Excluded", f"{totals['bulk_package_qty']:,.0f}")

        st.caption(
            "Safety interpretation: higher match rate means the Pyxis removal record and carousel return workflow are lining up. "
            "Unmatched rows are the exception list because incorrect return handling can put the wrong med back into circulation."
        )

        if trend.empty:
            st.info("No reconciliation trend data found for this date range.")
        else:
            trend_display = trend.copy()
            trend_display["period"] = pd.to_datetime(trend_display["period"])
            fig_match = px.line(
                trend_display,
                x="period",
                y="match_rate",
                markers=True,
                labels={"period": "", "match_rate": "Match rate (%)"},
            )
            fig_match.update_layout(height=360, yaxis_range=[0, 105], margin=dict(l=10, r=20, t=20, b=10))
            st.plotly_chart(fig_match, use_container_width=True)

            t1, t2 = st.columns(2)
            fig_unmatched = px.bar(
                trend_display,
                x="period",
                y="unmatched_med_days",
                labels={"period": "", "unmatched_med_days": "Unmatched med-days"},
                color="unmatched_med_days",
                color_continuous_scale="Reds",
            )
            fig_unmatched.update_layout(height=340, coloraxis_showscale=False, margin=dict(l=10, r=20, t=20, b=10))
            t1.plotly_chart(fig_unmatched, use_container_width=True)

            volume_long = trend_display.melt(
                id_vars=["period"],
                value_vars=["qty_pyxis", "qty_pharm"],
                var_name="Source",
                value_name="Qty",
            )
            volume_long["Source"] = volume_long["Source"].replace(
                {"qty_pyxis": "Pyxis removals", "qty_pharm": "Carousel returns"}
            )
            fig_volume = px.bar(
                volume_long,
                x="period",
                y="Qty",
                color="Source",
                barmode="group",
                labels={"period": ""},
            )
            fig_volume.update_layout(height=340, margin=dict(l=10, r=20, t=20, b=10))
            t2.plotly_chart(fig_volume, use_container_width=True)

            st.dataframe(
                trend_display.rename(
                    columns={
                        "period": "Period",
                        "qty_pyxis": "Pyxis Removal Qty",
                        "qty_pharm": "Carousel Return Qty",
                        "matched_qty": "Matched Qty",
                        "match_rate": "Match Rate",
                        "unmatched_med_days": "Unmatched Med-Days",
                    }
                ),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Match Rate": st.column_config.NumberColumn(format="%.1f%%"),
                    "Pyxis Removal Qty": st.column_config.NumberColumn(format="%.0f"),
                    "Carousel Return Qty": st.column_config.NumberColumn(format="%.0f"),
                    "Matched Qty": st.column_config.NumberColumn(format="%.0f"),
                    "Unmatched Med-Days": st.column_config.NumberColumn(format="%.0f"),
                },
            )

        if workflow_mix.empty:
            st.info("No carousel workflow mix data found for this date range.")
        else:
            workflow_mix_display = workflow_mix.copy()
            workflow_mix_display["period"] = pd.to_datetime(workflow_mix_display["period"])
            fig_mix = px.bar(
                workflow_mix_display,
                x="period",
                y="rows",
                color="workflow_type",
                labels={"period": "", "rows": "Workflow rows", "workflow_type": "Workflow type"},
            )
            fig_mix.update_layout(height=380, margin=dict(l=10, r=20, t=20, b=10))
            st.plotly_chart(fig_mix, use_container_width=True)

            st.caption(
                "Process-change signal: increased Instant Return and Instant Restock activity should show more scan-based handling over time."
            )

st.divider()

st.subheader("Other Projects So Far")
for project in PROJECTS[2:-1]:
    render_project_card(project)

st.divider()

with st.expander("Impact statement for resumes, interviews, or leadership updates", expanded=True):
    st.markdown(
        """
Changed the pharmacy unload process from a 45-day window to a 28-day window to rotate inventory sooner,
keep more usable stock available in the carousel, and reduce unnecessary drug ordering when medication
was already available in Pyxis inventory. Built RxTrack dashboards to make these operational changes
visible, measurable, and easier to sustain.
        """.strip()
    )
